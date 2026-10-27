use keyring::Entry;
use reqwest::{Url, blocking::Client, redirect::Policy};
use serde::{Deserialize, Serialize};
use std::{fmt, io::Read, sync::Arc, time::Duration};
use zeroize::{Zeroize, ZeroizeOnDrop, Zeroizing};

const AUTH_PATH: &str = "/auth/v1/signin";
const SIGNOUT_PATH: &str = "/auth/v1/user/signout";
const EMAIL_VERIFICATION_PATH: &str = "/auth/v1/verification";
const VERIFY_EMAIL_PATH: &str = "/auth/v1/verification/verify";
const SIGNUP_PATH: &str = "/auth/v1/signup";
const KEYRING_SERVICE: &str = "app.yingxu.desktop.cloudbase";
const KEYRING_ACCOUNT: &str = "user-session-v1";
const MAX_RESPONSE_BYTES: u64 = 64 * 1024;

#[derive(Clone)]
pub struct AuthConfig {
    origin: Url,
    client_id: String,
}

impl AuthConfig {
    pub fn new(origin: &str, client_id: &str) -> Result<Self, AuthError> {
        let origin = Url::parse(origin).map_err(|_| AuthError::InvalidConfiguration)?;
        if origin.scheme() != "https"
            || origin.host_str().is_none()
            || !origin.username().is_empty()
            || origin.password().is_some()
            || origin.port_or_known_default() != Some(443)
            || !matches!(origin.path(), "" | "/")
            || origin.query().is_some()
            || origin.fragment().is_some()
            || client_id.trim().is_empty()
            || client_id.len() > 500
        {
            return Err(AuthError::InvalidConfiguration);
        }
        Ok(Self {
            origin,
            client_id: client_id.to_owned(),
        })
    }

    fn from_embedded_config() -> Result<Self, AuthError> {
        let origin = option_env!("YINGXU_CLOUDBASE_AUTH_ORIGIN").ok_or(AuthError::NotConfigured)?;
        let client_id =
            option_env!("YINGXU_CLOUDBASE_CLIENT_ID").ok_or(AuthError::NotConfigured)?;
        Self::new(origin, client_id)
    }

    fn sign_in_url(&self) -> Result<Url, AuthError> {
        let mut url = self
            .origin
            .join(AUTH_PATH)
            .map_err(|_| AuthError::InvalidConfiguration)?;
        url.query_pairs_mut()
            .append_pair("client_id", &self.client_id);
        Ok(url)
    }

    fn sign_out_url(&self) -> Result<Url, AuthError> {
        let mut url = self
            .origin
            .join(SIGNOUT_PATH)
            .map_err(|_| AuthError::InvalidConfiguration)?;
        url.query_pairs_mut()
            .append_pair("client_id", &self.client_id);
        Ok(url)
    }

    fn endpoint_url(&self, path: &str) -> Result<Url, AuthError> {
        let mut url = self
            .origin
            .join(path)
            .map_err(|_| AuthError::InvalidConfiguration)?;
        url.query_pairs_mut()
            .append_pair("client_id", &self.client_id);
        Ok(url)
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum AuthError {
    NotConfigured,
    InvalidConfiguration,
    InvalidInput,
    InvalidEndpoint,
    AuthenticationRejected,
    ServiceUnavailable,
    InvalidResponse,
    SecureStoreUnavailable,
}

impl AuthError {
    pub fn public_message(self) -> &'static str {
        match self {
            Self::NotConfigured => "尚未配置影序 CloudBase 登录域名。",
            Self::InvalidConfiguration | Self::InvalidEndpoint => "登录服务地址配置无效。",
            Self::InvalidInput => "请填写有效的账号和密码。",
            Self::AuthenticationRejected => "登录失败，请检查账号信息或稍后重试。",
            Self::ServiceUnavailable => "登录服务暂不可用，请稍后重试。",
            Self::InvalidResponse => "登录服务返回无效响应。",
            Self::SecureStoreUnavailable => "系统凭据存储不可用，未保存登录状态。",
        }
    }
}

impl fmt::Display for AuthError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.public_message())
    }
}

