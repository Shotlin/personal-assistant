//! Pure overlay geometry for the Voice Pill and Conversation Panel.
//!
//! A committed layout deliberately mixes units: the top-left position is a
//! normalized ratio of the usable work area, while width and height are logical
//! points. Position therefore follows a display that changes shape (Dock moved,
//! resolution changed, different monitor), while a 520-point panel stays
//! perceptually 520 points on any display and stays correct on Retina.
//!
//! Everything here is pure and total: no window handle, no monitor query, no
//! panic. `windows.rs` supplies real work areas and converts the returned
//! logical frames to physical pixels.

use serde::{Deserialize, Serialize};

use crate::window_geometry::LogicalWorkArea;

/// The pill's height is fixed so it always matches the vibrancy corner radius.
pub const PILL_HEIGHT: f64 = 96.0;
pub const PILL_MIN_WIDTH: f64 = 560.0;
pub const PILL_MAX_WIDTH: f64 = 760.0;
pub const PANEL_MIN_WIDTH: f64 = 500.0;
pub const PANEL_MAX_WIDTH: f64 = 640.0;
pub const PANEL_MIN_HEIGHT: f64 = 400.0;
pub const PANEL_MAX_HEIGHT: f64 = 680.0;

/// Position and size of one overlay. Ratios are normalized against the usable
/// work area; `width`/`height` are logical points, never physical pixels.
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct OverlayFrame {
    pub x_ratio: f64,
    pub y_ratio: f64,
    pub width: f64,
    pub height: f64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct OverlayLayout {
    /// Id of the display this layout was committed against. Empty means "no
    /// preference", which resolves to the main window's display.
    pub display_affinity: String,
    pub pill: OverlayFrame,
    pub panel: OverlayFrame,
}

/// A rectangle in logical points relative to the top-left of a usable work area.
#[derive(Debug, Clone, Copy, PartialEq, Serialize)]
pub struct LogicalRect {
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ResolvedOverlayLayout {
    pub display: LogicalWorkArea,
    pub pill: LogicalRect,
    pub panel: LogicalRect,
    /// Plain-language explanation of every clamp applied to the request, so the
    /// editor can say why a frame moved instead of silently disagreeing.
    pub adjustments: Vec<String>,
}

/// Phase 1 placement, expressed against a 1512×945 MacBook usable work area:
/// a 700-point pill centered 92 points above the bottom edge.
pub fn default_pill_frame() -> OverlayFrame {
    OverlayFrame {
        x_ratio: 0.269,
        y_ratio: 0.801,
        width: 700.0,
        height: PILL_HEIGHT,
    }
}

/// Phase 1 placement: a 544×454 panel 20 points from the top-right corner.
pub fn default_panel_frame() -> OverlayFrame {
    OverlayFrame {
        x_ratio: 0.627,
        y_ratio: 0.021,
        width: 544.0,
        height: 454.0,
    }
}

impl Default for OverlayLayout {
    fn default() -> Self {
        Self {
            display_affinity: String::new(),
            pill: default_pill_frame(),
            panel: default_panel_frame(),
        }
    }
}

/// Resolve a committed or draft layout into safe logical frames.
///
/// Display order: saved affinity, the display holding the Sani main window,
/// the primary display, then the first available one. The cursor is never an
/// editor fallback.
pub fn resolve_overlay_layout(
    layout: &OverlayLayout,
    areas: &[LogicalWorkArea],
    main_display: Option<&str>,
) -> ResolvedOverlayLayout {
    let display = select_display(layout.display_affinity.trim(), areas, main_display);
    let mut adjustments = Vec::new();
    let pill = place(Overlay::Pill, &layout.pill, &display, &mut adjustments);
    let panel = place(Overlay::Panel, &layout.panel, &display, &mut adjustments);
    ResolvedOverlayLayout {
        display,
        pill,
        panel,
        adjustments,
    }
}

/// Shift a work-area-relative frame to display-relative logical coordinates.
/// `area.x`/`area.y` are the menu-bar/notch and Dock insets, so the excluded
/// strips can never receive an overlay frame.
pub fn display_rect(area: &LogicalWorkArea, rect: LogicalRect) -> LogicalRect {
    LogicalRect {
        x: rect.x + area.x,
        y: rect.y + area.y,
        ..rect
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Overlay {
    Pill,
    Panel,
}

impl Overlay {
    fn label(self) -> &'static str {
        match self {
            Overlay::Pill => "The voice pill",
            Overlay::Panel => "The conversation panel",
        }
    }

    fn default_frame(self) -> OverlayFrame {
        match self {
            Overlay::Pill => default_pill_frame(),
            Overlay::Panel => default_panel_frame(),
        }
    }

    fn limits(self) -> (f64, f64, f64, f64) {
        match self {
            Overlay::Pill => (PILL_MIN_WIDTH, PILL_MAX_WIDTH, PILL_HEIGHT, PILL_HEIGHT),
            Overlay::Panel => (
                PANEL_MIN_WIDTH,
                PANEL_MAX_WIDTH,
                PANEL_MIN_HEIGHT,
                PANEL_MAX_HEIGHT,
            ),
        }
    }
}

fn select_display(
    affinity: &str,
    areas: &[LogicalWorkArea],
    main_display: Option<&str>,
) -> LogicalWorkArea {
    areas
        .iter()
        .find(|area| !affinity.is_empty() && area.id == affinity)
        .or_else(|| main_display.and_then(|id| areas.iter().find(|area| area.id == id)))
        .or_else(|| areas.iter().find(|area| area.is_primary))
        .or_else(|| areas.first())
        .cloned()
        .unwrap_or_else(unknown_display)
}

/// Last resort when no work area is known at all. Sizing stays safe; the caller
/// still refuses to apply frames without a real display snapshot.
fn unknown_display() -> LogicalWorkArea {
    LogicalWorkArea {
        id: "unknown".into(),
        x: 0.0,
        y: 0.0,
        width: 1440.0,
        height: 900.0,
        scale_factor: 1.0,
        is_primary: true,
    }
}

fn place(
    overlay: Overlay,
    requested: &OverlayFrame,
    area: &LogicalWorkArea,
    adjustments: &mut Vec<String>,
) -> LogicalRect {
    let frame = if is_placeable(overlay, requested) {
        *requested
    } else {
        adjustments.push(format!(
            "{} had values Sani could not place, so its default position was restored.",
            overlay.label()
        ));
        overlay.default_frame()
    };
    let (min_width, max_width, min_height, max_height) = overlay.limits();
    let width = fit_dimension(
        frame.width,
        min_width,
        max_width,
        area.width,
        &format!("{} width", overlay.label()),
        adjustments,
    );
    let height = fit_dimension(
        // The pill's height is a supported constant, whatever a draft claims.
        if overlay == Overlay::Pill {
            PILL_HEIGHT
        } else {
            frame.height
        },
        min_height,
        max_height,
        area.height,
        &format!("{} height", overlay.label()),
        adjustments,
    );
    let wanted_x = frame.x_ratio * area.width;
    let wanted_y = frame.y_ratio * area.height;
    let rect = LogicalRect {
        x: wanted_x.clamp(0.0, (area.width - width).max(0.0)),
        y: wanted_y.clamp(0.0, (area.height - height).max(0.0)),
        width,
        height,
    };
    if (rect.x - wanted_x).abs() > 0.5 || (rect.y - wanted_y).abs() > 0.5 {
        adjustments.push(format!(
            "{} was moved to stay fully inside the visible work area.",
            overlay.label()
        ));
    }
    rect
}

/// A frame is placeable when its normalized position is a real ratio and its
/// width is a real positive size. The pill's height is never inspected because
/// it is not adjustable.
fn is_placeable(overlay: Overlay, frame: &OverlayFrame) -> bool {
    let finite = [frame.x_ratio, frame.y_ratio, frame.width]
        .into_iter()
        .all(f64::is_finite);
    let height_ok = overlay == Overlay::Pill || (frame.height.is_finite() && frame.height > 0.0);
    finite
        && height_ok
        && frame.width > 0.0
        && (0.0..=1.0).contains(&frame.x_ratio)
        && (0.0..=1.0).contains(&frame.y_ratio)
}

/// Clamp to the supported range first, then to what the work area can hold,
/// reporting both in the words the editor shows the user.
fn fit_dimension(
    requested: f64,
    minimum: f64,
    maximum: f64,
    available: f64,
    subject: &str,
    adjustments: &mut Vec<String>,
) -> f64 {
    let supported = requested.clamp(minimum, maximum);
    if (supported - requested).abs() > 0.5 {
        let (bound, adjective) = if requested < minimum {
            (minimum, "smallest")
        } else {
            (maximum, "largest")
        };
        adjustments.push(format!(
            "{subject} was set to {bound:.0} pt, the {adjective} size Sani supports."
        ));
    }
    let fitted = supported.min(available.max(1.0));
    if (fitted - supported).abs() > 0.5 {
        adjustments.push(format!(
            "{subject} was reduced to {fitted:.0} pt to fit this display's visible work area."
        ));
    }
    fitted
}

#[cfg(test)]
mod tests {
    use super::*;

    fn area(id: &str, width: f64, height: f64, is_primary: bool) -> LogicalWorkArea {
        LogicalWorkArea {
            id: id.into(),
            x: 0.0,
            y: 0.0,
            width,
            height,
            scale_factor: 2.0,
            is_primary,
        }
    }

    fn inset_area(id: &str, x: f64, y: f64, width: f64, height: f64) -> LogicalWorkArea {
        LogicalWorkArea {
            id: id.into(),
            x,
            y,
            width,
            height,
            scale_factor: 2.0,
            is_primary: true,
        }
    }

    /// A layout with no saved display affinity and the given panel size.
    fn saved(panel_width: f64, panel_height: f64) -> OverlayLayout {
        OverlayLayout {
            panel: OverlayFrame {
                width: panel_width,
                height: panel_height,
                ..default_panel_frame()
            },
            ..OverlayLayout::default()
        }
    }

    #[test]
    fn panel_keeps_logical_width_on_larger_work_area() {
        let resolved = resolve_overlay_layout(
            &saved(520.0, 640.0),
            &[area("large", 2200.0, 1300.0, true)],
            Some("large"),
        );

        assert_eq!(resolved.panel.width, 520.0);
        assert_eq!(resolved.panel.height, 640.0);
    }

    #[test]
    fn missing_affinity_uses_main_display_and_clamps() {
        let resolved = resolve_overlay_layout(
            &saved(9999.0, 9999.0),
            &[area("main", 1200.0, 800.0, true)],
            Some("main"),
        );

        assert_eq!(resolved.display.id, "main");
        assert!(resolved.panel.x + resolved.panel.width <= resolved.display.width);
        assert!(resolved.panel.y + resolved.panel.height <= resolved.display.height);
        assert_eq!(resolved.panel.width, PANEL_MAX_WIDTH);
        assert_eq!(resolved.panel.height, PANEL_MAX_HEIGHT);
        assert!(!resolved.adjustments.is_empty());
    }

    #[test]
    fn saved_affinity_wins_over_the_main_window_display() {
        let layout = OverlayLayout {
            display_affinity: "external".into(),
            ..OverlayLayout::default()
        };
        let areas = [
            area("built-in", 1512.0, 945.0, true),
            area("external", 2560.0, 1440.0, false),
        ];

        let resolved = resolve_overlay_layout(&layout, &areas, Some("built-in"));

        assert_eq!(resolved.display.id, "external");
    }

    #[test]
    fn disconnected_affinity_falls_back_to_main_then_primary() {
        let layout = OverlayLayout {
            display_affinity: "external".into(),
            ..OverlayLayout::default()
        };
        let areas = [
            area("built-in", 1512.0, 945.0, false),
            area("second", 1920.0, 1080.0, true),
        ];

        let with_main = resolve_overlay_layout(&layout, &areas, Some("built-in"));
        assert_eq!(with_main.display.id, "built-in");

        let without_main = resolve_overlay_layout(&layout, &areas, None);
        assert_eq!(without_main.display.id, "second");

        let no_primary = resolve_overlay_layout(
            &layout,
            &[area("only", 1512.0, 945.0, false)],
            Some("gone"),
        );
        assert_eq!(no_primary.display.id, "only");
    }

    #[test]
    fn no_displays_at_all_still_yields_a_safe_frame() {
        let resolved = resolve_overlay_layout(&OverlayLayout::default(), &[], None);

        assert!(resolved.pill.width > 0.0 && resolved.pill.height > 0.0);
        assert!(resolved.pill.x >= 0.0 && resolved.pill.y >= 0.0);
        assert!(resolved.pill.x + resolved.pill.width <= resolved.display.width);
        assert!(resolved.panel.y + resolved.panel.height <= resolved.display.height);
    }

    #[test]
    fn defaults_reproduce_the_phase_one_placement() {
        let resolved = resolve_overlay_layout(
            &OverlayLayout::default(),
            &[area("built-in", 1512.0, 945.0, true)],
            None,
        );

        // Pill: centered, 92 points of clearance above the work-area bottom.
        let pill = resolved.pill;
        assert!((pill.x - (1512.0 - pill.width) / 2.0).abs() < 2.0);
        assert!((resolved.display.height - (pill.y + pill.height) - 92.0).abs() < 2.0);
        assert_eq!(pill.height, PILL_HEIGHT);

        // Panel: 20 points from the top and right edges.
        let panel = resolved.panel;
        assert!((panel.y - 20.0).abs() < 2.0);
        assert!((resolved.display.width - (panel.x + panel.width) - 20.0).abs() < 2.0);
        assert!(resolved.adjustments.is_empty());
    }

    #[test]
    fn left_dock_inset_keeps_frames_out_of_the_dock() {
        // 1512-wide screen, 96-point Dock on the left, menu bar on top.
        let area = inset_area("built-in", 96.0, 0.0, 1416.0, 957.0);
        let resolved = resolve_overlay_layout(&OverlayLayout::default(), &[area.clone()], None);

        let pill = display_rect(&resolved.display, resolved.pill);
        let panel = display_rect(&resolved.display, resolved.panel);
        assert!(pill.x >= 96.0);
        assert!(panel.x >= 96.0);
        assert!(panel.x + panel.width <= 1512.0);
        assert!(pill.y + pill.height <= 982.0);
    }

    #[test]
    fn right_dock_inset_keeps_frames_out_of_the_dock() {
        let area = inset_area("built-in", 0.0, 0.0, 1416.0, 957.0);
        let layout = OverlayLayout {
            panel: OverlayFrame {
                x_ratio: 1.0,
                ..default_panel_frame()
            },
            ..OverlayLayout::default()
        };

        let resolved = resolve_overlay_layout(&layout, &[area.clone()], None);
        let panel = display_rect(&resolved.display, resolved.panel);

        assert!(panel.x + panel.width <= 1416.0);
    }

    #[test]
    fn bottom_dock_inset_keeps_frames_out_of_the_dock() {
        // 982-tall screen, 25-point menu bar, Dock on the bottom.
        let area = inset_area("built-in", 0.0, 25.0, 1512.0, 857.0);
        let layout = OverlayLayout {
            pill: OverlayFrame {
                y_ratio: 1.0,
                ..default_pill_frame()
            },
            ..OverlayLayout::default()
        };

        let resolved = resolve_overlay_layout(&layout, &[area.clone()], None);
        let pill = display_rect(&resolved.display, resolved.pill);

        assert!(pill.y >= 25.0);
        assert!(pill.y + pill.height <= 25.0 + 857.0);
    }

    #[test]
    fn logical_frames_are_independent_of_the_backing_scale_factor() {
        let layout = saved(520.0, 600.0);
        let non_retina = LogicalWorkArea {
            scale_factor: 1.0,
            ..area("display", 1920.0, 1080.0, true)
        };
        let retina = LogicalWorkArea {
            scale_factor: 2.0,
            ..area("display", 1920.0, 1080.0, true)
        };

        let at_1x = resolve_overlay_layout(&layout, &[non_retina], None);
        let at_2x = resolve_overlay_layout(&layout, &[retina], None);

        assert_eq!(at_1x.pill, at_2x.pill);
        assert_eq!(at_1x.panel, at_2x.panel);
    }

    #[test]
    fn invalid_frames_fall_back_to_defaults() {
        let areas = [area("built-in", 1512.0, 945.0, true)];
        let garbage = OverlayLayout {
            display_affinity: "built-in".into(),
            pill: OverlayFrame {
                x_ratio: f64::NAN,
                y_ratio: 4.0,
                width: -10.0,
                height: f64::INFINITY,
            },
            panel: OverlayFrame {
                x_ratio: -0.5,
                y_ratio: 0.2,
                width: 0.0,
                height: f64::NAN,
            },
        };

        let resolved = resolve_overlay_layout(&garbage, &areas, None);
        let expected = resolve_overlay_layout(&OverlayLayout::default(), &areas, None);

        assert_eq!(resolved.pill, expected.pill);
        assert_eq!(resolved.panel, expected.panel);
    }

    #[test]
    fn undersized_frames_are_raised_to_the_supported_minimum() {
        let areas = [area("built-in", 1512.0, 945.0, true)];
        let layout = OverlayLayout {
            pill: OverlayFrame {
                width: 40.0,
                ..default_pill_frame()
            },
            panel: OverlayFrame {
                width: 10.0,
                height: 10.0,
                ..default_panel_frame()
            },
            ..OverlayLayout::default()
        };

        let resolved = resolve_overlay_layout(&layout, &areas, None);

        assert_eq!(resolved.pill.width, PILL_MIN_WIDTH);
        assert_eq!(resolved.panel.width, PANEL_MIN_WIDTH);
        assert_eq!(resolved.panel.height, PANEL_MIN_HEIGHT);
    }

    #[test]
    fn frames_never_exceed_a_small_work_area() {
        let areas = [area("tiny", 420.0, 380.0, true)];

        let resolved = resolve_overlay_layout(&OverlayLayout::default(), &areas, None);

        assert!(resolved.pill.width <= 420.0);
        assert!(resolved.pill.height <= 380.0);
        assert!(resolved.panel.width <= 420.0);
        assert!(resolved.panel.height <= 380.0);
        assert!(resolved.pill.x + resolved.pill.width <= 420.0);
        assert!(resolved.panel.y + resolved.panel.height <= 380.0);
    }

    #[test]
    fn pill_height_is_always_the_supported_fixed_height() {
        let areas = [area("built-in", 1512.0, 945.0, true)];
        let layout = OverlayLayout {
            pill: OverlayFrame {
                height: 400.0,
                ..default_pill_frame()
            },
            ..OverlayLayout::default()
        };

        let resolved = resolve_overlay_layout(&layout, &areas, None);

        assert_eq!(resolved.pill.height, PILL_HEIGHT);
    }
}
