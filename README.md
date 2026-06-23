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

Do this in two stages: install `uv` to easily manage packages (you may already have it installed), then install `stackEDS-TW` itself.

### 1. Install `uv`

[`uv`](https://docs.astral.sh/uv/) is a single-binary Python installer; it pulls in the right Python version and other packages automatically. This step is optional, but it will make things run much more smoothly!

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

If you just want to keep up with whatever the latest version hosted on github is, install the app with:

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

## Update / uninstall

Running `upgrade` will fetch the latest version from github.
```sh
uv tool upgrade stackEDS-TW
uv tool uninstall stackEDS-TW
```


## Run

From anywhere:

```sh
stackEDS-TW
```

A folder picker opens - choose a directory containing your `.tif` element maps and the stacker launches. 

You can point a shortcut to this executable if you don't want to have to open the terminal and prefer to click an icon.

*Currently* it requires the maps to be in the form "[element].tiff". On launch a popup asks which kind of map you want to make, picking one of two fixed element sets:

- **Make False-colour Mineral Maps for silicates** — Al, Ca, Cr, Fe, K, Mg, Si, and Ti.
- **Make Mineral Maps for Zr- and phosphate phases** — Ca, Fe, and P.

It will attempt to select files with a consistent prefix/suffix naming pattern, and ask you to verify its selection. It uses the colour scheme described in [Joy et al. (2011)](http://www.lpi.usra.edu/meetings/leag2011/pdf/2007.pdf).

## Optional: Fiji (ImageJ) Integration:

If you have [Fiji](https://fiji.sc/) installed, each element card has an **Edit in Fiji** button for handing a single map off to Fiji. You can then perform any operation in Fiji (e.g. despeckle, background subtraction, manual masking, macros, etc.), and pull the result back into the stacker. This extends the capabilities beyond the basic set of options implemented natively within the app.

1. Click **Edit in Fiji** on an element card. The stacker writes that element's full-resolution map to a temporary 32-bit TIFF and opens it in Fiji.
2. Edit the image in Fiji, then **save it** (`File ▸ Save`, or `Ctrl`/`Cmd`+`S`).
3. Back in the stacker, click **↻** on the same element card to reload the edited map. Your Fiji edits will then be implemented.

Hitting **Reset** on the card discards the Fiji edit, and any other tweaks from within the app, and returns that element to the map originally loaded from disk. **Restore to Default** does the same for every element at once. The edited file is kept, so you can still re-pull it with **↻**.

The stacker looks for Fiji automatically (macOS, Windows and Linux); if it can't find it, it asks you to point to your install once and remembers the choice. You can also set a `FIJI_PATH` environment variable to the launcher (or `Fiji.app` on macOS).

### References
K. H. Joy, D. K. Ross, M. E. Zolensky, D. A. Kring (2011) Reconnaissance element mapping of lunar regolith breccias. *Annual Meeting of LEAG (Lunar Exploration Analysis Group)*, Abstract #2007.

