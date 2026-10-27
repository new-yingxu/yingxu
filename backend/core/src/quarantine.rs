//! Quarantines review candidates as verified copies. Originals are never moved.
use crate::{
    CoreError, CoreResult, Engine, fsops,
    model::{id, now},
};
use rusqlite::{OptionalExtension, params};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    fs::{self, OpenOptions},
    io::{Read, Write},
    path::{Path, PathBuf},
};

fn move_to_system_trash(path: &Path) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    {
        use objc2_foundation::{NSFileManager, NSString, NSURL};
        let path = path
            .to_str()
            .ok_or_else(|| "隔离副本路径编码无效".to_string())?;
        let path = NSString::from_str(path);
        let url = NSURL::fileURLWithPath(&path);
        return NSFileManager::defaultManager()
            .trashItemAtURL_resultingItemURL_error(&url, None)
            .map_err(|error| error.localizedDescription().to_string());
    }

    #[cfg(target_os = "windows")]
    {
        use std::{ffi::c_void, os::windows::ffi::OsStrExt};
        #[repr(C)]
        struct FileOperationW {
            window: *mut c_void,
            operation: u32,
            from: *const u16,
            to: *const u16,
            flags: u16,
            aborted: i32,
            name_mappings: *mut c_void,
            progress_title: *const u16,
        }
        #[link(name = "shell32")]
        unsafe extern "system" {
            fn SHFileOperationW(operation: *mut FileOperationW) -> i32;
        }
        const FO_DELETE: u32 = 3;
        const FOF_SILENT: u16 = 0x0004;
        const FOF_NOCONFIRMATION: u16 = 0x0010;
        const FOF_ALLOWUNDO: u16 = 0x0040;
        const FOF_NOERRORUI: u16 = 0x0400;
        let mut from = path.as_os_str().encode_wide().collect::<Vec<_>>();
        from.extend([0, 0]);
        let mut operation = FileOperationW {
            window: std::ptr::null_mut(),
            operation: FO_DELETE,
            from: from.as_ptr(),
            to: std::ptr::null(),
            flags: FOF_SILENT | FOF_NOCONFIRMATION | FOF_ALLOWUNDO | FOF_NOERRORUI,
            aborted: 0,
            name_mappings: std::ptr::null_mut(),
            progress_title: std::ptr::null(),
        };
        let result = unsafe { SHFileOperationW(&mut operation) };
        if result == 0 && operation.aborted == 0 {
            return Ok(());
        }
        return Err(format!("Windows 回收站操作失败，错误码 {result}"));
    }

    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    {
        let _ = path;
        Err("此平台没有配置系统废纸篓适配器".to_string())
    }
}

const PREVIEW_LIFETIME_SECONDS: i64 = 300;

fn hash_file(path: &Path) -> CoreResult<String> {
    let mut file = fs::File::open(path)?;
    let mut digest = Sha256::new();
    let mut buffer = [0u8; 1024 * 1024];
    loop {
        let count = file.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        digest.update(&buffer[..count]);
    }
    Ok(format!("{:x}", digest.finalize()))
}

fn is_regular_file(path: &Path) -> CoreResult<fs::Metadata> {
    let meta = fs::symlink_metadata(path)?;
    if meta.file_type().is_symlink() || !meta.is_file() {
        return Err(CoreError::new(
            "UNSAFE_MEDIA_PATH",
            "废片候选必须是普通文件，未执行操作",
        ));
    }
    if fs::canonicalize(path)? != path {
        return Err(CoreError::new(
            "UNSAFE_MEDIA_PATH",
            "废片候选路径身份已变化，未执行操作",
        ));
    }
    Ok(meta)
}

fn summary(
    id: &str,
    media_id: &str,
    name: &str,
    bytes: i64,
    sha256: &str,
    state: &str,
    created_at: &str,
    updated_at: &str,
    restore_via_system_trash: bool,
) -> Value {
    json!({"id":id,"mediaId":media_id,"name":name,"bytes":bytes,"sha256":sha256,"state":state,"createdAt":created_at,"updatedAt":updated_at,"restoreViaSystemTrash":restore_via_system_trash,"originalPreserved":true})
}

