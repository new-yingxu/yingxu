use keyring::Entry;
use reqwest::{Url, blocking::Client, redirect::Policy};
use serde::{Deserialize, Serialize};
use std::{
    fmt,
    io::Read,
    net::{IpAddr, SocketAddr},
    sync::Arc,
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use zeroize::{Zeroize, ZeroizeOnDrop, Zeroizing};

const EXCHANGE_PATH: &str = "/v1/auth/session/exchange";
const LOGOUT_PATH: &str = "/v1/auth/logout";
const MAX_RESPONSE_BYTES: u64 = 1024 * 1024;
const MAX_DOH_RESPONSE_BYTES: u64 = 32 * 1024;
const MAX_REQUEST_BYTES: usize = 4 * 1024 * 1024;
const KEYRING_SERVICE: &str = "app.yingxu.desktop.cloud";
const KEYRING_ACCOUNT: &str = "device-session-v1";
const DOH_HOST: &str = "cloudflare-dns.com";
const DOH_ENDPOINT: &str = "https://cloudflare-dns.com/dns-query";
const DOH_BOOTSTRAP_ADDRESSES: &[SocketAddr] = &[
    SocketAddr::new(IpAddr::V4(std::net::Ipv4Addr::new(1, 1, 1, 1)), 443),
    SocketAddr::new(IpAddr::V4(std::net::Ipv4Addr::new(1, 0, 0, 1)), 443),
];

#[derive(Clone)]
pub struct CloudApiConfig {
    origin: Url,
}

impl CloudApiConfig {
    pub fn new(origin: &str) -> Result<Self, CloudApiError> {
        let origin = Url::parse(origin).map_err(|_| CloudApiError::InvalidConfiguration)?;
        if origin.scheme() != "https"
            || origin.host_str().is_none()
            || !origin.username().is_empty()
            || origin.password().is_some()
            || origin.port_or_known_default() != Some(443)
            || !matches!(origin.path(), "" | "/")
            || origin.query().is_some()
            || origin.fragment().is_some()
        {
            return Err(CloudApiError::InvalidConfiguration);
        }
        Ok(Self { origin })
    }

    pub fn from_embedded_config() -> Result<Self, CloudApiError> {
        let origin = option_env!("YINGXU_CLOUD_API_ORIGIN").ok_or(CloudApiError::NotConfigured)?;
        Self::new(origin)
    }

    fn endpoint(&self, path: &str) -> Result<Url, CloudApiError> {
        if !matches!(path, EXCHANGE_PATH | LOGOUT_PATH) {
            return Err(CloudApiError::InvalidEndpoint);
        }
        self.origin
            .join(path)
            .map_err(|_| CloudApiError::InvalidEndpoint)
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CloudApiError {
    NotConfigured,
    InvalidConfiguration,
    InvalidEndpoint,
    Unauthenticated,
    InvalidRequest,
    Unavailable,
    InvalidResponse,
    SecureStoreUnavailable,
}

impl fmt::Display for CloudApiError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::NotConfigured => "影序云端 API 域名尚未配置。",
            Self::InvalidConfiguration | Self::InvalidEndpoint => "影序云端 API 地址配置无效。",
            Self::Unauthenticated => "影序账号会话已失效，请重新登录。",
            Self::InvalidRequest => "云端请求内容无效或超过大小限制。",
            Self::Unavailable => "影序云端暂时不可用，请稍后重试。",
            Self::InvalidResponse => "影序云端返回了无效响应。",
            Self::SecureStoreUnavailable => "系统凭据存储不可用，影序会话未保存。",
        })
    }
}

pub trait CloudApiTransport: Send + Sync {
    fn request_json(
        &self,
        method: &str,
        url: &Url,
        body: &[u8],
        bearer: Option<&str>,
    ) -> Result<serde_json::Value, CloudApiError>;
}

