# Tauri host

## Native CloudBase account and Yingxu cloud API

The native desktop authentication flow supports CloudBase username/password login, email verification-code login, email-code registration with a password, and sign-out. CloudBase and Yingxu cloud HTTP calls are made by Rust over HTTPS; the WebView never receives either provider or app bearer tokens. Provider access/refresh tokens and the separate 15-minute Yingxu JWT are stored in distinct operating-system credential entries. One-time verification codes/tokens are transient. Yingxu API paths are restricted to a native allowlist and redirects are disabled. IPC commands reject calls outside the `main` Tauri window.

The login hostname and CloudBase client ID are compile-time configuration, not credentials:

```sh
YINGXU_CLOUDBASE_AUTH_ORIGIN="https://<your-cloudbase-auth-domain>" \
YINGXU_CLOUDBASE_CLIENT_ID="<your-cloudbase-client-id>" \
YINGXU_CLOUD_API_ORIGIN="https://<your-yingxu-api-domain>" \
cargo tauri build
```

Use HTTPS origins only; do not include API paths, query, fragment, username, password, or nonstandard ports. These public endpoint/client identifiers are embedded at build time and are intentionally absent from the repository. If CloudBase values are absent, provider sign-in fails closed. If the Yingxu API origin is absent, provider verification may succeed but app-session exchange fails closed; the UI does not report the user signed in. Never put a CloudBase secret, API key, user password, or token in these variables.

CloudBase documents usernames as 2–48 ASCII characters, beginning with an ASCII letter or digit, with only letters, digits, `-_.:+ @` allowed thereafter. Email verification uses `/auth/v1/verification`, `/auth/v1/verification/verify`, and then the official sign-in or sign-up endpoint. Passwords and verification codes are sent only in HTTPS request bodies; request/response bodies and tokens are not logged. The native transport is HTTPS-only and does not follow redirects. Unit tests use synthetic responses only and do not send real email.

The exchange request sends only the CloudBase access token and a display name to `POST /v1/auth/session/exchange`; the service verifies the provider identity and creates the device ID. The short app token is saved in the OS credential store and expires after 900 seconds. On expiry the app asks the user to log in again; no refresh-token flow is configured. `POST /v1/auth/logout` revokes the device when online and the provider sign-out clears its separate token store.

`cloud_api_request` exposes only fixed API routes to the WebView. Account sync preferences read/write through `/v1/account/sync-preferences`; the account page's explicit “同步备份记录” action sends local SQLite outbox events to `/v1/sync/events`, remaps the event device ID to the server-issued session device ID, and marks events delivered only after server acceptance. Cloud Agent mode is off by default and requires the user to enable it; it sends only the submitted text to `/v1/agent/turns` with `useModel=false`, reads only already-synced backup summaries, and can restore the account's shared `default` conversation. No photo, original, local path, or tool execution is sent. Cloud model use still requires account-scoped provider credentials and explicit per-turn consent; no live model call is enabled here.

Tests use synthetic transport and local database fixtures only. No CloudBase account, email, photo, API secret, or deployed cloud endpoint was contacted. Live login/sync/Agent acceptance requires the final HTTPS API origin and CloudBase/client/server secrets to be configured by the owner in the approved console/build environment.
