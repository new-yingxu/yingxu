//! 百度网盘 xpan 真实对接（「个人使用」应用，2026-10-26 起可用）。
//!
//! 凭证只从环境变量读取（CLI 启动时装载 `backend/.env`），**绝不写进仓库**：
//! `YINGXU_BAIDU_APPKEY / SECRETKEY / REDIRECT_URI`。没配 → 视为资质未获批。
//!
//! 流程（官方 xpan 三段式）：
//!   1. precreate  —— 声明文件与分块 MD5，拿 `uploadid`
//!   2. superfile2 —— 逐块上传（multipart），支持并发与断点续传
//!   3. create     —— 合并；返回的 md5 用于远端一致性校验
use crate::model::{CoreError, CoreResult};
use crate::netdisk::http_request;
use md5::{Digest, Md5};
use serde_json::Value;
use std::io::{Read, Seek, SeekFrom};
use std::path::Path;

const AUTH_ENDPOINT: &str = "https://openapi.baidu.com/oauth/2.0/authorize";
const TOKEN_ENDPOINT: &str = "https://openapi.baidu.com/oauth/2.0/token";
const XPAN_FILE: &str = "https://pan.baidu.com/rest/2.0/xpan/file";
const PCS_SUPERFILE: &str = "https://d.pcs.baidu.com/rest/2.0/pcs/superfile2";
/// 个人使用应用上传目录（百度要求放在 /apps/<应用名>/ 下）
pub const REMOTE_DIR: &str = "/apps/影序YINGXU桌面";
/// 单分块 4 MiB，与 provider.max_chunk 一致
pub const BLOCK_SIZE: u64 = 4 * 1024 * 1024;
/// 分块上传是大 body，超时放宽到 5 分钟
const UPLOAD_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(300);

#[derive(Debug, Clone)]
pub struct Creds {
    pub app_key: String,
    pub secret_key: String,
    pub redirect_uri: String,
}

/// 凭证是否已配置。没配 → provider 保持「未获批」。
pub fn creds() -> Option<Creds> {
    let app_key = std::env::var("YINGXU_BAIDU_APPKEY").ok()?;
    let secret_key = std::env::var("YINGXU_BAIDU_SECRETKEY").ok()?;
    let redirect_uri = std::env::var("YINGXU_BAIDU_REDIRECT_URI")
        .unwrap_or_else(|_| "http://127.0.0.1:8765/callback".into());
    if app_key.is_empty() || secret_key.is_empty() {
        return None;
    }
    Some(Creds {
        app_key,
        secret_key,
        redirect_uri,
    })
}

/// 授权页 URL。百度 OAuth 不支持 PKCE，靠 `state` 防-CSRF + client_secret 换 token。
pub fn authorize_url(c: &Creds, state: &str) -> String {
    format!(
        "{AUTH_ENDPOINT}?response_type=code&client_id={}&redirect_uri={}&scope=basic,netdisk&display=page&state={}",
        enc(&c.app_key),
        enc(&c.redirect_uri),
        enc(state)
    )
}

/// 回调地址里写死的端口（listener 必须绑在同一个端口上）
pub fn redirect_port(c: &Creds) -> CoreResult<u16> {
    let rest = c
        .redirect_uri
        .strip_prefix("http://127.0.0.1:")
        .ok_or_else(|| {
            CoreError::new(
                "NETDISK_REDIRECT_INVALID",
                format!(
                    "redirect_uri 必须是 127.0.0.1 回环地址，当前：{}",
                    c.redirect_uri
                ),
            )
        })?;
    rest.split('/')
        .next()
        .and_then(|s| s.parse().ok())
        .ok_or_else(|| CoreError::new("NETDISK_REDIRECT_INVALID", c.redirect_uri.clone()))
}

