#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{
    collections::{HashMap, HashSet},
    io::{BufRead, BufReader, Read, Write},
    net::{Ipv4Addr, SocketAddrV4, TcpStream},
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, Ordering},
        mpsc,
    },
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use tauri::{
    Emitter, Manager,
    menu::{Menu, MenuItem},
    path::BaseDirectory,
    tray::TrayIconBuilder,
};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons};
use yingxu_core::{
    CoreError, Engine,
    api::{PROTOCOL_VERSION, Reply, Request},
};

struct HostState {
    engine: Engine,
    shutdown: Arc<AtomicBool>,
    worker_ready: Arc<AtomicBool>,
    worker: Mutex<Option<std::thread::JoinHandle<()>>>,
    sources: Mutex<HashSet<PathBuf>>,
    targets: Mutex<HashSet<PathBuf>>,
    directory_grants: Mutex<HashMap<String, (String, PathBuf)>>,
    pending_agent_actions: Mutex<HashMap<String, PendingOpenAction>>,
    app_data_dir: PathBuf,
    local_agent: Mutex<Option<LocalAgentProcess>>,
}

struct LocalAgentProcess {
    child: Child,
    port: u16,
    token: String,
}

impl Drop for LocalAgentProcess {
    fn drop(&mut self) {
        if self.child.try_wait().ok().flatten().is_none() {
            let _ = self.child.kill();
            let _ = self.child.wait();
        }
    }
}

struct PendingOpenAction {
    media_id: String,
    path: PathBuf,
    expires_at_ms: u64,
}

fn validate_action_expiry(now_ms: u64, expires_at_ms: u64) -> Result<(), CoreError> {
    if expires_at_ms <= now_ms || expires_at_ms > now_ms.saturating_add(5 * 60 * 1000) {
        return Err(CoreError::new(
            "ACTION_EXPIRED",
            "操作预览有效期无效，请重新查询",
        ));
    }
    Ok(())
}

fn local_agent_unavailable() -> serde_json::Value {
    serde_json::json!({
        "configured": false,
        "provider": "ollama",
        "model": "qwen2.5:7b",
        "region": "local",
        "availability": "sidecar_unavailable",
        "requiresExplicitConsent": true,
        "executionAuthorized": false,
        "cloudFallback": false
    })
}

fn local_agent_executable(app: &tauri::AppHandle) -> Result<PathBuf, CoreError> {
    let executable_name = if cfg!(windows) {
        "yingxu-local-agent.exe"
    } else {
        "yingxu-local-agent"
    };
    let relative = format!("agent-sidecar/yingxu-local-agent/{executable_name}");
    let resource = app.path().resolve(relative, BaseDirectory::Resource).ok();
    let development = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("agent-sidecar/yingxu-local-agent")
        .join(executable_name);
    resolve_local_agent_executable(resource, development, cfg!(debug_assertions))
}

fn resolve_local_agent_executable(
    resource: Option<PathBuf>,
    development: PathBuf,
    allow_development: bool,
) -> Result<PathBuf, CoreError> {
    if let Some(path) = resource.filter(|path| path.is_file()) {
        return Ok(path);
    }
    if allow_development && development.is_file() {
        return Ok(development);
    }
    Err(CoreError::new(
        "LOCAL_AGENT_SIDECAR_MISSING",
        "本机 Agent 运行组件尚未安装",
    ))
}

fn stable_agent_signing_key() -> Result<String, CoreError> {
    let entry = keyring::Entry::new("app.yingxu.desktop.next.local-agent", "signing-key")
        .map_err(|_| CoreError::new("LOCAL_AGENT_KEYSTORE_UNAVAILABLE", "系统凭据库不可用"))?;
    #[cfg(windows)]
    let stored = entry.get_secret().and_then(|bytes| {
        decode_agent_signing_key(&bytes).map_err(|_| keyring::Error::BadEncoding(bytes))
    });
    #[cfg(not(windows))]
    let stored = entry.get_password();
    match stored {
        Ok(key) => Ok(key),
        Err(keyring::Error::NoEntry) => {
            use rsa::pkcs8::EncodePrivateKey;
            let key = rsa::RsaPrivateKey::new(&mut rand::thread_rng(), 2048).map_err(|_| {
                CoreError::new("LOCAL_AGENT_KEY_CREATE_FAILED", "本机 Agent 密钥生成失败")
            })?;
            let pem = key
                .to_pkcs8_pem(rsa::pkcs8::LineEnding::LF)
                .map_err(|_| {
                    CoreError::new("LOCAL_AGENT_KEY_CREATE_FAILED", "本机 Agent 密钥编码失败")
                })?
                .to_string();
            // Windows credentials are limited in bytes: UTF-16 password storage
            // doubles this PEM beyond the limit. Store its ASCII/UTF-8 bytes.
            #[cfg(windows)]
            let saved = entry.set_secret(pem.as_bytes());
            #[cfg(not(windows))]
            let saved = entry.set_password(&pem);
            saved.map_err(|_| {
                CoreError::new(
                    "LOCAL_AGENT_KEYSTORE_UNAVAILABLE",
                    "无法保存本机 Agent 密钥",
                )
            })?;
            Ok(pem)
        }
        Err(_) => Err(CoreError::new(
            "LOCAL_AGENT_KEYSTORE_UNAVAILABLE",
            "无法读取系统凭据库中的本机 Agent 密钥",
        )),
    }
}

#[cfg(windows)]
fn decode_agent_signing_key(bytes: &[u8]) -> Result<String, ()> {
    use rsa::pkcs8::DecodePrivateKey;
    if let Ok(pem) = String::from_utf8(bytes.to_vec())
        && rsa::RsaPrivateKey::from_pkcs8_pem(&pem).is_ok()
    {
        return Ok(pem);
    }
    // Preserve an existing key saved by the old Windows password format.
    if !bytes.len().is_multiple_of(2) {
        return Err(());
    }
    let wide: Vec<u16> = bytes
        .chunks_exact(2)
        .map(|pair| u16::from_le_bytes([pair[0], pair[1]]))
        .collect();
    let pem = String::from_utf16(&wide).map_err(|_| ())?;
    rsa::RsaPrivateKey::from_pkcs8_pem(&pem).map_err(|_| ())?;
    Ok(pem)
}

fn start_local_agent(
    app: &tauri::AppHandle,
    app_data_dir: &std::path::Path,
) -> Result<LocalAgentProcess, CoreError> {
    let executable = local_agent_executable(app)?;
    spawn_local_agent(
        &executable,
        &app_data_dir.join("agent.sqlite"),
        &stable_agent_signing_key()?,
    )
}

fn spawn_local_agent(
    executable: &std::path::Path,
    database_path: &std::path::Path,
    signing_key: &str,
) -> Result<LocalAgentProcess, CoreError> {
    spawn_local_agent_with_timeout(
        executable,
        database_path,
        signing_key,
        Duration::from_secs(15),
    )
}

fn spawn_local_agent_with_timeout(
    executable: &std::path::Path,
    database_path: &std::path::Path,
    signing_key: &str,
    startup_timeout: Duration,
) -> Result<LocalAgentProcess, CoreError> {
    let database_path = database_path
        .to_str()
        .ok_or_else(|| CoreError::new("INVALID_PATH_ENCODING", "本机 Agent 数据目录无法读取"))?;
    let mut random = [0u8; 48];
    rand::RngCore::fill_bytes(&mut rand::rngs::OsRng, &mut random);
    let token = random
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>();
    let mut command = Command::new(executable);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000); // CREATE_NO_WINDOW: background Agent, no console flash.
    }
    command
        .env_clear()
        .env("YINGXU_LOCAL_AGENT_MODE", "1")
        .env("YINGXU_LOCAL_AGENT_TOKEN", &token)
        .env(
            "YINGXU_LOCAL_AGENT_DATABASE_URL",
            format!("sqlite:///{database_path}"),
        )
        .env("YINGXU_LOCAL_AGENT_SIGNING_KEY", &signing_key)
        .env("YINGXU_LOCAL_AGENT_KEY_ID", "local-agent-rsa-v1")
        .env("YINGXU_LOCAL_AGENT_MODEL", "qwen2.5:7b")
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    for name in [
        "HOME",
        "TMPDIR",
        "TEMP",
        "TMP",
        "SYSTEMROOT",
        "WINDIR",
        "USERPROFILE",
    ] {
        if let Some(value) = std::env::var_os(name) {
            command.env(name, value);
        }
    }
    if cfg!(windows)
        && let Some(path) = std::env::var_os("PATH")
    {
        command.env("PATH", path);
    }
    let mut child = command
        .spawn()
        .map_err(|_| CoreError::new("LOCAL_AGENT_START_FAILED", "本机 Agent 未能启动"))?;
    let stdout = child.stdout.take().ok_or_else(|| {
        let _ = child.kill();
        let _ = child.wait();
        CoreError::new("LOCAL_AGENT_START_FAILED", "本机 Agent 未返回启动状态")
    })?;
    let (sender, receiver) = mpsc::channel();
    std::thread::spawn(move || {
        let mut line = String::new();
        let _ = BufReader::new(stdout).read_line(&mut line);
        let _ = sender.send(line);
    });
    let ready_line = match receiver.recv_timeout(startup_timeout) {
        Ok(line) => line,
        Err(_) => {
            let _ = child.kill();
            let _ = child.wait();
            return Err(CoreError::new(
                "LOCAL_AGENT_START_TIMEOUT",
                "本机 Agent 启动超时",
            ));
        }
    };
    let ready = (|| {
        let ready: serde_json::Value = serde_json::from_str(&ready_line)
            .map_err(|_| CoreError::new("LOCAL_AGENT_START_FAILED", "本机 Agent 启动状态无效"))?;
        let port = ready["port"]
            .as_u64()
            .and_then(|value| u16::try_from(value).ok())
            .filter(|port| *port > 0)
            .ok_or_else(|| CoreError::new("LOCAL_AGENT_START_FAILED", "本机 Agent 端口无效"))?;
        if ready["event"] != "ready" || ready["host"] != "127.0.0.1" {
            return Err(CoreError::new(
                "LOCAL_AGENT_START_FAILED",
                "本机 Agent 地址不安全",
            ));
        }
        Ok(port)
    })();
    let port = match ready {
        Ok(port) => port,
        Err(error) => {
            let _ = child.kill();
            let _ = child.wait();
            return Err(error);
        }
    };
    Ok(LocalAgentProcess { child, port, token })
}

