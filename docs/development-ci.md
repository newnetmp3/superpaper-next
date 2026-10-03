# Automated checks

The repository uses GitHub Actions for three complementary checks:

- **Lint and functionality:** Ruff lint and formatting, `ty` type analysis, and headless
  `pytest` tests. These include KDE Plasma wallpaper-dispatch tests that stub
  the system calls rather than changing a real desktop wallpaper.
- **Distribution smoke test:** Build a Python wheel, install it into an isolated
  Python 3.14 environment, verify the packaged icons, and run the CLI help entry
  point. This catches missing package assets and startup import errors.
- **CodeQL:** Analyze Python code using the extended security query set on PRs,
  pushes to `master`/`ci/**`, and weekly on Sundays. Alerts appear under
  GitHub's Security / Code scanning when code scanning is enabled.

CI and CodeQL can also be started with **Run workflow** in GitHub Actions.

## Running checks locally

With `uv` installed and Python 3.14 available:

```sh
uv sync --frozen --group dev
uv run ruff check
uv run ruff format --check
uv run ty check
uv run pytest -q --durations=10
uv build --wheel --out-dir dist
```

These checks intentionally run without wxPython or KDE/DBus. They do **not**
replace visual and interactive testing on KDE Plasma/Wayland.
