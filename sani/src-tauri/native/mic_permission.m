// Sani — native macOS microphone authorization bridge.
//
// Apple requires an explicit TCC authorization decision before microphone
// capture returns anything but silence. CPAL cannot surface that decision, so
// Sani asks AVFoundation directly: check the authorization state, request it
// when undetermined, and never infer permission from RMS/silence.
//
// Compiled by build.rs (cc) and linked against AVFoundation. Exposed to Rust
// as plain C functions.

#import <AVFoundation/AVFoundation.h>
#import <stdatomic.h>

// Mirrors AVAuthorizationStatus so Rust never imports the enum:
//   0 = NotDetermined, 1 = Restricted, 2 = Denied, 3 = Authorized
static atomic_int g_request_settled = 0;

int32_t sani_mic_authorization_status(void) {
    AVAuthorizationStatus status =
        [AVCaptureDevice authorizationStatusForMediaType:AVMediaTypeAudio];
    return (int32_t)status;
}

// Triggers the system prompt when the state is NotDetermined. Returns
// immediately; poll sani_mic_request_settled() / the status for the result.
void sani_mic_request_authorization(void) {
    atomic_store(&g_request_settled, 0);
    [AVCaptureDevice requestAccessForMediaType:AVMediaTypeAudio
                             completionHandler:^(BOOL granted) {
        (void)granted;
        atomic_store(&g_request_settled, 1);
    }];
}

int32_t sani_mic_request_settled(void) {
    return atomic_load(&g_request_settled);
}