fn local_agent_endpoint(
    app: &tauri::AppHandle,
    state: &HostState,
) -> Result<(u16, String), CoreError> {
    let mut runtime = state
        .local_agent
        .lock()
        .map_err(|_| CoreError::new("STATE_UNAVAILABLE", "本机 Agent 状态不可用"))?;
    if runtime
        .as_mut()
        .is_some_and(|process| process.child.try_wait().ok().flatten().is_some())
    {
        runtime.take();
    }
    if runtime.is_none() {
        *runtime = Some(start_local_agent(app, &state.app_data_dir)?);
    }
    let process = runtime
        .as_ref()
        .ok_or_else(|| CoreError::new("LOCAL_AGENT_START_FAILED", "本机 Agent 未启动"))?;
    Ok((process.port, process.token.clone()))
}

fn local_agent_status_or_unavailable<F>(
    endpoint: Result<(u16, String), CoreError>,
    request_status: F,
) -> serde_json::Value
where
    F: FnOnce(u16, &str) -> Result<serde_json::Value, CoreError>,
{
    endpoint
        .and_then(|(port, token)| request_status(port, &token))
        .unwrap_or_else(|_| local_agent_unavailable())
}

fn local_agent_request(
    port: u16,
    token: &str,
    method: &str,
    path: &str,
    body: Option<&serde_json::Value>,
    timeout: Duration,
) -> Result<serde_json::Value, CoreError> {
    let address = std::net::SocketAddr::V4(SocketAddrV4::new(Ipv4Addr::LOCALHOST, port));
    let mut stream = TcpStream::connect_timeout(&address, timeout)
        .map_err(|_| CoreError::new("LOCAL_AGENT_UNAVAILABLE", "本机 Agent 连接失败"))?;
    stream
        .set_read_timeout(Some(timeout))
        .map_err(|_| CoreError::new("LOCAL_AGENT_UNAVAILABLE", "本机 Agent 连接失败"))?;
    stream
        .set_write_timeout(Some(timeout))
        .map_err(|_| CoreError::new("LOCAL_AGENT_UNAVAILABLE", "本机 Agent 连接失败"))?;
    let payload = body
        .map(serde_json::to_vec)
        .transpose()
        .map_err(|_| CoreError::new("INVALID_PARAMS", "本机 Agent 请求无法编码"))?
        .unwrap_or_default();
    write!(
        stream,
        "{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nAuthorization: Bearer {token}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        payload.len()
    )
    .and_then(|_| stream.write_all(&payload))
    .map_err(|_| CoreError::new("LOCAL_AGENT_UNAVAILABLE", "本机 Agent 请求发送失败"))?;
    let mut response = Vec::new();
    stream
        .take(2 * 1024 * 1024 + 1)
        .read_to_end(&mut response)
        .map_err(|_| CoreError::new("LOCAL_AGENT_UNAVAILABLE", "本机 Agent 请求读取失败"))?;
    if response.len() > 2 * 1024 * 1024 {
        return Err(CoreError::new(
            "LOCAL_AGENT_RESPONSE_TOO_LARGE",
            "本机 Agent 响应超过大小限制",
        ));
    }
    let header_end = response
        .windows(4)
        .position(|bytes| bytes == b"\r\n\r\n")
        .ok_or_else(|| CoreError::new("LOCAL_AGENT_INVALID_RESPONSE", "本机 Agent 响应格式无效"))?;
    let headers = std::str::from_utf8(&response[..header_end])
        .map_err(|_| CoreError::new("LOCAL_AGENT_INVALID_RESPONSE", "本机 Agent 响应格式无效"))?;
    let status = headers
        .lines()
        .next()
        .and_then(|line| line.split_whitespace().nth(1))
        .and_then(|code| code.parse::<u16>().ok())
        .ok_or_else(|| CoreError::new("LOCAL_AGENT_INVALID_RESPONSE", "本机 Agent 响应状态无效"))?;
    let payload = &response[header_end + 4..];
    if !(200..300).contains(&status) {
        return Err(CoreError::new(
            "LOCAL_AGENT_REQUEST_REJECTED",
            "本机 Agent 未接受请求",
        ));
    }
    serde_json::from_slice(payload)
        .map_err(|_| CoreError::new("LOCAL_AGENT_INVALID_RESPONSE", "本机 Agent 返回了无效数据"))
}

fn stop_local_agent(state: &HostState) {
    let Ok(mut runtime) = state.local_agent.lock() else {
        return;
    };
    let Some(mut process) = runtime.take() else {
        return;
    };
    stop_local_agent_process(&mut process);
}

fn stop_local_agent_process(process: &mut LocalAgentProcess) {
    let _ = local_agent_request(
        process.port,
        &process.token,
        "POST",
        "/_host/shutdown",
        Some(&serde_json::json!({})),
        Duration::from_secs(2),
    );
    let deadline = std::time::Instant::now() + Duration::from_secs(5);
    while std::time::Instant::now() < deadline {
        if process.child.try_wait().ok().flatten().is_some() {
            return;
        }
        std::thread::sleep(Duration::from_millis(25));
    }
    let _ = process.child.kill();
    let _ = process.child.wait();
}

#[tauri::command]
async fn select_directory(
    window: tauri::WebviewWindow,
    app: tauri::AppHandle,
    purpose: String,
) -> Result<serde_json::Value, CoreError> {
    if window.label() != "main" || !["source", "destination"].contains(&purpose.as_str()) {
        return Err(CoreError::new("PERMISSION_DENIED", "目录选择来源无效"));
    }
    tauri::async_runtime::spawn_blocking(move || {
        let selected = app
            .dialog()
            .file()
            .set_title(if purpose == "source" {
                "选择来源目录"
            } else {
                "选择目的地目录"
            })
            .blocking_pick_folder();
        let Some(selected) = selected else {
            return Ok(serde_json::Value::Null);
        };
        let path = selected
            .into_path()
            .map_err(|e| CoreError::new("INVALID_LOCAL_DIRECTORY", e))?;
        let path = yingxu_core::fsops::local_directory(&path)?;
        let state = app.state::<HostState>();
        let grants = if purpose == "source" {
            &state.sources
        } else {
            &state.targets
        };
        grants
            .lock()
            .map_err(|_| CoreError::new("STATE_UNAVAILABLE", "授权目录状态不可用"))?
            .insert(path.clone());
        let grant_id = format!(
            "directory-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap_or_default()
                .as_nanos()
        );
        state
            .directory_grants
            .lock()
            .map_err(|_| CoreError::new("STATE_UNAVAILABLE", "目录授权状态不可用"))?
            .insert(grant_id.clone(), (purpose.clone(), path.clone()));
        Ok(serde_json::json!({"path":path,"purpose":purpose,"grantId":grant_id}))
    })
    .await
    .map_err(|e| CoreError::new("BACKGROUND_TASK_FAILED", e))?
}

/// Prepare a local Agent filesystem operation. Model supplied values are
/// limited to a tool name, indexed media ID, name, or an opaque directory grant.
#[tauri::command]
async fn agent_prepare_file_action(
    window: tauri::WebviewWindow,
    app: tauri::AppHandle,
    tool: String,
    media_id: Option<String>,
    new_name: Option<String>,
    destination_grant_id: Option<String>,
    quarantine_id: Option<String>,
) -> Result<serde_json::Value, CoreError> {
    if window.label() != "main" {
        return Err(CoreError::new(
            "PERMISSION_DENIED",
            "仅主窗口可准备本地文件操作",
        ));
    }
    let state = app.state::<HostState>();
    if tool == "cleanup_quarantine" {
        let quarantine_id =
            quarantine_id.ok_or_else(|| CoreError::new("INVALID_PARAMS", "缺少隔离项编号"))?;
        let preview = state.engine.quarantine_prepare_cleanup(&quarantine_id)?;
        return Ok(serde_json::json!({
            "actionId":format!("quarantine:{}", preview["actionId"].as_str().unwrap_or_default()),
            "tool":tool,
            "objectName":preview["name"],
            "targetPath":"系统废纸篓中的待处理废片副本",
            "effect":"将待处理废片副本移入系统废纸篓；原始素材不变",
            "requiresConfirmation":true,
            "expiresAt":preview["expiresAt"]
        }));
    }
    if !["rename_item", "move_item"].contains(&tool.as_str()) {
        return Err(CoreError::new(
            "AGENT_TOOL_NOT_ALLOWED",
            "不支持的本地文件操作",
        ));
    }
    let media_id = media_id.ok_or_else(|| CoreError::new("INVALID_PARAMS", "缺少素材编号"))?;
    let engine = state.engine.clone();
    let lookup_id = media_id.clone();
    let source_path =
        tauri::async_runtime::spawn_blocking(move || engine.agent_resolve_open_item(&lookup_id))
            .await
            .map_err(|error| CoreError::new("BACKGROUND_TASK_FAILED", error))??;
    let source_roots = state
        .sources
        .lock()
        .map_err(|_| CoreError::new("STATE_UNAVAILABLE", "来源目录授权状态不可用"))?
        .iter()
        .cloned()
        .collect::<Vec<_>>();
    yingxu_core::agent::validate_open_grant(&source_path, &source_roots)?;
    let destination = if tool == "move_item" {
        let grant_id = destination_grant_id.ok_or_else(|| {
            CoreError::new("DESTINATION_GRANT_REQUIRED", "请先选择并授权目标目录")
        })?;
        let grants = state
            .directory_grants
            .lock()
            .map_err(|_| CoreError::new("STATE_UNAVAILABLE", "目录授权状态不可用"))?;
        let (purpose, path) = grants.get(&grant_id).ok_or_else(|| {
            CoreError::new(
                "DESTINATION_GRANT_REQUIRED",
                "目标目录授权已失效，请重新选择",
            )
        })?;
        if purpose != "destination" {
            return Err(CoreError::new(
                "PERMISSION_DENIED",
                "目标目录授权用途不匹配",
            ));
        }
        Some(path.clone())
    } else {
        None
    };
    let engine = state.engine.clone();
    tauri::async_runtime::spawn_blocking(move || {
        engine.prepare_file_action(
            &tool,
            &media_id,
            new_name.as_deref(),
            destination.as_deref(),
        )
    })
    .await
    .map_err(|error| CoreError::new("BACKGROUND_TASK_FAILED", error))?
}

