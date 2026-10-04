"""
New wallpaper configuration GUI for Superpaper.
"""

import copy
import os
import sys
import tempfile
import time
from operator import itemgetter

from PIL import Image, ImageOps

import superpaper.sp_logging as sp_logging
import superpaper.wallpaper_processing as wpproc
from superpaper.cloud_upscale import UPSCALE_MODES, normalize_scale_mode, normalize_sharpen
from superpaper.configuration_dialogs import (
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
from superpaper.image_adjustments import normalize_adjustment
from superpaper.local_shaders import ShaderImportError, available_shaders, import_shader_pack, normalize_shader
from superpaper.message_dialog import show_message_dialog
from superpaper.native_picker import NativePickerError, pick_kde_paths
from superpaper.preview_geometry import wheel_scroll_units
from superpaper.profile_id import ProfileId, ProfileIdError
from superpaper.source_paths import IMAGE_EXTENSIONS, resolved_wallpaper_selections, source_identity
from superpaper.sp_paths import RESOURCES_PATH, TRAY_ICON
from superpaper.studio_widgets import StudioActionButton, StudioNavigationButton, StudioSegmentButton
from superpaper.wallpaper_preview_panel import WallpaperPreviewPanel
from superpaper.wallpaper_processing import change_wallpaper_job

try:
    import wx  # pyright: ignore[reportMissingImports]  # ty:ignore[unresolved-import]
    import wx.adv  # pyright: ignore[reportMissingImports]  # ty:ignore[unresolved-import]
except ImportError:
    sys.exit()


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
        # Explicit source replacement is separate from wx.ListCtrl focus, which
        # GTK does not reliably transfer when Select(index) is called.
        self._pending_source_replacements = {}
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
        self._pending_source_replacements.clear()
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
        # Show the same complete positional image selection the renderer will
        # receive, not just the changed target's thumbnail. This is important
        # for multiple displays and advanced span groups.
        profile, _groups = self._collect_temp_profile(resolve_selection=True)
        preview_files = profile.selected
        if not preview_files:
            if os.path.isdir(path):
                preview_files = [
                    os.path.join(path, filename)
                    for filename in sorted(os.listdir(path))
                    if filename.lower().endswith(wpproc.G_SUPPORTED_IMAGE_EXTENSIONS)
                ][:1]
            else:
                preview_files = [path]
        if not preview_files:
            return
        display_data = self.display_sys.get_disp_list(self.show_advanced_settings)
        self.wpprev_pnl.preview_wallpaper(
            preview_files,
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
                self.path_listctrl.SetItemState(
                    index,
                    wx.LIST_STATE_SELECTED | wx.LIST_STATE_FOCUSED,
                    wx.LIST_STATE_SELECTED | wx.LIST_STATE_FOCUSED,
                )
                break
        # The renderer uses the serialized profile's selected= entry. Store
        # the explicit choice in the correct display/group slot even when GTK
        # leaves an older row with keyboard focus or multiple images are used.
        self._pending_source_replacements[target] = path
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
            # Apply is intentionally temporary: writing selected= to the
            # saved profile here can leave an orphaned image reference when the
            # replacement only exists in the unsaved source list. Save and
            # Save & Apply are the explicit persistence boundaries.
            if hasattr(self, "studio_status"):
                self.studio_status.SetLabel("Render finished. Check the desktop for the applied wallpaper.")
        finally:
            try:
                os.remove(temp_file)
            except OSError:
                pass
            del busy

    def _get_selected_wallpaper_path(self):
        """Get the selected source; GTK's focused item may be different."""
        item_idx = self.path_listctrl.GetFirstSelected()
        if item_idx == -1:
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

        When ``resolve_selection`` is True the image selection is reconciled
        with the actual source paths for every monitor or span group, preferring
        an explicit Change Image replacement and retaining valid prior choices.
        It is omitted from change-tracking, since Apply must be unsaved.
        This method never raises on partial field input.
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

        # span groups
        groups = None
        if self.cb_spangroups.GetValue():
            groups = self.read_spangroups()
            flat_groups = []
            if groups is not None:
                for grp in sorted(groups):
                    ids = "".join(str(i) for i in groups[grp])
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
            target_ids = [""]
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
            target_ids = list(paths_dict)
            for disp_id in target_ids:
                semicol_sep_paths = ";".join(paths_dict[disp_id])
                tmp_profile.paths_array.append(semicol_sep_paths)

        if resolve_selection:
            previous = self.loaded_profile.selected if self.loaded_profile is not None else None
            replacements = dict(self._pending_source_replacements)
            if len(target_ids) == 1 and not replacements:
                # Only the single-source view uses list selection as a direct
                # wallpaper choice. In multi/group mode each target must keep
                # its own current image unless explicitly replaced.
                selected_path = self._get_selected_wallpaper_path()
                if selected_path:
                    replacements[target_ids[0]] = selected_path
            tmp_profile.selected = resolved_wallpaper_selections(
                [paths.split(";") for paths in tmp_profile.paths_array],
                previous,
                replacements,
                targets=target_ids,
            )

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
            self._pending_source_replacements.clear()
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
        self._pending_source_replacements.clear()
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