/// 用授权码换 token（GET，官方口径）。返回原始 JSON（含 access_token / refresh_token / expires_in）。
pub fn exchange_token(c: &Creds, code: &str) -> CoreResult<Value> {
    let url = format!(
        "{TOKEN_ENDPOINT}?grant_type=authorization_code&code={}&client_id={}&client_secret={}&redirect_uri={}",
        enc(code),
        enc(&c.app_key),
        enc(&c.secret_key),
        enc(&c.redirect_uri)
    );
    let resp = http_request("GET", &url, None, &[], None)?;
    let v: Value = serde_json::from_slice(&resp.body).map_err(|_| {
        CoreError::new(
            "NETDISK_BAD_RESPONSE",
            format!(
                "token 响应不是 JSON（HTTP {}）：{}",
                resp.status,
                truncate(&resp.body)
            ),
        )
    })?;
    if resp.status != 200 || v.get("access_token").is_none() {
        let desc = v["error_description"]
            .as_str()
            .or_else(|| v["error"].as_str())
            .unwrap_or("未知错误");
        return Err(CoreError::new(
            "NETDISK_AUTH_FAILED",
            format!("换取 token 失败（HTTP {}）：{desc}", resp.status),
        ));
    }
    Ok(v)
}

/// 百度 `errno` → 统一错误码。0 / 缺 errno 都算成功。
fn check_errno(v: &Value) -> CoreResult<()> {
    let errno = v.get("errno").and_then(|e| e.as_i64());
    let error_code = v.get("error_code").and_then(|e| e.as_i64());
    // 两种错误字段都没有（或 errno=0），视为成功
    if errno == Some(0) || (errno.is_none() && error_code.is_none()) {
        return Ok(());
    }
    // 新式错误体（HTTP 400）：{"error_code":31112,"error_msg":"exceed quota"}
    if errno.is_none() {
        let (code, msg) = if error_code == Some(31112) {
            (
                "NETDISK_QUOTA_EXCEEDED",
                "网盘空间不足（已用超过总容量，请清理或扩容）",
            )
        } else {
            ("NETDISK_REMOTE_ERROR", "平台返回错误")
        };
        return Err(CoreError::new(
            code,
            format!("error_code={error_code:?}：{msg}"),
        ));
    }
    let (code, msg) = match errno {
        Some(-6) | Some(111) => (
            "NETDISK_AUTH_EXPIRED",
            "access_token 无效或已过期，请重新授权",
        ),
        // -10 在上传/合并语境下是「空间已满」
        Some(-10) | Some(-999) | Some(12) => (
            "NETDISK_QUOTA_EXCEEDED",
            "网盘空间不足（已用超过总容量，请清理或扩容）",
        ),
        Some(-7) | Some(31064) => ("NETDISK_PATH_INVALID", "远端路径不合法或文件已存在"),
        Some(31079) => ("NETDISK_REMOTE_ERROR", "未找到文件 MD5（分块可能未传全）"),
        Some(31363) => ("NETDISK_REMOTE_ERROR", "分块校验失败，请重新续传"),
        Some(2) => (
            "NETDISK_REMOTE_ERROR",
            "参数错误（常见于 path 不在 /apps 下）",
        ),
        _ => ("NETDISK_REMOTE_ERROR", "平台返回错误"),
    };
    Err(CoreError::new(code, format!("errno={errno:?}：{msg}")))
}

fn xpan_url(method: &str, token: &str) -> String {
    format!("{XPAN_FILE}?method={method}&access_token={}", enc(token))
}

/// 流式计算：每个分块的 MD5 + 整文件 MD5（hex）。
pub fn block_md5s(file: &Path, block: u64) -> CoreResult<(Vec<String>, String)> {
    let mut f = std::fs::File::open(file)?;
    let mut whole = Md5::new();
    let mut blocks = vec![];
    let mut buf = vec![0u8; block as usize];
    loop {
        let mut filled = 0usize;
        while filled < buf.len() {
            match f.read(&mut buf[filled..]) {
                Ok(0) => break,
                Ok(n) => filled += n,
                Err(ref e) if e.kind() == std::io::ErrorKind::Interrupted => {}
                Err(e) => return Err(CoreError::new("LOCAL_HASH_FAILED", e.to_string())),
            }
        }
        if filled == 0 {
            break;
        }
        let mut h = Md5::new();
        h.update(&buf[..filled]);
        blocks.push(hex(&h.finalize()));
        whole.update(&buf[..filled]);
        if filled < buf.len() {
            break;
        }
    }
    Ok((blocks, hex(&whole.finalize())))
}

