use crate::window_geometry::LogicalWorkArea;
use serde::Deserialize;

#[derive(Debug, Clone, PartialEq)]
pub struct AppKitFrame {
    pub id: String,
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
    pub scale_factor: f64,
}

#[derive(Debug, Clone, Deserialize)]
pub struct NativeVisibleFrame {
    pub id: String,
    pub screen_x: f64,
    pub screen_y: f64,
    pub screen_width: f64,
    pub screen_height: f64,
    pub visible_x: f64,
    pub visible_y: f64,
    pub visible_width: f64,
    pub visible_height: f64,
    pub backing_scale_factor: f64,
}

impl NativeVisibleFrame {
    pub fn screen_frame(&self) -> AppKitFrame {
        AppKitFrame {
            id: self.id.clone(),
            x: self.screen_x,
            y: self.screen_y,
            width: self.screen_width,
            height: self.screen_height,
            scale_factor: self.backing_scale_factor,
        }
    }

    pub fn visible_frame(&self) -> AppKitFrame {
        AppKitFrame {
            id: self.id.clone(),
            x: self.visible_x,
            y: self.visible_y,
            width: self.visible_width,
            height: self.visible_height,
            scale_factor: self.backing_scale_factor,
        }
    }
}

/// Convert AppKit's bottom-left screen coordinates to logical offsets from a
/// display's top-left. `visibleFrame` already excludes the menu bar/notch and
/// a Dock on any edge, so no guessed insets are ever involved.
pub fn logical_visible_area(screen: AppKitFrame, visible: AppKitFrame) -> LogicalWorkArea {
    let x = visible.x - screen.x;
    let y = screen.height - (visible.y - screen.y) - visible.height;
    LogicalWorkArea {
        id: visible.id,
        x: x.max(0.0),
        y: y.max(0.0),
        width: visible.width.max(1.0),
        height: visible.height.max(1.0),
        scale_factor: visible.scale_factor,
        is_primary: false,
    }
}

#[cfg(target_os = "macos")]
extern "C" {
    fn sani_visible_work_areas_json() -> *const std::ffi::c_char;
    fn sani_visible_work_areas_free(value: *const std::ffi::c_char);
}

#[cfg(target_os = "macos")]
pub fn visible_work_areas() -> Vec<NativeVisibleFrame> {
    use std::ffi::CStr;

    let raw = unsafe { sani_visible_work_areas_json() };
    if raw.is_null() {
        return Vec::new();
    }
    let parsed = unsafe { CStr::from_ptr(raw) }
        .to_str()
        .ok()
        .and_then(|json| serde_json::from_str(json).ok());
    unsafe { sani_visible_work_areas_free(raw) };
    match parsed {
        Some(areas) => areas,
        None => {
            log::warn!("[window] macOS visible work-area bridge returned invalid JSON");
            Vec::new()
        }
    }
}

#[cfg(not(target_os = "macos"))]
pub fn visible_work_areas() -> Vec<NativeVisibleFrame> {
    Vec::new()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn appkit_frame(
        id: &str,
        x: f64,
        y: f64,
        width: f64,
        height: f64,
        scale_factor: f64,
    ) -> AppKitFrame {
        AppKitFrame {
            id: id.into(),
            x,
            y,
            width,
            height,
            scale_factor,
        }
    }

    #[test]
    fn visible_frame_preserves_a_left_dock_inset() {
        let screen = appkit_frame("built-in", 0.0, 0.0, 1512.0, 982.0, 2.0);
        let visible = appkit_frame("built-in", 96.0, 25.0, 1416.0, 957.0, 2.0);

        let area = logical_visible_area(screen, visible);

        assert_eq!(area.x, 96.0);
        assert_eq!(area.y, 0.0);
        assert_eq!(area.width, 1416.0);
        assert_eq!(area.height, 957.0);
    }

    #[test]
    fn visible_frame_preserves_a_right_dock_inset() {
        let screen = appkit_frame("built-in", 0.0, 0.0, 1512.0, 982.0, 2.0);
        let visible = appkit_frame("built-in", 0.0, 25.0, 1416.0, 957.0, 2.0);

        let area = logical_visible_area(screen, visible);

        assert_eq!(area.x, 0.0);
        assert_eq!(area.width, 1416.0);
    }
}
