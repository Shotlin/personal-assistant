//! Microphone authorization.
//!
//! macOS gates the microphone behind an explicit TCC decision. CPAL cannot
//! tell us whether capture is actually permitted — a denied app just receives
//! silence — so Sani asks the OS directly through a small Objective-C bridge
//! (`native/mic_permission.m`) and never infers permission from RMS levels.
//!
//! On other platforms there is no equivalent system gate at this layer, so the
//! state is reported as `Granted` and capture proceeds.

use serde::Serialize;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum MicPermission {
    /// The OS has not asked the user yet.
    NotDetermined,
    /// Blocked by policy (e.g. MDM / parental controls); cannot be requested.
    Restricted,
    /// The user denied access; Sani must not pretend to listen.
    Denied,
    /// Capture is permitted.
    Granted,
    /// The platform has no microphone authorization gate we can query.
    Unknown,
}

impl MicPermission {
    pub fn as_str(&self) -> &'static str {
        match self {
            MicPermission::NotDetermined => "not_determined",
            MicPermission::Restricted => "restricted",
            MicPermission::Denied => "denied",
            MicPermission::Granted => "granted",
            MicPermission::Unknown => "unknown",
        }
    }

    /// True only when real capture is allowed right now.
    pub fn is_granted(&self) -> bool {
        matches!(self, MicPermission::Granted | MicPermission::Unknown)
    }
}

#[cfg(target_os = "macos")]
mod platform {
    extern "C" {
        fn sani_mic_authorization_status() -> i32;
        fn sani_mic_request_authorization();
        fn sani_mic_request_settled() -> i32;
    }

    pub fn status() -> super::MicPermission {
        // AVAuthorizationStatus: 0 NotDetermined, 1 Restricted, 2 Denied, 3 Authorized.
        match unsafe { sani_mic_authorization_status() } {
            0 => super::MicPermission::NotDetermined,
            1 => super::MicPermission::Restricted,
            2 => super::MicPermission::Denied,
            3 => super::MicPermission::Granted,
            _ => super::MicPermission::Unknown,
        }
    }

    pub fn request() {
        unsafe { sani_mic_request_authorization() };
    }

    pub fn request_settled() -> bool {
        unsafe { sani_mic_request_settled() != 0 }
    }
}

#[cfg(not(target_os = "macos"))]
mod platform {
    pub fn status() -> super::MicPermission {
        super::MicPermission::Unknown
    }
    pub fn request() {}
    pub fn request_settled() -> bool {
        true
    }
}

/// Current authorization state (never triggers a prompt).
pub fn status() -> MicPermission {
    platform::status()
}

/// Ask the OS for microphone access. Returns immediately; the prompt is modal
/// to the system, so callers poll [`status`] (or [`request_settled`]) for the
/// decision. A no-op when the state is already determined.
pub fn request() {
    platform::request();
}

/// True once the user has answered the most recent authorization prompt.
pub fn request_settled() -> bool {
    platform::request_settled()
}
