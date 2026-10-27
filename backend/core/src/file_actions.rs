//! Local, preview-first rename and move operations for Agent proposals.
//! Directory grants are enforced by the Tauri host; this module accepts only
//! indexed media IDs and a destination already resolved by that host.
use crate::{
    CoreError, CoreResult, Engine, fsops,
    fsops::SafeDir,
    model::{Fingerprint, id, now},
};
use rusqlite::{OptionalExtension, params};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    fs,
    io::{Read, Write},
    path::{Path, PathBuf},
};

const ACTION_WINDOW_MS: i64 = 5 * 60 * 1000;

fn hash_file(path: &Path) -> CoreResult<String> {
    let mut file = fs::File::open(path)?;
    let mut hasher = Sha256::new();
    let mut buffer = [0u8; 1024 * 1024];
    loop {
        let count = file.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        hasher.update(&buffer[..count]);
    }
    Ok(format!("{:x}", hasher.finalize()))
}

fn checked_file(path: &Path) -> CoreResult<fs::Metadata> {
    let metadata = fs::symlink_metadata(path)?;
    if metadata.file_type().is_symlink() || !metadata.is_file() || fs::canonicalize(path)? != path {
        return Err(CoreError::new(
            "UNSAFE_MEDIA_PATH",
            "素材必须是当前索引内的普通文件",
        ));
    }
    Ok(metadata)
}

fn validate_name(name: &str) -> CoreResult<&str> {
    let path = Path::new(name);
    if name.is_empty()
        || name.len() > 255
        || path.file_name().and_then(|value| value.to_str()) != Some(name)
        || name == "."
        || name == ".."
        || name.contains(['/', '\\', ':', '\0'])
    {
        return Err(CoreError::new("INVALID_FILE_NAME", "目标名称无效"));
    }
    Ok(name)
}

impl Engine {
    /// Prepare a one-time local preview. No file is changed by this method.
    pub fn prepare_file_action(
        &self,
        tool: &str,
        media_id: &str,
        new_name: Option<&str>,
        destination_dir: Option<&Path>,
    ) -> CoreResult<Value> {
        if !["rename_item", "move_item"].contains(&tool) {
            return Err(CoreError::new(
                "AGENT_TOOL_NOT_ALLOWED",
                "不支持的本地文件操作",
            ));
        }
        if media_id.is_empty() || media_id.len() > 160 {
            return Err(CoreError::new("INVALID_MEDIA_ID", "素材编号无效"));
        }
        let c = self.store.connect()?;
        let (source_text, current_name, metadata_text): (String, String, String) = c
            .query_row(
                "SELECT path,name,metadata FROM media_items WHERE id=?1",
                [media_id],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
            )
            .optional()?
            .ok_or_else(|| CoreError::new("MEDIA_NOT_FOUND", "本机索引中没有该素材"))?;
        let source = PathBuf::from(source_text);
        let source_meta = checked_file(&source)?;
        let metadata: Value = serde_json::from_str(&metadata_text)?;
        let expected: Fingerprint = serde_json::from_value(metadata["fingerprint"].clone())
            .map_err(|_| CoreError::new("SOURCE_CHANGED", "素材索引指纹缺失，请重新扫描"))?;
        if fsops::fingerprint(&source_meta) != expected {
            return Err(CoreError::new("SOURCE_CHANGED", "素材已变化，请重新扫描"));
        }

        let target_name = if tool == "rename_item" {
            validate_name(
                new_name
                    .ok_or_else(|| CoreError::new("INVALID_FILE_NAME", "重命名需要提供目标名称"))?,
            )?
            .to_string()
        } else {
            validate_name(&current_name)?.to_string()
        };
        let destination_parent = if tool == "rename_item" {
            source
                .parent()
                .ok_or_else(|| CoreError::new("UNSAFE_MEDIA_PATH", "素材目录无效"))?
                .to_path_buf()
        } else {
            let directory = destination_dir.ok_or_else(|| {
                CoreError::new("DESTINATION_GRANT_REQUIRED", "请先选择已授权的目标目录")
            })?;
            fsops::local_directory(directory)?
        };
        let destination = destination_parent.join(&target_name);
        if destination == source {
            return Err(CoreError::new("SAME_DESTINATION", "目标与来源相同"));
        }
        if tool == "move_item" && destination_parent == source.parent().unwrap_or(Path::new("")) {
            return Err(CoreError::new("SAME_DESTINATION", "移动目标与来源目录相同"));
        }
        if destination.exists() {
            return Err(CoreError::new("TARGET_EXISTS", "目标已存在；不会覆盖"));
        }
        let digest = hash_file(&source)?;
        let action_id = id();
        let expires_at_ms = chrono::Utc::now().timestamp_millis() + ACTION_WINDOW_MS;
        let created_at = now();
        self.store.connect()?.execute(
            "INSERT INTO file_action_previews(id,tool,media_id,source_path,destination_path,source_fingerprint,source_sha256,expires_at_ms,state,created_at,receipt) VALUES(?1,?2,?3,?4,?5,?6,?7,?8,'pending',?9,NULL)",
            params![action_id,tool,media_id,source.to_string_lossy(),destination.to_string_lossy(),serde_json::to_string(&expected)?,digest,expires_at_ms,created_at],
        )?;
        self.store.audit(
            "agent_file_action_prepared",
            &action_id,
            json!({"tool":tool,"mediaId":media_id,"name":current_name,"targetName":target_name,"destination":destination.to_string_lossy(),"sha256":digest}),
        )?;
        Ok(json!({
            "actionId":action_id,
            "tool":tool,
            "mediaId":media_id,
            "objectName":current_name,
            "sourcePath":source,
            "targetName":target_name,
            "targetPath":destination,
            "sha256":digest,
            "effect":if tool == "rename_item" { "在原目录重命名索引素材" } else { "校验后移至已授权目录，再从来源移除" },
            "requiresConfirmation":true,
            "expiresAtMs":expires_at_ms
        }))
    }

