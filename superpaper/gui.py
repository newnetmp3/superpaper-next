"""
New wallpaper configuration GUI for Superpaper.
"""

import copy
import os
import sys
import tempfile
import time
from operator import itemgetter
from typing import Literal, overload

from PIL import Image, ImageEnhance, ImageOps, UnidentifiedImageError

import superpaper.sp_logging as sp_logging
import superpaper.wallpaper_processing as wpproc
from superpaper.cloud_upscale import UPSCALE_MODES, locally_sharpen, normalize_scale_mode, normalize_sharpen
from superpaper.configuration_dialogs import (
    DisplayPositionEntry,
    HelpFrame,
    HelpPopup,
    PerspectiveConfig,
)
from superpaper.data import (
    CLIProfileData,
    GeneralSettingsData,
    TempProfileData,
    delete_managed_profile,
    managed_profile_for_selection,
    open_profile,
    parse_profile_file,
    save_managed_profile,
)
from superpaper.image_adjustments import apply_local_adjustments, normalize_adjustment, split_local_preview
from superpaper.local_shaders import ShaderImportError, available_shaders, import_shader_pack, normalize_shader
from superpaper.message_dialog import show_message_dialog
from superpaper.native_picker import NativePickerError, pick_kde_paths
from superpaper.preview_geometry import (
    comparison_drag_fraction,
    comparison_hit_region,
    crop_overflow,
    desktop_preview_layout,
    fit_preview_canvas,
    has_positive_area,
    pan_offset_for_drag,
    usable_preview_area,
    wheel_scroll_units,
)
from superpaper.profile_id import ProfileId, ProfileIdError
from superpaper.source_paths import IMAGE_EXTENSIONS, source_identity
from superpaper.sp_paths import RESOURCES_PATH, TRAY_ICON
from superpaper.wallpaper_processing import (
    change_wallpaper_job,
    resize_to_fill,
)

try:
    import wx  # pyright: ignore[reportMissingImports]  # ty:ignore[unresolved-import]
    import wx.adv  # pyright: ignore[reportMissingImports]  # ty:ignore[unresolved-import]
except ImportError:
    sys.exit()


