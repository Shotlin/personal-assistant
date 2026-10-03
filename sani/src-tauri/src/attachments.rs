//! Files the user attaches to a message (screenshots, PDFs, notes).
//!
//! They are copied into Sani's own `attachments/` folder under the app data
//! directory and handed on, by path, to whichever agent needs them (for coding
//! work that is Claude Code, which reads them itself; Sani's own model never has
//! to). Only a short list of safe types is accepted, the bytes must look like
//! the type the name claims, and size is capped.

use base64::Engine;
use std::path::{Path, PathBuf};
use tauri::{AppHandle, Manager};

pub const MAX_ATTACHMENT_BYTES: usize = 10 * 1024 * 1024;
const KEEP_DAYS: u64 = 30;
const ALLOWED: &[&str] = &[
    "png", "jpg", "jpeg", "gif", "webp", "pdf", "txt", "md", "json",
];

/// `(stem, extension)` with every character that is not a letter, digit, dash,
/// underscore or dot removed, and no path components.
fn clean_name(name: &str) -> Option<(String, String)> {
    let file = Path::new(name).file_name()?.to_string_lossy().into_owned();
    let (stem, ext) = file.rsplit_once('.')?;
    let ext = ext.to_ascii_lowercase();
    if !ALLOWED.contains(&ext.as_str()) {
        return None;
    }
    let stem: String = stem
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '-' || c == '_' {
                c
            } else {
                '-'
            }
        })
        .collect();
    let stem = stem.trim_matches('-');
    let stem = if stem.is_empty() { "file" } else { stem };
    Some((stem.chars().take(60).collect(), ext))
}

/// Do the bytes plausibly match the claimed type? Not a security boundary on
/// its own, just enough to refuse an obviously renamed file.
fn matches_type(ext: &str, data: &[u8]) -> bool {
    match ext {
        "png" => data.starts_with(&[0x89, b'P', b'N', b'G']),
        "jpg" | "jpeg" => data.starts_with(&[0xFF, 0xD8, 0xFF]),
        "gif" => data.starts_with(b"GIF8"),
        "webp" => data.len() > 12 && &data[0..4] == b"RIFF" && &data[8..12] == b"WEBP",
        "pdf" => data.starts_with(b"%PDF"),
        _ => std::str::from_utf8(data).is_ok(),
    }
}

pub(crate) fn store(dir: &Path, name: &str, data: &[u8]) -> Result<PathBuf, String> {
    let (stem, ext) = clean_name(name).ok_or_else(|| {
        "That file type can't be attached. Use an image, PDF or text file.".to_string()
    })?;
    if data.is_empty() {
        return Err("That file is empty.".into());
    }
    if data.len() > MAX_ATTACHMENT_BYTES {
        return Err(format!(
            "That file is larger than {} MB.",
            MAX_ATTACHMENT_BYTES / (1024 * 1024)
        ));
    }
    if !matches_type(&ext, data) {
        return Err(format!("That doesn't look like a real .{ext} file."));
    }
    std::fs::create_dir_all(dir).map_err(|err| err.to_string())?;
    let path = dir.join(format!("{}-{stem}.{ext}", uuid::Uuid::new_v4().simple()));
    std::fs::write(&path, data).map_err(|err| err.to_string())?;
    Ok(path)
}

/// Best effort: forget attachments older than the retention window.
fn prune(dir: &Path) {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };
    let cutoff = std::time::SystemTime::now() - std::time::Duration::from_secs(KEEP_DAYS * 86_400);
    for entry in entries.flatten() {
        let old = entry
            .metadata()
            .and_then(|meta| meta.modified())
            .map(|modified| modified < cutoff)
            .unwrap_or(false);
        if old {
            let _ = std::fs::remove_file(entry.path());
        }
    }
}

/// Save one attachment and return its absolute path.
#[tauri::command]
pub async fn save_attachment_cmd(
    app: AppHandle,
    name: String,
    data_base64: String,
) -> Result<String, String> {
    // Refuse before decoding: base64 is ~4/3 of the bytes.
    if data_base64.len() > MAX_ATTACHMENT_BYTES * 4 / 3 + 16 {
        return Err("That file is too large to attach.".into());
    }
    let data = base64::engine::general_purpose::STANDARD
        .decode(data_base64.as_bytes())
        .map_err(|_| "That file couldn't be read.".to_string())?;
    let dir = app
        .path()
        .app_data_dir()
        .map_err(|err| err.to_string())?
        .join("attachments");
    let path = store(&dir, &name, &data)?;
    prune(&dir);
    Ok(path.to_string_lossy().into_owned())
}

#[cfg(test)]
mod tests {
    use super::*;

    const PNG: &[u8] = &[0x89, b'P', b'N', b'G', 0x0D, 0x0A, 0x1A, 0x0A, 0, 0];

    fn dir() -> PathBuf {
        std::env::temp_dir().join(format!("sani-att-{}", uuid::Uuid::new_v4()))
    }

    #[test]
    fn a_real_image_is_stored_under_a_clean_unique_name() {
        let dir = dir();
        let path = store(&dir, "../../My Screenshot (1).PNG", PNG).expect("stored");
        assert!(path.starts_with(&dir));
        let file = path.file_name().unwrap().to_string_lossy().into_owned();
        assert!(file.ends_with("-My-Screenshot--1.png") || file.ends_with("-My-Screenshot-1.png"));
        assert_eq!(std::fs::read(&path).unwrap(), PNG);
        let again = store(&dir, "My Screenshot (1).PNG", PNG).expect("stored again");
        assert_ne!(path, again);
    }

    #[test]
    fn unsafe_types_empty_oversize_and_disguised_files_are_refused() {
        let dir = dir();
        assert!(store(&dir, "run.sh", b"echo hi").is_err());
        assert!(store(&dir, "app.exe", b"MZ").is_err());
        assert!(store(&dir, "noext", b"x").is_err());
        assert!(store(&dir, "a.png", b"").is_err());
        assert!(store(&dir, "a.png", b"#!/bin/sh\necho hi").is_err());
        let big = vec![0x89u8; MAX_ATTACHMENT_BYTES + 1];
        assert!(store(&dir, "a.png", &big).is_err());
        assert!(!dir.exists() || std::fs::read_dir(&dir).unwrap().next().is_none());
    }

    #[test]
    fn text_files_must_be_text() {
        let dir = dir();
        assert!(store(&dir, "notes.md", "# hello".as_bytes()).is_ok());
        assert!(store(&dir, "notes.txt", &[0xFF, 0xFE, 0x00, 0x9F]).is_err());
    }
}
