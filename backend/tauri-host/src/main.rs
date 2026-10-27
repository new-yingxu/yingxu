use std::{
    collections::HashSet,
    path::PathBuf,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, Ordering},
    },
    time::Duration,
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
use zeroize::Zeroizing;

mod cloud_api;
mod cloud_auth;

fn exchange_cloudbase_session(
    client: &cloud_auth::CloudBaseAuthClient,
    display_name: &str,
) -> Result<cloud_api::CloudSessionInfo, String> {
    let token = client
        .access_token_for_native_exchange()
        .map_err(|error| error.to_string())?
        .ok_or_else(|| cloud_auth::AuthError::AuthenticationRejected.to_string())?;
    let api =
        cloud_api::CloudApiClient::from_embedded_config().map_err(|error| error.to_string())?;
    api.exchange(&token, display_name)
        .map_err(|error| error.to_string())
}

#[tauri::command]
async fn cloudbase_password_sign_in(
    window: tauri::WebviewWindow,
    username: String,
    password: String,
) -> Result<cloud_api::CloudSessionInfo, String> {
    let password = Zeroizing::new(password);
    if window.label() != "main" {
        return Err("PERMISSION_DENIED".into());
    }
    tauri::async_runtime::spawn_blocking(move || {
        let client = cloud_auth::CloudBaseAuthClient::from_embedded_config()
            .map_err(|error| error.to_string())?;
        client
            .password_sign_in(username.trim(), &password)
            .map_err(|error| error.to_string())?;
        exchange_cloudbase_session(&client, username.trim())
    })
    .await
    .map_err(|_| cloud_auth::AuthError::ServiceUnavailable.to_string())?
}

#[tauri::command]
async fn cloudbase_request_email_code(
    window: tauri::WebviewWindow,
    email: String,
    for_signup: bool,
) -> Result<cloud_auth::EmailCodeChallenge, String> {
    if window.label() != "main" {
        return Err("PERMISSION_DENIED".into());
    }
    tauri::async_runtime::spawn_blocking(move || {
        let client = cloud_auth::CloudBaseAuthClient::from_embedded_config()
            .map_err(|error| error.to_string())?;
        client
            .request_email_code(email.trim(), for_signup)
            .map_err(|error| error.to_string())
    })
    .await
    .map_err(|_| cloud_auth::AuthError::ServiceUnavailable.to_string())?
}

#[tauri::command]
async fn cloudbase_email_code_sign_in(
    window: tauri::WebviewWindow,
    email: String,
    verification_id: String,
    verification_code: String,
) -> Result<cloud_api::CloudSessionInfo, String> {
    let verification_code = Zeroizing::new(verification_code);
    if window.label() != "main" {
        return Err("PERMISSION_DENIED".into());
    }
    tauri::async_runtime::spawn_blocking(move || {
        let client = cloud_auth::CloudBaseAuthClient::from_embedded_config()
            .map_err(|error| error.to_string())?;
        client
            .email_code_sign_in(email.trim(), verification_id.trim(), &verification_code)
            .map_err(|error| error.to_string())?;
        exchange_cloudbase_session(&client, email.trim())
    })
    .await
    .map_err(|_| cloud_auth::AuthError::ServiceUnavailable.to_string())?
}

#[tauri::command]
async fn cloudbase_email_code_sign_up(
    window: tauri::WebviewWindow,
    email: String,
    password: String,
    verification_id: String,
    verification_code: String,
) -> Result<cloud_api::CloudSessionInfo, String> {
    let password = Zeroizing::new(password);
    let verification_code = Zeroizing::new(verification_code);
    if window.label() != "main" {
        return Err("PERMISSION_DENIED".into());
    }
    tauri::async_runtime::spawn_blocking(move || {
        let client = cloud_auth::CloudBaseAuthClient::from_embedded_config()
            .map_err(|error| error.to_string())?;
        client
            .email_code_sign_up(
                email.trim(),
                &password,
                verification_id.trim(),
                &verification_code,
            )
            .map_err(|error| error.to_string())?;
        exchange_cloudbase_session(&client, email.trim())
    })
    .await
    .map_err(|_| cloud_auth::AuthError::ServiceUnavailable.to_string())?
}

#[tauri::command]
async fn cloudbase_sign_out(window: tauri::WebviewWindow) -> Result<(), String> {
    if window.label() != "main" {
        return Err("PERMISSION_DENIED".into());
    }
    tauri::async_runtime::spawn_blocking(|| {
        match cloud_api::CloudApiClient::from_embedded_config() {
            Ok(api) => api.logout().map_err(|error| error.to_string())?,
            Err(cloud_api::CloudApiError::NotConfigured) => {
                cloud_api::CloudSessionStore::clear(&cloud_api::SystemCloudSessionStore)
                    .map_err(|error| error.to_string())?
            }
            Err(error) => return Err(error.to_string()),
        }
        let client = cloud_auth::CloudBaseAuthClient::from_embedded_config()
            .map_err(|error| error.to_string())?;
        client.sign_out().map_err(|error| error.to_string())
    })
    .await
    .map_err(|_| cloud_auth::AuthError::ServiceUnavailable.to_string())?
}

