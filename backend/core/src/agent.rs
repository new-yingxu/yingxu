//! Narrow, local-only Agent tools. Cloud orchestration may request these tools,
//! but only the desktop host can grant access or perform native open actions.
use crate::{CoreError, CoreResult, Engine, fsops};
use serde_json::{Value, json};
use std::path::PathBuf;

/// Ensure a resolved indexed item is inside one of the current host grants and
/// that every component still resolves to the same ordinary path.
pub fn validate_open_grant(path: &std::path::Path, roots: &[PathBuf]) -> CoreResult<()> {
    let root = roots
        .iter()
        .find(|root| path.starts_with(root))
        .ok_or_else(|| {
            CoreError::new("DIRECTORY_GRANT_REQUIRED", "素材不在当前授权的来源目录内")
        })?;
    if std::fs::canonicalize(root)? != *root {
        return Err(CoreError::new(
            "DIRECTORY_GRANT_REQUIRED",
            "来源目录身份已变化，请重新授权",
        ));
    }
    let relative = path
        .strip_prefix(root)
        .map_err(|_| CoreError::new("PERMISSION_DENIED", "素材不在授权目录内"))?;
    let mut cursor = root.clone();
    for component in relative.components() {
        cursor.push(component.as_os_str());
        if std::fs::symlink_metadata(&cursor)?.file_type().is_symlink() {
            return Err(CoreError::new(
                "UNSAFE_MEDIA_PATH",
                "素材路径中检测到符号链接，未打开",
            ));
        }
    }
    if std::fs::canonicalize(path)? != path {
        return Err(CoreError::new(
            "UNSAFE_MEDIA_PATH",
            "素材路径在打开前发生变化，未打开",
        ));
    }
    Ok(())
}

pub fn tool_directory() -> Value {
    json!({
        "scope": "current_device",
        "executionAuthorized": false,
        "tools": [
            { "name": "search_media", "permission": "read", "localOnly": true },
            { "name": "get_metadata", "permission": "read", "localOnly": true },
            { "name": "get_backup_job", "permission": "read", "localOnly": true }
        ],
        "blockedTools": [
            { "name": "open_item", "code": "NATIVE_OPENER_REQUIRED", "requiresNativeHost": true }
        ]
    })
}

/// Execute one allowlisted local read tool from a cloud continuation request.
/// Tool arguments contain IDs and filters only; a model supplied path or shell
/// command is rejected before reaching any filesystem API.
pub fn execute_read_tool(engine: &Engine, tool: &str, arguments: &Value) -> CoreResult<Value> {
    match tool {
        "search_media" => {
            validate_arguments(arguments, &["query", "kind", "quality", "limit"])?;
            let query = required_str(arguments, "query")?;
            let kind = optional_str(arguments, "kind")?.unwrap_or("all");
            let quality = optional_str(arguments, "quality")?.unwrap_or("all");
            let limit = match arguments.get("limit") {
                Some(value) => value
                    .as_u64()
                    .ok_or_else(|| CoreError::new("INVALID_AGENT_TOOL_ARGS", "结果数量参数无效"))?,
                None => 20,
            };
            if limit > u32::MAX as u64 {
                return Err(CoreError::new("INVALID_AGENT_SEARCH", "结果数量无效"));
            }
            engine.agent_search_media(query, kind, quality, limit as u32)
        }
        "get_metadata" => {
            validate_arguments(arguments, &["mediaId"])?;
            engine.agent_get_metadata(required_str(arguments, "mediaId")?)
        }
        "get_backup_job" => {
            validate_arguments(arguments, &["jobId"])?;
            engine.agent_get_backup_job(required_str(arguments, "jobId")?)
        }
        "open_item" => {
            validate_arguments(arguments, &["mediaId"])?;
            engine.agent_open_item(required_str(arguments, "mediaId")?)
        }
        _ => Err(CoreError::new(
            "AGENT_TOOL_NOT_ALLOWED",
            "该本地 Agent 工具未列入只读白名单",
        )),
    }
}

