use crate::{Engine, engine::default_settings, model::*};
use rusqlite::params;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::path::PathBuf;

pub const PROTOCOL_VERSION: u32 = 1;
#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Request {
    pub request_id: String,
    pub version: u32,
    pub method: String,
    #[serde(default)]
    pub params: Value,
}
#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Reply {
    pub request_id: String,
    pub version: u32,
    pub ok: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub data: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<CoreError>,
}
pub fn decode<T: serde::de::DeserializeOwned>(v: Value) -> CoreResult<T> {
    serde_json::from_value(v).map_err(|e| CoreError::new("INVALID_PARAMS", e))
}
fn value<T: Serialize>(v: T) -> CoreResult<Value> {
    Ok(serde_json::to_value(v)?)
}

macro_rules! arg {($name:ident {$($field:ident:$ty:ty),*$(,)?})=>{#[derive(Deserialize)]#[serde(rename_all="camelCase",deny_unknown_fields)]struct $name{$($field:$ty),*}};}
arg!(Start {
    plan_id: String,
    confirmed: bool,
    idempotency_key: String
});
arg!(JobId { job_id: String });
arg!(Control{job_id:String,action:String,target_id:Option<String>});
arg!(Search {
    query: String,
    kind: String,
    quality: String
});
#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct SearchPage {
    query: String,
    kind: String,
    quality: String,
    #[serde(default = "default_search_page_limit")]
    limit: u32,
    #[serde(default)]
    offset: u32,
}
fn default_search_page_limit() -> u32 {
    200
}
arg!(History {
    query: String,
    state: String
});
arg!(Rename {
    job_id: String,
    name: String,
    confirmed: bool
});
arg!(Clear {
    confirmation: String
});
arg!(Source { source: PathBuf });
arg!(Naming {
    preset_id: String,
    values: Value
});
arg!(Export {
    job_id: String,
    destination: PathBuf,
    format: String,
    confirmed: bool
});
arg!(Restore {
    replica_id: String,
    destination: PathBuf,
    confirmed: bool,
    idempotency_key: String
});
arg!(Removal {
    token: String,
    confirmation: String
});
arg!(Undo {
    job_id: String,
    confirmed: bool
});
arg!(Delivery{name:String,media_ids:Vec<String>,destination:PathBuf,include_contact_sheet:bool,include_manifest:bool,confirmed:bool,idempotency_key:String});
arg!(RemovePreset { id: String });
arg!(MediaId { media_id: String });
arg!(Review {
    media_id: String,
    state: String,
    note: String,
    confirmed: bool
});
arg!(Events {
    after_sequence: i64,
    limit: u32
});
arg!(AgentSearch {
    query: String,
    kind: String,
    quality: String,
    limit: Option<u32>
});
arg!(AgentJob { job_id: String });
arg!(AgentMedia { media_id: String });
arg!(AgentTool {
    tool: String,
    arguments: Value
});

impl Engine {
    pub fn dispatch(&self, request: Request) -> Reply {
        let result = if request.version != PROTOCOL_VERSION {
            Err(CoreError::new("UNSUPPORTED_PROTOCOL", "只支持本地协议 v1"))
        } else if request.request_id.is_empty() || request.request_id.len() > 128 {
            Err(CoreError::new(
                "INVALID_REQUEST_ID",
                "requestId 长度需为 1–128",
            ))
        } else {
            self.call(&request.method, request.params)
        };
        match result {
            Ok(data) => Reply {
                request_id: request.request_id,
                version: PROTOCOL_VERSION,
                ok: true,
                data: Some(data),
                error: None,
            },
            Err(error) => Reply {
                request_id: request.request_id,
                version: PROTOCOL_VERSION,
                ok: false,
                data: None,
                error: Some(error),
            },
        }
    }
    fn call(&self, method: &str, p: Value) -> CoreResult<Value> {
        match method {
            "health" => Ok(
                json!({"service":"yingxu-local-core","version":env!("CARGO_PKG_VERSION"),"protocolVersion":PROTOCOL_VERSION,"databaseVersion":1,"capabilities":{"localBackup":true,"persistentQueue":true,"multiTarget":true,"transferToRecovery":true,"history":true,"replicaAudit":true,"restore":true,"delivery":true,"agentProvider":false,"cloudConnection":false,"photoSignals":true,"photoThumbnails":true,"rawDecode":false,"rawPreview":true,"videoMetadata":true,"videoFrameExtraction":"optional_ffmpeg","videoQualityModel":false,"netdiskUpload":false}}),
            ),
            "backup.scan" => {
                let a: Source = decode(p)?;
                let source = crate::fsops::local_directory(&a.source)?;
                if self.store.root.starts_with(&source) || source.starts_with(&self.store.root) {
                    return Err(CoreError::new("APP_DATA_OVERLAP", "不能扫描应用数据目录"));
                }
                value(crate::fsops::scan(&source)?)
            }
            "backup.preview" => {
                let mut r: PlanRequest = decode(p)?;
                r.transfer = false;
                value(self.preview(r)?)
            }
            "transfer.preview" => {
                let mut r: PlanRequest = decode(p)?;
                r.transfer = true;
                value(self.preview(r)?)
            }
            "jobs.start" => {
                let a: Start = decode(p)?;
                value(self.start(&a.plan_id, a.confirmed, &a.idempotency_key)?)
            }
            "jobs.detail" => {
                let a: JobId = decode(p)?;
                value(self.store.job(&a.job_id)?)
            }
            "jobs.control" => {
                let a: Control = decode(p)?;
                value(self.control(&a.job_id, &a.action, a.target_id.as_deref())?)
            }
            "worker.runNext" => value(self.worker()?.run_next()?),
            "history.list" => {
                let a: History = decode(p)?;
                value(self.history(&a.query, &a.state)?)
            }
            "history.rename" => {
                let a: Rename = decode(p)?;
                self.rename_history(&a.job_id, &a.name, a.confirmed)?;
                Ok(Value::Null)
            }
            "history.clear" => {
                let a: Clear = decode(p)?;
                self.clear_history(&a.confirmation)?;
                Ok(Value::Null)
            }
            "library.search" => {
                let a: Search = decode(p)?;
                value(self.search(&a.query, &a.kind, &a.quality)?)
            }
            "library.searchPage" => {
                let a: SearchPage = decode(p)?;
                self.search_page(&a.query, &a.kind, &a.quality, a.limit, a.offset)
            }
            "agent.tools" => Ok(crate::agent::tool_directory()),
            "agent.searchMedia" => {
                let a: AgentSearch = decode(p)?;
                self.agent_search_media(&a.query, &a.kind, &a.quality, a.limit.unwrap_or(20))
            }
            "agent.getBackupJob" => {
                let a: AgentJob = decode(p)?;
                self.agent_get_backup_job(&a.job_id)
            }
            "agent.getMetadata" => {
                let a: AgentMedia = decode(p)?;
                self.agent_get_metadata(&a.media_id)
            }
            "agent.executeTool" => {
                let a: AgentTool = decode(p)?;
                crate::agent::execute_read_tool(self, &a.tool, &a.arguments)
            }
            "agent.openItem" => {
                let a: AgentMedia = decode(p)?;
                self.agent_open_item(&a.media_id)
            }
            "replicas.list" => value(self.replicas()?),
            "library.index" => {
                let a: Source = decode(p)?;
                self.index_source(&a.source)
            }
            "library.analyzePhoto" => {
                let a: MediaId = decode(p)?;
                self.analyze_photo(&a.media_id)
            }
            "library.thumbnail" => {
                let a: MediaId = decode(p)?;
                self.thumbnail(&a.media_id)
            }
            "library.review" => {
                let a: Review = decode(p)?;
                self.review_media(&a.media_id, &a.state, &a.note, a.confirmed)
            }
            "library.duplicates" => value(self.duplicate_candidates()?),
            "settings.get" => self.store.get("settings", "preferences"),
            "settings.update" => self.update_settings(p),
            "settings.reset" => {
                let defaults = default_settings();
                self.update_settings(defaults)
            }
            "presets.list" => value(self.presets()?),
            "presets.save" => value(self.save_preset(decode(p)?)?),
            "presets.remove" => {
                let a: RemovePreset = decode(p)?;
                let preset: Preset = self.store.get("presets", &a.id)?;
                if preset.is_default {
                    return Err(CoreError::new(
                        "DEFAULT_PRESET_REQUIRED",
                        "请先选择另一个默认预设",
                    ));
                }
                self.store
                    .connect()?
                    .execute("DELETE FROM presets WHERE id=?1", [a.id])?;
                Ok(Value::Null)
            }
            "presets.preview" => {
                let a: Naming = decode(p)?;
                value(self.naming_preview(&a.preset_id, &a.values)?)
            }
            "reports.get" => {
                let a: JobId = decode(p)?;
                self.report(&a.job_id)
            }
            "reports.export" => {
                let a: Export = decode(p)?;
                value(self.export_report(&a.job_id, &a.destination, &a.format, a.confirmed)?)
            }
            "audit.run" => self.audit_replicas(),
            "restore.start" => {
                let a: Restore = decode(p)?;
                value(self.restore(
                    &a.replica_id,
                    &a.destination,
                    a.confirmed,
                    &a.idempotency_key,
                )?)
            }
            "transfer.prepareRemoval" => {
                let a: JobId = decode(p)?;
                value(self.prepare_source_removal(&a.job_id)?)
            }
            "transfer.confirmRemoval" => {
                let a: Removal = decode(p)?;
                self.confirm_source_removal(&a.token, &a.confirmation)
            }
            "transfer.undoRemoval" => {
                let a: Undo = decode(p)?;
                self.undo_source_removal(&a.job_id, a.confirmed)
            }
            "delivery.create" => {
                let a: Delivery = decode(p)?;
                value(self.create_delivery(
                    &a.name,
                    &a.media_ids,
                    &a.destination,
                    a.include_contact_sheet,
                    a.include_manifest,
                    a.confirmed,
                    &a.idempotency_key,
                )?)
            }
            "delivery.list" => {
                let c = self.store.connect()?;
                let mut q =
                    c.prepare("SELECT data FROM delivery_packages ORDER BY created_at DESC")?;
                let rows = q.query_map([], |r| r.get::<_, String>(0))?;
                let data = rows
                    .map(|r| Ok(serde_json::from_str::<Value>(&r?)?))
                    .collect::<CoreResult<Vec<_>>>()?;
                value(data)
            }
            "sync.outbox" => {
                let settings: Value = self.store.get("settings", "preferences")?;
                if settings["syncHistory"] != true {
                    return Ok(json!([]));
                }
                let a: Events = decode(p)?;
                if a.after_sequence < 0 || a.limit == 0 || a.limit > 200 {
                    return Err(CoreError::new(
                        "INVALID_CURSOR",
                        "limit 需为 1–200，游标不能为负",
                    ));
                }
                let c = self.store.connect()?;
                let mut q=c.prepare("SELECT event_id,device_id,sequence,event_type,occurred_at,payload FROM outbox_events WHERE sequence>?1 AND sent=0 ORDER BY sequence LIMIT ?2")?;
                let rows = q.query_map(params![a.after_sequence, a.limit], |r| {
                    Ok((
                        r.get::<_, String>(0)?,
                        r.get::<_, String>(1)?,
                        r.get::<_, i64>(2)?,
                        r.get::<_, String>(3)?,
                        r.get::<_, String>(4)?,
                        r.get::<_, String>(5)?,
                    ))
                })?;
                let mut result = vec![];
                for r in rows {
                    let (id, device, seq, event, at, payload) = r?;
                    let mut payload: Value = serde_json::from_str(&payload)?;
                    if settings["syncPaths"] != true
                        && let Some(map) = payload.as_object_mut()
                    {
                        map.remove("source");
                        map.remove("paths");
                    }
                    result.push(json!({"schemaVersion":1,"eventId":id,"deviceId":device,"sequence":seq,"eventType":event,"occurredAt":at,"payload":payload}));
                }
                value(result)
            }
            "workbench.snapshot" => {
                let c = self.store.connect()?;
                let (count, bytes): (i64, i64) = c.query_row(
                    "SELECT COUNT(*),COALESCE(SUM(bytes),0) FROM media_items",
                    [],
                    |r| Ok((r.get(0)?, r.get(1)?)),
                )?;
                let review: i64 = c.query_row(
                    "SELECT COUNT(*) FROM media_items WHERE quality_state='review'",
                    [],
                    |r| r.get(0),
                )?;
                Ok(
                    json!({"tasks":self.store.jobs()?.into_iter().take(5).collect::<Vec<_>>(),"fileCount":count,"totalBytes":bytes,"reviewCount":review}),
                )
            }
            "auth.restore" => Ok(Value::Null),
            "auth.login" | "auth.logout" | "agent.ask" | "agent.confirm" | "providers.connect"
            | "account.devices" | "account.revoke" => Err(CoreError::new(
                "CLOUD_ADAPTER_NOT_CONNECTED",
                "Agent/账号/网盘授权由云端会话实现，当前本地服务未连接该适配器",
            )),
            _ => self.call_extension(method, p),
        }
    }
}