pub trait CloudSessionStore: Send + Sync {
    fn save(&self, session: &CloudSession) -> Result<(), CloudApiError>;
    fn load(&self) -> Result<Option<CloudSession>, CloudApiError>;
    fn clear(&self) -> Result<(), CloudApiError>;
}

#[derive(Clone, Serialize, Deserialize, Zeroize, ZeroizeOnDrop)]
pub struct CloudSession {
    access_token: String,
    owner_id: String,
    device_id: String,
    origin: String,
    display_name: String,
    expires_at_unix: u64,
    expires_in: u64,
}

impl fmt::Debug for CloudSession {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("CloudSession")
            .field("access_token", &"[REDACTED]")
            .field("owner_id", &"[REDACTED]")
            .field("device_id", &"[REDACTED]")
            .field("origin", &self.origin)
            .field("expires_in", &self.expires_in)
            .finish()
    }
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct ExchangeRequest<'a> {
    display_name: &'a str,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct ExchangeResponse {
    access_token: String,
    token_type: String,
    expires_in: u64,
    owner_id: String,
    device_id: String,
    origin: String,
    device_attested: bool,
}

#[derive(Serialize)]
struct NoBody {}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CloudSessionInfo {
    pub owner_id: String,
    pub device_id: String,
    pub origin: String,
    pub display_name: String,
    pub expires_in: u64,
    pub expires_at_unix: u64,
    pub device_attested: bool,
}

pub struct CloudApiClient {
    config: CloudApiConfig,
    transport: Arc<dyn CloudApiTransport>,
    session_store: Arc<dyn CloudSessionStore>,
}

impl CloudApiClient {
    // Native-only provider bridge. Deliberately excluded from request_url's IPC
    // allowlist so codes and provider tokens never pass through WebView JS.
    pub(crate) fn baidu_broker(
        &self,
        operation: &str,
        body: Option<&serde_json::Value>,
    ) -> Result<serde_json::Value, CloudApiError> {
        if self.current_session()?.is_none() {
            return Err(CloudApiError::Unauthenticated);
        }
        let session = self
            .session_store
            .load()?
            .ok_or(CloudApiError::Unauthenticated)?;
        let (method, path) = match operation {
            "config" => ("GET", "/v1/netdisk/baidu/config"),
            "token" => ("POST", "/v1/netdisk/baidu/token"),
            _ => return Err(CloudApiError::InvalidEndpoint),
        };
        let url = self
            .config
            .origin
            .join(path)
            .map_err(|_| CloudApiError::InvalidEndpoint)?;
        let bytes = Zeroizing::new(
            serde_json::to_vec(&body.unwrap_or(&serde_json::json!({})))
                .map_err(|_| CloudApiError::InvalidRequest)?,
        );
        self.transport
            .request_json(method, &url, &bytes, Some(&session.access_token))
    }

    pub fn from_embedded_config() -> Result<Self, CloudApiError> {
        let config = CloudApiConfig::from_embedded_config()?;
        let host = config
            .origin
            .host_str()
            .ok_or(CloudApiError::InvalidConfiguration)?;
        let resolved_addresses = resolve_api_host_over_doh(host).ok();
        let mut builder = Client::builder()
            .https_only(true)
            .redirect(Policy::none())
            .timeout(Duration::from_secs(20))
            .no_proxy();
        if let Some(addresses) = resolved_addresses.as_deref() {
            builder = builder.resolve_to_addrs(host, addresses);
        }
        let client = builder.build().map_err(|_| CloudApiError::Unavailable)?;
        Ok(Self::new(
            config,
            Arc::new(NativeCloudApiTransport { client }),
            Arc::new(SystemCloudSessionStore),
        ))
    }

    pub fn new(
        config: CloudApiConfig,
        transport: Arc<dyn CloudApiTransport>,
        session_store: Arc<dyn CloudSessionStore>,
    ) -> Self {
        Self {
            config,
            transport,
            session_store,
        }
    }

