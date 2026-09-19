/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Only variable exposed to the bundle. Treat it as public. */
  readonly VITE_API_BASE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
