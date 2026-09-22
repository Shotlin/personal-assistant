import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import type {
  AiConfig,
  AiConfigPatch,
  FinalHealth,
  KeyStatus,
  ModelOption,
  PermissionSnapshot,
  SetupProgressEvent,
  SetupSnapshot,
} from "./types";

// ------------------------------------------------------------------ events

export const onSetupProgress = (cb: (e: SetupProgressEvent) => void) =>
  listen<SetupProgressEvent>("sani://setup-progress", (e) => cb(e.payload));

export const onSetupStage = (cb: (stage: string) => void) =>
  listen<string>("sani://setup-stage", (e) => cb(e.payload));

// ---------------------------------------------------------------- commands

export const setupState = () => invoke<SetupSnapshot>("setup_state");
export const runSetup = () => invoke<void>("run_setup");
export const retrySetupComponent = (step: string) =>
  invoke<void>("retry_setup_component", { step });
export const setOnboardingStage = (stage: string) =>
  invoke<{ percent: number; local_setup_complete: boolean }>("set_onboarding_stage", { stage });
export const completeOnboarding = () => invoke<void>("complete_onboarding");
export const resetOnboarding = () => invoke<void>("reset_onboarding");

export const getAiConfig = () => invoke<AiConfig>("get_ai_config");
export const saveAiConfig = (patch: AiConfigPatch) => invoke<AiConfig>("save_ai_config", { patch });
export const storeProviderKey = (provider: string, key: string) =>
  invoke<{ stored: boolean; has_openrouter: boolean; has_typesafe: boolean }>("store_provider_key", {
    provider,
    key,
  });
export const validateProviderKey = (provider: string, key: string) =>
  invoke<KeyStatus>("validate_provider_key", { provider, key });
export const listOpenrouterModels = (key: string | null) =>
  invoke<ModelOption[]>("list_openrouter_models", { key });

export const permissionSnapshot = () => invoke<PermissionSnapshot>("permission_snapshot");
export const openPermissionSettings = (pane: string) =>
  invoke<void>("open_permission_settings", { pane });
export const requestAccessibility = () => invoke<string>("request_accessibility");
export const requestScreenRecording = () => invoke<string>("request_screen_recording");
export const requestMicrophone = () => invoke<string>("request_mic_permission");
export const restartApp = () => invoke<void>("restart_app");

export const finalHealth = () => invoke<FinalHealth>("final_health");

// Preferences reuse the existing settings command.
export interface Preferences {
  launch_at_login: boolean;
  hotkey: string;
  mic_device: string;
  theme: string;
}
export const getPreferences = () =>
  invoke<{
    launch_at_login: boolean;
    hotkey: string;
    mic_device: string;
    theme: string;
    stt_model: string;
    stt_ready: boolean;
  }>("get_settings");
export const savePreferences = (patch: {
  launch_at_login?: boolean;
  hotkey?: string;
  mic_device?: string;
  theme?: string;
}) => invoke<void>("save_settings_cmd", patch);
export const listMics = () => invoke<string[]>("list_mics");