    pub fn exchange(
        &self,
        cloudbase_access_token: &str,
        display_name: &str,
    ) -> Result<CloudSessionInfo, CloudApiError> {
        if cloudbase_access_token.is_empty() {
            return Err(CloudApiError::Unauthenticated);
        }
        if display_name.trim().is_empty() || display_name.len() > 120 {
            return Err(CloudApiError::InvalidRequest);
        }
        let url = self.config.endpoint(EXCHANGE_PATH)?;
        let body = Zeroizing::new(
            serde_json::to_vec(&ExchangeRequest {
                display_name: display_name.trim(),
            })
            .map_err(|_| CloudApiError::InvalidResponse)?,
        );
        let response =
            self.transport
                .request_json("POST", &url, &body, Some(cloudbase_access_token))?;
        let response: ExchangeResponse =
            serde_json::from_value(response).map_err(|_| CloudApiError::InvalidResponse)?;
        if !response.token_type.eq_ignore_ascii_case("bearer")
            || response.access_token.is_empty()
            || response.expires_in != 900
            || response.owner_id.is_empty()
            || response.device_id.is_empty()
            || response.origin != "desktop"
            || response.device_attested
        {
            return Err(CloudApiError::InvalidResponse);
        }
        let expires_at_unix = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|_| CloudApiError::InvalidResponse)?
            .as_secs()
            + response.expires_in;
        let session = CloudSession {
            access_token: response.access_token,
            owner_id: response.owner_id,
            device_id: response.device_id,
            origin: response.origin,
            display_name: display_name.trim().to_owned(),
            expires_in: response.expires_in,
            expires_at_unix,
        };
        let info = CloudSessionInfo {
            owner_id: session.owner_id.clone(),
            device_id: session.device_id.clone(),
            origin: session.origin.clone(),
            display_name: session.display_name.clone(),
            expires_in: session.expires_in,
            expires_at_unix: session.expires_at_unix,
            device_attested: false,
        };
        self.session_store.save(&session)?;
        Ok(info)
    }

    pub fn current_session(&self) -> Result<Option<CloudSessionInfo>, CloudApiError> {
        let Some(session) = self.session_store.load()? else {
            return Ok(None);
        };
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|_| CloudApiError::InvalidResponse)?
            .as_secs();
        if session.expires_at_unix <= now {
            self.session_store.clear()?;
            return Ok(None);
        }
        Ok(Some(CloudSessionInfo {
            owner_id: session.owner_id.clone(),
            device_id: session.device_id.clone(),
            origin: session.origin.clone(),
            display_name: session.display_name.clone(),
            expires_in: session.expires_in,
            expires_at_unix: session.expires_at_unix,
            device_attested: false,
        }))
    }

    pub fn logout(&self) -> Result<(), CloudApiError> {
        let Some(session) = self.session_store.load()? else {
            return self.session_store.clear();
        };
        let url = self.config.endpoint(LOGOUT_PATH)?;
        let body = serde_json::to_vec(&NoBody {}).map_err(|_| CloudApiError::InvalidResponse)?;
        self.transport
            .request_json("POST", &url, &body, Some(&session.access_token))?;
        self.session_store.clear()
    }

    pub fn request(
        &self,
        method: &str,
        path: &str,
        body: Option<&serde_json::Value>,
    ) -> Result<serde_json::Value, CloudApiError> {
        let session = self
            .session_store
            .load()?
            .ok_or(CloudApiError::Unauthenticated)?;
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|_| CloudApiError::InvalidResponse)?
            .as_secs();
        if session.expires_at_unix <= now {
            self.session_store.clear()?;
            return Err(CloudApiError::Unauthenticated);
        }
        let url = self.request_url(method, path)?;
        let body = Zeroizing::new(match body {
            Some(value) => serde_json::to_vec(value).map_err(|_| CloudApiError::InvalidResponse)?,
            None => b"{}".to_vec(),
        });
        if body.len() > MAX_REQUEST_BYTES {
            return Err(CloudApiError::InvalidRequest);
        }
        match self
            .transport
            .request_json(method, &url, &body, Some(&session.access_token))
        {
            Err(CloudApiError::Unauthenticated) => {
                self.session_store.clear()?;
                Err(CloudApiError::Unauthenticated)
            }
            result => result,
        }
    }

    fn request_url(&self, method: &str, path: &str) -> Result<Url, CloudApiError> {
        let (route, query) = path
            .split_once('?')
            .map_or((path, None), |(route, query)| (route, Some(query)));
        let allowed = match (method, route) {
            ("GET", "/v1/devices")
            | ("POST", "/v1/sync/events")
            | ("POST", "/v1/sync/snapshots")
            | ("GET", "/v1/sync/snapshot")
            | ("GET", "/v1/account/sync-preferences")
            | ("PUT", "/v1/account/sync-preferences")
            | ("GET", "/v1/agent/model")
            | ("GET", "/v1/agent/session-deletions")
            | ("POST", "/v1/agent/turns")
            | ("POST", "/v1/agent/actions")
            | ("DELETE", "/v1/account/sync-data") => true,
            ("POST", value)
                if value
                    .strip_prefix("/v1/devices/")
                    .and_then(|tail| tail.strip_suffix("/revoke"))
                    .is_some_and(valid_route_id) =>
            {
                true
            }
            ("GET", value)
                if value
                    .strip_prefix("/v1/agent/sessions/")
                    .and_then(|tail| tail.strip_suffix("/turns"))
                    .is_some_and(valid_route_id) =>
            {
                true
            }
            ("DELETE", value)
                if value
                    .strip_prefix("/v1/agent/sessions/")
                    .is_some_and(valid_route_id) =>
            {
                true
            }
            ("POST", value)
                if value
                    .strip_prefix("/v1/agent/actions/")
                    .and_then(|tail| tail.strip_suffix("/receipt"))
                    .is_some_and(valid_route_id) =>
            {
                true
            }
            ("GET", value)
                if value
                    .strip_prefix("/v1/agent/actions/")
                    .is_some_and(valid_route_id) =>
            {
                true
            }
            ("GET", value)
                if value
                    .strip_prefix("/v1/sync/snapshots/")
                    .is_some_and(valid_snapshot_id) =>
            {
                true
            }
            _ => false,
        };
        if !allowed || query.is_some_and(|query| !valid_query(method, route, query)) {
            return Err(CloudApiError::InvalidEndpoint);
        }
        let mut url = self
            .config
            .origin
            .join(route)
            .map_err(|_| CloudApiError::InvalidEndpoint)?;
        if let Some(query) = query {
            url.set_query(Some(query));
        }
        Ok(url)
    }
}

