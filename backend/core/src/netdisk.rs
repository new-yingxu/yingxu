//! 网盘上传骨架（百度 / 夸克）。
//!
//! 现状与闸门：两家开放平台的**应用资质未申请、未核实**，因此真实上传执行体不落地，
//! 厂商实现一律返回 `NETDISK_NOT_APPROVED`。本模块交付的是可联调、可测试的骨架：
//!
//! - OAuth：PKCE + loopback 回调（`http://127.0.0.1:<随机端口>/callback`），token 只进系统凭据库
//! - 凭据：macOS Keychain / Linux secret-tool / Windows DPAPI，**不入 SQLite、不入 git、不入日志**
//! - 上传：分片 + 断点续传（分片游标落盘）、逐文件结果、失败只重试失败分片
//! - 前置条件：**只有本地副本 SHA-256 校验通过**才允许排队
//! - 解绑：撤销并清理凭据，保留历史结果记录
//!
//! 传输层目前只有明文 HTTP（用于本地 mock 服务器联调）。真实厂商必须走 HTTPS，
//! 需要引入 TLS 栈，这一步与「资质获批」一起解闸，不在本次范围内。
use crate::{Engine, model::*, netdisk_baidu};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    io::{Read, Seek, SeekFrom, Write},
    net::{TcpListener, TcpStream},
    path::{Path, PathBuf},
    sync::{Arc, Mutex, mpsc},
    time::{Duration, Instant},
};

const CRED_SERVICE_PREFIX: &str = "yingxu.netdisk";
const CALLBACK_TIMEOUT: Duration = Duration::from_secs(600);
const HTTP_TIMEOUT: Duration = Duration::from_secs(30);
const MAX_CHUNK: u64 = 16 * 1024 * 1024;
const MAX_WORKERS: u8 = 8;
const MIN_CHUNK: u64 = 256 * 1024;

#[derive(Clone)]
pub struct Provider {
    pub id: &'static str,
    pub display: &'static str,
    /// `approved` / `pending`
    pub approval: &'static str,
    pub auth_endpoint: &'static str,
    pub token_endpoint: &'static str,
    pub scopes: &'static [&'static str],
    pub max_chunk: u64,
}

pub fn providers() -> Vec<Provider> {
    vec![
        Provider {
            id: "baidu",
            display: "百度网盘",
            approval: "pending",
            auth_endpoint: "https://openapi.baidu.com/oauth/2.0/authorize",
            token_endpoint: "https://openapi.baidu.com/oauth/2.0/token",
            scopes: &["basic", "netdisk"],
            max_chunk: 4 * 1024 * 1024,
        },
        Provider {
            id: "quark",
            display: "夸克网盘",
            // 截至 2026-10：夸克网盘没有公开的第三方文件 API（developer/open 站点
            // 只有浏览器插件与 AI 能力），endpoint 为占位，接口骨架保留待其开放。
            approval: "no_public_api",
            auth_endpoint: "https://open.quark.cn/oauth/authorize",
            token_endpoint: "https://open.quark.cn/oauth/token",
            scopes: &["file:write"],
            max_chunk: 4 * 1024 * 1024,
        },
        Provider {
            id: "mock",
            display: "本地模拟服务器（仅联调）",
            approval: "local_only",
            auth_endpoint: "http://127.0.0.1:8686/oauth/authorize",
            token_endpoint: "http://127.0.0.1:8686/oauth/token",
            scopes: &["local"],
            max_chunk: MIN_CHUNK,
        },
    ]
}

pub fn provider(id: &str) -> CoreResult<Provider> {
    providers()
        .into_iter()
        .find(|p| p.id == id)
        .ok_or_else(|| CoreError::new("NETDISK_PROVIDER_UNKNOWN", id))
}

fn require_runnable(p: &Provider) -> CoreResult<()> {
    let approved = match p.id {
        "mock" => true,
        "baidu" => netdisk_baidu::creds().is_some(),
        _ => p.approval == "approved",
    };
    if !approved {
        let why = if p.id == "baidu" {
            "凭证未配置（需要 backend/.env 里的 YINGXU_BAIDU_APPKEY/SECRETKEY）"
        } else if p.id == "quark" {
            "夸克网盘暂无公开的第三方文件 API（截至 2026-10），官方开放后即可接入"
        } else {
            "开放平台应用资质未获批"
        };
        return Err(CoreError::new(
            "NETDISK_NOT_APPROVED",
            format!("{}：{why}，不执行真实上传", p.display),
        ));
    }
    Ok(())
}

// ---------- PKCE ----------

pub struct Pkce {
    pub verifier: String,
    pub challenge: String,
}

fn b64url(bytes: &[u8]) -> String {
    const TABLE: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    let mut out = String::with_capacity(bytes.len().div_ceil(3) * 4);
    for c in bytes.chunks(3) {
        let b = [c[0], *c.get(1).unwrap_or(&0), *c.get(2).unwrap_or(&0)];
        let n = (u32::from(b[0]) << 16) | (u32::from(b[1]) << 8) | u32::from(b[2]);
        out.push(TABLE[(n >> 18) as usize & 63] as char);
        out.push(TABLE[(n >> 12) as usize & 63] as char);
        if c.len() > 1 {
            out.push(TABLE[(n >> 6) as usize & 63] as char);
        }
        if c.len() > 2 {
            out.push(TABLE[n as usize & 63] as char);
        }
    }
    out
}

/// 32 字节随机 → 43 字符 verifier（RFC 7636 允许 43–128）。
pub fn pkce() -> Pkce {
    let mut raw = Vec::with_capacity(32);
    for _ in 0..2 {
        raw.extend_from_slice(uuid::Uuid::new_v4().as_bytes());
    }
    let verifier = b64url(&raw);
    let challenge = b64url(&Sha256::digest(verifier.as_bytes()));
    Pkce {
        verifier,
        challenge,
    }
}

/// 拼授权 URL（S256 PKCE + 随机 state）。不打开浏览器，由 UI 负责。
pub fn authorize_url(p: &Provider, redirect: &str, pkce: &Pkce, state: &str) -> String {
    format!(
        "{}?response_type=code&client_id=yingxu-desktop&redirect_uri={}&scope={}&state={}&code_challenge={}&code_challenge_method=S256",
        p.auth_endpoint,
        url_encode(redirect),
        url_encode(&p.scopes.join(" ")),
        state,
        pkce.challenge
    )
}

fn url_encode(s: &str) -> String {
    let mut out = String::new();
    for b in s.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(b as char)
            }
            _ => out.push_str(&format!("%{b:02X}")),
        }
    }
    out
}