/// Consume one preview. Every accepted rename/move rechecks both source and
/// destination grants immediately before the core touches any file.
#[tauri::command]
async fn agent_confirm_file_action(
    window: tauri::WebviewWindow,
    app: tauri::AppHandle,
    action_id: String,
    accepted: bool,
) -> Result<serde_json::Value, CoreError> {
    if window.label() != "main" {
        return Err(CoreError::new(
            "PERMISSION_DENIED",
            "仅主窗口可确认本地文件操作",
        ));
    }
    let state = app.state::<HostState>();
    if let Some(id) = action_id.strip_prefix("quarantine:") {
        let mut receipt = state.engine.quarantine_confirm_cleanup(id, accepted)?;
        receipt["actionId"] = serde_json::Value::String(action_id);
        receipt["tool"] = serde_json::Value::String("cleanup_quarantine".into());
        receipt["userConfirmed"] = serde_json::Value::Bool(accepted);
        receipt["cloudExecutionAuthorized"] = serde_json::Value::Bool(false);
        return Ok(receipt);
    }
    if accepted {
        let preview = state.engine.file_action_preview(&action_id)?;
        let source = PathBuf::from(preview["sourcePath"].as_str().unwrap_or_default());
        let target = PathBuf::from(preview["targetPath"].as_str().unwrap_or_default());
        let source_roots = state
            .sources
            .lock()
            .map_err(|_| CoreError::new("STATE_UNAVAILABLE", "来源目录授权状态不可用"))?
            .iter()
            .cloned()
            .collect::<Vec<_>>();
        yingxu_core::agent::validate_open_grant(&source, &source_roots)?;
        if preview["tool"] == "move_item" {
            let target_parent = target
                .parent()
                .ok_or_else(|| CoreError::new("INVALID_DESTINATION", "目标目录无效"))?;
            let grants = state
                .directory_grants
                .lock()
                .map_err(|_| CoreError::new("STATE_UNAVAILABLE", "目录授权状态不可用"))?;
            if !grants.values().any(|(purpose, path)| {
                purpose == "destination"
                    && target_parent.starts_with(path)
                    && std::fs::canonicalize(path).ok().as_ref() == Some(path)
            }) {
                return Err(CoreError::new(
                    "DESTINATION_GRANT_REQUIRED",
                    "目标目录授权已失效，请重新预览",
                ));
            }
        }
    }
    let engine = state.engine.clone();
    tauri::async_runtime::spawn_blocking(move || engine.confirm_file_action(&action_id, accepted))
        .await
        .map_err(|error| CoreError::new("BACKGROUND_TASK_FAILED", error))?
}

#[tauri::command]
async fn open_system_trash(app: tauri::AppHandle) -> Result<(), CoreError> {
    tauri::async_runtime::spawn_blocking(move || {
        #[cfg(target_os = "macos")]
        let result = {
            let home = app
                .path()
                .home_dir()
                .map_err(|error| CoreError::new("HOME_DIR_FAILED", error))?;
            let path = home.join(".Trash");
            std::process::Command::new("open").arg(path).spawn()
        };
        #[cfg(target_os = "windows")]
        let result = std::process::Command::new("explorer.exe")
            .arg("shell:RecycleBinFolder")
            .spawn();
        #[cfg(target_os = "linux")]
        let result = std::process::Command::new("xdg-open")
            .arg("trash:///")
            .spawn();
        #[cfg(not(any(target_os = "macos", target_os = "windows", target_os = "linux")))]
        let result: Result<std::process::Child, std::io::Error> = Err(std::io::Error::new(
            std::io::ErrorKind::Unsupported,
            "系统废纸篓不受支持",
        ));
        result
            .map(|_| ())
            .map_err(|error| CoreError::new("SYSTEM_TRASH_OPEN_FAILED", error))
    })
    .await
    .map_err(|error| CoreError::new("BACKGROUND_TASK_FAILED", error))?
}

fn check_paths(state: &HostState, request: &Request) -> Result<(), CoreError> {
    if request.method == "worker.runNext" {
        return Err(CoreError::new("PERMISSION_DENIED", "worker 由桌面宿主管理"));
    }
    let check = |path: &str, source: bool| -> Result<(), CoreError> {
        let path = yingxu_core::fsops::local_directory(&PathBuf::from(path))?;
        let grants = if source {
            &state.sources
        } else {
            &state.targets
        };
        if !grants
            .lock()
            .map_err(|_| CoreError::new("STATE_UNAVAILABLE", "目录状态不可用"))?
            .contains(&path)
        {
            return Err(CoreError::new(
                "DIRECTORY_GRANT_REQUIRED",
                "先通过原生选择器选择目录",
            ));
        }
        Ok(())
    };
    let p = &request.params;
    if [
        "backup.scan",
        "backup.scanReport",
        "backup.preview",
        "transfer.preview",
        "library.index",
    ]
    .contains(&request.method.as_str())
    {
        if let Some(path) = p["source"].as_str() {
            check(path, true)?;
        }
        if let Some(targets) = p["destinations"].as_array() {
            for target in targets {
                check(
                    target
                        .as_str()
                        .ok_or_else(|| CoreError::new("INVALID_PARAMS", "目的地必须是目录"))?,
                    false,
                )?;
            }
        }
    }
    if ["reports.export", "restore.start", "delivery.create"].contains(&request.method.as_str())
        && let Some(path) = p["destination"].as_str()
    {
        check(path, false)?;
    }
    if request.method == "jobs.start" {
        let plan: yingxu_core::Plan = state.engine.store.get(
            "plans",
            p["planId"]
                .as_str()
                .ok_or_else(|| CoreError::new("INVALID_PARAMS", "缺少 planId"))?,
        )?;
        check(
            plan.request
                .source
                .to_str()
                .ok_or_else(|| CoreError::new("INVALID_PATH_ENCODING", "目录不是 UTF-8"))?,
            true,
        )?;
        for target in plan.request.destinations {
            check(
                target
                    .to_str()
                    .ok_or_else(|| CoreError::new("INVALID_PATH_ENCODING", "目录不是 UTF-8"))?,
                false,
            )?;
        }
    }
    Ok(())
}

#[tauri::command]
async fn backend_request(
    window: tauri::WebviewWindow,
    app: tauri::AppHandle,
    request: Request,
) -> Reply {
    let state = app.state::<HostState>();
    let access = if window.label() == "main" {
        check_paths(&state, &request)
    } else {
        Err(CoreError::new(
            "PERMISSION_DENIED",
            "仅允许主窗口调用本地后端",
        ))
    };
    if let Err(error) = access {
        return Reply {
            request_id: request.request_id,
            version: PROTOCOL_VERSION,
            ok: false,
            data: None,
            error: Some(error),
        };
    }
    let engine = state.engine.clone();
    let request_id = request.request_id.clone();
    let ready = state.worker_ready.load(Ordering::Acquire);
    let is_health = request.method == "health";
    match tauri::async_runtime::spawn_blocking(move || engine.dispatch(request)).await {
        Ok(mut reply) => {
            if is_health && let Some(data) = reply.data.as_mut() {
                data["workerReady"] = serde_json::json!(ready);
            }
            reply
        }
        Err(e) => Reply {
            request_id,
            version: PROTOCOL_VERSION,
            ok: false,
            data: None,
            error: Some(CoreError::new("BACKGROUND_TASK_FAILED", e)),
        },
    }
}

#[tauri::command]
async fn agent_local_status(
    window: tauri::WebviewWindow,
    app: tauri::AppHandle,
) -> Result<serde_json::Value, CoreError> {
    if window.label() != "main" {
        return Err(CoreError::new(
            "PERMISSION_DENIED",
            "仅主窗口可查询本机 Agent 状态",
        ));
    }
    tauri::async_runtime::spawn_blocking(move || {
        let state = app.state::<HostState>();
        let endpoint = local_agent_endpoint(&app, &state);
        Ok(local_agent_status_or_unavailable(
            endpoint,
            |port, token| {
                local_agent_request(
                    port,
                    token,
                    "GET",
                    "/v1/agent/local-status",
                    None,
                    Duration::from_secs(4),
                )
            },
        ))
    })
    .await
    .map_err(|_| CoreError::new("BACKGROUND_TASK_FAILED", "本机 Agent 状态查询失败"))?
}

