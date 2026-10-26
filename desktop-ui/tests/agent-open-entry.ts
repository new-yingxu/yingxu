// SSR bundle entry for the ui-slice-2 agent confirmation tests.
export { default as AgentActionCard } from "../src/components/AgentActionCard.vue";
export {
  ACTION_WINDOW_MS,
  OPEN_PROPOSAL_SOURCE,
  createLocalAgentService,
} from "../src/api/local-agent";
export type {
  LocalActionReceipt,
  LocalOpenPlan,
} from "../src/api/local-agent";