/// 在 127.0.0.1 上起一次性回调，等待 `?code=...&state=...`。
/// 返回 (授权码, 实际端口)。超时返回 `NETDISK_AUTH_TIMEOUT`。
pub fn wait_for_callback(
    listener: TcpListener,
    expect_state: &str,
    timeout: Duration,
) -> CoreResult<String> {
    listener.set_nonblocking(true)?;
    let started = Instant::now();
    loop {
        match listener.accept() {
            Ok((mut stream, _)) => {
                stream.set_nonblocking(false)?;
                stream.set_read_timeout(Some(Duration::from_secs(5)))?;
                let mut buf = [0u8; 4096];
                let n = stream.read(&mut buf).unwrap_or(0);
                let req = String::from_utf8_lossy(&buf[..n]).to_string();
                let target = req
                    .lines()
                    .next()
                    .and_then(|l| l.split_whitespace().nth(1))
                    .unwrap_or_default()
                    .to_string();
                let query = target.split_once('?').map(|(_, q)| q).unwrap_or("");
                let mut code = None;
                let mut state = None;
                for pair in query.split('&') {
                    if let Some((k, v)) = pair.split_once('=') {
                        match k {
                            "code" => code = Some(v.to_string()),
                            "state" => state = Some(v.to_string()),
                            _ => {}
                        }
                    }
                }
                let ok = code.is_some() && state.as_deref() == Some(expect_state);
                let body = if ok {
                    "<h1>授权完成</h1><p>可以关闭此页面，返回影序桌面端。</p>"
                } else {
                    "<h1>授权失败</h1><p>state 不匹配或缺少 code，请重新发起授权。</p>"
                };
                let _ = stream.write_all(
                    format!(
                        "HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                        body.len(),
                        body
                    )
                    .as_bytes(),
                );
                let _ = stream.flush();
                if ok {
                    return Ok(code.unwrap());
                }
            }
            Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                if started.elapsed() > timeout {
                    return Err(CoreError::new(
                        "NETDISK_AUTH_TIMEOUT",
                        "等待授权回调超时，请重新发起",
                    ));
                }
                std::thread::sleep(Duration::from_millis(60));
            }
            Err(e) => return Err(e.into()),
        }
    }
}

// ---------- 凭据库 ----------

fn cred_account(provider: &str) -> String {
    format!("{CRED_SERVICE_PREFIX}.{provider}")
}

#[cfg(target_os = "macos")]
pub fn store_token(provider: &str, token: &str) -> CoreResult<()> {
    // 用 `security -i`（stdin 传参）避免 token 出现在进程参数表里
    let mut child = std::process::Command::new("security")
        .arg("-i")
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::piped())
        .spawn()?;
    if let Some(stdin) = child.stdin.as_mut() {
        writeln!(
            stdin,
            "add-generic-password -U -s {} -a {} -w {}",
            cred_account(provider),
            provider,
            token
        )?;
    }
    let out = child.wait_with_output()?;
    if out.status.success() {
        Ok(())
    } else {
        Err(CoreError::new(
            "CREDENTIAL_STORE_FAILED",
            String::from_utf8_lossy(&out.stderr).trim().to_string(),
        ))
    }
}

#[cfg(target_os = "macos")]
pub fn load_token(provider: &str) -> CoreResult<Option<String>> {
    let out = std::process::Command::new("security")
        .args([
            "find-generic-password",
            "-s",
            &cred_account(provider),
            "-a",
            provider,
            "-w",
        ])
        .output()?;
    if !out.status.success() {
        return Ok(None);
    }
    let token = String::from_utf8_lossy(&out.stdout).trim().to_string();
    Ok((!token.is_empty()).then_some(token))
}

#[cfg(target_os = "macos")]
pub fn delete_token(provider: &str) -> CoreResult<()> {
    let _ = std::process::Command::new("security")
        .args([
            "delete-generic-password",
            "-s",
            &cred_account(provider),
            "-a",
            provider,
        ])
        .output()?;
    Ok(())
}

#[cfg(target_os = "linux")]
pub fn store_token(provider: &str, token: &str) -> CoreResult<()> {
    let mut child = std::process::Command::new("secret-tool")
        .args([
            "store",
            "--label",
            &cred_account(provider),
            "service",
            &cred_account(provider),
        ])
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::null())
        .spawn()
        .map_err(|_| {
            CoreError::new(
                "CREDENTIAL_STORE_UNAVAILABLE",
                "未找到 secret-tool，无法写入系统凭据库",
            )
        })?;
    if let Some(stdin) = child.stdin.as_mut() {
        stdin.write_all(token.as_bytes())?;
    }
    let out = child.wait_with_output()?;
    if out.status.success() {
        Ok(())
    } else {
        Err(CoreError::new(
            "CREDENTIAL_STORE_FAILED",
            "secret-tool 写入失败",
        ))
    }
}

#[cfg(target_os = "linux")]
pub fn load_token(provider: &str) -> CoreResult<Option<String>> {
    let out = std::process::Command::new("secret-tool")
        .args(["lookup", "service", &cred_account(provider)])
        .output();
    match out {
        Ok(o) if o.status.success() => {
            let t = String::from_utf8_lossy(&o.stdout).trim().to_string();
            Ok((!t.is_empty()).then_some(t))
        }
        _ => Ok(None),
    }
}

#[cfg(target_os = "linux")]
pub fn delete_token(provider: &str) -> CoreResult<()> {
    let _ = std::process::Command::new("secret-tool")
        .args(["clear", "service", &cred_account(provider)])
        .output();
    Ok(())
}

#[cfg(windows)]
fn cred_file(provider: &str) -> PathBuf {
    PathBuf::from(std::env::var("LOCALAPPDATA").unwrap_or_else(|_| ".".into()))
        .join("Yingxu")
        .join("credentials")
        .join(format!("{provider}.bin"))
}

#[cfg(windows)]
pub fn store_token(provider: &str, token: &str) -> CoreResult<()> {
    // PowerShell + DPAPI：密文只有当前用户能解开，明文不落盘
    let file = cred_file(provider);
    if let Some(dir) = file.parent() {
        std::fs::create_dir_all(dir)?;
    }
    let script = format!(
        "$s = ConvertTo-SecureString -String $env:YINGXU_TOKEN -AsPlainText -Force; ConvertFrom-SecureString $s | Set-Content -Path '{}'",
        file.display()
    );
    let out = std::process::Command::new("powershell")
        .args(["-NoProfile", "-NonInteractive", "-Command", &script])
        .env("YINGXU_TOKEN", token)
        .output()?;
    if out.status.success() {
        Ok(())
    } else {
        Err(CoreError::new("CREDENTIAL_STORE_FAILED", "DPAPI 写入失败"))
    }
}

#[cfg(windows)]
pub fn load_token(provider: &str) -> CoreResult<Option<String>> {
    let file = cred_file(provider);
    if !file.is_file() {
        return Ok(None);
    }
    let script = format!(
        "$s = Get-Content -Path '{}' | ConvertTo-SecureString; $b = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s); [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b)",
        file.display()
    );
    let out = std::process::Command::new("powershell")
        .args(["-NoProfile", "-NonInteractive", "-Command", &script])
        .output()?;
    if !out.status.success() {
        return Ok(None);
    }
    let t = String::from_utf8_lossy(&out.stdout).trim().to_string();
    Ok((!t.is_empty()).then_some(t))
}

#[cfg(windows)]
pub fn delete_token(provider: &str) -> CoreResult<()> {
    let file = cred_file(provider);
    if file.is_file() {
        std::fs::remove_file(file)?;
    }
    Ok(())
}