class StudioNavigationButton(wx.Control):
    """Theme-independent sidebar entry, drawn consistently under GTK and KDE."""

    def __init__(self, parent, label, icon):
        super().__init__(parent, style=wx.BORDER_NONE)
        self.label = label
        self.icon = icon
        self.selected = False
        self.hovered = False
        self.SetMinSize(wx.Size(148, 39))
        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)
        self.SetCursor(wx.Cursor(wx.CURSOR_HAND))
        self.Bind(wx.EVT_PAINT, self._paint)
        self.Bind(wx.EVT_ENTER_WINDOW, self._enter)
        self.Bind(wx.EVT_LEAVE_WINDOW, self._leave)
        self.Bind(wx.EVT_LEFT_UP, self._activate)
        self.Bind(wx.EVT_KEY_DOWN, self._key_down)

    def SetSelected(self, selected):
        self.selected = selected
        self.Refresh()

    def _enter(self, event):
        self.hovered = True
        self.Refresh()

    def _leave(self, event):
        self.hovered = False
        self.Refresh()

    def _activate(self, event):
        clicked = wx.CommandEvent(wx.wxEVT_BUTTON, self.GetId())
        clicked.SetEventObject(self)
        wx.PostEvent(self, clicked)

    def _key_down(self, event):
        if event.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_SPACE):
            self._activate(event)
        else:
            event.Skip()

    def _paint(self, event):
        dc = wx.AutoBufferedPaintDC(self)
        w, h = self.GetClientSize()
        dc.SetBackground(wx.Brush(wx.Colour(25, 34, 48)))
        dc.Clear()
        background = (
            wx.Colour(35, 110, 207)
            if self.selected
            else wx.Colour(41, 58, 79)
            if self.hovered
            else wx.Colour(25, 34, 48)
        )
        dc.SetBrush(wx.Brush(background))
        dc.SetPen(wx.Pen(wx.Colour(61, 136, 234) if self.selected else background))
        dc.DrawRoundedRectangle(3, 2, max(1, w - 6), max(1, h - 4), 7)
        dc.SetTextForeground(wx.Colour(247, 250, 255) if self.selected else wx.Colour(206, 219, 235))
        font = self.GetFont()
        font.SetWeight(wx.FONTWEIGHT_BOLD if self.selected else wx.FONTWEIGHT_NORMAL)
        dc.SetFont(font)
        icon_w, icon_h = dc.GetTextExtent(self.icon)
        dc.DrawText(self.icon, 15, max(0, (h - icon_h) // 2))
        _, text_h = dc.GetTextExtent(self.label)
        dc.DrawText(self.label, max(39, icon_w + 21), max(0, (h - text_h) // 2))


class StudioActionButton(StudioNavigationButton):
    """A consistent, accessible painted action for the primary Studio commands."""

    def __init__(self, parent, label, primary=False):
        super().__init__(parent, label, "")
        self.primary = primary
        self.SetMinSize(wx.Size(max(96, len(label) * 8 + 26), 37))

    def SetLabel(self, label):
        self.label = label
        self.Refresh()

    def _paint(self, event):
        dc = wx.AutoBufferedPaintDC(self)
        w, h = self.GetClientSize()
        dc.SetBackground(wx.Brush(wx.Colour(17, 27, 41)))
        dc.Clear()
        if self.primary:
            background = wx.Colour(48, 132, 241) if self.hovered else wx.Colour(34, 107, 215)
        else:
            background = wx.Colour(57, 75, 98) if self.hovered else wx.Colour(43, 57, 75)
        dc.SetPen(wx.Pen(wx.Colour(72, 99, 131) if not self.primary else wx.Colour(67, 148, 246)))
        dc.SetBrush(wx.Brush(background))
        dc.DrawRoundedRectangle(2, 2, max(1, w - 4), max(1, h - 4), 6)
        font = self.GetFont()
        font.SetWeight(wx.FONTWEIGHT_BOLD if self.primary else wx.FONTWEIGHT_NORMAL)
        dc.SetFont(font)
        dc.SetTextForeground(wx.Colour(247, 251, 255))
        text_w, text_h = dc.GetTextExtent(self.label)
        dc.DrawText(self.label, max(0, (w - text_w) // 2), max(0, (h - text_h) // 2))


class StudioSegmentButton(StudioActionButton):
    """Compact painted tabs that retain keyboard and mouse button semantics."""

    def __init__(self, parent, label):
        super().__init__(parent, label)
        # Two columns fit inside the 294px Processing inspector on GTK.
        self.SetMinSize(wx.Size(112, 35))

    def SetSelected(self, selected):
        self.primary = bool(selected)
        super().SetSelected(selected)


class ConfigFrame(wx.Frame):
    """Wallpaper configuration dialog frame base class."""

    def __init__(self, parent_tray_obj):
        wx.Frame.__init__(self, parent=None, title="Superpaper Next - Wallpaper Studio")
        self.frame_sizer = wx.BoxSizer(wx.VERTICAL)
        config_panel = WallpaperSettingsPanel(self, parent_tray_obj)
        self.frame_sizer.Add(config_panel, 1, wx.EXPAND)
        self.SetAutoLayout(True)
        self.SetSizer(self.frame_sizer)
        self.SetIcon(wx.Icon(TRAY_ICON, wx.BITMAP_TYPE_PNG))
        self.Fit()
        self.SetMinSize(wx.Size(1020, 645))
        usable = wx.GetClientDisplayRect()
        self.SetSize(wx.Size(min(1510, usable.width), min(875, usable.height)))
        self.Layout()
        self.Center()
        self.Show()


class WallpaperSettingsPanel(wx.ScrolledWindow):
    """This class defines the wallpaper config dialog UI."""

    def ShouldScrollToChildOnFocus(self, child):
        """Retain the user's scroll position when a child control gains focus.

        wx.ScrolledWindow ordinarily jumps to the focused child when focus
        returns to the window. Wallpaper paths and other controls may be far
        below the current viewport, so this used to snap the page to the end
        simply by moving the pointer out and back.
        """
        return False

    def SendAutoScrollEvents(self, event):
        """Never scroll the settings window just because a drag leaves it.

        Explicit wheel movement and scrollbar manipulation still use the
        normal wx.ScrolledWindow code paths.
        """
        return False

    def __init__(self, parent, parent_tray_obj):
        wx.ScrolledWindow.__init__(self, parent, style=wx.VSCROLL)
        self.SetScrollRate(0, 20)
        self.frame = parent
        self.parent_tray_obj = parent_tray_obj
        self.current_profile_id = None
        self.expected_source_identity = None
        self.loaded_profile = None
        self.sizer_main = wx.BoxSizer(wx.HORIZONTAL)
        self.sizer_top_half = wx.BoxSizer(wx.HORIZONTAL)
        self.SetBackgroundColour(wx.Colour(17, 27, 41))  # wallpaper/monitor preview
        self.sizer_bottom_half = wx.BoxSizer(wx.VERTICAL)  # settings, buttons etc
        # bottom_half: setting sizers
        self.sizer_profiles = wx.BoxSizer(wx.HORIZONTAL)
        self.sizer_setting_sizers = wx.BoxSizer(wx.HORIZONTAL)
        self.sizer_settings_left = wx.BoxSizer(wx.VERTICAL)
        self.sizer_settings_right = wx.BoxSizer(wx.HORIZONTAL)
        # bottom_half: bottom button row
        self.sizer_bottom_buttonrow = wx.BoxSizer(wx.HORIZONTAL)

        self.defdir = GeneralSettingsData().browse_default_dir
        # settings GUI properties
        self.tc_width = 160  # standard width for wx.TextCtrl etc elements.
        self.show_advanced_settings = False
        self.use_multi_image = False
        self.multi_column_listc = False
        BMP_SIZE = 32
        self.tsize = (BMP_SIZE, BMP_SIZE)
        self.image_list = wx.ImageList(BMP_SIZE, BMP_SIZE)
        self._source_wheel_remainder = 0.0

        # top half
        self.resized = False
        # Profile change tracking. ``_clean_serial`` holds the serialized form of
        # the profile as last saved/loaded; the Save and Revert buttons enable
        # only when the current fields serialize differently. The wallpaper
        # (background) selection is deliberately excluded (see
        # _collect_temp_profile). ``_loading`` suppresses tracking while fields
        # are being (re)populated programmatically.
        self._clean_serial = None
        self._loading = False
        # System (display) settings change tracking. ``_system_clean`` holds a
        # snapshot of the shared DisplaySystem as last saved/loaded; the system
        # Save/Revert enable only when the live DisplaySystem differs from it.
        self._system_clean = None
        # This is staged dialog state. Do not publish it until Apply/Save; live
        # field edits must not alter the display system used by background jobs.
        self.display_sys = wpproc.DisplaySystem(update_globals=False)
        # self.wpprev_pnl = WallpaperPreviewPanel(self.frame, self.display_sys)
        self.wpprev_pnl = WallpaperPreviewPanel(self, self.display_sys)
        # A panorama must fill the useful height of the stage instead of
        # sitting as a thin strip in an oversized black rectangle.
        self.wpprev_pnl.SetMinSize(wx.Size(450, 265))
        self.wpprev_pnl.SetMaxSize(wx.Size(10000, 480))
        self.sizer_top_half.Add(self.wpprev_pnl, 1, wx.EXPAND | wx.ALL, 5)
        # self.sizer_top_half.SetMinSize((400,200))
        # self.sizer_top_half.SetMinSize()
        self.wpprev_pnl.Bind(wx.EVT_SIZE, self.onResize)
        self.wpprev_pnl.Bind(wx.EVT_IDLE, self.onIdle)

        # bottom half

        # profile sizer contents
        self.create_sizer_profiles()

        # settings sizer left contents
        self.create_sizer_settings_left()

        self.create_sizer_settings_advanced()

        # settings sizer right contents
        self.create_sizer_settings_right()

        # bottom button row contents
        self.create_sizer_bottom_buttonrow()

        # collapsible system (display-wide) settings band
        self.create_sizer_system_band()

        # Main Studio: a wide permanent preview at left and compact inspector
        # at right. Sources appear on demand below the preview, not as a giant
        # table beneath all controls.
        self.sizer_setting_sizers.Add(self.sizer_settings_left, 1, wx.EXPAND | wx.ALL, 5)
        self.sizer_displays = wx.BoxSizer(wx.VERTICAL)
        self.sizer_displays.Add(self.radiobox_spanmode, 0, wx.EXPAND | wx.ALL, 5)
        self.sizer_displays.Add(self.system_pane, 0, wx.EXPAND | wx.ALL, 5)
        self.sizer_displays.Add(self.sizer_setting_adv, 0, wx.EXPAND | wx.ALL, 5)
        self.sizer_displays.Hide(self.sizer_setting_adv, recursive=True)
        self.sizer_processing = wx.BoxSizer(wx.VERTICAL)
        self.sizer_processing.Add(self.sizer_setting_processing, 0, wx.EXPAND | wx.ALL, 5)
        self.sizer_advanced = wx.BoxSizer(wx.VERTICAL)
        self.sizer_advanced.Add(self.sizer_setting_slideshow, 0, wx.EXPAND | wx.ALL, 5)
        self.sizer_advanced.Add(self.sizer_setting_hotkey, 0, wx.EXPAND | wx.ALL, 5)
        self.sizer_gallery = wx.BoxSizer(wx.VERTICAL)
        self.create_studio_gallery()
        self.create_studio_navigation()
        self.sizer_bottom_half.Add(self.studio_header, 0, wx.EXPAND | wx.ALL, 10)
        self.sizer_bottom_half.Add(self.sizer_profiles, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        self.sizer_bottom_half.Hide(self.sizer_profiles, recursive=True)

        self.studio_editor_row = wx.BoxSizer(wx.HORIZONTAL)
        self.studio_canvas_column = wx.BoxSizer(wx.VERTICAL)
        self.studio_canvas_column.Add(self.sizer_top_half, 0, wx.EXPAND | wx.ALL, 4)
        self.studio_canvas_column.Add(self.studio_preview_tools, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 7)
        self.studio_canvas_column.Add(self.studio_alignment_tools, 0, wx.EXPAND | wx.ALL, 7)
        self.studio_canvas_column.Add(self.studio_display_editor, 0, wx.EXPAND | wx.ALL, 7)
        self.studio_canvas_column.Hide(self.studio_display_editor, recursive=True)
        self.studio_canvas_column.Add(self.studio_source_tools, 0, wx.EXPAND | wx.ALL, 8)
        self.studio_canvas_column.Add(self.studio_quick_profiles, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 6)
        self.studio_canvas_column.Add(self.sizer_settings_right, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        self.studio_canvas_column.Hide(self.sizer_settings_right, recursive=True)
        self.studio_canvas_column.Add(self.sizer_gallery, 1, wx.EXPAND | wx.ALL, 7)
        self.studio_canvas_column.Hide(self.sizer_gallery, recursive=True)
        self.studio_editor_row.Add(self.studio_canvas_column, 1, wx.EXPAND | wx.ALL, 2)
        self.studio_editor_row.Add(wx.StaticLine(self, style=wx.LI_VERTICAL), 0, wx.EXPAND | wx.TOP | wx.BOTTOM, 8)

        self.studio_inspector = wx.BoxSizer(wx.VERTICAL)
        self.studio_inspector.SetMinSize(wx.Size(294, -1))
        self.studio_inspector.Add(self.studio_workspace_title, 0, wx.EXPAND | wx.ALL, 9)
        self.studio_inspector.Add(self.studio_image_card, 0, wx.EXPAND | wx.ALL, 7)
        self.studio_inspector.Add(self.studio_fit_row, 0, wx.EXPAND | wx.ALL, 7)
        self._workspace_sizers = {
            "Wallpapers": self.sizer_setting_sizers,
            "Displays": self.sizer_displays,
            "Profiles": self.studio_profile_inspector,
            "Processing": self.sizer_processing,
            "Advanced": self.sizer_advanced,
        }
        for section in self._workspace_sizers.values():
            self.studio_inspector.Add(section, 0, wx.EXPAND | wx.ALL, 4)
        self.studio_editor_row.Add(self.studio_inspector, 0, wx.EXPAND | wx.ALL, 6)
        self.sizer_bottom_half.Add(self.studio_editor_row, 1, wx.EXPAND | wx.ALL, 3)
        self.sizer_bottom_half.Add(wx.StaticLine(self), 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        self.sizer_bottom_half.Add(self.sizer_bottom_buttonrow, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        self.sizer_bottom_half.Add(self.studio_status, 0, wx.EXPAND | wx.ALL, 10)
        self.sizer_main.Add(self.studio_sidebar, 0, wx.EXPAND | wx.ALL, 5)
        self.sizer_main.Add(self.sizer_bottom_half, 1, wx.EXPAND)
        self.SetSizer(self.sizer_main)
        self._workspace = None
        self._processing_compare_initialized = False
        self._set_studio_workspace("Wallpapers")
        self.FitInside()

        # Change tracking: text fields and naked choices propagate their command
        # events up to the panel, so a single panel-level binding covers them
        # without per-control wiring. Controls that already have a dedicated
        # handler (checkboxes, span radio, sliders, the path list) call
        # _update_dirty_state() from within that handler instead.
        self.Bind(wx.EVT_TEXT, self._on_field_changed)
        self.Bind(wx.EVT_COMBOBOX, self._on_field_changed)

        if self.parent_tray_obj.active_profile:
            active_prof_name = self.parent_tray_obj.active_profile.name
            active_id = self.choice_profiles.FindString(active_prof_name)
            self.choice_profiles.SetSelection(active_id)
            self.populate_fields(self.parent_tray_obj.active_profile)
        else:
            # No active profile: start from a clean, empty new-profile baseline.
            self._set_clean_baseline()

        # Record the loaded display-wide settings as the system clean baseline.
        self._set_system_baseline()
        self._refresh_profile_gallery()

        ### End __init__.

    #
    # Sizer creation methods
    #
    def create_sizer_profiles(self):
        # choice menu
        # self.list_of_profiles = list_profiles()
        self.list_of_profiles = self.parent_tray_obj.list_of_profiles
        self.profnames = []
        for prof in self.list_of_profiles:
            self.profnames.append(prof.name)
        self.profnames.append("Create a new profile")
        self.choice_profiles = wx.ComboBox(self, -1, name="ProfileChoice", choices=self.profnames, style=wx.CB_READONLY)
        self.choice_profiles.Bind(wx.EVT_COMBOBOX, self.onSelect)
        st_choice_profiles = wx.StaticText(self, -1, "Setting profiles:")
        # name txt ctrl
        st_name = wx.StaticText(self, -1, "Profile name:")
        self.tc_name = wx.TextCtrl(self, -1, size=wx.Size(self.tc_width, -1))
        self.tc_name.SetMaxLength(14)
        # buttons
        self.button_save = wx.Button(self, label="Save")
        self.button_revert = wx.Button(self, label="Revert")
        self.button_delete = wx.Button(self, label="Delete")
        self.button_save.Bind(wx.EVT_BUTTON, self.onSave)
        self.button_revert.Bind(wx.EVT_BUTTON, self.onRevert)
        self.button_delete.Bind(wx.EVT_BUTTON, self.onDeleteProfile)
        self.button_save.SetToolTip(wx.ToolTip("Save the current changes permanently to the profile."))
        self.button_revert.SetToolTip(wx.ToolTip("Discard unsaved changes and reload the saved profile."))
        # Save/Revert start disabled; they enable only when there are unsaved
        # changes to the profile configuration.
        self.button_save.Disable()
        self.button_revert.Disable()

        # Add elements to the sizer
        self.sizer_profiles.Add(st_choice_profiles, 0, wx.CENTER | wx.ALL, 5)
        self.sizer_profiles.Add(self.choice_profiles, 0, wx.CENTER | wx.ALL, 5)
        self.sizer_profiles.Add(st_name, 0, wx.CENTER | wx.ALL, 5)
        self.sizer_profiles.Add(self.tc_name, 0, wx.CENTER | wx.ALL, 5)

    def create_sizer_settings_left(self):
        # span mode sizer
        radio_choices_spanmode = [
            "Simple span",
            "Advanced span",
            "Separate image for every display",
        ]
        self.radiobox_spanmode = wx.RadioBox(
            self, wx.ID_ANY, label="Span mode", choices=radio_choices_spanmode, style=wx.RA_VERTICAL
        )
        self.radiobox_spanmode.Bind(wx.EVT_RADIOBOX, self.onSpanRadio)

        # slideshow sizer
        self.sizer_setting_slideshow = wx.StaticBoxSizer(wx.VERTICAL, self, "Wallpaper slideshow")
        statbox_parent_sshow = self.sizer_setting_slideshow.GetStaticBox()
        sizer_sshow_subsettings = wx.GridSizer(2, 5, 5)
        self.st_sshow_sort = wx.StaticText(statbox_parent_sshow, -1, "Slideshow order:")
        self.ch_sshow_sort = wx.ComboBox(
            statbox_parent_sshow,
            -1,
            name="SortChoice",
            #  size=(self.tc_width*0.7, -1),
            choices=["Shuffle", "Alphabetical", "Date seeded shuffle"],
            style=wx.CB_READONLY,
        )
        # ch_sort_size = self.ch_sshow_sort.GetClientSize()
        self.st_sshow_delay = wx.StaticText(statbox_parent_sshow, -1, "Delay (minutes):")
        self.tc_sshow_delay = wx.TextCtrl(
            statbox_parent_sshow,
            -1,
            # size=(self.tc_width*0.69, -1),
            # size=ch_sort_size,
            style=wx.TE_RIGHT,
        )
        self.cb_slideshow = wx.CheckBox(statbox_parent_sshow, -1, "Slideshow")
        self.st_sshow_sort.Disable()
        self.st_sshow_delay.Disable()
        self.tc_sshow_delay.Disable()
        self.ch_sshow_sort.Disable()
        self.cb_slideshow.Bind(wx.EVT_CHECKBOX, self.onCheckboxSlideshow)
        self.sizer_setting_slideshow.Add(self.cb_slideshow, 0, wx.ALIGN_LEFT | wx.ALL, 5)
        sizer_sshow_subsettings.Add(self.st_sshow_delay, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 0)
        sizer_sshow_subsettings.Add(self.tc_sshow_delay, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT | wx.ALIGN_LEFT, 3)
        sizer_sshow_subsettings.Add(self.st_sshow_sort, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 0)
        sizer_sshow_subsettings.Add(self.ch_sshow_sort, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT | wx.ALIGN_LEFT, 5)
        self.sizer_setting_slideshow.Add(sizer_sshow_subsettings, 0, wx.ALIGN_LEFT | wx.LEFT | wx.BOTTOM, 10)
        # self.sizer_setting_slideshow.AddSpacer(5)

        # hotkey sizer
        self.sizer_setting_hotkey = wx.StaticBoxSizer(wx.VERTICAL, self, "Hotkey")
        statbox_parent_hkey = self.sizer_setting_hotkey.GetStaticBox()
        self.cb_hotkey = wx.CheckBox(statbox_parent_hkey, -1, "Bind a hotkey to this profile")
        st_hotkey_bind = wx.StaticText(statbox_parent_hkey, -1, "Hotkey to bind:")
        st_hotkey_bind.Disable()
        self.tc_hotkey_bind = wx.TextCtrl(statbox_parent_hkey, -1, size=wx.Size(self.tc_width, -1))
        self.tc_hotkey_bind.Disable()
        self.tc_hotkey_bind.SetToolTip(wx.ToolTip("Modifiers: control, alt, shift, super.\nExample: control+super+x"))
        self.hotkey_bind_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.hotkey_bind_sizer.Add(st_hotkey_bind, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.hotkey_bind_sizer.Add(self.tc_hotkey_bind, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        help_bmp = wx.ArtProvider.GetBitmap(wx.ART_QUESTION, wx.ART_BUTTON, wx.Size(20, 20))
        self.button_help_hotkey = wx.BitmapButton(
            statbox_parent_hkey, bitmap=wx.BitmapBundle(help_bmp), name="butt_help_hk"
        )
        self.button_help_hotkey.Bind(wx.EVT_BUTTON, self.onHelpHotkey)
        self.hotkey_bind_sizer.Add(self.button_help_hotkey, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.sizer_setting_hotkey.Add(self.cb_hotkey, 0, wx.ALIGN_LEFT | wx.ALL, 5)
        self.sizer_setting_hotkey.Add(self.hotkey_bind_sizer, 0, wx.CENTER | wx.EXPAND | wx.ALL, 5)
        self.cb_hotkey.Bind(wx.EVT_CHECKBOX, self.onCheckboxHotkey)

        # image scaling & position sizer
        self.sizer_setting_zoom = wx.StaticBoxSizer(wx.VERTICAL, self, "Image scaling & position")
        statbox_parent_zoom = self.sizer_setting_zoom.GetStaticBox()
        zoom_grid = wx.FlexGridSizer(3, 3, 5, 5)
        zoom_grid.AddGrowableCol(1, 1)
        # Zoom
        st_zoom = wx.StaticText(statbox_parent_zoom, -1, "Zoom:")
        self.sld_zoom = wx.Slider(statbox_parent_zoom, -1, 100, 100, 400, style=wx.SL_HORIZONTAL)
        self.st_zoom_val = wx.StaticText(statbox_parent_zoom, -1, "100%", size=wx.Size(40, -1), style=wx.ALIGN_RIGHT)
        # Horizontal position
        st_offx = wx.StaticText(statbox_parent_zoom, -1, "Horizontal:")
        self.sld_offx = wx.Slider(statbox_parent_zoom, -1, 0, -100, 100, style=wx.SL_HORIZONTAL)
        self.st_offx_val = wx.StaticText(statbox_parent_zoom, -1, "0", size=wx.Size(40, -1), style=wx.ALIGN_RIGHT)
        # Vertical position
        st_offy = wx.StaticText(statbox_parent_zoom, -1, "Vertical:")
        self.sld_offy = wx.Slider(statbox_parent_zoom, -1, 0, -100, 100, style=wx.SL_HORIZONTAL)
        self.st_offy_val = wx.StaticText(statbox_parent_zoom, -1, "0", size=wx.Size(40, -1), style=wx.ALIGN_RIGHT)
        self.sld_zoom.SetToolTip(wx.ToolTip("Zoom further into the image. The wallpaper always fills the screen."))
        self.sld_offx.SetToolTip(wx.ToolTip("Move the visible area left or right within the image."))
        self.sld_offy.SetToolTip(wx.ToolTip("Move the visible area up or down within the image."))
        for sld in (self.sld_zoom, self.sld_offx, self.sld_offy):
            sld.Bind(wx.EVT_SLIDER, self.onZoomOffsetChange)
        zoom_grid.Add(st_zoom, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 5)
        zoom_grid.Add(self.sld_zoom, 1, wx.EXPAND)
        zoom_grid.Add(self.st_zoom_val, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        zoom_grid.Add(st_offx, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 5)
        zoom_grid.Add(self.sld_offx, 1, wx.EXPAND)
        zoom_grid.Add(self.st_offx_val, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        zoom_grid.Add(st_offy, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 5)
        zoom_grid.Add(self.sld_offy, 1, wx.EXPAND)
        zoom_grid.Add(self.st_offy_val, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.sizer_setting_zoom.Add(zoom_grid, 0, wx.EXPAND | wx.ALL, 5)

        self.sizer_setting_processing = wx.StaticBoxSizer(wx.VERTICAL, self, "Image processing and effects")
        processing_parent = self.sizer_setting_processing.GetStaticBox()
        self.processing_tab_buttons = {}
        # Avoid four cramped native GTK toggles overflowing this narrow inspector.
        # The painted two-column group uses the same visual language as navigation.
        processing_tabs = wx.GridSizer(2, 2, 5, 5)
        for name in ("Basic", "Cloud AI", "Shaders", "Adjustments"):
            tab = StudioSegmentButton(processing_parent, name)
            tab.Bind(wx.EVT_BUTTON, lambda event, section=name: self._set_processing_tab(section))
            processing_tabs.Add(tab, 0, wx.EXPAND)
            self.processing_tab_buttons[name] = tab
        self.sizer_setting_processing.Add(processing_tabs, 0, wx.EXPAND | wx.ALL, 5)
        self.sizer_processing_cloud = wx.BoxSizer(wx.VERTICAL)
        self.sizer_processing_shaders = wx.BoxSizer(wx.VERTICAL)
        self.sizer_processing_local = wx.BoxSizer(wx.VERTICAL)
        self._processing_tab = "Basic"
        self.cb_cloud_upscale = wx.CheckBox(processing_parent, -1, "Cloud AI upscale (uploads images)")
        self.cb_cloud_upscale.SetToolTip(
            "When enabled, small wallpaper images are uploaded to a third-party Hugging Face "
            "Real-ESRGAN Space. Uses free limited GPU time; no AI model is installed locally. "
            "Results are cached. Offline/quota failures use local resizing instead."
        )
        self.cb_cloud_upscale.Bind(wx.EVT_CHECKBOX, self._on_cloud_upscale_changed)
        self.sizer_processing_cloud.Add(self.cb_cloud_upscale, 0, wx.ALL, 5)

        cloud_grid = wx.FlexGridSizer(1, 3, 5, 5)
        cloud_grid.AddGrowableCol(1, 1)
        cloud_scale_label = wx.StaticText(processing_parent, -1, "AI model:")
        self.ch_cloud_scale = wx.Choice(processing_parent, choices=["Auto (when needed)", "2x", "4x", "8x"])
        self.ch_cloud_scale.SetSelection(0)
        self.ch_cloud_scale.SetToolTip(
            "Real-ESRGAN supports 2x, 4x and 8x models. Auto upscales only "
            "when required by display resolution and zoom (up to 4x). "
            "Selecting a fixed model can use cloud GPU time even when the image is large. "
            "Returning to a previously used model reuses its cached output."
        )
        self.ch_cloud_scale.Bind(wx.EVT_CHOICE, self._on_cloud_quality_changed)
        cloud_grid.Add(cloud_scale_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 5)
        cloud_grid.Add(self.ch_cloud_scale, 1, wx.EXPAND)
        cloud_grid.AddSpacer(1)

        cloud_sharpen_label = wx.StaticText(processing_parent, -1, "Sharpen (local):")
        self.sld_cloud_sharpen = wx.Slider(processing_parent, -1, 0, 0, 100, style=wx.SL_HORIZONTAL)
        self.st_cloud_sharpen = wx.StaticText(processing_parent, -1, "0", size=wx.Size(40, -1), style=wx.ALIGN_RIGHT)
        self.sld_cloud_sharpen.SetToolTip(
            "Sharpen the wallpaper locally with or without Cloud AI (0 = unchanged). "
            "The preview uses the original image; changing sharpness never spends cloud credits."
        )
        self.sld_cloud_sharpen.Bind(wx.EVT_SLIDER, self._on_cloud_quality_changed)
        self.sizer_processing_cloud.Add(cloud_grid, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)
        self._sync_cloud_quality_controls()

        shader_row = wx.FlexGridSizer(1, 3, 5, 5)
        shader_row.AddGrowableCol(1, 1)
        shader_row.Add(
            wx.StaticText(processing_parent, -1, "Anime4K preset / effect:"),
            0,
            wx.ALIGN_CENTER_VERTICAL | wx.LEFT,
            5,
        )
        self.ch_local_shader = wx.Choice(processing_parent)
        self._shader_names = [""]
        self.cb_shader_advanced = wx.CheckBox(processing_parent, label="Show individual shaders (advanced)")
        self.cb_shader_advanced.SetValue(False)
        self.cb_shader_advanced.SetToolTip(
            "The MPV pack contains standalone effects and internal pipeline stages. "
            "Mode A/B/C presets are recommended for normal wallpaper processing."
        )
        self.cb_shader_advanced.Bind(wx.EVT_CHECKBOX, self._on_shader_advanced_toggled)
        self._refresh_local_shader_options("")
        self.ch_local_shader.SetToolTip(
            "Use an ordered Mode A, B or C pipeline from the imported MPV Anime4K pack. "
            "AutoDownscalePre files are helper stages, not standalone effects. "
            "Requires FFmpeg/libplacebo and Vulkan. Effects appear after Apply."
        )
        self.ch_local_shader.Bind(wx.EVT_CHOICE, self._on_local_shader_changed)
        shader_row.Add(self.ch_local_shader, 1, wx.EXPAND)
        self.button_import_shaders = wx.Button(processing_parent, -1, "Import...")
        self.button_import_shaders.SetToolTip(
            "Import an Anime4K shader archive (.tar.gz or .zip) or an individual .glsl file. "
            "The installed shader files are small and run entirely on your GPU."
        )
        self.button_import_shaders.Bind(wx.EVT_BUTTON, self._on_import_shaders)
        shader_row.Add(self.button_import_shaders, 0)
        self.sizer_processing_shaders.Add(shader_row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)
        self.sizer_processing_shaders.Add(self.cb_shader_advanced, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 9)
        self.sizer_processing_shaders.Add(
            wx.StaticText(
                processing_parent,
                label="Mode A: restore + upscale\nMode B: soft restore + upscale\nMode C: denoise + upscale",
            ),
            0,
            wx.LEFT | wx.RIGHT | wx.BOTTOM,
            9,
        )

        self.sizer_processing_local.Add(
            wx.StaticText(processing_parent, label="Local image adjustments (no uploads)"),
            0,
            wx.LEFT | wx.TOP | wx.BOTTOM,
            8,
        )
        sharpen_grid = wx.FlexGridSizer(1, 3, 5, 5)
        sharpen_grid.AddGrowableCol(1, 1)
        sharpen_grid.Add(cloud_sharpen_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 5)
        sharpen_grid.Add(self.sld_cloud_sharpen, 1, wx.EXPAND)
        sharpen_grid.Add(self.st_cloud_sharpen, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.sizer_processing_local.Add(sharpen_grid, 0, wx.EXPAND | wx.ALL, 5)
        self.studio_tone_controls = {}
        self.studio_tone_labels = {}
        tone_grid = wx.FlexGridSizer(3, 3, 6, 6)
        tone_grid.AddGrowableCol(1, 1)
        for name in ("Brightness", "Contrast", "Saturation"):
            tone_grid.Add(wx.StaticText(processing_parent, label=f"{name}:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 5)
            slider = wx.Slider(processing_parent, value=0, minValue=-100, maxValue=100)
            slider.SetToolTip(f"{name} (-100 to +100). Rendered locally; Cloud AI credits are unaffected.")
            slider.Bind(wx.EVT_SLIDER, self._on_studio_tone_changed)
            self.studio_tone_controls[name.lower()] = slider
            tone_grid.Add(slider, 1, wx.EXPAND)
            value_label = wx.StaticText(processing_parent, label="0", size=wx.Size(40, -1), style=wx.ALIGN_RIGHT)
            self.studio_tone_labels[name.lower()] = value_label
            tone_grid.Add(value_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.sizer_processing_local.Add(tone_grid, 0, wx.EXPAND | wx.ALL, 5)
        for content in (self.sizer_processing_cloud, self.sizer_processing_shaders, self.sizer_processing_local):
            self.sizer_setting_processing.Add(content, 0, wx.EXPAND | wx.ALL, 4)
        # Select the simple view after every control exists. No profile
        # setting is changed merely by selecting a processing tab.
        self._set_processing_tab("Basic")

        # Small undo button to reset scaling & position to defaults without
        # touching the rest of the profile configuration.
        undo_bmp = wx.ArtProvider.GetBitmap(wx.ART_UNDO, wx.ART_BUTTON, wx.Size(16, 16))
        self.button_zoom_reset = wx.BitmapButton(
            statbox_parent_zoom, bitmap=wx.BitmapBundle(undo_bmp), name="butt_reset_zoom"
        )
        self.button_zoom_reset.SetToolTip(wx.ToolTip("Reset scaling & position to defaults"))
        self.button_zoom_reset.Bind(wx.EVT_BUTTON, self.onResetZoom)
        reset_row = wx.BoxSizer(wx.HORIZONTAL)
        reset_row.AddStretchSpacer()
        reset_row.Add(self.button_zoom_reset, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.sizer_setting_zoom.Add(reset_row, 0, wx.EXPAND | wx.BOTTOM, 3)

        # Add subsizers to the left column sizer
        self.sizer_settings_left.Add(self.sizer_setting_zoom, 0, wx.EXPAND | wx.TOP, 5)

    def create_sizer_settings_right(self):
        # paths sizer contents
        self.create_sizer_paths()

    def create_sizer_paths(self):
        self.sizer_setting_paths = wx.StaticBoxSizer(wx.VERTICAL, self, "Wallpaper paths")
        self.statbox_parent_paths = self.sizer_setting_paths.GetStaticBox()
        st_paths_info = wx.StaticText(
            self.statbox_parent_paths,
            -1,
            "Browse to add your wallpaper files or source folders here:",
        )
        if self.use_multi_image:
            self.path_listctrl = wx.ListCtrl(
                self.statbox_parent_paths,
                -1,
                style=wx.LC_REPORT | wx.BORDER_SIMPLE | wx.LC_SORT_ASCENDING,
            )
            self.path_listctrl.InsertColumn(0, "Display", wx.LIST_FORMAT_RIGHT, width=100)
            self.path_listctrl.InsertColumn(1, "Source", width=400)
        else:
            # show simpler listing without header if only one wallpaper target
            self.path_listctrl = wx.ListCtrl(
                self.statbox_parent_paths,
                -1,
                style=wx.LC_REPORT | wx.BORDER_SIMPLE | wx.LC_NO_HEADER,
            )
            self.path_listctrl.InsertColumn(0, "Source", width=500)
        self.path_listctrl.SetImageList(self.image_list, wx.IMAGE_LIST_SMALL)
        self.path_listctrl.SetMinSize(wx.Size(420, 120))
        self.path_listctrl.Bind(wx.EVT_LIST_ITEM_SELECTED, self.onWallpaperItemSelected)
        self.path_listctrl.Bind(wx.EVT_SIZE, self._on_sources_resize)
        self.path_listctrl.Bind(wx.EVT_MOUSEWHEEL, self._on_paths_wheel)

        self.sizer_setting_paths.Add(st_paths_info, 0, wx.ALIGN_LEFT | wx.ALL, 5)
        self.sizer_setting_paths.Add(self.path_listctrl, 1, wx.CENTER | wx.EXPAND | wx.TOP | wx.LEFT | wx.RIGHT, 5)
        # Buttons
        self.sizer_setting_paths_buttons = wx.WrapSizer(wx.HORIZONTAL)
        self.button_browse = wx.Button(self.statbox_parent_paths, label="Add images...")
        self.button_browse_folders = wx.Button(self.statbox_parent_paths, label="Add folder...")
        self.button_remove_source = wx.Button(self.statbox_parent_paths, label="Remove selected")
        self.button_browse.Bind(wx.EVT_BUTTON, self.onAddImagesSource)
        self.button_browse_folders.Bind(wx.EVT_BUTTON, self.onAddFolderSource)
        self.button_remove_source.Bind(wx.EVT_BUTTON, self.onRemoveSource)
        # Wheel input over the entire source group belongs to the outer window,
        # not just to the wx.ListCtrl that would otherwise consume it.
        for control in (
            self.statbox_parent_paths,
            st_paths_info,
            self.button_browse,
            self.button_browse_folders,
            self.button_remove_source,
        ):
            control.Bind(wx.EVT_MOUSEWHEEL, self._on_paths_wheel)
        self.sizer_setting_paths_buttons.Add(self.button_browse, 0, wx.CENTER | wx.ALL, 5)
        self.sizer_setting_paths_buttons.Add(self.button_browse_folders, 0, wx.CENTER | wx.ALL, 5)
        self.sizer_setting_paths_buttons.Add(self.button_remove_source, 0, wx.CENTER | wx.ALL, 5)
        # add button sizer to parent paths sizer
        self.sizer_setting_paths.Add(self.sizer_setting_paths_buttons, 0, wx.CENTER | wx.EXPAND | wx.ALL, 0)

        self.sizer_settings_right.Add(
            self.sizer_setting_paths, 1, wx.CENTER | wx.EXPAND | wx.TOP | wx.LEFT | wx.RIGHT, 5
        )
        self.path_listctrl.InvalidateBestSize()
        # self.sizer_setting_paths.SetItemMinSize(self.path_listctrl, (1000, -1))

    def create_sizer_settings_advanced(self):
        """Create sizer for advanced spanning settings.

        Display-wide settings (diagonal sizes, bezels) live in the separate
        collapsible system band; this box holds only profile-scoped advanced
        adjustments (manual offsets, span groups, perspective selection).
        """
        self.sizer_setting_adv = wx.StaticBoxSizer(wx.VERTICAL, self, "Advanced wallpaper adjustment")
        advanced_parent = self.sizer_setting_adv.GetStaticBox()
        help_bmp = wx.ArtProvider.GetBitmap(wx.ART_QUESTION, wx.ART_BUTTON, wx.Size(20, 20))

        # Offsets
        self.sizer_setting_offsets = wx.BoxSizer(wx.VERTICAL)
        statbox_parent_offsets = advanced_parent
        self.cb_offsets = wx.CheckBox(statbox_parent_offsets, -1, "Apply manual offsets")
        self.cb_offsets.Bind(wx.EVT_CHECKBOX, self.onCheckboxOffsets)
        st_offsets = wx.StaticText(statbox_parent_offsets, -1, "Manual offsets in pixels (x,y=px,px):")
        st_offsets.Disable()
        self.sizer_setting_offsets.Add(self.cb_offsets, 0, wx.ALIGN_LEFT | wx.BOTTOM, 5)
        self.sizer_setting_offsets.Add(st_offsets, 0, wx.ALIGN_LEFT | wx.LEFT, 10)
        tc_list_sizer_offs = wx.WrapSizer(wx.HORIZONTAL)
        self.tc_list_offsets = self.list_of_textctrl(statbox_parent_offsets, wpproc.NUM_DISPLAYS)
        for tc in self.tc_list_offsets:
            st = wx.StaticText(statbox_parent_offsets, -1, str(self.tc_list_offsets.index(tc)) + ":")
            tc_st_sizer = wx.BoxSizer(wx.HORIZONTAL)
            tc_st_sizer.Add(st, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT | wx.TOP | wx.BOTTOM, 5)
            tc_st_sizer.Add(tc, 0, wx.ALIGN_LEFT | wx.ALL, 5)
            tc_list_sizer_offs.Add(tc_st_sizer, 0, wx.ALIGN_LEFT | wx.ALL, 0)
            tc.SetValue("0,0")
            st.Disable()
            tc.Disable()
        self.sizer_setting_offsets.Add(tc_list_sizer_offs, 0, wx.ALIGN_LEFT | wx.LEFT, 5)

        # Span groups
        self.sizer_setting_spangroups = wx.BoxSizer(wx.VERTICAL)
        sizer_spangroups_cb = wx.BoxSizer(wx.HORIZONTAL)
        self.cb_spangroups = wx.CheckBox(advanced_parent, -1, "Use multiple span areas")
        self.cb_spangroups.Bind(wx.EVT_CHECKBOX, self.onCheckboxSpanGroups)
        self.button_help_spang = wx.BitmapButton(
            advanced_parent, bitmap=wx.BitmapBundle(help_bmp), name="butt_help_spang"
        )
        self.button_help_spang.Bind(wx.EVT_BUTTON, self.onHelpSpanGroups)
        sizer_spangroups_cb.Add(self.cb_spangroups, 0, wx.ALIGN_LEFT | wx.LEFT, 5)
        sizer_spangroups_cb.AddStretchSpacer()
        sizer_spangroups_cb.Add(self.button_help_spang, 0, wx.RIGHT, 5)
        sizer_spangroups_data = wx.WrapSizer(wx.HORIZONTAL)
        self.ch_list_spangroups = self.list_of_wxchoice(advanced_parent, wpproc.NUM_DISPLAYS, 0.4)
        for ch in self.ch_list_spangroups:
            st = wx.StaticText(advanced_parent, -1, str(self.ch_list_spangroups.index(ch)) + ":")
            ch_st_sizer = wx.BoxSizer(wx.HORIZONTAL)
            ch_st_sizer.Add(st, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT | wx.TOP | wx.BOTTOM, 5)
            ch_st_sizer.Add(ch, 0, wx.ALIGN_LEFT | wx.ALL, 5)
            sizer_spangroups_data.Add(ch_st_sizer, 0, wx.ALIGN_LEFT | wx.ALL, 0)
            ch.SetItems([str(idx) for idx in range(self.ch_list_spangroups.index(ch) + 1)])
            ch.SetSelection(0)
            st.Disable()
            ch.Disable()
        self.sizer_setting_spangroups.Add(sizer_spangroups_cb, 1, wx.EXPAND, 5)
        self.sizer_setting_spangroups.Add(sizer_spangroups_data, 0, wx.ALIGN_LEFT | wx.LEFT, 5)

        # Perspective profile
        self.sizer_setting_persp = wx.BoxSizer(wx.HORIZONTAL)
        st_perspprof = wx.StaticText(advanced_parent, -1, "Perspective profile:")
        persp_choices = ["default", *list(self.display_sys.perspective_dict.keys()), "disabled"]
        self.ch_persp = wx.ComboBox(
            advanced_parent,
            -1,
            name="PerspChoice",
            size=wx.Size(165, -1),
            choices=persp_choices,
            style=wx.CB_READONLY,
        )
        self.sizer_setting_persp.Add(st_perspprof, 0, wx.ALIGN_LEFT | wx.ALL | wx.ALIGN_CENTER_VERTICAL, 0)
        self.sizer_setting_persp.Add(self.ch_persp, 0, wx.ALIGN_LEFT | wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5)

        # Add setting subsizers to the adv settings sizer
        self.sizer_setting_adv.Add(self.sizer_setting_offsets, 0, wx.CENTER | wx.EXPAND | wx.ALL, 5)
        self.sizer_setting_adv.Add(self.sizer_setting_spangroups, 0, wx.CENTER | wx.EXPAND | wx.ALL, 5)
        self.sizer_setting_adv.Add(self.sizer_setting_persp, 0, wx.CENTER | wx.EXPAND | wx.ALL, 5)

    def create_sizer_system_band(self):
        """Build the collapsible system-settings band shown above the profile row.

        Holds display-wide settings (diagonal sizes, bezels) that are shared by
        every profile, plus a dedicated Save/Revert. Edits are applied live so
        the preview can be used to dial in values, but they are only written to
        disk when the band's Save is pressed; Revert restores the last saved
        state. Collapsed by default — set it once and forget it.
        """
        self.system_pane = wx.CollapsiblePane(
            self,
            label="Display system settings  (apply to all profiles)",
            style=wx.CP_DEFAULT_STYLE | wx.CP_NO_TLW_RESIZE,
        )
        self.system_pane.Bind(wx.EVT_COLLAPSIBLEPANE_CHANGED, self.onSystemPaneToggle)
        pane = self.system_pane.GetPane()

        band = wx.BoxSizer(wx.HORIZONTAL)

        # Diagonal sizes group
        self.sizer_setting_diaginch = wx.BoxSizer(wx.VERTICAL)
        self.build_diaginch_controls(pane)
        band.Add(self.sizer_setting_diaginch, 0, wx.ALL, 8)

        band.Add(wx.StaticLine(pane, style=wx.LI_VERTICAL), 0, wx.EXPAND | wx.TOP | wx.BOTTOM, 5)

        # Bezels group
        self.sizer_setting_bezels = wx.BoxSizer(wx.VERTICAL)
        self.build_bezel_controls(pane)
        band.Add(self.sizer_setting_bezels, 0, wx.ALL, 8)

        band.AddStretchSpacer()

        # System Save / Revert
        sys_btns = wx.BoxSizer(wx.VERTICAL)
        self.button_system_save = wx.Button(pane, label="Save")
        self.button_system_revert = wx.Button(pane, label="Revert")
        self.button_system_save.Bind(wx.EVT_BUTTON, self.onSaveSystem)
        self.button_system_revert.Bind(wx.EVT_BUTTON, self.onRevertSystem)
        self.button_system_save.SetToolTip(wx.ToolTip("Save these display settings for all profiles."))
        self.button_system_revert.SetToolTip(wx.ToolTip("Discard unsaved display setting changes."))
        self.button_system_save.Disable()
        self.button_system_revert.Disable()
        sys_btns.Add(self.button_system_save, 0, wx.ALL, 3)
        sys_btns.Add(self.button_system_revert, 0, wx.ALL, 3)
        band.Add(sys_btns, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 8)

        pane.SetSizer(band)
        self.system_pane.Collapse(True)

    def build_diaginch_controls(self, parent):
        """Build the manual display diagonal size controls into the system band."""
        self.cb_diaginch = wx.CheckBox(parent, -1, "Input display sizes manually")
        self.cb_diaginch.Bind(wx.EVT_CHECKBOX, self.onCheckboxDiaginch)
        st_diaginch = wx.StaticText(parent, -1, "Display diagonal sizes (inches):")
        self.sizer_setting_diaginch.Add(self.cb_diaginch, 0, wx.ALIGN_LEFT | wx.BOTTOM, 5)
        self.sizer_setting_diaginch.Add(st_diaginch, 0, wx.ALIGN_LEFT | wx.LEFT, 10)
        diags = [str(dsp.diagonal_size()[1]) for dsp in self.display_sys.disp_list]
        tc_list_sizer_diag = wx.WrapSizer(wx.HORIZONTAL)
        self.tc_list_diaginch = self.list_of_textctrl(parent, wpproc.NUM_DISPLAYS, fraction=2 / 5)
        use_user_diags = self.display_sys.use_user_diags
        for tc, diag in zip(self.tc_list_diaginch, diags):
            tc_list_sizer_diag.Add(tc, 0, wx.ALIGN_LEFT | wx.ALL, 5)
            tc.ChangeValue(diag)
            tc.Enable(use_user_diags)
            # Dedicated handler consumes the event so display-size edits don't
            # reach the profile field handler (these are system-wide settings).
            tc.Bind(wx.EVT_TEXT, self._on_system_field_changed)
        self.sizer_setting_diaginch.Add(tc_list_sizer_diag, 0, wx.ALIGN_LEFT | wx.LEFT, 5)
        self.cb_diaginch.SetValue(use_user_diags)

    def build_bezel_controls(self, parent):
        """Build the bezel configuration controls into the system band."""
        st_bezels = wx.StaticText(parent, -1, "Adjust bezel sizes:")
        self.sizer_bezel_buttons = wx.BoxSizer(wx.HORIZONTAL)
        self.button_bezels = wx.Button(parent, -1, label="Configure")
        self.button_bezels_save = wx.Button(parent, -1, label="Done")
        self.button_bezels_canc = wx.Button(parent, -1, label="Cancel")
        self.button_bezels.Bind(wx.EVT_BUTTON, self.onConfigureBezels)
        self.button_bezels_save.Bind(wx.EVT_BUTTON, self.onConfigureBezelsSave)
        self.button_bezels_canc.Bind(wx.EVT_BUTTON, self.onConfigureBezelsCanc)
        help_bmp = wx.ArtProvider.GetBitmap(wx.ART_QUESTION, wx.ART_BUTTON, wx.Size(20, 20))
        self.button_help_bezel = wx.BitmapButton(parent, bitmap=wx.BitmapBundle(help_bmp), name="butt_help_bez")
        self.button_help_bezel.Bind(wx.EVT_BUTTON, self.onHelpBezels)
        self.sizer_bezel_buttons.Add(self.button_bezels, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.sizer_bezel_buttons.Add(self.button_bezels_save, 0, wx.ALIGN_CENTER_VERTICAL | wx.TOP | wx.BOTTOM, 5)
        self.sizer_bezel_buttons.Add(self.button_bezels_canc, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.sizer_bezel_buttons.Add(self.button_help_bezel, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.sizer_setting_bezels.Add(st_bezels, 0, wx.ALL, 0)
        self.sizer_setting_bezels.Add(self.sizer_bezel_buttons, 1, wx.EXPAND, 0)
        self.button_bezels_save.Disable()
        self.button_bezels_canc.Disable()

    def onSystemPaneToggle(self, event):
        """Re-layout when the system band is expanded/collapsed."""
        self.sizer_main.Layout()
        self.FitInside()

    def create_sizer_bottom_buttonrow(self):
        self.button_help = wx.Button(self, label="Help")
        self.button_align_test = wx.Button(self, label="Align Test")
        self.button_perspectives = wx.Button(self, label="Perspectives")
        self.button_apply = wx.Button(self, label="Apply")
        self.button_save_apply = StudioActionButton(self, "Save & Apply", primary=True)
        self.button_apply.SetToolTip("Apply temporarily. Save the profile to retain its framing settings.")
        self.button_save_apply.SetToolTip("Save zoom, position, upscaling and image choice, then apply them.")
        self.button_close = StudioActionButton(self, "Close")

        self.button_apply.Bind(wx.EVT_BUTTON, self.onApply)
        self.button_save_apply.Bind(wx.EVT_BUTTON, self.onSaveAndApply)
        self.button_align_test.Bind(wx.EVT_BUTTON, self.onAlignTest)
        self.button_perspectives.Bind(wx.EVT_BUTTON, self.onPerspectives)
        self.button_help.Bind(wx.EVT_BUTTON, self.onHelp)
        self.button_close.Bind(wx.EVT_BUTTON, self.onClose)

        self.sizer_bottom_buttonrow.Add(self.button_help, 0, wx.ALIGN_LEFT | wx.ALL, 5)
        self.sizer_bottom_buttonrow.Add(self.button_align_test, 0, wx.ALIGN_LEFT | wx.ALL, 5)
        self.sizer_bottom_buttonrow.Hide(self.button_align_test)
        self.sizer_bottom_buttonrow.Add(self.button_perspectives, 0, wx.ALIGN_LEFT | wx.ALL, 5)
        self.sizer_bottom_buttonrow.Hide(self.button_perspectives)
        self.sizer_bottom_buttonrow.Layout()
        self.sizer_bottom_buttonrow.AddStretchSpacer()
        # Apply is already the primary blue action in the header. The footer
        # keeps only the explicit save-then-apply action, not a duplicate.
        self.sizer_bottom_buttonrow.Add(self.button_save_apply, 0, wx.ALL, 5)
        self.sizer_bottom_buttonrow.Add(self.button_close, 0, wx.ALL, 5)

    def create_studio_navigation(self):
        """Persistent navigation, status, and image preview controls."""
        self.studio_sidebar = wx.Panel(self, size=wx.Size(162, -1))
        self.studio_sidebar.SetBackgroundColour(wx.Colour(25, 34, 48))
        column = wx.BoxSizer(wx.VERTICAL)
        heading = wx.StaticText(self.studio_sidebar, label="SUPERPAPER\nNEXT")
        heading.SetForegroundColour(wx.Colour(240, 244, 252))
        font = heading.GetFont()
        font.SetPointSize(font.GetPointSize() + 4)
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        heading.SetFont(font)
        column.Add(heading, 0, wx.LEFT | wx.RIGHT | wx.TOP, 16)
        caption = wx.StaticText(self.studio_sidebar, label="WALLPAPER STUDIO")
        caption.SetForegroundColour(wx.Colour(121, 161, 212))
        column.Add(caption, 0, wx.LEFT | wx.TOP | wx.BOTTOM, 16)
        self.studio_navigation = {}
        for name, icon in (
            ("Wallpapers", "▣"),
            ("Displays", "▤"),
            ("Profiles", "◈"),
            ("Processing", "✦"),
            ("Advanced", "⚙"),
        ):
            button = StudioNavigationButton(self.studio_sidebar, name, icon)
            button.Bind(wx.EVT_BUTTON, lambda event, workspace=name: self._set_studio_workspace(workspace))
            column.Add(button, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 5)
            self.studio_navigation[name] = button
        # The old sidebar footer was below the visible frame on shorter
        # desktops. Keep the navigation compact and surface help in status.
        column.AddStretchSpacer()
        self.studio_sidebar.SetSizer(column)

        self.studio_header = wx.BoxSizer(wx.HORIZONTAL)
        self.studio_header_title = wx.StaticText(self, label="Wallpapers")
        title = self.studio_header_title
        font = title.GetFont()
        font.SetPointSize(font.GetPointSize() + 7)
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        title.SetFont(font)
        title_group = wx.BoxSizer(wx.VERTICAL)
        title_group.Add(title, 0, wx.BOTTOM, 3)
        self.studio_subtitle = wx.StaticText(
            self, label="Choose an image, align your monitors, and preview the result."
        )
        self.studio_subtitle.SetForegroundColour(wx.Colour(171, 193, 221))
        title_group.Add(self.studio_subtitle, 0)
        self.studio_header.Add(title_group, 1, wx.ALIGN_CENTER_VERTICAL)
        self.studio_save = StudioActionButton(self, "Save Profile")
        self.studio_save.Bind(wx.EVT_BUTTON, self.onSave)
        self.studio_header.Add(self.studio_save, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 8)
        self.studio_apply = StudioActionButton(self, "Apply", primary=True)
        self.studio_apply.Bind(wx.EVT_BUTTON, self.onApply)
        self.studio_apply.SetBackgroundColour(wx.Colour(35, 110, 207))
        self.studio_apply.SetForegroundColour(wx.Colour(255, 255, 255))
        self.studio_header.Add(self.studio_apply, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 8)
        self.studio_apply.SetToolTip("Test wallpaper with unsaved settings. Use Save Profile to persist.")

        # Keep comparison and monitor-view controls on separate rows. A single
        # horizontal sizer allowed the comparison status label to expand into
        # the inspector on GTK whenever a form control triggered layout.
        self.studio_preview_tools = wx.BoxSizer(wx.VERTICAL)
        self.studio_compare_row = wx.BoxSizer(wx.HORIZONTAL)
        self.studio_view_row = wx.BoxSizer(wx.HORIZONTAL)
        self.studio_compare = wx.CheckBox(self, label="Split original / locally adjusted")
        self.studio_compare.SetToolTip(
            "Compare the original image (left) with local adjustments (right). "
            "Cloud AI and Vulkan shader effects appear only on the applied wallpaper."
        )
        self.studio_compare.Bind(wx.EVT_CHECKBOX, self._studio_toggle_compare)
        self.studio_compare_row.Add(self.studio_compare, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.studio_split_slider = wx.Slider(self, value=50, minValue=0, maxValue=100, size=wx.Size(130, -1))
        self.studio_split_slider.SetToolTip(
            "Move the dividing line, or drag the blue handle directly on the wallpaper preview."
        )
        self.studio_split_slider.Enable(False)
        self.studio_split_slider.Bind(wx.EVT_SLIDER, self._studio_compare_position)
        self.studio_compare_row.Add(self.studio_split_slider, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 8)
        self.studio_compare_state = wx.StaticText(self, label="No local edits")
        self.studio_compare_state.SetForegroundColour(wx.Colour(166, 197, 230))
        self.studio_compare_state.SetToolTip(
            "Only local sharpening, brightness, contrast and saturation are previewed. "
            "With all of them at zero, the two sides show the same image."
        )
        self.studio_compare_state.Hide()
        self.studio_compare_row.Add(self.studio_compare_state, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 9)
        self.studio_view_row.AddStretchSpacer()
        self.studio_view_row.Add(wx.StaticText(self, label="Preview view:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        self.studio_desktop_layout = wx.CheckBox(self, label="Desktop layout")
        self.studio_desktop_layout.SetValue(True)
        self.studio_desktop_layout.SetToolTip(
            "Show the actual virtual-desktop monitor positions and gaps, as in a full Spectacle screenshot. "
            "Turn off to inspect the physical PPI/bezel arrangement."
        )
        self.studio_desktop_layout.Bind(wx.EVT_CHECKBOX, self._studio_toggle_desktop_layout)
        self.studio_view_row.Add(self.studio_desktop_layout, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 10)
        self.studio_monitor_choice = wx.Choice(self, choices=["All monitors"])
        self.studio_monitor_choice.SetSelection(0)
        self.studio_monitor_choice.Bind(wx.EVT_CHOICE, self._studio_choose_monitor)
        self.studio_view_row.Add(self.studio_monitor_choice, 0, wx.ALIGN_CENTER_VERTICAL)
        self.studio_reset_view = StudioActionButton(self, "Reset View")
        self.studio_reset_view.Bind(wx.EVT_BUTTON, self.onResetZoom)
        self.studio_view_row.Add(self.studio_reset_view, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 8)
        self.studio_preview_tools.Add(self.studio_compare_row, 0, wx.EXPAND | wx.BOTTOM, 3)
        self.studio_preview_tools.Add(self.studio_view_row, 0, wx.EXPAND)

        self.studio_alignment_tools = wx.BoxSizer(wx.HORIZONTAL)
        self.studio_alignment_tools.Add(
            wx.StaticText(self, label="Alignment"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6
        )
        for caption, position in (("Left", -100), ("Center", 0), ("Right", 100)):
            button = StudioActionButton(self, caption)
            button.SetToolTip(f"Align the image crop to the {caption.lower()}.")
            button.Bind(wx.EVT_BUTTON, lambda event, amount=position: self._studio_align_image(amount))
            self.studio_alignment_tools.Add(button, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)
        self.studio_alignment_tools.AddStretchSpacer()
        self.studio_alignment_tools.Add(wx.StaticText(self, label="Zoom"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.studio_quick_zoom = wx.Slider(self, value=100, minValue=100, maxValue=400, size=wx.Size(145, -1))
        self.studio_quick_zoom.SetToolTip("Live zoom; synced with the Image & Placement inspector.")
        self.studio_quick_zoom.Bind(wx.EVT_SLIDER, self._studio_quick_zoom_changed)
        self.studio_alignment_tools.Add(self.studio_quick_zoom, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.studio_zoom_label = wx.StaticText(self, label="100%", size=wx.Size(50, -1))
        self.studio_alignment_tools.Add(self.studio_zoom_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)

        self.studio_display_editor = wx.BoxSizer(wx.VERTICAL)
        self.studio_display_editor.Add(
            wx.StaticText(self, label="DISPLAY LAYOUT - choose a monitor to focus its preview"),
            0,
            wx.BOTTOM,
            9,
        )
        tiles = wx.BoxSizer(wx.HORIZONTAL)
        for index, display in enumerate(self.display_sys.disp_list):
            width, height = display.resolution
            tile = wx.Button(self, label=f"Monitor {index + 1}\n{width} x {height}")
            tile.SetMinSize(wx.Size(148, 77))
            tile.SetBackgroundColour(wx.Colour(27, 50, 77))
            tile.SetForegroundColour(wx.Colour(238, 245, 255))
            tile.Bind(wx.EVT_BUTTON, lambda event, number=index + 1: self._studio_focus_display(number))
            tiles.Add(tile, 1, wx.EXPAND | wx.RIGHT, 7)
        self.studio_display_editor.Add(tiles, 0, wx.EXPAND | wx.BOTTOM, 10)
        arrange = wx.Button(self, label="Arrange monitors in preview...")
        arrange.SetToolTip("Drag monitors to their real positions; use the preview's Save control to stage offsets.")
        arrange.Bind(wx.EVT_BUTTON, self._studio_arrange_displays)
        self.studio_display_editor.Add(arrange, 0, wx.ALIGN_LEFT)
        self.studio_display_editor.Add(
            wx.StaticText(
                self,
                label="Drag the monitors in the preview, then Save there. "
                "Use the Displays system settings to persist calibration.",
            ),
            0,
            wx.TOP,
            9,
        )

        self.studio_source_tools = wx.BoxSizer(wx.HORIZONTAL)
        self.studio_sources_toggle = StudioActionButton(self, "Show image sources")
        self.studio_sources_toggle.Bind(wx.EVT_BUTTON, self._studio_toggle_sources)
        self.studio_source_tools.Add(self.studio_sources_toggle, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        self.studio_source_tools.Add(
            wx.StaticText(self, label="Click and drag the monitor preview to reposition an image."),
            0,
            wx.ALIGN_CENTER_VERTICAL,
        )
        self._sources_expanded = False

        self.studio_image_card = wx.BoxSizer(wx.VERTICAL)
        self.studio_image_card.Add(wx.StaticText(self, label="IMAGE & PLACEMENT"), 0, wx.EXPAND | wx.BOTTOM, 8)
        empty_image = Image.new("RGB", (266, 94), (25, 33, 44))
        self.studio_image_thumbnail = wx.StaticBitmap(self, bitmap=wx.Bitmap.FromBuffer(266, 94, empty_image.tobytes()))
        self.studio_image_card.Add(self.studio_image_thumbnail, 0, wx.EXPAND | wx.BOTTOM, 6)
        self.studio_image_name = wx.StaticText(self, label="No image selected")
        self.studio_image_card.Add(self.studio_image_name, 0, wx.EXPAND | wx.BOTTOM, 7)
        self.studio_change_image = StudioActionButton(self, "Change Image...")
        self.studio_change_image.Bind(wx.EVT_BUTTON, self.onChangeImage)
        self.studio_image_card.Add(self.studio_change_image, 0, wx.EXPAND)

        self.studio_fit_row = wx.BoxSizer(wx.VERTICAL)
        self.studio_fit_row.Add(wx.StaticText(self, label="Fit method"), 0, wx.BOTTOM, 5)
        self.studio_fit_choice = wx.Choice(
            self, choices=["Span (keep aspect)", "Advanced span / bezels", "Separate per monitor"]
        )
        self.studio_fit_choice.SetSelection(0)
        self.studio_fit_choice.Bind(wx.EVT_CHOICE, self._studio_fit_changed)
        self.studio_fit_row.Add(self.studio_fit_choice, 0, wx.EXPAND)

        self.studio_profile_inspector = wx.BoxSizer(wx.VERTICAL)
        self.studio_profile_inspector.Add(
            wx.StaticText(
                self,
                label="Choose a thumbnail to load a profile.\n"
                "Changes to the selected profile are editable in the other workspaces.",
            ),
            0,
            wx.EXPAND | wx.ALL,
            8,
        )
        self.studio_profile_inspector.Add(self.button_save, 0, wx.EXPAND | wx.ALL, 5)
        self.studio_profile_inspector.Add(self.button_revert, 0, wx.EXPAND | wx.ALL, 5)
        self.studio_profile_inspector.Add(self.button_delete, 0, wx.EXPAND | wx.ALL, 5)

        self.studio_workspace_title = wx.StaticText(self, label="WALLPAPERS")
        font = self.studio_workspace_title.GetFont()
        font.SetPointSize(font.GetPointSize() + 2)
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        self.studio_workspace_title.SetFont(font)
        self.studio_status = wx.StaticText(
            self, label="Ready. Drag the wallpaper image in the preview to reposition it."
        )
        self._refresh_studio_monitor_options()

    def _set_studio_workspace(self, name):
        """Switch workspaces without discarding unsaved form values."""
        if name not in self._workspace_sizers:
            return
        self._workspace = name
        for key, section in self._workspace_sizers.items():
            # wx hides a nested sizer's layout item but may leave its controls
            # visible unless recursive=True. This was the advanced settings
            # leaking into the Wallpaper inspector.
            self.studio_inspector.Show(section, show=key == name, recursive=True)
        self.studio_inspector.Show(self.studio_image_card, show=name == "Wallpapers", recursive=True)
        self.studio_inspector.Show(self.studio_fit_row, show=name == "Wallpapers", recursive=True)
        self.sizer_displays.Show(
            self.sizer_setting_adv, show=name == "Displays" and self.show_advanced_settings, recursive=True
        )
        # Reclaim the removed comparison pane's vertical space for the real
        # multi-monitor preview in Processing, while keeping other tabs compact.
        self.wpprev_pnl.SetMinSize(wx.Size(450, 410 if name == "Processing" else 265))
        self.wpprev_pnl.SetMaxSize(wx.Size(10000, 480 if name == "Processing" else 295))
        self.studio_canvas_column.Show(self.sizer_top_half, show=name != "Profiles")
        self.studio_canvas_column.Show(self.studio_preview_tools, show=name != "Profiles", recursive=True)
        # Comparison starts enabled on the first Processing visit. Explicit
        # later user changes remain respected when switching back and forth.
        if name == "Processing" and not self._processing_compare_initialized:
            self._processing_compare_initialized = True
            self.studio_compare.SetValue(True)
            self.studio_split_slider.Enable(True)
        self._studio_sync_compare_workspace(name)
        self.studio_desktop_layout.Enable(name != "Displays")
        self.wpprev_pnl.set_desktop_layout(
            name not in ("Displays", "Profiles") and self.studio_desktop_layout.GetValue()
        )
        self.studio_canvas_column.Show(self.studio_source_tools, show=name == "Wallpapers")
        self.studio_canvas_column.Show(self.studio_quick_profiles, show=name == "Wallpapers", recursive=True)
        self.studio_canvas_column.Show(self.studio_alignment_tools, show=name == "Wallpapers", recursive=True)
        self.sizer_bottom_half.Show(self.sizer_profiles, show=name == "Profiles", recursive=True)
        self.studio_canvas_column.Show(self.studio_display_editor, show=name == "Displays", recursive=True)
        self.studio_canvas_column.Show(
            self.sizer_settings_right, show=name == "Wallpapers" and self._sources_expanded, recursive=True
        )
        self.studio_canvas_column.Show(self.sizer_gallery, show=name == "Profiles", recursive=True)
        self.studio_workspace_title.SetLabel(name.upper())
        # Avoid duplicating WALLPAPERS above IMAGE & PLACEMENT.
        self.studio_inspector.Show(self.studio_workspace_title, show=name != "Wallpapers")
        self.studio_header_title.SetLabel(name)
        descriptions = {
            "Wallpapers": "Select an image and adjust how it fits across your monitors.",
            "Displays": "Arrange screens, correct bezels, and calibrate your setup.",
            "Profiles": "Choose a saved wallpaper layout or create a new one.",
            "Processing": "Enhance images with cloud AI or local adjustments.",
            "Advanced": "Manage slideshows, hotkeys, and advanced options.",
        }
        self.studio_subtitle.SetLabel(descriptions[name])
        for key, button in self.studio_navigation.items():
            button.SetSelected(key == name)
        # Showing the Processing inspector recursively may reveal controls
        # from inactive sub-tabs. Restore the selected tab's child visibility.
        if name == "Processing":
            self._set_processing_tab(self._processing_tab)
        if name in ("Wallpapers", "Profiles"):
            self._refresh_profile_gallery()
        self.studio_canvas_column.Layout()
        self.studio_inspector.Layout()
        self.sizer_bottom_half.Layout()
        self.sizer_main.Layout()
        self.FitInside()
        self.Scroll(0, 0)
        self.resized = True

    def _studio_toggle_sources(self, event):
        """Expose full existing source manager only when the user needs it."""
        self._sources_expanded = not self._sources_expanded
        self.studio_sources_toggle.SetLabel("Hide image sources" if self._sources_expanded else "Show image sources")
        self.studio_canvas_column.Show(
            self.sizer_settings_right, show=self._sources_expanded and self._workspace == "Wallpapers"
        )
        self.studio_canvas_column.Layout()
        self.sizer_main.Layout()
        self.FitInside()

    def _studio_fit_changed(self, event):
        """Keep the compact fit selector and original span engine in sync."""
        value = self.studio_fit_choice.GetSelection()
        if value != wx.NOT_FOUND:
            self.radiobox_spanmode.SetSelection(value)
            self.onSpanRadio(None)
            self.studio_fit_choice.SetSelection(self.radiobox_spanmode.GetSelection())

    def _studio_refresh_image_card(self):
        """Use original image for inspector thumbnail, never cloud AI output."""
        if not hasattr(self, "studio_image_thumbnail"):
            return
        images = self.wpprev_pnl.current_preview_images
        path = images[0] if images else ""
        canvas = Image.new("RGB", (266, 94), (25, 33, 44))
        if path and os.path.isfile(path):
            try:
                with Image.open(path) as image:
                    source = ImageOps.exif_transpose(image)
                    source.thumbnail((266, 94), Image.Resampling.LANCZOS)
                    if source.mode == "RGBA":
                        canvas.paste(source, ((266 - source.width) // 2, (94 - source.height) // 2), source)
                    else:
                        canvas.paste(source.convert("RGB"), ((266 - source.width) // 2, (94 - source.height) // 2))
                self.studio_image_name.SetLabel(os.path.basename(path))
                self.studio_image_name.SetToolTip(path)
            except OSError, ValueError:
                self.studio_image_name.SetLabel("Image unavailable")
        else:
            self.studio_image_name.SetLabel("No image selected")
        self.studio_image_thumbnail.SetBitmap(wx.Bitmap.FromBuffer(266, 94, canvas.tobytes()))

    def _studio_focus_display(self, number):
        self.studio_monitor_choice.SetSelection(number)
        self._studio_choose_monitor(None)

    def _studio_arrange_displays(self, event):
        """Use the existing real monitor drag engine, not mockup-only tiles."""
        if not self.show_advanced_settings:
            self.studio_fit_choice.SetSelection(1)
            self._studio_fit_changed(None)
        if self.show_advanced_settings and not self.wpprev_pnl.config_mode:
            self.studio_monitor_choice.SetSelection(0)
            self._studio_choose_monitor(None)
            self.wpprev_pnl.onConfigure(None)
            self.studio_status.SetLabel("Drag monitor tiles in the preview; choose Save on the preview when done.")

    def _studio_align_image(self, value):
        """Apply alignment through the existing position and dirty-state logic."""
        if not self.sld_offx.IsEnabled():
            return
        self.sld_offx.SetValue(value)
        self.onZoomOffsetChange(None)

    def _studio_quick_zoom_changed(self, event):
        if not self.sld_zoom.IsEnabled():
            return
        self.sld_zoom.SetValue(self.studio_quick_zoom.GetValue())
        self.onZoomOffsetChange(None)

    def _studio_sync_compare_workspace(self, name):
        """Hide local before/after tools during monitor calibration.

        Keep the checkbox and split fraction intact for Wallpapers/Processing.
        Repaint locally when the effective comparison mode changes, so the
        Displays preview never retains an invisible split or caption overlay.
        """
        visible = name not in ("Displays", "Profiles")
        self.studio_preview_tools.Show(self.studio_compare_row, show=visible, recursive=True)
        self.studio_compare_row.Show(self.studio_compare_state, show=visible and self.studio_compare.GetValue())
        effective_compare = visible and self.studio_compare.GetValue()
        if self.wpprev_pnl.compare_original != effective_compare:
            self.wpprev_pnl.compare_original = effective_compare
            self.wpprev_pnl.update_zoom_offset(self.wpprev_pnl.zoom, self.wpprev_pnl.offset)

    def _studio_toggle_compare(self, event):
        self.wpprev_pnl.compare_original = self.studio_compare.GetValue()
        self.studio_split_slider.Enable(self.studio_compare.GetValue())
        self.wpprev_pnl.compare_fraction = self.studio_split_slider.GetValue() / 100.0
        self._studio_update_compare_feedback()
        self.wpprev_pnl.update_zoom_offset(self.wpprev_pnl.zoom, self.wpprev_pnl.offset)

    def _studio_update_compare_feedback(self):
        """Show a compact comparison status without expanding the view toolbar."""
        enabled = self.studio_compare.GetValue() and self._workspace not in ("Displays", "Profiles")
        if enabled:
            has_edits = bool(self.wpprev_pnl.sharpen or any(self.wpprev_pnl.tone))
            self.studio_compare_state.SetLabel("Local edits active" if has_edits else "No local edits")
        self.studio_compare_row.Show(self.studio_compare_state, show=enabled)
        self.studio_preview_tools.Layout()
        if hasattr(self, "studio_canvas_column"):
            self.studio_canvas_column.Layout()

    def _studio_compare_position(self, event):
        self.wpprev_pnl.compare_fraction = self.studio_split_slider.GetValue() / 100.0
        if self.wpprev_pnl.compare_original:
            self.wpprev_pnl.update_zoom_offset(self.wpprev_pnl.zoom, self.wpprev_pnl.offset)

    def _studio_drag_split_handle(self, fraction):
        """Synchronize the image drag handle and toolbar slider locally."""
        position = max(0, min(100, round(fraction * 100)))
        if position != self.studio_split_slider.GetValue():
            self.studio_split_slider.SetValue(position)
            self._studio_compare_position(None)

    def _refresh_studio_monitor_options(self):
        count = len(self.display_sys.disp_list)
        selected = self.studio_monitor_choice.GetSelection()
        self.studio_monitor_choice.SetItems(["All monitors", *[f"Monitor {i + 1}" for i in range(count)]])
        self.studio_monitor_choice.SetSelection(min(max(selected, 0), count))
        self.wpprev_pnl.focus_monitor = self.studio_monitor_choice.GetSelection()

    def _studio_choose_monitor(self, event):
        self.wpprev_pnl.focus_monitor = self.studio_monitor_choice.GetSelection()
        self.wpprev_pnl.update_zoom_offset(self.wpprev_pnl.zoom, self.wpprev_pnl.offset)

    def _studio_toggle_desktop_layout(self, event):
        """Switch between applied desktop geometry and physical calibration."""
        if self._workspace == "Displays":
            return
        self.wpprev_pnl.set_desktop_layout(self.studio_desktop_layout.GetValue())

    def create_studio_gallery(self):
        """Use real clickable thumbnail cards, not the platform's icon-list view."""
        gallery_header = wx.BoxSizer(wx.HORIZONTAL)
        title = wx.StaticText(self, label="SAVED PROFILES")
        title.SetForegroundColour(wx.Colour(225, 237, 250))
        gallery_header.Add(title, 1, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.studio_gallery_search = wx.SearchCtrl(self, style=wx.TE_PROCESS_ENTER)
        self.studio_gallery_search.SetDescriptiveText("Search profiles...")
        self.studio_gallery_search.Bind(wx.EVT_TEXT, self._refresh_profile_gallery)
        gallery_header.Add(self.studio_gallery_search, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.sizer_gallery.Add(gallery_header, 0, wx.EXPAND | wx.ALL, 5)

        self.studio_gallery_scroller = wx.ScrolledWindow(self, style=wx.VSCROLL | wx.BORDER_NONE)
        self.studio_gallery_scroller.SetScrollRate(0, 16)
        self.studio_gallery_scroller.SetMinSize(wx.Size(550, 365))
        self.studio_gallery_scroller.SetBackgroundColour(wx.Colour(19, 31, 47))
        self.studio_gallery_cards = wx.WrapSizer(wx.HORIZONTAL, flags=0)
        self.studio_gallery_scroller.SetSizer(self.studio_gallery_cards)
        self.sizer_gallery.Add(self.studio_gallery_scroller, 1, wx.EXPAND | wx.ALL, 5)

        actions = wx.BoxSizer(wx.HORIZONTAL)
        for label, handler in (
            ("New profile", self._studio_new_profile),
            ("Duplicate", self._studio_duplicate_profile),
            ("Delete", self.onDeleteProfile),
        ):
            button = wx.Button(self, label=label)
            button.Bind(wx.EVT_BUTTON, handler)
            actions.Add(button, 0, wx.RIGHT, 8)
        self.sizer_gallery.Add(actions, 0, wx.ALL, 5)

        self.studio_quick_profiles = wx.BoxSizer(wx.VERTICAL)
        quick_header = wx.BoxSizer(wx.HORIZONTAL)
        quick_title = wx.StaticText(self, label="YOUR PROFILES")
        quick_title.SetForegroundColour(wx.Colour(226, 238, 250))
        quick_header.Add(quick_title, 1, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        all_profiles = StudioActionButton(self, "View all profiles")
        all_profiles.Bind(wx.EVT_BUTTON, lambda event: self._set_studio_workspace("Profiles"))
        quick_header.Add(all_profiles, 0, wx.RIGHT, 5)
        self.studio_quick_profiles.Add(quick_header, 0, wx.EXPAND)
        self.studio_quick_profile_row = wx.WrapSizer(wx.HORIZONTAL, flags=0)
        self.studio_quick_profiles.Add(self.studio_quick_profile_row, 0, wx.EXPAND | wx.TOP, 6)
        self._building_gallery = False

    def _studio_profile_thumbnail(self, profile, width, height):
        """Create a centered photo preview without changing the source file."""
        canvas = Image.new("RGB", (width, height), (25, 34, 48))
        images = profile.next_wallpaper_files(peek=True)
        if images and os.path.isfile(images[0]):
            try:
                with Image.open(images[0]) as source:
                    oriented = ImageOps.exif_transpose(source)
                    oriented.thumbnail((width, height), Image.Resampling.LANCZOS)
                    canvas.paste(
                        oriented.convert("RGB"),
                        ((width - oriented.width) // 2, (height - oriented.height) // 2),
                    )
            except OSError, ValueError:
                pass
        return wx.Bitmap.FromBuffer(width, height, canvas.tobytes())

    def _studio_make_profile_card(self, parent, profile, width, height):
        selected = profile.name == self.tc_name.GetValue()
        background = wx.Colour(31, 83, 139) if selected else wx.Colour(32, 46, 65)
        card = wx.Panel(parent, style=wx.BORDER_NONE)
        card.SetBackgroundColour(background)
        card.SetMinSize(wx.Size(width + 18, height + 85))
        card_sizer = wx.BoxSizer(wx.VERTICAL)
        picture = wx.StaticBitmap(card, bitmap=self._studio_profile_thumbnail(profile, width, height))
        card_sizer.Add(picture, 0, wx.ALL, 9)
        name = wx.StaticText(card, label=profile.name)
        name.SetForegroundColour(wx.Colour(248, 250, 254))
        title_font = name.GetFont()
        title_font.SetWeight(wx.FONTWEIGHT_BOLD)
        name.SetFont(title_font)
        card_sizer.Add(name, 0, wx.LEFT | wx.RIGHT, 9)
        mode = "Separate" if profile.spanmode == "multi" else "Span"
        detail = wx.StaticText(card, label=f"{len(self.display_sys.disp_list)} monitors · {mode}")
        detail.SetForegroundColour(wx.Colour(176, 196, 220))
        card_sizer.Add(detail, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.TOP, 9)
        card.SetSizer(card_sizer)
        for control in (card, picture, name, detail):
            control.SetCursor(wx.Cursor(wx.CURSOR_HAND))
            control.Bind(
                wx.EVT_LEFT_UP,
                lambda event, profile_name=profile.name: self._studio_select_profile(profile_name),
            )
        return card

    def _refresh_profile_gallery(self, event=None):
        if not hasattr(self, "studio_gallery_cards") or self._building_gallery:
            return
        self._building_gallery = True
        try:
            self.studio_gallery_cards.Clear(delete_windows=True)
            self.studio_quick_profile_row.Clear(delete_windows=True)
            filter_text = self.studio_gallery_search.GetValue().strip().lower()
            for index, profile in enumerate(self.list_of_profiles):
                # wx creates newly added child panels visible even if their
                # containing sizer is currently hidden. Only create cards for
                # the active workspace, preventing the duplicate quick cards
                # from leaking into the full Profiles gallery.
                if self._workspace == "Profiles" and (not filter_text or filter_text in profile.name.lower()):
                    card = self._studio_make_profile_card(self.studio_gallery_scroller, profile, 234, 134)
                    self.studio_gallery_cards.Add(card, 0, wx.ALL, 7)
                if self._workspace == "Wallpapers" and index < 4:
                    compact = self._studio_make_profile_card(self, profile, 177, 89)
                    self.studio_quick_profile_row.Add(compact, 0, wx.RIGHT, 10)
            self.studio_gallery_scroller.Layout()
            self.studio_gallery_scroller.FitInside()
            if hasattr(self, "studio_canvas_column"):
                self.studio_canvas_column.Layout()
        finally:
            self._building_gallery = False

    def _studio_select_profile(self, name):
        """Use the same profile selection logic as the normal dropdown."""
        if self._building_gallery:
            return
        profile = self.parent_tray_obj.get_profile_by_name(name)
        if profile is None:
            return
        choice = self.choice_profiles.FindString(name)
        if choice != wx.NOT_FOUND:
            self.choice_profiles.SetSelection(choice)
        self.populate_fields(profile)
        # Defer rebuilding cards until the click handler returns; deleting the
        # clicked wx window from its own event handler is unsafe on GTK.
        wx.CallAfter(self._refresh_profile_gallery)
        self.studio_status.SetLabel(f"Selected profile: {name}")

    def _on_gallery_selected(self, event):
        """Compatibility callback for older profile-selection entry points."""
        if hasattr(event, "GetString"):
            self._studio_select_profile(event.GetString())

    def _studio_new_profile(self, event):
        self.onCreateNewProfile(None)
        self._set_studio_workspace("Wallpapers")
        self.studio_status.SetLabel("New profile: enter a name, select images, then Save & Apply.")

    def _studio_duplicate_profile(self, event):
        """Create a new managed profile with this profile's unsaved edits."""
        if self.loaded_profile is None:
            self.studio_status.SetLabel("Select an existing profile to duplicate.")
            return
        name = self.tc_name.GetValue()
        names = {p.name for p in self.list_of_profiles}
        duplicate = ""
        for count in range(1, 1000):
            suffix = f"-{count}"
            candidate = name[: 14 - len(suffix)] + suffix
            if candidate not in names:
                duplicate = candidate
                break
        if not duplicate:
            self.studio_status.SetLabel("No unused profile name is available.")
            return
        self.current_profile_id = None
        self.expected_source_identity = None
        self.tc_name.ChangeValue(duplicate)
        if self.onSave(None) is not None:
            self.studio_status.SetLabel(f"Duplicated profile: {duplicate}")

    #
    # Profile loading and display methods
    #
    def populate_fields(self, profile):
        """Populates config dialog fields with data from a profile."""
        self._loading = True
        self.current_profile_id = profile.profile_id
        self.expected_source_identity = profile.source_identity
        self.loaded_profile = profile
        self.tc_name.ChangeValue(profile.name)

        self.show_advanced_settings = False
        self.use_multi_image = False
        legacy_advanced = bool(
            profile.spanmode == "single" and bool(profile.ppimode or profile.bezels or profile.manual_offsets_useronly)
        )

        # Basic settings
        if profile.spanmode == "single" and not legacy_advanced:
            self.radiobox_spanmode.SetSelection(0)
            self.use_multi_image = False
        elif profile.spanmode == "advanced" or legacy_advanced:
            self.show_advanced_settings = True
            self.use_multi_image = False
            self.radiobox_spanmode.SetSelection(1)
        elif profile.spanmode == "multi":
            self.use_multi_image = True
            self.radiobox_spanmode.SetSelection(2)
        else:
            # default to simple span
            self.radiobox_spanmode.SetSelection(0)

        if profile.slideshow:
            self.cb_slideshow.SetValue(True)
            wx.PostEvent(self.cb_slideshow, wx.CommandEvent(commandEventType=wx.EVT_CHECKBOX.typeId))
            self.tc_sshow_delay.ChangeValue(str(profile.delay_list[0] / 60))
            if profile.sortmode == "shuffle":
                self.ch_sshow_sort.SetSelection(0)
            elif profile.sortmode == "alphabetical":
                self.ch_sshow_sort.SetSelection(1)
            elif profile.sortmode == "date_seeded_shuffle":
                self.ch_sshow_sort.SetSelection(2)
            else:
                self.ch_sshow_sort.SetSelection(wx.NOT_FOUND)
        else:
            self.cb_slideshow.SetValue(False)
            wx.PostEvent(self.cb_slideshow, wx.CommandEvent(commandEventType=wx.EVT_CHECKBOX.typeId))
            self.tc_sshow_delay.Clear()
            self.ch_sshow_sort.SetSelection(wx.NOT_FOUND)

        if profile.hk_binding:
            self.cb_hotkey.SetValue(True)
            self.tc_hotkey_bind.ChangeValue(self.show_hkbinding(profile.hk_binding))
        else:
            self.cb_hotkey.SetValue(False)
            self.tc_hotkey_bind.Clear()
        wx.PostEvent(self.cb_hotkey, wx.CommandEvent(commandEventType=wx.EVT_CHECKBOX.typeId))

        # Advanced settings
        self.show_adv_setting_sizer(self.show_advanced_settings)
        # profile.inches: not stored in profile anymore
        # profile.bezels: not stored in profile anymore
        if profile.manual_offsets_useronly:
            self.cb_offsets.SetValue(True)
            for tc, off in zip(self.tc_list_offsets, profile.manual_offsets_useronly):
                offstr = f"{off[0]},{off[1]}"
                tc.SetValue(offstr)
        else:
            self.cb_offsets.SetValue(False)
        wx.PostEvent(self.cb_offsets, wx.CommandEvent(commandEventType=wx.EVT_CHECKBOX.typeId))
        if profile.perspective:
            self.ch_persp.SetSelection(self.ch_persp.FindString(profile.perspective, False))
        else:
            self.ch_persp.SetSelection(0)
        if profile.spangroups:
            self.cb_spangroups.SetValue(True)
            for ch in self.ch_list_spangroups:
                dsp_id = self.ch_list_spangroups.index(ch)
                for grp in profile.spangroups:
                    if dsp_id in grp:
                        ch.SetSelection(profile.spangroups.index(grp))
                        break
                    else:
                        continue
                    # dsp_id wasn't in any group?
                    ch.SetSelection(0)
            self.toggle_spangroup_widgets(True)
        else:
            self.cb_spangroups.SetValue(False)
            self.toggle_spangroup_widgets(False)
            for ch in self.ch_list_spangroups:
                ch.SetSelection(0)

        # Paths displays: get number to show from profile.
        self.paths_array_to_listctrl(profile.paths_array)

        # Image scaling & position
        zoom_pct = round(profile.zoom * 100)
        offx_pct = round(profile.offsets[0] * 100)
        offy_pct = round(profile.offsets[1] * 100)
        self.sld_zoom.SetValue(zoom_pct)
        self.sld_offx.SetValue(offx_pct)
        self.sld_offy.SetValue(offy_pct)
        self.st_zoom_val.SetLabel(f"{zoom_pct}%")
        self.st_offx_val.SetLabel(str(offx_pct))
        self.st_offy_val.SetLabel(str(offy_pct))
        self.wpprev_pnl.zoom = profile.zoom
        self.wpprev_pnl.offset = profile.offsets
        self.studio_quick_zoom.SetValue(round(profile.zoom * 100))
        self.studio_zoom_label.SetLabel(f"{round(profile.zoom * 100)}%")
        self.cb_cloud_upscale.SetValue(getattr(profile, "cloud_upscale", False))
        scale_mode = normalize_scale_mode(getattr(profile, "cloud_upscale_scale", "auto"))
        self.ch_cloud_scale.SetSelection(UPSCALE_MODES.index(scale_mode))
        sharpen = normalize_sharpen(getattr(profile, "cloud_upscale_sharpen", 0))
        self.sld_cloud_sharpen.SetValue(sharpen)
        self.st_cloud_sharpen.SetLabel(str(sharpen))
        self.wpprev_pnl.sharpen = sharpen
        tone = tuple(
            normalize_adjustment(getattr(profile, f"local_{key}", 0))
            for key in ("brightness", "contrast", "saturation")
        )
        for key, level in zip(("brightness", "contrast", "saturation"), tone):
            self.studio_tone_controls[key].SetValue(level)
            self.studio_tone_labels[key].SetLabel(str(level))
        self.wpprev_pnl.tone = tone
        self._sync_cloud_quality_controls()
        self._refresh_local_shader_options(getattr(profile, "local_shader", ""))

        # Update wallpaper preview from selected profile
        if self.show_advanced_settings:
            display_data = self.display_sys.get_disp_list(True)
        else:
            display_data = self.display_sys.get_disp_list(False)
        self.wpprev_pnl.preview_wallpaper(
            # profile.next_wallpaper_files(peek=True),
            self.parent_tray_obj.get_profile_by_name(profile.name).next_wallpaper_files(peek=True),
            self.show_advanced_settings,
            self.use_multi_image,
            display_data,
            self.read_spangroups(True),
        )
        self.wpprev_pnl.toggle_buttons(show_config=self.show_advanced_settings, in_config=False)
        self.studio_fit_choice.SetSelection(self.radiobox_spanmode.GetSelection())
        # Advanced Span edits a physical PPI/bezel canvas, not the stitched
        # virtual desktop. Start in the mode that matches those settings;
        # Desktop layout remains an explicit option in the preview toolbar.
        self.studio_desktop_layout.SetValue(not self.show_advanced_settings)
        self.wpprev_pnl.set_desktop_layout(not self.show_advanced_settings and self._workspace != "Displays")
        # A profile may load while GTK is still assigning the preview its
        # initial size. Recompute after layout rather than requiring the user
        # to enter and cancel Positions to trigger a redraw.
        self.resized = True
        self._studio_refresh_image_card()

        # Loaded data is the new clean baseline. Slideshow normalization in
        # _collect_temp_profile means the later (posted) checkbox events produce
        # the same serialization, so this stays clean without a deferred call.
        self._loading = False
        self._set_clean_baseline()
        if hasattr(self, "studio_monitor_choice"):
            self._refresh_studio_monitor_options()

    def paths_array_to_listctrl(self, paths_array):
        multi_img = self.use_multi_image or self.use_spangroups()
        self.refresh_path_listctrl(multi_img)
        if multi_img:
            for plist, idx in zip(paths_array, range(len(paths_array))):
                for pth in plist:
                    self.append_to_listctrl([str(idx), pth])
        else:
            for plist in paths_array:
                for pth in plist:
                    self.append_to_listctrl([pth])

    def show_hkbinding(self, hktuple):
        """Format a hotkey tuple into a '+' separated string."""
        if hktuple:
            hkstring = "+".join(hktuple)
            return hkstring
        else:
            return ""

    #
    # Helper methods
    #
    def _current_serial(self):
        """Serialize the current field state for change comparison.

        Mirrors exactly what Save would write to disk (minus the background
        selection), so dirtiness tracks real persisted differences.
        """
        tmp_profile, _groups = self._collect_temp_profile(resolve_selection=False)
        return tmp_profile._serialize()

    def _set_clean_baseline(self):
        """Record the current field state as the saved/clean baseline."""
        self._clean_serial = self._current_serial()
        self.button_save.Enable(False)
        self.button_revert.Enable(False)

    def _update_dirty_state(self):
        """Enable/disable Save and Revert based on unsaved profile changes."""
        if self._loading or self._clean_serial is None:
            return
        dirty = self._current_serial() != self._clean_serial
        if dirty != self.button_save.IsEnabled():
            self.button_save.Enable(dirty)
            self.button_revert.Enable(dirty)

    def _on_field_changed(self, event):
        """Panel-level handler: re-evaluate dirty state when a field changes."""
        event.Skip()
        self._update_dirty_state()

    #
    # System (display) settings change tracking
    #
    # These settings (display diagonal sizes, bezels, physical positions) live in
    # the shared DisplaySystem and apply to every profile. They are edited live so
    # the preview can be used to dial in values, but nothing is written to disk
    # until the system Save button is pressed. Revert restores the snapshot taken
    # at load / last save. This is the system-scoped analogue of the profile
    # Save/Revert above.
    def _system_snapshot(self):
        """Capture the system-wide display settings as a comparable structure."""
        ds = self.display_sys
        return {
            "bezels": tuple((round(b[0], 2), round(b[1], 2)) for b in ds.bezels_in_mm()),
            "offsets": tuple((round(o[0]), round(o[1])) for o in ds.get_ppinorm_offsets()),
            "use_user_diags": ds.use_user_diags,
            "diags": tuple(round(dsp.diagonal_size()[1], 2) for dsp in ds.disp_list),
        }

    def _apply_system_snapshot(self, snap):
        """Restore DisplaySystem to a previously captured snapshot."""
        ds = self.display_sys
        ds.update_bezels([tuple(b) for b in snap["bezels"]])
        if snap["use_user_diags"]:
            ds.update_display_diags(list(snap["diags"]), reset_offsets=False)
        else:
            ds.update_display_diags("auto")
        # Positions are restored last; the diag/bezel updates above recompute
        # initial offsets, so the saved offsets must be written afterwards.
        ds.update_ppinorm_offsets([tuple(o) for o in snap["offsets"]])
        self._refresh_system_preview()

    def _refresh_system_preview(self):
        """Push current DisplaySystem layout to the wallpaper preview."""
        display_data = self.display_sys.get_disp_list(self.show_advanced_settings)
        self.wpprev_pnl.update_display_data(display_data, self.show_advanced_settings, self.use_multi_image)

    def _refresh_diaginch_fields(self):
        """Sync the diagonal-size text fields and checkbox to the DisplaySystem."""
        if not hasattr(self, "tc_list_diaginch"):
            return
        self.cb_diaginch.SetValue(self.display_sys.use_user_diags)
        diags = [str(dsp.diagonal_size()[1]) for dsp in self.display_sys.disp_list]
        for tc, diag in zip(self.tc_list_diaginch, diags):
            tc.ChangeValue(diag)
            tc.Enable(self.display_sys.use_user_diags)

    def _set_system_baseline(self):
        """Record the current system settings as the saved/clean baseline."""
        self._system_clean = self._system_snapshot()
        if hasattr(self, "button_system_save"):
            self.button_system_save.Enable(False)
            self.button_system_revert.Enable(False)

    def _update_system_dirty(self):
        """Enable/disable the system Save and Revert based on unsaved changes."""
        if self._loading or self._system_clean is None or not hasattr(self, "button_system_save"):
            return
        dirty = self._system_snapshot() != self._system_clean
        if dirty != self.button_system_save.IsEnabled():
            self.button_system_save.Enable(dirty)
            self.button_system_revert.Enable(dirty)

    def _on_system_field_changed(self, event):
        """Live-apply valid diagonal-size edits and refresh the system dirty state.

        Consumes the event so it does not also reach the profile-level field
        handler (display sizes are system-wide, not part of a profile).
        """
        if not self._loading and self.cb_diaginch.GetValue():
            inches = []
            for tc in self.tc_list_diaginch:
                val = self.test_diag_value(tc.GetValue())
                if not val:
                    # Mid-edit / invalid entry: don't apply, wait for valid input.
                    inches = None
                    break
                inches.append(val)
            if inches:
                self.display_sys.update_display_diags(inches, reset_offsets=False)
                self._refresh_system_preview()
        self._update_system_dirty()

    def onSaveSystem(self, event):
        """Persist the staged system-wide display settings to disk."""
        self.display_sys.save_system()
        self._set_system_baseline()

    def onRevertSystem(self, event):
        """Discard unsaved system-wide display changes, restoring the baseline."""
        if self._system_clean is None:
            return
        self._loading = True
        self._apply_system_snapshot(self._system_clean)
        self._refresh_diaginch_fields()
        self._loading = False
        self._update_system_dirty()

    def update_choiceprofile(self):
        """Reload profile list into the choice box."""
        # self.list_of_profiles = list_profiles()
        self.list_of_profiles = self.parent_tray_obj.list_of_profiles
        self.profnames = []
        for prof in self.list_of_profiles:
            self.profnames.append(prof.name)
        self.profnames.append("Create a new profile")
        self.choice_profiles.SetItems(self.profnames)
        self._refresh_profile_gallery()

    def list_of_textctrl(self, ctrl_parent, num_disp, fraction=1 / 2):
        tcrtl_list = []
        for _i in range(num_disp):
            tcrtl_list.append(
                wx.TextCtrl(
                    ctrl_parent,
                    -1,
                    size=wx.Size(int(self.tc_width * fraction), -1),
                    style=wx.TE_RIGHT,
                )
            )
        return tcrtl_list

    def list_of_wxchoice(self, ctrl_parent, num_disp, fraction=1 / 2):
        ch_list = []
        for _i in range(num_disp):
            ch_list.append(
                wx.ComboBox(
                    ctrl_parent,
                    -1,
                    size=wx.Size(int(self.tc_width * fraction), -1),
                    style=wx.CB_READONLY,
                )
            )
        return ch_list

    def sizer_toggle_children(self, sizer, bool_state, toggle_cb=False):
        for child in sizer.GetChildren():
            if child.IsSizer():
                self.sizer_toggle_children(child.GetSizer(), bool_state)
            else:
                widget = child.GetWindow()
                # Help/note buttons stay clickable even when their section is
                # disabled, so the explanation is always available to the user.
                if widget is not None and widget.GetName().startswith("butt_help"):
                    continue
                if isinstance(widget, (wx.TextCtrl, wx.StaticText, wx.Choice, wx.ComboBox, wx.Button)) or (
                    isinstance(widget, wx.CheckBox) and toggle_cb
                ):
                    widget.Enable(bool_state)

    def toggle_radio_and_profile_choice(self, enable):
        """Toggle enabled state of span mode radiobox and profile sizer children."""
        self.radiobox_spanmode.Enable(enable)
        self.sizer_toggle_children(self.sizer_profiles, enable)
        self.sizer_toggle_children(self.sizer_setting_diaginch, enable, True)
        if enable:
            try:
                diag_cb_state = self.cb_diaginch.GetValue()
                self.sizer_toggle_children(self.sizer_setting_diaginch, diag_cb_state)
            except AttributeError:
                pass

    def show_adv_setting_sizer(self, show_bool):
        """Show/Hide the sizer for advanced spanning settings."""
        self.sizer_displays.Show(
            self.sizer_setting_adv, show=show_bool and getattr(self, "_workspace", "") == "Displays", recursive=True
        )
        self.toggle_bezel_buttons(enable_config_butt=True)
        # To only reveal sizer sit no frame resize
        self.path_listctrl.InvalidateBestSize()
        # self.sizer_setting_paths.SetItemMinSize(self.path_listctrl, (1000, -1))
        self.sizer_main.Layout()
        self.FitInside()
        # to re-layout the whole window making it wider run:
        # self.sizer_setting_adv.Layout()
        # self.sizer_main.Fit(self.frame)
        self.sizer_bottom_buttonrow.Show(self.button_align_test, show=show_bool)
        self.sizer_bottom_buttonrow.Show(self.button_perspectives, show=show_bool)
        self.sizer_bottom_buttonrow.Layout()

    def toggle_bezel_buttons(self, bezel_mode=False, enable_config_butt=True):
        """Show/Hide bezel config buttons.

        If not in bezel mode show config button, and if in it hide config and show
        save and cancel buttons.
        enable_config_butt optional controls whether the config button should be
        enabled/disabled."""
        # self.sizer_bezel_buttons.Show(self.button_bezels, show=not bezel_mode)
        # self.sizer_bezel_buttons.Show(self.button_bezels_save, show=bezel_mode)
        # self.sizer_bezel_buttons.Show(self.button_bezels_canc, show=bezel_mode)
        # self.sizer_bezel_buttons.Layout()
        self.button_bezels.Enable(enable_config_butt)
        self.button_bezels_save.Enable(bezel_mode)
        self.button_bezels_canc.Enable(bezel_mode)
        self.button_help_bezel.Enable(True)

    def refresh_path_listctrl(self, use_multi_image, migrate_paths=False):
        if use_multi_image == self.multi_column_listc and migrate_paths:
            self.sizer_main.Layout()
        else:
            if migrate_paths and self.path_listctrl.GetItemCount():
                # warn that paths can't be migrated
                msg = (
                    "Wallpaper sources cannot be migrated between single span,"
                    " span groups, or multi image, continue?"
                    "\n"
                    "Saved sources are not affected until you overwrite."
                )
                res = show_message_dialog(msg, style="YES_NO")
                if not res:
                    # user canceled
                    return False
            self.path_listctrl.Destroy()
            self.image_list.RemoveAll()
            if use_multi_image:
                self.multi_column_listc = True
                self.path_listctrl = wx.ListCtrl(
                    self.statbox_parent_paths,
                    -1,
                    style=wx.LC_REPORT | wx.BORDER_SIMPLE | wx.LC_SORT_ASCENDING,
                )
                col0_str = "Display"
                if self.use_spangroups():
                    col0_str = "Group"
                self.path_listctrl.InsertColumn(0, col0_str, wx.LIST_FORMAT_RIGHT, width=100)
                self.path_listctrl.InsertColumn(1, "Source", width=400)
            else:
                self.multi_column_listc = False
                # show simpler listing without header if only one wallpaper target
                self.path_listctrl = wx.ListCtrl(
                    self.statbox_parent_paths,
                    -1,
                    style=wx.LC_REPORT | wx.BORDER_SIMPLE | wx.LC_NO_HEADER,
                )
                self.path_listctrl.InsertColumn(0, "Source", width=500)
            self.path_listctrl.SetImageList(self.image_list, wx.IMAGE_LIST_SMALL)
            self.path_listctrl.Bind(wx.EVT_LIST_ITEM_SELECTED, self.onWallpaperItemSelected)
            self.path_listctrl.Bind(wx.EVT_SIZE, self._on_sources_resize)
            self.path_listctrl.Bind(wx.EVT_MOUSEWHEEL, self._on_paths_wheel)
            self.sizer_setting_paths.Insert(1, self.path_listctrl, 1, wx.CENTER | wx.EXPAND | wx.ALL, 5)
            self.path_listctrl.InvalidateBestSize()
            # self.sizer_setting_paths.SetItemMinSize(self.path_listctrl, (1000, -1))
            self.sizer_main.Layout()
        return True

    def test_diag_value(self, inch_str):
        """Test that entered inch_str is a valid size and return it."""
        try:
            num = float(inch_str)
            if num > 0:
                return num
            else:
                return False
        except ValueError:
            return False

    def read_spangroups(self, one_is_none=False):
        """Reads user input for span groups."""
        groups = {}
        for ch in self.ch_list_spangroups:
            val = ch.GetSelection()
            index = self.ch_list_spangroups.index(ch)
            if val in groups:
                groups[val].append(index)
            else:
                groups[val] = [index]
        if one_is_none and len(groups.keys()) < 2:
            return None
        return groups

    def use_spangroups(self):
        """Check UI if spangroups are in use."""
        cb_state = self.cb_spangroups.GetValue()
        return cb_state and self.show_advanced_settings

    def toggle_spangroup_widgets(self, enable):
        sizer = self.sizer_setting_spangroups
        self.sizer_toggle_children(sizer, enable)
        for ch in self.ch_list_spangroups:
            ch.Enable(enable)

    #
    # Event methods
    #
    def _on_paths_wheel(self, event):
        """Always scroll the enclosing settings window over wallpaper paths."""
        if event.ControlDown() or event.ShiftDown():
            event.Skip()
            return
        units, self._source_wheel_remainder = wheel_scroll_units(
            event.GetWheelRotation(),
            event.GetWheelDelta(),
            event.GetLinesPerAction(),
            self._source_wheel_remainder,
        )
        if units:
            x, y = self.GetViewStart()
            self.Scroll(x, y + units)

    def on_wallpaper_dragged(self, offsets):
        """Synchronize dragging with the persisted zoom/offset controls."""
        if not (self.sld_offx.IsEnabled() and self.sld_offy.IsEnabled()):
            return
        x = round(offsets[0] * 100)
        y = round(offsets[1] * 100)
        if (x, y) != (self.sld_offx.GetValue(), self.sld_offy.GetValue()):
            self.sld_offx.SetValue(x)
            self.sld_offy.SetValue(y)
            self.onZoomOffsetChange(None)

    def _sync_cloud_quality_controls(self):
        cloud_enabled = self.cb_cloud_upscale.GetValue() and self.cb_cloud_upscale.IsEnabled()
        self.ch_cloud_scale.Enable(cloud_enabled)
        # Local sharpening belongs to the normal image pipeline and does not
        # require network access or an enabled cloud upscaler.
        self.sld_cloud_sharpen.Enable(self.sld_zoom.IsEnabled())

    def _on_cloud_upscale_changed(self, event):
        """Record consent; actual network requests run on the render worker."""
        self._sync_cloud_quality_controls()
        self._update_dirty_state()

    def _on_cloud_quality_changed(self, event):
        """Refresh source preview locally when sharpening changes."""
        value = self.sld_cloud_sharpen.GetValue()
        self.st_cloud_sharpen.SetLabel(str(value))
        if value != self.wpprev_pnl.sharpen:
            self.wpprev_pnl.sharpen = value
            self.wpprev_pnl.update_zoom_offset(self.wpprev_pnl.zoom, self.wpprev_pnl.offset)
        self._studio_update_compare_feedback()
        self._update_dirty_state()

    def _refresh_local_shader_options(self, selected):
        """Show modes by default; preserve saved legacy individual effects."""
        selected = normalize_shader(selected)
        installed = set(available_shaders())
        presets = sorted(name for name in installed if name.startswith("Anime4K_Mode_"))
        individual = sorted(name for name in installed if not name.startswith("Anime4K_Mode_"))
        names = presets + (individual if self.cb_shader_advanced.GetValue() else [])
        if selected and selected not in names:
            names.append(selected)
        self._shader_names = ["", *names]

        def display_name(name):
            label = name.removeprefix("Anime4K_").removesuffix(".glsl").replace("_", " ")
            if "AutoDownscalePre" in name:
                return label + " (pipeline helper; select a Mode instead)"
            if name not in installed:
                return label + " (missing)"
            if name not in presets and not self.cb_shader_advanced.GetValue():
                return label + " (saved custom effect)"
            return label

        self.ch_local_shader.SetItems(["None", *(display_name(name) for name in names)])
        self.ch_local_shader.SetSelection(self._shader_names.index(selected))

    def _on_shader_advanced_toggled(self, event):
        """Toggle individual effects without changing the stored selection."""
        selected = self._shader_names[max(0, self.ch_local_shader.GetSelection())]
        self._refresh_local_shader_options(selected)
        if hasattr(self, "studio_inspector"):
            self.sizer_setting_processing.Layout()
            self.studio_inspector.Layout()

    def _set_processing_tab(self, name):
        """Show only the selected, functional image-processing controls."""
        if name not in self.processing_tab_buttons:
            return
        self._processing_tab = name
        panels = {
            "Basic": (True, False, True),
            "Cloud AI": (True, False, False),
            "Shaders": (False, True, False),
            "Adjustments": (False, False, True),
        }
        cloud, shaders, local = panels[name]
        for button_name, button in self.processing_tab_buttons.items():
            selected = name == button_name
            button.SetSelected(selected)
        self.sizer_setting_processing.Show(self.sizer_processing_cloud, show=cloud, recursive=True)
        self.sizer_setting_processing.Show(self.sizer_processing_shaders, show=shaders, recursive=True)
        self.sizer_setting_processing.Show(self.sizer_processing_local, show=local, recursive=True)
        if hasattr(self, "studio_inspector"):
            self.sizer_setting_processing.Layout()
            self.studio_inspector.Layout()
            self.sizer_main.Layout()
            self.FitInside()

    def _on_studio_tone_changed(self, event):
        tone = tuple(self.studio_tone_controls[key].GetValue() for key in ("brightness", "contrast", "saturation"))
        for key, value in zip(("brightness", "contrast", "saturation"), tone):
            self.studio_tone_labels[key].SetLabel(str(value))
        self.wpprev_pnl.tone = tone
        self.wpprev_pnl.update_zoom_offset(self.wpprev_pnl.zoom, self.wpprev_pnl.offset)
        self._studio_update_compare_feedback()
        self._update_dirty_state()

    def _on_local_shader_changed(self, event):
        """Record the shader selection; GPU work happens during wallpaper render."""
        self._update_dirty_state()

    def _on_import_shaders(self, event):
        with wx.FileDialog(
            self,
            "Import local Anime4K shaders",
            wildcard="Shader packs (*.tar.gz;*.zip;*.glsl)|*.tar.gz;*.zip;*.glsl|All files (*.*)|*.*",
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        ) as dialog:
            if dialog.ShowModal() != wx.ID_OK:
                return
            archive = dialog.GetPath()
        try:
            count = import_shader_pack(archive)
        except (OSError, ValueError, ShaderImportError) as exc:
            wx.MessageBox(str(exc), "Shader import failed", wx.OK | wx.ICON_ERROR, self)
            return
        old_selected = self._shader_names[max(0, self.ch_local_shader.GetSelection())]
        self._refresh_local_shader_options(old_selected)
        wx.MessageBox(
            f"Installed {count} MPV Anime4K shader files. Choose an ordered Mode A/B/C preset "
            "or an individual enhancement, then Save & Apply.",
            "Shader pack imported",
            wx.OK | wx.ICON_INFORMATION,
            self,
        )

    def onZoomOffsetChange(self, event):
        """Live-update the preview as zoom/position sliders move."""
        zoom_pct = self.sld_zoom.GetValue()
        offx_pct = self.sld_offx.GetValue()
        offy_pct = self.sld_offy.GetValue()
        self.st_zoom_val.SetLabel(f"{zoom_pct}%")
        self.studio_quick_zoom.SetValue(zoom_pct)
        self.studio_zoom_label.SetLabel(f"{zoom_pct}%")
        self.st_offx_val.SetLabel(str(offx_pct))
        self.st_offy_val.SetLabel(str(offy_pct))
        self.wpprev_pnl.update_zoom_offset(zoom_pct / 100.0, (offx_pct / 100.0, offy_pct / 100.0))
        self._update_dirty_state()

    def onResize(self, event):
        # wx sends intermediate 0-height sizes while the scrolled window lays
        # out its children. Defer bitmap work until the preview has real space.
        self.resized = True
        event.Skip()

    def onIdle(self, event):
        leftdown = wx.GetMouseState().LeftIsDown()
        update = bool(self.resized and not leftdown and self.wpprev_pnl.preview_area_ready())
        if update:
            self.wpprev_pnl.full_refresh_preview(
                update,
                self.show_advanced_settings,
                self.use_multi_image,
                spangroups=self.read_spangroups(True),
            )
            self.resized = False
        event.Skip()

    def onSpanRadio(self, event):
        old_adv_set = self.show_advanced_settings
        old_mult_img_set = self.use_multi_image
        selection = self.radiobox_spanmode.GetSelection()
        if selection == 1:
            self.show_advanced_settings = True
            self.use_multi_image = False
        elif selection == 2:
            self.show_advanced_settings = False
            self.use_multi_image = True
        else:
            self.show_advanced_settings = False
            self.use_multi_image = False
        cont = self.refresh_path_listctrl(self.use_multi_image, migrate_paths=True)
        if not cont:
            self.show_advanced_settings = old_adv_set
            self.use_multi_image = old_mult_img_set
            if old_adv_set and not old_mult_img_set:
                self.radiobox_spanmode.SetSelection(1)
            elif not old_adv_set and old_mult_img_set:
                self.radiobox_spanmode.SetSelection(2)
            else:
                self.radiobox_spanmode.SetSelection(0)
            self.studio_fit_choice.SetSelection(self.radiobox_spanmode.GetSelection())
            return
        self.show_adv_setting_sizer(self.show_advanced_settings)
        display_data = self.display_sys.get_disp_list(self.show_advanced_settings)
        spangroups = None
        if self.cb_spangroups.GetValue():
            spangroups = self.read_spangroups(True)
        self.wpprev_pnl.update_display_data(
            display_data, self.show_advanced_settings, self.use_multi_image, spangroups=spangroups
        )
        self.wpprev_pnl.toggle_buttons(show_config=self.show_advanced_settings, in_config=False)
        self.studio_fit_choice.SetSelection(self.radiobox_spanmode.GetSelection())
        self.studio_desktop_layout.SetValue(not self.show_advanced_settings)
        self.wpprev_pnl.set_desktop_layout(not self.show_advanced_settings and self._workspace != "Displays")
        self.resized = True
        self._update_dirty_state()

    def onCheckboxSlideshow(self, event):
        cb_state = self.cb_slideshow.GetValue()
        sizer = self.sizer_setting_slideshow
        self.sizer_toggle_children(sizer, cb_state)
        # Slideshow needs a sort order; default to Shuffle when enabling so the
        # dropdown isn't left empty (an unselected dropdown also broke the
        # dirty-state tracking that reads its current value).
        if cb_state and self.ch_sshow_sort.GetSelection() == wx.NOT_FOUND:
            self.ch_sshow_sort.SetSelection(0)
        # Zoom/pan is tuned to a single image's framing. In a slideshow the same
        # crop would be forced onto every (differently composed) image, so reset
        # the controls to defaults and disable them while slideshow is enabled.
        if cb_state:
            self.reset_zoom_offset()
        self.toggle_zoom_widgets(not cb_state)
        self._update_dirty_state()

    def reset_zoom_offset(self):
        """Return the zoom/position sliders to their no-op defaults."""
        self.sld_zoom.SetValue(100)
        self.sld_offx.SetValue(0)
        self.sld_offy.SetValue(0)
        self.onZoomOffsetChange(None)

    def onResetZoom(self, event):
        """Reset the image scaling & position to defaults.

        Only the zoom/position controls are affected; the rest of the profile
        configuration is left untouched.
        """
        self.reset_zoom_offset()

    def toggle_zoom_widgets(self, enable):
        """Enable/disable the image scaling & position controls."""
        self.sizer_toggle_children(self.sizer_setting_zoom, enable)
        for sld in (self.sld_zoom, self.sld_offx, self.sld_offy):
            sld.Enable(enable)
        self.studio_quick_zoom.Enable(enable)
        self._sync_cloud_quality_controls()

    def onCheckboxHotkey(self, event):
        cb_state = self.cb_hotkey.GetValue()
        sizer = self.hotkey_bind_sizer
        self.sizer_toggle_children(sizer, cb_state)
        self._update_dirty_state()

    def onCheckboxOffsets(self, event):
        cb_state = self.cb_offsets.GetValue()
        sizer = self.sizer_setting_offsets
        self.sizer_toggle_children(sizer, cb_state)
        self._update_dirty_state()

    def onCheckboxSpanGroups(self, event):
        cb_state = self.cb_spangroups.GetValue()
        cont = self.refresh_path_listctrl(cb_state, migrate_paths=True)
        if not cont:
            self.cb_spangroups.SetValue(not cb_state)
            return
        self.toggle_spangroup_widgets(cb_state)
        self._update_dirty_state()

    def onCheckboxDiaginch(self, event):
        """Toggle manual display sizes. Changes are staged, not saved to disk.

        Checking enables the inch fields and applies their current values to the
        preview; unchecking returns to auto-detected sizes. Either way the change
        is only persisted when the system band's Save is pressed.
        """
        cb_state = self.cb_diaginch.GetValue()
        for tc in self.tc_list_diaginch:
            tc.Enable(cb_state)
        if cb_state:
            # Apply the current field values (if valid) so the preview updates.
            self._on_system_field_changed(None)
        else:
            # Back to auto-detected sizes (staged only).
            self.display_sys.update_display_diags("auto")
            self._refresh_diaginch_fields()
            self._refresh_system_preview()
            self._update_system_dirty()

    #
    # ListCtrl methods
    #

    def _on_sources_resize(self, event):
        """Fit the source column to the available space."""
        ctrl = event.GetEventObject()
        available = max(100, ctrl.GetClientSize().width - 16)
        if ctrl.GetColumnCount() == 2:
            display_width = min(105, max(45, available // 4))
            ctrl.SetColumnWidth(0, display_width)
            ctrl.SetColumnWidth(1, max(60, available - display_width))
        else:
            ctrl.SetColumnWidth(0, available)
        event.Skip()

    def append_to_listctrl(self, data_row):
        """Show each source once per target, preserving missing saved paths."""
        target = data_row[0] if len(data_row) == 2 else ""
        path = data_row[-1]
        candidate = source_identity(path, target)
        columns = self.path_listctrl.GetColumnCount()
        for row in range(self.path_listctrl.GetItemCount()):
            existing_target = self.path_listctrl.GetItemText(row, 0) if columns == 2 else ""
            existing_path = self.path_listctrl.GetItemText(row, columns - 1)
            if source_identity(existing_path, existing_target) == candidate:
                return
        if (self.use_multi_image or self.use_spangroups()) and len(data_row) == 2:
            img_id = self.add_to_imagelist(data_row[1])
            index = self.path_listctrl.InsertItem(self.path_listctrl.GetItemCount(), data_row[0], img_id)
            self.path_listctrl.SetItem(index, 1, data_row[1])
        elif not self.use_multi_image and len(data_row) == 1:
            img_id = self.add_to_imagelist(data_row[0])
            index = self.path_listctrl.InsertItem(self.path_listctrl.GetItemCount(), data_row[0], img_id)
        else:
            sp_logging.G_LOGGER.info("UseMultImg: %s. Bad data_row: %s", self.use_multi_image, data_row)

    def add_to_imagelist(self, path):
        folder_bmp = wx.ArtProvider.GetBitmap(wx.ART_FOLDER, wx.ART_TOOLBAR, wx.Size(*self.tsize))
        if os.path.isdir(path):
            img_id = self.image_list.Add(folder_bmp)
        else:
            thumb_bmp = self.create_thumb_bmp(path)
            img_id = self.image_list.Add(thumb_bmp)
        return img_id

    def create_thumb_bmp(self, filename):
        wximg = wx.Image(filename, type=wx.BITMAP_TYPE_ANY)
        if not wximg.IsOk() or wximg.GetWidth() <= 0 or wximg.GetHeight() <= 0:
            return wx.ArtProvider.GetBitmap(wx.ART_NORMAL_FILE, wx.ART_TOOLBAR, wx.Size(*self.tsize))
        imgsize = wximg.GetSize()
        w2h_ratio = imgsize[0] / imgsize[1]
        if w2h_ratio > 1:
            target_w = self.tsize[0]
            target_h = target_w / w2h_ratio
            pos = (0, round((target_w - target_h) / 2))
        else:
            target_h = self.tsize[1]
            target_w = target_h * w2h_ratio
            pos = (round((target_h - target_w) / 2), 0)
        bmp = (
            wximg.Scale(round(target_w), round(target_h), quality=wx.IMAGE_QUALITY_BOX_AVERAGE)
            .Resize(wx.Size(*self.tsize), wx.Point(pos))
            .ConvertToBitmap()
        )
        return bmp

    def populate_lc_browse(self, pathslist, imglist):
        for path_item in pathslist:
            self.append_to_listctrl(path_item)
        self._update_dirty_state()

    #
    # Top level button definitions
    #
    def onWallpaperItemSelected(self, event):
        """Preview the selected wallpaper image in the preview panel."""
        item_idx = event.GetIndex()
        columns = self.path_listctrl.GetColumnCount()
        if columns == 2:
            path = self.path_listctrl.GetItemText(item_idx, 1)
        else:
            path = self.path_listctrl.GetItemText(item_idx, 0)

        if not path:
            return

        # Resolve folder to first image inside it
        preview_file = None
        if os.path.isdir(path):
            for f in sorted(os.listdir(path)):
                if f.lower().endswith(wpproc.G_SUPPORTED_IMAGE_EXTENSIONS):
                    preview_file = os.path.join(path, f)
                    break
        elif os.path.isfile(path):
            preview_file = path

        if not preview_file:
            return

        if self.show_advanced_settings:
            display_data = self.display_sys.get_disp_list(True)
        else:
            display_data = self.display_sys.get_disp_list(False)
        self.wpprev_pnl.preview_wallpaper(
            [preview_file],
            self.show_advanced_settings,
            self.use_multi_image,
            display_data,
            self.read_spangroups(True),
        )
        self._studio_refresh_image_card()

    def onConfigureBezels(self, event):
        """Start bezel size config mode."""
        self.toggle_radio_and_profile_choice(False)
        self.wpprev_pnl.start_bezel_config()
        self.button_bezels.Disable()
        self.button_bezels_save.Enable()
        self.button_bezels_canc.Enable()

    def onConfigureBezelsSave(self, event):
        """Exit bezel config mode, staging the new bezel sizes (not saved yet)."""
        self.toggle_radio_and_profile_choice(True)
        self.wpprev_pnl.bezel_config_save()
        self.button_bezels_save.Disable()
        self.button_bezels_canc.Disable()
        self.button_bezels.Enable()
        self._update_system_dirty()

    def onConfigureBezelsCanc(self, event):
        """Cancel out of bezel size config mode."""
        self.toggle_radio_and_profile_choice(True)
        self.wpprev_pnl.bezel_config_cancel()
        self.button_bezels_save.Disable()
        self.button_bezels_canc.Disable()
        self.button_bezels.Enable()

    def _choose_native_sources(self, *, folders=False, multiple=False, title="Choose wallpaper images"):
        """Use KDE's file picker on Plasma and an OS file picker elsewhere."""
        directory = self.defdir if os.path.isdir(self.defdir) else os.path.expanduser("~")
        try:
            sources = pick_kde_paths(directory, folders=folders, multiple=multiple, title=title)
        except NativePickerError as exc:
            sp_logging.G_LOGGER.warning("KDE file picker failed; using native wx file dialog: %s", exc)
            sources = None

        # None means the KDE picker was unavailable, not that the user pressed Cancel.
        if sources is None:
            if folders:
                with wx.DirDialog(
                    self, title, defaultPath=directory, style=wx.DD_DEFAULT_STYLE | wx.DD_DIR_MUST_EXIST
                ) as picker:
                    sources = [picker.GetPath()] if picker.ShowModal() == wx.ID_OK else []
            else:
                wildcard = (
                    "Images (*.jpg;*.jpeg;*.png;*.bmp;*.gif;*.tiff;*.webp)|*.jpg;*.jpeg;*.png;*.bmp;*.gif;*.tiff;*.webp"
                )
                flags = wx.FD_OPEN | wx.FD_FILE_MUST_EXIST
                if multiple:
                    flags |= wx.FD_MULTIPLE
                with wx.FileDialog(self, title, defaultDir=directory, wildcard=wildcard, style=flags) as picker:
                    if picker.ShowModal() == wx.ID_OK:
                        sources = picker.GetPaths() if multiple else [picker.GetPath()]
                    else:
                        sources = []

        valid = []
        for source in sources:
            if (folders and os.path.isdir(source)) or (
                not folders and os.path.isfile(source) and source.lower().endswith(IMAGE_EXTENSIONS)
            ):
                valid.append(os.path.abspath(source))
            else:
                kind = "folder" if folders else "image"
                wx.MessageBox(
                    f"Not a supported local wallpaper {kind}: {source}",
                    "Invalid wallpaper source",
                    wx.OK | wx.ICON_WARNING,
                    self,
                )
        if valid:
            last_directory = valid[-1] if folders else os.path.dirname(valid[-1])
            self.defdir = last_directory
            settings = GeneralSettingsData()
            if settings.browse_default_dir != last_directory:
                settings.browse_default_dir = last_directory
                settings.save_settings()
        return valid

    def _choose_source_target(self, selected_row=-1):
        """Keep the existing target or ask which monitor/group receives images."""
        if not (self.use_multi_image or self.use_spangroups()):
            return ""
        if selected_row >= 0:
            return self.path_listctrl.GetItemText(selected_row, 0)
        if self.use_spangroups():
            targets = [str(group) for group in sorted(self.read_spangroups())]
            label = "span group"
        else:
            targets = [str(display) for display in range(len(self.display_sys.disp_list))]
            label = "display"
        if not targets:
            return None
        if len(targets) == 1:
            return targets[0]
        choices = [f"{label.title()} {target}" for target in targets]
        with wx.SingleChoiceDialog(self, f"Choose a {label} for these images:", "Wallpaper target", choices) as picker:
            if picker.ShowModal() != wx.ID_OK:
                return None
            return targets[picker.GetSelection()]

    def _preview_source_path(self, path):
        """Update the real wallpaper preview without saving or applying."""
        if os.path.isdir(path):
            candidate = next(
                (
                    os.path.join(path, name)
                    for name in sorted(os.listdir(path))
                    if name.lower().endswith(wpproc.G_SUPPORTED_IMAGE_EXTENSIONS)
                ),
                None,
            )
        else:
            candidate = path
        if candidate is None:
            return
        display_data = self.display_sys.get_disp_list(self.show_advanced_settings)
        self.wpprev_pnl.preview_wallpaper(
            [candidate],
            self.show_advanced_settings,
            self.use_multi_image,
            display_data,
            self.read_spangroups(True),
        )
        self._studio_refresh_image_card()

    def onChangeImage(self, event):
        """Replace a selected wallpaper image with a native file chooser."""
        paths = self._choose_native_sources(title="Change wallpaper image")
        if not paths:
            return
        path = paths[0]
        columns = self.path_listctrl.GetColumnCount()
        row = self.path_listctrl.GetFirstSelected()
        target = self._choose_source_target(row)
        if target is None:
            return
        if row < 0:
            # Without an explicit row, replace only this monitor/group's first
            # source and preserve all other targets and slideshow entries.
            for index in range(self.path_listctrl.GetItemCount()):
                existing_target = self.path_listctrl.GetItemText(index, 0) if columns == 2 else ""
                if existing_target == target:
                    row = index
                    break
        candidate = source_identity(path, target)
        for index in range(self.path_listctrl.GetItemCount()):
            if index == row:
                continue
            existing_target = self.path_listctrl.GetItemText(index, 0) if columns == 2 else ""
            existing_path = self.path_listctrl.GetItemText(index, columns - 1)
            if source_identity(existing_path, existing_target) == candidate:
                self.studio_status.SetLabel("That image is already in the selected wallpaper sources.")
                return
        if row >= 0:
            self.path_listctrl.DeleteItem(row)
        self.append_to_listctrl([target, path] if columns == 2 else [path])
        for index in range(self.path_listctrl.GetItemCount()):
            existing_target = self.path_listctrl.GetItemText(index, 0) if columns == 2 else ""
            existing_path = self.path_listctrl.GetItemText(index, columns - 1)
            if source_identity(existing_path, existing_target) == candidate:
                self.path_listctrl.Select(index)
                break
        self._preview_source_path(path)
        self._update_dirty_state()

    def onAddImagesSource(self, event):
        """Add source images using the desktop file chooser, never a tree dialog."""
        paths = self._choose_native_sources(multiple=True, title="Add wallpaper images")
        if not paths:
            return
        target = self._choose_source_target()
        if target is None:
            return
        columns = self.path_listctrl.GetColumnCount()
        for path in paths:
            self.append_to_listctrl([target, path] if columns == 2 else [path])
        self._preview_source_path(paths[-1])
        self._update_dirty_state()

    def onAddFolderSource(self, event):
        """Choose a slideshow/source directory in the native folder picker."""
        paths = self._choose_native_sources(folders=True, title="Add wallpaper source folder")
        if not paths:
            return
        target = self._choose_source_target()
        if target is None:
            return
        columns = self.path_listctrl.GetColumnCount()
        for path in paths:
            self.append_to_listctrl([target, path] if columns == 2 else [path])
        self._preview_source_path(paths[-1])
        self._update_dirty_state()

    def onRemoveSource(self, event):
        """Remove every selected source, not only the focused row."""
        selected = []
        item = self.path_listctrl.GetFirstSelected()
        while item != -1:
            selected.append(item)
            item = self.path_listctrl.GetNextSelected(item)
        for item in reversed(selected):
            self.path_listctrl.DeleteItem(item)
        if selected:
            self._update_dirty_state()

    def onClose(self, event):
        """Closes the profile config panel."""
        self.frame.Close(True)

    def onSelect(self, event):
        """Acts once a profile is picked in the dropdown menu."""
        event_object = event.GetEventObject()
        if event_object.GetName() == "ProfileChoice":
            item = event.GetSelection()
            if event.GetString() == "Create a new profile":
                self.onCreateNewProfile(event)
            else:
                self.populate_fields(self.list_of_profiles[item])
        else:
            pass

    def onRevert(self, event):
        """Discards unsaved changes and reloads the saved profile from disk."""
        selection = self.choice_profiles.GetSelection()
        name = self.choice_profiles.GetString(selection) if selection != wx.NOT_FOUND else ""
        if name and name != "Create a new profile":
            profile = self.parent_tray_obj.get_profile_by_name(name)
            if profile is None:
                profile = open_profile(name)
            if profile is not None:
                self.populate_fields(profile)
                return
        # No saved profile to revert to: reset to an empty new profile.
        self.onCreateNewProfile(None)

    def onSaveAndApply(self, event):
        """Persist the source-referenced framing before applying the wallpaper."""
        if self.onSave(None) is not None:
            self.onApply(event)

    def onApply(self, event):
        """Render the current edits as a one-off preview without saving.

        The wallpaper is set using the settings exactly as shown in the dialog,
        but the stored ``.profile`` on disk and the daemon's active/running
        profile are left untouched. Use Save to persist the changes.
        """
        tmp_profile, _groups = self._collect_temp_profile(resolve_selection=True)
        if not tmp_profile.test_save(managed=False):
            sp_logging.G_LOGGER.info("onApply: validation failed, nothing applied.")
            return
        busy = wx.BusyCursor()
        if hasattr(self, "studio_status"):
            self.studio_status.SetLabel("Rendering wallpaper with the current settings...")
        # ProfileData parses from a file, so serialize the working copy to a
        # throwaway file (never the real profile) and render that. This reuses
        # the full ProfileData feature set (zoom/pan, span modes, selection)
        # instead of duplicating render logic.
        fd, temp_file = tempfile.mkstemp(prefix="sp_preview_", suffix=".profile")
        os.close(fd)
        try:
            tmp_profile.save(filename=temp_file)
            preview_profile = parse_profile_file(temp_file)
            sp_logging.G_LOGGER.info("onApply preview (unsaved): %s", preview_profile.name)
            # Render with the dialog's live DisplaySystem so staged (unsaved)
            # display settings — bezels, sizes, positions — are reflected. This
            # deliberately does NOT reload from disk (refresh_display_data), which
            # lets the user test system tweaks via Apply before committing them.
            thrd = change_wallpaper_job(preview_profile, force=True, display_system=copy.deepcopy(self.display_sys))
            # Pump the event loop while rendering so the GUI stays responsive.
            while thrd is not None and thrd.is_alive():
                wx.YieldIfNeeded()
                time.sleep(0.05)
            # The applied image becomes the current selection so reopening the
            # dialog (or a later render) shows the same wallpaper instead of
            # reverting to the previously saved one (#158). Persist it on the
            # live profile instance.
            if tmp_profile.selected:
                live = self._loaded_profile_with_selection()
                if live is not None:
                    live.set_selected_wallpaper(tmp_profile.selected, persist=True)
            if hasattr(self, "studio_status"):
                self.studio_status.SetLabel("Render finished. Check the desktop for the applied wallpaper.")
        finally:
            try:
                os.remove(temp_file)
            except OSError:
                pass
            del busy

    def _get_selected_wallpaper_path(self):
        """Get the file path of the currently selected item in the wallpaper list."""
        item_idx = self.path_listctrl.GetFocusedItem()
        if item_idx == -1:
            return None
        columns = self.path_listctrl.GetColumnCount()
        if columns == 2:
            path = self.path_listctrl.GetItemText(item_idx, 1)
        else:
            path = self.path_listctrl.GetItemText(item_idx, 0)
        if os.path.isdir(path):
            return None
        return path

    def _loaded_profile_with_selection(self):
        """Return the profile currently open in the dialog, by its ORIGINAL name.

        The original name is taken from the profile dropdown, not the (possibly
        just-edited) name field, so that renaming a profile does not lose its
        persistent wallpaper selection. Prefers the live tray instance so an
        updated selection is visible without a reload.
        """
        return self.loaded_profile

    def _collect_temp_profile(self, resolve_selection=False):
        """Builds a TempProfileData from the current dialog fields.

        Returns a ``(tmp_profile, groups)`` tuple where ``groups`` is the span
        groups mapping (or ``None``) used for the preview.

        When ``resolve_selection`` is True the persistent wallpaper selection is
        filled in (from the focused list item, falling back to the saved
        profile). It is left out for change-tracking, where the background
        selection must not count as an unsaved profile change. This method never
        raises on partial input so it is safe to call on every field edit.
        """
        tmp_profile = TempProfileData()
        tmp_profile.name = self.tc_name.GetLineText(0)
        tmp_profile.cloud_upscale = self.cb_cloud_upscale.GetValue()
        tmp_profile.cloud_upscale_scale = UPSCALE_MODES[max(0, self.ch_cloud_scale.GetSelection())]
        tmp_profile.cloud_upscale_sharpen = self.sld_cloud_sharpen.GetValue()
        tmp_profile.local_brightness = self.studio_tone_controls["brightness"].GetValue()
        tmp_profile.local_contrast = self.studio_tone_controls["contrast"].GetValue()
        tmp_profile.local_saturation = self.studio_tone_controls["saturation"].GetValue()
        tmp_profile.local_shader = self._shader_names[max(0, self.ch_local_shader.GetSelection())]
        tmp_profile.slideshow = self.cb_slideshow.GetValue()
        if tmp_profile.slideshow:
            delay_text = self.tc_sshow_delay.GetLineText(0)
            try:
                # save delay as seconds for compatibility!
                tmp_profile.delay = str(60 * float(delay_text))
            except ValueError:
                # Mid-edit / invalid input; keep raw text so test_save rejects it.
                tmp_profile.delay = delay_text
            # The sort dropdown may have no selection yet (-1); GetString(-1)
            # raises in wx, so only read it when a sort order is chosen.
            sort_sel = self.ch_sshow_sort.GetSelection()
            if sort_sel != wx.NOT_FOUND:
                tmp_profile.sortmode = self.ch_sshow_sort.GetString(sort_sel).lower().replace(" ", "_")
        if self.cb_hotkey.GetValue():
            tmp_profile.hk_binding = self.tc_hotkey_bind.GetLineText(0)

        # span mode
        span_sel = self.radiobox_spanmode.GetSelection()
        if span_sel == 0:
            tmp_profile.spanmode = "single"
        elif span_sel == 1:
            tmp_profile.spanmode = "advanced"
        elif span_sel == 2:
            tmp_profile.spanmode = "multi"

        # manual offsets
        if self.cb_offsets.GetValue():
            offs_strs = []
            for tc in self.tc_list_offsets:
                line = tc.GetLineText(0)
                if line:
                    offs_strs.append(line)
                else:
                    # if offset field is empty, assume user wants no offset
                    offs_strs.append("0,0")
            tmp_profile.manual_offsets = ";".join(offs_strs)
        # perspective
        persp_sel = self.ch_persp.GetSelection()
        if persp_sel != wx.NOT_FOUND:
            tmp_profile.perspective = self.ch_persp.GetString(persp_sel)
        else:
            tmp_profile.perspective = "default"

        # image scaling & position. Zoom/pan only applies to a single fixed
        # image; a slideshow always renders at defaults regardless of slider state.
        if self.cb_slideshow.GetValue():
            tmp_profile.zoom = 1.0
            tmp_profile.align = (0.0, 0.0)
        else:
            tmp_profile.zoom = self.sld_zoom.GetValue() / 100.0
            tmp_profile.align = (self.sld_offx.GetValue() / 100.0, self.sld_offy.GetValue() / 100.0)

        # wallpaper selection (persistent). For single/advanced span the
        # focused list item is the chosen image. If the user didn't pick a new
        # one, preserve any selection already saved in the profile. Skipped for
        # change-tracking, where the background selection is not a profile change.
        if resolve_selection and tmp_profile.spanmode != "multi":
            selected_file = self._get_selected_wallpaper_path()
            if selected_file and os.path.isfile(selected_file):
                tmp_profile.selected = [selected_file]
            else:
                # No new image picked in the list: preserve the selection of the
                # profile currently open in the dialog. Look it up by the original
                # (dropdown) name, NOT tmp_profile.name, which may have just been
                # edited (rename) and not exist on disk yet -- otherwise the
                # selection is lost and the preview/applied image cycles (#158).
                existing = self._loaded_profile_with_selection()
                if existing and existing.selected:
                    tmp_profile.selected = existing.selected

        # span groups
        groups = None
        if self.cb_spangroups.GetValue():
            groups = self.read_spangroups()
            flat_groups = []
            if groups is not None:
                for grp in groups:
                    ids = "".join([str(i) for i in groups[grp]])
                    flat_groups.append(ids)
            tmp_profile.spangroups = ",".join(flat_groups)

        # Paths
        # extract data from path_listctrl
        path_lc_contents = []
        columns = self.path_listctrl.GetColumnCount()
        for idx in range(self.path_listctrl.GetItemCount()):
            item_dat = []
            for col in range(columns):
                item_dat.append(self.path_listctrl.GetItemText(idx, col))
            path_lc_contents.append(item_dat)

        # format paths
        if columns == 1:
            flat_contents = [path for row in path_lc_contents for path in row]
            semicol_sep_paths = ";".join(flat_contents)
            tmp_profile.paths_array.append(semicol_sep_paths)
        else:
            path_lc_contents = sorted(path_lc_contents, key=itemgetter(0))
            paths_dict = {}
            for row in path_lc_contents:
                disp_id, path_item = row
                if disp_id in paths_dict:
                    paths_dict[disp_id].append(path_item)
                else:
                    paths_dict[disp_id] = [path_item]
            for disp_id in paths_dict:
                semicol_sep_paths = ";".join(paths_dict[disp_id])
                tmp_profile.paths_array.append(semicol_sep_paths)

        return tmp_profile, groups

    def onSave(self, event):
        """Saves currently open profile into file. A test method is called to verify data."""
        busy = None
        if event:
            busy = wx.BusyCursor()
        tmp_profile, groups = self._collect_temp_profile(resolve_selection=True)

        # log
        sp_logging.G_LOGGER.info(tmp_profile.name)
        sp_logging.G_LOGGER.info(tmp_profile.spanmode)
        sp_logging.G_LOGGER.info(tmp_profile.slideshow)
        sp_logging.G_LOGGER.info(tmp_profile.delay)
        sp_logging.G_LOGGER.info(tmp_profile.sortmode)
        sp_logging.G_LOGGER.info(tmp_profile.manual_offsets)
        sp_logging.G_LOGGER.info(tmp_profile.hk_binding)
        sp_logging.G_LOGGER.info(tmp_profile.paths_array)

        # test collected data and save if it is valid, otherwise pass
        current_profile_id = self.current_profile_id
        if tmp_profile.test_save(current_profile_id=current_profile_id):
            old_profile_binding = self.loaded_profile.hk_binding if self.loaded_profile is not None else None
            active = self.parent_tray_obj.active_profile
            update_active = (
                current_profile_id is not None
                and active is not None
                and active.profile_id == current_profile_id
                and tmp_profile.name != current_profile_id.value
            )
            try:
                saved_file = save_managed_profile(
                    tmp_profile,
                    current_profile_id=current_profile_id,
                    expected_source_identity=self.expected_source_identity,
                    update_active=update_active,
                )
            except OSError as error:
                sp_logging.G_LOGGER.warning("Could not save profile: %s", error)
                show_message_dialog(str(error), "Error")
                del busy
                return None
            self.parent_tray_obj.reload_profiles(event)
            # The just-saved profile becomes the active one so reopening the
            # dialog shows the saved state (span mode, etc.) instead of a stale
            # in-memory profile from before the edit.
            self.parent_tray_obj.active_profile = self.parent_tray_obj.get_profile_by_name(tmp_profile.name)
            self.update_choiceprofile()
            self.parent_tray_obj.update_hotkey(tmp_profile.name, old_profile_binding, tmp_profile.hk_binding)
            # Re-arm the active profile's slideshow timer so toggling slideshow
            # (or editing the delay) takes effect immediately instead of only
            # after an app restart.
            self.parent_tray_obj.rearm_active_timer()
            self.choice_profiles.SetSelection(self.choice_profiles.FindString(tmp_profile.name))
            # Update wallpaper preview from selected profile. The profile's
            # persistent selection (if any) is what next_wallpaper_files returns.
            saved_profile = open_profile(ProfileId.parse(tmp_profile.name))
            if saved_profile is None:
                show_message_dialog("The saved profile could not be reloaded.", "Error")
                del busy
                return None
            self.current_profile_id = saved_profile.profile_id
            self.expected_source_identity = saved_profile.source_identity
            self.loaded_profile = saved_profile
            if self.show_advanced_settings:
                display_data = self.display_sys.get_disp_list(True)
            else:
                display_data = self.display_sys.get_disp_list(False)
            preview_files = saved_profile.next_wallpaper_files(peek=True)
            self.wpprev_pnl.preview_wallpaper(
                preview_files,
                self.show_advanced_settings,
                self.use_multi_image,
                display_data,
                groups,
            )
            self._studio_refresh_image_card()
            # Saved state becomes the new clean baseline.
            self.list_of_profiles = self.parent_tray_obj.list_of_profiles
            self._set_clean_baseline()
            self._refresh_profile_gallery()
            if hasattr(self, "studio_status"):
                self.studio_status.SetLabel(f"Profile saved: {tmp_profile.name}")
            del busy
            return saved_file
        else:
            sp_logging.G_LOGGER.info("test_save failed.")
            del busy
            return None

    def onCreateNewProfile(self, event):
        """Empties the wallpaper profile config fields."""
        self._loading = True
        self.current_profile_id = None
        self.expected_source_identity = None
        self.loaded_profile = None
        self.choice_profiles.SetSelection(self.choice_profiles.FindString("Create a new profile"))

        self.tc_name.ChangeValue("")

        self.refresh_path_listctrl(False, migrate_paths=False)

        self.radiobox_spanmode.SetSelection(0)
        self.onSpanRadio(None)

        self.cb_slideshow.SetValue(False)
        self.tc_sshow_delay.ChangeValue("")
        self.ch_sshow_sort.SetSelection(wx.NOT_FOUND)
        self.onCheckboxSlideshow(None)

        self.cb_offsets.SetValue(False)
        for tc in self.tc_list_offsets:
            tc.SetValue("0,0")
        self.onCheckboxOffsets(None)

        self.cb_hotkey.SetValue(False)
        self.tc_hotkey_bind.ChangeValue("")
        self.onCheckboxHotkey(None)

        self.reset_zoom_offset()
        self.cb_cloud_upscale.SetValue(False)
        self.ch_cloud_scale.SetSelection(0)
        self.sld_cloud_sharpen.SetValue(0)
        self.st_cloud_sharpen.SetLabel("0")
        self.wpprev_pnl.sharpen = 0
        self.wpprev_pnl.tone = (0, 0, 0)
        for key in ("brightness", "contrast", "saturation"):
            self.studio_tone_controls[key].SetValue(0)
            self.studio_tone_labels[key].SetLabel("0")
        self._refresh_local_shader_options("")
        self._sync_cloud_quality_controls()
        self.studio_compare.SetValue(False)
        self.studio_split_slider.SetValue(50)
        self.studio_split_slider.Enable(False)
        self.studio_compare_state.Hide()
        self.wpprev_pnl.compare_original = False
        self.wpprev_pnl.compare_fraction = 0.5
        self.studio_monitor_choice.SetSelection(0)
        self.wpprev_pnl.focus_monitor = 0

        # refresh wallpaper preview back to black previews
        self.wpprev_pnl.current_preview_images = []
        self.wpprev_pnl.draw_displays()
        self._studio_refresh_image_card()
        self.Refresh()
        self.Update()

        # A fresh (empty) profile starts clean.
        self._loading = False
        self._set_clean_baseline()

    def onDeleteProfile(self, event):
        """Deletes the currently selected profile after getting confirmation."""
        selection = self.choice_profiles.GetSelection()
        selected_name = self.choice_profiles.GetString(selection) if selection != wx.NOT_FOUND else ""
        try:
            selected_id = ProfileId.parse(selected_name)
        except ProfileIdError:
            selected_id = None
        profile = managed_profile_for_selection(self.list_of_profiles, selected_id) if selected_id is not None else None
        if profile is None:
            msg = "Selected profile is not saved."
            show_message_dialog(msg, "Error")
            return
        # Open confirmation dialog
        dlg = wx.MessageDialog(
            None,
            f"Do you want to delete profile: {selected_name}?",
            "Confirm Delete",
            wx.YES_NO | wx.ICON_QUESTION,
        )
        result = dlg.ShowModal()
        if result == wx.ID_YES:
            try:
                delete_managed_profile(profile)
            except (OSError, ValueError) as error:
                show_message_dialog(str(error), "Error")
                return
            # Refresh tray state so the deleted profile disappears from the
            # dropdown and is no longer active; otherwise it lingers in memory
            # and a later save can recreate it.
            if (
                self.parent_tray_obj.active_profile is not None
                and self.parent_tray_obj.active_profile.profile_id == profile.profile_id
            ):
                self.parent_tray_obj.active_profile = None
            self.parent_tray_obj.reload_profiles(event)
            self.update_choiceprofile()
            self.onCreateNewProfile(None)
        else:
            pass

    def onAlignTest(self, event):
        """Align test, takes alignment settings from open profile and sets a test image wp."""
        # Use the settings currently written out in the fields!
        testimage = [os.path.join(RESOURCES_PATH, "test.png")]
        if not os.path.isfile(testimage[0]):
            msg = f"Test image not found in {testimage}."
            show_message_dialog(msg, "Error")

        offsets = []
        for off_tc in self.tc_list_offsets:
            off = off_tc.GetLineText(0).split(",")
            try:
                offsets.append([int(off[0]), int(off[1])])
            except IndexError, ValueError:
                show_message_dialog(
                    f"Offsets must be integer pairs separated with a comma!\nProblematic offset is {off}"
                )
                return -1
        flat_offsets = []
        for off in offsets:
            for pix in off:
                flat_offsets.append(pix)

        persp_sel = self.ch_persp.GetSelection()
        if persp_sel != wx.NOT_FOUND:
            perspective = self.ch_persp.GetString(persp_sel)
        else:
            perspective = "default"

        busy = wx.BusyCursor()

        # Use the simplified CLI profile class
        wpproc.refresh_display_data()
        profile = CLIProfileData(
            testimage, advanced=True, perspective=perspective, spangroups=None, offsets=flat_offsets
        )
        thrd = change_wallpaper_job(profile, force=True)
        # Pump the event loop while rendering so the GUI stays responsive
        # instead of freezing.
        while thrd is not None and thrd.is_alive():
            wx.YieldIfNeeded()
            time.sleep(0.05)
        del busy

    def onPerspectives(self, event):
        """Open perspective configuration dialog."""
        dlg = PerspectiveConfig(self)
        dlg.ShowModal()
        # if res == wx.ID_OK:
        # pass
        dlg.Destroy()
        # Update perspective profile choices
        open_item = self.choice_profiles.GetSelection()
        if self.choice_profiles.GetString(open_item) == "Create a new profile" or not self.choice_profiles.GetString(
            open_item
        ):
            old_persp_str = "default"
        else:
            old_persp_str = self.list_of_profiles[open_item].perspective
        persp_choices = ["default", *list(self.display_sys.perspective_dict.keys()), "disabled"]
        self.ch_persp.SetItems(persp_choices)
        if old_persp_str in persp_choices:
            self.ch_persp.SetSelection(self.ch_persp.FindString(old_persp_str))
        else:
            # The profile referenced a perspective that no longer exists; fall
            # back to default and surface it as an unsaved change rather than
            # silently writing to disk (Save is the only thing that persists).
            self.ch_persp.SetSelection(0)
            self._update_dirty_state()

    def onHelp(self, event):
        """Open help dialog."""
        HelpFrame(self)

    def onHelpHotkey(self, evt):
        """Popup hotkey help."""
        text = (
            "Bind a hotkey to start this profile. Choose max 3\n"
            "modfiers out of: control, alt, shift, super(=win).\n"
            "Example: control+super+x"
        )
        pop = HelpPopup(self, text)
        btn = evt.GetEventObject()
        pos = btn.ClientToScreen((0, 0))
        sz = btn.GetSize()
        pop.Position(pos, wx.Size(0, sz[1]))
        pop.Popup()

    def onHelpBezels(self, evt):
        """Popup bezel config help."""
        text = (
            "Configure your bezels in the wallpaper preview\n"
            "panel with the buttons placed at the right and\n"
            "bottom edges of displays. Bezels between displays\n"
            "are meaningful. Adjacent bezel pair thicknesses are\n"
            "grouped together with the gap in between to a single\n"
            "number."
        )
        pop = HelpPopup(self, text)
        btn = evt.GetEventObject()
        pos = btn.ClientToScreen((0, 0))
        sz = btn.GetSize()
        pop.Position(pos, wx.Size(0, sz[1]))
        pop.Popup()

    def onHelpSpanGroups(self, evt):
        """Popup span group config help."""
        text = (
            "You can span wallpapers on groups of displays.\n"
            "To configure this, select a group number for\n"
            "each display. By default each display belongs\n"
            "to the first group, group 0.\n"
            "Once you have selected your groups, add at least\n"
            "one wallpaper source for each group."
        )
        pop = HelpPopup(self, text)
        btn = evt.GetEventObject()
        pos = btn.ClientToScreen((0, 0))
        sz = btn.GetSize()
        pop.Position(pos, wx.Size(0, sz[1]))
        pop.Popup()


class WallpaperPreviewPanel(wx.Panel):
    """
    Wallpaper & monitor preview panel.

    Previews wallpaper settings as applied to the input image.
    In the advanced mode allows the user to enter their monitor
    setup, which will then be saved into a file as a monitor
    configuration. Method looks up saved setups to see if one
    exists that matches the given resolutions, offsets and sizes.
    """

    def __init__(self, parent, display_sys, image_list=None, use_ppi_px=False, use_multi_image=False):
        self.preview_size = (1080, 265)
        wx.Panel.__init__(self, parent, size=wx.Size(*self.preview_size))
        self.frame = parent

        # Buttons
        self.config_mode = False
        self.bezel_conifg_mode = False
        self.create_buttons(use_ppi_px)

        # Colour definitions
        self.clr_prw_mntr = wx.Colour(0, 0, 0, alpha=wx.ALPHA_OPAQUE)
        self.clr_prw_bkg = wx.Colour(30, 30, 30, alpha=wx.ALPHA_OPAQUE)
        self.SetBackgroundColour(self.clr_prw_bkg)

        # Display data and sizes
        self.display_sys = display_sys
        self.display_data = self.display_sys.get_disp_list()
        self.dtop_canvas_px = self.get_canvas(self.display_data)
        self.dtop_canvas_relsz, self.dtop_canvas_pos, scaling_fac = self.fit_canvas_wrkarea(self.dtop_canvas_px)
        self.display_rel_sizes = self.displays_on_canvas(self.display_data, self.dtop_canvas_pos, scaling_fac)

        # bitmaps to be shown
        self.use_multi_image = use_multi_image
        self.current_preview_images = []
        self.preview_img_list = []
        self.bmp_list = []

        # Image zoom, positioning and local sharpening are previewed using
        # the original image, without triggering any cloud inference.
        self.zoom = 1.0
        self.offset = (0.0, 0.0)
        self.sharpen = 0
        self.tone = (0, 0, 0)
        self.compare_original = False
        self.compare_fraction = 0.5
        self.focus_monitor = 0
        self._last_use_ppi = use_ppi_px
        self._last_use_multi = use_multi_image
        self._last_spangroups = None
        self._background_drag = None
        self._comparison_drag = None
        self._comparison_crops = []
        self.desktop_layout_enabled = False
        self._raw_preview_bmps = []

        # Draw preview
        self.draw_displays()
        # Separate image surface showing precisely where the virtual desktop
        # monitors begin/end. The existing bitmaps remain for physical editing.
        self.desktop_preview = wx.StaticBitmap(self, bitmap=wx.Bitmap(1, 1))
        self.desktop_preview.Hide()

        # Create bezel buttons for displays in preview
        self.bez_buttons = []
        self.create_bezel_buttons()

        self.draggable_shapes = []
        self.positions_dragged = False
        self.Bind(wx.EVT_PAINT, self.OnPaint)
        self.Bind(wx.EVT_MOUSE_CAPTURE_LOST, self._on_background_capture_lost)
        self.bind_background_drag()
        self.bind_wallpaper_bitmap_drag()
        self.desktop_preview.Bind(wx.EVT_LEFT_DOWN, self._on_background_down)
        self.desktop_preview.Bind(wx.EVT_LEFT_UP, self._on_background_up)
        self.desktop_preview.Bind(wx.EVT_MOTION, self._on_background_motion)
        self.SetToolTip("Drag the image to reposition it; zoom in for more movement.")

    def preview_area_ready(self):
        """Only create preview bitmaps after wx has a usable panel size."""
        return usable_preview_area(self.GetClientSize())

    #
    # UI drawing methods
    #
    def draw_displays(self, use_ppi_px=False, use_multi_image=False):
        # draw canvas
        bmp_canv = wx.Bitmap.FromRGBA(
            self.dtop_canvas_relsz[0], self.dtop_canvas_relsz[1], red=0, green=0, blue=0, alpha=255
        )
        if not self.preview_img_list:
            # preview StaticBitmaps don't exist yet
            self.bmp_list.append(bmp_canv)
            self.st_bmp_canvas = wx.StaticBitmap(self, wx.ID_ANY, wx.BitmapBundle(bmp_canv))
            self.st_bmp_canvas.SetPosition(wx.Point(self.dtop_canvas_pos))
            self.st_bmp_canvas.Hide()

            # draw monitor previews
            for disp in self.display_rel_sizes:
                size = disp[0]
                offs = disp[1]
                bmp = wx.Bitmap.FromRGBA(size[0], size[1], red=0, green=0, blue=0, alpha=255)
                self.bmp_list.append(bmp)
                st_bmp = wx.StaticBitmap(self, wx.ID_ANY, wx.BitmapBundle(bmp))
                st_bmp.Hide()
                # st_bmp.SetScaleMode(wx.Scale_AspectFill)  # New in wxpython 4.1
                st_bmp.SetPosition(offs)
                self.preview_img_list.append(st_bmp)
        else:
            # previews exist and should be blanked
            self.current_preview_images = []  # drop chached image list

            self.st_bmp_canvas.SetBitmap(wx.BitmapBundle(bmp_canv))
            self.st_bmp_canvas.SetPosition(wx.Point(self.dtop_canvas_pos))
            # self.st_bmp_canvas.Hide()

            # blank monitor previews
            for disp, st_bmp in zip(self.display_rel_sizes, self.preview_img_list):
                size = disp[0]
                offs = disp[1]
                bmp = wx.Bitmap.FromRGBA(size[0], size[1], red=0, green=0, blue=0, alpha=255)
                st_bmp.SetBitmap(bmp)
                st_bmp.SetPosition(offs)
                # st_bmp.Hide()
        self.draw_monitor_numbers(use_ppi_px)
        self.Refresh()

    def resize_displays(self, use_ppi_px):
        if use_ppi_px:
            for disp, img_sz, bez_szs, st_bmp in zip(
                self.display_rel_sizes, self.img_rel_sizes, self.bz_rel_sizes, self.preview_img_list
            ):
                size, offs = disp
                bmp = wx.Bitmap.FromRGBA(img_sz[0], img_sz[1], red=0, green=0, blue=0, alpha=255)
                bmp_w_bez = self.bezels_to_bitmap(bmp, size, bez_szs)
                st_bmp.SetSize(size)
                st_bmp.SetPosition(offs)
                st_bmp.SetBitmap(bmp_w_bez)
        else:
            for disp, st_bmp in zip(self.display_rel_sizes, self.preview_img_list):
                size = disp[0]
                offs = disp[1]
                bmp = wx.Bitmap.FromRGBA(size[0], size[1], red=0, green=0, blue=0, alpha=255)
                st_bmp.SetBitmap(bmp)
                st_bmp.SetPosition(offs)
                st_bmp.SetSize(size)
        self.draw_monitor_numbers(use_ppi_px)

    def draw_monitor_numbers(self, use_ppi_px):
        """Compact, readable monitor badges rather than oversized grey indices."""
        font = wx.Font(10, wx.FONTFAMILY_SWISS, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD)
        for index, st_bmp in enumerate(self.preview_img_list):
            bitmap = st_bmp.GetBitmap()
            if not bitmap.IsOk():
                continue
            dc = wx.MemoryDC(bitmap)
            title = f"Monitor {index + 1}"
            dc.SetFont(font)
            width, _height = dc.GetTextExtent(title)
            dc.SetPen(wx.Pen(wx.Colour(82, 107, 143)))
            dc.SetBrush(wx.Brush(wx.Colour(24, 34, 47)))
            dc.DrawRoundedRectangle(8, 8, width + 22, 27, 5)
            dc.SetTextForeground(wx.Colour(244, 247, 252))
            dc.DrawText(title, 18, 13)
            dc.SelectObject(wx.NullBitmap)
            st_bmp.SetBitmap(bitmap)
        if use_ppi_px:
            self.draw_monitor_sizes()

    def draw_monitor_sizes(self):
        """Show actual monitor resolution in small readable text."""
        font = wx.Font(9, wx.FONTFAMILY_SWISS, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL)
        for st_bmp, disp in zip(self.preview_img_list, self.display_sys.disp_list):
            bitmap = st_bmp.GetBitmap()
            if not bitmap.IsOk():
                continue
            dc = wx.MemoryDC(bitmap)
            width, _height = bitmap.GetSize()
            label = f"{disp.resolution[0]} x {disp.resolution[1]}"
            dc.SetFont(font)
            text_width, text_height = dc.GetTextExtent(label)
            x = max(8, width - text_width - 17)
            dc.SetPen(wx.TRANSPARENT_PEN)
            dc.SetBrush(wx.Brush(wx.Colour(24, 34, 47)))
            dc.DrawRoundedRectangle(x - 6, 8, text_width + 12, text_height + 10, 4)
            dc.SetTextForeground(wx.Colour(231, 239, 251))
            dc.DrawText(label, x, 13)
            dc.SelectObject(wx.NullBitmap)
            st_bmp.SetBitmap(bitmap)

    def refresh_preview(self, use_ppi_px=False, force_refresh=False):
        if not self.preview_area_ready():
            return
        if force_refresh or (not self.config_mode or not self.bezel_conifg_mode):
            self.dtop_canvas_px = self.get_canvas(self.display_data, use_ppi_px)
            self.dtop_canvas_relsz, self.dtop_canvas_pos, scaling_fac = self.fit_canvas_wrkarea(self.dtop_canvas_px)
            self.st_bmp_canvas.SetPosition(wx.Point(self.dtop_canvas_pos))
            self.st_bmp_canvas.SetSize(wx.Size(*self.dtop_canvas_relsz))

            if use_ppi_px:
                (self.display_rel_sizes, self.img_rel_sizes, self.bz_rel_sizes) = self.displays_on_canvas(
                    self.display_data, self.dtop_canvas_pos, scaling_fac, use_ppi_px
                )
            else:
                self.display_rel_sizes = self.displays_on_canvas(self.display_data, self.dtop_canvas_pos, scaling_fac)
            for disp, st_bmp in zip(self.display_rel_sizes, self.preview_img_list):
                size = disp[0]
                offs = disp[1]
                st_bmp.SetPosition(offs)
                st_bmp.SetSize(size)
        # if self.bezel_conifg_mode:
        # pass
        # self.st_bmp_canvas.Hide()

        self.move_buttons()
        self.move_bezel_buttons()

    def full_refresh_preview(self, is_resized, use_ppi_px, use_multi_image, spangroups=None):
        self.use_multi_image = use_multi_image
        if not self.preview_area_ready():
            return
        if is_resized and not self.config_mode:
            dtop_canvas_relsz, dtop_canvas_pos, scaling_fac = self.fit_canvas_wrkarea(self.dtop_canvas_px)
            # if (self.current_preview_images and dtop_canvas_relsz is not self.dtop_canvas_relsz):
            if self.current_preview_images:
                self.preview_wallpaper(self.current_preview_images, use_ppi_px, use_multi_image, spangroups=spangroups)
                self.move_bezel_buttons()
                # self.st_bmp_canvas.Hide()
            else:
                self.refresh_preview(use_ppi_px)
                self.resize_displays(use_ppi_px)
                self.move_bezel_buttons()
                self.Refresh()
                # self.st_bmp_canvas.Hide()

    def preview_wallpaper(
        self,
        image_list,
        use_ppi_px=False,
        use_multi_image=False,
        display_data=None,
        spangroups=None,
    ):
        self.use_multi_image = use_multi_image
        if display_data:
            self.display_data = display_data
        self._last_use_ppi = use_ppi_px
        self._last_use_multi = use_multi_image
        self._last_spangroups = spangroups
        self.current_preview_images = list(image_list or ())
        if not self.preview_area_ready():
            return
        self.refresh_preview(use_ppi_px)
        image_list = self.current_preview_images
        if not image_list:
            self._comparison_crops = []
            self.desktop_preview.Hide()
            self.resize_displays(use_ppi_px)
            self.Refresh()
            return

        def safe_sub_bitmap(bm, rect):
            if rect.GetBottom() >= bm.GetHeight():
                rect.SetBottom(bm.GetHeight() - 1)
            if rect.GetRight() >= bm.GetWidth():
                rect.SetRight(bm.GetWidth() - 1)
            return bm.GetSubBitmap(rect)

        self._comparison_crops = [None] * len(self.preview_img_list)
        if use_multi_image:
            while len(image_list) < len(self.preview_img_list):
                image_list.append(image_list[0])
            for index, (img_nm, st_bmp) in enumerate(zip(image_list, self.preview_img_list)):
                prev_sz = st_bmp.GetSize()
                st_bmp.SetBitmap(self.resize_and_bitmap(img_nm, prev_sz))
                self._comparison_crops[index] = (0, prev_sz[0], prev_sz[0])
        elif use_ppi_px and spangroups:
            self.use_multi_image = True  # disables canvas drawing
            # for each group of displays, run span wallpaper preview
            for grp_id, img_nm in zip(spangroups, image_list):
                grp = spangroups[grp_id]
                display_rel_sizes = [self.display_rel_sizes[i] for i in grp]
                img_rel_sizes = [self.img_rel_sizes[i] for i in grp]
                bz_rel_sizes = [self.bz_rel_sizes[i] for i in grp]
                preview_img_list = [self.preview_img_list[i] for i in grp]

                canv_sz, canvas_pos = self.canvas_display_group(display_rel_sizes, (0, 0))
                bmp_clr, bmp_bw = self.resize_and_bitmap(img_nm, canv_sz, True)
                for index, disp, img_sz, bez_szs, st_bmp in zip(
                    grp, display_rel_sizes, img_rel_sizes, bz_rel_sizes, preview_img_list
                ):
                    sz = disp[0]
                    pos = (disp[1][0] - canvas_pos[0], disp[1][1] - canvas_pos[1])
                    crop = safe_sub_bitmap(bmp_clr, wx.Rect(pos, img_sz))
                    crop_w_bez = self.bezels_to_bitmap(crop, sz, bez_szs)
                    st_bmp.SetBitmap(crop_w_bez)
                    self._comparison_crops[index] = (pos[0], img_sz[0], canv_sz[0])
        elif use_ppi_px and not spangroups:
            img = image_list[0]
            # set canvas to fit with keeping aspect the image, with dim/blur
            # and crop pieces to show on monitor previews unaltered.
            # With use_ppi_px any provided bezels will be drawn.
            canv_sz = self.st_bmp_canvas.GetSize()
            bmp_clr, bmp_bw = self.resize_and_bitmap(img, canv_sz, True)
            self.st_bmp_canvas.SetBitmap(wx.BitmapBundle(bmp_bw))
            # self.st_bmp_canvas.Show()

            canvas_pos = self.dtop_canvas_pos
            for index, (disp, img_sz, bez_szs, st_bmp) in enumerate(
                zip(self.display_rel_sizes, self.img_rel_sizes, self.bz_rel_sizes, self.preview_img_list)
            ):
                sz = disp[0]
                pos = (disp[1][0] - canvas_pos[0], disp[1][1] - canvas_pos[1])
                crop = safe_sub_bitmap(bmp_clr, wx.Rect(pos, img_sz))
                crop_w_bez = self.bezels_to_bitmap(crop, sz, bez_szs)
                st_bmp.SetBitmap(crop_w_bez)
                self._comparison_crops[index] = (pos[0], img_sz[0], canv_sz[0])
                # st_bmp.Show()
        else:
            img = image_list[0]
            # set canvas to fit with keeping aspect the image, with dim/blur
            # and crop pieces to show on monitor previews unaltered.
            canv_sz = self.st_bmp_canvas.GetSize()
            bmp_clr, bmp_bw = self.resize_and_bitmap(img, canv_sz, True)
            self.st_bmp_canvas.SetBitmap(wx.BitmapBundle(bmp_bw))
            # self.st_bmp_canvas.Show()

            canvas_pos = self.dtop_canvas_pos
            for index, (disp, st_bmp) in enumerate(zip(self.display_rel_sizes, self.preview_img_list)):
                sz = disp[0]
                pos = (disp[1][0] - canvas_pos[0], disp[1][1] - canvas_pos[1])
                crop = safe_sub_bitmap(bmp_clr, wx.Rect(pos, sz))
                st_bmp.SetBitmap(crop)
                self._comparison_crops[index] = (pos[0], sz[0], canv_sz[0])
                # st_bmp.Show()
        # Preserve the unlabelled per-monitor images. The virtual desktop
        # preview repositions these same crops at actual OS monitor offsets.
        self._raw_preview_bmps = [wx.Bitmap(bitmap.GetBitmap().ConvertToImage()) for bitmap in self.preview_img_list]
        if self.compare_original:
            self._paint_comparison_handles()
        for index, bitmap in enumerate(self.preview_img_list):
            bitmap.Show(self.focus_monitor == 0 or index == self.focus_monitor - 1)
        self.draw_monitor_numbers(use_ppi_px)
        self._update_desktop_preview()
        self.Refresh()

    def _comparison_regions(self, *, desktop=False):
        """Map each displayed monitor back to its original split canvas."""
        if desktop:
            try:
                rectangles = self._desktop_preview_rectangles()[2]
            except ValueError:
                return []
        else:
            rectangles = []
            for bitmap in self.preview_img_list:
                position = bitmap.GetPosition()
                size = bitmap.GetSize()
                rectangles.append((position.x, position.y, size.width, size.height))
        regions = []
        for index, (left, top, width, height) in enumerate(rectangles):
            if index >= len(self._comparison_crops):
                break
            crop = self._comparison_crops[index]
            if crop is None or (self.focus_monitor and index != self.focus_monitor - 1):
                continue
            crop_left, crop_width, canvas_width = crop
            # PPI previews can include a bezel area after the image pixels.
            bitmap_width = self.preview_img_list[index].GetSize()[0]
            if desktop and bitmap_width > 0:
                width = width * crop_width / bitmap_width
            else:
                width = min(width, crop_width)
            if width > 0 and height > 0:
                regions.append((left, top, width, height, crop_left, crop_width, canvas_width))
        return regions

    @staticmethod
    def _draw_comparison_handle(dc, x, y):
        """Paint a small grabbable blue handle over the preview-only divider."""
        center_x, center_y = round(x), round(y)
        dc.SetPen(wx.Pen(wx.Colour(96, 190, 255), 2))
        dc.SetBrush(wx.Brush(wx.Colour(23, 48, 78)))
        dc.DrawCircle(center_x, center_y, 14)
        dc.SetPen(wx.Pen(wx.Colour(232, 247, 255), 2))
        for direction in (-1, 1):
            dc.DrawLine(center_x + direction * 5, center_y - 5, center_x + direction * 9, center_y)
            dc.DrawLine(center_x + direction * 9, center_y, center_x + direction * 5, center_y + 5)

    def _paint_comparison_handles(self):
        """Overlay grips on the displayed bitmap, never on saved wallpapers."""
        for index, bitmap in enumerate(self.preview_img_list):
            if index >= len(self._comparison_crops) or self._comparison_crops[index] is None:
                continue
            crop_left, crop_width, canvas_width = self._comparison_crops[index]
            divider = self.compare_fraction * canvas_width - crop_left
            if not 0 <= divider <= crop_width:
                continue
            image = bitmap.GetBitmap()
            dc = wx.MemoryDC(image)
            try:
                self._draw_comparison_handle(dc, divider, image.GetHeight() / 2)
            finally:
                dc.SelectObject(wx.NullBitmap)
            bitmap.SetBitmap(image)

    def set_desktop_layout(self, enabled):
        """Switch between final OS geometry and the physical calibration view."""
        self.desktop_layout_enabled = bool(enabled)
        self._update_desktop_preview()

    def _desktop_preview_rectangles(self):
        """Project real digital monitor offsets into this preview's viewport."""
        displays = [(dsp.resolution, dsp.digital_offset) for dsp in self.display_sys.disp_list]
        return desktop_preview_layout(displays, self.GetClientSize())

    def _update_desktop_preview(self):
        """Recompose only cached local preview crops; never invoke Cloud AI."""
        if not self.desktop_layout_enabled or self.config_mode or self.bezel_conifg_mode:
            self.desktop_preview.Hide()
            for index, bitmap in enumerate(self.preview_img_list):
                bitmap.Show(self.focus_monitor == 0 or index == self.focus_monitor - 1)
            return
        if not self._raw_preview_bmps or not self.preview_area_ready():
            self.desktop_preview.Hide()
            return
        try:
            canvas_size, canvas_pos, rectangles = self._desktop_preview_rectangles()
        except ValueError:
            return
        output = wx.Bitmap.FromRGBA(canvas_size[0], canvas_size[1], red=25, green=30, blue=39, alpha=255)
        dc = wx.MemoryDC(output)
        try:
            for index, ((x, y, width, height), picture) in enumerate(zip(rectangles, self._raw_preview_bmps)):
                if self.focus_monitor != 0 and index != self.focus_monitor - 1:
                    continue
                resized = picture.ConvertToImage().Scale(width, height, wx.IMAGE_QUALITY_HIGH).ConvertToBitmap()
                dc.DrawBitmap(resized, x - canvas_pos[0], y - canvas_pos[1])
                dc.SetBrush(wx.TRANSPARENT_BRUSH)
                dc.SetPen(wx.Pen(wx.Colour(86, 145, 217), 1))
                dc.DrawRectangle(x - canvas_pos[0], y - canvas_pos[1], width, height)
                dc.SetFont(wx.Font(9, wx.FONTFAMILY_SWISS, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
                label = f"Monitor {index + 1}"
                label_w, label_h = dc.GetTextExtent(label)
                dc.SetPen(wx.TRANSPARENT_PEN)
                dc.SetBrush(wx.Brush(wx.Colour(26, 40, 61)))
                dc.DrawRoundedRectangle(x - canvas_pos[0] + 6, y - canvas_pos[1] + 7, label_w + 15, label_h + 9, 4)
                dc.SetTextForeground(wx.Colour(245, 249, 255))
                dc.DrawText(label, x - canvas_pos[0] + 13, y - canvas_pos[1] + 11)
            if self.compare_original:
                for left, top, width, height, crop_left, crop_width, canvas_width in self._comparison_regions(
                    desktop=True
                ):
                    divider = left + (self.compare_fraction * canvas_width - crop_left) * width / crop_width
                    if left <= divider <= left + width:
                        self._draw_comparison_handle(dc, divider - canvas_pos[0], top + height / 2 - canvas_pos[1])
        finally:
            dc.SelectObject(wx.NullBitmap)
        self.desktop_preview.SetBitmap(output)
        self.desktop_preview.SetPosition(wx.Point(*canvas_pos))
        self.desktop_preview.Show()
        self.desktop_preview.Raise()
        # Configuration/help controls must remain reachable above the image.
        for button in (
            self.button_config,
            self.button_save,
            self.button_reset,
            self.button_cancel,
            self.button_entry,
            self.button_help,
        ):
            button.Raise()
        for bitmap in self.preview_img_list:
            bitmap.Hide()

    def update_zoom_offset(self, zoom, offset):
        """Re-render the current preview with new zoom & offset values."""
        self.zoom = zoom
        self.offset = offset
        if not self.current_preview_images:
            return
        self.preview_wallpaper(
            list(self.current_preview_images),
            self._last_use_ppi,
            self._last_use_multi,
            self.display_data,
            self._last_spangroups,
        )

    @overload
    def resize_and_bitmap(self, fname, size, enhance_color: Literal[False] = False) -> wx.Bitmap: ...
    @overload
    def resize_and_bitmap(self, fname, size, enhance_color: Literal[True]) -> tuple[wx.Bitmap, wx.Bitmap]: ...

    def resize_and_bitmap(self, fname, size, enhance_color=False):
        """Take filename of an image and resize and crop it to size."""
        try:
            with Image.open(fname) as source:
                oriented = ImageOps.exif_transpose(source)
                prepared = locally_sharpen(oriented, self.sharpen)
                prepared = apply_local_adjustments(
                    prepared, brightness=self.tone[0], contrast=self.tone[1], saturation=self.tone[2]
                )
                pil = resize_to_fill(prepared, size, quality="fast", zoom=self.zoom, offset=self.offset)
                if self.compare_original:
                    has_adjustments = bool(self.sharpen or any(self.tone))
                    plain = (
                        resize_to_fill(oriented, size, quality="fast", zoom=self.zoom, offset=self.offset)
                        if has_adjustments
                        else pil
                    )
                    pil = split_local_preview(plain, pil, self.compare_fraction, has_adjustments=has_adjustments)
        except OSError, UnidentifiedImageError:
            msg = (
                f"Opening image '{fname}' failed with PIL.UnidentifiedImageError."
                "It could be corrupted or is of foreign type."
            )
            sp_logging.G_LOGGER.info(msg)
            # show_message_dialog(msg)
            black_bmp = wx.Bitmap.FromRGBA(size[0], size[1], red=0, green=0, blue=0, alpha=255)
            if enhance_color:
                return (black_bmp, black_bmp)
            return black_bmp
        img = wx.Image(pil.size[0], pil.size[1])
        img.SetData(pil.convert("RGB").tobytes())
        if enhance_color:
            converter = ImageEnhance.Color(pil)
            pilenh_bw = converter.enhance(0.25)
            brightns = ImageEnhance.Brightness(pilenh_bw)
            pilenh = brightns.enhance(0.45)
            imgenh = wx.Image(pil.size[0], pil.size[1])
            imgenh.SetData(pilenh.convert("RGB").tobytes())
            return (img.ConvertToBitmap(), imgenh.ConvertToBitmap())
        return img.ConvertToBitmap()

    def bezels_to_bitmap(self, bmp, disp_sz, bez_rects):
        """Add bezel rectangles ( right_bez , bottom_bez ) to given bitmap."""
        # sp_logging.G_LOGGER.info("bezels_to_bitmap: bez_rects: %s", bez_rects)
        right_bez, bottom_bez = bez_rects
        if not has_positive_area(right_bez) and not has_positive_area(bottom_bez):
            return bmp
        # bmp into wx.Image and new output
        img = bmp.ConvertToImage()
        img_sz = img.GetSize()
        img_out = wx.Image(disp_sz[0], disp_sz[1])
        img_out.Paste(img, 0, 0)

        # Subpixel bezels can round to (0, N) or (N, 0). wx cannot
        # construct bitmaps with either dimension equal to zero.
        # Add bezels sequentially to the Image.
        if has_positive_area(bottom_bez):
            b_bez_bmp = wx.Bitmap.FromRGBA(bottom_bez[0], bottom_bez[1], red=5, green=5, blue=5, alpha=100)
            b_bez_img = b_bez_bmp.ConvertToImage()
            img_out.Paste(b_bez_img, 0, img_sz[1])

        # right bez: is longer if bottom bez is present
        if has_positive_area(right_bez):
            r_bez_bmp = wx.Bitmap.FromRGBA(right_bez[0], right_bez[1], red=5, green=5, blue=5, alpha=100)
            r_bez_img = r_bez_bmp.ConvertToImage()
            img_out.Paste(r_bez_img, img_sz[0], 0)

        # Convert Image back to wx.Bitmap
        return img_out.ConvertToBitmap()

    def update_display_data(self, display_data, use_ppi_px, use_multi_image, spangroups=None):
        self.display_data = display_data
        self.refresh_preview()
        self.full_refresh_preview(True, use_ppi_px, use_multi_image, spangroups=spangroups)

    #
    # Data analysis methods
    #
    def get_canvas(self, disp_data, use_ppi_px=False):
        """Returns a size tuple for the desktop are in pixels or millimeters."""
        if use_ppi_px:
            rightmost_edge = max(
                [disp.resolution[0] + disp.digital_offset[0] + disp.ppi_norm_bezels[0] for disp in disp_data]
            )
            bottommost_edge = max(
                [disp.resolution[1] + disp.digital_offset[1] + disp.ppi_norm_bezels[1] for disp in disp_data]
            )
        else:
            rightmost_edge = max([disp.resolution[0] + disp.digital_offset[0] for disp in disp_data])
            bottommost_edge = max([disp.resolution[1] + disp.digital_offset[1] for disp in disp_data])
        return (rightmost_edge, bottommost_edge)

    def fit_canvas_wrkarea(self, canvas_px):
        """Fit the desktop canvas into the usable preview panel area.

        wx can report transient zero-sized windows during layout and when
        minimized. Retain the last usable size instead of dividing by zero
        or allocating zero-width/height preview bitmaps.
        """
        work_sz = self.GetClientSize()
        if usable_preview_area(work_sz):
            self._last_valid_work_size = (work_sz[0], work_sz[1])
        else:
            work_sz = getattr(self, "_last_valid_work_size", self.preview_size)
        return fit_preview_canvas(canvas_px, work_sz)

    def displays_on_canvas(self, disp_data, canvas_pos, scaling_fac, use_ppi_px=False):
        """Return sizes and positions of displays in disp_data on the working area.

        if use_ppi_px == True, returned display sizes contain bezels and lists of
        image sizes and bezel rectangles are returned separately.
        bz_szs is a list of size tuples pairs, each in pair for each possible bezel.
        """
        if use_ppi_px:
            display_szs_pos = []
            image_szs = []
            bz_szs = []
            for disp in disp_data:
                res = disp.resolution
                doff = disp.digital_offset
                off = canvas_pos
                bez = disp.ppi_norm_bezels
                display_szs_pos.append(
                    (
                        # tuple 1: size = res + bez
                        (
                            max(1, round(scaling_fac * (res[0] + bez[0]))),
                            max(1, round(scaling_fac * (res[1] + bez[1]))),
                        ),
                        # tuple 2: pos
                        (
                            round(scaling_fac * doff[0]) + off[0],
                            round(scaling_fac * doff[1]) + off[1],
                        ),
                    )
                )
                image_szs.append((max(1, round(scaling_fac * res[0])), max(1, round(scaling_fac * res[1]))))
                if bez[0] != 0:
                    right_bez = (
                        round(scaling_fac * bez[0]),
                        round(scaling_fac * (res[1] + bez[1])),
                    )
                else:
                    right_bez = (0, 0)
                if bez[1] != 0:
                    bottom_bez = (round(scaling_fac * res[0]), round(scaling_fac * bez[1]))
                else:
                    bottom_bez = (0, 0)
                bz_szs.append((right_bez, bottom_bez))
            return [display_szs_pos, image_szs, bz_szs]
        else:
            display_szs_pos = []
            for disp in disp_data:
                doff = disp.digital_offset
                off = canvas_pos
                display_szs_pos.append(
                    (
                        tuple(max(1, round(px * scaling_fac)) for px in disp.resolution),
                        (
                            round(doff[0] * scaling_fac) + off[0],
                            round(doff[1] * scaling_fac) + off[1],
                        ),
                    )
                )
            return display_szs_pos

    def canvas_display_group(self, disp_szs_pos, major_canv_pos):
        """Compute canvas size and positions for a subset of displays."""
        canv_pos = (
            round(min([sz_pos[1][0] for sz_pos in disp_szs_pos]) + major_canv_pos[0]),
            round(min([sz_pos[1][1] for sz_pos in disp_szs_pos]) + major_canv_pos[1]),
        )
        canv_sz = (
            round(max([sz_pos[0][0] + sz_pos[1][0] for sz_pos in disp_szs_pos]) - canv_pos[0]),
            round(max([sz_pos[0][1] + sz_pos[1][1] for sz_pos in disp_szs_pos]) - canv_pos[1]),
        )
        return (canv_sz, canv_pos)

    #
    # Buttons
    #
    def create_buttons(self, use_ppi_px):
        """Create buttons for display preview positioning config."""
        # Buttons - show only if use_ppi_px == True
        self.button_config = wx.Button(self, label="Positions")
        self.button_save = wx.Button(self, label="Save")
        self.button_reset = wx.Button(self, label="Reset")
        self.button_cancel = wx.Button(self, label="Cancel")
        self.button_entry = wx.Button(self, label="Exact entry")
        # GTK themes can require more than 27px for a button's padding and
        # borders alone. Let the native toolkit choose a valid minimum size.
        self.button_help = wx.Button(self, label="Help", name="butt_help")
        self.button_help.SetToolTip("About monitor preview and positioning")

        self.button_config.Bind(wx.EVT_BUTTON, self.onConfigure)
        self.button_save.Bind(wx.EVT_BUTTON, self.onSave)
        self.button_reset.Bind(wx.EVT_BUTTON, self.onReset)
        self.button_cancel.Bind(wx.EVT_BUTTON, self.onCancel)
        self.button_entry.Bind(wx.EVT_BUTTON, self.onEntry)
        self.button_help.Bind(wx.EVT_BUTTON, self.onHelp)

        self.move_buttons()

        self.button_config.Show(use_ppi_px)
        self.button_save.Show(False)
        self.button_reset.Show(False)
        self.button_entry.Show(False)
        self.button_cancel.Show(False)

    def move_buttons(self):
        """Position display config buttons in the bottom-right corner.

        Each button is right-aligned using its own width so wider labels such as
        "Positions" and "Exact entry" stay fully inside the panel. Using
        ``GetDefaultSize`` here clipped them, as it returns wx's generic button
        width and ignores the actual label.
        """
        sz_area = self.GetSize()
        self.butt_gap = 10
        gap = self.butt_gap
        row_h = self.button_config.GetBestSize()[1]
        right = sz_area[0] - gap
        bottom_y = sz_area[1] - row_h - gap

        def right_aligned(button, row):
            button.SetPosition(wx.Point(right - button.GetBestSize()[0], bottom_y - row * (row_h + gap)))

        # Right column, bottom to top. Config/Cancel share the bottom row (only
        # one is ever shown at a time depending on config mode).
        right_aligned(self.button_config, 0)
        right_aligned(self.button_cancel, 0)
        right_aligned(self.button_reset, 1)
        right_aligned(self.button_entry, 2)
        # Save sits to the left of the bottom-right Cancel button.
        self.button_save.SetPosition(
            wx.Point(
                right - self.button_cancel.GetBestSize()[0] - gap - self.button_save.GetBestSize()[0],
                bottom_y,
            )
        )
        self.button_help.SetPosition(wx.Point(right - self.button_help.GetBestSize()[0], gap))

    def toggle_buttons(self, show_config, in_config):
        """Toggle visibility of display positioning config buttons."""
        self.button_config.Show(show_config)
        self.button_save.Show(in_config)
        self.button_reset.Show(in_config)
        self.button_entry.Show(in_config)
        self.button_cancel.Show(in_config)

    def onConfigure(self, evt):
        """Start diplay position config mode."""
        # Calibration uses physical positions. Remember (do not overwrite) the
        # user's desktop-vs-physical preview preference for Save/Cancel.
        self._desktop_layout_before_config = self.desktop_layout_enabled
        self.set_desktop_layout(False)
        self.frame.studio_desktop_layout.Disable()
        self.old_ppinorm_offs = self.display_sys.get_ppinorm_offsets()  # back up the offsets
        self.frame.toggle_radio_and_profile_choice(False)
        self.frame.toggle_bezel_buttons(False, False)
        self.config_mode = True
        self.toggle_buttons(False, True)
        self.show_staticbmps(False)
        self.create_shapes()
        self.Refresh()

    def onSave(self, evt):
        """Stage current Display offsets into DisplaySystem.

        The new positions are kept in memory (and shown in the preview) but are
        only written to disk when the system band's Save is pressed.
        """
        self.config_mode = False
        self.bind_movement_binds(False)
        self.toggle_buttons(True, False)
        # Export offsets to DisplaySystem (staged, not persisted here)
        if self.positions_dragged:  # only export drag positions if actually dragged
            self.export_offsets(self.display_sys)
        display_data = self.display_sys.get_disp_list(use_ppi_norm=True)
        # Full redraw of preview with new offset data
        if self.current_preview_images:
            self.preview_wallpaper(
                self.current_preview_images,
                True,
                self.frame.use_multi_image,
                display_data=display_data,
                spangroups=self.frame.read_spangroups(True),
            )
        else:
            self.display_data = display_data
            self.refresh_preview(True)
            self.resize_displays(True)
        self.set_desktop_layout(getattr(self, "_desktop_layout_before_config", False))
        self.frame.studio_desktop_layout.Enable(self.frame._workspace != "Displays")
        self.draggable_shapes = []  # Destroys DragShapes
        self.positions_dragged = False
        self.frame.toggle_radio_and_profile_choice(True)
        self.frame.toggle_bezel_buttons(False, True)
        self.Refresh()
        self.frame._update_system_dirty()

    def onReset(self, evt):
        """Reset Display preview positions to the initial guess."""
        # Back up current offsets
        ppinorm_offs = self.display_sys.get_ppinorm_offsets()
        # Compute and get reset offsets
        self.display_sys.compute_initial_preview_offsets()
        display_data = self.display_sys.get_disp_list(use_ppi_norm=True)
        dtop_canvas_px = self.get_canvas(display_data, True)
        dtop_canvas_relsz, dtop_canvas_pos, scaling_fac = self.fit_canvas_wrkarea(dtop_canvas_px)
        (display_rel_sizes, img_rel_sizes, bz_rel_sizes) = self.displays_on_canvas(
            display_data, dtop_canvas_pos, scaling_fac, True
        )
        # Move display preview draggable_shapes
        for shp, off in zip(self.draggable_shapes, display_rel_sizes):
            shp.pos = off[1]
        # Restore backed up offsets
        self.display_sys.update_ppinorm_offsets(ppinorm_offs)
        self.positions_dragged = True
        self.Refresh()

    def onEntry(self, evt):
        """Opens display positions accurate entry dialog."""
        DisplayPositionEntry(self, dragged_positions=self.positions_dragged)

    def onCancel(self, evt):
        """Cancel out of diplay position config mode."""
        self.config_mode = False
        self.bind_movement_binds(False)
        self.toggle_buttons(True, False)
        self.draggable_shapes = []  # Destroys DragShapes
        self.display_sys.update_ppinorm_offsets(self.old_ppinorm_offs)
        # redraw preview with restored data
        self.display_data = self.display_sys.get_disp_list(True)
        self.refresh_preview(True)
        self.full_refresh_preview(True, True, self.frame.use_multi_image, spangroups=self.frame.read_spangroups(True))
        self.set_desktop_layout(getattr(self, "_desktop_layout_before_config", False))
        self.frame.studio_desktop_layout.Enable(self.frame._workspace != "Displays")
        self.frame.toggle_radio_and_profile_choice(True)
        self.frame.toggle_bezel_buttons(False, True)
        self.positions_dragged = False
        self.Refresh()

    def onHelp(self, evt):
        """Popup a help dialog."""
        text = (
            "Preview of your wallpaper settings.\n"
            "In 'advanced span' mode you need use the 'Positions'\n"
            "tool to move the display previews by dragging to\n"
            "as accurately as possible represent the actual\n"
            "positions of you displays on your desk."
        )
        use_per = self.display_sys.use_perspective
        persp_sel = self.frame.ch_persp.GetSelection()
        if persp_sel != wx.NOT_FOUND:
            persname = self.frame.ch_persp.GetString(persp_sel)
        else:
            persname = "default"
        pop = HelpPopup(self, text, show_image_quality=True, use_perspective=use_per, persp_name=persname)
        btn = evt.GetEventObject()
        pos = btn.ClientToScreen((0, 0))
        sz = btn.GetSize()
        pop.Position(pos, wx.Size(0, sz[1]))
        pop.Popup()

    def show_staticbmps(self, show):
        """Show/Hide StaticBitmaps."""
        if self.current_preview_images:
            self.st_bmp_canvas.Show(show)
        else:
            self.st_bmp_canvas.Show(False)
        for st_bmp in self.preview_img_list:
            st_bmp.Show(show)

    def export_offsets(self, display_sys):
        """Read dragged preview positions, normalize them to be positive,
        and scale sizes up to old canvas size."""
        # DragShape sizes and positions need to be scaled up by true_canvas_old_w/preview_canv_w
        prev_canv_w = self.st_bmp_canvas.GetSize()[0]
        true_canv_w = self.get_canvas(display_sys.get_disp_list(True))[0]
        scaling = true_canv_w / prev_canv_w
        sanitzed_offs = self.sanitize_shape_offs()
        ppi_norm_offsets = []
        for off in sanitzed_offs:
            ppi_norm_offsets.append((off[0] * scaling, off[1] * scaling))
        display_sys.update_ppinorm_offsets(ppi_norm_offsets, bezels_included=False)

    def sanitize_shape_offs(self):
        """Return shapes' relative offsets, anchoring to (0,0)."""
        sanitized_offs = []
        leftmost_offset = min([shape.pos[0] for shape in self.draggable_shapes])
        topmost_offset = min([shape.pos[1] for shape in self.draggable_shapes])
        for shape in self.draggable_shapes:
            sanitized_offs.append((shape.pos[0] - leftmost_offset, shape.pos[1] - topmost_offset))
        return sanitized_offs

    #
    # DragImage methods
    #

    class DragShape:
        def __init__(self, bmp):
            self.bmp = bmp
            self.pos = (0, 0)
            self.shown = True
            self.text = None
            self.fullscreen = False

        def HitTest(self, pt):
            rect = self.GetRect()
            return rect.Contains(pt)

        def GetRect(self):
            return wx.Rect(self.pos[0], self.pos[1], self.bmp.GetWidth(), self.bmp.GetHeight())

        def Draw(self, dc, op=wx.COPY):
            if self.bmp.IsOk():
                memDC = wx.MemoryDC()
                memDC.SelectObject(self.bmp)

                dc.Blit(
                    self.pos[0],
                    self.pos[1],
                    self.bmp.GetWidth(),
                    self.bmp.GetHeight(),
                    memDC,
                    0,
                    0,
                    op,
                    True,
                )

                return True
            else:
                return False

    def create_shapes(self, enable_movement=True):
        """Create draggable objects from display previews."""
        self.draggable_shapes = []
        self.drag_image = None
        self.drag_shape = None

        for st_bmp in self.preview_img_list:
            shape = self.DragShape(st_bmp.GetBitmap())
            shape.pos = st_bmp.GetPosition()
            self.draggable_shapes.append(shape)

        self.Bind(wx.EVT_PAINT, self.OnPaint)
        if enable_movement:
            self.bind_movement_binds(True)

    def bind_background_drag(self):
        """Enable picture panning while display arrangement is inactive."""
        self.Bind(wx.EVT_LEFT_DOWN, self._on_background_down)
        self.Bind(wx.EVT_LEFT_UP, self._on_background_up)
        self.Bind(wx.EVT_MOTION, self._on_background_motion)

    def bind_wallpaper_bitmap_drag(self):
        """Bitmap children receive mouse events before their parent panel.

        Mouse events are not command events, so clicking a displayed wallpaper
        does not bubble up to the preview panel. Bind each rendered bitmap once
        rather than rebinding when entering/exiting monitor arrangement mode.
        """
        for bitmap in (self.st_bmp_canvas, *self.preview_img_list):
            bitmap.Bind(wx.EVT_LEFT_DOWN, self._on_background_down)
            bitmap.Bind(wx.EVT_LEFT_UP, self._on_background_up)
            bitmap.Bind(wx.EVT_MOTION, self._on_background_motion)

    def _preview_mouse_position(self, event):
        """Translate bitmap-local mouse coordinates to preview-panel space."""
        source = event.GetEventObject()
        point = event.GetPosition()
        if source is self:
            return point
        return self.ScreenToClient(source.ClientToScreen(point))

    def bind_movement_binds(self, toggle):
        """Keep dragging monitors separate from dragging the wallpaper."""
        for event_type in (wx.EVT_LEFT_DOWN, wx.EVT_LEFT_UP, wx.EVT_MOTION):
            self.Unbind(event_type)
        if toggle:
            self._finish_background_drag()
            self.Bind(wx.EVT_LEFT_DOWN, self.OnLeftDown)
            self.Bind(wx.EVT_LEFT_UP, self.OnLeftUp)
            self.Bind(wx.EVT_MOTION, self.OnMotion)
            self.Bind(wx.EVT_LEAVE_WINDOW, self.OnLeaveWindow)
        else:
            self.Unbind(wx.EVT_LEAVE_WINDOW)
            self.bind_background_drag()

    def draw_shapes(self, dc):
        for shape in self.draggable_shapes:
            if shape.shown:
                shape.Draw(dc)

    def draw_canvas(self, dc, draw=True):
        if self.st_bmp_canvas:
            pos = self.st_bmp_canvas.GetPosition()
            bmp = self.st_bmp_canvas.GetBitmap()
            bmp_sz = bmp.GetSize()
            if not draw:
                bmp = wx.Bitmap.FromRGBA(bmp_sz[0], bmp_sz[1], red=30, green=30, blue=30, alpha=255)
            op = wx.COPY
            if bmp.IsOk():
                memDC = wx.MemoryDC()
                # memDC.SelectObject(wx.NullBitmap)
                memDC.SelectObject(bmp)

                dc.Blit(pos[0], pos[1], bmp_sz[0], bmp_sz[1], memDC, 0, 0, op, True)

                return True
            else:
                return False

    def draw_st_bmps(self, dc):
        for st_bmp in self.preview_img_list:
            pos = st_bmp.GetPosition()
            bmp = st_bmp.GetBitmap()
            self.draw_bmp(dc, pos, bmp)

    def draw_bmp(self, dc, pos, bmp):
        bmp_sz = bmp.GetSize()
        op = wx.COPY
        if bmp.IsOk():
            memDC = wx.MemoryDC()
            memDC.SelectObject(bmp)

            dc.Blit(pos[0], pos[1], bmp_sz[0], bmp_sz[1], memDC, 0, 0, op, True)
            return True
        else:
            return False

    def find_shape(self, pt):
        for shape in self.draggable_shapes:
            if shape.HitTest(pt):
                return shape
        return None

    def OnPaint(self, evt):
        dc = wx.PaintDC(self)
        # Hiding bitmap widgets, probably unnecessary
        # for st_bmp in self.preview_img_list:
        # st_bmp.Hide()

        # Canvas drawing
        if not self.config_mode and not self.use_multi_image and self.current_preview_images:
            self.draw_canvas(dc)
        else:
            self.draw_canvas(dc, False)

        # Display drawing
        if self.config_mode:
            self.draw_shapes(dc)
        else:
            self.draw_st_bmps(dc)

    def _drag_target(self, point):
        """Find the displayed image and crop rectangle under the mouse."""
        if not self.current_preview_images or self.config_mode or self.bezel_conifg_mode:
            return None
        if self.desktop_layout_enabled and self.desktop_preview.IsShown():
            _canvas_size, _canvas_pos, rectangles = self._desktop_preview_rectangles()
            for x, y, width, height in rectangles:
                if x <= point.x < x + width and y <= point.y < y + height:
                    return self.current_preview_images[0], self.dtop_canvas_relsz
            return None
        if self.use_multi_image or (self._last_use_ppi and self._last_spangroups):
            for index, (size, position) in enumerate(self.display_rel_sizes):
                x, y = position
                if x <= point.x < x + size[0] and y <= point.y < y + size[1]:
                    if self.use_multi_image and not self._last_spangroups:
                        image_index = min(index, len(self.current_preview_images) - 1)
                        return self.current_preview_images[image_index], size
                    for group_index, group in enumerate(self._last_spangroups):
                        if index in self._last_spangroups[group]:
                            displays = [self.display_rel_sizes[i] for i in self._last_spangroups[group]]
                            canvas_size, _position = self.canvas_display_group(displays, (0, 0))
                            image_index = min(group_index, len(self.current_preview_images) - 1)
                            return self.current_preview_images[image_index], canvas_size
            return None
        size = self.dtop_canvas_relsz
        x, y = self.dtop_canvas_pos
        if x <= point.x < x + size[0] and y <= point.y < y + size[1]:
            return self.current_preview_images[0], size
        return None

    def _on_background_down(self, event):
        point = self._preview_mouse_position(event)
        if self.compare_original and not (self.config_mode or self.bezel_conifg_mode):
            pixels = comparison_hit_region(
                self._comparison_regions(desktop=self.desktop_layout_enabled and self.desktop_preview.IsShown()),
                (point.x, point.y),
                self.compare_fraction,
            )
            if pixels is not None:
                self._comparison_drag = (point.x, self.compare_fraction, pixels)
                self.CaptureMouse()
                self.SetCursor(wx.Cursor(wx.CURSOR_SIZEWE))
                return
        if not (self.frame.sld_offx.IsEnabled() and self.frame.sld_offy.IsEnabled()):
            event.Skip()
            return
        target = self._drag_target(point)
        if target is None:
            event.Skip()
            return
        path, size = target
        try:
            with Image.open(path) as image:
                overflow = crop_overflow(ImageOps.exif_transpose(image).size, size, self.zoom)
        except OSError, ValueError:
            event.Skip()
            return
        if not any(overflow):
            event.Skip()
            return
        self._background_drag = (point, self.offset, overflow)
        self.CaptureMouse()
        self.SetCursor(wx.Cursor(wx.CURSOR_HAND))

    def _on_background_motion(self, event):
        if self._comparison_drag is not None:
            if event.LeftIsDown():
                start_x, fraction, pixels = self._comparison_drag
                point = self._preview_mouse_position(event)
                self.frame._studio_drag_split_handle(comparison_drag_fraction(fraction, point.x - start_x, pixels))
            return
        if self._background_drag is None or not event.LeftIsDown():
            event.Skip()
            return
        start, offsets, overflow = self._background_drag
        point = self._preview_mouse_position(event)
        moved = (point.x - start.x, point.y - start.y)
        self.frame.on_wallpaper_dragged(pan_offset_for_drag(offsets, moved, overflow))

    def _finish_background_drag(self):
        self._comparison_drag = None
        self._background_drag = None
        if self.HasCapture():
            self.ReleaseMouse()
        self.SetCursor(wx.NullCursor)

    def _on_background_up(self, event):
        if self._comparison_drag is not None or self._background_drag is not None:
            self._finish_background_drag()
        else:
            event.Skip()

    def _on_background_capture_lost(self, event):
        self._comparison_drag = None
        self._background_drag = None
        self.SetCursor(wx.NullCursor)
        event.Skip()

    def OnLeftDown(self, evt):
        # Did the mouse go down on one of our shapes?
        shape = self.find_shape(evt.GetPosition())

        # If a shape was 'hit', then set that as the shape we're going to
        # drag around. Get our start position. Dragging has not yet started.
        # That will happen once the mouse moves, OR the mouse is released.
        if shape:
            self.drag_shape = shape
            self.dragStartPos = evt.GetPosition()

    def OnLeftUp(self, evt):
        if not self.drag_image or not self.drag_shape:
            self.drag_image = None
            self.drag_shape = None
            return

        # Hide the image, end dragging, and nuke out the drag image.
        self.drag_image.Hide()
        self.drag_image.EndDrag()
        self.drag_image = None

        self.drag_shape.pos = (
            self.drag_shape.pos[0] + evt.GetPosition()[0] - self.dragStartPos[0],
            self.drag_shape.pos[1] + evt.GetPosition()[1] - self.dragStartPos[1],
        )

        self.drag_shape.shown = True
        self.RefreshRect(self.drag_shape.GetRect())
        self.drag_shape = None
        self.positions_dragged = True

    def OnMotion(self, evt):
        # Ignore mouse movement if we're not dragging.
        if not self.drag_shape or not evt.Dragging() or not evt.LeftIsDown():
            return

        # if we have a shape, but haven't started dragging yet
        if self.drag_shape and not self.drag_image:
            # only start the drag after having moved a couple pixels
            tolerance = 2
            pt = evt.GetPosition()
            dx = abs(pt.x - self.dragStartPos.x)
            dy = abs(pt.y - self.dragStartPos.y)
            if dx <= tolerance and dy <= tolerance:
                return

            # refresh the area of the window where the shape was so it
            # will get erased.
            self.drag_shape.shown = False
            self.RefreshRect(self.drag_shape.GetRect(), True)
            self.Update()

            item = self.drag_shape.text or self.drag_shape.bmp
            self.drag_image = wx.DragImage(item, wx.Cursor(wx.CURSOR_HAND))

            hotspot = self.dragStartPos - self.drag_shape.pos
            self.drag_image.BeginDrag(hotspot, self, self.drag_shape.fullscreen)

            self.drag_image.Move(pt)
            self.drag_image.Show()

        # if we have shape and image then move it, posibly highlighting another shape.
        elif self.drag_shape and self.drag_image:
            # now move it and show it again if needed
            self.drag_image.Move(evt.GetPosition())

    def OnLeaveWindow(self, evt):
        """On leavewindow event drop dragged image by simulating a left up event."""
        self.OnLeftUp(evt)

    #
    # Bezel Configuration mode
    #
    def start_bezel_config(self):
        """Enters bezel config mode.

        Reveals buttons on each display right and bottom edges
        to allow adding bezels. Additionally hides display
        position config button(s)."""
        # TODO Change background color?
        self.old_bezels = self.display_sys.bezels_in_mm()
        self.old_ppinorm_offs = self.display_sys.get_ppinorm_offsets()
        self.bezel_conifg_mode = True
        # Draw bitmaps manually, widgets can't overlap
        # self.show_staticbmps(False)
        # self.create_shapes(enable_movement=False)
        self.show_bezel_buttons(True)
        # Hide preview positioning config button
        self.toggle_buttons(False, False)

    def bezel_config_save(self):
        """Stage the new bezel values for the active DisplaySystem.

        Bezel sizes are kept in memory and shown in the preview; persistence to
        disk happens only when the system band's Save is pressed.
        """
        self.bezel_conifg_mode = False
        self.show_bezel_buttons(False)
        # Show preview positioning config button
        self.toggle_buttons(True, False)
        # self.draggable_shapes = []  # Destroys DragShapes / manually drawn previews
        self.full_refresh_preview(True, True, False)
        # self.show_staticbmps(True)
        self.Refresh()

    def bezel_config_cancel(self):
        """Exits out of the bezel config mode without saving."""
        self.bezel_conifg_mode = False
        self.show_bezel_buttons(False)
        # Show preview positioning config button
        self.toggle_buttons(True, False)
        self.display_sys.update_bezels(self.old_bezels)
        self.display_sys.update_ppinorm_offsets(self.old_ppinorm_offs)
        for pops, bez_mms in zip(self.bezel_popups, self.old_bezels):
            pops[0].set_bezel_value(bez_mms[0])
            pops[1].set_bezel_value(bez_mms[1])
        self.display_data = self.display_sys.get_disp_list(True)
        # self.draggable_shapes = []  # Destroys DragShapes / manually drawn previews
        self.full_refresh_preview(True, True, False)
        # self.show_staticbmps(True)
        self.Refresh()

    def create_bezel_buttons(self):
        # load icons into bitmaps
        rb_png = os.path.join(RESOURCES_PATH, "icons8-merge-vertical-96.png")
        bb_png = os.path.join(RESOURCES_PATH, "icons8-merge-horizontal-96.png")
        rb_img = wx.Image(rb_png, type=wx.BITMAP_TYPE_ANY)
        bb_img = wx.Image(bb_png, type=wx.BITMAP_TYPE_ANY)
        rb_bmp = rb_img.Scale(20, 20).Resize(wx.Size(20, 20), wx.Point((0, 0))).ConvertToBitmap()
        bb_bmp = bb_img.Scale(20, 20).Resize(wx.Size(20, 20), wx.Point((0, 0))).ConvertToBitmap()

        # create bitmap buttons
        for st_bmp in self.preview_img_list:
            butt_rb = wx.BitmapButton(self, bitmap=rb_bmp, name="butt_bez_r", style=wx.BORDER_NONE)
            butt_bb = wx.BitmapButton(self, bitmap=bb_bmp, name="butt_bez_b", style=wx.BORDER_NONE)
            bez_butt_color = wx.Colour(41, 47, 52)
            butt_rb.SetBackgroundColour(bez_butt_color)
            butt_bb.SetBackgroundColour(bez_butt_color)
            self.bez_butt_sz = butt_rb.GetSize()
            pos_rb, pos_bb = self.bezel_button_positions(st_bmp)
            butt_rb.SetPosition(wx.Point((pos_rb[0], pos_rb[1])))
            butt_bb.SetPosition(wx.Point((pos_bb[0], pos_bb[1])))
            butt_rb.Bind(wx.EVT_BUTTON, self.onBezelButton)
            butt_bb.Bind(wx.EVT_BUTTON, self.onBezelButton)
            self.bez_buttons.append((butt_rb, butt_bb))
        self.show_bezel_buttons(False)
        self.create_bezel_popups()

    def create_bezel_popups(self):
        self.bezel_popups = []
        bezel_mm = self.display_sys.bezels_in_mm()
        for butts, bez_mm in zip(self.bez_buttons, bezel_mm):
            pop_rb = self.popup_at_button(butts[0])
            pop_rb.set_bezel_value(bez_mm[0])
            pop_bb = self.popup_at_button(butts[1])
            pop_bb.set_bezel_value(bez_mm[1])
            self.bezel_popups.append((pop_rb, pop_bb))

    def popup_at_button(self, button):
        """Initialize a popup at button position."""
        try:
            pop = self.BezelEntryPopup(self, wx.SIMPLE_BORDER | wx.PU_CONTAINS_CONTROLS)
        except AttributeError:
            pop = self.BezelEntryPopup(self, wx.SIMPLE_BORDER)
        return pop

    def move_popup_to_button(self, pop, button):
        """Move pop next to its associated button."""
        butt_name = button.GetName()
        pos = button.ClientToScreen((0, 0))
        butt_sz = button.GetSize()
        pop_sz = pop.GetSize()
        if butt_name == "butt_bez_r":
            # Center pop vertically to button
            y_cntr = (-pop_sz[1] + butt_sz[1]) / 2
            pop.Position(pos, (-pop_sz[0], y_cntr))
        else:
            # Center pop horizontally to button
            x_cntr = (-pop_sz[0] + butt_sz[0]) / 2
            pop.Position(pos, (x_cntr, -pop_sz[1]))

    def show_bezel_buttons(self, show):
        """Show/Hide the bezel buttons."""
        for butt in self.bez_buttons:
            butt[0].Show(show)
            butt[1].Show(show)

    def bezel_button_positions(self, st_bmp):
        """Return the mid points on the screen of the right and bottom edges
        of the given StaticBitmap."""
        sz = st_bmp.GetSize()
        pos = st_bmp.GetPosition()
        bsz = self.bez_butt_sz
        pos_rb = (round(sz[0] + pos[0] - bsz[0] / 2), round(sz[1] / 2 + pos[1] - bsz[1] / 2))
        pos_bb = (round(sz[0] / 2 + pos[0] - bsz[0] / 2), round(sz[1] + pos[1] - bsz[1] / 2))
        return [pos_rb, pos_bb]

    def move_bezel_buttons(self):
        """Move bezel buttons after a resize."""
        for butts, st_bmp in zip(self.bez_buttons, self.preview_img_list):
            pos_rb, pos_bb = self.bezel_button_positions(st_bmp)
            butts[0].SetPosition((pos_rb[0], pos_rb[1]))
            butts[1].SetPosition((pos_bb[0], pos_bb[1]))

    def move_bezel_popups(self):
        """Move bezel popups to their respective buttons."""
        for butts, pops in zip(self.bez_buttons, self.bezel_popups):
            self.move_popup_to_button(pops[0], butts[0])
            self.move_popup_to_button(pops[1], butts[1])

    def onBezelButton(self, event):
        for pop in self.bezel_popups:
            pop[0].Hide()
            pop[1].Hide()
        self.move_bezel_popups()
        # Get button instance and find it in list
        button = event.GetEventObject()
        button_pos = None
        for butt_pair in self.bez_buttons:
            if button in butt_pair:
                button_pos = (self.bez_buttons.index(butt_pair), butt_pair.index(button))
                break

        # Pick and show respective popup
        if button_pos is None:
            return
        pop = self.bezel_popups[button_pos[0]][button_pos[1]]
        # pop.Popup()
        pop.Show()

    #
    # Bezel entry pop-up
    #

    class BezelEntryPopup(wx.PopupTransientWindow):
        # class BezelEntryPopup(wx.PopupWindow):
        """Popup that is shown when a bezel button is pressed in bezel config."""

        def __init__(self, parent, style):
            # wx.PopupTransientWindow.__init__(self, parent, style)
            wx.PopupWindow.__init__(self, parent, style)
            self.preview = parent
            pnl = wx.Panel(self)
            # pnl.SetBackgroundColour("CADET BLUE")

            st = wx.StaticText(pnl, -1, "Enter the size of adjacent bezels and gap\nin millimeters:")
            # self.tc_bez = wx.TextCtrl(pnl, -1, size=(100, -1))
            self.tc_bez = wx.TextCtrl(pnl, -1, size=wx.Size(60, -1), style=wx.TE_RIGHT | wx.TE_PROCESS_ENTER)
            self.tc_bez.Bind(wx.EVT_TEXT_ENTER, self.OnEnter)
            self.current_bez_val = None
            butt_save = wx.Button(pnl, label="Apply")
            butt_canc = wx.Button(pnl, label="Cancel")
            butt_save.Bind(wx.EVT_BUTTON, self.onApply)
            butt_canc.Bind(wx.EVT_BUTTON, self.onCancel)
            butt_sizer = wx.BoxSizer(wx.HORIZONTAL)
            # butt_sizer.AddStretchSpacer()
            butt_sizer.Add(self.tc_bez, 0, wx.ALL, 5)
            butt_sizer.Add(butt_save, 0, wx.ALL, 5)
            butt_sizer.Add(butt_canc, 0, wx.ALL, 5)

            sizer = wx.BoxSizer(wx.VERTICAL)
            sizer.Add(st, 0, wx.ALL, 5)
            # sizer.Add(self.tc_bez, 0, wx.ALL, 5)
            sizer.Add(butt_sizer, 0, wx.ALL | wx.EXPAND, 0)
            pnl.SetSizer(sizer)

            sizer.Fit(pnl)
            sizer.Fit(self)
            self.Layout()

        def ProcessLeftDown(self, event):
            # return wx.PopupTransientWindow.ProcessLeftDown(self, event)
            return False

        def OnEnter(self, evt):
            """Bind pressing Enter in the txtctrl to apply entered value."""
            self.onApply(evt)

        def OnDismiss(self):
            self.onCancel(None)

        def onApply(self, event):
            entered_val = self.test_bezel_value()
            if entered_val is False:
                # Abort applying and alert user but don't
                # Dismiss popup to fascilitate fixing.
                msg = f"Bezel thickness must be a non-negative number, '{self.tc_bez.GetValue()}' was entered."
                sp_logging.G_LOGGER.info(msg)
                self.Hide()
                dial = wx.MessageDialog(self, msg, "Error", wx.OK | wx.STAY_ON_TOP | wx.CENTRE)
                dial.ShowModal()
                self.Show()
                return -1
            self.current_bez_val = entered_val
            pops = self.preview.bezel_popups
            bezel_mms = []
            for pop_pair in pops:
                bezel_mms.append((pop_pair[0].bezel_value(), pop_pair[1].bezel_value()))
            # self.Dismiss()
            self.Hide()
            # propagate values and refresh preview
            self.preview.display_sys.update_bezels(bezel_mms)
            self.preview.display_data = self.preview.display_sys.get_disp_list(True)
            self.preview.full_refresh_preview(True, True, False)
            # self.preview.show_staticbmps(False) # Use PaintDC drawing separately from staticbitmaps
            # self.preview.draggable_shapes = []
            # self.preview.create_shapes(enable_movement=False)

        def onCancel(self, event):
            if self.current_bez_val:
                self.tc_bez.SetValue(str(self.current_bez_val))
            else:
                self.tc_bez.SetValue("0.0")
            # self.Dismiss()
            self.Hide()

        def bezel_value(self):
            """Return the entered bezel thickness as a float."""
            bez = self.tc_bez.GetValue()
            return float(bez)

        def set_bezel_value(self, val):
            """Write val to TextCtrl."""
            self.tc_bez.SetValue(str(val))
            self.current_bez_val = str(val)

        def test_bezel_value(self):
            """Test that entered value in tc_bez is valid and return it."""
            val = self.tc_bez.GetValue()
            try:
                num = float(val)
                if num >= 0:
                    return num
                else:
                    return False
            except ValueError:
                return False