#[tauri::command]
async fn cloud_session_current(
    window: tauri::WebviewWindow,
) -> Result<Option<cloud_api::CloudSessionInfo>, String> {
    if window.label() != "main" {
        return Err("PERMISSION_DENIED".into());
    }
    tauri::async_runtime::spawn_blocking(|| {
        match cloud_api::CloudApiClient::from_embedded_config() {
            Ok(api) => api.current_session().map_err(|error| error.to_string()),
            Err(cloud_api::CloudApiError::NotConfigured) => Ok(None),
            Err(error) => Err(error.to_string()),
        }
    })
    .await
    .map_err(|_| cloud_api::CloudApiError::Unavailable.to_string())?
}

#[tauri::command]
async fn cloud_api_request(
    window: tauri::WebviewWindow,
    method: String,
    path: String,
    body: Option<serde_json::Value>,
) -> Result<serde_json::Value, String> {
    if window.label() != "main" {
        return Err("PERMISSION_DENIED".into());
    }
    tauri::async_runtime::spawn_blocking(move || {
        let api =
            cloud_api::CloudApiClient::from_embedded_config().map_err(|error| error.to_string())?;
        api.request(&method, &path, body.as_ref())
            .map_err(|error| error.to_string())
    })
    .await
    .map_err(|_| cloud_api::CloudApiError::Unavailable.to_string())?
}

#[tauri::command]
async fn cloud_sync_backup_records(
    window: tauri::WebviewWindow,
    app: tauri::AppHandle,
) -> Result<serde_json::Value, String> {
    if window.label() != "main" {
        return Err("PERMISSION_DENIED".into());
    }
    let engine = app.state::<HostState>().engine.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let request = |method: &str, params: serde_json::Value| Request {
            request_id: format!("cloud-sync-{method}"), version: PROTOCOL_VERSION, method: method.into(), params,
        };
        let outbox = engine.dispatch(request("sync.outbox", serde_json::json!({"afterSequence":0,"limit":100})));
        if !outbox.ok { return Err(outbox.error.map(|e| e.to_string()).unwrap_or_else(|| "本地同步队列读取失败。".into())); }
        let events = outbox.data.unwrap_or_else(|| serde_json::Value::Array(vec![]));
        let Some(mut list) = events.as_array().cloned() else { return Err("本地同步队列格式无效。".into()); };
        if list.is_empty() { return Ok(serde_json::json!({"sent":0,"acknowledged":0})); }
        let through_sequence = list.iter().filter_map(|event| event.get("sequence").and_then(|v| v.as_i64())).max()
            .ok_or_else(|| "同步队列序号无效。".to_owned())?;
        let api = cloud_api::CloudApiClient::from_embedded_config().map_err(|error| error.to_string())?;
        let session = api.current_session().map_err(|error| error.to_string())?.ok_or_else(|| cloud_api::CloudApiError::Unauthenticated.to_string())?;
        for event in &mut list {
            let Some(object) = event.as_object_mut() else { return Err("本地同步事件格式无效。".into()); };
            object.insert("deviceId".into(), serde_json::Value::String(session.device_id.clone()));
        }
        api.request("POST", "/v1/sync/events", Some(&serde_json::json!({"events":list}))).map_err(|error| error.to_string())?;
        let ack = engine.dispatch(request("sync.ack", serde_json::json!({"throughSequence":through_sequence})));
        if !ack.ok { return Err(ack.error.map(|e| e.to_string()).unwrap_or_else(|| "云端已接收，但本地队列确认失败；下次会安全重试。".into())); }
        Ok(serde_json::json!({"sent":list.len(),"acknowledged":ack.data.and_then(|v| v.get("acknowledged").and_then(|n| n.as_u64())).unwrap_or(0)}))
    }).await.map_err(|_| cloud_api::CloudApiError::Unavailable.to_string())?
}

struct HostState {
    engine: Engine,
    shutdown: Arc<AtomicBool>,
    worker_ready: Arc<AtomicBool>,
    worker: Mutex<Option<std::thread::JoinHandle<()>>>,
    sources: Mutex<HashSet<PathBuf>>,
    targets: Mutex<HashSet<PathBuf>>,
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

fn main() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            select_directory,
            backend_request,
            cloudbase_password_sign_in,
            cloudbase_request_email_code,
            cloudbase_email_code_sign_in,
            cloudbase_email_code_sign_up,
            cloudbase_sign_out,
            cloud_session_current,
            cloud_api_request,
            cloud_sync_backup_records
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
            let root = app.path().app_data_dir()?;
            let engine = Engine::open(root)?;
            let worker = engine.worker()?;
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