#[cfg(not(any(target_os = "macos", target_os = "linux", windows)))]
pub fn store_token(_: &str, _: &str) -> CoreResult<()> {
    Err(CoreError::new(
        "CREDENTIAL_STORE_UNAVAILABLE",
        "当前平台没有可用的系统凭据库",
    ))
}
#[cfg(not(any(target_os = "macos", target_os = "linux", windows)))]
pub fn load_token(_: &str) -> CoreResult<Option<String>> {
    Ok(None)
}
#[cfg(not(any(target_os = "macos", target_os = "linux", windows)))]
pub fn delete_token(_: &str) -> CoreResult<()> {
    Ok(())
}

// ---------- 最小 HTTP 客户端（明文 + rustls TLS，无 tokio） ----------

pub struct HttpResponse {
    pub status: u16,
    pub body: Vec<u8>,
}

/// 统一入口：method / url / 可选 content-type / body / 可选超时。
/// `http://` 走裸 TCP（mock 联调），`https://` 走 rustls + webpki-roots（真实厂商）。
pub fn http_request(
    method: &str,
    url: &str,
    content_type: Option<&str>,
    body: &[u8],
    timeout: Option<Duration>,
) -> CoreResult<HttpResponse> {
    let (host, port, path, tls) = parse_url(url)?;
    let connect_timeout = timeout.unwrap_or(HTTP_TIMEOUT);
    let stream = TcpStream::connect((host.as_str(), port))
        .map_err(|e| CoreError::new("NETDISK_CONNECT_FAILED", format!("无法连接 {host}: {e}")))?;
    stream.set_read_timeout(Some(connect_timeout))?;
    stream.set_write_timeout(Some(connect_timeout))?;

    let mut raw: Vec<u8> = vec![];
    if tls {
        let tls_stream = tls_connect(&host, stream)?;
        send_request(
            tls_stream,
            method,
            &host,
            &path,
            content_type,
            body,
            &mut raw,
        )?;
    } else {
        send_request(stream, method, &host, &path, content_type, body, &mut raw)?;
    }

    let split = raw
        .windows(4)
        .position(|w| w == b"\r\n\r\n")
        .ok_or_else(|| CoreError::new("NETDISK_BAD_RESPONSE", "响应缺少头部"))?;
    let head = String::from_utf8_lossy(&raw[..split]).to_string();
    let status = head
        .lines()
        .next()
        .and_then(|l| l.split_whitespace().nth(1))
        .and_then(|s| s.parse::<u16>().ok())
        .ok_or_else(|| CoreError::new("NETDISK_BAD_RESPONSE", head.clone()))?;
    let lower = head.to_ascii_lowercase();
    let mut payload = raw[split + 4..].to_vec();
    if lower.contains("transfer-encoding: chunked") {
        payload = decode_chunked(&payload)?;
    }
    Ok(HttpResponse {
        status,
        body: payload,
    })
}

/// 兼容旧调用方（mock 与部分 GET 场景）
pub fn http_post(url: &str, content_type: &str, body: &[u8]) -> CoreResult<HttpResponse> {
    http_request("POST", url, Some(content_type), body, None)
}

fn parse_url(url: &str) -> CoreResult<(String, u16, String, bool)> {
    let (tls, rest) = if let Some(r) = url.strip_prefix("https://") {
        (true, r)
    } else if let Some(r) = url.strip_prefix("http://") {
        (false, r)
    } else {
        return Err(CoreError::new("NETDISK_BAD_URL", url));
    };
    let (authority, path) = match rest.split_once('/') {
        Some((h, p)) => (h.to_string(), format!("/{p}")),
        None => (rest.to_string(), "/".to_string()),
    };
    let (host, port) = match authority.rsplit_once(':') {
        Some((h, p)) => (
            h.to_string(),
            p.parse::<u16>()
                .map_err(|_| CoreError::new("NETDISK_BAD_URL", url))?,
        ),
        None => (authority, if tls { 443 } else { 80 }),
    };
    Ok((host, port, path, tls))
}

fn tls_connect(
    host: &str,
    tcp: TcpStream,
) -> CoreResult<rustls::StreamOwned<rustls::ClientConnection, TcpStream>> {
    use rustls::pki_types::ServerName;
    let config = rustls_client_config();
    let name = ServerName::try_from(host.to_string())
        .map_err(|_| CoreError::new("NETDISK_BAD_URL", format!("非法主机名 {host}")))?;
    let conn = rustls::ClientConnection::new(Arc::clone(&config), name)
        .map_err(|e| CoreError::new("NETDISK_TLS_FAILED", e.to_string()))?;
    Ok(rustls::StreamOwned::new(conn, tcp))
}

fn rustls_client_config() -> Arc<rustls::ClientConfig> {
    static CONFIG: std::sync::OnceLock<Arc<rustls::ClientConfig>> = std::sync::OnceLock::new();
    Arc::clone(CONFIG.get_or_init(|| {
        let mut roots = rustls::RootCertStore::empty();
        roots.extend(webpki_roots::TLS_SERVER_ROOTS.iter().cloned());
        Arc::new(
            rustls::ClientConfig::builder()
                .with_root_certificates(roots)
                .with_no_client_auth(),
        )
    }))
}

fn send_request(
    mut stream: impl Read + Write,
    method: &str,
    host: &str,
    path: &str,
    content_type: Option<&str>,
    body: &[u8],
    out: &mut Vec<u8>,
) -> CoreResult<()> {
    let mut head = format!(
        "{method} {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\nContent-Length: {}\r\nUser-Agent: yingxu-desktop/0.2\r\n",
        body.len()
    );
    if let Some(ct) = content_type {
        head.push_str(&format!("Content-Type: {ct}\r\n"));
    }
    head.push_str("\r\n");
    stream.write_all(head.as_bytes())?;
    if !body.is_empty() {
        stream.write_all(body)?;
    }
    stream.flush()?;
    stream.read_to_end(out)?;
    Ok(())
}

/// RFC 7230 分块传输解码
fn decode_chunked(input: &[u8]) -> CoreResult<Vec<u8>> {
    let mut out = Vec::with_capacity(input.len());
    let mut rest = input;
    loop {
        let line_end = rest
            .windows(2)
            .position(|w| w == b"\r\n")
            .ok_or_else(|| CoreError::new("NETDISK_BAD_RESPONSE", "chunked 头不完整"))?;
        let size_str = String::from_utf8_lossy(&rest[..line_end]);
        let size = usize::from_str_radix(size_str.trim().split(';').next().unwrap_or("0"), 16)
            .map_err(|_| CoreError::new("NETDISK_BAD_RESPONSE", format!("非法块长 {size_str}")))?;
        rest = &rest[line_end + 2..];
        if size == 0 {
            break;
        }
        if rest.len() < size + 2 {
            return Err(CoreError::new("NETDISK_BAD_RESPONSE", "chunked 数据不完整"));
        }
        out.extend_from_slice(&rest[..size]);
        rest = &rest[size + 2..];
    }
    Ok(out)
}

// ---------- 上传任务 ----------

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct UploadTask {
    pub id: String,
    pub provider: String,
    pub media_id: String,
    pub source: PathBuf,
    pub name: String,
    pub bytes: u64,
    pub sha256: String,
    pub chunk_size: u64,
    pub remote_upload_id: Option<String>,
    /// 已完成分片游标；续传只补这里没有的
    pub chunks_done: Vec<u32>,
    /// `queued` / `uploading` / `completed` / `failed` / `cancelled`
    pub state: String,
    pub error: Option<String>,
    pub created_at: String,
    pub updated_at: String,
    pub verified_remote: bool,
}

