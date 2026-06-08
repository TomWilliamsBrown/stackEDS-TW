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

From inside the directory run the command:

```sh
uv tool install .
```

`uv` will create an isolated environment, install the dependencies (`opencv-python`, `numpy`, `tifffile`, `Pillow`, `PyQt5`, `imagecodecs`), and put a `stackEDS-TW` command on your `PATH`.

## Run

From anywhere:

```sh
stackEDS-TW
```

A folder picker opens — choose a directory containing your `.tif` element maps and the stacker launches.

## Update / uninstall

```sh
uv tool upgrade stackEDS-TW
uv tool uninstall stackEDS-TW
```

## Editing the code

If you want to edit the code for your uses, installing as:

```sh
uv tool install --editable .
```

Means the  `stackEDS-TW` command points directly at the source file.
