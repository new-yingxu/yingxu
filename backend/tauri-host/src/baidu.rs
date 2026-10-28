//! Native OAuth, per-account keyring, and verified-replica upload dispatch.
//! Never return provider tokens, callback codes or app secrets to the WebView.
use crate::cloud_api::CloudApiClient;
use reqwest::Url;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
    io::{Read, Write},
    net::TcpListener,
    path::PathBuf,
    sync::{Arc, Mutex, OnceLock},
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};
use yingxu_core::{
    Engine,
    baidu::{BaiduHttps, BaiduTransport},
};
use zeroize::{Zeroize, ZeroizeOnDrop, Zeroizing};

const CALLBACK: &str = "http://127.0.0.1:8765/callback";
const KEYRING: &str = "app.yingxu.desktop.baidu.v1";
static PENDING: OnceLock<Mutex<Option<Pending>>> = OnceLock::new();
static TOKEN_LOCK: Mutex<()> = Mutex::new(());

struct Pending {
    owner: String,
    folder: String,
    expires: Instant,
    result: Arc<Mutex<Option<Result<String, String>>>>,
}

#[derive(Serialize, Deserialize, Zeroize, ZeroizeOnDrop)]
struct Credentials {
    access_token: String,
    refresh_token: String,
    expires_at: u64,
    app_folder: String,
    refresh_uncertain: bool,
}

fn clock() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}
fn entry(owner: &str) -> Result<keyring::Entry, String> {
    keyring::Entry::new(KEYRING, owner).map_err(|_| "系统凭据库不可用。".into())
}
fn read(owner: &str) -> Result<Option<Credentials>, String> {
    match entry(owner)?.get_password() {
        Ok(raw) => {
            let raw = Zeroizing::new(raw);
            serde_json::from_str(&raw)
                .map(Some)
                .map_err(|_| "百度网盘凭据无效，请重新连接。".into())
        }
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(_) => Err("无法读取百度网盘系统凭据。".into()),
    }
}
fn save(owner: &str, credentials: &Credentials) -> Result<(), String> {
    let raw =
        Zeroizing::new(serde_json::to_string(credentials).map_err(|_| "无法保存百度网盘凭据。")?);
    entry(owner)?
        .set_password(&raw)
        .map_err(|_| "系统凭据库写入失败，请重新连接。".into())
}
fn token_pair(value: Value, folder: String) -> Result<Credentials, String> {
    let get = |name: &str| {
        value[name]
            .as_str()
            .filter(|s| !s.is_empty() && s.len() <= 16384 && !s.chars().any(char::is_whitespace))
            .map(str::to_owned)
            .ok_or_else(|| "百度授权返回无效凭据。".to_string())
    };
    let scopes = value["scope"].as_str().unwrap_or("");
    if !scopes
        .split(|c: char| c.is_whitespace() || c == ',')
        .any(|s| s == "netdisk")
    {
        return Err("百度授权未包含网盘权限。".into());
    }
    let expiry = value["expires_in"]
        .as_u64()
        .filter(|v| *v > 0 && *v <= 366 * 86400)
        .ok_or("百度授权有效期无效。")?;
    Ok(Credentials {
        access_token: get("access_token")?,
        refresh_token: get("refresh_token")?,
        expires_at: clock() + expiry,
        app_folder: folder,
        refresh_uncertain: false,
    })
}

fn callback_code(target: &str, expected: &str) -> Option<Result<String, String>> {
    if !target.starts_with("/callback?") {
        return None;
    }
    let url = Url::parse(&format!("http://127.0.0.1:8765{target}")).ok()?;
    if url.path() != "/callback" {
        return None;
    }
    let pairs = url.query_pairs().collect::<Vec<_>>();
    let values = |key: &str| {
        pairs
            .iter()
            .filter(|(k, _)| k == key)
            .map(|(_, v)| v.as_ref())
            .collect::<Vec<_>>()
    };
    if values("state") != [expected] {
        return None;
    }
    if !values("error").is_empty() {
        return Some(Err("百度授权已取消或拒绝。".into()));
    }
    let code = values("code");
    if code.len() != 1
        || code[0].is_empty()
        || code[0].len() > 4096
        || code[0].chars().any(char::is_whitespace)
    {
        return None;
    }
    Some(Ok(code[0].into()))
}