impl Engine {
    /// Copy a human/quality-review candidate into the app-managed quarantine.
    /// The indexed source is fingerprint checked both before and after copying.
    pub fn quarantine_candidate(&self, media_id: &str) -> CoreResult<Value> {
        let c = self.store.connect()?;
        let (source, name, state, metadata): (String, String, String, String) = c
            .query_row(
                "SELECT path,name,quality_state,metadata FROM media_items WHERE id=?1",
                [media_id],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)),
            )
            .map_err(|e| {
                if matches!(e, rusqlite::Error::QueryReturnedNoRows) {
                    CoreError::new("MEDIA_NOT_FOUND", "本机索引中没有该素材")
                } else {
                    e.into()
                }
            })?;
        if state != "review" {
            return Err(CoreError::new(
                "QUARANTINE_CANDIDATE_REQUIRED",
                "只有待复核候选可以放入待处理废片目录",
            ));
        }
        let source = PathBuf::from(source);
        let source_meta = is_regular_file(&source)?;
        let expected: crate::Fingerprint = serde_json::from_value(
            serde_json::from_str::<Value>(&metadata)?["fingerprint"].clone(),
        )
        .map_err(|_| CoreError::new("SOURCE_CHANGED", "索引指纹缺失，请重新扫描"))?;
        if fsops::fingerprint(&source_meta) != expected {
            return Err(CoreError::new(
                "SOURCE_CHANGED",
                "原片在索引后发生变化，请重新扫描",
            ));
        }
        let item_id = id();
        let name_path = Path::new(&name);
        if name_path.file_name().and_then(|n| n.to_str()) != Some(name.as_str()) || name.is_empty()
        {
            return Err(CoreError::new("UNSAFE_MEDIA_NAME", "候选文件名无效"));
        }
        let root = self.store.root.join("待处理废片");
        fs::create_dir_all(&root)?;
        if fs::symlink_metadata(&root)?.file_type().is_symlink() {
            return Err(CoreError::new(
                "UNSAFE_QUARANTINE_PATH",
                "隔离目录不能是符号链接",
            ));
        }
        let root = root.canonicalize()?;
        let item_dir = root.join(&item_id);
        fs::create_dir(&item_dir)?;
        let destination = item_dir.join(&name);
        let copy_result = (|| -> CoreResult<String> {
            let mut input = fs::File::open(&source)?;
            let mut output = OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&destination)?;
            std::io::copy(&mut input, &mut output)?;
            output.flush()?;
            output.sync_all()?;
            drop(output);
            if fsops::fingerprint(&is_regular_file(&source)?) != expected {
                return Err(CoreError::new(
                    "SOURCE_CHANGED",
                    "复制期间原片发生变化，隔离副本已清理",
                ));
            }
            let digest = hash_file(&destination)?;
            let source_digest = hash_file(&source)?;
            if digest != source_digest || fs::metadata(&destination)?.len() != expected.bytes {
                return Err(CoreError::new(
                    "CHECKSUM_MISMATCH",
                    "隔离副本校验不一致，原片保留",
                ));
            }
            Ok(digest)
        })();
        let digest = match copy_result {
            Ok(digest) => digest,
            Err(error) => {
                let _ = fs::remove_file(&destination);
                let _ = fs::remove_dir(&item_dir);
                return Err(error);
            }
        };
        let created = now();
        let bytes = i64::try_from(expected.bytes)
            .map_err(|_| CoreError::new("FILE_TOO_LARGE", "候选文件过大"))?;
        self.store.connect()?.execute(
            "INSERT INTO quarantine_items(id,media_id,name,bytes,sha256,quarantine_path,state,created_at,updated_at) VALUES(?1,?2,?3,?4,?5,?6,'isolated',?7,?7)",
            params![item_id, media_id, name, bytes, digest, destination.to_string_lossy(), created],
        )?;
        self.store.audit("quarantine_copy_created", &item_id, json!({"mediaId":media_id,"name":name,"bytes":bytes,"sha256":digest,"originalPreserved":true}))?;
        Ok(summary(
            &item_id, media_id, &name, bytes, &digest, "isolated", &created, &created, false,
        ))
    }

    /// Return metadata only; source and quarantine paths stay inside SQLite.
    pub fn quarantine_list(&self) -> CoreResult<Vec<Value>> {
        let mut c = self.store.connect()?;
        let tx = c.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        let rows = {
            let mut q = tx.prepare("SELECT id,media_id,name,bytes,sha256,quarantine_path,state,created_at,updated_at FROM quarantine_items ORDER BY created_at DESC,id")?;
            q.query_map([], |r| {
                Ok((
                    r.get::<_, String>(0)?,
                    r.get::<_, String>(1)?,
                    r.get::<_, String>(2)?,
                    r.get::<_, i64>(3)?,
                    r.get::<_, String>(4)?,
                    r.get::<_, String>(5)?,
                    r.get::<_, String>(6)?,
                    r.get::<_, String>(7)?,
                    r.get::<_, String>(8)?,
                ))
            })?
            .collect::<Result<Vec<_>, _>>()?
        };
        let mut result = Vec::with_capacity(rows.len());
        let mut restored = Vec::new();
        for (id, media, name, bytes, digest, path, mut state, created, mut updated) in rows {
            let mut restore_available = false;
            if state == "trashed" || state == "trash_uncertain" {
                // Finder/Explorer's native “Put Back” restores the item to its original
                // quarantine path. Reconcile that user action in SQLite on the next read.
                if is_regular_file(Path::new(&path)).is_ok()
                    && hash_file(Path::new(&path)).is_ok_and(|actual| actual == digest)
                {
                    state = "restored".into();
                    updated = now();
                    tx.execute("UPDATE quarantine_items SET state='restored',updated_at=?1 WHERE id=?2 AND state IN ('trashed','trash_uncertain')", params![updated, id])?;
                    restored.push((id.clone(), digest.clone()));
                } else if state == "trash_uncertain" && !Path::new(&path).exists() {
                    state = "trashed".into();
                    updated = now();
                    restore_available = true;
                    tx.execute("UPDATE quarantine_items SET state='trashed',updated_at=?1 WHERE id=?2 AND state='trash_uncertain'", params![updated, id])?;
                } else {
                    restore_available = true;
                }
            }
            result.push(summary(
                &id,
                &media,
                &name,
                bytes,
                &digest,
                &state,
                &created,
                &updated,
                restore_available,
            ));
        }
        tx.commit()?;
        for (id, digest) in restored {
            self.store.audit(
                "quarantine_copy_restored",
                &id,
                json!({"source":"system_trash","sha256":digest}),
            )?;
        }
        Ok(result)
    }

    pub fn quarantine_prepare_cleanup(&self, item_id: &str) -> CoreResult<Value> {
        let mut c = self.store.connect()?;
        let tx = c.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        let (media_id, name, bytes, digest, state): (String, String, i64, String, String) = tx
            .query_row(
                "SELECT media_id,name,bytes,sha256,state FROM quarantine_items WHERE id=?1",
                [item_id],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?)),
            )
            .optional()?
            .ok_or_else(|| CoreError::new("QUARANTINE_ITEM_NOT_FOUND", "隔离副本不存在"))?;
        if !["isolated", "cleanup_cancelled", "cleanup_failed"].contains(&state.as_str()) {
            return Err(CoreError::new(
                "QUARANTINE_STATE_INVALID",
                "该副本当前不能进入废纸篓",
            ));
        }
        let action_id = id();
        let expires_at =
            (chrono::Utc::now() + chrono::Duration::seconds(PREVIEW_LIFETIME_SECONDS)).to_rfc3339();
        let created_at = now();
        tx.execute("INSERT INTO quarantine_actions(id,quarantine_id,expires_at,state,created_at) VALUES(?1,?2,?3,'pending',?4)",params![action_id,item_id,expires_at,created_at])?;
        tx.execute(
            "UPDATE quarantine_items SET state='cleanup_pending',updated_at=?1 WHERE id=?2",
            params![created_at, item_id],
        )?;
        tx.commit()?;
        self.store.audit("quarantine_cleanup_prepared", item_id, json!({"actionId":action_id,"name":name,"bytes":bytes,"sha256":digest,"originalPreserved":true}))?;
        Ok(
            json!({"actionId":action_id,"itemId":item_id,"mediaId":media_id,"name":name,"bytes":bytes,"sha256":digest,"source":"待处理废片隔离副本","target":"系统废纸篓","effect":"只移动已校验的隔离副本；原片不变","requiresConfirmation":true,"expiresAt":expires_at}),
        )
    }

    pub fn quarantine_confirm_cleanup(&self, action_id: &str, accepted: bool) -> CoreResult<Value> {
        self.quarantine_confirm_cleanup_with(action_id, accepted, |path| move_to_system_trash(path))
    }

    fn quarantine_confirm_cleanup_with<F>(
        &self,
        action_id: &str,
        accepted: bool,
        move_to_trash: F,
    ) -> CoreResult<Value>
    where
        F: FnOnce(&Path) -> Result<(), String>,
    {
        let mut c = self.store.connect()?;
        let tx = c.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        let row: Option<(String,String,String,String)> = tx.query_row(
            "SELECT a.quarantine_id,a.expires_at,a.state,q.quarantine_path FROM quarantine_actions a JOIN quarantine_items q ON q.id=a.quarantine_id WHERE a.id=?1",[action_id],|r|Ok((r.get(0)?,r.get(1)?,r.get(2)?,r.get(3)?))
        ).optional()?;
        let Some((item_id, expires_at, action_state, path)) = row else {
            return Err(CoreError::new(
                "QUARANTINE_ACTION_NOT_FOUND",
                "废片操作预览不存在或已失效",
            ));
        };
        if action_state != "pending" {
            return Err(CoreError::new(
                "QUARANTINE_ACTION_USED",
                "废片操作预览已使用",
            ));
        }
        if chrono::DateTime::parse_from_rfc3339(&expires_at)
            .map_or(true, |expiry| expiry <= chrono::Utc::now())
        {
            tx.execute(
                "UPDATE quarantine_actions SET state='expired' WHERE id=?1",
                [action_id],
            )?;
            tx.execute("UPDATE quarantine_items SET state='isolated',updated_at=?1 WHERE id=?2 AND state='cleanup_pending'",params![now(),item_id])?;
            tx.commit()?;
            return Err(CoreError::new(
                "ACTION_EXPIRED",
                "预览已过期，请重新查看影响范围",
            ));
        }
        if !accepted {
            let timestamp = now();
            tx.execute(
                "UPDATE quarantine_actions SET state='cancelled' WHERE id=?1",
                [action_id],
            )?;
            tx.execute(
                "UPDATE quarantine_items SET state='cleanup_cancelled',updated_at=?1 WHERE id=?2",
                params![timestamp, item_id],
            )?;
            tx.commit()?;
            self.store.audit(
                "quarantine_cleanup_cancelled",
                &item_id,
                json!({"actionId":action_id,"copyPreserved":true}),
            )?;
            return Ok(
                json!({"actionId":action_id,"itemId":item_id,"status":"cancelled","copyPreserved":true,"originalPreserved":true,"executionAuthorized":false}),
            );
        }
        let path = PathBuf::from(path);
        let root = self.store.root.join("待处理废片").canonicalize()?;
        if !path.starts_with(&root) || is_regular_file(&path).is_err() {
            return Err(CoreError::new(
                "UNSAFE_QUARANTINE_PATH",
                "只能清理应用隔离目录内的普通文件",
            ));
        }
        let expected: String = tx.query_row(
            "SELECT sha256 FROM quarantine_items WHERE id=?1",
            [&item_id],
            |r| r.get(0),
        )?;
        if hash_file(&path)? != expected {
            return Err(CoreError::new(
                "QUARANTINE_COPY_CHANGED",
                "隔离副本已变化，请重新检查",
            ));
        }
        let timestamp = now();
        tx.execute(
            "UPDATE quarantine_actions SET state='executing' WHERE id=?1",
            [action_id],
        )?;
        tx.execute(
            "UPDATE quarantine_items SET state='trash_pending',updated_at=?1 WHERE id=?2",
            params![timestamp, item_id],
        )?;
        tx.commit()?;
        match move_to_trash(&path) {
            Ok(()) => {
                let timestamp = now();
                self.store.connect()?.execute(
                    "UPDATE quarantine_actions SET state='succeeded' WHERE id=?1",
                    [action_id],
                )?;
                self.store.connect()?.execute(
                    "UPDATE quarantine_items SET state='trashed',updated_at=?1 WHERE id=?2",
                    params![timestamp, item_id],
                )?;
                self.store.audit("quarantine_copy_trashed", &item_id, json!({"actionId":action_id,"sha256":expected,"originalPreserved":true,"restoreViaSystemTrash":true}))?;
                Ok(
                    json!({"actionId":action_id,"itemId":item_id,"status":"succeeded","effect":"quarantine_copy_moved_to_system_trash","sha256":expected,"originalPreserved":true,"restoreViaSystemTrash":true,"executionAuthorized":false}),
                )
            }
            Err(error) => {
                let timestamp = now();
                let state = if path.exists() {
                    "cleanup_failed"
                } else {
                    "trash_uncertain"
                };
                self.store.connect()?.execute(
                    "UPDATE quarantine_actions SET state='failed' WHERE id=?1",
                    [action_id],
                )?;
                self.store.connect()?.execute(
                    "UPDATE quarantine_items SET state=?1,updated_at=?2 WHERE id=?3",
                    params![state, timestamp, item_id],
                )?;
                self.store.audit(
                    "quarantine_copy_trash_failed",
                    &item_id,
                    json!({"actionId":action_id,"state":state,"error":error.to_string()}),
                )?;
                Err(CoreError::new(
                    "SYSTEM_TRASH_FAILED",
                    "系统废纸篓操作失败；请检查隔离副本状态",
                ))
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    #[test]
    fn stage_copy_cancel_cleanup_and_reconcile_system_restore() {
        let temp = TempDir::new().unwrap();
        let temp_root = temp.path().canonicalize().unwrap();
        let source = temp_root.join("card");
        fs::create_dir(&source).unwrap();
        let original = source.join("synthetic-review.jpg");
        fs::write(&original, b"synthetic, non-photo test bytes").unwrap();
        let engine = Engine::open(temp_root.join("app-data")).unwrap();
        engine.index_source(&source).unwrap();
        let media_id: String = engine
            .store
            .connect()
            .unwrap()
            .query_row("SELECT id FROM media_items LIMIT 1", [], |r| r.get(0))
            .unwrap();
        engine
            .store
            .connect()
            .unwrap()
            .execute(
                "UPDATE media_items SET quality_state='review' WHERE id=?1",
                [&media_id],
            )
            .unwrap();
        let original_hash = hash_file(&original).unwrap();

        let staged = engine.quarantine_candidate(&media_id).unwrap();
        let item_id = staged["id"].as_str().unwrap();
        let copy_path = engine
            .store
            .root
            .join("待处理废片")
            .join(item_id)
            .join("synthetic-review.jpg");
        assert_eq!(staged["state"], "isolated");
        assert_eq!(hash_file(&copy_path).unwrap(), original_hash);
        assert_eq!(hash_file(&original).unwrap(), original_hash);

        let cancel = engine.quarantine_prepare_cleanup(item_id).unwrap();
        let cancelled = engine
            .quarantine_confirm_cleanup(cancel["actionId"].as_str().unwrap(), false)
            .unwrap();
        assert_eq!(cancelled["status"], "cancelled");
        assert!(copy_path.exists());
        assert_eq!(
            engine.quarantine_list().unwrap()[0]["state"],
            "cleanup_cancelled"
        );

        let preview = engine.quarantine_prepare_cleanup(item_id).unwrap();
        let trash_folder = temp_root.join("fake-system-trash");
        fs::create_dir(&trash_folder).unwrap();
        let trashed_path = trash_folder.join("synthetic-review.jpg");
        let receipt = engine
            .quarantine_confirm_cleanup_with(preview["actionId"].as_str().unwrap(), true, |path| {
                fs::rename(path, &trashed_path).map_err(|error| error.to_string())
            })
            .unwrap();
        assert_eq!(receipt["status"], "succeeded");
        assert!(!copy_path.exists());
        assert_eq!(hash_file(&original).unwrap(), original_hash);
        assert_eq!(engine.quarantine_list().unwrap()[0]["state"], "trashed");

        fs::rename(&trashed_path, &copy_path).unwrap(); // Simulate Finder “Put Back”.
        let restored = engine.quarantine_list().unwrap();
        assert_eq!(restored[0]["state"], "restored");
        assert_eq!(restored[0]["originalPreserved"], true);
    }
}
