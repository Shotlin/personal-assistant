// Sani — native macOS Accessibility + Screen Recording authorization bridge.
//
// Both are TCC decisions macOS owns outright: an app may *ask* and may *observe*
// the current status, but can never flip the user's Privacy & Security toggle.
// Sani therefore surfaces the real state (never a guess) and opens the correct
// System Settings pane; the user performs the one consent action macOS requires.
//
// Compiled by build.rs (cc) and linked against ApplicationServices +
// CoreGraphics. Exposed to Rust as plain C functions.

#import <ApplicationServices/ApplicationServices.h>
#import <CoreGraphics/CoreGraphics.h>

// --- Accessibility (AXIsProcessTrusted) -----------------------------------

// 1 when Sani is trusted for Accessibility, else 0. Read-only: does not prompt.
int32_t sani_accessibility_trusted(void) {
    return AXIsProcessTrusted() ? 1 : 0;
}

// Adds Sani to the Accessibility list and (with the option) prompts, so the
// toggle exists for the user to enable. Returns the post-call trusted state.
int32_t sani_accessibility_prompt(void) {
    const void *keys[] = { kAXTrustedCheckOptionPrompt };
    const void *values[] = { kCFBooleanTrue };
    CFDictionaryRef options =
        CFDictionaryCreate(NULL, keys, values, 1,
                           &kCFTypeDictionaryKeyCallBacks,
                           &kCFTypeDictionaryValueCallBacks);
    Boolean trusted = AXIsProcessTrustedWithOptions(options);
    CFRelease(options);
    return trusted ? 1 : 0;
}

// --- Screen Recording (CGPreflightScreenCaptureAccess) --------------------

// 1 when Sani currently has Screen Recording access, else 0. Read-only.
int32_t sani_screen_recording_allowed(void) {
    if (__builtin_available(macOS 11.0, *)) {
        return CGPreflightScreenCaptureAccess() ? 1 : 0;
    }
    return 1; // pre-Big Sur had no such gate
}

// Triggers the system prompt / adds Sani to the Screen Recording list. Returns
// the post-request allowed state (capture typically needs a relaunch to apply).
int32_t sani_screen_recording_request(void) {
    if (__builtin_available(macOS 11.0, *)) {
        return CGRequestScreenCaptureAccess() ? 1 : 0;
    }
    return 1;
}
