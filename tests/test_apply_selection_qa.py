"""Regression checks: source changes must reach the actual wallpaper renderer.

These tests deliberately never open a GUI, call an AI endpoint or set a desktop
wallpaper. They inspect the exact transient .profile passed to rendering.
"""

import ast
import copy
import os
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from superpaper.source_paths import resolved_wallpaper_selections


def image(tmp_path, filename, color=(80, 120, 160)):
    path = tmp_path / filename
    with Image.new("RGB", (8, 8), color) as picture:
        picture.save(path)
    return str(path)


def working_profile(data, name, groups, *, spanmode, selected):
    profile = data.TempProfileData()
    profile.name = name
    profile.spanmode = spanmode
    profile.slideshow = False
    profile.sortmode = "alphabetical"
    profile.paths_array = [";".join(group) for group in groups]
    profile.selected = list(selected) if selected else None
    return profile


def run_real_apply_handler(panel, data, render):
    """Extract only onApply so tests remain independent of wx/GTK imports."""
    path = Path(__file__).resolve().parents[1] / "superpaper" / "gui.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    klass = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "WallpaperSettingsPanel")
    handler = next(node for node in klass.body if isinstance(node, ast.FunctionDef) and node.name == "onApply")
    namespace = {
        "wx": SimpleNamespace(BusyCursor=lambda: object(), YieldIfNeeded=lambda: None),
        "tempfile": tempfile,
        "os": os,
        "copy": copy,
        "time": time,
        "sp_logging": SimpleNamespace(G_LOGGER=SimpleNamespace(info=lambda *args: None)),
        "parse_profile_file": lambda filename: data.ProfileData(filename),
        "change_wallpaper_job": render,
    }
    exec(compile(ast.Module(body=[handler], type_ignores=[]), "<apply-handler>", "exec"), namespace)
    namespace["onApply"](panel, None)


def test_one_source_replacement_is_serialized_into_real_render_input(profile_modules, tmp_path):
    data, _wpproc = profile_modules
    old = image(tmp_path, "old.png")
    replacement = image(tmp_path, "replacement image.png")
    saved = working_profile(data, "single", [[old]], spanmode="single", selected=[old])
    saved_path = tmp_path / "saved.profile"
    saved.save(filename=saved_path)
    before = saved_path.read_bytes()
    selected = resolved_wallpaper_selections([[replacement]], [old], {"": replacement})
    updated = working_profile(data, "single", [[replacement]], spanmode="single", selected=selected)
    rendered = []

    run_real_apply_handler(
        SimpleNamespace(
            _collect_temp_profile=lambda resolve_selection: (updated, None),
            display_sys=SimpleNamespace(),
            studio_status=SimpleNamespace(SetLabel=lambda _text: None),
        ),
        data,
        lambda profile, **kwargs: rendered.append(
            (profile.next_wallpaper_files(), profile.paths_array, kwargs["force"])
        ),
    )

    assert rendered == [([replacement], [[replacement]], True)]
    assert saved_path.read_bytes() == before
    assert data.ProfileData(saved_path).next_wallpaper_files() == [old]


def test_multi_monitor_change_targets_only_correct_real_render_slot(profile_modules, monkeypatch, tmp_path):
    data, wpproc = profile_modules
    monkeypatch.setattr(wpproc, "NUM_DISPLAYS", 2)
    original = image(tmp_path, "first-original.png", (200, 0, 0))
    updated = image(tmp_path, "first-new.png", (0, 200, 0))
    second = image(tmp_path, "second.png", (0, 0, 200))
    groups = [[updated], [second]]
    chosen = resolved_wallpaper_selections(groups, [original, second], {"0": updated}, targets=["0", "1"])
    assert chosen == [updated, second]

    profile = working_profile(data, "multi", groups, spanmode="multi", selected=chosen)
    output = tmp_path / "unsaved.profile"
    profile.save(filename=output)
    parsed = data.ProfileData(output)
    assert parsed.next_wallpaper_files() == [updated, second]
    assert parsed.paths_array == groups


def test_nonconsecutive_advanced_group_keys_do_not_swap_images(tmp_path):
    group_zero = image(tmp_path, "group-zero.png")
    new_group_two = image(tmp_path, "new-group-two.png")
    old_group_two = image(tmp_path, "old-group-two.png")
    sources = [[group_zero], [new_group_two]]
    actual = resolved_wallpaper_selections(
        sources,
        [group_zero, old_group_two],
        {"2": new_group_two},
        targets=["0", "2"],
    )
    assert actual == [group_zero, new_group_two]


def test_selected_images_from_removed_sources_cannot_survive_change(tmp_path):
    stale = image(tmp_path, "stale.png")
    replacement = image(tmp_path, "replacement.png")
    second = image(tmp_path, "second.png")
    assert resolved_wallpaper_selections([[replacement], [second]], [stale, second], {}) == [replacement, second]


