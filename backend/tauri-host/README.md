# Tauri host

## CloudBase username/password identity slice

This native-only slice sends username/password login from Rust over HTTPS to CloudBase `POST /auth/v1/signin`, stores the returned tokens in the operating system credential store, and revokes the provider session before clearing the local tokens on sign-out. The browser preview continues to use mock authentication. IPC commands reject calls that do not originate from the `main` Tauri window.

The login hostname and CloudBase client ID are compile-time configuration, not credentials:

```sh
YINGXU_CLOUDBASE_AUTH_ORIGIN="https://<your-cloudbase-auth-domain>" \
YINGXU_CLOUDBASE_CLIENT_ID="<your-cloudbase-client-id>" \
cargo tauri build
```

Use the HTTPS origin only; do not include an API path, query, fragment, username, password, or nonstandard port. Both values are embedded at build time. They are intentionally absent from this repository and must be supplied by the release build environment. If either is absent or malformed, native sign-in fails closed with a configuration error. Do not put a CloudBase secret, API key, user password, or token in either variable.

CloudBase documents usernames as 2–48 ASCII characters, beginning with an ASCII letter or digit, with only letters, digits, `-_.:+ @` allowed thereafter. Passwords are sent only in the HTTPS request body; request/response bodies and tokens are not logged. The native transport is HTTPS-only and does not follow redirects.

This verifies only a CloudBase provider identity. It does not exchange that identity for a Yingxu application session, enable account sync, or make the demonstration workspace data real. Those integrations remain separate work. Tests use fixture responses and do not contact CloudBase.
