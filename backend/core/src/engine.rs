use crate::{
    db::Store,
    fsops::{self, SafeDir},
    model::*,
};
use rusqlite::{OptionalExtension, params};
use serde_json::{Value, json};
use std::{
    fs::OpenOptions,
    path::{Path, PathBuf},
};

#[derive(Clone)]
pub struct Engine {
    pub store: Store,
}
pub struct Worker {
    pub engine: Engine,
    _lock: std::fs::File,
}
impl Engine {
    pub fn open(root: impl AsRef<Path>) -> CoreResult<Self> {
        let store = Store::open(root)?;
        let c = store.connect()?;
        c.execute(
            "INSERT OR IGNORE INTO settings(id,data) VALUES('preferences',?1)",
            [default_settings().to_string()],
        )?;
        c.execute(
            "INSERT OR IGNORE INTO settings(id,data) VALUES('deviceId',?1)",
            [json!(id()).to_string()],
        )?;
        let p = Preset {
            id: "default-camera".into(),
            name: "相机导入".into(),
            fields: vec!["date".into(), "event".into(), "device".into()],
            is_default: true,
            version: 1,
        };
        c.execute(
            "INSERT OR IGNORE INTO presets(id,data) VALUES(?1,?2)",
            params![p.id, serde_json::to_string(&p)?],
        )?;
        Ok(Self { store })
    }
    pub fn preview(&self, mut request: PlanRequest) -> CoreResult<Plan> {
        if request.name.trim().is_empty() || request.name.len() > 300 {
            return Err(CoreError::new("INVALID_NAME", "任务名称不能为空或过长"));
        }
        if request.destinations.is_empty() || request.destinations.len() > 8 {
            return Err(CoreError::new("INVALID_TARGETS", "请选择 1–8 个本地目的地"));
        }
        if request.transfer && !request.verify {
            return Err(CoreError::new(
                "TRANSFER_REQUIRES_VERIFY",
                "转移必须校验 SHA-256",
            ));
        }
        request.source = fsops::local_directory(&request.source)?;
        if request.source.starts_with(&self.store.root)
            || self.store.root.starts_with(&request.source)
        {
            return Err(CoreError::new(
                "APP_DATA_OVERLAP",
                "来源包含应用数据库或应用数据包含来源",
            ));
        }
        let mut identities = vec![];
        let mut spaces = vec![];
        let mut previous: Vec<PathBuf> = vec![];
        for p in &mut request.destinations {
            *p = fsops::local_directory(p)?;
            if p.starts_with(&request.source)
                || request.source.starts_with(&*p)
                || p.starts_with(&self.store.root)
                || self.store.root.starts_with(&*p)
                || previous
                    .iter()
                    .any(|q| p.starts_with(q) || q.starts_with(&*p))
            {
                return Err(CoreError::new(
                    "OVERLAPPING_PATHS",
                    "来源、目的地和应用数据目录不能相同或相互嵌套",
                ));
            }
            let dir = SafeDir::open(p)?;
            let probe = PathBuf::from(format!(".yingxu-probe-{}", id()));
            let f = dir.create(&probe)?;
            let fp = fsops::fingerprint(&f.metadata()?);
            f.sync_all()?;
            drop(f);
            dir.cleanup_temp(&probe, &fp.identity);
            spaces.push(fs2::available_space(&*p)?);
            identities.push(fsops::fingerprint(&std::fs::metadata(&*p)?));
            previous.push(p.clone());
        }
        if let Some(p) = &request.subdirectory {
            fsops::relative(p)?;
        }
        let mut files = fsops::scan(&request.source)?;
        if !request.selected.is_empty() {
            for rel in &request.selected {
                fsops::relative(rel)?;
                if !files.iter().any(|f| f.relative == *rel) {
                    return Err(CoreError::new("SELECTION_NOT_FOUND", rel.display()));
                }
            }
            files.retain(|f| request.selected.contains(&f.relative));
        }
        if files.is_empty() {
            return Err(CoreError::new("EMPTY_SOURCE", "来源没有可备份的普通文件"));
        }
        let total_bytes = files.iter().try_fold(0u64, |n, f| {
            n.checked_add(f.fingerprint.bytes)
                .ok_or_else(|| CoreError::new("SIZE_OVERFLOW", "容量超出支持范围"))
        })?;
        if spaces
            .iter()
            .any(|free| *free < total_bytes.saturating_add(1024 * 1024))
        {
            return Err(CoreError::new(
                "INSUFFICIENT_SPACE",
                "目的地空间不足（需预留 1 MiB）",
            ));
        }
        let plan = Plan {
            id: id(),
            created_at: now(),
            source_identity: fsops::fingerprint(&std::fs::metadata(&request.source)?),
            request,
            files,
            total_bytes,
            target_identities: identities,
            available_bytes: spaces,
        };
        self.store.put("plans", &plan.id, &plan)?;
        Ok(plan)
    }
    pub fn start(&self, plan_id: &str, confirmed: bool, idempotency_key: &str) -> CoreResult<Job> {
        self.start_context(plan_id, confirmed, idempotency_key, None)
    }
    pub(crate) fn start_context(
        &self,
        plan_id: &str,
        confirmed: bool,
        idempotency_key: &str,
        context: Option<(&str, Value)>,
    ) -> CoreResult<Job> {
        if !confirmed {
            return Err(CoreError::new(
                "CONFIRMATION_REQUIRED",
                "请确认来源、目标和命名计划",
            ));
        }
        if idempotency_key.is_empty() || idempotency_key.len() > 128 {
            return Err(CoreError::new("IDEMPOTENCY_KEY_REQUIRED", "必须提供幂等键"));
        }
        let mut c = self.store.connect()?;
        if let Some(existing) = c
            .query_row(
                "SELECT id FROM jobs WHERE idempotency_key=?1",
                [idempotency_key],
                |r| r.get::<_, String>(0),
            )
            .optional()?
        {
            let j = self.store.job(&existing)?;
            if !context.as_ref().map_or_else(
                || j.options["planId"] == plan_id,
                |(kind, options)| {
                    j.kind == *kind
                        && options.as_object().is_some_and(|fields| {
                            fields.iter().all(|(k, v)| j.options.get(k) == Some(v))
                        })
                },
            ) {
                return Err(CoreError::new(
                    "IDEMPOTENCY_CONFLICT",
                    "此幂等键已用于其他计划",
                ));
            }
            return Ok(j);
        }
        let p: Plan = self.store.get("plans", plan_id)?;
        let created = chrono::DateTime::parse_from_rfc3339(&p.created_at)
            .map_err(|e| CoreError::new("INVALID_PLAN", e))?;
        if chrono::Utc::now()
            .signed_duration_since(created)
            .num_minutes()
            > 30
        {
            return Err(CoreError::new(
                "PLAN_EXPIRED",
                "预检计划已超过 30 分钟，请重新扫描",
            ));
        }
        if fsops::fingerprint(&std::fs::metadata(&p.request.source)?).identity
            != p.source_identity.identity
        {
            return Err(CoreError::new("SOURCE_CHANGED", "来源设备已变化"));
        }
        let source = SafeDir::open(&p.request.source)?;
        for f in &p.files {
            if fsops::fingerprint(&source.read(&f.relative)?.metadata()?) != f.fingerprint {
                return Err(CoreError::new("SOURCE_CHANGED", f.relative.display()));
            }
        }
        for (i, path) in p.request.destinations.iter().enumerate() {
            SafeDir::open(path)?;
            if fsops::fingerprint(&std::fs::metadata(path)?).identity
                != p.target_identities[i].identity
            {
                return Err(CoreError::new("TARGET_CHANGED", path.display()));
            }
            if fs2::available_space(path)? < p.total_bytes.saturating_add(1024 * 1024) {
                return Err(CoreError::new("INSUFFICIENT_SPACE", path.display()));
            }
        }
        let job_id = id();
        let targets: Vec<Target> = p
            .request
            .destinations
            .iter()
            .enumerate()
            .map(|(i, path)| Target {
                id: id(),
                path: path.clone(),
                identity: p.target_identities[i].clone(),
                state: "queued".into(),
                copied: 0,
                skipped: 0,
                verified: 0,
                failed: 0,
            })
            .collect();
        let mut job = Job {
            id: job_id.clone(),
            name: p.request.name,
            kind: if p.request.transfer {
                "transfer"
            } else {
                "backup"
            }
            .into(),
            state: "queued".into(),
            phase: "queued".into(),
            source: p.request.source,
            source_identity: p.source_identity,
            verify: p.request.verify,
            created_at: now(),
            updated_at: now(),
            total_bytes: p.total_bytes,
            files: p.files.len(),
            progress: 0.0,
            targets: targets.clone(),
            items: vec![],
            error: None,
            options: json!({"planId":plan_id}),
            runtime: json!({}),
        };
        if let Some((kind, options)) = &context {
            job.kind = kind.to_string();
            if let Some(fields) = options.as_object() {
                for (k, v) in fields {
                    job.options[k] = v.clone();
                }
            }
        }
        let tx = c.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        // Re-check the key under the write transaction to prevent concurrent duplicate starts.
        if let Some(existing) = tx
            .query_row(
                "SELECT id FROM jobs WHERE idempotency_key=?1",
                [idempotency_key],
                |r| r.get::<_, String>(0),
            )
            .optional()?
        {
            drop(tx);
            let j = self.store.job(&existing)?;
            if !context.as_ref().map_or_else(
                || j.options["planId"] == plan_id,
                |(kind, options)| {
                    j.kind == *kind
                        && options.as_object().is_some_and(|fields| {
                            fields.iter().all(|(k, v)| j.options.get(k) == Some(v))
                        })
                },
            ) {
                return Err(CoreError::new(
                    "IDEMPOTENCY_CONFLICT",
                    "此幂等键已用于其他计划",
                ));
            }
            return Ok(j);
        }
        tx.execute("INSERT INTO jobs(id,state,phase,created_at,data,idempotency_key) VALUES(?1,'queued','queued',?2,?3,?4)",params![job.id,job.created_at,serde_json::to_string(&job)?,idempotency_key])?;
        for t in &targets {
            tx.execute(
                "INSERT INTO job_targets(id,job_id,path,state) VALUES(?1,?2,?3,'queued')",
                params![t.id, job.id, t.path.to_string_lossy()],
            )?;
        }
        for f in &p.files {
            let path = job.source.join(&f.relative);
            let path = path.to_string_lossy();
            let media_id = tx
                .query_row(
                    "SELECT id FROM media_items WHERE path=?1",
                    [path.as_ref()],
                    |r| r.get::<_, String>(0),
                )
                .optional()?
                .unwrap_or_else(id);
            let metadata = json!({"timestampSource":"filesystem_modified","fingerprint":f.fingerprint,"exifStatus":"not_analyzed"});
            let sql_bytes = i64::try_from(f.fingerprint.bytes)
                .map_err(|_| CoreError::new("FILE_TOO_LARGE", &path))?;
            tx.execute("INSERT INTO media_items(id,path,name,kind,bytes,metadata) VALUES(?1,?2,?3,?4,?5,?6) ON CONFLICT(path) DO UPDATE SET bytes=excluded.bytes,metadata=excluded.metadata,sha256=NULL,quality_state='unanalyzed'",params![media_id,path,path.file_name_or_empty(),f.kind,sql_bytes,metadata.to_string()])?;
            tx.execute(
                "DELETE FROM media_derivatives WHERE media_id=?1",
                [&media_id],
            )?;
            for t in &targets {
                let rel = p
                    .request
                    .subdirectory
                    .as_ref()
                    .map(|x| x.join(&f.relative))
                    .unwrap_or_else(|| f.relative.clone());
                let item = JobItem {
                    id: id(),
                    target_id: t.id.clone(),
                    relative: f.relative.clone(),
                    output: t.path.join(rel),
                    fingerprint: f.fingerprint.clone(),
                    state: "pending".into(),
                    sha256: None,
                    verified: false,
                    error: None,
                    media_id: media_id.clone(),
                    expected_sha256: f.expected_sha256.clone(),
                    source_root: job.source.clone(),
                    source_root_identity: job.source_identity.clone(),
                };
                tx.execute("INSERT INTO job_items(id,job_id,target_id,state,data) VALUES(?1,?2,?3,'pending',?4)",params![item.id,job.id,t.id,serde_json::to_string(&item)?])?;
                job.items.push(item);
            }
        }
        tx.commit()?;
        self.store.audit(
            "start_job",
            &job.id,
            json!({"planId":plan_id,"confirmed":true}),
        )?;
        self.store.job(&job.id)
    }
    pub fn control(&self, job_id: &str, action: &str, target_id: Option<&str>) -> CoreResult<Job> {
        let job = self.store.job(job_id)?;
        let mut c = self.store.connect()?;
        let tx = c.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        let state: String =
            tx.query_row("SELECT state FROM jobs WHERE id=?1", [job_id], |r| r.get(0))?;
        let next = match action {
            "pause" if ["running", "queued"].contains(&state.as_str()) => "paused",
            "resume" if state == "paused" => "queued",
            "cancel" if ["running", "queued", "paused"].contains(&state.as_str()) => "cancelled",
            "retry" if ["partial", "failed", "cancelled"].contains(&state.as_str()) => "queued",
            _ => {
                return Err(CoreError::new(
                    "INVALID_TRANSITION",
                    format!("{state} → {action}"),
                ));
            }
        };
        if let Some(t) = target_id
            && (action != "retry"
                || !job
                    .targets
                    .iter()
                    .any(|x| x.id == t && ["failed", "partial"].contains(&x.state.as_str())))
        {
            return Err(CoreError::new("INVALID_TARGET_RETRY", t));
        }
        if action == "retry" {
            for mut item in job
                .items
                .iter()
                .filter(|i| {
                    !["copied", "skipped"].contains(&i.state.as_str())
                        && target_id.is_none_or(|t| i.target_id == t)
                })
                .cloned()
            {
                item.state = "pending".into();
                item.error = None;
                tx.execute(
                    "UPDATE job_items SET state='pending',data=?1 WHERE id=?2",
                    params![serde_json::to_string(&item)?, item.id],
                )?;
            }
            let mut next_job = job.clone();
            next_job.error = None;
            tx.execute(
                "UPDATE jobs SET data=?1 WHERE id=?2",
                params![serde_json::to_string(&next_job)?, job_id],
            )?;
        }
        tx.execute(
            "UPDATE jobs SET state=?1,phase=?2 WHERE id=?3",
            params![next, format!("user_{action}"), job_id],
        )?;
        tx.commit()?;
        self.store
            .audit(action, job_id, json!({"targetId":target_id}))?;
        self.store.job(job_id)
    }
    pub fn worker(&self) -> CoreResult<Worker> {
        let f = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(self.store.root.join("worker.lockfile"))?;
        fs2::FileExt::try_lock_exclusive(&f)
            .map_err(|_| CoreError::new("WORKER_ALREADY_RUNNING", "同一数据库只允许一个 worker"))?;
        let c = self.store.connect()?;
        c.execute("UPDATE jobs SET state='paused',phase='interrupted_resume_required' WHERE state='running'",[])?;
        self.cleanup_pending_writes()?;
        Ok(Worker {
            engine: self.clone(),
            _lock: f,
        })
    }
    pub fn cleanup_pending_writes(&self) -> CoreResult<()> {
        let c = self.store.connect()?;
        let mut q = c.prepare(
            "SELECT id,target_root,root_identity,path,file_identity FROM pending_writes",
        )?;
        let rows = q
            .query_map([], |r| {
                Ok((
                    r.get::<_, String>(0)?,
                    r.get::<_, String>(1)?,
                    r.get::<_, String>(2)?,
                    r.get::<_, String>(3)?,
                    r.get::<_, String>(4)?,
                ))
            })?
            .collect::<Result<Vec<_>, _>>()?;
        drop(q);
        for (key, root, root_id, path, file_id) in rows {
            let Ok(dir) = SafeDir::open(Path::new(&root)) else {
                continue;
            };
            if fsops::fingerprint(&std::fs::metadata(&dir.path)?).identity != root_id {
                continue;
            }
            let rel = Path::new(&path);
            dir.cleanup_temp(rel, &file_id);
            if dir.read(rel).is_err_and(|e| e.code == "IO_NOT_FOUND") {
                c.execute("DELETE FROM pending_writes WHERE id=?1", [key])?;
            }
        }
        Ok(())
    }
    pub fn finalize(&self, job_id: &str) -> CoreResult<Job> {
        let settings: Value = self.store.get("settings", "preferences")?;
        let device: String = self.store.get("settings", "deviceId")?;
        let mut c = self.store.connect()?;
        let tx = c.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        let mut job = self.store.job(job_id)?;
        if !["paused", "cancelled", "queued"].contains(&job.state.as_str()) {
            let failures = job.items.iter().filter(|i| i.state == "failed").count();
            let good = job
                .items
                .iter()
                .filter(|i| ["copied", "skipped"].contains(&i.state.as_str()))
                .count();
            job.state = if failures > 0 || job.error.is_some() {
                if good > 0 { "partial" } else { "failed" }
            } else if good == job.items.len() {
                "success"
            } else {
                "failed"
            }
            .into();
            job.phase = if job.kind == "transfer" && job.state == "success" {
                "awaiting_source_removal_confirmation"
            } else if !job.verify && job.state == "success" {
                "copied_without_verification"
            } else {
                &job.state
            }
            .into();
        }
        job.updated_at = now();
        let report = json!({"schemaVersion":1,"appVersion":env!("CARGO_PKG_VERSION"),"sourceSafety":"retained","job":job});
        tx.execute(
            "UPDATE jobs SET state=?1,phase=?2,data=?3 WHERE id=?4",
            params![job.state, job.phase, serde_json::to_string(&job)?, job.id],
        )?;
        tx.execute("INSERT INTO reports(job_id,data,created_at) VALUES(?1,?2,?3) ON CONFLICT(job_id) DO UPDATE SET data=excluded.data,created_at=excluded.created_at",params![job.id,report.to_string(),now()])?;
        for t in &job.targets {
            tx.execute(
                "UPDATE job_targets SET state=?1 WHERE id=?2",
                params![t.state, t.id],
            )?;
        }
        if settings["syncHistory"] == true {
            let mut payload = json!({"jobId":job.id,"name":job.name,"state":job.state,"phase":job.phase,"files":job.files,"bytes":job.total_bytes,"progress":job.progress,"targets":job.targets.iter().map(|t|json!({"id":t.id,"state":t.state,"copied":t.copied,"skipped":t.skipped,"verified":t.verified,"failed":t.failed})).collect::<Vec<_>>()});
            if settings["syncPaths"] == true {
                payload["source"] = json!(job.source);
                payload["paths"] = json!(job.targets.iter().map(|t| &t.path).collect::<Vec<_>>());
            }
            tx.execute(
                "INSERT INTO outbox_events(event_id,device_id,job_id,payload) VALUES(?1,?2,?3,?4)",
                params![id(), device, job.id, payload.to_string()],
            )?;
        }
        tx.commit()?;
        Ok(job)
    }
}

