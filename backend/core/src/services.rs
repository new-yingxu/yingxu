use crate::{
    engine::{Engine, default_settings},
    fsops::{self, SafeDir},
    model::*,
};
use rusqlite::{OptionalExtension, params};
use serde_json::{Value, json};
use std::path::{Path, PathBuf};

impl Engine {
    pub fn search(&self, query: &str, kind: &str, quality: &str) -> CoreResult<Vec<Value>> {
        if query.len() > 500
            || !["all", "photo", "video", "other"].contains(&kind)
            || !["all", "good", "review", "unanalyzed"].contains(&quality)
        {
            return Err(CoreError::new("INVALID_FILTER", "检索条件无效"));
        }
        let c = self.store.connect()?;
        let mut s=c.prepare("SELECT id,path,name,kind,bytes,sha256,quality_state,metadata FROM media_items WHERE (?1='all' OR kind=?1) AND (?2='all' OR quality_state=?2) AND (?3='' OR instr(lower(name || ' ' || path || ' ' || metadata),lower(?3))>0 OR rowid IN (SELECT rowid FROM media_fts WHERE media_fts MATCH ?4)) ORDER BY name,id LIMIT 500")?;
        let literal = format!("\"{}\"*", query.replace('"', "\"\""));
        let rows=s.query_map(params![kind,quality,query,if query.is_empty(){"\"__empty__\""}else{&literal}],|r|Ok(json!({"id":r.get::<_,String>(0)?,"path":r.get::<_,String>(1)?,"name":r.get::<_,String>(2)?,"kind":r.get::<_,String>(3)?,"bytes":r.get::<_,i64>(4)?,"sha256":r.get::<_,Option<String>>(5)?,"qualityState":r.get::<_,String>(6)?,"metadata":serde_json::from_str::<Value>(&r.get::<_,String>(7)?).unwrap_or(Value::Null)})))?;
        rows.map(|r| Ok(r?)).collect()
    }
    pub fn history(&self, query: &str, state: &str) -> CoreResult<Vec<Job>> {
        Ok(self
            .store
            .jobs()?
            .into_iter()
            .filter(|j| {
                (state == "all" || j.state == state)
                    && (query.is_empty()
                        || j.name.to_lowercase().contains(&query.to_lowercase())
                        || j.id.contains(query))
            })
            .collect())
    }
    pub fn rename_history(&self, key: &str, name: &str, confirmed: bool) -> CoreResult<()> {
        if !confirmed {
            return Err(CoreError::new(
                "CONFIRMATION_REQUIRED",
                "修改记录名称需要确认",
            ));
        }
        if name.trim().is_empty() || name.len() > 300 {
            return Err(CoreError::new("INVALID_NAME", "名称为空或过长"));
        }
        let mut c = self.store.connect()?;
        let tx = c.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        let text: String =
            tx.query_row("SELECT data FROM jobs WHERE id=?1", [key], |r| r.get(0))?;
        let mut job: Job = serde_json::from_str(&text)?;
        job.name = name.into();
        job.updated_at = now();
        tx.execute(
            "UPDATE jobs SET data=?1 WHERE id=?2",
            params![serde_json::to_string(&job)?, key],
        )?;
        let report: Option<String> = tx
            .query_row("SELECT data FROM reports WHERE job_id=?1", [key], |r| {
                r.get(0)
            })
            .optional()?;
        if let Some(text) = report {
            let mut report: Value = serde_json::from_str(&text)?;
            report["job"]["name"] = json!(name);
            tx.execute(
                "UPDATE reports SET data=?1 WHERE job_id=?2",
                params![report.to_string(), key],
            )?;
        }
        tx.commit()?;
        self.store
            .audit("rename_history", key, json!({"name":name}))
    }
    pub fn clear_history(&self, confirmation: &str) -> CoreResult<()> {
        if confirmation != "清空归档记录" {
            return Err(CoreError::new(
                "CONFIRMATION_REQUIRED",
                "请输入“清空归档记录”；媒体文件不会删除",
            ));
        }
        let mut c = self.store.connect()?;
        let tx = c.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        let active: i64 = tx.query_row(
            "SELECT COUNT(*) FROM jobs WHERE state IN ('running','queued','paused')",
            [],
            |r| r.get(0),
        )?;
        if active > 0 {
            return Err(CoreError::new("ACTIVE_JOBS_EXIST", "请先处理活动任务"));
        }
        tx.execute("DELETE FROM reports", [])?;
        tx.execute("DELETE FROM jobs", [])?;
        tx.commit()?;
        self.store.audit(
            "clear_history",
            "history",
            json!({"mediaRetained":true,"replicasRetained":true}),
        )
    }
    pub fn update_settings(&self, patch: Value) -> CoreResult<Value> {
        let Some(fields) = patch.as_object() else {
            return Err(CoreError::new("INVALID_SETTINGS", "设置必须是对象"));
        };
        let defaults = default_settings();
        let mut c = self.store.connect()?;
        let tx = c.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        let text: String = tx.query_row(
            "SELECT data FROM settings WHERE id='preferences'",
            [],
            |r| r.get(0),
        )?;
        let mut next: Value = serde_json::from_str(&text)?;
        let previous = next.clone();
        for (k, v) in fields {
            let Some(default) = defaults.get(k) else {
                return Err(CoreError::new("UNKNOWN_SETTING", k));
            };
            if (default.is_boolean() && !v.is_boolean())
                || (default.is_string() && !v.is_string())
                || (default.is_number() && !v.is_u64())
            {
                return Err(CoreError::new("INVALID_SETTING_TYPE", k));
            }
            if k == "theme" && !matches!(v.as_str(), Some("light" | "dark")) {
                return Err(CoreError::new("INVALID_THEME", k));
            }
            if (k == "similarity" && v.as_u64().unwrap_or(0) > 100)
                || (k == "maxUploadMbps" && v.as_u64().unwrap_or(0) > 100000)
            {
                return Err(CoreError::new("INVALID_SETTING_RANGE", k));
            }
            if k == "webhookEnabled" && *v == true {
                return Err(CoreError::new(
                    "PROVIDER_CONFIGURATION_REQUIRED",
                    "Webhook 发送通道尚未配置，未启用发送",
                ));
            }
            if k == "webhookUrl" && v.as_str().is_some_and(|s| !s.is_empty()) {
                return Err(CoreError::new(
                    "SECURE_STORAGE_REQUIRED",
                    "Webhook 地址可能包含秘密，不能明文保存到 SQLite",
                ));
            }
            if k == "aiProvider" && v.as_str() != Some("本地规则优先") {
                return Err(CoreError::new(
                    "PROVIDER_CONFIGURATION_REQUIRED",
                    "模型供应商需安全凭据配置后启用",
                ));
            }
            next[k] = v.clone();
        }
        // A preference change must not mutate the identity/content of an already-issued event.
        // Retire unsent path-bearing rows and append sanitized replacements at new sequences.
        if previous["syncPaths"] == true && next["syncPaths"] != true {
            let mut q=tx.prepare("SELECT sequence,device_id,job_id,event_type,occurred_at,payload FROM outbox_events WHERE sent=0")?;
            let rows = q
                .query_map([], |r| {
                    Ok((
                        r.get::<_, i64>(0)?,
                        r.get::<_, String>(1)?,
                        r.get::<_, String>(2)?,
                        r.get::<_, String>(3)?,
                        r.get::<_, String>(4)?,
                        r.get::<_, String>(5)?,
                    ))
                })?
                .collect::<Result<Vec<_>, _>>()?;
            drop(q);
            for (seq, device, job, event, at, body) in rows {
                let mut body: Value = serde_json::from_str(&body)?;
                if let Some(map) = body.as_object_mut()
                    && (map.contains_key("source") || map.contains_key("paths"))
                {
                    map.remove("source");
                    map.remove("paths");
                    tx.execute("UPDATE outbox_events SET sent=-1 WHERE sequence=?1", [seq])?;
                    tx.execute("INSERT INTO outbox_events(event_id,device_id,job_id,event_type,occurred_at,payload) VALUES(?1,?2,?3,?4,?5,?6)",params![id(),device,job,event,at,body.to_string()])?;
                }
            }
        }
        tx.execute(
            "UPDATE settings SET data=?1 WHERE id='preferences'",
            [next.to_string()],
        )?;
        tx.commit()?;
        self.store.audit(
            "update_settings",
            "preferences",
            json!({"fields":fields.keys().collect::<Vec<_>>()}),
        )?;
        Ok(next)
    }
    pub fn presets(&self) -> CoreResult<Vec<Preset>> {
        let c = self.store.connect()?;
        let mut s = c.prepare("SELECT data FROM presets ORDER BY id")?;
        s.query_map([], |r| r.get::<_, String>(0))?
            .map(|r| Ok(serde_json::from_str(&r?)?))
            .collect()
    }
    pub fn save_preset(&self, p: Preset) -> CoreResult<Preset> {
        if p.id.is_empty()
            || p.id.len() > 128
            || p.name.trim().is_empty()
            || p.fields.is_empty()
            || p.fields.len() > 6
            || p.fields.iter().any(|f| {
                !["date", "event", "location", "device", "kind", "custom"].contains(&f.as_str())
            })
        {
            return Err(CoreError::new("INVALID_PRESET", "预设字段不在白名单内"));
        }
        let mut c = self.store.connect()?;
        let tx = c.transaction()?;
        if p.is_default {
            let mut s = tx.prepare("SELECT id,data FROM presets")?;
            let records = s
                .query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)))?
                .collect::<Result<Vec<_>, _>>()?;
            drop(s);
            for (key, data) in records {
                let mut old: Preset = serde_json::from_str(&data)?;
                old.is_default = false;
                tx.execute(
                    "UPDATE presets SET data=?1 WHERE id=?2",
                    params![serde_json::to_string(&old)?, key],
                )?;
            }
        }
        tx.execute("INSERT INTO presets(id,data) VALUES(?1,?2) ON CONFLICT(id) DO UPDATE SET data=excluded.data",params![p.id,serde_json::to_string(&p)?])?;
        tx.commit()?;
        Ok(p)
    }
    pub fn naming_preview(&self, preset_id: &str, values: &Value) -> CoreResult<PathBuf> {
        let p: Preset = self.store.get("presets", preset_id)?;
        let mut output = PathBuf::new();
        for key in &p.fields {
            let value = values
                .get(key)
                .and_then(Value::as_str)
                .ok_or_else(|| CoreError::new("NAMING_VALUE_REQUIRED", key))?;
            safe_component(value)?;
            output.push(value);
        }
        fsops::relative(&output)?;
        Ok(output)
    }
    pub fn report(&self, job_id: &str) -> CoreResult<Value> {
        let c = self.store.connect()?;
        let text: String = c
            .query_row("SELECT data FROM reports WHERE job_id=?1", [job_id], |r| {
                r.get(0)
            })
            .map_err(|_| CoreError::new("REPORT_NOT_READY", "任务尚未生成报告"))?;
        Ok(serde_json::from_str(&text)?)
    }
    pub fn export_report(
        &self,
        job_id: &str,
        destination: &Path,
        format: &str,
        confirmed: bool,
    ) -> CoreResult<PathBuf> {
        if !confirmed {
            return Err(CoreError::new(
                "CONFIRMATION_REQUIRED",
                "报告可能包含本地路径，导出前需要确认",
            ));
        }
        let report = self.report(job_id)?;
        let job: Job = serde_json::from_value(report["job"].clone())?;
        let bytes=match format {
            "json"=>serde_json::to_vec_pretty(&report)?,
            "md"|"txt"=>format!("# 影序任务报告\n\n任务：{}\nID：{}\n状态：{} / {}\n来源：{}\n来源安全：{}\n数量：{}\n容量：{} bytes\nSHA-256：{}\n\n{}",job.name.replace(['<','>'],""),job.id,job.state,job.phase,job.source.display(),report["sourceSafety"],job.files,job.total_bytes,job.verify,job.items.iter().map(|i|format!("- {} → {} | {} | verified={} | sha256={} | {}",i.relative.display(),i.output.display(),i.state,i.verified,i.sha256.as_deref().unwrap_or(""),i.error.as_deref().unwrap_or(""))).collect::<Vec<_>>().join("\n")).into_bytes(),
            "csv"=>{let mut text="source,target,state,verified,sha256,error\r\n".to_owned();for i in &job.items{text.push_str(&[i.relative.to_string_lossy().as_ref(),i.output.to_string_lossy().as_ref(),&i.state,&i.verified.to_string(),i.sha256.as_deref().unwrap_or(""),i.error.as_deref().unwrap_or("")].map(csv).join(","));text.push_str("\r\n");}text.into_bytes()},
            _=>return Err(CoreError::new("INVALID_REPORT_FORMAT",format)),
        };
        let root = SafeDir::open(destination)?;
        let filename = PathBuf::from(format!("{job_id}.report.{format}"));
        fsops::write_new(&root, &filename, &bytes)?;
        self.store
            .audit("export_report", job_id, json!({"format":format}))?;
        Ok(root.path.join(filename))
    }
    pub fn audit_replicas(&self) -> CoreResult<Value> {
        let c = self.store.connect()?;
        let mut q = c.prepare("SELECT id,path,sha256,media_id FROM replicas ORDER BY path")?;
        let rows = q
            .query_map([], |r| {
                Ok((
                    r.get::<_, String>(0)?,
                    r.get::<_, String>(1)?,
                    r.get::<_, String>(2)?,
                    r.get::<_, String>(3)?,
                ))
            })?
            .collect::<Result<Vec<_>, _>>()?;
        drop(q);
        let mut issues = vec![];
        let mut healthy = 0;
        for (key, path, expected, _) in &rows {
            let path = Path::new(path);
            let actual = (|| {
                let root = SafeDir::open(
                    path.parent()
                        .ok_or_else(|| CoreError::new("UNSAFE_PATH", "无父目录"))?,
                )?;
                fsops::hash_at(&root, Path::new(path.file_name().unwrap()), &mut || Ok(()))
            })();
            match actual {
                Ok(hash) if hash == *expected => {
                    healthy += 1;
                    c.execute(
                        "UPDATE replicas SET verified=1,checked_at=?1 WHERE id=?2",
                        params![now(), key],
                    )?;
                }
                other => {
                    let reason = match other {
                        Ok(_) => "SHA-256 校验不匹配".into(),
                        Err(e) => e.to_string(),
                    };
                    c.execute(
                        "UPDATE replicas SET verified=0,checked_at=?1 WHERE id=?2",
                        params![now(), key],
                    )?;
                    issues.push(
                        json!({"replicaId":key,"path":path,"reason":reason,"recoverable":false}),
                    );
                }
            }
        }
        // Only another replica verified in this run can establish recoverability.
        for issue in &mut issues {
            let media = rows
                .iter()
                .find(|r| r.0 == issue["replicaId"].as_str().unwrap_or(""))
                .map(|r| &r.3);
            if let Some(media) = media {
                let n: i64 = c.query_row(
                    "SELECT COUNT(*) FROM replicas WHERE media_id=?1 AND verified=1",
                    [media],
                    |r| r.get(0),
                )?;
                issue["recoverable"] = json!(n > 0);
            }
        }
        let result = json!({"id":id(),"checked":rows.len(),"healthy":healthy,"issues":issues,"lastRun":now()});
        c.execute(
            "INSERT INTO audit_runs(id,data,created_at) VALUES(?1,?2,?3)",
            params![result["id"].as_str(), result.to_string(), now()],
        )?;
        Ok(result)
    }
    pub fn restore(
        &self,
        replica_id: &str,
        destination: &Path,
        confirmed: bool,
        key: &str,
    ) -> CoreResult<Job> {
        if !confirmed {
            return Err(CoreError::new(
                "CONFIRMATION_REQUIRED",
                "恢复需要确认输出目标",
            ));
        }
        let c = self.store.connect()?;
        if let Some(existing) = c
            .query_row("SELECT id FROM jobs WHERE idempotency_key=?1", [key], |r| {
                r.get::<_, String>(0)
            })
            .optional()?
        {
            let job = self.store.job(&existing)?;
            if job.kind == "restore"
                && job.options["replicaId"] == replica_id
                && job.options["destination"] == json!(destination)
            {
                return Ok(job);
            }
            return Err(CoreError::new(
                "IDEMPOTENCY_CONFLICT",
                "幂等键已用于不同请求",
            ));
        }
        let (path, expected): (String, String) = c.query_row(
            "SELECT path,sha256 FROM replicas WHERE id=?1",
            [replica_id],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )?;
        let path = PathBuf::from(path);
        let root = SafeDir::open(path.parent().unwrap())?;
        if fsops::hash_at(&root, Path::new(path.file_name().unwrap()), &mut || Ok(()))? != expected
        {
            return Err(CoreError::new("REPLICA_DAMAGED", "副本校验失败，未恢复"));
        }
        let mut p = self.preview(PlanRequest {
            source: root.path,
            destinations: vec![destination.into()],
            name: format!("恢复 {}", path.file_name().unwrap().to_string_lossy()),
            verify: true,
            transfer: false,
            selected: vec![PathBuf::from(path.file_name().unwrap())],
            subdirectory: None,
        })?;
        p.files[0].expected_sha256 = Some(expected);
        self.store.put("plans", &p.id, &p)?;
        self.start_context(
            &p.id,
            true,
            key,
            Some((
                "restore",
                json!({"replicaId":replica_id,"destination":destination}),
            )),
        )
    }
    pub fn replicas(&self) -> CoreResult<Vec<Value>> {
        let c = self.store.connect()?;
        let mut q = c.prepare(
            "SELECT id,job_id,media_id,path,sha256,verified,checked_at FROM replicas ORDER BY path",
        )?;
        q.query_map([],|r|Ok(json!({"id":r.get::<_,String>(0)?,"jobId":r.get::<_,String>(1)?,"mediaId":r.get::<_,String>(2)?,"path":r.get::<_,String>(3)?,"sha256":r.get::<_,String>(4)?,"verified":r.get::<_,bool>(5)?,"checkedAt":r.get::<_,String>(6)?})))?.map(|r|Ok(r?)).collect()
    }
}
pub fn safe_component(name: &str) -> CoreResult<()> {
    let p = Path::new(name);
    fsops::relative(p)?;
    if p.components().count() != 1
        || name.trim().is_empty()
        || name.len() > 180
        || name.ends_with(['.', ' '])
        || name
            .chars()
            .any(|c| c.is_control() || "<>:\"/\\|?*".contains(c))
        || [
            "CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7",
            "COM8", "COM9", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
        ]
        .contains(
            &name
                .split('.')
                .next()
                .unwrap_or("")
                .to_ascii_uppercase()
                .as_str(),
        )
    {
        return Err(CoreError::new("INVALID_FILENAME", name));
    }
    Ok(())
}
fn csv(s: &str) -> String {
    let s = if s.starts_with(['=', '+', '-', '@', '\t', '\r']) {
        format!("'{s}")
    } else {
        s.into()
    };
    format!("\"{}\"", s.replace('"', "\"\""))
}