fn validate_arguments(arguments: &Value, allowed: &[&str]) -> CoreResult<()> {
    let fields = arguments
        .as_object()
        .ok_or_else(|| CoreError::new("INVALID_AGENT_TOOL_ARGS", "本地工具参数必须是对象"))?;
    if fields.keys().any(|key| !allowed.contains(&key.as_str())) {
        return Err(CoreError::new(
            "INVALID_AGENT_TOOL_ARGS",
            "本地工具包含未允许的参数；路径与命令参数会被拒绝",
        ));
    }
    Ok(())
}

fn required_str<'a>(arguments: &'a Value, key: &str) -> CoreResult<&'a str> {
    arguments
        .get(key)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty() && value.len() <= 500)
        .ok_or_else(|| CoreError::new("INVALID_AGENT_TOOL_ARGS", "本地工具参数无效"))
}

fn optional_str<'a>(arguments: &'a Value, key: &str) -> CoreResult<Option<&'a str>> {
    match arguments.get(key) {
        Some(value) => value
            .as_str()
            .map(Some)
            .ok_or_else(|| CoreError::new("INVALID_AGENT_TOOL_ARGS", "本地工具参数类型无效")),
        None => Ok(None),
    }
}

impl Engine {
    /// Return bounded, local-only media matches from the existing SQLite index.
    pub fn agent_search_media(
        &self,
        query: &str,
        kind: &str,
        quality: &str,
        limit: u32,
    ) -> CoreResult<Value> {
        if query.trim().is_empty() || query.len() > 500 || !(1..=50).contains(&limit) {
            return Err(CoreError::new(
                "INVALID_AGENT_SEARCH",
                "请输入 1–500 字检索词，结果数量需为 1–50",
            ));
        }
        let mut matches = self
            .search(query.trim(), kind, quality)?
            .into_iter()
            .map(|row| {
                json!({
                    "mediaId": row["id"],
                    "name": row["name"],
                    "kind": row["kind"],
                    "bytes": row["bytes"],
                    "qualityState": row["qualityState"]
                })
            })
            .collect::<Vec<_>>();
        let truncated = matches.len() > limit as usize;
        matches.truncate(limit as usize);
        Ok(json!({
            "tool": "search_media",
            "scope": "current_device",
            "query": query.trim(),
            "matches": matches,
            "truncated": truncated,
            "executionAuthorized": false
        }))
    }

    /// Do not place local paths, fingerprints or per-file paths in model-facing
    /// task results. The renderer can resolve details locally when needed.
    pub fn agent_get_backup_job(&self, job_id: &str) -> CoreResult<Value> {
        if job_id.is_empty() || job_id.len() > 160 {
            return Err(CoreError::new("INVALID_JOB_ID", "任务编号无效"));
        }
        let job = self.store.job(job_id)?;
        Ok(json!({
            "tool": "get_backup_job",
            "scope": "current_device",
            "job": {
                "jobId": job.id,
                "name": job.name,
                "kind": job.kind,
                "state": job.state,
                "phase": job.phase,
                "createdAt": job.created_at,
                "files": job.files,
                "totalBytes": job.total_bytes,
                "progress": job.progress,
                "verify": job.verify,
                "targets": job.targets.into_iter().map(|target| json!({
                    "state": target.state,
                    "copied": target.copied,
                    "skipped": target.skipped,
                    "verified": target.verified,
                    "failed": target.failed
                })).collect::<Vec<_>>()
            },
            "executionAuthorized": false
        }))
    }