/// 第 1 步：precreate。`autoinit=1` 时同一文件重复调用会返回同一个 uploadid（天然支持续传）。
pub fn precreate(
    token: &str,
    remote_path: &str,
    size: u64,
    blocks: &[String],
) -> CoreResult<String> {
    // rtype=3：云端同名文件直接覆盖（续传/重跑语义）
    let body = form(&[
        ("path", remote_path),
        ("size", &size.to_string()),
        ("isdir", "0"),
        ("autoinit", "1"),
        ("rtype", "3"),
        ("block_list", &serde_json::to_string(blocks)?),
    ]);
    let resp = http_request(
        "POST",
        &xpan_url("precreate", token),
        Some("application/x-www-form-urlencoded"),
        body.as_bytes(),
        None,
    )?;
    let v: Value = serde_json::from_slice(&resp.body).map_err(|_| {
        CoreError::new(
            "NETDISK_BAD_RESPONSE",
            format!("precreate：{}", truncate(&resp.body)),
        )
    })?;
    check_errno(&v)?;
    if resp.status != 200 {
        return Err(CoreError::new(
            "NETDISK_REMOTE_ERROR",
            format!("precreate HTTP {}", resp.status),
        ));
    }
    let uploadid = match &v["uploadid"] {
        Value::String(s) => Some(s.clone()),
        Value::Number(n) => Some(n.to_string()),
        _ => None,
    };
    uploadid.ok_or_else(|| CoreError::new("NETDISK_BAD_RESPONSE", "precreate 缺少 uploadid"))
}

/// 第 2 步：superfile2 单块上传（multipart）。失败返回 Err，调用方保留游标。
pub fn upload_part(
    token: &str,
    remote_path: &str,
    uploadid: &str,
    partseq: u32,
    data: &[u8],
) -> CoreResult<()> {
    let boundary = format!("yingxu-boundary-{}", std::process::id());
    let mut body = Vec::with_capacity(data.len() + 512);
    body.extend_from_slice(
        format!(
            "--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"chunk{partseq}\"\r\nContent-Type: application/octet-stream\r\n\r\n"
        )
        .as_bytes(),
    );
    body.extend_from_slice(data);
    body.extend_from_slice(format!("\r\n--{boundary}--\r\n").as_bytes());
    let url = format!(
        "{PCS_SUPERFILE}?method=upload&access_token={}&type=tmpfile&path={}&uploadid={}&partseq={partseq}",
        enc(token),
        enc(remote_path),
        enc(uploadid)
    );
    let resp = http_request(
        "POST",
        &url,
        Some(&format!("multipart/form-data; boundary={boundary}")),
        &body,
        Some(UPLOAD_TIMEOUT),
    )?;
    let v: Value = serde_json::from_slice(&resp.body).map_err(|_| {
        CoreError::new(
            "NETDISK_BAD_RESPONSE",
            format!("分块上传：{}", truncate(&resp.body)),
        )
    })?;
    check_errno(&v)?;
    if resp.status != 200 {
        return Err(CoreError::new(
            "NETDISK_REMOTE_ERROR",
            format!("分块 {partseq} HTTP {}", resp.status),
        ));
    }
    Ok(())
}

/// 第 3 步：create（合并）。返回 JSON（含 md5 / server_md5 / size）。
pub fn create_file(
    token: &str,
    remote_path: &str,
    size: u64,
    uploadid: &str,
    blocks: &[String],
) -> CoreResult<Value> {
    let body = form(&[
        ("path", remote_path),
        ("size", &size.to_string()),
        ("isdir", "0"),
        ("uploadid", uploadid),
        ("block_list", &serde_json::to_string(blocks)?),
    ]);
    let resp = http_request(
        "POST",
        &xpan_url("create", token),
        Some("application/x-www-form-urlencoded"),
        body.as_bytes(),
        None,
    )?;
    let v: Value = serde_json::from_slice(&resp.body).map_err(|_| {
        CoreError::new(
            "NETDISK_BAD_RESPONSE",
            format!("create：{}", truncate(&resp.body)),
        )
    })?;
    check_errno(&v)?;
    if resp.status != 200 {
        return Err(CoreError::new(
            "NETDISK_REMOTE_ERROR",
            format!("create HTTP {}", resp.status),
        ));
    }
    Ok(v)
}

