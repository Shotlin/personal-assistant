pub const INITIAL_MAIN_WIDTH: f64 = 1180.0;
pub const INITIAL_MAIN_HEIGHT: f64 = 760.0;
pub const MIN_MAIN_WIDTH: f64 = 900.0;
pub const MIN_MAIN_HEIGHT: f64 = 620.0;

#[derive(Debug, Clone, PartialEq)]
pub struct LogicalWorkArea {
    pub id: String,
    /// Logical desktop origin of the usable frame.
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
    pub scale_factor: f64,
    pub is_primary: bool,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct NormalWindowBounds {
    /// Logical coordinates relative to the selected usable work area.
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
}

#[derive(Debug, Clone, PartialEq)]
pub struct SavedMainWindow {
    pub display_id: String,
    pub normal: NormalWindowBounds,
    pub maximized: bool,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ResolvedRestore {
    pub work_area: LogicalWorkArea,
    pub normal: NormalWindowBounds,
    pub maximized: bool,
}

pub fn resolve_restore(
    saved: Option<&SavedMainWindow>,
    areas: &[LogicalWorkArea],
) -> ResolvedRestore {
    let work_area = select_work_area(saved.map(|state| state.display_id.as_str()), areas);
    let requested = saved
        .map(|state| state.normal)
        .filter(bounds_are_valid)
        .unwrap_or_else(|| centered_initial(&work_area));
    ResolvedRestore {
        normal: clamp_normal_bounds(requested, &work_area),
        maximized: saved.is_some_and(|state| state.maximized),
        work_area,
    }
}

fn select_work_area(saved_display_id: Option<&str>, areas: &[LogicalWorkArea]) -> LogicalWorkArea {
    areas
        .iter()
        .find(|area| Some(area.id.as_str()) == saved_display_id)
        .or_else(|| areas.iter().find(|area| area.is_primary))
        .or_else(|| areas.first())
        .cloned()
        .unwrap_or_else(fallback_work_area)
}

fn fallback_work_area() -> LogicalWorkArea {
    LogicalWorkArea {
        id: "fallback".into(),
        x: 0.0,
        y: 0.0,
        width: 1440.0,
        height: 900.0,
        scale_factor: 1.0,
        is_primary: true,
    }
}

fn bounds_are_valid(bounds: &NormalWindowBounds) -> bool {
    [bounds.x, bounds.y, bounds.width, bounds.height]
        .into_iter()
        .all(f64::is_finite)
        && bounds.width > 0.0
        && bounds.height > 0.0
}

fn centered_initial(area: &LogicalWorkArea) -> NormalWindowBounds {
    let width = fit_dimension(INITIAL_MAIN_WIDTH, MIN_MAIN_WIDTH, area.width);
    let height = fit_dimension(INITIAL_MAIN_HEIGHT, MIN_MAIN_HEIGHT, area.height);
    NormalWindowBounds {
        x: ((area.width - width) / 2.0).max(0.0),
        y: ((area.height - height) / 2.0).max(0.0),
        width,
        height,
    }
}

fn clamp_normal_bounds(bounds: NormalWindowBounds, area: &LogicalWorkArea) -> NormalWindowBounds {
    let width = fit_dimension(bounds.width, MIN_MAIN_WIDTH, area.width);
    let height = fit_dimension(bounds.height, MIN_MAIN_HEIGHT, area.height);
    NormalWindowBounds {
        x: bounds.x.clamp(0.0, (area.width - width).max(0.0)),
        y: bounds.y.clamp(0.0, (area.height - height).max(0.0)),
        width,
        height,
    }
}

fn fit_dimension(requested: f64, minimum: f64, available: f64) -> f64 {
    let available = available.max(1.0);
    requested.max(minimum).min(available)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn area(
        id: &str,
        x: f64,
        y: f64,
        width: f64,
        height: f64,
        scale_factor: f64,
        is_primary: bool,
    ) -> LogicalWorkArea {
        LogicalWorkArea {
            id: id.into(),
            x,
            y,
            width,
            height,
            scale_factor,
            is_primary,
        }
    }

    fn bounds(x: f64, y: f64, width: f64, height: f64) -> NormalWindowBounds {
        NormalWindowBounds {
            x,
            y,
            width,
            height,
        }
    }

    #[test]
    fn missing_external_display_uses_primary_visible_area() {
        let areas = vec![area("built-in", 0.0, 25.0, 1512.0, 945.0, 2.0, true)];
        let saved = SavedMainWindow {
            display_id: "external".into(),
            normal: bounds(80.0, 40.0, 1180.0, 760.0),
            maximized: false,
        };

        let resolved = resolve_restore(Some(&saved), &areas);

        assert_eq!(resolved.work_area.id, "built-in");
        assert!(resolved.normal.x >= 0.0);
        assert!(resolved.normal.y >= 0.0);
        assert!(resolved.normal.x + resolved.normal.width <= resolved.work_area.width);
        assert!(resolved.normal.y + resolved.normal.height <= resolved.work_area.height);
    }

    #[test]
    fn malformed_or_offscreen_normal_bounds_are_clamped_not_reused() {
        let areas = vec![area("built-in", 0.0, 25.0, 1512.0, 945.0, 2.0, true)];
        let saved = SavedMainWindow {
            display_id: "built-in".into(),
            normal: bounds(-10000.0, f64::NAN, 99999.0, -1.0),
            maximized: true,
        };

        let resolved = resolve_restore(Some(&saved), &areas);

        assert_eq!(resolved.normal.width, 1180.0);
        assert_eq!(resolved.normal.height, 760.0);
        assert!(resolved.maximized);
        assert!(resolved.normal.x >= 0.0 && resolved.normal.y >= 0.0);
        assert!(resolved.normal.x + resolved.normal.width <= resolved.work_area.width);
        assert!(resolved.normal.y + resolved.normal.height <= resolved.work_area.height);
    }

    #[test]
    fn restore_retains_maximized_flag_but_resolves_normal_bounds_first() {
        let areas = vec![area("built-in", 0.0, 25.0, 1512.0, 945.0, 2.0, true)];
        let saved = SavedMainWindow {
            display_id: "built-in".into(),
            normal: bounds(110.0, 90.0, 1040.0, 700.0),
            maximized: true,
        };

        let resolved = resolve_restore(Some(&saved), &areas);

        assert_eq!(resolved.normal, bounds(110.0, 90.0, 1040.0, 700.0));
        assert!(resolved.maximized);
    }
}
