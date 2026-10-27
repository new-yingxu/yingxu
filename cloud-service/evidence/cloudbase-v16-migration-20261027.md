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