#[derive(serde::Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct LocalAgentTurnRequest {
    query: String,
    session_id: String,
    turn_id: String,
    use_model: bool,
    capabilities: Vec<String>,
    destination_grant_ids: Vec<String>,
}

fn local_agent_id_is_safe(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 160
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.'))
}

fn validate_local_turn_request(value: &serde_json::Value) -> Result<(), CoreError> {
    let request: LocalAgentTurnRequest = serde_json::from_value(value.clone())
        .map_err(|_| CoreError::new("INVALID_PARAMS", "本机 Agent 请求字段无效"))?;
    if request.query.trim().is_empty() || request.query.len() > 500 {
        return Err(CoreError::new("INVALID_PARAMS", "查询内容需为 1 至 500 字"));
    }
    if !local_agent_id_is_safe(&request.session_id) || !local_agent_id_is_safe(&request.turn_id) {
        return Err(CoreError::new("INVALID_PARAMS", "本机 Agent 会话编号无效"));
    }
    if !request.use_model
        || request.capabilities
            != [
                "search_media",
                "get_metadata",
                "get_backup_job",
                "open_item",
            ]
        || !request.destination_grant_ids.is_empty()
    {
        return Err(CoreError::new(
            "AGENT_CAPABILITY_NOT_ALLOWED",
            "当前本机 Agent 仅允许本地只读查询与需确认的打开提议",
        ));
    }
    Ok(())
}

fn validate_local_turn_response(
    response: &serde_json::Value,
    session_id: &str,
    turn_id: &str,
) -> Result<(), CoreError> {
    if response["sessionId"] != session_id
        || response["turnId"] != turn_id
        || response["orchestrator"] != "langgraph"
        || response["executionAuthorized"] != false
        || response["filesChanged"] != false
    {
        return Err(CoreError::new(
            "LOCAL_AGENT_INVALID_RESPONSE",
            "本机 Agent 响应边界无效",
        ));
    }
    Ok(())
}

#[tauri::command]
async fn agent_local_turn(
    window: tauri::WebviewWindow,
    app: tauri::AppHandle,
    request: serde_json::Value,
) -> Result<serde_json::Value, CoreError> {
    if window.label() != "main" {
        return Err(CoreError::new(
            "PERMISSION_DENIED",
            "仅主窗口可调用本机 Agent",
        ));
    }
    validate_local_turn_request(&request)?;
    tauri::async_runtime::spawn_blocking(move || {
        let session_id = request["sessionId"]
            .as_str()
            .unwrap_or_default()
            .to_string();
        let turn_id = request["turnId"].as_str().unwrap_or_default().to_string();
        let state = app.state::<HostState>();
        let (port, token) = local_agent_endpoint(&app, &state)?;
        let response = local_agent_request(
            port,
            &token,
            "POST",
            "/v1/agent/local-turns",
            Some(&request),
            Duration::from_secs(180),
        )?;
        validate_local_turn_response(&response, &session_id, &turn_id)?;
        Ok(response)
    })
    .await
    .map_err(|_| CoreError::new("BACKGROUND_TASK_FAILED", "本机 Agent 请求失败"))?
}

#[tauri::command]
async fn agent_local_turn_results(
    window: tauri::WebviewWindow,
    app: tauri::AppHandle,
    session_id: String,
    turn_id: String,
    request: serde_json::Value,
) -> Result<serde_json::Value, CoreError> {
    if window.label() != "main" {
        return Err(CoreError::new(
            "PERMISSION_DENIED",
            "仅主窗口可续交本机 Agent 结果",
        ));
    }
    if !local_agent_id_is_safe(&session_id) || !local_agent_id_is_safe(&turn_id) {
        return Err(CoreError::new("INVALID_PARAMS", "本机 Agent 会话编号无效"));
    }
    let continuation = request["continuationToken"].as_str().unwrap_or_default();
    let results = request["results"].as_array();
    if continuation.is_empty()
        || continuation.len() > 16_384
        || request["useModel"] != true
        || results.is_none_or(|items| items.len() > 10)
    {
        return Err(CoreError::new("INVALID_PARAMS", "本机 Agent 续交结果无效"));
    }
    tauri::async_runtime::spawn_blocking(move || {
        let path = format!("/v1/agent/local-turns/{session_id}/{turn_id}/results");
        let state = app.state::<HostState>();
        let (port, token) = local_agent_endpoint(&app, &state)?;
        let response = local_agent_request(
            port,
            &token,
            "POST",
            &path,
            Some(&request),
            Duration::from_secs(180),
        )?;
        validate_local_turn_response(&response, &session_id, &turn_id)?;
        Ok(response)
    })
    .await
    .map_err(|_| CoreError::new("BACKGROUND_TASK_FAILED", "本机 Agent 续交失败"))?
}

/// Prepare a short-lived, single-use preview for an Agent open action. The
/// model supplies only an indexed media ID; the host resolves and authorizes
/// the path, then requires a separate explicit confirmation before opening.
#[tauri::command]
async fn agent_prepare_action(
    window: tauri::WebviewWindow,
    app: tauri::AppHandle,
    action_id: String,
    tool: String,
    file_id: String,
    expires_at_ms: u64,
) -> Result<serde_json::Value, CoreError> {
    if window.label() != "main" || tool != "open_item" {
        return Err(CoreError::new(
            "PERMISSION_DENIED",
            "仅主窗口可预览打开素材操作",
        ));
    }
    if action_id.is_empty() || action_id.len() > 160 {
        return Err(CoreError::new("INVALID_ACTION_ID", "操作编号无效"));
    }
    let now_ms = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64;
    validate_action_expiry(now_ms, expires_at_ms)?;
    let state = app.state::<HostState>();
    let engine = state.engine.clone();
    let lookup_id = file_id.clone();
    let path =
        tauri::async_runtime::spawn_blocking(move || engine.agent_resolve_open_item(&lookup_id))
            .await
            .map_err(|error| CoreError::new("BACKGROUND_TASK_FAILED", error))??;
    let roots = state
        .sources
        .lock()
        .map_err(|_| CoreError::new("STATE_UNAVAILABLE", "来源目录授权状态不可用"))?
        .iter()
        .cloned()
        .collect::<Vec<_>>();
    yingxu_core::agent::validate_open_grant(&path, &roots)?;
    let name = path
        .file_name()
        .and_then(|value| value.to_str())
        .ok_or_else(|| CoreError::new("INVALID_PATH_ENCODING", "素材名称无法显示"))?
        .to_string();
    let mut pending = state
        .pending_agent_actions
        .lock()
        .map_err(|_| CoreError::new("STATE_UNAVAILABLE", "操作预览状态不可用"))?;
    pending.retain(|_, action| action.expires_at_ms > now_ms);
    if pending.contains_key(&action_id) {
        return Err(CoreError::new(
            "DUPLICATE_ACTION_ID",
            "操作编号已使用，请重新查询",
        ));
    }
    pending.insert(
        action_id.clone(),
        PendingOpenAction {
            media_id: file_id.clone(),
            path,
            expires_at_ms,
        },
    );
    Ok(serde_json::json!({
        "actionId": action_id,
        "tool": "open_item",
        "fileId": file_id,
        "name": name,
        "expiresAtMs": expires_at_ms
    }))
}

/// Consume a prepared action exactly once. Declines never touch the file;
/// acceptance revalidates the indexed identity and directory grant before the
/// fixed OS default-app opener is called.
#[tauri::command]
async fn agent_confirm_action(
    window: tauri::WebviewWindow,
    app: tauri::AppHandle,
    action_id: String,
    accepted: bool,
) -> Result<serde_json::Value, CoreError> {
    if window.label() != "main" {
        return Err(CoreError::new(
            "PERMISSION_DENIED",
            "仅主窗口可确认本机操作",
        ));
    }
    let state = app.state::<HostState>();
    let pending = state
        .pending_agent_actions
        .lock()
        .map_err(|_| CoreError::new("STATE_UNAVAILABLE", "操作预览状态不可用"))?
        .remove(&action_id)
        .ok_or_else(|| CoreError::new("ACTION_NOT_FOUND", "操作预览已失效或已使用"))?;
    let receipt = |status: &str, data: serde_json::Value| {
        serde_json::json!({
            "actionId": action_id,
            "tool": "open_item",
            "status": status,
            "data": data,
            "executionAuthorized": false
        })
    };
    if !accepted {
        return Ok(receipt("declined", serde_json::json!({})));
    }
    let now_ms = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64;
    if pending.expires_at_ms <= now_ms {
        return Ok(receipt("failed", serde_json::json!({})));
    }
    let current = state.engine.agent_resolve_open_item(&pending.media_id)?;
    if current != pending.path {
        return Ok(receipt("failed", serde_json::json!({})));
    }
    let roots = state
        .sources
        .lock()
        .map_err(|_| CoreError::new("STATE_UNAVAILABLE", "来源目录授权状态不可用"))?
        .iter()
        .cloned()
        .collect::<Vec<_>>();
    yingxu_core::agent::validate_open_grant(&current, &roots)?;
    open_in_default_app(&current)?;
    let receipt_id = format!(
        "agent-open-{}-{}",
        std::process::id(),
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos()
    );
    state.engine.store.audit(
        "agent_open_item",
        &pending.media_id,
        serde_json::json!({"receiptId": receipt_id, "opened": true, "actionId": action_id}),
    )?;
    Ok(receipt(
        "succeeded",
        serde_json::json!({
            "mediaId": pending.media_id,
            "opened": true,
            "receiptId": receipt_id
        }),
    ))
}

