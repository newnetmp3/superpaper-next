"""The KDE picker launches a real system dialog; CI replaces only the process."""

from types import SimpleNamespace

import pytest

from superpaper.native_picker import IMAGE_FILTER, NativePickerError, kde_session, pick_kde_paths


def completed(code, stdout="", stderr=""):
    return SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)


def test_kde_session_detection_including_wayland():
    assert kde_session({"XDG_CURRENT_DESKTOP": "KDE"})
    assert kde_session({"XDG_CURRENT_DESKTOP": "X-Cinnamon:KDE"})
    assert kde_session({"XDG_SESSION_DESKTOP": "plasma", "XDG_SESSION_TYPE": "wayland"})
    assert kde_session({"KDE_FULL_SESSION": "true"})
    assert not kde_session({"XDG_CURRENT_DESKTOP": "GNOME"})


def test_kdialog_opens_one_image_in_native_kde_dialog(tmp_path):
    observed = []

    def runner(args, **kwargs):
        observed.append((args, kwargs))
        return completed(0, str(tmp_path / "wallpaper with spaces.jpg") + "\n")

    result = pick_kde_paths(
        str(tmp_path),
        title="Change wallpaper image",
        environment={"XDG_CURRENT_DESKTOP": "KDE", "XDG_SESSION_TYPE": "wayland"},
        which=lambda command: "/usr/bin/kdialog" if command == "kdialog" else None,
        runner=runner,
    )
    assert result == [str(tmp_path / "wallpaper with spaces.jpg")]
    args, kwargs = observed[0]
    assert args[:4] == ["/usr/bin/kdialog", "--title", "Change wallpaper image", "--getopenfilename"]
    assert args[4] == str(tmp_path)
    assert args[5] == IMAGE_FILTER
    assert "--multiple" not in args
    assert kwargs == {"capture_output": True, "text": True, "check": False}


def test_kdialog_supports_multiple_images_and_folder_choice(tmp_path):
    calls = []

    def runner(argv, **_kwargs):
        calls.append(argv)
        return completed(0, "/tmp/one.png\n/tmp/two.webp\n")

    options = dict(environment={"XDG_CURRENT_DESKTOP": "KDE"}, which=lambda _: "kdialog", runner=runner)
    assert pick_kde_paths(str(tmp_path), multiple=True, **options) == ["/tmp/one.png", "/tmp/two.webp"]
    assert calls[-1][-2:] == ["--multiple", "--separate-output"]
    runner_folder = lambda *_args, **_kwargs: completed(0, "/tmp/wallpaper folder\n")
    assert pick_kde_paths(str(tmp_path), folders=True, **(options | {"runner": runner_folder})) == [
        "/tmp/wallpaper folder"
    ]


def test_cancel_does_not_reopen_gtk_fallback(tmp_path):
    options = dict(environment={"XDG_CURRENT_DESKTOP": "KDE"}, which=lambda _: "kdialog")
    assert pick_kde_paths(str(tmp_path), runner=lambda *_a, **_kw: completed(1), **options) == []


def test_unavailable_kde_picker_explicitly_requests_toolkit_fallback(tmp_path):
    runner = lambda *_a, **_kw: pytest.fail("Should not start process without kdialog")
    assert (
        pick_kde_paths(
            str(tmp_path),
            environment={"XDG_CURRENT_DESKTOP": "GNOME"},
            which=lambda _: "kdialog",
            runner=runner,
        )
        is None
    )
    assert (
        pick_kde_paths(
            str(tmp_path),
            environment={"XDG_CURRENT_DESKTOP": "KDE"},
            which=lambda _: None,
            runner=runner,
        )
        is None
    )


def test_picker_errors_do_not_masquerade_as_cancellations(tmp_path):
    options = dict(environment={"XDG_CURRENT_DESKTOP": "KDE"}, which=lambda _: "kdialog")
    with pytest.raises(NativePickerError, match="exit code 2"):
        pick_kde_paths(str(tmp_path), runner=lambda *_a, **_kw: completed(2, stderr="display error"), **options)
    with pytest.raises(NativePickerError, match="could not be started"):
        pick_kde_paths(
            str(tmp_path),
            runner=lambda *_a, **_kw: (_ for _ in ()).throw(OSError("disconnected")),
            **options,
        )
