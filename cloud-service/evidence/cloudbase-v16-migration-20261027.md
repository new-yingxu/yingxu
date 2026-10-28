# CloudBase v16 migration and function candidate

Date: 2026-10-27 (Asia/Shanghai)
Environment: `yingxu-dev` experience environment

## Database

- Applied migrations v11–v16 in order through the CloudBase PostgreSQL SQL editor. Each migration completed successfully and the `schema_migrations` table was read back after applying it.
- Read-back timestamps: v11 `19:09:26`, v12 `19:13:55`, v13 `19:17:03`, v14 `19:19:53`, v15 `19:22:45`, v16 `19:23:29` (+08:00).
- A read-only catalog check confirmed the new RPCs exist. `service_role` can execute them; `anon` and `authenticated` cannot. The `yingxu_clear_sync_data` routine is `SECURITY DEFINER`; its function owner is the CloudBase PostgreSQL owner. The routine was defined but never called.
- No synthetic fixture owners were cleaned, and no user or photo data was accessed.

## Local validation and candidate package

- Cloud service tests: Python 3.11, `138 passed, 130 skipped, 181 subtests passed`; skipped cases include live CloudBase/credential-dependent checks. One upstream Starlette deprecation warning.
- Function context source verification: build ID `5d5bb5be36c6702ed1f6af61ae2b95e565e2ec06662ec53e1c4271514c6a37ef`, app version `0.9.0-local-sync-preview`, schema version `16`, 2,738 files and 60 Python 3.11 Linux dependencies; source/lock/bootstrap matched.
- ZIP: `infra/cloud/function-packages/0.9.0-local-sync-preview-5d5bb5be36c6.zip`, 34,273,312 bytes, 2,739 entries, SHA-256 `7efeeb2e62464044c48526c4ca24b740e94e996508063e192dfe6bbccd707a27`. ZIP manifest read-back and synthetic empty-config credential scan passed.
- This is a local deployment candidate only. It has not been uploaded or deployed.

## Remaining cloud prerequisites

- Configure trusted HTTPS issuer/audience, session signing secrets (including key ID and optional verification-key overlap), registered client origins, CloudBase service credentials and HTTPS CORS origins in the function's server-side configuration. No secret values are recorded here or in chat.
- Current login decision (updated 2026-10-27): email verification code and username/password; new accounts register through email verification before setting username/password. SMS signature/template/provider setup remains pending, and WeChat login is paused. Do not enable or advertise SMS as usable until delivery has been configured and verified by the account owner.
- CloudBase CLI is not installed in this environment. Its console authorization asks for broad CloudBase resource access, so no authorization was granted. The SQL editor was used only for the specifically authorized v11–v16 migrations.
- Do not deploy until authentication and service secrets are configured and validated. No function deployment or production login/model call was performed.

## 2026-10-28 refreshed candidate and desktop origin decision

- Rechecked the previously listed ZIP against the current `cloud-service/app` and `contracts`; it no longer matched the current source, so it must not be deployed.
- Rebuilt the ignored local Linux/Python 3.11 function context from the current source and lock, then packaged it with an empty synthetic deployment config for credential scanning. Readback reports `sourceMatched=true`, `credentialsChecked=true`, 2,738 manifest files, 60 Linux dependencies, schema version 16, and `deployed=false`.
- Current candidate: `infra/cloud/function-packages/0.9.0-local-sync-preview-03c5fe6fc059.zip`; SHA-256 `e4099e10395faff6de02b6a8e42616f605ee2103a77eb9019d34be6a4a9bfedf`; size 34,273,635 bytes; 2,739 archive entries. No live function credentials were inspected or embedded.
- The stage-2 desktop-origin route uses native Rust HTTPS for both identity and app API, so no CloudBase Web security-source change or plan upgrade is required. The Windows and macOS app API origin is the same HTTP Gateway root; CloudBase Authentication has a separate official HTTP API origin. See `infra/cloud/console-configuration-checklist.md`.
- The live function remains the older `0.2.0-store-preview` deployment with `APP_ENV=production` and `AUTH_MODE=jwt`; server API Key injection is enabled and values remain masked. New CloudBase session configuration is not yet set. The candidate is not deployed.