/// Invoke only the operating system's fixed default-app opener. No shell,
/// model-supplied command, URL, or arbitrary path is accepted here.
#[cfg(target_os = "macos")]
fn open_in_default_app(path: &std::path::Path) -> Result<(), CoreError> {
    let status = std::process::Command::new("/usr/bin/open")
        .arg(path)
        .status()
        .map_err(|_| CoreError::new("OS_OPEN_FAILED", "系统未能启动默认应用"))?;
    if !status.success() {
        return Err(CoreError::new("OS_OPEN_FAILED", "系统未能打开此素材"));
    }
    Ok(())
}

#[cfg(windows)]
fn open_in_default_app(path: &std::path::Path) -> Result<(), CoreError> {
    use std::{ffi::OsStr, os::windows::ffi::OsStrExt};
    let operation: Vec<u16> = OsStr::new("open").encode_wide().chain(Some(0)).collect();
    let file: Vec<u16> = path.as_os_str().encode_wide().chain(Some(0)).collect();
    #[link(name = "shell32")]
    unsafe extern "system" {
        fn ShellExecuteW(
            hwnd: *mut std::ffi::c_void,
            operation: *const u16,
            file: *const u16,
            parameters: *const u16,
            directory: *const u16,
            show_command: i32,
        ) -> isize;
    }
    // SAFETY: Both strings are NUL-terminated and remain alive for the call;
    // the only target is the already-authorized indexed file.
    let result = unsafe {
        ShellExecuteW(
            std::ptr::null_mut(),
            operation.as_ptr(),
            file.as_ptr(),
            std::ptr::null(),
            std::ptr::null(),
            1,
        )
    };
    if result <= 32 {
        return Err(CoreError::new("OS_OPEN_FAILED", "系统未能打开此素材"));
    }
    Ok(())
}

#[cfg(all(not(target_os = "macos"), not(windows)))]
fn open_in_default_app(path: &std::path::Path) -> Result<(), CoreError> {
    let status = std::process::Command::new("xdg-open")
        .arg(path)
        .status()
        .map_err(|_| CoreError::new("OS_OPEN_FAILED", "系统未能启动默认应用"))?;
    if !status.success() {
        return Err(CoreError::new("OS_OPEN_FAILED", "系统未能打开此素材"));
    }
    Ok(())
}

fn shutdown(app: tauri::AppHandle) {
    let state = app.state::<HostState>();
    if state.shutdown.swap(true, Ordering::AcqRel) {
        return;
    }
    if let Ok(jobs) = state.engine.store.jobs() {
        for job in jobs {
            if ["queued", "running"].contains(&job.state.as_str()) {
                let _ = state.engine.control(&job.id, "pause", None);
            }
        }
    }
    let handle = state.worker.lock().ok().and_then(|mut h| h.take());
    std::thread::spawn(move || {
        if let Some(handle) = handle {
            let _ = handle.join();
        }
        stop_local_agent(&app.state::<HostState>());
        app.exit(0);
    });
}
fn request_quit(app: tauri::AppHandle) {
    let active = app
        .state::<HostState>()
        .engine
        .store
        .jobs()
        .map_or(true, |jobs| {
            jobs.iter()
                .any(|j| ["queued", "running"].contains(&j.state.as_str()))
        });
    if active {
        let next = app.clone();
        app.dialog()
            .message("仍有任务运行。退出将暂停任务并保存检查点，重新打开后可继续。")
            .title("退出影序")
            .buttons(MessageDialogButtons::OkCancel)
            .show(move |confirmed| {
                if confirmed {
                    shutdown(next)
                }
            });
    } else {
        shutdown(app)
    }
}

/// 启动失败时的兜底：弹原生对话框说明原因，然后以可读的退出码退出。
/// 绝不让 setup 阶段的错误变成 Rust panic（GUI 程序的 panic 用户完全看不到）。
fn fail_startup(app: &tauri::AppHandle, error: &CoreError) -> ! {
    let message = if error.code == "WORKER_ALREADY_RUNNING" {
        "影序已经在运行（可能驻留在托盘）。请从托盘图标打开，或先退出已运行的实例。"
    } else {
        &error.message
    };
    let _ = app
        .dialog()
        .message(message)
        .title("影序启动失败")
        .blocking_show();
    eprintln!("startup failed: {} {}", error.code, error.message);
    std::process::exit(2);
}

/// 崩溃日志目录（测试反馈 §9 P2）：与 Tauri 的 `app_data_dir` 同源，
/// 固定 identifier `app.yingxu.desktop.next`，子目录 `logs/`。
fn crash_logs_dir() -> Option<PathBuf> {
    let base = if cfg!(target_os = "macos") {
        std::env::var_os("HOME").map(|h| PathBuf::from(h).join("Library/Application Support"))
    } else {
        std::env::var_os("APPDATA").map(PathBuf::from)
    }?;
    Some(base.join("app.yingxu.desktop.next").join("logs"))
}

/// Unix 秒 → `YYYYMMDD-HHMMSS`（UTC，Howard Hinnant civil_from_days 算法，不引依赖）。
fn utc_stamp(secs: u64) -> String {
    let days = (secs / 86_400) as i64;
    let rem = secs % 86_400;
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    format!(
        "{y:04}{m:02}{d:02}-{rem_h:02}{rem_m:02}{rem_s:02}",
        rem_h = rem / 3600,
        rem_m = rem % 3600 / 60,
        rem_s = rem % 60
    )
}

/// 安装 panic hook：GUI 程序的 panic 只写 stdout，用户完全看不到（启动闪退报告 §7.3）。
/// 改为把 panic 消息、位置、线程名与回溯写入 `logs/crash-<时间>.log`，便于测试方取证。
fn install_crash_logger() {
    let dir = crash_logs_dir();
    if let Some(dir) = &dir {
        let _ = std::fs::create_dir_all(dir);
    }
    std::panic::set_hook(Box::new(move |info| {
        let thread = std::thread::current();
        let thread = thread.name().unwrap_or("<unnamed>").to_string();
        let payload = if let Some(s) = info.payload().downcast_ref::<&str>() {
            (*s).to_string()
        } else if let Some(s) = info.payload().downcast_ref::<String>() {
            s.clone()
        } else {
            "unknown panic payload".to_string()
        };
        let location = info
            .location()
            .map_or_else(|| "<unknown>".to_string(), |l| l.to_string());
        let backtrace = std::backtrace::Backtrace::force_capture();
        let secs = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);
        eprintln!("thread '{thread}' panicked at {location}: {payload}");
        let Some(dir) = dir.as_ref() else {
            return;
        };
        let mut text = format!(
            "time_utc: {}\nthread: {thread}\nlocation: {location}\nmessage: {payload}\nbacktrace:\n{backtrace}\n",
            utc_stamp(secs)
        );
        if let Ok(extra) = std::env::var("YINGXU_CRASH_NOTE") {
            text.push_str("note: ");
            text.push_str(&extra);
            text.push('\n');
        }
        let file = dir.join(format!("crash-{}.log", utc_stamp(secs)));
        let _ = std::fs::write(&file, text);
        // 只保留最近 20 份，防止日志无限增长
        if let Ok(entries) = std::fs::read_dir(dir) {
            let mut crashes: Vec<(SystemTime, PathBuf)> = entries
                .filter_map(|e| {
                    let entry = e.ok()?;
                    let p = entry.path();
                    let t = entry.metadata().ok()?.modified().ok()?;
                    p.file_name()?
                        .to_str()?
                        .starts_with("crash-")
                        .then_some((t, p))
                })
                .collect();
            if crashes.len() > 20 {
                crashes.sort_by_key(|(t, _)| *t);
                for (_, p) in crashes.drain(..crashes.len() - 20) {
                    let _ = std::fs::remove_file(p);
                }
            }
        }
    }));
}

fn format_rate(bytes_per_second: u64) -> String {
    const UNITS: [&str; 4] = ["B/s", "KB/s", "MB/s", "GB/s"];
    let mut value = bytes_per_second as f64;
    let mut unit = 0;
    while value >= 1024.0 && unit < UNITS.len() - 1 {
        value /= 1024.0;
        unit += 1;
    }
    format!("{value:.1} {}", UNITS[unit])
}

fn tray_progress(job: &yingxu_core::Job) -> (String, String) {
    let percent = job.progress.clamp(0.0, 100.0).round() as u8;
    let speed = job.runtime["speedBytesPerSecond"].as_u64().unwrap_or(0);
    if job.state != "running" {
        let state = match job.state.as_str() {
            "queued" => "排队中",
            "paused" => "已暂停",
            _ => "等待中",
        };
        return (
            format!("{state} {percent}%"),
            format!("影序 · {} · {state} · {percent}%", job.name),
        );
    }

    let rate = if speed > 0 {
        format_rate(speed)
    } else {
        "计算中".to_string()
    };
    let total_bytes = job.total_bytes.saturating_mul(job.targets.len() as u64);
    let remaining_bytes =
        (total_bytes as f64 * (100.0 - job.progress.clamp(0.0, 100.0)) / 100.0) as u64;
    let eta = if speed == 0 {
        "估算中".to_string()
    } else {
        let seconds = remaining_bytes / speed;
        if seconds >= 3600 {
            format!("{}小时{}分", seconds / 3600, seconds % 3600 / 60)
        } else if seconds >= 60 {
            format!("{}分{}秒", seconds / 60, seconds % 60)
        } else {
            format!("{seconds}秒")
        }
    };
    (
        format!("{percent}% · {rate}"),
        format!("影序 · {} · {percent}% · {rate} · 剩余约 {eta}", job.name),
    )
}

