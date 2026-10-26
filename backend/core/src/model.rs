use serde::{Deserialize, Serialize};
use std::path::PathBuf;

pub type CoreResult<T> = Result<T, CoreError>;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CoreError {
    pub code: String,
    pub message: String,
}
impl CoreError {
    pub fn new(code: &str, message: impl ToString) -> Self {
        Self {
            code: code.into(),
            message: message.to_string(),
        }
    }
}
impl std::fmt::Display for CoreError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}: {}", self.code, self.message)
    }
}
impl std::error::Error for CoreError {}
impl From<std::io::Error> for CoreError {
    fn from(e: std::io::Error) -> Self {
        Self::new(
            match e.kind() {
                std::io::ErrorKind::NotFound => "IO_NOT_FOUND",
                std::io::ErrorKind::PermissionDenied => "IO_PERMISSION_DENIED",
                std::io::ErrorKind::AlreadyExists => "IO_ALREADY_EXISTS",
                _ => "IO_ERROR",
            },
            e,
        )
    }
}
impl From<rusqlite::Error> for CoreError {
    fn from(e: rusqlite::Error) -> Self {
        Self::new("DATABASE_ERROR", e)
    }
}
impl From<serde_json::Error> for CoreError {
    fn from(e: serde_json::Error) -> Self {
        Self::new("INVALID_JSON", e)
    }
}

pub fn now() -> String {
    chrono::Utc::now().to_rfc3339()
}
pub fn id() -> String {
    uuid::Uuid::new_v4().to_string()
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Fingerprint {
    pub bytes: u64,
    pub modified_ns: u128,
    pub identity: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ScannedFile {
    pub relative: PathBuf,
    pub fingerprint: Fingerprint,
    pub kind: String,
    pub expected_sha256: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct PlanRequest {
    pub source: PathBuf,
    pub destinations: Vec<PathBuf>,
    pub name: String,
    #[serde(default = "default_verify")]
    pub verify: bool,
    #[serde(default)]
    pub transfer: bool,
    #[serde(default)]
    pub selected: Vec<PathBuf>,
    #[serde(default)]
    pub subdirectory: Option<PathBuf>,
}
fn default_verify() -> bool {
    true
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Plan {
    pub id: String,
    pub created_at: String,
    pub request: PlanRequest,
    pub files: Vec<ScannedFile>,
    pub total_bytes: u64,
    pub source_identity: Fingerprint,
    pub target_identities: Vec<Fingerprint>,
    pub available_bytes: Vec<u64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Job {
    pub id: String,
    pub name: String,
    pub kind: String,
    pub state: String,
    pub phase: String,
    pub source: PathBuf,
    pub source_identity: Fingerprint,
    pub verify: bool,
    pub created_at: String,
    pub updated_at: String,
    pub total_bytes: u64,
    pub files: usize,
    pub progress: f64,
    pub targets: Vec<Target>,
    pub items: Vec<JobItem>,
    pub error: Option<String>,
    pub options: serde_json::Value,
    pub runtime: serde_json::Value,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Target {
    pub id: String,
    pub path: PathBuf,
    pub identity: Fingerprint,
    pub state: String,
    pub copied: usize,
    pub skipped: usize,
    pub verified: usize,
    pub failed: usize,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct JobItem {
    pub id: String,
    pub target_id: String,
    pub relative: PathBuf,
    pub output: PathBuf,
    pub fingerprint: Fingerprint,
    pub state: String,
    pub sha256: Option<String>,
    pub verified: bool,
    pub error: Option<String>,
    pub media_id: String,
    pub expected_sha256: Option<String>,
    pub source_root: PathBuf,
    pub source_root_identity: Fingerprint,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct CopyResult {
    pub output: PathBuf,
    pub sha256: String,
    pub skipped: bool,
    pub verified: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Preset {
    pub id: String,
    pub name: String,
    pub fields: Vec<String>,
    pub is_default: bool,
    pub version: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RemovalPlan {
    pub token: String,
    pub job_id: String,
    pub files: Vec<PathBuf>,
    pub expires_at: String,
    pub effect: String,
}