#[derive(Deserialize)]
struct DohResponse {
    #[serde(rename = "Status")]
    status: u8,
    #[serde(default, rename = "Answer")]
    answers: Vec<DohAnswer>,
}

#[derive(Deserialize)]
struct DohAnswer {
    #[serde(rename = "type")]
    record_type: u16,
    data: String,
}

/// Resolve only the configured Yingxu API origin through an HTTPS DNS query.
/// The bootstrap address is pinned so this lookup still works when the VPN's
/// configured recursive DNS servers cannot resolve the API hostname. TLS is
/// verified against `cloudflare-dns.com`; all other host lookups remain native.
fn resolve_api_host_over_doh(host: &str) -> Result<Vec<SocketAddr>, CloudApiError> {
    let mut url = Url::parse(DOH_ENDPOINT).map_err(|_| CloudApiError::InvalidEndpoint)?;
    url.query_pairs_mut()
        .append_pair("name", host)
        .append_pair("type", "A");
    let client = Client::builder()
        .https_only(true)
        .redirect(Policy::none())
        .timeout(Duration::from_secs(6))
        .no_proxy()
        .resolve_to_addrs(DOH_HOST, DOH_BOOTSTRAP_ADDRESSES)
        .build()
        .map_err(|_| CloudApiError::Unavailable)?;
    let mut response = client
        .get(url)
        .header(reqwest::header::ACCEPT, "application/dns-json")
        .send()
        .map_err(|_| CloudApiError::Unavailable)?;
    if !response.status().is_success()
        || response
            .content_length()
            .is_some_and(|length| length > MAX_DOH_RESPONSE_BYTES)
    {
        return Err(CloudApiError::Unavailable);
    }
    let mut bytes = Vec::new();
    (&mut response)
        .take(MAX_DOH_RESPONSE_BYTES + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| CloudApiError::Unavailable)?;
    if bytes.len() as u64 > MAX_DOH_RESPONSE_BYTES {
        return Err(CloudApiError::InvalidResponse);
    }
    let answer: DohResponse =
        serde_json::from_slice(&bytes).map_err(|_| CloudApiError::InvalidResponse)?;
    if answer.status != 0 {
        return Err(CloudApiError::Unavailable);
    }
    let mut addresses = answer
        .answers
        .into_iter()
        .filter(|record| record.record_type == 1 || record.record_type == 28)
        .filter_map(|record| {
            record
                .data
                .parse::<IpAddr>()
                .ok()
                .map(|ip| SocketAddr::new(ip, 443))
        })
        .collect::<Vec<_>>();
    addresses.sort_unstable();
    addresses.dedup();
    if addresses.is_empty() {
        return Err(CloudApiError::Unavailable);
    }
    Ok(addresses)
}