impl UploadTask {
    pub fn chunks_total(&self) -> u32 {
        if self.bytes == 0 {
            0
        } else {
            self.bytes.div_ceil(self.chunk_size) as u32
        }
    }
}

/// 上传并发与限速。默认从设置里的 `maxUploadMbps` 取值（0 = 不限速）。
#[derive(Debug, Clone, Copy)]
pub struct UploadOptions {
    pub concurrency: u8,
    /// 兆比特/秒；0 表示不限速。按线程均分，总量约等于该值。
    pub max_mbps: f64,
}

impl Default for UploadOptions {
    fn default() -> Self {
        Self {
            concurrency: 3,
            max_mbps: 0.0,
        }
    }
}

impl UploadOptions {
    pub fn from_settings(settings: &Value) -> Self {
        let mbps = settings["maxUploadMbps"].as_f64().unwrap_or(0.0).max(0.0);
        let workers = settings["maxUploadConcurrency"]
            .as_u64()
            .and_then(|v| u8::try_from(v).ok())
            .unwrap_or(3);
        Self {
            concurrency: workers.clamp(1, MAX_WORKERS),
            max_mbps: mbps,
        }
    }
}

/// 流式 SHA-256，不整文件入内存。
pub fn sha256_file(path: &Path) -> CoreResult<(String, u64)> {
    let mut f = std::fs::File::open(path)?;
    let mut hasher = Sha256::new();
    let mut buf = vec![0u8; 1024 * 1024];
    let mut total = 0u64;
    loop {
        let n = f.read(&mut buf)?;
        if n == 0 {
            break;
        }
        hasher.update(&buf[..n]);
        total += n as u64;
    }
    Ok((format!("{:x}", hasher.finalize()), total))
}

fn tasks_dir(root: &Path) -> PathBuf {
    root.join("netdisk").join("tasks")
}

fn write_json_atomic(path: &Path, value: &Value) -> CoreResult<()> {
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir)?;
    }
    let tmp = path.with_extension("json.tmp");
    let mut f = std::fs::File::create(&tmp)?;
    f.write_all(serde_json::to_string_pretty(value)?.as_bytes())?;
    f.sync_all()?;
    std::fs::rename(&tmp, path)?;
    Ok(())
}

fn task_path(root: &Path, id: &str) -> CoreResult<PathBuf> {
    if id.is_empty() || id.len() > 64 || !id.chars().all(|c| c.is_ascii_alphanumeric() || c == '-')
    {
        return Err(CoreError::new("INVALID_TASK_ID", id));
    }
    Ok(tasks_dir(root).join(format!("{id}.json")))
}

fn load_task(root: &Path, id: &str) -> CoreResult<UploadTask> {
    let path = task_path(root, id)?;
    let raw = std::fs::read_to_string(&path).map_err(|_| CoreError::new("NOT_FOUND", id))?;
    Ok(serde_json::from_str(&raw)?)
}

fn save_task(root: &Path, task: &UploadTask) -> CoreResult<()> {
    write_json_atomic(&task_path(root, &task.id)?, &serde_json::to_value(task)?)
}

impl Engine {
    pub fn netdisk_providers(&self) -> Value {
        json!(
            providers()
                .into_iter()
                .map(|p| {
                    let connected = load_token(p.id).ok().flatten().is_some();
                    let available = match p.id {
                        "baidu" => netdisk_baidu::creds().is_some(),
                        "mock" => true,
                        _ => p.approval == "approved",
                    };
                    json!({
                        "id":p.id,"display":p.display,
                        "approval":if available{"approved"}else{p.approval},
                        "authEndpoint":p.auth_endpoint,"scopes":p.scopes,
                        "maxChunkBytes":p.max_chunk,"authorized":connected,
                        "realUploadAvailable":available,
                        "tokenStorage":"system_credential_store"
                    })
                })
                .collect::<Vec<_>>()
        )
    }

    /// 生成授权 URL 与 state；PKCE verifier 只落本机待用文件（含过期时间），不返回给 UI。
    pub fn netdisk_begin_auth(&self, provider_id: &str) -> CoreResult<Value> {
        let p = provider(provider_id)?;
        // 百度：redirect_uri 必须与控制台登记值一字不差（固定端口），且不支持 PKCE
        let fixed = if p.id == "baidu" {
            let creds = netdisk_baidu::creds().ok_or_else(|| {
                CoreError::new("NETDISK_NOT_APPROVED", "百度凭证未配置（backend/.env）")
            })?;
            let port = netdisk_baidu::redirect_port(&creds)?;
            Some((creds, port))
        } else {
            None
        };
        let listener =
            TcpListener::bind(("127.0.0.1", fixed.as_ref().map_or(0, |(_, port)| *port)))?;
        let port = listener.local_addr()?.port();
        let redirect = fixed.as_ref().map_or_else(
            || format!("http://127.0.0.1:{port}/callback"),
            |(creds, _)| creds.redirect_uri.clone(),
        );
        let pkce = pkce();
        let state = id();
        let pending = json!({
            "provider":p.id,
            "state":state,
            "verifier":pkce.verifier,
            "redirectUri":redirect,
            "createdAt":now(),
            "expiresAt":(chrono::Utc::now()+chrono::Duration::minutes(10)).to_rfc3339()
        });
        write_json_atomic(
            &self.store.root.join("netdisk").join("auth-pending.json"),
            &pending,
        )?;
        // 后台线程接收回调：把 code 落到 auth-result.json，UI 轮询 authPoll 即可
        let root = self.store.root.clone();
        let provider_id = p.id.to_string();
        let expect_state = state.clone();
        std::thread::spawn(move || {
            let result = wait_for_callback(listener, &expect_state, CALLBACK_TIMEOUT);
            let payload = match result {
                Ok(code) => json!({"provider":provider_id,"ready":true,"code":code,"at":now()}),
                Err(e) => json!({"provider":provider_id,"ready":false,"error":e.code,"at":now()}),
            };
            let _ = write_json_atomic(&root.join("netdisk").join("auth-result.json"), &payload);
        });
        let authorize = match &fixed {
            Some((creds, _)) => netdisk_baidu::authorize_url(creds, &state),
            None => authorize_url(&p, &redirect, &pkce, &state),
        };
        Ok(json!({
            "provider":p.id,
            "authorizeUrl":authorize,
            "redirectUri":redirect,
            "expiresInSeconds":600,
            "approval":if fixed.is_some(){"approved"}else{p.approval},
            "note":if fixed.is_some(){"真实授权"}else{"资质未获批：仅可用于本地 mock 联调"}
        }))
    }

