# stackEDS-TW

Stack EDS element maps (TIFF) into a false-colour composite.

## Install

### 1. Install `uv`

`uv` is a single-binary Python installer; it pulls in the right Python version automatically.

- **macOS / Linux**
  ```sh
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```
- **Windows (PowerShell)**
  ```powershell
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  ```

Restart your shell (or `source $HOME/.local/bin/env` on Unix) so `uv` is on `PATH`. I suggest Googling `uv` before installing to make sure this download path is up to date. `Homebrew` is likely the easiest option for installation if you are on macOS and you have it installed.

### 2. Install the app

If you want to be able to edit the code locally, clone the repo first, then install in editable mode from inside the directory:

```sh
git clone https://github.com/TomWilliamsBrown/stackEDS-TW.git
cd stackEDS-TW
uv tool install --editable .
```

This will mean that the `stackEDS-TW` command points directly at the source file on your system.

If you just want to keep up with whatever is on github, install from anywhere with:

```sh
uv tool install git+https://github.com/TomWilliamsBrown/stackEDS-TW.git
```

`uv` will create an isolated environment, install the dependencies (`opencv-python`, `numpy`, `tifffile`, `Pillow`, `PyQt5`, `imagecodecs`), and put a `stackEDS-TW` command on your `PATH`.

## Run

From anywhere:

```sh
stackEDS-TW
```

A folder picker opens — choose a directory containing your `.tif` element maps and the stacker launches.

## Update / uninstall

Running `upgrade` will fetch the latest version from github.
```sh
uv tool upgrade stackEDS-TW
uv tool uninstall stackEDS-TW
```
