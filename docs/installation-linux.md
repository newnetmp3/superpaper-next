# Installation on Linux


## The easy portable way

AppImage packages for this fork are available on the [releases page](https://github.com/mauro-lanza/superpaper-next/releases). The AppImage will run once you make it executable.


## The recommended way

This will allow Superpaper to integrate the best with your system theme and icons.
See the screenshot in the Readme taken on Manjaro KDE.


### Step 1: Install pipx and wxPython 4.X:

Because of the differences between Linux distributions, the installation options differ:

- Arch / Manjaro: `sudo pacman -S python-pipx python-wxpython`
- Debian / Ubuntu and relatives: `sudo apt install pipx wxpython-tools python3-setuptools`
- Fedora : `sudo dnf install pipx python3-wxpython4`
- Older distros with no wxPython4 package: [wxpython.org](https://wxpython.org/pages/downloads/)
  - Install the wheel if available for your OS: CentOS, Debian, Fedora and Ubuntu

Using `pipx` simplifies management of the virtual environment in which Superpaper is installed, but is not a hard requirement.
You may use a manually created venv.


### Step 2: Install Superpaper Next

The upstream `superpaper` package on PyPI is still version 2.2.1 and does not
contain this fork's changes. Install this fork directly from GitHub; it needs
Python 3.14+:

```sh
pipx install --system-site-packages git+https://github.com/mauro-lanza/superpaper-next.git
```
On some Linux setups, you might need to log out and in, or restart to get the menu/launcher entry to show up.


### Native KDE image picker

On KDE Plasma, **Change Image**, **Add images**, and **Add folder** now launch
KDE's system file chooser through `kdialog`. On Arch Linux:

```sh
sudo pacman -S kdialog
```

On KDE without `kdialog`, or on other desktops/operating systems,
Superpaper uses wxPython's platform file dialog. The former `GenericDirCtrl`
wallpaper tree browser has been removed. Image selection updates only the
local preview; it does not apply or save the profile or contact Cloud AI.