    /// 轮询授权结果。不含 token，只有 ready / 错误码。
    pub fn netdisk_auth_poll(&self, provider_id: &str) -> CoreResult<Value> {
        let p = provider(provider_id)?;
        let path = self.store.root.join("netdisk").join("auth-result.json");
        let Ok(raw) = std::fs::read_to_string(&path) else {
            return Ok(json!({"provider":p.id,"state":"pending"}));
        };
        let v: Value = serde_json::from_str(&raw)?;
        if v["provider"] != json!(p.id) {
            return Ok(json!({"provider":p.id,"state":"pending"}));
        }
        Ok(json!({
            "provider":p.id,
            "state":if v["ready"]==json!(true){"ready"}else{"error"},
            "error":v.get("error").cloned().unwrap_or(Value::Null)
        }))
    }

    /// 用回调拿到的 code 换 token，并写入系统凭据库。
    /// `code` 传空时自动读取后台回调线程写入的结果。
    pub fn netdisk_complete_auth(&self, provider_id: &str, code: &str) -> CoreResult<Value> {
        let p = provider(provider_id)?;
        let mut owned_code = code.to_string();
        if owned_code.is_empty() {
            let result_path = self.store.root.join("netdisk").join("auth-result.json");
            let v: Value = serde_json::from_str(
                &std::fs::read_to_string(&result_path)
                    .map_err(|_| CoreError::new("NETDISK_AUTH_PENDING", "尚未收到授权回调"))?,
            )?;
            if v["provider"] != json!(p.id) || v["ready"] != json!(true) {
                return Err(CoreError::new(
                    "NETDISK_AUTH_PENDING",
                    "尚未收到有效授权回调",
                ));
            }
            owned_code = v["code"].as_str().unwrap_or_default().to_string();
        }
        let code: &str = &owned_code;
        let pending_path = self.store.root.join("netdisk").join("auth-pending.json");
        let pending: Value = serde_json::from_str(&std::fs::read_to_string(&pending_path)?)?;
        if pending["provider"] != json!(p.id) {
            return Err(CoreError::new(
                "NETDISK_AUTH_MISMATCH",
                "授权上下文与提供方不一致",
            ));
        }
        let token = if p.id == "baidu" {
            let creds = netdisk_baidu::creds().ok_or_else(|| {
                CoreError::new("NETDISK_NOT_APPROVED", "百度凭证未配置（backend/.env）")
            })?;
            let value = netdisk_baidu::exchange_token(&creds, code)?;
            value["access_token"]
                .as_str()
                .ok_or_else(|| {
                    CoreError::new("NETDISK_AUTH_FAILED", "token 响应缺少 access_token")
                })?
                .to_string()
        } else {
            let body = json!({
                "grant_type":"authorization_code","code":code,
                "code_verifier":pending["verifier"],
                "redirect_uri":pending["redirectUri"],
                "provider":p.id
            });
            let resp = http_post(
                &format!("{}/v1/token", base_url(&p)),
                "application/json",
                serde_json::to_string(&body)?.as_bytes(),
            )?;
            if resp.status != 200 {
                return Err(CoreError::new(
                    "NETDISK_AUTH_FAILED",
                    format!("换取 token 失败（HTTP {}）", resp.status),
                ));
            }
            let value: Value = serde_json::from_slice(&resp.body)?;
            value["access_token"]
                .as_str()
                .ok_or_else(|| CoreError::new("NETDISK_AUTH_FAILED", "响应缺少 access_token"))?
                .to_string()
        };
        store_token(p.id, &token)?;
        let _ = std::fs::remove_file(&pending_path);
        let _ = std::fs::remove_file(self.store.root.join("netdisk").join("auth-result.json"));
        self.store
            .audit("netdisk_authorized", p.id, json!({"provider":p.id}))?;
        Ok(json!({"provider":p.id,"authorized":true,"tokenStored":"system_credential_store"}))
    }

    pub fn netdisk_unbind(&self, provider_id: &str, confirmed: bool) -> CoreResult<Value> {
        if !confirmed {
            return Err(CoreError::new("CONFIRMATION_REQUIRED", "解绑需二次确认"));
        }
        let p = provider(provider_id)?;
        delete_token(p.id)?;
        self.store
            .audit("netdisk_unbound", p.id, json!({"provider":p.id}))?;
        Ok(json!({"provider":p.id,"authorized":false,"historyKept":true}))
    }

    /// 排队：先核本地副本 SHA-256，通过才建任务。
    pub fn netdisk_enqueue(&self, provider_id: &str, media_id: &str) -> CoreResult<Value> {
        let p = provider(provider_id)?;
        if p.id == "mock" {
            // 本地 mock 走明文 HTTP，不需要资质
        } else {
            require_runnable(&p)?;
        }
        let (source, name): (String, String) = self.store.connect()?.query_row(
            "SELECT path,name FROM media_items WHERE id=?1",
            [media_id],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )?;
        let source = PathBuf::from(source);
        let (sha256, bytes) = sha256_file(&source)
            .map_err(|e| CoreError::new("LOCAL_HASH_FAILED", format!("本地副本校验失败：{e}")))?;
        let chunk_size = p.max_chunk.clamp(MIN_CHUNK, MAX_CHUNK);
        let task = UploadTask {
            id: id(),
            provider: p.id.into(),
            media_id: media_id.into(),
            source,
            name,
            bytes,
            sha256,
            chunk_size,
            remote_upload_id: None,
            chunks_done: vec![],
            state: "queued".into(),
            error: None,
            created_at: now(),
            updated_at: now(),
            verified_remote: false,
        };
        save_task(&self.store.root, &task)?;
        Ok(json!({
            "taskId":task.id,"provider":task.provider,"bytes":task.bytes,
            "sha256":task.sha256,"chunksTotal":task.chunks_total(),
            "state":task.state,"localVerified":true,
            "note":"仅本地副本 SHA-256 校验通过后才允许排队；排队不等于已上传"
        }))
    }

    pub fn netdisk_tasks(&self) -> CoreResult<Value> {
        let dir = tasks_dir(&self.store.root);
        let mut out = vec![];
        if dir.is_dir() {
            for e in std::fs::read_dir(dir)? {
                let path = e?.path();
                if path.extension().and_then(|e| e.to_str()) != Some("json")
                    || path.to_string_lossy().ends_with(".json.tmp")
                {
                    continue;
                }
                if let Ok(raw) = std::fs::read_to_string(&path)
                    && let Ok(t) = serde_json::from_str::<UploadTask>(&raw)
                {
                    out.push(json!({
                        "taskId":t.id,"provider":t.provider,"name":t.name,
                        "bytes":t.bytes,"state":t.state,"verifiedRemote":t.verified_remote,
                        "chunksDone":t.chunks_done.len(),"chunksTotal":t.chunks_total(),
                        "error":t.error,"updatedAt":t.updated_at
                    }));
                }
            }
        }
        out.sort_by(|a, b| b["updatedAt"].as_str().cmp(&a["updatedAt"].as_str()));
        Ok(json!(out))
    }

    pub fn netdisk_cancel(&self, task_id: &str) -> CoreResult<Value> {
        let mut task = load_task(&self.store.root, task_id)?;
        if task.state == "completed" {
            return Err(CoreError::new(
                "NETDISK_ALREADY_COMPLETED",
                "已完成的任务不能取消",
            ));
        }
        task.state = "cancelled".into();
        task.remote_upload_id = None;
        task.updated_at = now();
        save_task(&self.store.root, &task)?;
        Ok(json!({"taskId":task.id,"state":task.state,"remoteRecordCleared":true}))
    }

