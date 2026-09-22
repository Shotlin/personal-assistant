fn main() {
    // Compile the native macOS microphone-authorization bridge (Objective-C)
    // and link AVFoundation. Other platforms skip it: Sani falls back to a
    // "granted" assumption where the OS has no TCC microphone gate.
    #[cfg(target_os = "macos")]
    {
        // Compiling the bridges only needs the SDK framework headers; the
        // frameworks are linked into the final binary by the directives below.
        cc::Build::new()
            .file("native/mic_permission.m")
            .file("native/snapshot.m")
            .file("native/system_permissions.m")
            .flag("-fobjc-arc")
            .compile("sani_native_bridges");
        println!("cargo:rustc-link-lib=framework=AVFoundation");
        println!("cargo:rustc-link-lib=framework=WebKit");
        println!("cargo:rustc-link-lib=framework=AppKit");
        println!("cargo:rustc-link-lib=framework=ApplicationServices");
        println!("cargo:rustc-link-lib=framework=CoreGraphics");
    }

    tauri_build::build()
}
