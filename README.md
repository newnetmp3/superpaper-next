# Superpaper Next

A community-maintained fork of [Superpaper](https://github.com/hhannine/superpaper), an advanced multi monitor wallpaper manager for **Linux** and **Windows** operating systems, with partial support (no hotkeys) for **MacOS**.

This fork focuses on KDE Plasma 6 support and improving the wallpaper selection experience. Development and testing is primarily done on Plasma 6; other desktop environments may work but are not actively tested.

![](https://raw.githubusercontent.com/hhannine/Superpaper/branch-resources/readme-banner.jpg)
![](https://raw.githubusercontent.com/hhannine/Superpaper/branch-resources/gui-screenshot.png)


## What's new in this fork

### KDE Plasma 6 & Activity Support
*Contributed by [FredworkLemmas](https://github.com/FredworkLemmas)*

- Per-activity wallpaper support: name your profiles to match your KDE Activities (case-sensitive) and the wallpaper will follow the active activity.
- Updated installation and dependency handling for Plasma 6 environments.

### Wallpaper Selection Improvements
- **Select to use**: the wallpaper you choose is remembered as the profile's current wallpaper. Opening the app or switching to a profile shows that wallpaper and no longer cycles to a random image on launch.
- **Click-to-preview**: clicking a wallpaper in the source list updates the preview panel immediately
- **Apply selected image**: when slideshow is off, "Apply" sets the specific image you selected instead of a random one from the rotation
- **Cycling on demand only**: the wallpaper changes when the slideshow timer fires or when you pick "Next Wallpaper" from the tray, never just because a profile was re-rendered
- **Consistent preview after save**: the preview shows your selected wallpaper instead of a random pick from a freshly shuffled list

### Wallpaper Studio UI

The native wxPython configuration window now opens in **Wallpaper Studio**,
with a larger persistent three-monitor preview and a left-side workspace
navigator. Switching workspaces preserves unsaved changes:

- **Wallpapers:** wallpaper sources, span mode, zoom and XY placement.
- **Displays:** real system display options, bezel/diagonal calibration, and
  profile-scoped advanced spanning offsets and groups.
- **Profiles:** a thumbnail gallery of saved profiles, with selection, create,
  duplicate and delete actions. The usual profile dropdown stays available
  across every workspace.
- **Processing:** cloud AI upscaling, optional local Anime4K GLSL shaders,
  local sharpening and live-adjustable brightness, contrast and saturation.
  Local adjustments require no Internet access or API credits.
- **Advanced:** slideshow playback and hotkeys.

Above the workspace, the preview offers **Split original / locally adjusted**
and **All monitors / Monitor N** views. The split compares the original source
against your locally adjusted preview; cloud AI enhancement and GLSL shader
effects are applied to the final wallpaper, rather than simulated in the
preview. Drag on a monitor image to adjust wallpaper positioning, or use the
zoom and horizontal/vertical sliders in Wallpapers.

The primary **Save & Apply** action is visible in the header and the bottom
action bar; **Apply** alone still tests without saving. Existing profile
settings and wallpaper engine behavior remain compatible with older profiles.

### Image Scaling & Position
- **Always fills the screen**: images are cover-fitted so there is never any letterboxing
- **Zoom**: zoom further into the image while it keeps filling the display
- **Horizontal / vertical positioning**: move the visible area within the image to place the content where you want it, with a live preview
- Saved per profile. These controls apply to a single fixed image, so they are reset and disabled while a profile's slideshow is enabled.

### Optional cloud AI image upscaling

Under **Image scaling & position**, enable **Cloud AI upscale (uploads images)**
for a profile that needs higher-quality wallpaper output. This is **off by default**
because it sends your source wallpaper image to a public third-party
[Hugging Face Space](https://huggingface.co/spaces/Nick088/Real-ESRGAN_Pytorch).
You do not need a local AI model, GPU, or paid account.

Install the small API client once using `uv pip install gradio_client`, or install
Superpaper with `pip install -e '.[upscale-cloud]'`. No model weights are downloaded.
Images are processed only when the render needs a larger source and successful
AI results are cached locally; repeated wallpaper changes reuse the cache.
Hugging Face public GPU capacity is limited and can be unavailable, so processing
can be delayed or refused. When this happens, Superpaper uses local Lanczos
resampling rather than failing to set the wallpaper. The original input files
are never modified. Very large files are not uploaded. Enabling cloud upscaling
may add latency to **Apply**, and slideshow profiles can consume the free quota.

This is optional, not a privacy-preserving upload: your image leaves your
machine. A public community Space is not a service with guaranteed uptime,
confidentiality, or long-term API compatibility. Avoid enabling it for private
or sensitive pictures. Already-cached outputs may be used while offline.

### Offline Anime4K GLSL image shaders

Superpaper can apply **Anime4K mpv-style GLSL shaders directly to still-image
wallpapers**, without cloud API calls or downloads of AI model weights.
Open **Image scaling & position > Local Anime4K shader > Import...** and select
an Anime4K `.tar.gz`, `.zip`, or individual `.glsl` shader file. The
importer accepts the accompanying Anime4K pack, safely copies its hooks into
the Superpaper configuration directory, and lets you select each imported
shader in the dropdown. You can also reuse shaders already present in your
local `~/.config/mpv/shaders` directory. Importing a pack is a one-time action.

Rendering uses **FFmpeg with the libplacebo filter and a Vulkan GPU driver**.
On Arch install the `ffmpeg` package and a suitable Vulkan driver (for AMD,
normally `vulkan-radeon`). Check support with
`ffmpeg -hide_banner -h filter=libplacebo`. Selection is saved in each
wallpaper profile, and the actual effect is applied locally to the rendered
wallpaper in single, grouped, perspective and per-monitor modes. The original
source image and cloud AI output cache remain unchanged; local shader results
are cached separately. Anime4K 2x upscalers use a double-sized working bitmap
and their original-image placement remains stable.

**The on-screen editor preview keeps the original image**, including zoom,
crop and local sharpening; it does not simulate the GPU shader. Use
**Save & Apply** to see the finished shader effect on your desktop. If FFmpeg,
libplacebo or Vulkan cannot run the shader, the wallpaper still applies with
the non-shader rendering path. Complex Anime4K CNN hooks may be slower than
simple shaders, and the filters are optimized for anime/illustration rather
than photographs. Shader pack files are stored locally; they are not uploaded.

The imported GLSL shader source includes its upstream license headers. These
files originate from the Anime4K project by bloc97 (MIT-licensed variants and
public-domain components).

### Cloud upscale quality settings

The public Real-ESRGAN Space exposes **2×, 4× and 8× image models**. Choose
**Auto (when needed)** to preserve Superpaper's default, credit-saving behavior:
the source image is enhanced only when display size/zoom requires it, selecting
2× or 4×. You can explicitly select 2×, 4× or 8× for a different model, even
when the source already covers the display. Larger models may require more time
and output memory; the 8× option is skipped for images whose resulting pixel
count would exceed the safety limit.

**Sharpen (local)** works with Cloud AI **on or off**, from 0 (unchanged) to 100.
It sharpens the original image when cloud AI is disabled, skipped or unavailable,
and sharpens the enhanced result when cloud AI succeeds. The preview always
renders the original image with the chosen sharpness so positioning stays
consistent. It is local Pillow processing, **not** a remote AI quality or denoise
slider. Changing sharpness never consumes new cloud credits, and neither does
disabling and re-enabling the same model: the model output is cached separately
from the sharpening choice. Switching between 2×, 4× and 8×
can require one cloud job per new choice, while returning to a cached choice
reuses its existing result. Both preferences are stored per profile.

### Matching the preview to an AI-upscaled wallpaper

**The wallpaper preview always uses the original image.** The applied wallpaper
uses the enhanced image when cloud AI upscaling is enabled and succeeds.
Both paths use the same source-relative zoom and horizontal/vertical placement;
the upscaled image does not create a new position coordinate system. EXIF
orientation is respected in the preview and in the final render, and cloud
outputs with an incompatible aspect ratio are rejected.

Click **Save & Apply** to persist the source image choice, zoom, placement,
span settings and AI upscaling preference to the profile before applying the
wallpaper. The existing **Apply** button is a temporary test; the ordinary
**Save** control also persists these settings without applying. Saved framing
continues to work when the cloud service is unavailable, and the original
wallpaper file is not modified.

### Native Wayland System Tray
- **Interactive tray on Wayland**: a native `StatusNotifierItem` tray icon replaces the legacy X11 tray, which appeared but was completely unclickable on modern Wayland desktops (notably KDE Plasma 6).
- **Full tray controls**: left-click opens the wallpaper settings, middle-click advances the wallpaper, and right-click shows the full menu with your profiles grouped under a **Profiles** submenu.
- Automatically used on Linux when a `StatusNotifierWatcher` is present, falling back to the classic tray otherwise.

### Display System Settings
- **Dedicated Save / Revert**: display sizes, bezels and physical positions are system-wide settings (shared by all profiles) and now have their own explicit Save and Revert instead of being written to disk silently.
- **Test before you commit**: "Apply" renders with your staged, unsaved system settings, so you can check bezels and sizes before saving them permanently.
- **Collapsible band**: the system settings live in a collapsible "Display system settings" band above the profile selector; its Save/Revert buttons gray out when nothing has changed.
- **Manual display sizes**: input display sizes manually with always-visible inch fields (the old "Override detected sizes" toggle is gone).

### Stability & Bug Fixes
- **Responsive while applying**: the GUI no longer freezes while a profile is being applied.
- **Outer bezels on apply**: outer bezels are now respected when the wallpaper is applied, matching the preview.
- **CLI `--profile`**: launching with `--profile <name>` now resolves the profile and applies the wallpaper on startup.
- **Robust file handling**: a missing wallpaper file can no longer cause an infinite loop, and an empty image list no longer crashes.
- Plus NumPy 1.24+ compatibility, Zorin OS detection, Python 3.13 install fixes, and various correctness/type-safety improvements.


## Planned Improvements

- **Visual wallpaper selector**: replace the file path list with an image grid/icon view, showing the path on hover
- **Interactive positioning**: drag-to-pan and scroll-to-zoom directly on the preview, in addition to the current sliders

Python 3.14 is the compatibility floor for development after v2.3.2. The v2.3.2 release is the stable cutoff for older Python environments.


## Features

### Novel features include
- Advanced wallpaper spanning options
  - Pixel density correction
  - Bezel correction
  - Perspective correction
  - These are described in more detail on this [wiki page](https://github.com/hhannine/superpaper/wiki/Wallpaper-spanning-with-advanced-options:-what-the-pixel-density-and-perspective-corrections-are-about).
- Extensive Linux support!
  - Aims to support all desktop environments
  - Span wallpaper on KDE and XFCE!
- Cross-platform: works on Linux, MacOS, and Windows
  - MacOS needs testing and packaging

### Features in detail
- Set a single image across all displays
- Set different image on every display
- Span images on groups of displays: one image on laptop screen and another spanned on two external monitors, for example.
- **Pixel density correction**: span an image flawlessly across displays of different shapes and sizes!
- **Bezel correction**: let the image continuously span behind your bezels.
- **Perspective correction**: span the image even more flawlessly!
- Manual pixel offsets for fine-tuning
- **System display settings**: display sizes, bezels and positions are shared by all profiles, with a dedicated Save/Revert and a "test before save" Apply.
- **Image zoom & positioning**: zoom into an image and move the visible area while it keeps filling the screen (single-image profiles)
- Slideshow with configurable file order from local sources
- Add wallpapers one by one or a folder at a time (no subfolders)
- Command-line interface
- Run a script after wallpaper change: [example script](./example-script/run-after-wp-change.py)
- Tray applet for slideshow control (native StatusNotifierItem tray on Wayland)
- Hotkey support for easy slideshow control (Only Linux and Windows)
- Align test tool to help fine tune your settings (Accessible only from GUI)

In the above banner photo you can see the PPI and bezel corrections in action. The left one is a 27" 4K display, and the right one is a 25" 1440p display.

Supported Linux desktop environments / window managers are:
- BSPWM (needs feh)
- Budgie
- Cinnamon
- Gnome
- i3 (needs feh)
- KDE
- LXDE & LXQt
- Mate
- Pantheon
- XFCE

and additionally there is support for
- supplying a [custom command](./docs/custom-command.md) to set the wallpaper

if support for your system of choice is not built-in.


### Support
If you find Superpaper useful please consider supporting the original developer:

- [Support via PayPal](https://www.paypal.me/superpaper/5)
- [Support via Github Sponsors](https://github.com/sponsors/hhannine)


## Installation

### Linux

An AppImage package is available on the [releases page](https://github.com/mauro-lanza/superpaper-next/releases).
The AppImage will run once you make it executable.

#### From source

1. Clone the repo
2. Create a virtualenv: `python -m venv .venv && source .venv/bin/activate`
3. Install Superpaper with its Linux extras: `pip install -e '.[gui,linux]'`
4. Run: `python -m superpaper`

For other installation options see: [installing on linux](./docs/installation-linux.md).

### Windows 10 & 11

A Windows installer and a portable package are available on the upstream [releases page](https://github.com/hhannine/superpaper/releases).

### MacOS

 You must install the dependencies and run the project, see [development-macos](./docs/development-macos.md).


## Usage

You can either:

- Open Superpaper as a graphical application
  - First run opens help and wallpaper settings. Disable 'show help at start' to run Superpaper silently in the background.
  - Control Superpaper in the background from the tray menu or with hotkeys.
- Call it from the [command-line](./docs/cli-usage.md)
  - Perspectives cannot be configured or used through the CLI currently.


## Troubleshooting

If you run into issues and Superpaper closes unexpectedly, you can either:
- Enable logging in the Settings.
- Manually enable logging in the 'general_settings' file by setting 'logging=true'.
- Run Superpaper from the command-line with the switch '--debug' to get debugging prints.
```sh
superpaper --debug
#or
./Superpaper-2.0.2-x86_64.AppImage --debug
```
Check the logs and come create an issue!


## Known issues

For some common problems and solutions, check [Known issues](./docs/known-issues.md).


## License

Superpaper is published under the [MIT License](./LICENSE).
