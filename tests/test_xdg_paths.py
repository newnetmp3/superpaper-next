"""Regression coverage for fresh Linux XDG config/cache directory creation."""

import os

from superpaper import sp_paths


def test_creates_missing_cache_base_and_superpaper_dir(tmp_path, monkeypatch):
    cache_home = tmp_path / "new" / "cache"
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_home))

    result = sp_paths.xdg_path_setup("XDG_CACHE_HOME", str(tmp_path / "fallback"))

    assert result == str(cache_home / "superpaper")
    assert os.path.isdir(result)


def test_missing_default_cache_parent_is_created(tmp_path, monkeypatch):
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    default_cache = tmp_path / "home" / ".cache"

    result = sp_paths.xdg_path_setup("XDG_CACHE_HOME", str(default_cache))

    assert result == str(default_cache / "superpaper")
    assert os.path.isdir(result)


def test_relative_xdg_base_uses_absolute_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", "relative/cache")
    fallback = tmp_path / "fallback"

    result = sp_paths.xdg_path_setup("XDG_CACHE_HOME", str(fallback))

    assert result == str(fallback / "superpaper")
    assert os.path.isdir(result)
