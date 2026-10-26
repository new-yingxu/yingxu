use crate::{
    Engine,
    fsops::{self, SafeDir},
    model::*,
};
use rusqlite::{OptionalExtension, params};
use serde_json::json;
use std::{
    collections::BTreeMap,
    path::{Path, PathBuf},
};

impl Engine {
    fn transfer_files(&self, job: &Job) -> CoreResult<Vec<JobItem>> {
        if job.kind != "transfer" || job.state != "success" || !job.verify {
            return Err(CoreError::new(
                "TRANSFER_NOT_VERIFIED",
                "仅完整校验成功的转移任务可移除来源",
            ));
        }
        let mut files = BTreeMap::new();
        for item in &job.items {
            if !item.verified || !["copied", "skipped"].contains(&item.state.as_str()) {
                return Err(CoreError::new(
                    "TRANSFER_NOT_VERIFIED",
                    item.relative.display(),
                ));
            }
            files.entry(item.relative.clone()).or_insert(item.clone());
        }
        Ok(files.into_values().collect())
    }
    fn verify_transfer_file(&self, job: &Job, item: &JobItem, source: &SafeDir) -> CoreResult<()> {
        if fsops::fingerprint(&std::fs::metadata(&job.source)?).identity
            != job.source_identity.identity
        {
            return Err(CoreError::new("ROOT_CHANGED", "来源根目录已变化"));
        }
        if fsops::fingerprint(&source.read(&item.relative)?.metadata()?) != item.fingerprint {
            return Err(CoreError::new("SOURCE_CHANGED", item.relative.display()));
        }
        let expected = item
            .sha256
            .as_ref()
            .ok_or_else(|| CoreError::new("MISSING_CHECKSUM", &item.id))?;
        if fsops::hash_at(source, &item.relative, &mut || Ok(()))? != *expected {
            return Err(CoreError::new("SOURCE_CHANGED", item.relative.display()));
        }
        for replica in job.items.iter().filter(|i| i.relative == item.relative) {
            let target = job
                .targets
                .iter()
                .find(|t| t.id == replica.target_id)
                .ok_or_else(|| CoreError::new("NOT_FOUND", &replica.target_id))?;
            if fsops::fingerprint(&std::fs::metadata(&target.path)?).identity
                != target.identity.identity
            {
                return Err(CoreError::new("ROOT_CHANGED", target.path.display()));
            }
            let root = SafeDir::open(&target.path)?;
            let relative = replica
                .output
                .strip_prefix(&root.path)
                .map_err(|e| CoreError::new("UNSAFE_PATH", e))?;
            if fsops::hash_at(&root, relative, &mut || Ok(()))? != *expected {
                return Err(CoreError::new(
                    "REPLICA_DAMAGED",
                    "目标副本已变化，保留来源",
                ));
            }
        }
        Ok(())
    }
    pub fn prepare_source_removal(&self, job_id: &str) -> CoreResult<RemovalPlan> {
        let job = self.store.job(job_id)?;
        let source = SafeDir::open(&job.source)?;
        let mut pending = vec![];
        let c = self.store.connect()?;
        for item in self.transfer_files(&job)? {
            let rel = item.relative.to_string_lossy();
            if c.query_row(
                "SELECT recovery FROM source_removals WHERE job_id=?1 AND relative=?2",
                params![job_id, rel],
                |r| r.get::<_, String>(0),
            )
            .optional()?
            .is_some()
            {
                continue;
            }
            let recovery = recovery_path(job_id, &item.relative);
            // Reconcile a crash after atomic rename but before the SQLite checkpoint.
            if let Err(e) = source.read(&item.relative) {
                if e.code == "IO_NOT_FOUND"
                    && source.read(&recovery).is_ok()
                    && fsops::hash_at(&source, &recovery, &mut || Ok(()))?
                        == item.sha256.clone().unwrap_or_default()
                {
                    c.execute("INSERT OR IGNORE INTO source_removals(job_id,relative,recovery) VALUES(?1,?2,?3)",params![job_id,rel,recovery.to_string_lossy()])?;
                    continue;
                }
                return Err(e);
            }
            self.verify_transfer_file(&job, &item, &source)?;
            pending.push(item.relative);
        }
        if pending.is_empty() {
            return Err(CoreError::new(
                "NOTHING_TO_REMOVE",
                "来源已暂存至可恢复目录，没有待移除项",
            ));
        }
        let plan=RemovalPlan{token:id(),job_id:job_id.into(),files:pending,expires_at:(chrono::Utc::now()+chrono::Duration::minutes(5)).to_rfc3339(),effect:"将这些来源文件移入来源磁盘的 .yingxu-transfer-recovery/<任务ID>/，可撤回；目标副本保留，不永久删除原片。".into()};
        self.store.put("removals", &plan.token, &plan)?;
        Ok(plan)
    }
    pub fn confirm_source_removal(
        &self,
        token: &str,
        confirmation: &str,
    ) -> CoreResult<serde_json::Value> {
        if confirmation != "将来源移入可恢复目录" {
            return Err(CoreError::new(
                "CONFIRMATION_REQUIRED",
                "请输入“将来源移入可恢复目录”以完成第二次确认",
            ));
        }
        let plan: RemovalPlan = self.store.get("removals", token)?;
        if chrono::DateTime::parse_from_rfc3339(&plan.expires_at)
            .map_err(|e| CoreError::new("INVALID_PLAN", e))?
            < chrono::Utc::now()
        {
            return Err(CoreError::new(
                "CONFIRMATION_EXPIRED",
                "请重新检查并生成来源移除计划",
            ));
        }
        let job = self.store.job(&plan.job_id)?;
        let source = SafeDir::open(&job.source)?;
        let items = self.transfer_files(&job)?;
        // Validate the entire batch before changing the first file.
        for rel in &plan.files {
            let item = items
                .iter()
                .find(|i| i.relative == *rel)
                .ok_or_else(|| CoreError::new("INVALID_PLAN", rel.display()))?;
            self.verify_transfer_file(&job, item, &source)?;
        }
        if self
            .store
            .connect()?
            .execute("DELETE FROM removals WHERE id=?1", [token])?
            != 1
        {
            return Err(CoreError::new("CONFIRMATION_ALREADY_USED", token));
        }
        self.store.audit(
            "source_removal_confirmed",
            &job.id,
            json!({"files":plan.files,"effect":plan.effect}),
        )?;
        let mut moved = vec![];
        for rel in &plan.files {
            let item = items.iter().find(|i| i.relative == *rel).unwrap();
            self.verify_transfer_file(&job, item, &source)?;
            let recovery = recovery_path(&job.id, rel);
            source.commit(rel, &recovery)?;
            // A competing process may replace a source immediately before rename.
            // Keep its data recoverable and try to put it back; never unlink it.
            if fsops::fingerprint(&source.read(&recovery)?.metadata()?) != item.fingerprint {
                let _ = source.commit(&recovery, rel);
                self.store.audit(
                    "source_changed_during_staging",
                    &job.id,
                    json!({"relative":rel,"recovery":recovery}),
                )?;
                return Err(CoreError::new(
                    "SOURCE_CHANGED",
                    "来源在暂存时变化，已保留文件供恢复",
                ));
            }
            self.store.connect()?.execute(
                "INSERT INTO source_removals(job_id,relative,recovery) VALUES(?1,?2,?3)",
                params![job.id, rel.to_string_lossy(), recovery.to_string_lossy()],
            )?;
            self.store.audit(
                "source_staged_for_recovery",
                &job.id,
                json!({"relative":rel,"recovery":recovery}),
            )?;
            moved.push(recovery);
        }
        self.store.connect()?.execute(
            "UPDATE jobs SET phase='source_staged_for_recovery' WHERE id=?1",
            [&job.id],
        )?;
        let mut report = self.report(&job.id)?;
        report["sourceSafety"] = json!("staged_for_recovery");
        report["recoveryFiles"] = json!(moved);
        report["job"]["phase"] = json!("source_staged_for_recovery");
        self.store.connect()?.execute(
            "UPDATE reports SET data=?1 WHERE job_id=?2",
            params![report.to_string(), job.id],
        )?;
        Ok(json!({"jobId":job.id,"staged":moved,"permanentlyDeleted":0}))
    }
    pub fn undo_source_removal(
        &self,
        job_id: &str,
        confirmed: bool,
    ) -> CoreResult<serde_json::Value> {
        if !confirmed {
            return Err(CoreError::new(
                "CONFIRMATION_REQUIRED",
                "撤回转移来源位置需要确认",
            ));
        }
        let job = self.store.job(job_id)?;
        let source = SafeDir::open(&job.source)?;
        let c = self.store.connect()?;
        let mut st = c.prepare(
            "SELECT relative,recovery FROM source_removals WHERE job_id=?1 ORDER BY relative",
        )?;
        let records = st
            .query_map([job_id], |r| {
                Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?))
            })?
            .collect::<Result<Vec<_>, _>>()?;
        drop(st);
        let mut restored = vec![];
        for (rel, recovery) in records {
            let item = job
                .items
                .iter()
                .find(|i| i.relative == Path::new(&rel))
                .ok_or_else(|| CoreError::new("INVALID_RECOVERY", &rel))?;
            if fsops::hash_at(&source, Path::new(&recovery), &mut || Ok(()))?
                != item.sha256.clone().unwrap_or_default()
            {
                return Err(CoreError::new(
                    "RECOVERY_CHANGED",
                    "可恢复文件发生变化，未覆盖原位置",
                ));
            }
            source.commit(Path::new(&recovery), Path::new(&rel))?;
            c.execute(
                "DELETE FROM source_removals WHERE job_id=?1 AND relative=?2",
                params![job_id, rel],
            )?;
            restored.push(rel);
        }
        self.store.audit(
            "source_removal_undone",
            job_id,
            json!({"restored":restored}),
        )?;
        c.execute(
            "UPDATE jobs SET phase='source_removal_undone' WHERE id=?1",
            [job_id],
        )?;
        let mut report = self.report(job_id)?;
        report["sourceSafety"] = json!("retained");
        report["job"]["phase"] = json!("source_removal_undone");
        c.execute(
            "UPDATE reports SET data=?1 WHERE job_id=?2",
            params![report.to_string(), job_id],
        )?;
        Ok(json!({"restored":restored}))
    }
}
fn recovery_path(job_id: &str, relative: &Path) -> PathBuf {
    PathBuf::from(".yingxu-transfer-recovery")
        .join(job_id)
        .join(relative)
}