impl Worker {
    pub fn run_next(&self) -> CoreResult<Option<Job>> {
        let e = &self.engine;
        let mut c = e.store.connect()?;
        let tx = c.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        let job_id = tx
            .query_row(
                "SELECT id FROM jobs WHERE state='queued' ORDER BY created_at,id LIMIT 1",
                [],
                |r| r.get::<_, String>(0),
            )
            .optional()?;
        let Some(job_id) = job_id else {
            return Ok(None);
        };
        tx.execute(
            "UPDATE jobs SET state='running',phase='copying' WHERE id=?1",
            [&job_id],
        )?;
        tx.commit()?;
        let job = e.store.job(&job_id)?;
        for mut item in job.items.iter().filter(|i| i.state == "pending").cloned() {
            if e.store.state(&job_id)? != "running" {
                break;
            }
            let result = (|| {
                let source = SafeDir::open(&item.source_root)?;
                let target = job
                    .targets
                    .iter()
                    .find(|t| t.id == item.target_id)
                    .ok_or_else(|| CoreError::new("TARGET_NOT_FOUND", &item.target_id))?;
                if fsops::fingerprint(&std::fs::metadata(&item.source_root)?).identity
                    != item.source_root_identity.identity
                    || fsops::fingerprint(&std::fs::metadata(&target.path)?).identity
                        != target.identity.identity
                {
                    return Err(CoreError::new(
                        "ROOT_CHANGED",
                        "来源或目标在创建任务后被替换",
                    ));
                }
                let target = SafeDir::open(&target.path)?;
                let relative_output = item
                    .output
                    .strip_prefix(&target.path)
                    .map_err(|e| CoreError::new("UNSAFE_PATH", e))?;
                let mut check = || {
                    let state = e.store.state(&job_id)?;
                    if state != "running" {
                        Err(CoreError::new("INTERRUPTED", state))
                    } else {
                        Ok(())
                    }
                };
                let mut journal = |path: &Path, fp: &Fingerprint| {
                    e.store.connect()?.execute("INSERT INTO pending_writes(id,item_id,target_root,root_identity,path,file_identity,created_at) VALUES(?1,?2,?3,?4,?5,?6,?7)",params![id(),item.id,target.path.to_string_lossy(),fsops::fingerprint(&std::fs::metadata(&target.path)?).identity,path.to_string_lossy(),fp.identity,now()])?;
                    Ok(())
                };
                let started = std::time::Instant::now();
                let mut last = std::time::Instant::now();
                let mut phase = "";
                let mut progress = |p: fsops::CopyProgress| {
                    if phase != p.phase || last.elapsed().as_millis() >= 200 {
                        let speed = if p.phase == "copying" {
                            (p.bytes as f64 / started.elapsed().as_secs_f64().max(0.001)) as u64
                        } else {
                            0
                        };
                        let data = json!({"itemId":item.id,"currentFile":item.relative,"currentBytes":p.bytes,"currentTotalBytes":p.total,"speedBytesPerSecond":speed,"updatedAt":now()});
                        e.store.connect()?.execute(
                            "UPDATE jobs SET runtime=?1,phase=?2 WHERE id=?3 AND state='running'",
                            params![data.to_string(), p.phase, job_id],
                        )?;
                        last = std::time::Instant::now();
                        phase = p.phase;
                    }
                    Ok(())
                };
                fsops::copy_observed(
                    fsops::CopySpec {
                        source: &source,
                        relative: &item.relative,
                        expected: &item.fingerprint,
                        target: &target,
                        output: relative_output,
                        verify: job.verify,
                        expected_digest: item.expected_sha256.as_deref(),
                    },
                    &mut check,
                    &mut journal,
                    &mut progress,
                )
            })();
            match result {
                Ok(copied) => {
                    item.output = copied.output;
                    item.sha256 = Some(copied.sha256.clone());
                    item.state = if copied.skipped { "skipped" } else { "copied" }.into();
                    item.verified = copied.verified;
                    let mut db = e.store.connect()?;
                    let tx = db.transaction()?;
                    tx.execute(
                        "UPDATE job_items SET state=?1,data=?2 WHERE id=?3",
                        params![item.state, serde_json::to_string(&item)?, item.id],
                    )?;
                    tx.execute(
                        "UPDATE media_items SET sha256=?1 WHERE id=?2",
                        params![copied.sha256, item.media_id],
                    )?;
                    tx.execute("INSERT INTO replicas(id,job_id,media_id,path,sha256,verified,checked_at) VALUES(?1,?2,?3,?4,?5,?6,?7) ON CONFLICT(job_id,path) DO UPDATE SET sha256=excluded.sha256,verified=excluded.verified,checked_at=excluded.checked_at",params![id(),job_id,item.media_id,item.output.to_string_lossy(),copied.sha256,copied.verified,now()])?;
                    tx.commit()?;
                }
                Err(err) if err.code == "INTERRUPTED" => break,
                Err(err) => {
                    item.state = "failed".into();
                    item.error = Some(err.to_string());
                    e.store.connect()?.execute(
                        "UPDATE job_items SET state='failed',data=?1 WHERE id=?2",
                        params![serde_json::to_string(&item)?, item.id],
                    )?;
                }
            }
            e.cleanup_pending_writes()?;
        }
        if job.kind == "delivery"
            && e.store.state(&job_id)? == "running"
            && let Err(err) = e.finish_delivery(&job_id)
        {
            let mut j = e.store.job(&job_id)?;
            j.error = Some(err.to_string());
            e.store.connect()?.execute(
                "UPDATE jobs SET data=?1 WHERE id=?2",
                params![serde_json::to_string(&j)?, job_id],
            )?;
        }
        Ok(Some(e.finalize(&job_id)?))
    }
}
pub fn default_settings() -> Value {
    json!({"theme":"light","startOnLogin":true,"backgroundOnClose":true,"verify":true,"autoAnalyze":true,"similarity":88,"maxUploadMbps":0,"syncHistory":true,"syncAgent":true,"syncThumbnails":true,"syncPaths":false,"webhookEnabled":false,"webhookUrl":"","aiProvider":"本地规则优先"})
}

trait FileName {
    fn file_name_or_empty(&self) -> String;
}
impl FileName for std::borrow::Cow<'_, str> {
    fn file_name_or_empty(&self) -> String {
        Path::new(self.as_ref())
            .file_name()
            .unwrap_or_default()
            .to_string_lossy()
            .into_owned()
    }
}
