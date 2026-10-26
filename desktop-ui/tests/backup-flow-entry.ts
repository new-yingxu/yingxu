// SSR bundle entry for the ui-slice-1 pure-frontend tests (no Tauri / Rust required).
export {
  JOB_CONTROL,
  canControl,
  canRetryTarget,
  jobStateToFlow,
  isTerminal,
  summarizeResult,
  canEnterStep,
} from "../src/api/backup-state";
export { mapJob, formatBytes } from "../src/api/format";
export type { RawJob } from "../src/api/format";