fn listen(
    listener: TcpListener,
    state: String,
    result: Arc<Mutex<Option<Result<String, String>>>>,
) {
    let start = Instant::now();
    let _ = listener.set_nonblocking(true);
    while start.elapsed() < Duration::from_secs(180) {
        if let Ok((mut stream, _)) = listener.accept() {
            let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
            let mut buf = [0; 8192];
            let n = stream.read(&mut buf).unwrap_or(0);
            let raw = String::from_utf8_lossy(&buf[..n]);
            let request = raw
                .lines()
                .next()
                .unwrap_or("")
                .split_whitespace()
                .collect::<Vec<_>>();
            let outcome = if request.len() == 3 && request[0] == "GET" {
                callback_code(request[1], &state)
            } else {
                None
            };
            let body = if outcome.is_some() {
                "收到授权响应，请返回影序查看连接结果。"
            } else {
                "无效的授权回调。"
            };
            let status = if outcome.is_some() {
                "200 OK"
            } else {
                "400 Bad Request"
            };
            let reply = format!(
                "HTTP/1.1 {status}\r\nContent-Type: text/plain; charset=utf-8\r\nCache-Control: no-store\r\nContent-Security-Policy: default-src 'none'\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                body.len()
            );
            let _ = stream.write_all(reply.as_bytes());
            if let Some(value) = outcome {
                if let Ok(mut result) = result.lock() {
                    *result = Some(value);
                }
                return;
            }
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    if let Ok(mut result) = result.lock() {
        *result = Some(Err("百度授权等待超时，请重新连接。".into()));
    }
}

fn open_browser(url: &Url) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    let result = std::process::Command::new("open")
        .arg(url.as_str())
        .status();
    #[cfg(target_os = "windows")]
    let result = std::process::Command::new("rundll32")
        .args(["url.dll,FileProtocolHandler", url.as_str()])
        .status();
    #[cfg(target_os = "linux")]
    let result = std::process::Command::new("xdg-open")
        .arg(url.as_str())
        .status();
    result
        .map_err(|_| "无法打开系统浏览器。")?
        .success()
        .then_some(())
        .ok_or("无法打开系统浏览器。".into())
}

fn active_credentials(owner: &str, api: &CloudApiClient) -> Result<Credentials, String> {
    let _lock = TOKEN_LOCK.lock().map_err(|_| "百度凭据状态不可用。")?;
    let credentials = read(owner)?.ok_or("请先连接百度网盘。")?;
    refresh_if_needed(
        credentials,
        |credentials| save(owner, credentials),
        |refresh_token| {
            api.baidu_broker(
                "token",
                Some(&json!({"grant_type":"refresh_token","credential":refresh_token})),
            )
            .map_err(|_| "百度授权刷新未完成，请重新连接；不会自动重复刷新。".to_string())
        },
    )
}

fn refresh_if_needed(
    mut credentials: Credentials,
    persist: impl Fn(&Credentials) -> Result<(), String>,
    exchange: impl Fn(&str) -> Result<Value, String>,
) -> Result<Credentials, String> {
    if credentials.refresh_uncertain {
        return Err("上次令牌刷新结果不明确，请重新连接百度网盘。".into());
    }
    if credentials.expires_at <= clock() + 60 {
        // Refresh tokens may rotate. Persist an uncertainty fence before the
        // external request; a crash/timeout cannot cause an automatic replay.
        credentials.refresh_uncertain = true;
        persist(&credentials)?;
        let value = exchange(&credentials.refresh_token)?;
        credentials = token_pair(value, credentials.app_folder.clone())?;
        persist(&credentials)?;
    }
    Ok(credentials)
}

fn allowed(path: &std::path::Path, grants: &[PathBuf]) -> bool {
    path.canonicalize()
        .is_ok_and(|path| grants.iter().any(|root| path.starts_with(root)))
}

pub fn handle(
    engine: &Engine,
    grants: &[PathBuf],
    action: &str,
    replica_id: Option<&str>,
    task_id: Option<&str>,
    confirmed: bool,
) -> Result<Value, String> {
    let api = CloudApiClient::from_embedded_config().map_err(|e| e.to_string())?;
    let session = api
        .current_session()
        .map_err(|e| e.to_string())?
        .ok_or("请先登录影序账号，再连接个人百度网盘。")?;
    let owner = &session.owner_id;
    match action {
        "status" => {
            let credentials = read(owner)?;
            Ok(
                json!({"authorized":credentials.as_ref().is_some_and(|c|!c.refresh_uncertain),
            "expiresAt":credentials.as_ref().map(|c|c.expires_at),"appFolder":credentials.as_ref().map(|c|c.app_folder.clone()),"tokenStorage":"system_keyring"}),
            )
        }
        "beginAuth" => {
            let mut pending = PENDING
                .get_or_init(|| Mutex::new(None))
                .lock()
                .map_err(|_| "授权状态不可用。")?;
            if pending.as_ref().is_some_and(|p| p.expires > Instant::now()) {
                return Err("已有百度授权正在进行，请先完成或等待超时。".into());
            }
            let config = api
                .baidu_broker("config", None)
                .map_err(|_| "百度授权服务尚未配置或部署；需要先配置 CloudBase 服务端。")?;
            if config["redirectUri"] != CALLBACK {
                return Err("百度回调地址与桌面端不一致。".into());
            }
            let folder = config["appFolder"]
                .as_str()
                .ok_or("百度应用目录未配置。")?
                .to_owned();
            let client_id = config["clientId"]
                .as_str()
                .filter(|s| !s.is_empty())
                .ok_or("百度 AppKey 未配置。")?;
            let listener = TcpListener::bind(("127.0.0.1", 8765))
                .map_err(|_| "本机授权端口 8765 被占用，请关闭占用它的程序后重试。")?;
            let state = format!("{}{}", yingxu_core::model::id(), yingxu_core::model::id());
            let mut url = Url::parse("https://openapi.baidu.com/oauth/2.0/authorize").unwrap();
            url.query_pairs_mut()
                .append_pair("response_type", "code")
                .append_pair("client_id", client_id)
                .append_pair("redirect_uri", CALLBACK)
                .append_pair("scope", "basic,netdisk")
                .append_pair("state", &state)
                .append_pair("display", "page");
            open_browser(&url)?;
            let result = Arc::new(Mutex::new(None));
            let thread_result = result.clone();
            std::thread::spawn(move || listen(listener, state, thread_result));
            *pending = Some(Pending {
                owner: owner.clone(),
                folder,
                expires: Instant::now() + Duration::from_secs(180),
                result,
            });
            Ok(json!({"state":"awaiting_browser","expiresInSeconds":180}))
        }
        "pollAuth" => {
            let mut guard = PENDING
                .get_or_init(|| Mutex::new(None))
                .lock()
                .map_err(|_| "授权状态不可用。")?;
            let pending = guard.as_ref().ok_or("没有等待中的百度授权，请重新连接。")?;
            if pending.owner != *owner {
                return Err("授权所属影序账号已变更，请重新连接。".into());
            }
            let outcome = pending
                .result
                .lock()
                .map_err(|_| "授权结果不可用。")?
                .take();
            let Some(outcome) = outcome else {
                return Ok(json!({"state":"awaiting_browser"}));
            };
            let _lock = TOKEN_LOCK.lock().map_err(|_| "凭据状态不可用。")?;
            let folder = pending.folder.clone();
            *guard = None;
            drop(guard);
            // Consume pending state before exchanging: never replay this code.
            let code = Zeroizing::new(outcome?);
            let value = api
                .baidu_broker(
                    "token",
                    Some(&json!({"grant_type":"authorization_code","credential":code.as_str()})),
                )
                .map_err(|_| "百度授权换取令牌未完成，请重新连接；不会重复使用本轮授权码。")?;
            let credentials = token_pair(value, folder)?;
            save(owner, &credentials)?;
            Ok(
                json!({"state":"authorized","authorized":true,"appFolder":credentials.app_folder,"tokenStorage":"system_keyring"}),
            )
        }
        "disconnect" => {
            if !confirmed {
                return Err("请先确认断开百度网盘连接。".into());
            }
            let mut pending = PENDING
                .get_or_init(|| Mutex::new(None))
                .lock()
                .map_err(|_| "授权状态不可用。")?;
            if pending.as_ref().is_some_and(|p| p.owner == *owner) {
                *pending = None;
            }
            let _lock = TOKEN_LOCK.lock().map_err(|_| "凭据状态不可用。")?;
            match entry(owner)?.delete_credential() {
                Ok(()) | Err(keyring::Error::NoEntry) => {}
                Err(_) => return Err("系统凭据删除失败。".into()),
            }
            Ok(json!({"authorized":false,"remoteFilesDeleted":false,"providerGrantRevoked":false}))
        }
        "replicas" => Ok(json!(
            engine
                .replicas()
                .map_err(|e| e.message)?
                .into_iter()
                .filter(|r| r["verified"] == true
                    && r["path"]
                        .as_str()
                        .is_some_and(|p| allowed(std::path::Path::new(p), grants)))
                .collect::<Vec<_>>()
        )),
        "tasks" => engine.baidu_tasks(owner).map_err(|e| e.message),
        "prepare" | "enqueue" => {
            if action == "enqueue" && !confirmed {
                return Err("上传前请确认本地备份副本和网盘目标。".into());
            }
            let replica_id = replica_id.ok_or("缺少备份副本。")?;
            let replicas = engine.replicas().map_err(|e| e.message)?;
            let replica = replicas
                .iter()
                .find(|r| r["id"] == replica_id)
                .ok_or("找不到备份副本。")?;
            if !replica["path"]
                .as_str()
                .is_some_and(|p| allowed(std::path::Path::new(p), grants))
            {
                return Err("请先通过原生选择器授权备份目录。".into());
            }
            let credentials = read(owner)?.ok_or("请先连接百度网盘。")?;
            engine
                .baidu_enqueue(
                    owner,
                    replica_id,
                    task_id.ok_or("缺少任务编号。")?,
                    &credentials.app_folder,
                )
                .map_err(|e| e.message)
        }
        "run" => {
            if !confirmed {
                return Err("请先确认上传或重试此任务。".into());
            }
            let task_id = task_id.ok_or("缺少任务编号。")?;
            let tasks = engine.baidu_tasks(owner).map_err(|e| e.message)?;
            let task = tasks
                .as_array()
                .and_then(|a| a.iter().find(|t| t["taskId"] == task_id))
                .ok_or("找不到上传任务。")?;
            if !task["localPath"]
                .as_str()
                .is_some_and(|p| allowed(std::path::Path::new(p), grants))
            {
                return Err("请先通过原生选择器重新授权备份目录。".into());
            }
            let credentials = active_credentials(owner, &api)?;
            let client = BaiduHttps::new().map_err(|e| e.message)?;
            engine
                .baidu_run(owner, task_id, &credentials.access_token, &client)
                .map_err(|e| e.message)
        }
        "cancel" => engine
            .baidu_cancel(owner, task_id.ok_or("缺少任务编号。")?)
            .map_err(|e| e.message),
        "verifyAccount" => {
            let credentials = active_credentials(owner, &api)?;
            let value = BaiduHttps::new()
                .map_err(|e| e.message)?
                .call("userinfo", &credentials.access_token, &[], None)
                .map_err(|e| e.message)?;
            Ok(
                json!({"baiduName":value["baidu_name"],"netdiskName":value["netdisk_name"],"authorized":true}),
            )
        }
        _ => Err("不支持的百度网盘操作。".into()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn callback_rejects_wrong_path_state_and_duplicates_decodes_code() {
        assert!(callback_code("/favicon.ico?state=ok&code=x", "ok").is_none());
        assert!(callback_code("/callback?state=wrong&code=x", "ok").is_none());
        assert!(callback_code("/callback?state=ok&state=wrong&code=x", "ok").is_none());
        assert!(callback_code("/callback?state=ok&code=x&code=y", "ok").is_none());
        assert_eq!(
            callback_code("/callback?state=ok&code=a%2Bb%3D", "ok")
                .unwrap()
                .unwrap(),
            "a+b="
        );
        assert!(
            callback_code("/callback?state=ok&error=access_denied", "ok")
                .unwrap()
                .is_err()
        );
    }
    #[test]
    fn token_pair_requires_netdisk_and_refresh_replacement() {
        assert!(
            token_pair(
                json!({"access_token":"x","refresh_token":"y","scope":"basic","expires_in":60}),
                "/apps/test".into()
            )
            .is_err()
        );
        let value=token_pair(json!({"access_token":"x","refresh_token":"new-refresh","scope":"basic,netdisk","expires_in":60}),"/apps/test".into()).unwrap();
        assert_eq!(value.refresh_token, "new-refresh");
        assert!(!value.refresh_uncertain);
    }
    #[test]
    fn refresh_fence_is_persisted_before_io_and_never_replayed() {
        use std::cell::{Cell, RefCell};
        let saved = RefCell::new(String::new());
        let calls = Cell::new(0);
        let credentials = Credentials {
            access_token: "expired".into(),
            refresh_token: "old-refresh".into(),
            expires_at: 0,
            app_folder: "/apps/test".into(),
            refresh_uncertain: false,
        };
        let result = refresh_if_needed(
            credentials,
            |c| {
                *saved.borrow_mut() = serde_json::to_string(c).unwrap();
                Ok(())
            },
            |_| {
                calls.set(calls.get() + 1);
                assert!(saved.borrow().contains("\"refresh_uncertain\":true"));
                Err("synthetic-timeout".into())
            },
        );
        assert!(result.is_err());
        let persisted: Credentials = serde_json::from_str(&saved.borrow()).unwrap();
        assert!(
            refresh_if_needed(
                persisted,
                |_| panic!("must not persist"),
                |_| panic!("must not repeat")
            )
            .is_err()
        );
        assert_eq!(calls.get(), 1);
    }
    #[test]
    fn successful_refresh_saves_rotated_pair() {
        use std::cell::RefCell;
        let saved = RefCell::new(vec![]);
        let credentials = Credentials {
            access_token: "expired".into(),
            refresh_token: "old-refresh".into(),
            expires_at: 0,
            app_folder: "/apps/test".into(),
            refresh_uncertain: false,
        };
        let value=refresh_if_needed(credentials,|c|{saved.borrow_mut().push((c.refresh_uncertain,c.refresh_token.clone()));Ok(())},|token|{assert_eq!(token,"old-refresh");Ok(json!({"access_token":"new-access","refresh_token":"rotated-refresh","scope":"basic netdisk","expires_in":3600}))}).unwrap();
        assert_eq!(value.refresh_token, "rotated-refresh");
        assert_eq!(
            *saved.borrow(),
            [
                (true, "old-refresh".into()),
                (false, "rotated-refresh".into())
            ]
        );
    }
}