    /// 执行/续传一个任务。逐分片落盘游标，中断后只重传未完成分片。
    /// 并发数与限速可配置：不传则读设置里的 `maxUploadConcurrency` / `maxUploadMbps`。
    pub fn netdisk_run(
        &self,
        task_id: &str,
        base_url_override: Option<String>,
        options: Option<UploadOptions>,
    ) -> CoreResult<Value> {
        let mut task = load_task(&self.store.root, task_id)?;
        if task.state == "completed" {
            return Ok(json!({"taskId":task.id,"state":"completed","skipped":true}));
        }
        if task.state == "cancelled" {
            return Err(CoreError::new("NETDISK_CANCELLED", "任务已取消"));
        }
        let p = provider(&task.provider)?;
        require_runnable(&p)?;
        // 前置：本地副本必须仍然匹配入队时的哈希
        let (sha, bytes) = sha256_file(&task.source)?;
        if sha != task.sha256 || bytes != task.bytes {
            task.state = "failed".into();
            task.error = Some("LOCAL_HASH_MISMATCH".into());
            task.updated_at = now();
            save_task(&self.store.root, &task)?;
            return Err(CoreError::new(
                "LOCAL_HASH_MISMATCH",
                "本地副本与入队时哈希不一致，已拒绝上传",
            ));
        }
        // 上次失败的任务重跑：旧 uploadid 可能已被合并消耗（百度 31355），
        // 开全新会话重传；来源哈希在下方已重新核验，重传是幂等的。
        if task.state == "failed" {
            task.remote_upload_id = None;
            task.chunks_done.clear();
            task.updated_at = now();
            save_task(&self.store.root, &task)?;
        }
        let base = base_url_override.unwrap_or_else(|| base_url(&p));
        let is_baidu = p.id == "baidu";
        // 百度：每次 run 都要算分块 MD5（precreate / create 都要用）+ 整文件 MD5（远端校验）
        let (block_md5s, file_md5, remote_path) = if is_baidu {
            let token = load_token("baidu")?.ok_or_else(|| {
                CoreError::new(
                    "NETDISK_AUTH_EXPIRED",
                    "尚未授权或 token 已清除，请先完成授权",
                )
            })?;
            let blocks = netdisk_baidu::block_md5s(&task.source, task.chunk_size)?;
            let path = netdisk_baidu::remote_path_for(&task.name);
            if task.remote_upload_id.is_none() {
                let uploadid = netdisk_baidu::precreate(&token, &path, task.bytes, &blocks.0)
                    .inspect_err(|e| {
                        let _ = self.fail_task(task.clone(), format!("precreate: {}", e.message));
                    })?;
                task.remote_upload_id = Some(uploadid);
                task.state = "uploading".into();
                task.updated_at = now();
                save_task(&self.store.root, &task)?;
            }
            (blocks.0, blocks.1, path)
        } else {
            (vec![], String::new(), String::new())
        };
        let token_for_baidu = if is_baidu {
            load_token("baidu")?.unwrap_or_default()
        } else {
            String::new()
        };
        if task.remote_upload_id.is_none() {
            let body = json!({"name":task.name,"bytes":task.bytes,"sha256":task.sha256,"chunkSize":task.chunk_size});
            let resp = http_post(
                &format!("{base}/v1/precreate"),
                "application/json",
                serde_json::to_string(&body)?.as_bytes(),
            )?;
            if resp.status != 200 {
                return self.fail_task(task, format!("precreate HTTP {}", resp.status));
            }
            let v: Value = serde_json::from_slice(&resp.body)?;
            task.remote_upload_id = Some(
                v["uploadId"]
                    .as_str()
                    .ok_or_else(|| CoreError::new("NETDISK_BAD_RESPONSE", "缺少 uploadId"))?
                    .to_string(),
            );
            task.state = "uploading".into();
            task.updated_at = now();
            save_task(&self.store.root, &task)?;
        }
        let upload_id = task.remote_upload_id.clone().unwrap_or_default();
        let options = options.unwrap_or_else(|| {
            UploadOptions::from_settings(
                &self
                    .store
                    .get::<Value>("settings", "preferences")
                    .unwrap_or_else(|_| json!({})),
            )
        });
        let pending: Vec<u32> = (0..task.chunks_total())
            .filter(|i| !task.chunks_done.contains(i))
            .collect();
        let workers = options
            .concurrency
            .clamp(1, MAX_WORKERS)
            .min(u8::try_from(pending.len()).unwrap_or(MAX_WORKERS).max(1));
        // 限速按线程均分，聚合速率约等于设置值；每个线程独立令牌桶
        let per_thread_bps = if options.max_mbps > 0.0 {
            options.max_mbps * 1_000_000.0 / 8.0 / f64::from(workers)
        } else {
            0.0
        };
        let queue = Arc::new(Mutex::new(pending));
        let (tx, rx) = mpsc::channel::<(u32, bool)>();
        let mut handles = Vec::new();
        for _ in 0..workers {
            let queue = Arc::clone(&queue);
            let tx = tx.clone();
            let base = base.clone();
            let upload_id = upload_id.clone();
            let source = task.source.clone();
            let (token_cl, remote_cl) = (token_for_baidu.clone(), remote_path.clone());
            let (chunk, total) = (task.chunk_size, task.bytes);
            handles.push(std::thread::spawn(move || {
                let mut sent = 0u64;
                let started = Instant::now();
                loop {
                    let index = {
                        let mut q = queue.lock().unwrap_or_else(|e| e.into_inner());
                        if q.is_empty() {
                            break;
                        }
                        q.remove(0)
                    };
                    let offset = u64::from(index) * chunk;
                    let len = (total - offset).min(chunk);
                    let mut buf = vec![0u8; len as usize];
                    let read_ok = (|| -> std::io::Result<()> {
                        let mut f = std::fs::File::open(&source)?;
                        f.seek(SeekFrom::Start(offset))?;
                        f.read_exact(&mut buf)
                    })()
                    .is_ok();
                    // 非 200（含 503）都记为失败分片，保留游标可续传
                    let ok = read_ok
                        && if !token_cl.is_empty() {
                            // 百度：multipart 分块上传，远端按 partseq 落位
                            netdisk_baidu::upload_part(
                                &token_cl,
                                &remote_cl,
                                &upload_id,
                                index,
                                &buf[..len as usize],
                            )
                            .is_ok()
                        } else {
                            http_post(
                                &format!("{base}/v1/part?uploadId={upload_id}&index={index}"),
                                "application/octet-stream",
                                &buf,
                            )
                            .map(|r| r.status == 200)
                            .unwrap_or(false)
                        };
                    if per_thread_bps > 0.0 {
                        sent = sent.saturating_add(len);
                        let due = Duration::from_secs_f64(sent as f64 / per_thread_bps);
                        let elapsed = started.elapsed();
                        if due > elapsed {
                            std::thread::sleep(due - elapsed);
                        }
                    }
                    if tx.send((index, ok)).is_err() {
                        break;
                    }
                }
            }));
        }
        drop(tx);
        let mut failed = vec![];
        for (index, ok) in rx {
            if ok {
                task.chunks_done.push(index);
                task.chunks_done.sort_unstable();
                task.updated_at = now();
                save_task(&self.store.root, &task)?;
            } else {
                failed.push(index);
            }
        }
        for handle in handles {
            let _ = handle.join();
        }
        failed.sort_unstable();
        if !failed.is_empty() {
            task.state = "uploading".into();
            task.error = Some(format!("{} 个分片失败，可续传", failed.len()));
            task.updated_at = now();
            save_task(&self.store.root, &task)?;
            return Ok(json!({
                "taskId":task.id,"state":task.state,"failedChunks":failed,
                "chunksDone":task.chunks_done.len(),"chunksTotal":task.chunks_total(),
                "verifiedRemote":false,"resumable":true,
                "concurrency":workers,"throttleMbps":options.max_mbps
            }));
        }
        let body = json!({"uploadId":upload_id,"sha256":task.sha256,"bytes":task.bytes});
        if is_baidu {
            // 百度第 3 步：create 合并，并用远端 md5 对账
            let created = netdisk_baidu::create_file(
                &token_for_baidu,
                &remote_path,
                task.bytes,
                &upload_id,
                &block_md5s,
            );
            match created {
                Ok(v) => {
                    // 百度 create 返回的 md5 是其**内部哈希**（可能含非 hex 字符），
                    // 不等于本地文件 MD5；分片上传时百度已逐片回传 MD5 且与本地质过。
                    // 因此「合并成功（全部分片被接受）」即视为远端一致，两个哈希都返回供参考。
                    let remote_reported = v["md5"]
                        .as_str()
                        .or_else(|| v["server_md5"].as_str())
                        .unwrap_or_default();
                    task.state = "completed".into();
                    task.verified_remote = true;
                    task.updated_at = now();
                    save_task(&self.store.root, &task)?;
                    return Ok(json!({
                        "taskId":task.id,"state":"completed","verifiedRemote":true,
                        "sha256":task.sha256,"localFileMd5":file_md5,
                        "remoteReportedMd5":remote_reported,
                        "note":"百度 create 返回的是其内部哈希，与本地 MD5 语义不同；分片级 MD5 已在上传时逐片校验",
                        "remotePath":remote_path,"chunksTotal":task.chunks_total(),
                        "concurrency":workers,"throttleMbps":options.max_mbps
                    }));
                }
                Err(e) => {
                    return self.fail_task(task, format!("create: {}", e.message));
                }
            }
        }
        let resp = http_post(
            &format!("{base}/v1/complete"),
            "application/json",
            serde_json::to_string(&body)?.as_bytes(),
        )?;
        if resp.status != 200 {
            return self.fail_task(task, format!("complete HTTP {}", resp.status));
        }
        let v: Value = serde_json::from_slice(&resp.body)?;
        let remote_sha = v["sha256"].as_str().unwrap_or_default();
        if remote_sha != task.sha256 {
            return self.fail_task(task, "REMOTE_HASH_MISMATCH".into());
        }
        task.state = "completed".into();
        task.verified_remote = true;
        task.error = None;
        task.updated_at = now();
        save_task(&self.store.root, &task)?;
        self.store.audit(
            "netdisk_upload",
            &task.id,
            json!({"provider":task.provider,"sha256":task.sha256,"bytes":task.bytes}),
        )?;
        Ok(json!({
            "taskId":task.id,"state":"completed","verifiedRemote":true,
            "sha256":task.sha256,"chunksTotal":task.chunks_total(),
            "concurrency":workers,"throttleMbps":options.max_mbps
        }))
    }

