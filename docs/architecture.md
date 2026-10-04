# Superpaper Next — module map

Superpaper's public entry point, profile format and wx event API are unchanged.
This refactor moves complete, cohesive responsibilities out of large files while
preserving older import locations wherever existing callers may rely on them.

## Wallpaper Studio interface

| Module | Responsibility |
| --- | --- |
| `gui.py` | Studio settings, profile edits, Apply/Save/Revert, source controls, workspace navigation and event wiring |
| `wallpaper_preview_panel.py` | Live wallpaper display, aspect/crop preview, split-comparison handles, mouse dragging, bezel and position overlays |
| `studio_widgets.py` | Reusable, custom-painted navigation/action/segment buttons and keyboard support |
| `configuration_dialogs.py` | Legacy compatibility imports; **no dialog implementation** |
| `display_position_dialog.py` | Manual positions and physical monitor offsets |
| `perspective_dialog.py` | Rotation and perspective tuning and test rendering |
| `settings_dialog.py` | Application-wide settings window and controls |
| `help_dialog.py` | Main help and contextual help popups |

The original `superpaper.gui.WallpaperPreviewPanel` name still resolves to
the extracted preview class, and the original
`superpaper.configuration_dialogs.{DisplayPositionEntry,PerspectiveConfig,
SettingsFrame,SettingsPanel,HelpFrame,HelpPanel,HelpPopup}` exports remain
available. New code should import the owning module directly; existing code
does not need to change.

## Wallpaper selection and rendering

| Module | Responsibility |
| --- | --- |
| `data.py` | Managed and temporary profiles, safe file writes, profile validation and serialization, application settings |
| `wallpaper_sources.py` | Source directory enumeration, supported image identity, per-monitor sorting, collision-avoidant slideshow selection |
| `source_paths.py` | Path identities, picker selection validity and explicit replacement reconciliation |
| `wallpaper_processing.py` | Display discovery, render dispatch and OS-specific wallpaper application |
| `monitor_geometry.py` | PPI-normalized sizes, monitor crop rectangles, canvas sizing, bezel-inclusive canvas |
| `image_framing.py` | Image cover-fit resizing, original-space crop, zoom and pan |
| `preview_geometry.py` | Headless preview layout, comparison hit testing, crop positioning and pointer coordinate math |
| `image_adjustments.py` | Local brightness, contrast and saturation |
| `cloud_upscale.py` | Opt-in external upscale and credit-saving cache |
| `local_shaders.py` | Local Anime4K/libplacebo shader selection, invocation, caching and fallback |

For compatibility, `ProfileData.Filehandler` is a thin subclass of
`wallpaper_sources.SourceFileHandler`; existing error-dialog callbacks are
injected by `data.py`. Existing render functions such as
`wallpaper_processing.resize_to_fill` and
`wallpaper_processing.compute_working_canvas` remain importable under their
original names. The on-disk `.profile` format and `selected=` semantics
have not changed.

## Where to debug or add features

- **Preview positioning / split-slider behavior:** start with
  `wallpaper_preview_panel.py` and `preview_geometry.py`.
- **Change Image / Apply vs Save:** start with `gui.py`, `source_paths.py`
  and `data.py`; test the serialized profile actually passed to rendering.
- **Slideshow and cross-display image allocation:** start with
  `wallpaper_sources.py`; do not change selected-image persistence to fix a
  batch scheduling problem.
- **PPI / spanning / bezel geometry:** start with `monitor_geometry.py`
  (the math) and `wallpaper_processing.py` (render composition).
- **KDE file picker / desktop setter:** `native_picker.py` and
  `wallpaper_processing.py` respectively.
- **Reusable Studio controls:** `studio_widgets.py`; change only the
  settings form `gui.py` if wiring or layout must change.
- **Perspective and physical monitor UI:** `perspective_dialog.py` and
  `display_position_dialog.py`.

## Compatibility and test strategy

`tests/test_modular_boundaries.py` verifies the public alias contracts and
basic pure rendering behavior. Existing behavior tests verify source selection,
profile persistence, geometry, local processing, shader fallback, and rendering.

The headless CI environment does **not** exercise native wx/GTK/KDialog
windows, Plasma Wayland monitor APIs or AMD Vulkan. On a real system, manually
verify the Studio opens, opens each dialog, redraws the preview after changing
an image, preserves multi-monitor assignments, and applies and saves correctly.
Cloud API credits must never be consumed by automated tests.

Some larger modules remain intentionally cohesive to avoid converting
wxPython event handlers and tightly coupled desktop setter state into dynamic
delegation. Future extractions should be based on exercised boundaries rather
than file length alone.