pub trait AuthTransport: Send + Sync {
    fn post_json(
        &self,
        url: &Url,
        body: &[u8],
        bearer_token: Option<&str>,
    ) -> Result<serde_json::Value, AuthError>;
}

pub trait TokenStore: Send + Sync {
    fn save(&self, tokens: &AuthTokens) -> Result<(), AuthError>;
    fn load(&self) -> Result<Option<AuthTokens>, AuthError>;
    fn clear(&self) -> Result<(), AuthError>;
}

#[derive(Clone, Serialize, Deserialize, Zeroize, ZeroizeOnDrop)]
pub struct AuthTokens {
    access_token: String,
    refresh_token: String,
    expires_in: u64,
    subject: String,
}

impl fmt::Debug for AuthTokens {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("AuthTokens")
            .field("access_token", &"[REDACTED]")
            .field("refresh_token", &"[REDACTED]")
            .field("expires_in", &self.expires_in)
            .field("subject", &"[REDACTED]")
            .finish()
    }
}

#[derive(Serialize)]
struct PasswordGrant<'a> {
    username: &'a str,
    password: &'a str,
}

#[derive(Serialize)]
struct EmailCodeRequest<'a> {
    email: &'a str,
    target: &'static str,
}

#[derive(Deserialize)]
struct EmailCodeResponse {
    verification_id: String,
    expires_in: u64,
}

#[derive(Serialize)]
struct VerifyEmailCodeRequest<'a> {
    verification_id: &'a str,
    verification_code: &'a str,
}

#[derive(Deserialize, Zeroize, ZeroizeOnDrop)]
struct VerificationToken {
    verification_token: String,
    #[zeroize(skip)]
    expires_in: u64,
}

#[derive(Serialize)]
struct EmailCodeLogin<'a> {
    verification_token: &'a str,
}

#[derive(Serialize)]
struct EmailCodeSignup<'a> {
    email: &'a str,
    password: &'a str,
    verification_token: &'a str,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct EmailCodeChallenge {
    pub verification_id: String,
    pub expires_in: u64,
}

#[derive(Deserialize)]
struct TokenResponse {
    token_type: String,
    access_token: String,
    refresh_token: String,
    expires_in: u64,
    sub: String,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AuthIdentity {
    pub subject: String,
    pub expires_in: u64,
}

pub struct CloudBaseAuthClient {
    config: AuthConfig,
    transport: Arc<dyn AuthTransport>,
    token_store: Arc<dyn TokenStore>,
}

impl CloudBaseAuthClient {
    pub fn new(
        config: AuthConfig,
        transport: Arc<dyn AuthTransport>,
        token_store: Arc<dyn TokenStore>,
    ) -> Self {
        Self {
            config,
            transport,
            token_store,
        }
    }

    pub fn from_embedded_config() -> Result<Self, AuthError> {
        let config = AuthConfig::from_embedded_config()?;
        let transport = Arc::new(NativeHttpsTransport::new(
            config.origin.clone(),
            config.client_id.clone(),
        )?);
        Ok(Self::new(config, transport, Arc::new(SystemTokenStore)))
    }

    pub fn password_sign_in(
        &self,
        username: &str,
        password: &str,
    ) -> Result<AuthIdentity, AuthError> {
        if !valid_username(username) || password.is_empty() {
            return Err(AuthError::InvalidInput);
        }
        let url = self.config.sign_in_url()?;
        let request = PasswordGrant { username, password };
        let body =
            Zeroizing::new(serde_json::to_vec(&request).map_err(|_| AuthError::InvalidInput)?);
        let response = self.transport.post_json(&url, &body, None)?;
        let token_response: TokenResponse =
            serde_json::from_value(response).map_err(|_| AuthError::InvalidResponse)?;
        if !token_response.token_type.eq_ignore_ascii_case("bearer")
            || token_response.access_token.is_empty()
            || token_response.refresh_token.is_empty()
            || token_response.expires_in == 0
            || token_response.sub.is_empty()
        {
            return Err(AuthError::InvalidResponse);
        }
        let identity = AuthIdentity {
            subject: token_response.sub.clone(),
            expires_in: token_response.expires_in,
        };
        let tokens = AuthTokens {
            access_token: token_response.access_token,
            refresh_token: token_response.refresh_token,
            expires_in: token_response.expires_in,
            subject: token_response.sub,
        };
        self.token_store.save(&tokens)?;
        Ok(identity)
    }