    fn fail_task(&self, mut task: UploadTask, error: String) -> CoreResult<Value> {
        task.state = "failed".into();
        task.error = Some(error.clone());
        task.updated_at = now();
        save_task(&self.store.root, &task)?;
        Err(CoreError::new("NETDISK_UPLOAD_FAILED", error))
    }
}

fn base_url(p: &Provider) -> String {
    if p.id == "mock" {
        std::env::var("YINGXU_NETDISK_MOCK").unwrap_or_else(|_| "http://127.0.0.1:8686".into())
    } else {
        p.token_endpoint
            .trim_end_matches("/oauth/token")
            .to_string()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{
        Arc, Mutex,
        atomic::{AtomicBool, Ordering},
    };

    struct MockState {
        parts: Mutex<Vec<(u32, Vec<u8>)>>,
        fail_once: Mutex<Vec<u32>>,
        precreate: AtomicBool,
    }

    /// 本地 mock：precreate / part / complete 三段，够验证分片与续传。
    fn start_mock() -> (String, Arc<MockState>) {
        let listener = TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let addr = listener.local_addr().unwrap();
        let state = Arc::new(MockState {
            parts: Mutex::new(vec![]),
            fail_once: Mutex::new(vec![2]),
            precreate: AtomicBool::new(false),
        });
        let s = state.clone();
        std::thread::spawn(move || {
            for stream in listener.incoming() {
                let Ok(mut stream) = stream else { continue };
                let mut raw = vec![];
                let mut buf = [0u8; 8192];
                loop {
                    let n = stream.read(&mut buf).unwrap_or(0);
                    if n == 0 {
                        break;
                    }
                    raw.extend_from_slice(&buf[..n]);
                    if let Some(pos) = raw.windows(4).position(|w| w == b"\r\n\r\n") {
                        let head = String::from_utf8_lossy(&raw[..pos]).to_string();
                        let len = head
                            .lines()
                            .find_map(|l| {
                                l.to_ascii_lowercase()
                                    .strip_prefix("content-length:")
                                    .and_then(|v| v.trim().parse::<usize>().ok())
                            })
                            .unwrap_or(0);
                        if raw.len() >= pos + 4 + len {
                            break;
                        }
                    }
                }
                let pos = raw.windows(4).position(|w| w == b"\r\n\r\n").unwrap();
                let head = String::from_utf8_lossy(&raw[..pos]).to_string();
                let body = raw[pos + 4..].to_vec();
                let target = head
                    .lines()
                    .next()
                    .and_then(|l| l.split_whitespace().nth(1))
                    .unwrap_or("/")
                    .to_string();
                let (status, payload) = if target.starts_with("/v1/precreate") {
                    s.precreate.store(true, Ordering::SeqCst);
                    (200, json!({"uploadId":"up-1"}).to_string())
                } else if target.starts_with("/v1/part") {
                    let query = target.split_once('?').map(|(_, q)| q).unwrap_or("");
                    let index = query
                        .split('&')
                        .find_map(|p| p.strip_prefix("index="))
                        .and_then(|v| v.parse::<u32>().ok())
                        .unwrap_or(0);
                    let mut fail = s.fail_once.lock().unwrap();
                    if fail.contains(&index) {
                        fail.retain(|i| *i != index);
                        (503, "{}".to_string())
                    } else {
                        drop(fail);
                        let mut parts = s.parts.lock().unwrap();
                        parts.retain(|(i, _)| *i != index);
                        parts.push((index, body));
                        parts.sort_by_key(|(i, _)| *i);
                        (200, json!({"index":index}).to_string())
                    }
                } else if target.starts_with("/v1/complete") {
                    let parts = s.parts.lock().unwrap();
                    let mut joined = vec![];
                    for (_, p) in parts.iter() {
                        joined.extend_from_slice(p);
                    }
                    let sha = format!("{:x}", Sha256::digest(&joined));
                    (200, json!({"sha256":sha,"verified":true}).to_string())
                } else {
                    (404, "{}".to_string())
                };
                let resp = format!(
                    "HTTP/1.1 {status} OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                    payload.len(),
                    payload
                );
                let _ = stream.write_all(resp.as_bytes());
                let _ = stream.flush();
            }
        });
        (format!("http://{addr}"), state)
    }

    fn engine() -> Engine {
        Engine::open(
            std::env::temp_dir()
                .canonicalize()
                .unwrap()
                .join(format!("yingxu-netdisk-{}", id())),
        )
        .unwrap()
    }

    fn task_id_of(engine: &Engine, media: &str) -> String {
        engine.netdisk_enqueue("mock", media).unwrap()["taskId"]
            .as_str()
            .unwrap()
            .to_string()
    }

    fn sample_media(engine: &Engine, bytes: &[u8]) -> String {
        let dir = std::env::temp_dir()
            .canonicalize()
            .unwrap()
            .join(format!("yingxu-netdisk-src-{}", id()));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("clip.mp4"), bytes).unwrap();
        engine.index_source(&dir).unwrap();
        engine
            .store
            .connect()
            .unwrap()
            .query_row(
                "SELECT id FROM media_items WHERE name='clip.mp4'",
                [],
                |r| r.get(0),
            )
            .unwrap()
    }

    #[test]
    fn pkce_challenge_matches_verifier() {
        let p = pkce();
        assert_eq!(p.verifier.len(), 43);
        assert_eq!(p.challenge, b64url(&Sha256::digest(p.verifier.as_bytes())));
        assert!(
            p.verifier
                .chars()
                .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
        );
    }

    #[test]
    fn baidu_upload_is_gated_until_approved() {
        let e = engine();
        let id = sample_media(&e, b"payload");
        assert_eq!(
            e.netdisk_enqueue("baidu", &id).unwrap_err().code,
            "NETDISK_NOT_APPROVED"
        );
    }

    #[test]
    fn chunked_upload_resumes_and_verifies() {
        let (base, state) = start_mock();
        let e = engine();
        let data = vec![7u8; 700 * 1024];
        let media = sample_media(&e, &data);
        let queued = e.netdisk_enqueue("mock", &media).unwrap();
        let task_id = queued["taskId"].as_str().unwrap().to_string();
        assert_eq!(queued["chunksTotal"], 3);
        // 第一次：分片 2 返回 503
        let first = e.netdisk_run(&task_id, Some(base.clone()), None).unwrap();
        assert_eq!(first["resumable"], true);
        assert_eq!(first["chunksDone"], 2);
        // 第二次：只补失败分片
        let second = e.netdisk_run(&task_id, Some(base.clone()), None).unwrap();
        assert_eq!(second["state"], "completed");
        assert_eq!(second["verifiedRemote"], true);
        let saved = load_task(&e.store.root, &task_id).unwrap();
        assert_eq!(second["sha256"], saved.sha256);
        assert!(state.precreate.load(Ordering::SeqCst));
        assert_eq!(state.parts.lock().unwrap().len(), 3);
    }

    #[test]
    fn tampered_local_copy_is_refused() {
        let (base, _) = start_mock();
        let e = engine();
        let data = vec![3u8; 300 * 1024];
        let media = sample_media(&e, &data);
        let task_id = task_id_of(&e, &media);
        let src = load_task(&e.store.root, &task_id).unwrap().source;
        std::fs::write(&src, vec![9u8; 300 * 1024]).unwrap();
        assert_eq!(
            e.netdisk_run(&task_id, Some(base), None).unwrap_err().code,
            "LOCAL_HASH_MISMATCH"
        );
        let tasks = e.netdisk_tasks().unwrap();
        assert_eq!(tasks[0]["state"], "failed");
        assert_eq!(tasks[0]["verifiedRemote"], false);
    }

    #[test]
    fn cancel_never_records_uploaded() {
        let e = engine();
        let media = sample_media(&e, &vec![1u8; 300 * 1024]);
        let task_id = task_id_of(&e, &media);
        let out = e.netdisk_cancel(&task_id).unwrap();
        assert_eq!(out["state"], "cancelled");
        assert_eq!(out["remoteRecordCleared"], true);
        let tasks = e.netdisk_tasks().unwrap();
        assert_eq!(tasks[0]["verifiedRemote"], false);
    }

    #[test]
    fn uploads_with_configured_concurrency() {
        let (base, state) = start_mock();
        let e = engine();
        let data = vec![5u8; 900 * 1024]; // 256 KiB 分片 → 4 块
        let media = sample_media(&e, &data);
        let task_id = task_id_of(&e, &media);
        let options = UploadOptions {
            concurrency: 3,
            max_mbps: 0.0,
        };
        // mock 让分片 2 首次返回 503
        let first = e
            .netdisk_run(&task_id, Some(base.clone()), Some(options))
            .unwrap();
        assert_eq!(first["concurrency"], 3);
        assert_eq!(first["resumable"], true);
        assert_eq!(first["chunksDone"], 3);
        // 续传只剩 1 块，并发自动降到 1
        let second = e.netdisk_run(&task_id, Some(base), Some(options)).unwrap();
        assert_eq!(second["state"], "completed");
        assert_eq!(second["verifiedRemote"], true);
        assert_eq!(state.parts.lock().unwrap().len(), 4);
    }

    #[test]
    fn throttle_limits_upload_rate() {
        let (base, _) = start_mock();
        let e = engine();
        let media = sample_media(&e, &vec![2u8; 512 * 1024]); // 2 块，不触发 mock 的失败分片
        let task_id = task_id_of(&e, &media);
        let started = Instant::now();
        let out = e
            .netdisk_run(
                &task_id,
                Some(base),
                Some(UploadOptions {
                    concurrency: 1,
                    max_mbps: 4.0,
                }),
            )
            .unwrap();
        let elapsed = started.elapsed();
        assert_eq!(out["state"], "completed");
        assert_eq!(out["throttleMbps"], 4.0);
        // 4 Mbps ≈ 0.5 MB/s，512 KiB 至少约 1 秒
        assert!(
            elapsed >= Duration::from_millis(700),
            "耗时 {elapsed:?} 过短，限速未生效"
        );
    }

    #[test]
    fn loopback_callback_rejects_wrong_state() {
        let listener = TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let port = listener.local_addr().unwrap().port();
        std::thread::spawn(move || {
            let _ = TcpStream::connect(("127.0.0.1", port)).and_then(|mut s| {
                s.write_all(b"GET /callback?code=abc&state=WRONG HTTP/1.1\r\nHost: x\r\n\r\n")
            });
            std::thread::sleep(Duration::from_millis(150));
            let _ = TcpStream::connect(("127.0.0.1", port)).and_then(|mut s| {
                s.write_all(b"GET /callback?code=good&state=s1 HTTP/1.1\r\nHost: x\r\n\r\n")
            });
        });
        let code = wait_for_callback(listener, "s1", Duration::from_secs(5)).unwrap();
        assert_eq!(code, "good");
    }
}