fn valid_route_id(value: &str) -> bool {
    !value.is_empty()
        && value != "."
        && value != ".."
        && value.len() <= 200
        && value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"-_.:".contains(&b))
}

fn valid_snapshot_id(value: &str) -> bool {
    value.len() == 36 && value.bytes().all(|b| b.is_ascii_hexdigit() || b == b'-')
}

fn valid_query(method: &str, route: &str, query: &str) -> bool {
    if query.is_empty() || query.len() > 100 {
        return false;
    }
    let route_allows_cursor = method == "GET"
        && (route == "/v1/sync/snapshot"
            || route.starts_with("/v1/sync/snapshots/")
            || route == "/v1/agent/session-deletions"
            || route.starts_with("/v1/agent/sessions/") && route.ends_with("/turns"));
    if !route_allows_cursor {
        return false;
    }
    let mut seen = std::collections::HashSet::new();
    for pair in query.split('&') {
        let Some((key, value)) = pair.split_once('=') else {
            return false;
        };
        if !matches!(key, "after_id" | "limit")
            || !seen.insert(key)
            || value.is_empty()
            || !value.bytes().all(|b| b.is_ascii_digit())
        {
            return false;
        }
        if key == "limit"
            && value
                .parse::<u16>()
                .map_or(true, |v| !(1..=500).contains(&v))
        {
            return false;
        }
    }
    true
}

struct NativeCloudApiTransport {
    client: Client,
}

impl CloudApiTransport for NativeCloudApiTransport {
    fn request_json(
        &self,
        method: &str,
        url: &Url,
        body: &[u8],
        bearer: Option<&str>,
    ) -> Result<serde_json::Value, CloudApiError> {
        if url.scheme() != "https"
            || url.host_str().is_none()
            || url.username() != ""
            || url.password().is_some()
            || !matches!(method, "GET" | "POST" | "PUT" | "DELETE")
            || url.fragment().is_some()
        {
            return Err(CloudApiError::InvalidEndpoint);
        }
        let method = reqwest::Method::from_bytes(method.as_bytes())
            .map_err(|_| CloudApiError::InvalidEndpoint)?;
        let mut request = self
            .client
            .request(method.clone(), url.clone())
            .header(reqwest::header::CONTENT_TYPE, "application/json");
        if method != reqwest::Method::GET {
            request = request.body(body.to_vec());
        }
        if let Some(token) = bearer {
            request = request.bearer_auth(token);
        }
        let mut response = request.send().map_err(|_| CloudApiError::Unavailable)?;
        if response.status().as_u16() == 401 || response.status().as_u16() == 403 {
            return Err(CloudApiError::Unauthenticated);
        }
        if !response.status().is_success()
            || response
                .content_length()
                .is_some_and(|length| length > MAX_RESPONSE_BYTES)
        {
            return Err(CloudApiError::Unavailable);
        }
        let mut bytes = Zeroizing::new(Vec::new());
        (&mut response)
            .take(MAX_RESPONSE_BYTES + 1)
            .read_to_end(&mut bytes)
            .map_err(|_| CloudApiError::Unavailable)?;
        if bytes.len() as u64 > MAX_RESPONSE_BYTES {
            return Err(CloudApiError::InvalidResponse);
        }
        serde_json::from_slice(&bytes).map_err(|_| CloudApiError::InvalidResponse)
    }
}

