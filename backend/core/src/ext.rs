//! 新增能力的统一入口，独立成文件以减少与 `api.rs` 的冲突面。
//!
//! `api.rs` 只在 `_ =>` 兜底分支里加一行 `self.call_extension(method, params)`，
//! 其余方法名与参数解析都收在这里。
use crate::{Engine, api::decode, model::*, raw::is_raw_path, video::is_supported_container};
use serde::Deserialize;
use serde_json::{Value, json};

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct ScanArg {
    source: std::path::PathBuf,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct MediaArg {
    media_id: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct VideoAnalyzeArg {
    media_id: String,
    #[serde(default = "default_true")]
    extract_frames: bool,
}

fn default_true() -> bool {
    true
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct ProviderArg {
    provider: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct AuthArg {
    provider: String,
    code: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct UnbindArg {
    provider: String,
    confirmed: bool,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct EnqueueArg {
    provider: String,
    media_id: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct RunArg {
    task_id: String,
    /// 仅联调用：覆盖服务端地址（默认取 provider 配置）
    #[serde(default)]
    base_url: Option<String>,
    /// 并发分片数 1–8；不传则读设置
    #[serde(default)]
    max_concurrency: Option<u8>,
    /// 限速（兆比特/秒）；不传则读设置，0 表示不限速
    #[serde(default)]
    max_mbps: Option<f64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct TaskArg {
    task_id: String,
}

impl Engine {
    /// 按文件类型分派到照片 / RAW / 视频三条分析链路。
    pub fn analyze_media(&self, media_id: &str) -> CoreResult<Value> {
        let (path, kind): (String, String) = self.store.connect()?.query_row(
            "SELECT path,kind FROM media_items WHERE id=?1",
            [media_id],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )?;
        let path = std::path::PathBuf::from(path);
        match kind.as_str() {
            "photo" if is_raw_path(&path) => self.analyze_raw(media_id),
            "photo" => self.analyze_photo(media_id),
            "video" if is_supported_container(&path) => self.analyze_video(media_id, true),
            "video" => Err(CoreError::new(
                "VIDEO_CONTAINER_UNSUPPORTED",
                "当前只支持 MP4/MOV，其他容器暂不支持",
            )),
            other => Err(CoreError::new(
                "ANALYZE_UNSUPPORTED",
                format!("{other} 类型暂不支持分析"),
            )),
        }
    }

    pub fn capabilities(&self) -> Value {
        json!({
            "rawPreview":true,
            "rawFullDecode":false,
            "rawFormats":crate::raw::RAW_EXTENSIONS,
            "videoMetadata":true,
            "videoFrameExtraction":crate::video::frame_extractor_present(),
            "videoContainers":crate::video::VIDEO_EXTENSIONS,
            "netdiskProviders":crate::netdisk::providers().iter().map(|p| json!({
                "id":p.id,"approval":p.approval,"realUploadAvailable":p.approval=="approved"
            })).collect::<Vec<_>>(),
            "netdiskRealUpload":false
        })
    }

    pub(crate) fn call_extension(&self, method: &str, p: Value) -> CoreResult<Value> {
        match method {
            "library.analyze" => {
                let a: MediaArg = decode(p)?;
                self.analyze_media(&a.media_id)
            }
            "library.analyzeRaw" => {
                let a: MediaArg = decode(p)?;
                self.analyze_raw(&a.media_id)
            }
            "video.probe" => {
                let a: MediaArg = decode(p)?;
                self.probe_video(&a.media_id)
            }
            "video.analyze" => {
                let a: VideoAnalyzeArg = decode(p)?;
                self.analyze_video(&a.media_id, a.extract_frames)
            }
            "capabilities.get" => Ok(self.capabilities()),
            // 与 backup.scan 同样的入参，但会附带「被跳过的条目及原因」。
            // 前端应该用这个：否则用户只会看到「3 万张只认出 2.9 万张」而不知道为什么。
            "backup.scanReport" => {
                const MAX_SKIPPED: usize = 200;
                let a: ScanArg = decode(p)?;
                let source = crate::fsops::local_directory(&a.source)?;
                if self.store.root.starts_with(&source) || source.starts_with(&self.store.root) {
                    return Err(CoreError::new("APP_DATA_OVERLAP", "不能扫描应用数据目录"));
                }
                let report = crate::fsops::scan_report(&source)?;
                let scanned = report.files.len();
                let skipped_total = report.skipped.len();
                let skipped: Vec<_> = report.skipped.into_iter().take(MAX_SKIPPED).collect();
                Ok(json!({
                    "files": report.files,
                    "skipped": skipped,
                    "scanned": scanned,
                    "skippedTotal": skipped_total,
                    "skippedTruncated": skipped_total > MAX_SKIPPED,
                }))
            }
            "netdisk.providers" => Ok(self.netdisk_providers()),
            "netdisk.beginAuth" => {
                let a: ProviderArg = decode(p)?;
                self.netdisk_begin_auth(&a.provider)
            }
            "netdisk.completeAuth" => {
                let a: AuthArg = decode(p)?;
                self.netdisk_complete_auth(&a.provider, &a.code)
            }
            "netdisk.unbind" => {
                let a: UnbindArg = decode(p)?;
                self.netdisk_unbind(&a.provider, a.confirmed)
            }
            "netdisk.enqueue" => {
                let a: EnqueueArg = decode(p)?;
                self.netdisk_enqueue(&a.provider, &a.media_id)
            }
            "netdisk.tasks" => self.netdisk_tasks(),
            "netdisk.run" => {
                let a: RunArg = decode(p)?;
                let options = match (a.max_concurrency, a.max_mbps) {
                    (None, None) => None,
                    (c, m) => {
                        let mut o = crate::netdisk::UploadOptions::default();
                        if let Some(c) = c {
                            o.concurrency = c.clamp(1, 8);
                        }
                        if let Some(m) = m {
                            if !m.is_finite() || m < 0.0 {
                                return Err(CoreError::new(
                                    "INVALID_PARAMS",
                                    "maxMbps 必须是非负有限数",
                                ));
                            }
                            o.max_mbps = m;
                        }
                        Some(o)
                    }
                };
                self.netdisk_run(&a.task_id, a.base_url, options)
            }
            "netdisk.authPoll" => {
                let a: ProviderArg = decode(p)?;
                self.netdisk_auth_poll(&a.provider)
            }
            "netdisk.cancel" => {
                let a: TaskArg = decode(p)?;
                self.netdisk_cancel(&a.task_id)
            }
            _ => Err(CoreError::new("UNKNOWN_METHOD", method)),
        }
    }
}