/// 首次关窗缩托盘的一次性提示（测试反馈 §9 P1）。Windows 原生气泡需要签名/AUMID，
/// 这里用一次性原生对话框兜底，并发前端事件供后续做 toast。
fn tray_hint_once(app: &tauri::AppHandle, state: &HostState) {
    let Ok(prefs) = state
        .engine
        .store
        .get::<serde_json::Value>("settings", "preferences")
    else {
        return;
    };
    if prefs["trayHintShown"] == true {
        return;
    }
    let mut prefs = prefs;
    prefs["trayHintShown"] = serde_json::json!(true);
    if state
        .engine
        .store
        .put("settings", "preferences", &prefs)
        .is_err()
    {
        return;
    }
    let _ = app.emit("local-minimized-to-tray", ());
    app.dialog()
        .message("影序已最小化到系统托盘，点击托盘图标可恢复窗口。本提示只显示这一次。")
        .title("影序 YINGXU")
        .buttons(MessageDialogButtons::Ok)
        .show(|_| {});
}

fn main() {
    install_crash_logger();
    let app = tauri::Builder::default()
        // 单实例必须最先注册：第二实例启动时，回调在第一实例里执行（唤回窗口），
        // 第二实例自身静默退出（退出码 0）。修复「重复双击 → setup panic 闪退」（测试反馈 2026-10-26）。
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.unminimize();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            select_directory,
            backend_request,
            agent_local_status,
            agent_local_turn,
            agent_local_turn_results,
            agent_prepare_action,
            agent_confirm_action,
            agent_prepare_file_action,
            agent_confirm_file_action,
            open_system_trash
        ])
        .plugin(
            tauri::plugin::Builder::<tauri::Wry, ()>::new("navigation-guard")
                .on_navigation(|_, url| {
                    matches!(url.scheme(), "tauri" | "asset")
                        || url.host_str().is_some_and(|h| {
                            h == "tauri.localhost"
                                || cfg!(debug_assertions)
                                    && h == "127.0.0.1"
                                    && url.port() == Some(5173)
                        })
                })
                .build(),
        )
        .setup(|app| {
            // 引擎初始化失败不能 panic：panic 对 GUI 程序用户不可见（表现为"闪退"）。
            let root = match app.path().app_data_dir() {
                Ok(root) => root,
                Err(e) => fail_startup(
                    app.handle(),
                    &CoreError::new("APP_DATA_DIR_FAILED", e.to_string()),
                ),
            };
            let engine = match Engine::open(root.clone()) {
                Ok(engine) => engine,
                Err(e) => fail_startup(app.handle(), &e),
            };
            let worker = match engine.worker() {
                Ok(worker) => worker,
                Err(e) => fail_startup(app.handle(), &e),
            };
            let shutdown_flag = Arc::new(AtomicBool::new(false));
            let ready = Arc::new(AtomicBool::new(true));
            let stop = shutdown_flag.clone();
            let running = ready.clone();
            let handle = app.handle().clone();
            let thread = std::thread::spawn(move || {
                let mut failures = 0;
                while !stop.load(Ordering::Acquire) {
                    match worker.run_next() {
                        Ok(Some(job)) => {
                            failures = 0;
                            let _ = handle.emit("local-job-updated", &job);
                        }
                        Ok(None) => std::thread::sleep(Duration::from_millis(250)),
                        Err(error) => {
                            let _ = handle.emit("local-backend-error", &error);
                            failures += 1;
                            if failures >= 3 {
                                break;
                            }
                            std::thread::sleep(Duration::from_secs(1));
                        }
                    }
                }
                running.store(false, Ordering::Release);
            });
            let progress_stop = shutdown_flag.clone();
            let progress_engine = engine.clone();
            let progress_handle = app.handle().clone();
            std::thread::spawn(move || {
                while !progress_stop.load(Ordering::Acquire) {
                    let active = progress_engine.store.jobs().ok().and_then(|jobs| {
                        jobs.into_iter()
                            .filter_map(|job| {
                                let priority = match job.state.as_str() {
                                    "running" => 3,
                                    "paused" => 2,
                                    "queued" => 1,
                                    _ => return None,
                                };
                                Some((job, priority))
                            })
                            .max_by_key(|(_, priority)| *priority)
                            .map(|(job, _)| job)
                    });
                    if let Some(job) = active.as_ref() {
                        let _ = progress_handle.emit("local-job-updated", job);
                    }
                    let (title, tooltip) = active
                        .as_ref()
                        .map_or_else(|| (String::new(), "影序 YINGXU".to_string()), tray_progress);
                    let main_thread_handle = progress_handle.clone();
                    let _ = progress_handle.run_on_main_thread(move || {
                        if let Some(tray) = main_thread_handle.tray_by_id("main") {
                            let _ = tray.set_title((!title.is_empty()).then_some(title.as_str()));
                            let _ = tray.set_tooltip(Some(tooltip.as_str()));
                        }
                    });
                    std::thread::sleep(Duration::from_millis(500));
                }
            });
            app.manage(HostState {
                engine,
                shutdown: shutdown_flag,
                worker_ready: ready,
                worker: Mutex::new(Some(thread)),
                sources: Mutex::new(HashSet::new()),
                targets: Mutex::new(HashSet::new()),
                directory_grants: Mutex::new(HashMap::new()),
                pending_agent_actions: Mutex::new(HashMap::new()),
                app_data_dir: root,
                local_agent: Mutex::new(None),
            });
            let open = MenuItem::with_id(app, "open", "打开影序", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "退出影序", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&open, &quit])?;
            let mut pixels = vec![0u8; 24 * 24 * 4];
            for y in 0..24 {
                for x in 0..24 {
                    let i = (y * 24 + x) * 4;
                    let light = (5..10).contains(&x) || (14..19).contains(&x);
                    let light = light && ((5..10).contains(&y) || (14..19).contains(&y));
                    pixels[i..i + 4].copy_from_slice(if light {
                        &[244, 242, 237, 255]
                    } else {
                        &[39, 61, 49, 255]
                    });
                }
            }
            TrayIconBuilder::with_id("main")
                .icon(tauri::image::Image::new_owned(pixels, 24, 24))
                .title("")
                .tooltip("影序 YINGXU")
                .menu(&menu)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "open" => {
                        if let Some(w) = app.get_webview_window("main") {
                            let _ = w.show();
                            let _ = w.set_focus();
                        }
                    }
                    "quit" => request_quit(app.clone()),
                    _ => {}
                })
                .build(app)?;
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                let state = window.state::<HostState>();
                let background = state
                    .engine
                    .store
                    .get::<serde_json::Value>("settings", "preferences")
                    .map_or(true, |p| p["backgroundOnClose"] == true);
                api.prevent_close();
                if background {
                    let _ = window.hide();
                    tray_hint_once(window.app_handle(), &state);
                } else {
                    request_quit(window.app_handle().clone());
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("Failed to initialize YINGXU desktop host");
    app.run(|app, event| {
        if let tauri::RunEvent::ExitRequested { api, .. } = event
            && !app.state::<HostState>().shutdown.load(Ordering::Acquire)
        {
            api.prevent_exit();
            request_quit(app.clone());
        }
    });
}

#[cfg(test)]
mod agent_action_tests {
    use super::{
        HostState, check_paths, local_agent_request, local_agent_status_or_unavailable,
        resolve_local_agent_executable, spawn_local_agent, spawn_local_agent_with_timeout,
        stop_local_agent_process, tray_progress, validate_action_expiry,
        validate_local_turn_request, validate_local_turn_response,
    };
    use std::collections::{HashMap, HashSet};
    use std::{
        path::PathBuf,
        sync::{Arc, Mutex, atomic::AtomicBool},
        time::{Duration, SystemTime, UNIX_EPOCH},
    };
    use yingxu_core::{
        Engine,
        agent::execute_read_tool,
        api::{PROTOCOL_VERSION, Request},
    };

    fn tray_job(state: &str, progress: f64, speed: u64) -> yingxu_core::Job {
        yingxu_core::Job {
            id: "job-1".into(),
            name: "测试备份".into(),
            kind: "backup".into(),
            state: state.into(),
            phase: "copying".into(),
            source: PathBuf::from("/source"),
            source_identity: yingxu_core::Fingerprint {
                bytes: 0,
                modified_ns: 0,
                identity: "source".into(),
            },
            verify: true,
            created_at: String::new(),
            updated_at: String::new(),
            total_bytes: 60 * 1024 * 1024,
            files: 1,
            progress,
            targets: vec![yingxu_core::Target {
                id: "target-1".into(),
                path: PathBuf::from("/target"),
                identity: yingxu_core::Fingerprint {
                    bytes: 0,
                    modified_ns: 0,
                    identity: "target".into(),
                },
                state: "running".into(),
                copied: 0,
                skipped: 0,
                verified: 0,
                failed: 0,
            }],
            items: vec![],
            error: None,
            options: serde_json::json!({}),
            runtime: serde_json::json!({ "speedBytesPerSecond": speed }),
        }
    }

    #[test]
    fn tray_progress_includes_rate_and_estimated_remaining_time() {
        let (title, tooltip) = tray_progress(&tray_job("running", 25.0, 1024 * 1024));
        assert!(title.contains("25%"));
        assert!(title.contains("1.0 MB/s"));
        assert!(tooltip.contains("测试备份"));
        assert!(tooltip.contains("剩余约 45秒"));
    }

