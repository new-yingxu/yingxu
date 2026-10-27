# Tauri host

## CloudBase native authentication

The native desktop authentication flow supports CloudBase username/password login, email verification-code login, email-code registration with a password, and sign-out. All CloudBase HTTP calls are made by Rust over HTTPS to fixed, allowlisted paths. Access and refresh tokens are stored in the operating-system credential store; one-time email verification codes and verification tokens are not persisted or returned to the WebView. Sign-out revokes the provider session before clearing local tokens. The browser preview continues to use mock authentication. IPC commands reject calls that do not originate from the `main` Tauri window.

The login hostname and CloudBase client ID are compile-time configuration, not credentials:

```sh
YINGXU_CLOUDBASE_AUTH_ORIGIN="https://<your-cloudbase-auth-domain>" \
YINGXU_CLOUDBASE_CLIENT_ID="<your-cloudbase-client-id>" \
cargo tauri build
```

Use the HTTPS origin only; do not include an API path, query, fragment, username, password, or nonstandard port. Both values are embedded at build time. They are intentionally absent from this repository and must be supplied by the release build environment. If either is absent or malformed, native sign-in fails closed with a configuration error. Do not put a CloudBase secret, API key, user password, or token in either variable.

CloudBase documents usernames as 2–48 ASCII characters, beginning with an ASCII letter or digit, with only letters, digits, `-_.:+ @` allowed thereafter. Email verification uses `/auth/v1/verification`, `/auth/v1/verification/verify`, and then the official sign-in or sign-up endpoint. Passwords and verification codes are sent only in HTTPS request bodies; request/response bodies and tokens are not logged. The native transport is HTTPS-only and does not follow redirects. Unit tests use synthetic responses only and do not send real email.

This verifies only a CloudBase provider identity. It does not exchange that identity for a Yingxu application session, enable account sync, or make the demonstration workspace data real. Those integrations remain separate work. Tests use fixture responses and do not contact CloudBase.