/// 删除远端文件（取消/清理用）。静默容忍失败：本地记录已清即视为不再持有远端引用。
pub fn remove_file(token: &str, remote_path: &str) -> CoreResult<()> {
    let body = form(&[("filelist", &serde_json::to_string(&[remote_path])?)]);
    let resp = http_request(
        "POST",
        &xpan_url("delete", token),
        Some("application/x-www-form-urlencoded"),
        body.as_bytes(),
        None,
    )?;
    if resp.status == 200
        && let Ok(v) = serde_json::from_slice::<Value>(&resp.body)
    {
        check_errno(&v)?;
    }
    Ok(())
}

// ---------- 小工具 ----------

pub fn remote_path_for(name: &str) -> String {
    // 只保留文件名本身，去掉任何路径成分；百度路径必须以 / 开头
    let safe: String = name
        .rsplit(['/', '\\'])
        .next()
        .unwrap_or("file")
        .to_string();
    format!("{REMOTE_DIR}/{safe}")
}

fn form(pairs: &[(&str, &str)]) -> String {
    pairs
        .iter()
        .map(|(k, v)| format!("{}={}", enc(k), enc(v)))
        .collect::<Vec<_>>()
        .join("&")
}

/// 百度对 form/路径里的中文与特殊字符要求百分号编码
pub fn enc(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for b in s.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(b as char)
            }
            b' ' => out.push_str("%20"),
            other => out.push_str(&format!("%{other:02X}")),
        }
    }
    out
}

fn hex(digest: &[u8]) -> String {
    digest.iter().map(|b| format!("{b:02x}")).collect()
}

fn truncate(body: &[u8]) -> String {
    let s = String::from_utf8_lossy(body);
    if s.len() > 200 {
        format!("{}…", &s[..200])
    } else {
        s.into_owned()
    }
}

/// 读一段文件（供分块线程复用）
pub fn read_chunk(file: &Path, offset: u64, len: usize) -> CoreResult<Vec<u8>> {
    let mut f = std::fs::File::open(file)?;
    f.seek(SeekFrom::Start(offset))?;
    let mut buf = vec![0u8; len];
    f.read_exact(&mut buf)?;
    Ok(buf)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn encodes_form_values() {
        assert_eq!(enc("abc-_.~"), "abc-_.~");
        assert_eq!(enc("a b"), "a%20b");
        assert_eq!(enc("影"), "%E5%BD%B1");
        assert_eq!(enc("1:1"), "1%3A1");
    }

    #[test]
    fn remote_path_strips_directories() {
        assert_eq!(
            remote_path_for("/别处/照片 1.jpg"),
            "/apps/影序YINGXU桌面/照片 1.jpg"
        );
        assert_eq!(remote_path_for("..\\evil"), "/apps/影序YINGXU桌面/evil");
    }

    #[test]
    fn redirect_port_is_parsed() {
        let c = Creds {
            app_key: "k".into(),
            secret_key: "s".into(),
            redirect_uri: "http://127.0.0.1:8765/callback".into(),
        };
        assert_eq!(redirect_port(&c).unwrap(), 8765);
    }

    #[test]
    fn block_md5_matches_reference() {
        // md5("abc") = 900150983cd24fb0d6963f7d28e17f72
        let dir = std::env::temp_dir().canonicalize().unwrap();
        let p = dir.join(format!("yingxu-md5-{}", std::process::id()));
        std::fs::write(&p, b"abc").unwrap();
        let (blocks, whole) = block_md5s(&p, BLOCK_SIZE).unwrap();
        std::fs::remove_file(&p).ok();
        assert_eq!(blocks, vec!["900150983cd24fb0d6963f7d28e17f72".to_string()]);
        assert_eq!(whole, "900150983cd24fb0d6963f7d28e17f72");
    }
}
