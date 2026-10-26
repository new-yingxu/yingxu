#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{
    collections::{HashMap, HashSet},
    path::PathBuf,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, Ordering},
    },
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use tauri::{
    Emitter, Manager,
    menu::{Menu, MenuItem},
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
    pending_agent_actions: Mutex<HashMap<String, PendingOpenAction>>,
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
        Ok(serde_json::json!({"path":path,"purpose":purpose}))
    })
    .await
    .map_err(|e| CoreError::new("BACKGROUND_TASK_FAILED", e))?
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

/// Open one already-indexed item after a direct renderer user action. The
/// renderer can supply only a media ID; roots, paths and fingerprints are
/// revalidated by the local engine and host before the OS opener is called.
#[tauri::command]
async fn agent_open_item(
    window: tauri::WebviewWindow,
    app: tauri::AppHandle,
    media_id: String,
) -> Result<serde_json::Value, CoreError> {
    if window.label() != "main" {
        return Err(CoreError::new(
            "PERMISSION_DENIED",
            "仅主窗口可打开本地素材",
        ));
    }
    let state = app.state::<HostState>();
    let engine = state.engine.clone();
    let lookup_media_id = media_id.clone();
    let path = tauri::async_runtime::spawn_blocking(move || {
        engine.agent_resolve_open_item(&lookup_media_id)
    })
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
    let checked_path = state.engine.agent_resolve_open_item(&media_id)?;
    if checked_path != path {
        return Err(CoreError::new(
            "SOURCE_CHANGED",
            "素材身份在打开前发生变化，未打开",
        ));
    }
    open_in_default_app(&path)?;
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
        &media_id,
        serde_json::json!({"receiptId":receipt_id,"opened":true}),
    )?;
    Ok(serde_json::json!({"mediaId":media_id,"opened":true,"receiptId":receipt_id}))
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
            agent_open_item,
            agent_prepare_action,
            agent_confirm_action
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
            let engine = match Engine::open(root) {
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
            app.manage(HostState {
                engine,
                shutdown: shutdown_flag,
                worker_ready: ready,
                worker: Mutex::new(Some(thread)),
                sources: Mutex::new(HashSet::new()),
                targets: Mutex::new(HashSet::new()),
                pending_agent_actions: Mutex::new(HashMap::new()),
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
            TrayIconBuilder::new()
                .icon(tauri::image::Image::new_owned(pixels, 24, 24))
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
    use super::validate_action_expiry;

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
}
