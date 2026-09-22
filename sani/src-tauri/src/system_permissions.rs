//! macOS Accessibility and Screen Recording authorization.
//!
//! Sani never claims it can flip a Privacy & Security toggle — macOS controls
//! those. What Sani *can* do automatically is open the exact settings page,
//! detect the real permission state, re-check live, and restart when the OS
//! requires it. The user performs only the one consent action macOS mandates.
//!
//! Mirrors [`crate::permissions`] (microphone) for consistency. Non-macOS
//! platforms report `Granted`, since there is no equivalent gate here.

use serde::Serialize;
use std::sync::atomic::{AtomicBool, Ordering};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PermissionStatus {
    /// Never requested; the user can be prompted.
    NotDetermined,
    /// Granted.
    Granted,
    /// Not (yet) granted — the user must enable it in System Settings.
    Denied,
    /// The platform exposes no such gate; treated as usable.
    Unknown,
}

impl PermissionStatus {
    pub fn as_str(&self) -> &'static str {
        match self {
            PermissionStatus::NotDetermined => "not_determined",
            PermissionStatus::Granted => "granted",
            PermissionStatus::Denied => "denied",
            PermissionStatus::Unknown => "unknown",
        }
    }
    pub fn is_granted(&self) -> bool {
        matches!(self, PermissionStatus::Granted | PermissionStatus::Unknown)
    }
}

/// True when Sani launched *without* Screen Recording but now has it: capture
/// APIs only pick the grant up after a relaunch, so the UI offers "Restart Sani".
static LAUNCHED_WITHOUT_SCREEN_RECORDING: AtomicBool = AtomicBool::new(false);

/// Record that Sani launched *without* Screen Recording, so a grant made during
/// this session can be flagged as needing a relaunch (capture APIs only pick
/// the grant up after restart).
pub fn init_screen_recording_baseline() {
    LAUNCHED_WITHOUT_SCREEN_RECORDING.store(!screen_recording().is_granted(), Ordering::Relaxed);
}

#[cfg(target_os = "macos")]
mod platform {
    extern "C" {
        fn sani_accessibility_trusted() -> i32;
        fn sani_accessibility_prompt() -> i32;
        fn sani_screen_recording_allowed() -> i32;
        fn sani_screen_recording_request() -> i32;
    }

    pub fn accessibility() -> super::PermissionStatus {
        if unsafe { sani_accessibility_trusted() } != 0 {
            super::PermissionStatus::Granted
        } else {
            super::PermissionStatus::Denied
        }
    }

    pub fn accessibility_prompt() -> super::PermissionStatus {
        let trusted = unsafe { sani_accessibility_prompt() } != 0;
        if trusted { super::PermissionStatus::Granted } else { super::PermissionStatus::Denied }
    }

    pub fn screen_recording() -> super::PermissionStatus {
        if unsafe { sani_screen_recording_allowed() } != 0 {
            super::PermissionStatus::Granted
        } else {
            super::PermissionStatus::Denied
        }
    }

    pub fn screen_recording_request() -> super::PermissionStatus {
        let allowed = unsafe { sani_screen_recording_request() } != 0;
        if allowed { super::PermissionStatus::Granted } else { super::PermissionStatus::Denied }
    }
}

#[cfg(not(target_os = "macos"))]
mod platform {
    pub fn accessibility() -> super::PermissionStatus {
        super::PermissionStatus::Unknown
    }
    pub fn accessibility_prompt() -> super::PermissionStatus {
        super::PermissionStatus::Unknown
    }
    pub fn screen_recording() -> super::PermissionStatus {
        super::PermissionStatus::Unknown
    }
    pub fn screen_recording_request() -> super::PermissionStatus {
        super::PermissionStatus::Unknown
    }
}

pub fn accessibility() -> PermissionStatus {
    platform::accessibility()
}

/// Adds Sani to the Accessibility list so the toggle exists to enable.
pub fn accessibility_prompt() -> PermissionStatus {
    platform::accessibility_prompt()
}

pub fn screen_recording() -> PermissionStatus {
    platform::screen_recording()
}

pub fn screen_recording_request() -> PermissionStatus {
    platform::screen_recording_request()
}

/// Screen Recording was just granted this session but capture needs a relaunch.
pub fn screen_recording_restart_required() -> bool {
    LAUNCHED_WITHOUT_SCREEN_RECORDING.load(Ordering::Relaxed) && screen_recording().is_granted()
}

/// Open the exact System Settings privacy pane. `pane` is one of
/// "accessibility" | "screen_recording" | "microphone".
pub fn open_settings(pane: &str) {
    #[cfg(target_os = "macos")]
    {
        let url = match pane {
            "accessibility" => "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
            "screen_recording" => "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
            "microphone" => "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone",
            other => {
                log::warn!("[perms] unknown settings pane '{other}'");
                return;
            }
        };
        let _ = std::process::Command::new("open").arg(url).spawn();
    }
    #[cfg(not(target_os = "macos"))]
    let _ = pane;
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn statuses_serialize_snake_case() {
        assert_eq!(serde_json::to_string(&PermissionStatus::NotDetermined).unwrap(), "\"not_determined\"");
        assert_eq!(serde_json::to_string(&PermissionStatus::Granted).unwrap(), "\"granted\"");
    }

    #[test]
    fn granted_or_unknown_counts_as_usable() {
        assert!(PermissionStatus::Granted.is_granted());
        assert!(PermissionStatus::Unknown.is_granted());
        assert!(!PermissionStatus::Denied.is_granted());
    }
}