pub struct SystemCloudSessionStore;
impl CloudSessionStore for SystemCloudSessionStore {
    fn save(&self, session: &CloudSession) -> Result<(), CloudApiError> {
        let bytes = Zeroizing::new(
            serde_json::to_vec(session).map_err(|_| CloudApiError::SecureStoreUnavailable)?,
        );
        Entry::new(KEYRING_SERVICE, KEYRING_ACCOUNT)
            .map_err(|_| CloudApiError::SecureStoreUnavailable)?
            .set_secret(&bytes)
            .map_err(|_| CloudApiError::SecureStoreUnavailable)
    }
    fn load(&self) -> Result<Option<CloudSession>, CloudApiError> {
        let entry = Entry::new(KEYRING_SERVICE, KEYRING_ACCOUNT)
            .map_err(|_| CloudApiError::SecureStoreUnavailable)?;
        let bytes = match entry.get_secret() {
            Ok(bytes) => Zeroizing::new(bytes),
            Err(keyring::Error::NoEntry) => return Ok(None),
            Err(_) => return Err(CloudApiError::SecureStoreUnavailable),
        };
        serde_json::from_slice(&bytes)
            .map(Some)
            .map_err(|_| CloudApiError::SecureStoreUnavailable)
    }
    fn clear(&self) -> Result<(), CloudApiError> {
        let entry = Entry::new(KEYRING_SERVICE, KEYRING_ACCOUNT)
            .map_err(|_| CloudApiError::SecureStoreUnavailable)?;
        match entry.delete_credential() {
            Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
            Err(_) => Err(CloudApiError::SecureStoreUnavailable),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;
    struct FakeStore(Mutex<Option<CloudSession>>);
    impl CloudSessionStore for FakeStore {
        fn save(&self, session: &CloudSession) -> Result<(), CloudApiError> {
            *self.0.lock().unwrap() = Some(session.clone());
            Ok(())
        }
        fn load(&self) -> Result<Option<CloudSession>, CloudApiError> {
            Ok(self.0.lock().unwrap().clone())
        }
        fn clear(&self) -> Result<(), CloudApiError> {
            *self.0.lock().unwrap() = None;
            Ok(())
        }
    }
    struct FakeTransport {
        requests: Mutex<Vec<(String, Option<String>)>>,
    }
    impl CloudApiTransport for FakeTransport {
        fn request_json(
            &self,
            _method: &str,
            url: &Url,
            _body: &[u8],
            bearer: Option<&str>,
        ) -> Result<serde_json::Value, CloudApiError> {
            self.requests
                .lock()
                .unwrap()
                .push((url.path().to_owned(), bearer.map(str::to_owned)));
            if url.path() == EXCHANGE_PATH {
                Ok(
                    serde_json::json!({"accessToken":"secret","tokenType":"Bearer","expiresIn":900,"ownerId":"owner","deviceId":"device-generated","origin":"desktop","deviceAttested":false}),
                )
            } else {
                Ok(serde_json::json!({"revoked":true}))
            }
        }
    }
    #[test]
    fn api_origin_requires_https_root_origin() {
        assert!(CloudApiConfig::new("https://api.example.test").is_ok());
        for value in [
            "http://api.example.test",
            "https://api.example.test/path",
            "https://user@api.example.test",
            "https://api.example.test:444",
        ] {
            assert!(CloudApiConfig::new(value).is_err(), "{value}");
        }
    }
    #[test]
    fn baidu_token_bridge_is_native_only_and_requires_active_device_session() {
        let store = Arc::new(FakeStore(Mutex::new(None)));
        let transport = Arc::new(FakeTransport {
            requests: Mutex::new(Vec::new()),
        });
        let api = CloudApiClient::new(
            CloudApiConfig::new("https://api.example.test").unwrap(),
            transport.clone(),
            store,
        );
        assert!(api.baidu_broker("config", None).is_err());
        assert!(transport.requests.lock().unwrap().is_empty());
        assert!(api.request_url("POST", "/v1/netdisk/baidu/token").is_err());
        assert!(api.request_url("GET", "/v1/netdisk/baidu/config").is_err());
        api.exchange("synthetic-cloudbase", "Fixture").unwrap();
        api.baidu_broker("config", None).unwrap();
        api.baidu_broker("token",Some(&serde_json::json!({"grant_type":"authorization_code","credential":"synthetic-code"}))).unwrap();
        assert!(api.baidu_broker("https://attacker.invalid", None).is_err());
        let requests = transport.requests.lock().unwrap();
        assert_eq!(
            requests[1],
            ("/v1/netdisk/baidu/config".into(), Some("secret".into()))
        );
        assert_eq!(
            requests[2],
            ("/v1/netdisk/baidu/token".into(), Some("secret".into()))
        );
    }
    #[test]
    fn exchange_stores_short_session_without_exposing_token_and_logout_revokes_it() {
        let store = Arc::new(FakeStore(Mutex::new(None)));
        let transport = Arc::new(FakeTransport {
            requests: Mutex::new(Vec::new()),
        });
        let api = CloudApiClient::new(
            CloudApiConfig::new("https://api.example.test").unwrap(),
            transport.clone(),
            store.clone(),
        );
        let info = api.exchange("cloudbase-secret", "Camera").unwrap();
        assert_eq!(info.owner_id, "owner");
        assert_eq!(info.device_id, "device-generated");
        assert_eq!(
            api.current_session().unwrap().unwrap().device_id,
            "device-generated"
        );
        api.logout().unwrap();
        assert!(store.load().unwrap().is_none());
        let requests = transport.requests.lock().unwrap();
        assert_eq!(
            requests[0],
            (EXCHANGE_PATH.into(), Some("cloudbase-secret".into()))
        );
        assert_eq!(requests[1], (LOGOUT_PATH.into(), Some("secret".into())));
    }

    #[test]
    fn api_transport_allows_only_named_routes_and_bounded_cursor_queries() {
        let store = Arc::new(FakeStore(Mutex::new(Some(CloudSession {
            access_token: "session".into(),
            owner_id: "owner".into(),
            device_id: "device".into(),
            origin: "desktop".into(),
            display_name: "Fixture".into(),
            expires_in: 900,
            expires_at_unix: SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_secs()
                + 900,
        }))));
        let api = CloudApiClient::new(
            CloudApiConfig::new("https://api.example.test").unwrap(),
            Arc::new(FakeTransport {
                requests: Mutex::new(Vec::new()),
            }),
            store,
        );
        assert!(
            api.request_url("GET", "/v1/sync/snapshot?after_id=7&limit=50")
                .is_ok()
        );
        assert!(
            api.request_url("GET", "/v1/sync/snapshot?owner=other")
                .is_err()
        );
        assert!(
            api.request_url("POST", "/v1/sync/events?after_id=7")
                .is_err()
        );
        assert!(
            api.request_url("GET", "https://attacker.invalid/v1/devices")
                .is_err()
        );
        assert!(
            api.request_url("GET", "/v1/agent/sessions/../turns")
                .is_err()
        );
    }
}
