use std::path::Path;

/// Tauri embeds the production frontend in the application binary. Cargo does
/// not discover Vite output by itself, so without these dependency markers a
/// frontend-only change can produce a new `dist/` while reusing an older
/// bundled Sani binary. Track every generated asset so release builds always
/// contain the UI that `beforeBuildCommand` just created.
fn track_frontend_assets(path: &Path) {
    println!("cargo:rerun-if-changed={}", path.display());
    if let Ok(entries) = std::fs::read_dir(path) {
        for entry in entries.flatten() {
            let child = entry.path();
            if child.is_dir() {
                track_frontend_assets(&child);
            } else {
                println!("cargo:rerun-if-changed={}", child.display());
            }
        }
    }
}

fn main() {
    track_frontend_assets(Path::new("../dist"));
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
            .file("native/macos_work_area.m")
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
