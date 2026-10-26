use crate::{
    Engine,
    fsops::{self, SafeDir},
    model::*,
    services::safe_component,
};
use rusqlite::{OptionalExtension, params};
use serde_json::{Value, json};
use std::path::{Path, PathBuf};

impl Engine {
    // Mirrors the explicit local RPC fields; callers must supply each safety flag.
    #[allow(clippy::too_many_arguments)]
    pub fn create_delivery(
        &self,
        name: &str,
        media_ids: &[String],
        destination: &Path,
        contact_sheet: bool,
        manifest: bool,
        confirmed: bool,
        key: &str,
    ) -> CoreResult<Job> {
        if !confirmed {
            return Err(CoreError::new(
                "CONFIRMATION_REQUIRED",
                "请确认交付素材与输出目录",
            ));
        }
        safe_component(name)?;
        if media_ids.is_empty() || media_ids.len() > 10000 || key.is_empty() {
            return Err(CoreError::new(
                "INVALID_DELIVERY",
                "请选择 1–10000 项素材并提供幂等键",
            ));
        }
        let mut c = self.store.connect()?;
        if let Some(j) = c
            .query_row("SELECT id FROM jobs WHERE idempotency_key=?1", [key], |r| {
                r.get::<_, String>(0)
            })
            .optional()?
        {
            let job = self.store.job(&j)?;
            if job.kind == "delivery"
                && job.name == name
                && job.options["mediaIds"] == json!(media_ids)
                && job.targets[0].path == destination
                && job.options["contactSheet"] == contact_sheet
                && job.options["manifest"] == manifest
            {
                return Ok(job);
            }
            return Err(CoreError::new(
                "IDEMPOTENCY_CONFLICT",
                "幂等键已用于不同请求",
            ));
        }
        let root = SafeDir::open(destination)?;
        if root.path.starts_with(&self.store.root) || self.store.root.starts_with(&root.path) {
            return Err(CoreError::new(
                "APP_DATA_OVERLAP",
                "交付目录不能包含应用数据",
            ));
        }
        let job_id = id();
        let package_dir = PathBuf::from(format!("{name}-{}", &job_id[..8]));
        let target = Target {
            id: id(),
            path: root.path.clone(),
            identity: fsops::fingerprint(&std::fs::metadata(&root.path)?),
            state: "queued".into(),
            copied: 0,
            skipped: 0,
            verified: 0,
            failed: 0,
        };
        let mut items = vec![];
        let mut total_bytes = 0u64;
        let mut seen = std::collections::HashSet::new();
        for media in media_ids {
            if !seen.insert(media) {
                return Err(CoreError::new("DUPLICATE_SELECTION", media));
            }
            let (path,expected):(String,String)=c.query_row("SELECT path,sha256 FROM replicas WHERE media_id=?1 AND verified=1 ORDER BY checked_at DESC LIMIT 1",[media],|r|Ok((r.get(0)?,r.get(1)?))).map_err(|_|CoreError::new("VERIFIED_REPLICA_REQUIRED",media))?;
            let path = PathBuf::from(path);
            let source = SafeDir::open(path.parent().unwrap())?;
            let relative = PathBuf::from(path.file_name().unwrap());
            let fingerprint = fsops::fingerprint(&source.read(&relative)?.metadata()?);
            total_bytes = total_bytes
                .checked_add(fingerprint.bytes)
                .ok_or_else(|| CoreError::new("SIZE_OVERFLOW", media))?;
            let filename = format!(
                "{}-{}",
                &id()[..8],
                path.file_name().unwrap().to_string_lossy()
            );
            safe_component(&filename)?;
            items.push(JobItem {
                id: id(),
                target_id: target.id.clone(),
                relative,
                output: root.path.join(&package_dir).join("files").join(filename),
                fingerprint,
                expected_sha256: Some(expected),
                media_id: media.clone(),
                state: "pending".into(),
                sha256: None,
                verified: false,
                error: None,
                source_root: source.path.clone(),
                source_root_identity: fsops::fingerprint(&std::fs::metadata(&source.path)?),
            });
        }
        if fs2::available_space(&root.path)? < total_bytes.saturating_add(1024 * 1024) {
            return Err(CoreError::new("INSUFFICIENT_SPACE", "交付目的地空间不足"));
        }
        let job = Job {
            id: job_id.clone(),
            name: name.into(),
            kind: "delivery".into(),
            state: "queued".into(),
            phase: "queued".into(),
            source: items[0].source_root.clone(),
            source_identity: items[0].source_root_identity.clone(),
            verify: true,
            created_at: now(),
            updated_at: now(),
            total_bytes,
            files: items.len(),
            progress: 0.0,
            targets: vec![target.clone()],
            items: vec![],
            error: None,
            options: json!({"packageDirectory":package_dir,"mediaIds":media_ids,"contactSheet":contact_sheet,"manifest":manifest}),
            runtime: json!({}),
        };
        let tx = c.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        if let Some(j) = tx
            .query_row("SELECT id FROM jobs WHERE idempotency_key=?1", [key], |r| {
                r.get::<_, String>(0)
            })
            .optional()?
        {
            drop(tx);
            let job = self.store.job(&j)?;
            if job.kind == "delivery"
                && job.name == name
                && job.options["mediaIds"] == json!(media_ids)
                && job.targets[0].path == destination
                && job.options["contactSheet"] == contact_sheet
                && job.options["manifest"] == manifest
            {
                return Ok(job);
            }
            return Err(CoreError::new(
                "IDEMPOTENCY_CONFLICT",
                "幂等键已用于不同请求",
            ));
        }
        tx.execute("INSERT INTO jobs(id,state,phase,created_at,data,idempotency_key) VALUES(?1,'queued','queued',?2,?3,?4)",params![job.id,job.created_at,serde_json::to_string(&job)?,key])?;
        tx.execute(
            "INSERT INTO job_targets(id,job_id,path,state) VALUES(?1,?2,?3,'queued')",
            params![target.id, job.id, target.path.to_string_lossy()],
        )?;
        for item in items {
            tx.execute("INSERT INTO job_items(id,job_id,target_id,state,data) VALUES(?1,?2,?3,'pending',?4)",params![item.id,job.id,item.target_id,serde_json::to_string(&item)?])?;
        }
        tx.commit()?;
        self.store.audit(
            "create_delivery",
            &job.id,
            json!({"mediaIds":media_ids,"directory":root.path.join(package_dir)}),
        )?;
        self.store.job(&job.id)
    }
    pub(crate) fn finish_delivery(&self, job_id: &str) -> CoreResult<()> {
        let job = self.store.job(job_id)?;
        if job
            .items
            .iter()
            .any(|i| !["copied", "skipped"].contains(&i.state.as_str()) || !i.verified)
        {
            return Ok(());
        }
        let target = &job.targets[0];
        let root = SafeDir::open(&target.path)?;
        let directory = Path::new(
            job.options["packageDirectory"]
                .as_str()
                .ok_or_else(|| CoreError::new("INVALID_DELIVERY", "缺交付目录"))?,
        );
        let base = target.path.join(directory);
        let mut entries = vec![];
        for i in &job.items {
            let relative = i
                .output
                .strip_prefix(&target.path)
                .map_err(|e| CoreError::new("UNSAFE_PATH", e))?;
            if fsops::hash_at(&root, relative, &mut || Ok(()))?
                != i.sha256.clone().unwrap_or_default()
            {
                return Err(CoreError::new("DELIVERY_CHANGED", i.output.display()));
            }
            entries.push(json!({"mediaId":i.media_id,"file":i.output.strip_prefix(&base).map_err(|e|CoreError::new("UNSAFE_PATH",e))?,"sha256":i.sha256,"bytes":i.fingerprint.bytes}));
        }
        if job.options["manifest"] == true {
            let data = json!({"schemaVersion":1,"packageId":job.id,"name":job.name,"createdAt":job.created_at,"entries":entries});
            write_metadata(
                &root,
                &directory.join("manifest.json"),
                &serde_json::to_vec_pretty(&data)?,
            )?;
            let checks = entries
                .iter()
                .map(|i| {
                    format!(
                        "{}  {}",
                        i["sha256"].as_str().unwrap(),
                        i["file"].as_str().unwrap()
                    )
                })
                .collect::<Vec<_>>()
                .join("\n")
                + "\n";
            write_metadata(&root, &directory.join("SHA256SUMS.txt"), checks.as_bytes())?;
        }
        if job.options["contactSheet"] == true {
            let mut html = format!(
                "<!doctype html><meta charset=\"utf-8\"><title>{}</title><style>body{{font:15px system-ui;background:#f4f2ed;color:#1e2420;padding:24px}}main{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px}}figure{{background:white;padding:16px;border-radius:12px;margin:0}}img{{width:100%;height:200px;object-fit:contain}}figcaption{{overflow-wrap:anywhere}}</style><h1>{}</h1><p>本地交付联系表；RAW/不支持的格式请下载原文件查看。</p><main>",
                escape_html(&job.name),
                escape_html(&job.name)
            );
            for i in &entries {
                let file = i["file"].as_str().unwrap();
                let url = encode_url(file);
                let ext = Path::new(file)
                    .extension()
                    .unwrap_or_default()
                    .to_string_lossy()
                    .to_lowercase();
                html.push_str("<figure>");
                if ["jpg", "jpeg", "png", "gif", "webp"].contains(&ext.as_str()) {
                    html.push_str(&format!(
                        "<img loading=\"lazy\" src=\"{url}\" alt=\"{}\">",
                        escape_html(file)
                    ));
                }
                html.push_str(&format!(
                    "<figcaption><a href=\"{url}\">{}</a></figcaption></figure>",
                    escape_html(file)
                ));
            }
            html.push_str("</main>");
            write_metadata(
                &root,
                &directory.join("contact-sheet.html"),
                html.as_bytes(),
            )?;
        }
        let data: Value = json!({"id":job.id,"directory":base,"items":entries.len(),"includeContactSheet":job.options["contactSheet"],"includeManifest":job.options["manifest"],"createdAt":job.created_at});
        self.store.connect()?.execute("INSERT INTO delivery_packages(id,data,created_at) VALUES(?1,?2,?3) ON CONFLICT(id) DO UPDATE SET data=excluded.data",params![job.id,data.to_string(),now()])?;
        Ok(())
    }
}
fn write_metadata(root: &SafeDir, path: &Path, bytes: &[u8]) -> CoreResult<()> {
    use std::io::Read;
    match root.read(path) {
        Ok(mut file) => {
            let mut existing = vec![];
            file.read_to_end(&mut existing)?;
            if existing != bytes {
                return Err(CoreError::new("METADATA_CONFLICT", path.display()));
            }
            Ok(())
        }
        Err(e) if e.code == "IO_NOT_FOUND" => fsops::write_new(root, path, bytes),
        Err(e) => Err(e),
    }
}
fn escape_html(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&#39;")
}
fn encode_url(s: &str) -> String {
    s.as_bytes()
        .iter()
        .map(|b| {
            if b.is_ascii_alphanumeric() || b"/-._~".contains(b) {
                (*b as char).to_string()
            } else {
                format!("%{b:02X}")
            }
        })
        .collect()
}