    #[cfg(windows)]
    #[test]
    fn windows_signing_key_fits_secret_storage_and_reads_legacy_format() {
        use rsa::pkcs8::EncodePrivateKey;
        let key = rsa::RsaPrivateKey::new(&mut rand::thread_rng(), 2048).unwrap();
        let pem = key
            .to_pkcs8_pem(rsa::pkcs8::LineEnding::LF)
            .unwrap()
            .to_string();
        assert!(pem.len() <= 2560);
        assert!(pem.encode_utf16().count() * 2 > 2560);
        assert_eq!(
            super::decode_agent_signing_key(pem.as_bytes()).unwrap(),
            pem
        );
        let legacy: Vec<u8> = pem.encode_utf16().flat_map(u16::to_le_bytes).collect();
        assert_eq!(super::decode_agent_signing_key(&legacy).unwrap(), pem);
        assert!(super::decode_agent_signing_key(b"invalid key").is_err());
    }

    #[test]
    fn tray_progress_handles_unknown_speed_and_idle_jobs() {
        let (_, running) = tray_progress(&tray_job("running", 0.0, 0));
        assert!(running.contains("估算中"));
        let (paused, _) = tray_progress(&tray_job("paused", 40.0, 0));
        assert!(paused.contains("已暂停"));
    }

    fn local_agent_step_states(
        database_path: &std::path::Path,
        session_id: &str,
        turn_id: &str,
    ) -> Vec<(i64, String)> {
        let connection = rusqlite::Connection::open(database_path).unwrap();
        let mut statement = connection
            .prepare(
                "SELECT step_index, state FROM local_agent_steps \
                 WHERE session_id = ?1 AND turn_id = ?2 ORDER BY step_index",
            )
            .unwrap();
        statement
            .query_map([session_id, turn_id], |row| Ok((row.get(0)?, row.get(1)?)))
            .unwrap()
            .map(Result::unwrap)
            .collect()
    }

    #[test]
    fn agent_open_preview_must_be_future_and_at_most_five_minutes() {
        let now = 1_000_000;
        assert!(validate_action_expiry(now, now + 1).is_ok());
        assert!(validate_action_expiry(now, now + 5 * 60 * 1000).is_ok());
        assert_eq!(
            validate_action_expiry(now, now).unwrap_err().code,
            "ACTION_EXPIRED"
        );
        assert_eq!(
            validate_action_expiry(now, now + 5 * 60 * 1000 + 1)
                .unwrap_err()
                .code,
            "ACTION_EXPIRED"
        );
    }

    #[test]
    fn local_agent_turn_rejects_remote_or_write_capabilities() {
        let mut request = serde_json::json!({
            "query": "查找 IMG_2048.jpg",
            "sessionId": "session-1",
            "turnId": "turn-1",
            "useModel": true,
            "capabilities": ["search_media", "get_metadata", "get_backup_job", "open_item"],
            "destinationGrantIds": []
        });
        assert!(validate_local_turn_request(&request).is_ok());
        request["destinationGrantIds"] = serde_json::json!(["cloud-drive"]);
        assert_eq!(
            validate_local_turn_request(&request).unwrap_err().code,
            "AGENT_CAPABILITY_NOT_ALLOWED"
        );
        request["destinationGrantIds"] = serde_json::json!([]);
        request["capabilities"] = serde_json::json!(["delete_file"]);
        assert_eq!(
            validate_local_turn_request(&request).unwrap_err().code,
            "AGENT_CAPABILITY_NOT_ALLOWED"
        );
        request["capabilities"] = serde_json::json!([
            "search_media",
            "get_metadata",
            "get_backup_job",
            "open_item"
        ]);
        request["useModel"] = serde_json::json!(false);
        assert_eq!(
            validate_local_turn_request(&request).unwrap_err().code,
            "AGENT_CAPABILITY_NOT_ALLOWED"
        );
    }

    #[test]
    fn missing_sidecar_and_status_failures_are_reported_as_unavailable() {
        let missing = std::env::temp_dir()
            .join(format!("yingxu-missing-sidecar-{}", std::process::id()))
            .join("yingxu-local-agent");
        assert_eq!(
            resolve_local_agent_executable(None, missing, false)
                .unwrap_err()
                .code,
            "LOCAL_AGENT_SIDECAR_MISSING"
        );

        let unavailable = local_agent_status_or_unavailable(
            Err(yingxu_core::CoreError::new(
                "LOCAL_AGENT_SIDECAR_MISSING",
                "missing fixture",
            )),
            |_, _| panic!("status must not be requested without a sidecar"),
        );
        assert_eq!(unavailable["availability"], "sidecar_unavailable");
        assert_eq!(unavailable["cloudFallback"], false);

        let unavailable = local_agent_status_or_unavailable(Ok((1234, "token".into())), |_, _| {
            Err(yingxu_core::CoreError::new(
                "LOCAL_AGENT_UNAVAILABLE",
                "failed status fixture",
            ))
        });
        assert_eq!(unavailable["availability"], "sidecar_unavailable");
        assert_eq!(unavailable["cloudFallback"], false);
    }