    /// Return a conservative metadata allowlist. Raw EXIF, GPS, full paths,
    /// fingerprints and hashes stay in the local database.
    pub fn agent_get_metadata(&self, media_id: &str) -> CoreResult<Value> {
        if media_id.is_empty() || media_id.len() > 160 {
            return Err(CoreError::new("INVALID_MEDIA_ID", "素材编号无效"));
        }
        let c = self.store.connect()?;
        let (name, kind, bytes, quality_state, serialized): (String, String, i64, String, String) =
            c.query_row(
                "SELECT name,kind,bytes,quality_state,metadata FROM media_items WHERE id=?1",
                [media_id],
                |row| {
                    Ok((
                        row.get(0)?,
                        row.get(1)?,
                        row.get(2)?,
                        row.get(3)?,
                        row.get(4)?,
                    ))
                },
            )
            .map_err(|error| match error {
                rusqlite::Error::QueryReturnedNoRows => {
                    CoreError::new("MEDIA_NOT_FOUND", "本机索引中没有该素材")
                }
                other => other.into(),
            })?;
        let metadata: Value = serde_json::from_str(&serialized)?;
        let mut safe = serde_json::Map::new();
        for key in ["width", "height", "durationMs"] {
            if let Some(value) = metadata.get(key)
                && (value.is_string() || value.is_number())
            {
                safe.insert(key.into(), value.clone());
            }
        }
        Ok(json!({
            "tool": "get_metadata",
            "scope": "current_device",
            "media": {"mediaId": media_id, "name": name, "kind": kind, "bytes": bytes, "qualityState": quality_state, "metadata": safe},
            "executionAuthorized": false
        }))
    }

    /// `open_item` must be dispatched by the native host after it rechecks
    /// the selected directory grant and file identity. The core intentionally
    /// does not launch processes or treat a model supplied path as authority.
    pub fn agent_open_item(&self, media_id: &str) -> CoreResult<Value> {
        let _ = self.agent_resolve_open_item(media_id)?;
        Err(CoreError::new(
            "NATIVE_OPENER_REQUIRED",
            "宿主尚未提供按 mediaId 校验授权目录和文件指纹后打开素材的接口；未打开任何文件",
        ))
    }

