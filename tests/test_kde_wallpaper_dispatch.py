"""Headless integration checks for Plasma/Wayland wallpaper dispatch."""

import pytest


@pytest.mark.parametrize(
    ("desktop_session", "full_session", "session_desktop"),
    [
        ("plasma", None, None),
        ("plasmawayland", None, None),
        ("unknown", "true", None),
        ("unknown", None, "KDE"),
        (None, "true", None),
    ],
)
def test_kde_session_dispatch_uses_plasma(profile_modules, monkeypatch, desktop_session, full_session, session_desktop):
    _, wpproc = profile_modules
    for key, value in (
        ("DESKTOP_SESSION", desktop_session),
        ("KDE_FULL_SESSION", full_session),
        ("XDG_SESSION_DESKTOP", session_desktop),
    ):
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)

    monkeypatch.setattr(wpproc, "G_SET_COMMAND_STRING", "")
    monkeypatch.setattr(wpproc, "G_ACTIVE_PROFILE", "Work")

    plasma_calls = []
    fallback_calls = []
    monkeypatch.setattr(
        wpproc,
        "kdeplasma_actions",
        lambda outputfile, **kwargs: plasma_calls.append((outputfile, kwargs)),
    )
    monkeypatch.setattr(wpproc.subprocess, "run", lambda *args, **kwargs: fallback_calls.append((args, kwargs)))

    assert wpproc.running_kde()
    wpproc.set_wallpaper_linux("/tmp/my wallpaper.png", force=True)

    assert plasma_calls == [("/tmp/my wallpaper.png", {"force": True, "profile_name": "Work"})]
    assert fallback_calls == []


def test_kde_piecewise_dispatch_preserves_monitor_order(profile_modules, monkeypatch):
    _, wpproc = profile_modules
    monkeypatch.setenv("DESKTOP_SESSION", "plasmawayland")
    monkeypatch.setattr(wpproc, "IS_LINUX", True)
    monkeypatch.setattr(wpproc, "G_ACTIVE_PROFILE", "Work")

    calls = []
    monkeypatch.setattr(
        wpproc,
        "kdeplasma_actions",
        lambda outputfile, image_piece_list=None, **kwargs: calls.append(
            (outputfile, image_piece_list, kwargs)
        ),
    )
    images = ["/tmp/left.png", "/tmp/right.png"]

    assert wpproc.set_wallpaper_piecewise(images) == 0
    assert calls == [(None, images, {"profile_name": "Work"})]


def test_custom_wallpaper_command_overrides_kde_backend(profile_modules, monkeypatch):
    _, wpproc = profile_modules
    monkeypatch.setenv("DESKTOP_SESSION", "plasmawayland")
    monkeypatch.setattr(wpproc, "G_SET_COMMAND_STRING", "setter --file {image}")
    plasma_calls = []
    commands = []
    monkeypatch.setattr(wpproc, "kdeplasma_actions", lambda *args, **kwargs: plasma_calls.append(args))
    monkeypatch.setattr(
        wpproc.subprocess, "run", lambda command, **kwargs: commands.append((command, kwargs))
    )

    wpproc.set_wallpaper_linux("/tmp/wallpaper.png")

    assert not plasma_calls
    assert [command for command, _kwargs in commands] == [["setter", "--file", "/tmp/wallpaper.png"]]