    #[cfg(unix)]
    #[test]
    fn invalid_or_slow_sidecar_readiness_is_rejected_and_child_stopped() {
        use std::os::unix::fs::PermissionsExt;

        let root = std::env::current_dir()
            .unwrap()
            .canonicalize()
            .unwrap()
            .join("target")
            .join(format!("yingxu-sidecar-readiness-{}", std::process::id()));
        std::fs::create_dir_all(&root).unwrap();
        let script = root.join("fake-sidecar.sh");
        let invalid_marker = root.join("invalid-child-survived");
        let invalid_script = format!(
            "#!/bin/sh\nprintf '%s\\n' '{{\"event\":\"ready\",\"host\":\"0.0.0.0\",\"port\":43210}}'\n/bin/sleep 0.2\n/usr/bin/touch '{}'\n",
            invalid_marker.display()
        );
        std::fs::write(&script, invalid_script).unwrap();
        std::fs::set_permissions(&script, std::fs::Permissions::from_mode(0o700)).unwrap();
        let error = spawn_local_agent_with_timeout(
            &script,
            &root.join("agent.sqlite"),
            "test-key",
            Duration::from_secs(2),
        )
        .err()
        .unwrap();
        assert_eq!(error.code, "LOCAL_AGENT_START_FAILED");
        std::thread::sleep(Duration::from_millis(250));
        assert!(
            !invalid_marker.exists(),
            "invalid sidecar child must be stopped"
        );

        std::fs::write(&script, "#!/bin/sh\n/bin/sleep 5\n").unwrap();
        std::fs::set_permissions(&script, std::fs::Permissions::from_mode(0o700)).unwrap();
        let error = spawn_local_agent_with_timeout(
            &script,
            &root.join("agent.sqlite"),
            "test-key",
            Duration::from_millis(40),
        )
        .err()
        .unwrap();
        assert_eq!(error.code, "LOCAL_AGENT_START_TIMEOUT");
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn scan_report_requires_a_granted_source_directory() {
        let root = std::env::current_dir()
            .unwrap()
            .canonicalize()
            .unwrap()
            .join("target")
            .join(format!(
                "yingxu-host-scan-report-scope-{}",
                std::process::id()
            ));
        let app_data = root.join("app-data");
        let source = root.join("source");
        std::fs::create_dir_all(&source).unwrap();
        let engine = Engine::open(&app_data).unwrap();
        let state = HostState {
            engine,
            shutdown: Arc::new(AtomicBool::new(false)),
            worker_ready: Arc::new(AtomicBool::new(false)),
            worker: Mutex::new(None),
            sources: Mutex::new(HashSet::new()),
            targets: Mutex::new(HashSet::new()),
            directory_grants: Mutex::new(HashMap::new()),
            pending_agent_actions: Mutex::new(HashMap::new()),
            app_data_dir: app_data,
            local_agent: Mutex::new(None),
        };
        let request = Request {
            request_id: "scope-test".to_string(),
            version: PROTOCOL_VERSION,
            method: "backup.scanReport".to_string(),
            params: serde_json::json!({"source": source}),
        };
        assert_eq!(
            check_paths(&state, &request).unwrap_err().code,
            "DIRECTORY_GRANT_REQUIRED"
        );
        state
            .sources
            .lock()
            .unwrap()
            .insert(source.canonicalize().unwrap());
        assert!(check_paths(&state, &request).is_ok());
        drop(state);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn packaged_local_agent_status_and_shutdown_round_trip() {
        let executable = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(if cfg!(windows) {
            "agent-sidecar/yingxu-local-agent/yingxu-local-agent.exe"
        } else {
            "agent-sidecar/yingxu-local-agent/yingxu-local-agent"
        });
        if !executable.is_file() {
            return;
        }
        use rsa::pkcs8::EncodePrivateKey;
        let signing_key = rsa::RsaPrivateKey::new(&mut rand::thread_rng(), 2048)
            .unwrap()
            .to_pkcs8_pem(rsa::pkcs8::LineEnding::LF)
            .unwrap()
            .to_string();
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("target")
            .join(format!(
                "yingxu-host-agent-smoke-{}-{}",
                std::process::id(),
                SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .unwrap()
                    .as_nanos()
            ));
        std::fs::create_dir_all(&root).unwrap();
        let mut process =
            spawn_local_agent(&executable, &root.join("agent.sqlite"), &signing_key).unwrap();
        let health = local_agent_request(
            process.port,
            &process.token,
            "GET",
            "/healthz",
            None,
            Duration::from_secs(4),
        )
        .unwrap();
        assert_eq!(health["service"], "yingxu-local-agent");
        let status = local_agent_request(
            process.port,
            &process.token,
            "GET",
            "/v1/agent/local-status",
            None,
            Duration::from_secs(4),
        )
        .unwrap();
        assert_eq!(status["requiresExplicitConsent"], true);
        assert_eq!(status["executionAuthorized"], false);
        assert_eq!(status["cloudFallback"], false);
        assert!(matches!(
            status["availability"].as_str(),
            Some("ready" | "model_not_installed" | "ollama_unavailable")
        ));
        assert!(
            local_agent_request(
                process.port,
                "wrong-token",
                "GET",
                "/v1/agent/local-status",
                None,
                Duration::from_secs(4),
            )
            .is_err()
        );
        stop_local_agent_process(&mut process);
        assert!(process.child.try_wait().unwrap().is_some());
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn packaged_local_agent_real_model_tool_round_trip_from_host() {
        if std::env::var("YINGXU_RUN_OLLAMA_HOST_TESTS").as_deref() != Ok("1") {
            return;
        }
        let executable = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(if cfg!(windows) {
            "agent-sidecar/yingxu-local-agent/yingxu-local-agent.exe"
        } else {
            "agent-sidecar/yingxu-local-agent/yingxu-local-agent"
        });
        assert!(
            executable.is_file(),
            "stage the platform sidecar package first"
        );
        use rsa::pkcs8::EncodePrivateKey;
        let signing_key = rsa::RsaPrivateKey::new(&mut rand::thread_rng(), 2048)
            .unwrap()
            .to_pkcs8_pem(rsa::pkcs8::LineEnding::LF)
            .unwrap()
            .to_string();
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("target")
            .join(format!(
                "yingxu-host-ollama-smoke-{}-{}",
                std::process::id(),
                SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .unwrap()
                    .as_nanos()
            ));
        std::fs::create_dir_all(&root).unwrap();
        let source = root.join("media-source");
        std::fs::create_dir_all(&source).unwrap();
        std::fs::write(source.join("IMG_2048.jpg"), b"indexed local photo fixture").unwrap();
        let media_engine = Engine::open(root.join("media-db")).unwrap();
        media_engine.index_source(&source).unwrap();
        let indexed = media_engine
            .agent_search_media("IMG_2048", "photo", "all", 10)
            .unwrap();
        assert_eq!(indexed["matches"].as_array().unwrap().len(), 1);
        assert_eq!(indexed["matches"][0]["name"], "IMG_2048.jpg");
        let mut process =
            spawn_local_agent(&executable, &root.join("agent.sqlite"), &signing_key).unwrap();
        let database_path = root.join("agent.sqlite");
        let status = local_agent_request(
            process.port,
            &process.token,
            "GET",
            "/v1/agent/local-status",
            None,
            Duration::from_secs(4),
        )
        .unwrap();
        assert_eq!(
            status["configured"], true,
            "Ollama model must be installed for this opt-in test"
        );
        let request = serde_json::json!({
            "query": "请先使用 search_media 工具，在本机照片索引里搜索文件名包含 IMG_2048 的照片。收到结果后用中文总结，不再调用工具，也不要打开文件。",
            "sessionId": "host-verify-session",
            "turnId": "host-verify-turn",
            "useModel": true,
            "capabilities": ["search_media", "get_metadata", "get_backup_job", "open_item"],
            "destinationGrantIds": []
        });
        validate_local_turn_request(&request).unwrap();
        let mut turn = local_agent_request(
            process.port,
            &process.token,
            "POST",
            "/v1/agent/local-turns",
            Some(&request),
            Duration::from_secs(120),
        )
        .unwrap();
        validate_local_turn_response(&turn, "host-verify-session", "host-verify-turn").unwrap();
        assert_eq!(turn["status"], "awaiting_local_tools");
        assert_eq!(turn["modelRequests"], 1);
        let tool = &turn["localToolRequests"][0];
        assert_eq!(tool["tool"], "search_media");
        let local_result =
            execute_read_tool(&media_engine, "search_media", &tool["arguments"]).unwrap();
        assert_eq!(local_result["matches"].as_array().unwrap().len(), 1);
        assert_eq!(local_result["matches"][0]["name"], "IMG_2048.jpg");
        let replay_body = serde_json::json!({
            "continuationToken": turn["continuationToken"],
            "useModel": true,
            "results": [{
                "callId": tool["callId"],
                "tool": "search_media",
                "status": "succeeded",
                "data": {
                    "matches": local_result["matches"],
                    "truncated": local_result["truncated"]
                }
            }]
        });
        let path = "/v1/agent/local-turns/host-verify-session/host-verify-turn/results";
        assert_eq!(
            local_agent_step_states(&database_path, "host-verify-session", "host-verify-turn"),
            vec![(0, "awaiting_tools".to_string())]
        );

        // Model a desktop host crash: the sidecar is killed without sending its graceful
        // shutdown endpoint. Its SQLite ledger and signed continuation must survive.
        process.child.kill().unwrap();
        assert!(!process.child.wait().unwrap().success());
        let mut process = spawn_local_agent(&executable, &database_path, &signing_key).unwrap();
        let status = local_agent_request(
            process.port,
            &process.token,
            "GET",
            "/v1/agent/local-status",
            None,
            Duration::from_secs(4),
        )
        .unwrap();
        assert_eq!(status["cloudFallback"], false);
        assert_eq!(
            local_agent_step_states(&database_path, "host-verify-session", "host-verify-turn"),
            vec![(0, "awaiting_tools".to_string())],
            "starting the sidecar must not re-run a pending model step"
        );
        let duplicate_start = local_agent_request(
            process.port,
            &process.token,
            "POST",
            "/v1/agent/local-turns",
            Some(&request),
            Duration::from_secs(10),
        )
        .unwrap();
        assert_eq!(duplicate_start["status"], "awaiting_local_tools");
        assert_eq!(duplicate_start["modelRequests"], 1);
        assert_eq!(
            local_agent_step_states(&database_path, "host-verify-session", "host-verify-turn"),
            vec![(0, "awaiting_tools".to_string())]
        );

        turn = local_agent_request(
            process.port,
            &process.token,
            "POST",
            path,
            Some(&replay_body),
            Duration::from_secs(120),
        )
        .unwrap();
        validate_local_turn_response(&turn, "host-verify-session", "host-verify-turn").unwrap();
        assert_eq!(turn["status"], "completed");
        assert_eq!(turn["modelRequests"], 2);
        assert_eq!(turn["executionAuthorized"], false);
        assert_eq!(turn["filesChanged"], false);
        assert!(turn.to_string().contains("IMG_2048.jpg"));
        let replay = local_agent_request(
            process.port,
            &process.token,
            "POST",
            path,
            Some(&replay_body),
            Duration::from_secs(10),
        )
        .unwrap();
        assert_eq!(replay["duplicate"], true);
        assert_eq!(
            local_agent_step_states(&database_path, "host-verify-session", "host-verify-turn"),
            vec![
                (0, "awaiting_tools".to_string()),
                (1, "completed".to_string())
            ]
        );

        let mut uncertain_request = request.clone();
        uncertain_request["turnId"] = serde_json::json!("host-verify-uncertain");
        let uncertain_turn = local_agent_request(
            process.port,
            &process.token,
            "POST",
            "/v1/agent/local-turns",
            Some(&uncertain_request),
            Duration::from_secs(120),
        )
        .unwrap();
        assert_eq!(uncertain_turn["status"], "awaiting_local_tools");
        // Fault fixture: model the durable ledger state left when a provider call's
        // outcome cannot be established. This does not simulate the provider failure
        // transition; it verifies host/sidecar restart behavior for a persisted uncertain row.
        let connection = rusqlite::Connection::open(&database_path).unwrap();
        connection
            .execute(
                "UPDATE local_agent_steps SET state='uncertain', output_nonce=NULL, response_json=NULL \
                 WHERE session_id=?1 AND turn_id=?2 AND step_index=0",
                ["host-verify-session", "host-verify-uncertain"],
            )
            .unwrap();
        drop(connection);
        assert_eq!(
            local_agent_step_states(
                &database_path,
                "host-verify-session",
                "host-verify-uncertain"
            ),
            vec![(0, "uncertain".to_string())]
        );

        process.child.kill().unwrap();
        let _ = process.child.wait();
        let mut process = spawn_local_agent(&executable, &database_path, &signing_key).unwrap();
        let status = local_agent_request(
            process.port,
            &process.token,
            "GET",
            "/v1/agent/local-status",
            None,
            Duration::from_secs(4),
        )
        .unwrap();
        assert_eq!(status["cloudFallback"], false);
        assert_eq!(
            local_agent_step_states(
                &database_path,
                "host-verify-session",
                "host-verify-uncertain"
            ),
            vec![(0, "uncertain".to_string())],
            "starting the sidecar must not re-run an uncertain model step"
        );
        let uncertain_replay = local_agent_request(
            process.port,
            &process.token,
            "POST",
            "/v1/agent/local-turns",
            Some(&uncertain_request),
            Duration::from_secs(10),
        )
        .unwrap_err();
        assert_eq!(uncertain_replay.code, "LOCAL_AGENT_REQUEST_REJECTED");
        assert_eq!(
            local_agent_step_states(
                &database_path,
                "host-verify-session",
                "host-verify-uncertain"
            ),
            vec![(0, "uncertain".to_string())]
        );

        stop_local_agent_process(&mut process);
        assert!(process.child.try_wait().unwrap().is_some());
        std::fs::remove_dir_all(root).unwrap();
    }
}
