/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_CLOUDBASE_ENV_ID?: string;
  readonly VITE_CLOUDBASE_REGION?: string;
  readonly VITE_CLOUDBASE_CLIENT_ID?: string;
  readonly VITE_CLOUDBASE_ALLOWED_ORIGINS?: string;
  readonly VITE_CLOUDBASE_AUTH_READY?: string;
  readonly VITE_IDENTITY_API_BASE_URL?: string;
  readonly VITE_IDENTITY_API_READY?: string;
}