    pub fn request_email_code(
        &self,
        email: &str,
        for_signup: bool,
    ) -> Result<EmailCodeChallenge, AuthError> {
        if !valid_email(email) {
            return Err(AuthError::InvalidInput);
        }
        let url = self.config.endpoint_url(EMAIL_VERIFICATION_PATH)?;
        let request = EmailCodeRequest {
            email,
            target: if for_signup { "ANY" } else { "USER" },
        };
        let body =
            Zeroizing::new(serde_json::to_vec(&request).map_err(|_| AuthError::InvalidInput)?);
        let response = self.transport.post_json(&url, &body, None)?;
        let challenge: EmailCodeResponse =
            serde_json::from_value(response).map_err(|_| AuthError::InvalidResponse)?;
        if challenge.verification_id.is_empty()
            || challenge.verification_id.len() > 500
            || challenge.expires_in == 0
        {
            return Err(AuthError::InvalidResponse);
        }
        Ok(EmailCodeChallenge {
            verification_id: challenge.verification_id,
            expires_in: challenge.expires_in,
        })
    }

    pub fn email_code_sign_in(
        &self,
        email: &str,
        verification_id: &str,
        verification_code: &str,
    ) -> Result<AuthIdentity, AuthError> {
        if !valid_email(email) || !valid_verification_input(verification_id, verification_code) {
            return Err(AuthError::InvalidInput);
        }
        let verification = self.verify_email_code(verification_id, verification_code)?;
        let url = self.config.sign_in_url()?;
        let request = EmailCodeLogin {
            verification_token: &verification.verification_token,
        };
        let body =
            Zeroizing::new(serde_json::to_vec(&request).map_err(|_| AuthError::InvalidInput)?);
        let response = self.transport.post_json(&url, &body, None)?;
        self.store_token_response(response)
    }

    pub fn email_code_sign_up(
        &self,
        email: &str,
        password: &str,
        verification_id: &str,
        verification_code: &str,
    ) -> Result<AuthIdentity, AuthError> {
        if !valid_email(email)
            || password.is_empty()
            || password.len() > 1024
            || !valid_verification_input(verification_id, verification_code)
        {
            return Err(AuthError::InvalidInput);
        }
        let verification = self.verify_email_code(verification_id, verification_code)?;
        let url = self.config.endpoint_url(SIGNUP_PATH)?;
        let request = EmailCodeSignup {
            email,
            password,
            verification_token: &verification.verification_token,
        };
        let body =
            Zeroizing::new(serde_json::to_vec(&request).map_err(|_| AuthError::InvalidInput)?);
        let response = self.transport.post_json(&url, &body, None)?;
        self.store_token_response(response)
    }

    fn verify_email_code(
        &self,
        verification_id: &str,
        verification_code: &str,
    ) -> Result<VerificationToken, AuthError> {
        let url = self.config.endpoint_url(VERIFY_EMAIL_PATH)?;
        let request = VerifyEmailCodeRequest {
            verification_id,
            verification_code,
        };
        let body =
            Zeroizing::new(serde_json::to_vec(&request).map_err(|_| AuthError::InvalidInput)?);
        let response = self.transport.post_json(&url, &body, None)?;
        let token: VerificationToken =
            serde_json::from_value(response).map_err(|_| AuthError::InvalidResponse)?;
        if token.verification_token.is_empty() || token.expires_in == 0 {
            return Err(AuthError::InvalidResponse);
        }
        Ok(token)
    }