def test_target_can_be_selected_from_a_folder_without_choosing_wrong_monitor(tmp_path):
    directory = tmp_path / "slide show folder"
    directory.mkdir()
    selected_file = image(directory, "01-sky.png")
    other = image(tmp_path, "other.png")
    assert resolved_wallpaper_selections(
        [[str(directory)], [other]], [selected_file, other], {}, targets=["0", "1"]
    ) == [selected_file, other]


def test_sources_that_cannot_render_return_none(tmp_path):
    gone = tmp_path / "missing.png"
    assert resolved_wallpaper_selections([[str(gone)]], [str(gone)], {"": str(gone)}) is None
    assert resolved_wallpaper_selections([[]], None, {}) is None
    with pytest.raises(ValueError, match="matching lengths"):
        resolved_wallpaper_selections([[], []], None, {}, targets=["0"])


def test_apply_handler_never_updates_live_profile_selection(profile_modules, tmp_path):
    data, _ = profile_modules
    old = image(tmp_path, "old.png")
    newer = image(tmp_path, "new.png")
    saved = working_profile(data, "temporary", [[old]], spanmode="single", selected=[old])
    saved_path = tmp_path / "saved.profile"
    saved.save(filename=saved_path)
    live = data.ProfileData(saved_path)
    updated = working_profile(data, "temporary", [[newer]], spanmode="single", selected=[newer])
    before = saved_path.read_bytes()
    rendered = []

    run_real_apply_handler(
        SimpleNamespace(
            _collect_temp_profile=lambda resolve_selection: (updated, None),
            display_sys=SimpleNamespace(),
            _loaded_profile_with_selection=lambda: live,
        ),
        data,
        lambda profile, **_kwargs: rendered.append(profile.next_wallpaper_files()),
    )
    assert rendered == [[newer]]
    assert live.selected == [old]
    assert saved_path.read_bytes() == before


def test_identical_source_lists_use_distinct_display_indices(profile_modules, tmp_path):
    data, _ = profile_modules
    shared = image(tmp_path, "shared.png")
    profile = working_profile(data, "duplicate-groups", [[shared], [shared]], spanmode="multi", selected=[shared, shared])
    serialized = profile._serialize()
    assert f"display0paths={shared}" in serialized
    assert f"display1paths={shared}" in serialized
    assert serialized.count("display0paths=") == 1
    assert serialized.count("display1paths=") == 1


def test_preview_uses_same_complete_selection_as_multi_monitor_renderer(tmp_path):
    path = Path(__file__).resolve().parents[1] / "superpaper" / "gui.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    klass = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "WallpaperSettingsPanel")
    handler = next(node for node in klass.body if isinstance(node, ast.FunctionDef) and node.name == "_preview_source_path")
    namespace = {"os": os, "wpproc": SimpleNamespace(G_SUPPORTED_IMAGE_EXTENSIONS=(".png",))}
    exec(compile(ast.Module(body=[handler], type_ignores=[]), "<preview-handler>", "exec"), namespace)
    left = image(tmp_path, "left.png")
    right = image(tmp_path, "right.png")
    previews = []
    panel = SimpleNamespace(
        _collect_temp_profile=lambda resolve_selection: (SimpleNamespace(selected=[left, right]), None),
        display_sys=SimpleNamespace(get_disp_list=lambda advanced: ["monitor0", "monitor1"]),
        show_advanced_settings=False,
        use_multi_image=True,
        read_spangroups=lambda include_all: None,
        wpprev_pnl=SimpleNamespace(preview_wallpaper=lambda *args: previews.append(args)),
        _studio_refresh_image_card=lambda: None,
    )
    namespace["_preview_source_path"](panel, right)
    assert previews
    assert previews[0][0] == [left, right]
    assert previews[0][2] is True


def test_advanced_group_selection_survives_profile_round_trip(profile_modules, monkeypatch, tmp_path):
    data, wpproc = profile_modules
    monkeypatch.setattr(wpproc, "NUM_DISPLAYS", 3)
    group_a = image(tmp_path, "group-a.png")
    group_b = image(tmp_path, "group-b.png")
    chosen = resolved_wallpaper_selections(
        [[group_a], [group_b]], [group_a, group_b], {"2": group_b}, targets=["0", "2"]
    )
    profile = working_profile(data, "advanced", [[group_a], [group_b]], spanmode="advanced", selected=chosen)
    profile.spangroups = "01,2"
    output = tmp_path / "advanced.profile"
    profile.save(filename=output)
    reloaded = data.ProfileData(output)
    assert reloaded.spangroups == [[0, 1], [2]]
    assert reloaded.selected == [group_a, group_b]
    assert reloaded.next_wallpaper_files() == [group_a, group_b]
