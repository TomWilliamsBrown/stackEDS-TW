# stackEDS-TW

Stack EDS element maps (TIFF) into a false-colour composite using a GUI.

## Example Usage

<div align="center">
  <video src="https://github.com/user-attachments/assets/38288771-a1d9-4844-8fe3-f322ed6fe7f5" poster="https://github.com/user-attachments/assets/b1218077-043e-4f86-9932-98acd4aac1bc" controls width="720">
    Your browser does not support the video tag.
    <a href="https://github.com/user-attachments/assets/38288771-a1d9-4844-8fe3-f322ed6fe7f5">Watch the demo</a>
  </video>
</div>

## Install

### 1. Install `uv`

`uv` is a single-binary Python installer; it pulls in the right Python version automatically (https://docs.astral.sh/uv/getting-started/installation/). This step is optional, but it will make things run more smoothly if you don't want to install dependencies manually!

- **macOS / Linux**
  ```sh
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```
  or...
  ```sh
  brew install uv
  ```
  which is likely the easiest option.

- **Windows (PowerShell)**
  ```powershell
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  ```

Restart your shell (or `source $HOME/.local/bin/env` on Unix) so `uv` is on `PATH`. 

### 2. Install the app

If you just want to keep up with whatever is on github, install from anywhere with:

```sh
uv tool install git+https://github.com/TomWilliamsBrown/stackEDS-TW.git
```

`uv` will create an isolated environment, install the dependencies (`opencv-python`, `numpy`, `tifffile`, `Pillow`, `PyQt5`, `imagecodecs`), and put a `stackEDS-TW` command on your `PATH`.

If you want to be able to edit the code locally, clone the repo first, then install in editable mode from inside the directory:

```sh
git clone https://github.com/TomWilliamsBrown/stackEDS-TW.git
cd stackEDS-TW
uv tool install --editable .
```

This will mean that the `stackEDS-TW` command points directly at the source file on your system.

## Run

From anywhere:

```sh
stackEDS-TW
```

A folder picker opens - choose a directory containing your `.tif` element maps and the stacker launches. 

You can point a shortcut to this executable if you don't want to have to open the terminal and prefer to click an icon.

*Currently* it requires the maps to be in the form "[element].tiff", where [element] is fixed as 
Al, Ca, Cr, Fe, K, Mg, Si, and Ti, although it does give the option for the files to have a consistent prefix/suffix naming pattern. Right now you can only change the elements/naming pattern if you have the editable version, as they are hardcoded.

## Update / uninstall

Running `upgrade` will fetch the latest version from github.
```sh
uv tool upgrade stackEDS-TW
uv tool uninstall stackEDS-TW
```