    fn store_token_response(&self, response: serde_json::Value) -> Result<AuthIdentity, AuthError> {
        let token_response: TokenResponse =
            serde_json::from_value(response).map_err(|_| AuthError::InvalidResponse)?;
        if !token_response.token_type.eq_ignore_ascii_case("bearer")
            || token_response.access_token.is_empty()
            || token_response.refresh_token.is_empty()
            || token_response.expires_in == 0
            || token_response.sub.is_empty()
        {
            return Err(AuthError::InvalidResponse);
        }
        let identity = AuthIdentity {
            subject: token_response.sub.clone(),
            expires_in: token_response.expires_in,
        };
        let tokens = AuthTokens {
            access_token: token_response.access_token,
            refresh_token: token_response.refresh_token,
            expires_in: token_response.expires_in,
            subject: token_response.sub,
        };
        self.token_store.save(&tokens)?;
        Ok(identity)
    }

    fn post_to(&self, url: &str, body: &serde_json::Value) -> Result<(), AuthError> {
        let url = Url::parse(url).map_err(|_| AuthError::InvalidEndpoint)?;
        validate_auth_endpoint(&self.config.origin, &url, &self.config.client_id)?;
        self.transport.post_json(
            &url,
            &serde_json::to_vec(body).map_err(|_| AuthError::InvalidResponse)?,
            None,
        )?;
        Ok(())
    }

    pub fn sign_out(&self) -> Result<(), AuthError> {
        let Some(tokens) = self.token_store.load()? else {
            return self.token_store.clear();
        };
        let url = self.config.sign_out_url()?;
        validate_auth_endpoint(&self.config.origin, &url, &self.config.client_id)?;
        self.transport
            .post_json(&url, b"{}", Some(&tokens.access_token))?;
        self.token_store.clear()
    }
}

fn valid_username(username: &str) -> bool {
    let bytes = username.as_bytes();
    (2..=48).contains(&bytes.len())
        && bytes[0].is_ascii_alphanumeric()
        && bytes
            .iter()
            .all(|byte| byte.is_ascii_alphanumeric() || b"-_.:+ @".contains(byte))
}

fn valid_email(email: &str) -> bool {
    !email.is_empty()
        && email.len() <= 254
        && !email.chars().any(char::is_whitespace)
        && email.contains('@')
        && email.split_once('@').is_some_and(|(local, domain)| {
            !local.is_empty() && !domain.is_empty() && domain.contains('.')
        })
}

fn valid_verification_input(verification_id: &str, code: &str) -> bool {
    !verification_id.trim().is_empty()
        && verification_id.len() <= 500
        && code.len() == 6
        && code.bytes().all(|byte| byte.is_ascii_digit())
}

fn validate_auth_endpoint(origin: &Url, target: &Url, client_id: &str) -> Result<(), AuthError> {
    let query = target.query_pairs().collect::<Vec<_>>();
    let allowed_path = matches!(
        target.path(),
        AUTH_PATH | SIGNOUT_PATH | EMAIL_VERIFICATION_PATH | VERIFY_EMAIL_PATH | SIGNUP_PATH
    );
    if origin.scheme() != "https"
        || target.scheme() != "https"
        || target.host_str() != origin.host_str()
        || target.port_or_known_default() != origin.port_or_known_default()
        || !allowed_path
        || target.username() != ""
        || target.password().is_some()
        || target.fragment().is_some()
        || query.len() != 1
        || query[0].0 != "client_id"
        || query[0].1 != client_id
    {
        return Err(AuthError::InvalidEndpoint);
    }
    Ok(())
}

pub struct NativeHttpsTransport {
    origin: Url,
    client_id: String,
    client: Client,
}

impl NativeHttpsTransport {
    fn new(origin: Url, client_id: String) -> Result<Self, AuthError> {
        let client = Client::builder()
            .https_only(true)
            .redirect(Policy::none())
            .timeout(Duration::from_secs(15))
            .no_proxy()
            .build()
            .map_err(|_| AuthError::ServiceUnavailable)?;
        Ok(Self {
            origin,
            client_id,
            client,
        })
    }
}

impl AuthTransport for NativeHttpsTransport {
    fn post_json(
        &self,
        url: &Url,
        body: &[u8],
        bearer_token: Option<&str>,
    ) -> Result<serde_json::Value, AuthError> {
        validate_auth_endpoint(&self.origin, url, &self.client_id)?;
        let mut request = self
            .client
            .post(url.clone())
            .header(reqwest::header::CONTENT_TYPE, "application/json")
            .body(body.to_vec());
        if let Some(token) = bearer_token {
            request = request.bearer_auth(token);
        }
        let mut response = request.send().map_err(|_| AuthError::ServiceUnavailable)?;
        if !response.status().is_success()
            || response
                .content_length()
                .is_some_and(|length| length > MAX_RESPONSE_BYTES)
        {
            return Err(AuthError::AuthenticationRejected);
        }
        let mut limited = (&mut response).take(MAX_RESPONSE_BYTES + 1);
        let mut bytes = Zeroizing::new(Vec::new());
        limited
            .read_to_end(&mut bytes)
            .map_err(|_| AuthError::ServiceUnavailable)?;
        if bytes.len() as u64 > MAX_RESPONSE_BYTES {
            return Err(AuthError::InvalidResponse);
        }
        serde_json::from_slice(&bytes).map_err(|_| AuthError::InvalidResponse)
    }
}

pub struct SystemTokenStore;

impl TokenStore for SystemTokenStore {
    fn save(&self, tokens: &AuthTokens) -> Result<(), AuthError> {
        let mut bytes = Zeroizing::new(
            serde_json::to_vec(tokens).map_err(|_| AuthError::SecureStoreUnavailable)?,
        );
        Entry::new(KEYRING_SERVICE, KEYRING_ACCOUNT)
            .map_err(|_| AuthError::SecureStoreUnavailable)?
            .set_secret(&bytes)
            .map_err(|_| AuthError::SecureStoreUnavailable)?;
        bytes.zeroize();
        Ok(())
    }