    /// Return the pending local preview to the host so it can revalidate
    /// directory grants immediately before confirmation.
    pub fn file_action_preview(&self, action_id: &str) -> CoreResult<Value> {
        let c = self.store.connect()?;
        let (tool, media_id, source, destination, expires, state): (
            String,
            String,
            String,
            String,
            i64,
            String,
        ) = c
            .query_row(
                "SELECT tool,media_id,source_path,destination_path,expires_at_ms,state FROM file_action_previews WHERE id=?1",
                [action_id],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?, r.get(5)?)),
            )
            .optional()?
            .ok_or_else(|| CoreError::new("ACTION_NOT_FOUND", "本地操作预览不存在"))?;
        if state != "pending" || expires <= chrono::Utc::now().timestamp_millis() {
            return Err(CoreError::new("ACTION_EXPIRED", "本地操作预览已失效"));
        }
        Ok(
            json!({"actionId":action_id,"tool":tool,"mediaId":media_id,"sourcePath":source,"targetPath":destination}),
        )
    }

    /// Consume one preview exactly once. A decline records an exact no-effect receipt.
    pub fn confirm_file_action(&self, action_id: &str, accepted: bool) -> CoreResult<Value> {
        let mut c = self.store.connect()?;
        let tx = c.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        let row: Option<(String,String,String,String,String,String,i64,String,Option<String>)> = tx
            .query_row(
                "SELECT tool,media_id,source_path,destination_path,source_fingerprint,source_sha256,expires_at_ms,state,receipt FROM file_action_previews WHERE id=?1",
                [action_id],
                |r| Ok((r.get(0)?,r.get(1)?,r.get(2)?,r.get(3)?,r.get(4)?,r.get(5)?,r.get(6)?,r.get(7)?,r.get(8)?)),
            )
            .optional()?;
        let Some((
            tool,
            media_id,
            source,
            destination,
            fingerprint,
            digest,
            expires,
            state,
            receipt,
        )) = row
        else {
            return Err(CoreError::new("ACTION_NOT_FOUND", "本地操作预览不存在"));
        };
        if let Some(receipt) = receipt {
            return serde_json::from_str(&receipt).map_err(Into::into);
        }
        if state != "pending" {
            return Err(CoreError::new("ACTION_ALREADY_USED", "本地操作预览已使用"));
        }
        let source_path = PathBuf::from(source);
        let destination_path = PathBuf::from(destination);
        if expires <= chrono::Utc::now().timestamp_millis() {
            tx.execute(
                "UPDATE file_action_previews SET state='expired' WHERE id=?1",
                [action_id],
            )?;
            tx.commit()?;
            return Ok(
                json!({"actionId":action_id,"tool":tool,"status":"expired","mediaId":media_id,"sourcePath":source_path,"targetPath":destination_path,"sourcePreserved":true,"destinationCreated":false,"userConfirmed":true,"cloudExecutionAuthorized":false,"error":{"code":"ACTION_EXPIRED","message":"预览已过期，请重新查看目标和影响"}}),
            );
        }
        if !accepted {
            let receipt = json!({"actionId":action_id,"tool":tool,"status":"declined","mediaId":media_id,"sourcePath":source_path,"targetPath":destination_path,"sourcePreserved":true,"destinationCreated":false,"userConfirmed":false,"cloudExecutionAuthorized":false});
            tx.execute(
                "UPDATE file_action_previews SET state='declined',receipt=?1 WHERE id=?2",
                params![receipt.to_string(), action_id],
            )?;
            tx.commit()?;
            self.store.audit(
                "agent_file_action_declined",
                action_id,
                json!({"tool":tool,"mediaId":media_id}),
            )?;
            return Ok(receipt);
        }
        tx.execute(
            "UPDATE file_action_previews SET state='executing' WHERE id=?1",
            [action_id],
        )?;
        tx.commit()?;

        let expected: Fingerprint = serde_json::from_str(&fingerprint)?;
        let result = self.execute_file_action(
            &tool,
            &media_id,
            &source_path,
            &destination_path,
            &expected,
            &digest,
        );
        let (receipt, final_state) = match result {
            Ok((status, source_preserved, destination_created, error)) => (
                json!({"actionId":action_id,"tool":tool,"status":status,"mediaId":media_id,"sourcePath":source_path,"targetPath":destination_path,"sha256":digest,"sourcePreserved":source_preserved,"destinationCreated":destination_created,"userConfirmed":true,"cloudExecutionAuthorized":false,"error":error}),
                status.to_string(),
            ),
            Err(error) => {
                let source_preserved = source_path.exists();
                let destination_created = destination_path.exists();
                let receipt = json!({"actionId":action_id,"tool":tool,"status":"failed","mediaId":media_id,"sourcePath":source_path,"targetPath":destination_path,"sha256":digest,"sourcePreserved":source_preserved,"destinationCreated":destination_created,"userConfirmed":true,"cloudExecutionAuthorized":false,"error":{"code":error.code,"message":error.message}});
                (receipt, "failed".to_string())
            }
        };
        let timestamp = now();
        self.store.connect()?.execute(
            "UPDATE file_action_previews SET state=?1,receipt=?2 WHERE id=?3",
            params![final_state, receipt.to_string(), action_id],
        )?;
        self.store
            .audit("agent_file_action_receipt", action_id, receipt.clone())?;
        let _ = timestamp;
        Ok(receipt)
    }

    fn execute_file_action(
        &self,
        tool: &str,
        media_id: &str,
        source: &Path,
        destination: &Path,
        expected: &Fingerprint,
        expected_sha256: &str,
    ) -> CoreResult<(&'static str, bool, bool, Value)> {
        let source_meta = checked_file(source)?;
        if fsops::fingerprint(&source_meta) != *expected || hash_file(source)? != expected_sha256 {
            return Err(CoreError::new(
                "SOURCE_CHANGED",
                "素材在确认后发生变化，未执行操作",
            ));
        }
        let target_parent = destination
            .parent()
            .ok_or_else(|| CoreError::new("INVALID_DESTINATION", "目标目录无效"))?;
        if fsops::local_directory(target_parent)? != target_parent {
            return Err(CoreError::new(
                "DESTINATION_CHANGED",
                "目标目录身份变化，请重新预览",
            ));
        }
        if destination.exists() {
            return Err(CoreError::new("TARGET_EXISTS", "目标已存在；不会覆盖"));
        }
        let source_parent = source
            .parent()
            .ok_or_else(|| CoreError::new("UNSAFE_MEDIA_PATH", "来源目录无效"))?;
        let old_name = source.file_name().unwrap().to_string_lossy().to_string();
        let target_name = destination
            .file_name()
            .unwrap()
            .to_string_lossy()
            .to_string();
        let source_dir = SafeDir::open(source_parent)?;
        if tool == "rename_item" {
            if source_parent != target_parent {
                return Err(CoreError::new(
                    "INVALID_ACTION_PREVIEW",
                    "重命名目标目录与来源不一致",
                ));
            }
            source_dir.commit(Path::new(&old_name), Path::new(&target_name))?;
            let new_path = destination.to_string_lossy().to_string();
            if let Err(error) = self.update_media_index(media_id, source, destination) {
                self.store.audit(
                    "agent_file_action_index_update_failed",
                    media_id,
                    json!({"tool":tool,"targetPath":new_path,"error":error.to_string()}),
                )?;
                return Ok((
                    "completed_unindexed",
                    false,
                    true,
                    json!({"code":"INDEX_UPDATE_FAILED","message":"文件已重命名，但索引更新失败；请重新扫描资料库"}),
                ));
            }
            return Ok(("succeeded", false, true, Value::Null));
        }
        if tool != "move_item" {
            return Err(CoreError::new(
                "AGENT_TOOL_NOT_ALLOWED",
                "不支持的本地文件操作",
            ));
        }
        if source_parent == target_parent {
            return Err(CoreError::new("SAME_DESTINATION", "移动目标与来源目录相同"));
        }
        let target_dir = SafeDir::open(target_parent)?;
        let temp_name = format!(".yingxu-move-{media_id}.tmp");
        let mut input = source_dir.read(Path::new(&old_name))?;
        let mut output = target_dir.create(Path::new(&temp_name))?;
        std::io::copy(&mut input, &mut output)?;
        output.flush()?;
        output.sync_all()?;
        drop(output);
        drop(input);
        if hash_file(&target_parent.join(&temp_name))? != expected_sha256
            || fsops::fingerprint(&checked_file(source)?) != *expected
            || hash_file(source)? != expected_sha256
        {
            target_dir.cleanup_temp(
                Path::new(&temp_name),
                &fsops::fingerprint(&fs::metadata(target_parent.join(&temp_name))?).identity,
            );
            return Err(CoreError::new(
                "SOURCE_CHANGED",
                "复制校验期间素材发生变化；来源保留",
            ));
        }
        target_dir.commit(Path::new(&temp_name), Path::new(&target_name))?;
        if let Err(error) = self.update_media_index(media_id, source, destination) {
            return Ok((
                "partial",
                true,
                true,
                json!({"code":"INDEX_UPDATE_FAILED","message":format!("目标副本已校验，但索引更新失败：{error}")}),
            ));
        }
        source_dir.cleanup_temp(Path::new(&old_name), &expected.identity);
        if source.exists() {
            return Ok((
                "partial",
                true,
                true,
                json!({"code":"SOURCE_REMOVE_FAILED","message":"目标副本已校验；来源仍保留，请检查后重试"}),
            ));
        }
        Ok(("succeeded", false, true, Value::Null))
    }

    fn update_media_index(
        &self,
        media_id: &str,
        source: &Path,
        destination: &Path,
    ) -> CoreResult<()> {
        let metadata = fsops::fingerprint(&checked_file(destination)?);
        let mut c = self.store.connect()?;
        let tx = c.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        let serialized: String = tx
            .query_row(
                "SELECT metadata FROM media_items WHERE id=?1 AND path=?2",
                params![media_id, source.to_string_lossy()],
                |r| r.get(0),
            )
            .map_err(|error| {
                if matches!(error, rusqlite::Error::QueryReturnedNoRows) {
                    CoreError::new("MEDIA_INDEX_CHANGED", "索引在操作前发生变化，请重新扫描")
                } else {
                    error.into()
                }
            })?;
        let mut row: Value = serde_json::from_str(&serialized)?;
        row["fingerprint"] = serde_json::to_value(metadata)?;
        let name = destination
            .file_name()
            .and_then(|v| v.to_str())
            .ok_or_else(|| CoreError::new("INVALID_FILE_NAME", "目标名称编码无效"))?;
        tx.execute(
            "UPDATE media_items SET path=?1,name=?2,metadata=?3 WHERE id=?4 AND path=?5",
            params![
                destination.to_string_lossy(),
                name,
                row.to_string(),
                media_id,
                source.to_string_lossy()
            ],
        )?;
        tx.commit()?;
        self.store.audit(
            "agent_file_action_index_updated",
            media_id,
            json!({"targetName":name,"targetPath":destination.to_string_lossy()}),
        )?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    fn indexed_engine(temp: &TempDir) -> (Engine, PathBuf, PathBuf, String, String) {
        let root = temp.path().canonicalize().unwrap();
        let source = root.join("source");
        let destination = root.join("destination");
        fs::create_dir(&source).unwrap();
        fs::create_dir(&destination).unwrap();
        let original = source.join("synthetic.jpg");
        fs::write(
            &original,
            b"synthetic file-action fixture; not a photograph",
        )
        .unwrap();
        let engine = Engine::open(root.join("app-data")).unwrap();
        engine.index_source(&source).unwrap();
        let media_id: String = engine
            .store
            .connect()
            .unwrap()
            .query_row(
                "SELECT id FROM media_items WHERE path=?1",
                [original.to_string_lossy()],
                |r| r.get(0),
            )
            .unwrap();
        let digest = hash_file(&original).unwrap();
        (engine, original, destination, media_id, digest)
    }

    #[test]
    fn rename_requires_preview_and_confirmation_and_returns_exact_receipt() {
        let temp = TempDir::new().unwrap();
        let (engine, source, _, media_id, digest) = indexed_engine(&temp);
        let preview = engine
            .prepare_file_action("rename_item", &media_id, Some("renamed.jpg"), None)
            .unwrap();
        let target = PathBuf::from(preview["targetPath"].as_str().unwrap());
        assert!(
            source.exists(),
            "preparing a preview must not mutate source"
        );
        let receipt = engine
            .confirm_file_action(preview["actionId"].as_str().unwrap(), true)
            .unwrap();
        assert_eq!(receipt["status"], "succeeded");
        assert_eq!(receipt["sha256"], digest);
        assert!(!source.exists());
        assert_eq!(hash_file(&target).unwrap(), digest);
        assert_eq!(
            engine
                .confirm_file_action(preview["actionId"].as_str().unwrap(), true)
                .unwrap(),
            receipt
        );
    }

    #[test]
    fn move_decline_and_target_collision_never_overwrite_or_remove_source() {
        let temp = TempDir::new().unwrap();
        let (engine, source, destination, media_id, digest) = indexed_engine(&temp);
        let preview = engine
            .prepare_file_action("move_item", &media_id, None, Some(&destination))
            .unwrap();
        let declined = engine
            .confirm_file_action(preview["actionId"].as_str().unwrap(), false)
            .unwrap();
        assert_eq!(declined["status"], "declined");
        assert!(source.exists());
        assert_eq!(hash_file(&source).unwrap(), digest);

        let preview = engine
            .prepare_file_action("move_item", &media_id, None, Some(&destination))
            .unwrap();
        let target = PathBuf::from(preview["targetPath"].as_str().unwrap());
        fs::write(&target, b"user-owned collision").unwrap();
        let collision = engine
            .confirm_file_action(preview["actionId"].as_str().unwrap(), true)
            .unwrap();
        assert_eq!(collision["status"], "failed");
        assert_eq!(collision["error"]["code"], "TARGET_EXISTS");
        assert_eq!(fs::read(&target).unwrap(), b"user-owned collision");
        assert_eq!(hash_file(&source).unwrap(), digest);
    }

    #[test]
    fn changed_source_after_preview_is_not_modified() {
        let temp = TempDir::new().unwrap();
        let (engine, source, _, media_id, _) = indexed_engine(&temp);
        let preview = engine
            .prepare_file_action("rename_item", &media_id, Some("renamed.jpg"), None)
            .unwrap();
        fs::write(&source, b"changed after preview").unwrap();
        let receipt = engine
            .confirm_file_action(preview["actionId"].as_str().unwrap(), true)
            .unwrap();
        assert_eq!(receipt["status"], "failed");
        assert_eq!(receipt["error"]["code"], "SOURCE_CHANGED");
        assert!(source.exists());
        assert!(!PathBuf::from(preview["targetPath"].as_str().unwrap()).exists());
    }

    #[test]
    fn expired_preview_returns_receipt_without_touching_either_path() {
        let temp = TempDir::new().unwrap();
        let (engine, source, _, media_id, digest) = indexed_engine(&temp);
        let preview = engine
            .prepare_file_action("rename_item", &media_id, Some("renamed.jpg"), None)
            .unwrap();
        let action_id = preview["actionId"].as_str().unwrap();
        engine
            .store
            .connect()
            .unwrap()
            .execute(
                "UPDATE file_action_previews SET expires_at_ms=0 WHERE id=?1",
                [action_id],
            )
            .unwrap();
        let receipt = engine.confirm_file_action(action_id, true).unwrap();
        assert_eq!(receipt["status"], "expired");
        assert_eq!(receipt["userConfirmed"], true);
        assert_eq!(receipt["sourcePreserved"], true);
        assert_eq!(receipt["destinationCreated"], false);
        assert_eq!(hash_file(&source).unwrap(), digest);
        assert!(!PathBuf::from(preview["targetPath"].as_str().unwrap()).exists());
    }
}