    /// Resolve an indexed file only after the host has received a user action.
    /// The Tauri host must additionally enforce its selected-root grants before
    /// passing this canonical path to the OS opener.
    pub fn agent_resolve_open_item(&self, media_id: &str) -> CoreResult<PathBuf> {
        if media_id.is_empty() || media_id.len() > 160 {
            return Err(CoreError::new("INVALID_MEDIA_ID", "素材编号无效"));
        }
        let (stored, serialized): (String, String) = self
            .store
            .connect()?
            .query_row(
                "SELECT path,metadata FROM media_items WHERE id=?1",
                [media_id],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .map_err(|error| match error {
                rusqlite::Error::QueryReturnedNoRows => {
                    CoreError::new("MEDIA_NOT_FOUND", "本机索引中没有该素材")
                }
                other => other.into(),
            })?;
        let path = PathBuf::from(stored);
        let link_metadata = std::fs::symlink_metadata(&path)?;
        if link_metadata.file_type().is_symlink() || !link_metadata.is_file() {
            return Err(CoreError::new(
                "UNSAFE_MEDIA_FILE",
                "素材不是普通文件或检测到符号链接",
            ));
        }
        let canonical = path.canonicalize()?;
        if canonical != path {
            return Err(CoreError::new(
                "UNSAFE_MEDIA_PATH",
                "索引路径发生变化；请重新扫描",
            ));
        }
        let stored_metadata: Value = serde_json::from_str(&serialized)?;
        let current_fingerprint = fsops::fingerprint(&link_metadata);
        if stored_metadata.get("fingerprint") != Some(&json!(current_fingerprint)) {
            return Err(CoreError::new(
                "SOURCE_CHANGED",
                "索引后素材发生变化；请重新扫描",
            ));
        }
        Ok(canonical)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{Engine, PlanRequest};
    use image::{ImageFormat, Rgb, RgbImage};
    use rusqlite::params;
    use std::fs;
    use tempfile::TempDir;

    struct Fixture {
        _temp: TempDir,
        engine: Engine,
        source: PathBuf,
        target: PathBuf,
        image_path: PathBuf,
        media_id: String,
    }

    fn fixture() -> Fixture {
        let temp = tempfile::tempdir().unwrap();
        // The production scanner rejects symlinked roots, so make the fixture
        // root canonical on every host rather than assuming a macOS temp path.
        let root = temp.path().canonicalize().unwrap();
        let source = root.join("authorized-source");
        let target = root.join("target");
        let app = root.join("app-data");
        fs::create_dir_all(&source).unwrap();
        fs::create_dir_all(&target).unwrap();
        let image_path = source.join("fixture-photo.jpg");
        let mut image = RgbImage::new(3, 2);
        for (x, y, pixel) in image.enumerate_pixels_mut() {
            *pixel = Rgb([x as u8 * 40, y as u8 * 40, 90]);
        }
        image
            .save_with_format(&image_path, ImageFormat::Jpeg)
            .unwrap();
        let engine = Engine::open(app).unwrap();
        engine.index_source(&source).unwrap();
        let media_id = engine
            .store
            .connect()
            .unwrap()
            .query_row(
                "SELECT id FROM media_items WHERE path=?1",
                [image_path.to_str().unwrap()],
                |row| row.get(0),
            )
            .unwrap();
        Fixture {
            _temp: temp,
            engine,
            source,
            target,
            image_path,
            media_id,
        }
    }

    #[test]
    fn local_search_metadata_and_backup_job_are_redacted() {
        let fixture = fixture();
        let search = fixture
            .engine
            .agent_search_media("fixture-photo", "all", "all", 10)
            .unwrap();
        assert_eq!(search["matches"][0]["mediaId"], fixture.media_id);
        let search_text = search.to_string();
        assert!(!search_text.contains(fixture.source.to_str().unwrap()));
        assert!(!search_text.contains("fingerprint"));
        assert!(!search_text.contains("sha256"));

        fixture.engine.analyze_photo(&fixture.media_id).unwrap();
        let connection = fixture.engine.store.connect().unwrap();
        let current: String = connection
            .query_row(
                "SELECT metadata FROM media_items WHERE id=?1",
                [&fixture.media_id],
                |row| row.get(0),
            )
            .unwrap();
        let mut metadata: Value = serde_json::from_str(&current).unwrap();
        metadata["width"] = json!(3);
        metadata["height"] = json!(2);
        metadata["durationMs"] = json!(0);
        metadata["gps"] = json!({"latitude": 1.2, "longitude": 3.4});
        metadata["privateExif"] = json!("must not leave SQLite");
        connection
            .execute(
                "UPDATE media_items SET metadata=?1 WHERE id=?2",
                params![metadata.to_string(), fixture.media_id],
            )
            .unwrap();
        drop(connection);
        let safe = fixture
            .engine
            .agent_get_metadata(&fixture.media_id)
            .unwrap();
        assert_eq!(safe["media"]["metadata"]["width"], 3);
        assert_eq!(safe["media"]["metadata"]["height"], 2);
        assert_eq!(safe["media"]["metadata"]["durationMs"], 0);
        let safe_text = safe.to_string();
        assert!(!safe_text.contains("gps"));
        assert!(!safe_text.contains("privateExif"));
        assert!(!safe_text.contains(fixture.source.to_str().unwrap()));

        let plan = fixture
            .engine
            .preview(PlanRequest {
                source: fixture.source.clone(),
                destinations: vec![fixture.target.clone()],
                name: "temporary fixture job".into(),
                verify: true,
                transfer: false,
                selected: vec![],
                subdirectory: None,
            })
            .unwrap();
        fixture
            .engine
            .start(&plan.id, true, "agent-test-idempotency")
            .unwrap();
        let job = fixture
            .engine
            .agent_get_backup_job(&fixture.engine.store.jobs().unwrap()[0].id)
            .unwrap();
        let job_text = job.to_string();
        assert!(job_text.contains("temporary fixture job"));
        assert!(!job_text.contains(fixture.source.to_str().unwrap()));
        assert!(!job_text.contains(fixture.target.to_str().unwrap()));
        assert!(!job_text.contains("fingerprint"));
        assert!(!job_text.contains("output"));
    }

    #[test]
    fn executor_rejects_unknown_or_wrong_typed_arguments_before_effects() {
        let fixture = fixture();
        for arguments in [
            json!({"query":"fixture-photo", "path":"/tmp/not-used"}),
            json!({"query":"fixture-photo", "command":"open /tmp/not-used"}),
            json!({"query":"fixture-photo", "limit":"20"}),
            json!({"query":"fixture-photo", "kind":17}),
            json!({"query":"fixture-photo", "quality":false}),
        ] {
            let result = execute_read_tool(&fixture.engine, "search_media", &arguments);
            assert_eq!(result.unwrap_err().code, "INVALID_AGENT_TOOL_ARGS");
        }
        assert!(!fixture._temp.path().join("not-used").exists());
        assert_eq!(
            execute_read_tool(
                &fixture.engine,
                "arbitrary_shell",
                &json!({"command":"open"})
            )
            .unwrap_err()
            .code,
            "AGENT_TOOL_NOT_ALLOWED"
        );
        assert_eq!(
            fixture
                .engine
                .agent_get_metadata("missing-media-id")
                .unwrap_err()
                .code,
            "MEDIA_NOT_FOUND"
        );
        assert_eq!(
            fixture
                .engine
                .agent_get_backup_job("missing-job-id")
                .unwrap_err()
                .code,
            "NOT_FOUND"
        );
        assert_eq!(
            fixture
                .engine
                .agent_resolve_open_item("missing-media-id")
                .unwrap_err()
                .code,
            "MEDIA_NOT_FOUND"
        );
    }

    #[test]
    fn open_item_requires_current_root_and_rejects_symlinks_and_changed_files() {
        let fixture = fixture();
        let resolved = fixture
            .engine
            .agent_resolve_open_item(&fixture.media_id)
            .unwrap();
        assert_eq!(resolved, fixture.image_path.canonicalize().unwrap());
        assert!(validate_open_grant(&resolved, std::slice::from_ref(&fixture.source)).is_ok());
        let other_root = fixture._temp.path().join("other-authorized-root");
        fs::create_dir_all(&other_root).unwrap();
        assert_eq!(
            validate_open_grant(&resolved, std::slice::from_ref(&other_root))
                .unwrap_err()
                .code,
            "DIRECTORY_GRANT_REQUIRED"
        );

        #[cfg(unix)]
        {
            use std::os::unix::fs::symlink;
            let external = fixture._temp.path().join("outside.jpg");
            fs::copy(&fixture.image_path, &external).unwrap();
            let alias = fixture.source.join("alias.jpg");
            symlink(&external, &alias).unwrap();
            let bytes = fs::metadata(&external).unwrap().len() as i64;
            fixture.engine.store.connect().unwrap().execute(
                "INSERT INTO media_items(id,path,name,kind,bytes,quality_state,metadata) VALUES(?1,?2,?3,'photo',?4,'unanalyzed','{}')",
                params!["symlink-media", alias.to_str().unwrap(), "alias.jpg", bytes],
            ).unwrap();
            assert_eq!(
                fixture
                    .engine
                    .agent_resolve_open_item("symlink-media")
                    .unwrap_err()
                    .code,
                "UNSAFE_MEDIA_FILE"
            );
            assert_eq!(
                validate_open_grant(&alias, std::slice::from_ref(&fixture.source))
                    .unwrap_err()
                    .code,
                "UNSAFE_MEDIA_PATH"
            );
        }

        fs::write(&fixture.image_path, b"changed after indexing").unwrap();
        assert_eq!(
            fixture
                .engine
                .agent_resolve_open_item(&fixture.media_id)
                .unwrap_err()
                .code,
            "SOURCE_CHANGED"
        );
    }
}
