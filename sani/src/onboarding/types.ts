// Wire contract mirroring the Rust snake_case types in onboarding.rs /
// setup.rs. The Rust side owns this state; the UI only reflects it.

export type ComponentStatus = "pending" | "running" | "complete" | "failed" | "skipped";

export interface ComponentView {
  step: string;
  friendly: string;
  status: ComponentStatus;
  detail: string;
  error: string | null;
}

export interface SetupSnapshot {
  schema_version: number;
  onboarding_complete: boolean;
  current_stage: string;
  percent: number;
  local_setup_complete: boolean;
  components: ComponentView[];
}

export interface SetupProgressEvent {
  step: string;
  friendly: string;
  status: ComponentStatus;
  percent: number;
  message: string;
  detail: string;
}

export interface AiConfig {
  reasoning_provider: string;
  reasoning_model: string;
  velo_provider: string;
  velo_model: string;
  has_openrouter: boolean;
  has_typesafe: boolean;
}

export interface AiConfigPatch {
  reasoning_provider?: string;
  reasoning_model?: string;
  velo_provider?: string;
  velo_model?: string;
}

export type KeyStatus =
  | { status: "connected"; label: string | null }
  | { status: "invalid" }
  | { status: "offline" };

export interface ModelOption {
  id: string;
  name: string;
}

export interface PermissionSnapshot {
  microphone: string;
  accessibility: string;
  screen_recording: string;
  screen_recording_restart_required: boolean;
}

export interface HealthCheck {
  label: string;
  ok: boolean;
  note: string;
}

export interface FinalHealth {
  checks: HealthCheck[];
  local_healthy: boolean;
  network_only_issue: boolean;
}

export type OnboardingStage =
  | "welcome"
  | "preparing"
  | "config"
  | "permissions"
  | "preferences"
  | "verify"
  | "ready";