    fn load(&self) -> Result<Option<AuthTokens>, AuthError> {
        let entry = Entry::new(KEYRING_SERVICE, KEYRING_ACCOUNT)
            .map_err(|_| AuthError::SecureStoreUnavailable)?;
        let mut bytes = match entry.get_secret() {
            Ok(bytes) => Zeroizing::new(bytes),
            Err(keyring::Error::NoEntry) => return Ok(None),
            Err(_) => return Err(AuthError::SecureStoreUnavailable),
        };
        let tokens =
            serde_json::from_slice(&bytes).map_err(|_| AuthError::SecureStoreUnavailable)?;
        bytes.zeroize();
        Ok(Some(tokens))
    }

    fn clear(&self) -> Result<(), AuthError> {
        let entry = Entry::new(KEYRING_SERVICE, KEYRING_ACCOUNT)
            .map_err(|_| AuthError::SecureStoreUnavailable)?;
        match entry.delete_credential() {
            Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
            Err(_) => Err(AuthError::SecureStoreUnavailable),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::{
        collections::VecDeque,
        sync::{Arc, Mutex},
    };

    #[derive(Default)]
    struct MockTransport {
        request: Mutex<Option<(String, serde_json::Value)>>,
        response: Mutex<Option<Result<serde_json::Value, AuthError>>>,
    }

    impl AuthTransport for MockTransport {
        fn post_json(
            &self,
            url: &reqwest::Url,
            body: &[u8],
            bearer_token: Option<&str>,
        ) -> Result<serde_json::Value, AuthError> {
            *self.request.lock().unwrap() = Some((
                format!("{}|bearer={}", url, bearer_token.unwrap_or("")),
                serde_json::from_slice(body).unwrap(),
            ));
            self.response.lock().unwrap().take().unwrap()
        }
    }

    #[derive(Default)]
    struct SequenceTransport {
        requests: Mutex<Vec<(String, serde_json::Value)>>,
        responses: Mutex<VecDeque<Result<serde_json::Value, AuthError>>>,
    }

    impl AuthTransport for SequenceTransport {
        fn post_json(
            &self,
            url: &reqwest::Url,
            body: &[u8],
            bearer_token: Option<&str>,
        ) -> Result<serde_json::Value, AuthError> {
            self.requests.lock().unwrap().push((
                format!("{}|bearer={}", url, bearer_token.unwrap_or("")),
                serde_json::from_slice(body).unwrap(),
            ));
            self.responses.lock().unwrap().pop_front().unwrap()
        }
    }

    #[derive(Default)]
    struct MemoryTokenStore(Mutex<Option<AuthTokens>>);

    impl TokenStore for MemoryTokenStore {
        fn save(&self, tokens: &AuthTokens) -> Result<(), AuthError> {
            *self.0.lock().unwrap() = Some(tokens.clone());
            Ok(())
        }

        fn load(&self) -> Result<Option<AuthTokens>, AuthError> {
            Ok(self.0.lock().unwrap().clone())
        }

        fn clear(&self) -> Result<(), AuthError> {
            self.0.lock().unwrap().take();
            Ok(())
        }
    }

    fn fixture_client(
        response: Result<serde_json::Value, AuthError>,
    ) -> (
        CloudBaseAuthClient,
        Arc<MockTransport>,
        Arc<MemoryTokenStore>,
    ) {
        let transport = Arc::new(MockTransport::default());
        *transport.response.lock().unwrap() = Some(response);
        let tokens = Arc::new(MemoryTokenStore::default());
        let config = AuthConfig::new("https://auth.example.test", "desktop-client").unwrap();
        let client = CloudBaseAuthClient::new(config, transport.clone(), tokens.clone());
        (client, transport, tokens)
    }

    fn sequence_client(
        responses: impl IntoIterator<Item = Result<serde_json::Value, AuthError>>,
    ) -> (
        CloudBaseAuthClient,
        Arc<SequenceTransport>,
        Arc<MemoryTokenStore>,
    ) {
        let transport = Arc::new(SequenceTransport::default());
        *transport.responses.lock().unwrap() = responses.into_iter().collect();
        let tokens = Arc::new(MemoryTokenStore::default());
        let config = AuthConfig::new("https://auth.example.test", "desktop-client").unwrap();
        let client = CloudBaseAuthClient::new(config, transport.clone(), tokens.clone());
        (client, transport, tokens)
    }

    #[test]
    fn password_sign_in_uses_only_fixed_signin_endpoint_and_stores_tokens() {
        let (client, transport, tokens) = fixture_client(Ok(json!({
            "token_type": "Bearer",
            "access_token": "access-fixture",
            "refresh_token": "refresh-fixture",
            "expires_in": 7200,
            "sub": "user-fixture"
        })));

        let user = client
            .password_sign_in("nanyu@example.test", "password-fixture")
            .unwrap();
        assert_eq!(user.subject, "user-fixture");
        let (url, request) = transport.request.lock().unwrap().clone().unwrap();
        assert_eq!(
            url,
            "https://auth.example.test/auth/v1/signin?client_id=desktop-client|bearer="
        );
        assert_eq!(
            request,
            json!({"username":"nanyu@example.test","password":"password-fixture"})
        );
        let stored = tokens.load().unwrap().unwrap();
        assert_eq!(stored.access_token, "access-fixture");
        assert_eq!(stored.refresh_token, "refresh-fixture");
    }

    #[test]
    fn username_validation_matches_cloudbase_documented_constraints() {
        let too_long = "x".repeat(49);
        for username in ["ab", "A9", "user.name", "u+ser@example.test", "user name"] {
            assert!(
                valid_username(username),
                "rejected valid username {username:?}"
            );
        }
        for username in [
            "",
            "a",
            "_starts-with-symbol",
            "中 文",
            "has/slash",
            too_long.as_str(),
        ] {
            assert!(
                !valid_username(username),
                "accepted invalid username {username:?}"
            );
        }
    }

    #[test]
    fn invalid_username_is_rejected_before_any_network_request() {
        let (client, transport, tokens) = fixture_client(Ok(json!({
            "token_type": "Bearer", "access_token": "a", "refresh_token": "r", "expires_in": 60, "sub": "u"
        })));
        assert_eq!(
            client.password_sign_in("用户", "password").unwrap_err(),
            AuthError::InvalidInput
        );
        assert!(transport.request.lock().unwrap().is_none());
        assert!(tokens.load().unwrap().is_none());
    }

    #[test]
    fn email_code_challenge_uses_user_or_any_target_and_validates_response() {
        let (client, transport, _) = sequence_client([
            Ok(json!({"verification_id":"verify-fixture","expires_in":600,"is_user":true})),
            Ok(json!({"verification_id":"signup-fixture","expires_in":600,"is_user":false})),
        ]);
        let existing = client
            .request_email_code("user@example.com", false)
            .unwrap();
        let new_user = client.request_email_code("new@example.com", true).unwrap();
        assert_eq!(existing.verification_id, "verify-fixture");
        assert_eq!(existing.expires_in, 600);
        assert_eq!(new_user.verification_id, "signup-fixture");
        let requests = transport.requests.lock().unwrap();
        assert_eq!(
            requests[0].0,
            "https://auth.example.test/auth/v1/verification?client_id=desktop-client|bearer="
        );
        assert_eq!(
            requests[0].1,
            json!({"email":"user@example.com","target":"USER"})
        );
        assert_eq!(
            requests[1].1,
            json!({"email":"new@example.com","target":"ANY"})
        );
    }

    #[test]
    fn email_code_sign_in_keeps_verification_token_in_native_layer() {
        let (client, transport, tokens) = sequence_client([
            Ok(json!({"verification_token":"verification-secret","expires_in":600})),
            Ok(
                json!({"token_type":"Bearer","access_token":"access-fixture","refresh_token":"refresh-fixture","expires_in":7200,"sub":"user-fixture"}),
            ),
        ]);
        let identity = client
            .email_code_sign_in("user@example.com", "verify-id", "123456")
            .unwrap();
        assert_eq!(identity.subject, "user-fixture");
        let requests = transport.requests.lock().unwrap();
        assert_eq!(
            requests[0].0,
            "https://auth.example.test/auth/v1/verification/verify?client_id=desktop-client|bearer="
        );
        assert_eq!(
            requests[0].1,
            json!({"verification_id":"verify-id","verification_code":"123456"})
        );
        assert_eq!(
            requests[1].1,
            json!({"verification_token":"verification-secret"})
        );
        assert!(tokens.load().unwrap().is_some());
    }

    #[test]
    fn email_signup_requires_verified_code_and_stores_only_session_tokens() {
        let (client, transport, tokens) = sequence_client([
            Ok(json!({"verification_token":"verification-secret","expires_in":600})),
            Ok(
                json!({"token_type":"Bearer","access_token":"access-fixture","refresh_token":"refresh-fixture","expires_in":7200,"sub":"new-user"}),
            ),
        ]);
        client
            .email_code_sign_up("new@example.com", "password-fixture", "verify-id", "123456")
            .unwrap();
        let requests = transport.requests.lock().unwrap();
        assert_eq!(
            requests[1].0,
            "https://auth.example.test/auth/v1/signup?client_id=desktop-client|bearer="
        );
        assert_eq!(
            requests[1].1,
            json!({"email":"new@example.com","password":"password-fixture","verification_token":"verification-secret"})
        );
        assert_eq!(tokens.load().unwrap().unwrap().subject, "new-user");
    }

    #[test]
    fn email_code_inputs_are_rejected_before_network_and_malformed_tokens_are_not_saved() {
        let (client, transport, tokens) =
            sequence_client([Ok(json!({"verification_token":"" ,"expires_in":0}))]);
        assert_eq!(
            client.request_email_code("bad", false).unwrap_err(),
            AuthError::InvalidInput
        );
        assert_eq!(
            client
                .email_code_sign_in("user@example.com", "", "123456")
                .unwrap_err(),
            AuthError::InvalidInput
        );
        assert!(transport.requests.lock().unwrap().is_empty());
        assert_eq!(
            client
                .email_code_sign_in("user@example.com", "verify-id", "12a456")
                .unwrap_err(),
            AuthError::InvalidInput
        );
        assert_eq!(
            client
                .email_code_sign_in("user@example.com", "verify-id", "123456")
                .unwrap_err(),
            AuthError::InvalidResponse
        );
        assert!(tokens.load().unwrap().is_none());
    }

    #[test]
    fn invalid_https_origin_and_path_are_rejected_before_transport() {
        for base in [
            "http://auth.example.test",
            "https://user:pass@auth.example.test",
            "https://auth.example.test/untrusted-prefix",
            "https://auth.example.test:444",
        ] {
            assert!(
                AuthConfig::new(base, "desktop-client").is_err(),
                "accepted {base}"
            );
        }

        let (client, transport, _) = fixture_client(Ok(json!({
            "token_type": "Bearer", "access_token": "a", "refresh_token": "r", "expires_in": 60, "sub": "u"
        })));
        assert!(
            client
                .post_to(
                    "http://auth.example.test/auth/v1/signin?client_id=desktop-client",
                    &json!({})
                )
                .is_err()
        );
        assert!(
            client
                .post_to(
                    "https://other.example.test/auth/v1/signin?client_id=desktop-client",
                    &json!({})
                )
                .is_err()
        );
        assert!(
            client
                .post_to(
                    "https://auth.example.test/auth/v1/token?client_id=desktop-client",
                    &json!({})
                )
                .is_err()
        );
        assert!(
            client
                .post_to(
                    "https://auth.example.test/auth/v1/signin?client_id=other-client",
                    &json!({})
                )
                .is_err()
        );
        assert!(transport.request.lock().unwrap().is_none());
    }

    #[test]
    fn failed_login_returns_sanitized_error_and_does_not_store_tokens() {
        let (client, transport, tokens) = fixture_client(Err(AuthError::ServiceUnavailable));
        let err = client
            .password_sign_in("private-user", "private-password")
            .err()
            .unwrap();
        let rendered = err.to_string();
        assert!(!rendered.contains("private-user"));
        assert!(!rendered.contains("private-password"));
        assert!(!format!("{err:?}").contains("private-password"));
        assert!(tokens.load().unwrap().is_none());
        assert_eq!(
            transport.request.lock().unwrap().as_ref().unwrap().1["password"],
            "private-password"
        );
    }

    #[test]
    fn malformed_auth_response_is_sanitized_and_never_persisted() {
        let (client, _, tokens) = fixture_client(Ok(json!({
            "access_token": "partial-token",
            "refresh_token": "",
            "expires_in": 0,
            "sub": "user-fixture"
        })));
        assert!(
            client
                .password_sign_in("user@example.test", "password-fixture")
                .is_err()
        );
        assert!(tokens.load().unwrap().is_none());
    }

    #[test]
    fn token_store_roundtrips_and_clears_tokens() {
        let (_, _, tokens) = fixture_client(Err(AuthError::ServiceUnavailable));
        let fixture = AuthTokens {
            access_token: "a".into(),
            refresh_token: "r".into(),
            expires_in: 60,
            subject: "sub".into(),
        };
        tokens.save(&fixture).unwrap();
        assert_eq!(tokens.load().unwrap().unwrap().subject, "sub");
        tokens.clear().unwrap();
        assert!(tokens.load().unwrap().is_none());
    }

    #[test]
    fn sign_out_revokes_cloud_token_before_clearing_local_store() {
        let (client, transport, tokens) = fixture_client(Ok(json!({
            "redirect_uri": "https://auth.example.test/logout"
        })));
        tokens
            .save(&AuthTokens {
                access_token: "access-fixture".into(),
                refresh_token: "refresh-fixture".into(),
                expires_in: 7200,
                subject: "user-fixture".into(),
            })
            .unwrap();
        client.sign_out().unwrap();
        let (url, body) = transport.request.lock().unwrap().clone().unwrap();
        assert_eq!(
            url,
            "https://auth.example.test/auth/v1/user/signout?client_id=desktop-client|bearer=access-fixture"
        );
        assert_eq!(body, json!({}));
        assert!(tokens.load().unwrap().is_none());
    }
}
