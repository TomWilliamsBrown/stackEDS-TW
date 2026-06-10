"""Stack individual EDS element maps (TIFF) into a false-colour composite.

Each element has an adjustable processing pipeline:
    smooth -> black level -> gamma -> brightness (gain) -> colour
"""

import json
import os
import re
import sys
from pathlib import Path
from dataclasses import dataclass

import cv2
import numpy as np
import tifffile
from PIL import ImageColor

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import (
    QApplication, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QSizePolicy, QVBoxLayout,
    QWidget,
)

# ---------------- CONFIG ---------------- #

# Where the directory picker opens by default.
DEFAULT_IMAGE_DIR = ""


@dataclass
class Element:
    """One element map and its default processing settings."""
    name: str
    colour: str
    brightness: float = 5.0  # gain
    smoothing: float = 2.0  # Gaussian sigma (full-res pixels)
    black: float = 0.0  # black-level threshold, [0, 1]
    white: float = 1.0  # white-level threshold, [0, 1]
    gamma: float = 1.0  # contrast curve
    filename: str = ""  # defaults to `name` (i.e. "<name>.tif")

    def __post_init__(self):
        if not self.filename:
            self.filename = self.name

    @property
    def rgb(self):
        """Colour as an RGB array normalised to [0, 1]."""
        return np.array(ImageColor.getrgb(self.colour), dtype=np.float32) / 255.0

# Here I am using Katie and Josh's preferred colour scheme.
# The file for each element is currently just its chemical symbol, with fixed elements.
# TODO: Adjust so user can enter their own file format/element without needing to
# hardcode it or use the current fail fallback.
ELEMENTS = [
    Element("Al", "white", brightness=5, smoothing=1),
    Element("Ca", "yellow", brightness=5, smoothing=1),
    Element("Cr", "orange", brightness=5, smoothing=1),
    Element("Fe", "red", brightness=5, smoothing=1),
    Element("K", "cyan", brightness=5, smoothing=1),
    Element("Mg", "green", brightness=5, smoothing=1),
    Element("Si", "blue", brightness=5, smoothing=1),
    Element("Ti", "magenta", brightness=5, smoothing=1),
]

PREVIEW_SCALE = 0.25  # downsample factor for the per-element thumbnails
COMBINED_SCALE = 0.5  # higher-res downsample used only for the combined view
GRID_COLUMNS = 4  # element panels per row
PREVIEW_SIZE = 170  # px, per-element thumbnail
CARD_PAD = 10  # px, inner padding of each card
CARD_WIDTH = PREVIEW_SIZE + 2 * CARD_PAD  # card hugs the thumbnail width
TITLE_H = 22  # px, element/combined title height (kept equal so the
#     big preview's top lines up with the thumbnails)
COMBINED_MIN = 420  # px, minimum side of the (responsive) combined preview
HIST_W = PREVIEW_SIZE  # px, histogram width
HIST_H = 20  # px, histogram height
EXPORT_MAX = 65535  # 16-bit TIFF export range

# adjustable-control ranges
BRIGHTNESS_MAX = 100.0
MAX_SMOOTHING = 10.0
GAMMA_MIN, GAMMA_MAX = 0.2, 5.0

# Zoom/pan: smallest fraction of the image that can fill the view
MIN_ROI_SPAN = 0.01
ZOOM_STEP = 1.2

# ---------------- THEME ----------------- #
# This is a Qt Style Sheet. It only restyles widgets; none
# of the image-processing code depends on it.

BG_WINDOW = "#16171a"
BG_CARD = "#1f2124"
BG_SUNKEN = "#0c0d10"  # thumbnail / histogram wells
BORDER = "#2c2e33"
BORDER_HOVER = "#3f434a"
FIELD_BG = "#26282d"
FIELD_BORDER = "#34373d"
ACCENT = "#4a9eff"
TEXT = "#e6e6e8"
TEXT_MUTED = "#a6a6a6"

# 0 = invisible, 1 = solid. Controls text/border transparency on missing-file cards.
MISSING_ALPHA = 0.40

STYLESHEET = f"""
QWidget#viewer {{
    background: {BG_WINDOW};
}}
QWidget {{
    color: {TEXT};
    font-size: 13px;
}}
QLabel {{
    background: transparent;
    color: {TEXT_MUTED};
}}
QFrame#card {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 12px;
}}
QLabel#thumb, QLabel#hist {{
    background: {BG_SUNKEN};
    border: 1px solid {BORDER};
    border-radius: 8px;
}}
QLineEdit {{
    background: {FIELD_BG};
    border: 1px solid {FIELD_BORDER};
    border-radius: 7px;
    padding: 3px 8px;
    color: #f0f0f2;
    selection-background-color: {ACCENT};
    font-family: "JetBrainsMono Nerd Font", "Menlo", "Consolas", monospace;
}}
QLineEdit:focus {{
    border: 1px solid {ACCENT};
}}
QPushButton {{
    background: {FIELD_BG};
    border: 1px solid {FIELD_BORDER};
    border-radius: 8px;
    padding: 6px 12px;
    color: {TEXT};
}}
QPushButton:hover {{
    background: #2f3138;
    border-color: {BORDER_HOVER};
}}
QPushButton:pressed {{
    background: #202227;
}}
QPushButton#primary {{
    background: #2563b0;
    border: 1px solid #2f6fc0;
    color: #ffffff;
}}
QPushButton#primary:hover {{
    background: #2d6fc2;
}}
QPushButton#primary:pressed {{
    background: #1f5599;
}}
QPushButton#toggle {{
    background: transparent;
    border: 1px solid {FIELD_BORDER};
    border-radius: 5px;
    padding: 0;
    color: {TEXT_MUTED};
    font-size: 12px;
    font-weight: 700;
}}
QPushButton#toggle:hover {{
    border-color: {BORDER_HOVER};
}}
QPushButton#toggle:checked {{
    background: #1f3a5c;
    border: 1px solid {ACCENT};
    color: #dceaff;
}}
"""


def _hex(rgb):
    """('r','g','b') floats in [0,1] -> '#rrggbb'."""
    return "#%02x%02x%02x" % tuple(int(round(max(0.0, min(1.0, c)) * 255)) for c in rgb)


def _rgba(hex_color, alpha):
    """'#rrggbb' + alpha float -> 'rgba(r, g, b, a)' for Qt stylesheets."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r}, {g}, {b}, {alpha})"


def title_colour(rgb):
    """Brighten an element's colour just enough to read on the dark cards.
    The true colour still shows in the small swatch; this is only for the text.
    """
    r, g, b = (float(c) for c in rgb)
    lum = 0.2126 * r + 0.7154 * g + 0.0721 * b
    # (This uses RGB weighting from:
    # https://scikit-image.org/docs/stable/auto_examples/color_exposure/plot_rgb_to_gray.html
    target = 0.66
    if lum < target:
        t = max(0.0, min((target - lum) / (1.0 - lum if lum < 1.0 else 1.0), 0.72))
        r, g, b = (c + (1.0 - c) * t for c in (r, g, b))
    return _hex((r, g, b))


# ---------- IMAGE PROCESSING ------------ #

def load_and_preprocess(path):
    """Load a TIFF and normalise it to [0, 1]"""

    img = tifffile.imread(path).astype(np.float32)

    # EDS element maps should be single-channel. If a TIFF arrives with extra
    # channels (saved as RGB/RGBA by accident), the channels are hopefully
    # identical — take channel 0 and flag it so unexpected inputs are visible.
    # TODO: Test with more image files, add checks for file format.
    if img.ndim == 3:
        if img.shape[2] == 4:
            img = img[:, :, :3]
        print(f"WARNING: {Path(path).name} is multi-channel; using channel 0.")
        img = img[:, :, 0]

    # Safety fallback for unexpected formats
    while img.ndim > 2:
        img = img[0]

    # Normalise
    img -= img.min()
    if img.max() > 0:
        img /= img.max()

    return img


def downsample(img, scale=PREVIEW_SCALE):
    return cv2.resize(img, (0, 0), fx=scale, fy=scale)


def smooth(gray, sigma, scale=1.0):
    """Gaussian blur. `scale` lets the preview match full-res visually."""
    s = sigma * scale
    return cv2.GaussianBlur(gray, (0, 0), s) if s > 0 else gray


def tonemap(gray, black, white, gamma):
    """Stretch [black, white] to [0, 1] then apply gamma."""
    g = np.clip((gray - black) / max(white - black, 1e-6), 0, 1)
    return g ** gamma if gamma != 1.0 else g


def colorize(gray, rgb, brightness):
    """Tint a greyscale map (HxW, [0,1]) with an RGB colour scaled by brightness."""
    return np.clip((gray * brightness)[..., None] * rgb, 0, 1)


def render_layer(src, w, scale):
    """Run a source map through one element's full pipeline -> colour layer.
    `scale` is the source's downsample factor vs full-res
    """
    gray = tonemap(smooth(src, w.smoothing, scale), w.black, w.white, w.gamma)
    return colorize(gray, w.rgb, w.brightness)


def histogram_image(gray, black, white, colour=None, bins=64, width=HIST_W, height=HIST_H):
    """Render a log-scaled intensity histogram with black and white markers.
    Bars are tinted with the element's colour (dimmed); the threshold markers
    are bright vertical lines (black = red-orange, white = pale blue).
    """
    counts, _ = np.histogram(gray, bins=bins, range=(0.0, 1.0))
    counts = np.log1p(counts.astype(np.float32))
    peak = counts.max()

    bar = (np.asarray(colour, dtype=np.float32) * 0.85
           if colour is not None else np.array([0.75, 0.75, 0.75], np.float32))

    img = np.zeros((height, width, 3), dtype=np.float32)
    if peak > 0:
        for i, c in enumerate(counts):
            h = int(c / peak * (height - 1))
            x0 = int(i * width / bins)
            x1 = int((i + 1) * width / bins)
            img[height - h:height, x0:x1, :] = bar

    img[:, int(np.clip(black, 0, 1) * (width - 1)), :] = (1.0, 0.25, 0.2)
    img[:, int(np.clip(white, 0, 1) * (width - 1)), :] = (0.65, 0.8, 1.0)
    return img


def crop_to_roi(img, roi):
    """Slice a 2D or 3D image to a normalised ROI = (x0, y0, x1, y1) in [0, 1].
    Each output dimension is guaranteed to be at least one pixel.
    """
    x0, y0, x1, y1 = roi
    h, w = img.shape[:2]
    i0 = max(0, min(h - 1, int(round(y0 * h))))
    i1 = max(i0 + 1, min(h, int(round(y1 * h))))
    j0 = max(0, min(w - 1, int(round(x0 * w))))
    j1 = max(j0 + 1, min(w, int(round(x1 * w))))
    return img[i0:i1, j0:j1]


def to_qimage(img):
    """Convert a float RGB image in [0, 1] to a QImage.
    The QImage shares `img8`'s buffer; callers copy it (via QPixmap.fromImage)
    before the local array is garbage-collected.
    """
    img8 = np.ascontiguousarray((img * 255).astype(np.uint8))
    h, w, _ = img8.shape
    return QImage(img8.data, w, h, 3 * w, QImage.Format_RGB888)


# ---------- GUI: A NUMERIC ENTRY -------- #

class Control(QWidget):
    """A labelled numeric entry box, clamped to [vmin, vmax].
    Label sits left, value right. You can add a new dial by adding another Control instance.
    Out-of-range entries snap to the limit.
    """

    def __init__(self, name, vmin, vmax, value, on_change, fmt="{:.3f}"):
        super().__init__()
        self.name = name
        self.vmin, self.vmax = vmin, vmax
        self.fmt = fmt
        self.on_change = on_change
        self.default = value
        self._value = self._clamp(value)

        self.label = QLabel(name)

        self.box = QLineEdit()
        self.box.setFixedWidth(72)
        self.box.setAlignment(Qt.AlignRight)
        self.box.editingFinished.connect(self._changed)

        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.label)
        layout.addStretch(1)
        layout.addWidget(self.box)
        self.setLayout(layout)
        self._refresh_box()

    def _clamp(self, v):
        return min(max(v, self.vmin), self.vmax)

    def value(self):
        return self._value

    def set_value(self, v):
        self._value = self._clamp(v)
        self._refresh_box()

    def reset(self):
        self.set_value(self.default)

    def _changed(self):
        try:
            v = float(self.box.text())
        except ValueError:
            self._refresh_box()  # revert to the last valid value
            return
        self._value = self._clamp(v)
        self._refresh_box()  # show the clamped/formatted value
        self.on_change()

    def _refresh_box(self):
        self.box.blockSignals(True)
        self.box.setText(self.fmt.format(self._value))
        self.box.blockSignals(False)


# ---------- GUI: PER-ELEMENT PANEL ------ #

class ElementWidget(QFrame):
    def __init__(self, element, preview_img, combined_img, viewer, available=True):
        super().__init__()
        self.setObjectName("card")
        self.setFixedWidth(CARD_WIDTH)
        self.viewer = viewer
        self.name = element.name
        self.preview_img = preview_img
        self.combined_img = combined_img
        self.rgb = element.rgb
        self.available = available

        # Caches so edits don't redo work (might mean computers with little RAM struggle on large files?):
        #   _sm_*     : the Gaussian-blurred map, kept until `smoothing` changes
        #   _layer    : this element's colour contribution to the combined image
        #   _thumb_rgb: the full thumbnail-scale colour image, re-cropped on
        #               every ROI change without redoing the processing pipeline
        self._sm_p = self._sm_c = None
        self._sm_p_sigma = self._sm_c_sigma = None
        self._layer = None
        self._thumb_rgb = None

        # title: colour swatch + tinted element name + include/exclude toggle
        self._swatch_on = _hex(element.rgb)
        self._title_on = title_colour(element.rgb)
        self.swatch = QLabel()
        self.swatch.setFixedSize(10, 10)
        self.name_label = QLabel(element.name)

        # top-right toggle: include this element in the combined image (default on)
        self.include_btn = QPushButton("\u2713")  # check mark
        self.include_btn.setObjectName("toggle")
        self.include_btn.setCheckable(True)
        self.include_btn.setChecked(True)
        self.include_btn.setFixedSize(18, 18)
        self.include_btn.setCursor(Qt.PointingHandCursor)
        self.include_btn.setToolTip("Include this element in the combined image")
        self.include_btn.toggled.connect(self._on_toggle)

        title = QHBoxLayout()
        title.setContentsMargins(2, 0, 0, 0)
        title.setSpacing(7)
        title.addWidget(self.swatch)
        title.addWidget(self.name_label)
        title.addStretch(1)
        title.addWidget(self.include_btn)
        title_row = QWidget()
        title_row.setFixedHeight(TITLE_H)
        title_row.setLayout(title)
        self._apply_included_style()

        self.thumb = QLabel()
        self.thumb.setObjectName("thumb")
        self.thumb.setFixedSize(PREVIEW_SIZE, PREVIEW_SIZE)
        self.thumb.setAlignment(Qt.AlignCenter)

        self.hist = QLabel()
        self.hist.setObjectName("hist")
        self.hist.setFixedSize(HIST_W, HIST_H)
        self.hist.setScaledContents(True)

        refresh = self.notify_change
        self.brightness_c = Control("Brightness", 0.0, BRIGHTNESS_MAX, element.brightness, refresh)
        self.black_c = Control("Black level", 0.0, 1.0, element.black, refresh)
        self.white_c = Control("White level", 0.0, 1.0, element.white, refresh)
        self.gamma_c = Control("Gamma", GAMMA_MIN, GAMMA_MAX, element.gamma, refresh)
        self.smooth_c = Control("Smoothing", 0.0, MAX_SMOOTHING, element.smoothing, refresh, fmt="{:.1f}")
        self.smooth_c.setToolTip("Gaussian smoothing sigma (in full-resolution pixels). Enter 0 to disable smoothing for this element.")
        self.controls = [self.brightness_c, self.black_c, self.white_c, self.gamma_c, self.smooth_c]

        self.reset_btn = QPushButton("Reset")
        self.reset_btn.clicked.connect(self.reset)

        layout = QVBoxLayout()
        layout.setSpacing(4)
        layout.setContentsMargins(CARD_PAD, CARD_PAD, CARD_PAD, CARD_PAD)
        layout.addWidget(title_row)
        layout.addWidget(self.thumb, alignment=Qt.AlignCenter)
        layout.addWidget(self.hist, alignment=Qt.AlignCenter)
        layout.addSpacing(2)
        for c in self.controls:
            layout.addWidget(c)
        layout.addSpacing(2)
        layout.addWidget(self.reset_btn)
        self.setLayout(layout)

        if not self.available:
            self.include_btn.blockSignals(True)
            self.include_btn.setChecked(False)
            self.include_btn.blockSignals(False)
            self.include_btn.setEnabled(False)
            self.include_btn.setToolTip(
                f"{element.name}.tif not found in the chosen folder")
            for c in self.controls:
                c.setEnabled(False)
            self.reset_btn.setEnabled(False)
            self._apply_included_style()

    # current values
    @property
    def brightness(self):
        return self.brightness_c.value()

    @property
    def black(self):
        return self.black_c.value()

    @property
    def white(self):
        return self.white_c.value()

    @property
    def gamma(self):
        return self.gamma_c.value()

    @property
    def smoothing(self):
        return self.smooth_c.value()

    @property
    def included(self):
        """Whether this element is summed into the combined image."""
        return self.include_btn.isChecked()

    def smoothed_preview(self):
        """Blurred thumbnail-scale map, re-blurred only when smoothing changes."""
        if self._sm_p is None or self._sm_p_sigma != self.smoothing:
            self._sm_p = smooth(self.preview_img, self.smoothing, PREVIEW_SCALE)
            self._sm_p_sigma = self.smoothing
        return self._sm_p

    def smoothed_combined(self):
        """Blurred combined-scale map, re-blurred only when smoothing changes."""
        if self._sm_c is None or self._sm_c_sigma != self.smoothing:
            self._sm_c = smooth(self.combined_img, self.smoothing, COMBINED_SCALE)
            self._sm_c_sigma = self.smoothing
        return self._sm_c

    def layer(self):
        """This element's cached colour contribution to the combined image."""
        return self._layer

    def recompute(self):
        """Refresh this element's thumbnail, histogram and cached layer."""
        if not self.available:
            return
        sm_p = self.smoothed_preview()
        gray = tonemap(sm_p, self.black, self.white, self.gamma)
        self._thumb_rgb = colorize(gray, self.rgb, self.brightness)
        self._render_thumb()
        self.hist.setPixmap(QPixmap.fromImage(
            to_qimage(histogram_image(sm_p, self.black, self.white, self.rgb))))

        sm_c = self.smoothed_combined()
        gray_c = tonemap(sm_c, self.black, self.white, self.gamma)
        self._layer = colorize(gray_c, self.rgb, self.brightness)

    def _render_thumb(self):
        """Crop the cached thumbnail RGB to the shared ROI and show it."""
        if self._thumb_rgb is None:
            return
        cropped = crop_to_roi(self._thumb_rgb, self.viewer.view_roi)
        pixmap = QPixmap.fromImage(to_qimage(cropped))
        self.thumb.setPixmap(pixmap.scaled(
            self.thumb.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def notify_change(self):
        """A control for this element changed: recompute just this element."""
        self.recompute()
        self.viewer.refresh_combined()

    def _on_toggle(self):
        # Inclusion changed but the layer is unchanged, so just re-sum.
        self._apply_included_style()
        self.viewer.refresh_combined()

    def _apply_included_style(self):
        """Brighten the title when included; dim it when excluded.
        Cards for missing files get an extra faded card/well treatment.
        """
        if self.included:
            self.swatch.setStyleSheet(
                f"background: {self._swatch_on}; border-radius: 2px;")
            self.name_label.setStyleSheet(
                f"color: {self._title_on}; font-weight: 600;")
        else:
            self.swatch.setStyleSheet(
                f"background: {BORDER_HOVER}; border-radius: 2px;")
            self.name_label.setStyleSheet(
                f"color: {TEXT_MUTED}; font-weight: 600;")

        if not self.available:
            # Fade every widget if the element isn't found
            muted_text = _rgba(TEXT_MUTED, MISSING_ALPHA)
            muted_border = _rgba(BORDER, MISSING_ALPHA)
            muted_field = _rgba(BG_WINDOW, MISSING_ALPHA)
            self.setStyleSheet(
                f"QFrame#card {{ background: {BG_WINDOW};"
                f" border: 1px solid {BORDER}; border-radius: 12px; }}"
                f" QLabel#thumb, QLabel#hist {{ background: {BG_WINDOW};"
                f" border: 1px solid {BORDER}; border-radius: 8px; }}"
                f" QLabel {{ color: {muted_text}; }}"
                f" QLineEdit {{ color: {muted_text}; background: {muted_field};"
                f" border: 1px solid {muted_border}; border-radius: 7px;"
                f" padding: 3px 8px; }}"
                f" QPushButton {{ color: {muted_text}; background: {muted_field};"
                f" border: 1px solid {muted_border}; border-radius: 8px;"
                f" padding: 6px 12px; }}"
                f" QPushButton#toggle {{ background: transparent;"
                f" color: {muted_text}; border: 1px solid {muted_border};"
                f" border-radius: 5px; padding: 0; }}")

    def reset(self):
        for c in self.controls:
            c.reset()
        self.notify_change()


# ---------- FILE DISCOVERY -------------- #

def resolve_filenames(image_dir, names):
    """Work out which on-disk TIFF map to use for each element symbol.

    Preference order:
      1. The default "<symbol>.tif" naming (e.g. "Al.tif").
      2. A single pattern that wraps the same text before and/or
         after every symbol, e.g. "User1-Al_K.tif" / "User1-Ca_K.tif".

     Using (2) because the default AZtec naming convention is different to my archiving.

    Returns one of:
      ("default",     {symbol: filename})
      ("alternative", {symbol: filename}, prefix, suffix)
      ("none",        {})

    """
    try:
        entries = os.listdir(image_dir)
    except OSError:
        return ("none", {})

    # (stem, filename) for every TIFF, where stem is the name minus extension.
    stems = []
    for f in entries:
        root, ext = os.path.splitext(f)
        if ext.lower() in (".tif", ".tiff"):
            stems.append((root, f))

    # 1) Default: a file whose stem is exactly the symbol.
    stem_to_file = {}
    for stem, f in stems:
        stem_to_file.setdefault(stem, f)
    default = {name: stem_to_file[name] for name in names if name in stem_to_file}
    if default:
        return ("default", default)

    # 2) Otherwise look for a consistent prefix/suffix wrapped around symbols.
    #    Prefix must match exactly across elements. Suffix is normalised so that
    #    EDS line-family letters (K/L/M, e.g. "_K_alpha" vs "_L_alpha") and a
    #    trailing numeric tail (e.g. "_1" or "_1,2") are treated as equivalent.
    candidates = {}
    for name in names:
        for stem, f in stems:
            start = 0
            while True:
                idx = stem.find(name, start)
                if idx < 0:
                    break
                prefix = stem[:idx]
                suffix = stem[idx + len(name):]
                key = (prefix, _normalise_suffix(suffix))
                candidates.setdefault(key, {}).setdefault(name, f)
                start = idx + 1

    best = None
    for (prefix, norm_suffix), matched in candidates.items():
        # The same pattern must hold for at least two elements
        if len(matched) < 2:
            continue
        # Most elements wins; then prefer the simplest (shortest) affixes;
        # finally fall back to lexicographic order so the choice is stable.
        key = (len(matched), -(len(prefix) + len(norm_suffix)), prefix, norm_suffix)
        if best is None or key > best[0]:
            best = (key, prefix, norm_suffix, matched)

    if best is None:
        return ("none", {})
    return ("alternative", best[3], best[1], best[2])


_LINE_LETTER_RE = re.compile(r"(?<![A-Za-z])[KLM](?![A-Za-z])")
_TRAILING_NUM_RE = re.compile(r"_\d+(?:,\d+)*$")


def _normalise_suffix(suffix):
    """Collapse the parts of a suffix that are allowed to vary per element.

    inc. X-ray emission line in the filenams
    """
    s = _TRAILING_NUM_RE.sub("", suffix)
    s = _LINE_LETTER_RE.sub("<line>", s)
    return s


# ---------- GUI: COMBINED VIEW ---------- #

class CombinedView(QLabel):
    """QLabel that captures wheel + drag and routes them to the viewer's ROI.

    This is the only widget that accepts zoom/pan input. All element thumbnails
    stay plain QLabels and re-render passively from the shared ROI.
    """

    def __init__(self, viewer):
        super().__init__()
        self.viewer = viewer
        self._drag_anchor = None  # (cursor_pos, starting_roi, displayed_rect)
        self.setCursor(Qt.OpenHandCursor)

    def _displayed_rect(self):
        """Pixel rect inside the label that the pixmap fills.
        Returns (offset_x, offset_y, drawn_w, drawn_h) or None when no pixmap.
        """
        pm = self.pixmap()
        if pm is None or pm.isNull():
            return None
        size = pm.size().scaled(self.size(), Qt.KeepAspectRatio)
        dw, dh = size.width(), size.height()
        if dw <= 0 or dh <= 0:
            return None
        ox = (self.width() - dw) / 2.0
        oy = (self.height() - dh) / 2.0
        return ox, oy, dw, dh

    def _cursor_to_norm(self, pos):
        """Map a label cursor position to normalised image coords in the full image.
        Returns None if the cursor is outside the drawn pixmap.
        """
        rect = self._displayed_rect()
        if rect is None:
            return None
        ox, oy, dw, dh = rect
        x = pos.x() - ox
        y = pos.y() - oy
        if not (0 <= x <= dw and 0 <= y <= dh):
            return None
        fx, fy = x / dw, y / dh
        x0, y0, x1, y1 = self.viewer.view_roi
        return x0 + fx * (x1 - x0), y0 + fy * (y1 - y0)

    def _zoom_around(self, anchor_norm, factor):
        """Shrink/grow the ROI by `factor`, keeping `anchor_norm` fixed on screen."""
        nx, ny = anchor_norm
        x0, y0, x1, y1 = self.viewer.view_roi
        fx = (nx - x0) / (x1 - x0)
        fy = (ny - y0) / (y1 - y0)
        new_w = (x1 - x0) * factor
        new_h = (y1 - y0) * factor
        new_x0 = nx - fx * new_w
        new_y0 = ny - fy * new_h
        self.viewer.set_roi((new_x0, new_y0, new_x0 + new_w, new_y0 + new_h))

    def _pan_pixels(self, dx_px, dy_px):
        """Shift the ROI by a pixel offset measured in the displayed pixmap."""
        rect = self._displayed_rect()
        if rect is None:
            return
        _, _, dw, dh = rect
        x0, y0, x1, y1 = self.viewer.view_roi
        w, h = x1 - x0, y1 - y0
        # Content follows the gesture: scrolling/dragging right reveals image to the left.
        nx0 = x0 - dx_px / dw * w
        ny0 = y0 - dy_px / dh * h
        self.viewer.set_roi((nx0, ny0, nx0 + w, ny0 + h))

    def wheelEvent(self, event):
        # Plain trackpad scroll pans; any modifier (Option/Cmd/Ctrl) makes it zoom.
        zoom_modifier = event.modifiers() & (
            Qt.AltModifier | Qt.ControlModifier | Qt.MetaModifier)
        pixel_delta = event.pixelDelta()

        if not zoom_modifier and not pixel_delta.isNull():
            self._pan_pixels(pixel_delta.x(), pixel_delta.y())
            event.accept()
            return

        # Otherwise zoom: mouse wheel, or modifier-held trackpad scroll.
        norm = self._cursor_to_norm(event.pos())
        if norm is None:
            return
        delta = event.angleDelta().y() or pixel_delta.y()
        if delta == 0:
            return
        # Smooth: each 120 angle-units (or ~120 px on a trackpad) is one step.
        factor = ZOOM_STEP ** (-delta / 120.0)
        self._zoom_around(norm, factor)
        event.accept()

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        rect = self._displayed_rect()
        if rect is None:
            return
        ox, oy, dw, dh = rect
        x = event.pos().x() - ox
        y = event.pos().y() - oy
        if not (0 <= x <= dw and 0 <= y <= dh):
            return
        self._drag_anchor = (event.pos(), self.viewer.view_roi, rect)
        self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        if self._drag_anchor is None:
            return
        start, start_roi, (ox, oy, dw, dh) = self._drag_anchor
        x0, y0, x1, y1 = start_roi
        w, h = x1 - x0, y1 - y0
        # Dragging the image to the right moves the ROI to the left in image space.
        dx = -(event.pos().x() - start.x()) / dw * w
        dy = -(event.pos().y() - start.y()) / dh * h
        new_x0 = x0 + dx
        new_y0 = y0 + dy
        self.viewer.set_roi((new_x0, new_y0, new_x0 + w, new_y0 + h))

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._drag_anchor is not None:
            self._drag_anchor = None
            self.setCursor(Qt.OpenHandCursor)


# ---------- GUI: MAIN WINDOW ------------ #

class Viewer(QWidget):
    def __init__(self, image_dir):
        super().__init__()
        self.setObjectName("viewer")
        self.setStyleSheet(STYLESHEET)
        self.image_dir = image_dir
        self.elements = []  # ElementWidget per element
        self.full_data = []  # (full_res_map, rgb) per element
        self.used_elements = []  # names actually found on disk
        self._combined_rgb = None  # float RGB of the full combined image; cropped on render
        self.view_roi = (0.0, 0.0, 1.0, 1.0)  # shared zoom/pan rectangle
        self._init_ui()
        self._load_data()

    def _init_ui(self):
        # --- left: 4x2 grid of element cards ---
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(14)
        self.grid.setVerticalSpacing(14)
        self.grid.setContentsMargins(0, 0, 0, 0)

        # Width hugs the grid so all spare window width
        # goes to growing the combined preview.
        self._grid_w = GRID_COLUMNS * CARD_WIDTH + (GRID_COLUMNS - 1) * 14
        self.grid_container = QWidget()
        self.grid_container.setStyleSheet("background: transparent;")
        self.grid_container.setFixedWidth(self._grid_w)
        self.grid_container.setLayout(self.grid)

        # --- right: combined preview card (responsive) + actions ---
        combined_title = QLabel("Combined")
        combined_title.setFixedHeight(TITLE_H)
        combined_title.setStyleSheet(f"color: {TEXT}; font-weight: 600;")

        # Global brightness-factor nudge: multiplies every element's brightness:
        # New Brightness = Current Brightness * Brightness factor
        factor_label = QLabel("Brightness factor")
        self.factor_box = QLineEdit("1.0")
        self.factor_box.setFixedWidth(72)
        self.factor_box.setAlignment(Qt.AlignRight)
        self.factor_box.editingFinished.connect(self._apply_brightness_factor)
        factor_row = QHBoxLayout()
        factor_row.setContentsMargins(0, 0, 0, 0)
        factor_row.addWidget(factor_label)
        factor_row.addStretch(1)
        factor_row.addWidget(self.factor_box)
        factor_widget = QWidget()
        factor_widget.setFixedHeight(TITLE_H)
        factor_widget.setLayout(factor_row)

        self.combined_label = CombinedView(self)
        self.combined_label.setMinimumSize(COMBINED_MIN, COMBINED_MIN)
        self.combined_label.setAlignment(Qt.AlignCenter)
        self.combined_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.combined_label.setToolTip(
            "Option-scroll/Cmd-scroll to zoom; "
            "drag or two-finger scroll to pan.")

        reset_view_btn = QPushButton("Reset view")
        reset_view_btn.setToolTip("Restore the full image in every panel.")
        reset_view_btn.clicked.connect(self.reset_view)
        reset_view_row = QHBoxLayout()
        reset_view_row.setContentsMargins(0, 0, 0, 0)
        reset_view_row.addStretch(1)
        reset_view_row.addWidget(reset_view_btn)
        reset_view_widget = QWidget()
        reset_view_widget.setLayout(reset_view_row)

        combined_card = QFrame()
        combined_card.setObjectName("card")
        combined_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        cc_layout = QVBoxLayout()
        cc_layout.setContentsMargins(CARD_PAD, CARD_PAD, CARD_PAD, CARD_PAD)
        cc_layout.setSpacing(6)
        cc_layout.addWidget(combined_title)
        cc_layout.addWidget(factor_widget)
        cc_layout.addWidget(self.combined_label, stretch=1)
        cc_layout.addWidget(reset_view_widget)
        combined_card.setLayout(cc_layout)

        save_btn = QPushButton("Save Full Resolution")
        save_btn.setObjectName("primary")
        save_btn.clicked.connect(self.save_full_res)

        load_btn = QPushButton("Load Settings")
        load_btn.clicked.connect(self.load_settings)

        reset_btn = QPushButton("Restore to Default")
        reset_btn.clicked.connect(self.reset_all)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(10)
        right.addWidget(combined_card, stretch=1)
        right.addWidget(save_btn)
        right.addWidget(load_btn)
        right.addWidget(reset_btn)
        right_container = QWidget()
        right_container.setLayout(right)

        layout = QHBoxLayout()
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(16)
        layout.addWidget(self.grid_container, stretch=0, alignment=Qt.AlignTop)
        layout.addWidget(right_container, stretch=1)
        self.setLayout(layout)

        self.setWindowTitle("Element Map Viewer")

    def _finalise_size(self):
        """Size the window so that all cards show at once."""
        self.grid.activate()
        self.adjustSize()  # fit height to the (tall) grid
        h = self.height()
        min_w = self._grid_w + 16 + COMBINED_MIN + 28  # grid + gap + preview + margins
        self.setMinimumSize(min_w, h)  # never shrink enough to hide a row
        self.resize(min_w + 220, h)  # a little extra width for the preview

    def _resolve_naming(self):
        """Pick the on-disk filename for each element.

        Uses the default "<symbol>.tif" naming when present. If not, and a
        consistent prefix/suffix pattern is detected, ask the user whether to
        use it instead.
        """
        names = [el.name for el in ELEMENTS]
        result = resolve_filenames(self.image_dir, names)
        kind = result[0]

        if kind == "default":
            return result[1]

        if kind == "alternative":
            mapping, prefix, suffix = result[1], result[2], result[3]
            lines = "\n".join(f"    {name}:  {mapping[name]}"
                              for name in names if name in mapping)
            pattern = f'"{prefix}<element>{suffix}"'
            msg = (
                'No files named "<element>.tif" (e.g. "Al.tif") were found.\n\n'
                f'However, maps using the naming pattern {pattern} '
                'were detected:\n\n'
                f'{lines}\n\n'
                'Do you want to use these files?'
            )
            reply = QMessageBox.question(
                self, "Use detected file names?", msg,
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.Yes:
                return mapping
            raise RuntimeError(
                'File names were not confirmed.\n\n'
                'Choose a different folder/rename your maps to "<element>.tif" (e.g. "Al.tif")'
                '/hardcode a new file format in this python script.')

        raise RuntimeError(
            'No element maps found.\n\n'
            'Expected files to be named "<element>.tif" (e.g. "Al.tif", "Ca.tif") \n\n'
            'Or even for there to a set of files that all share the same text before/after the '
            'element symbol (e.g. "User1-Al_K.tif", "User1-Ca_K.tif").')

    def _load_data(self):
        # Load every available map once; the first found defines the reference
        # shapes that the others are cropped to. Two downsampled copies are
        # kept: a small one for thumbnails and a larger one for the combined.
        filenames = self._resolve_naming()  # {symbol: filename}; may prompt/raise
        maps = {}
        ref = ref_preview = ref_combined = None
        for el in ELEMENTS:
            fname = filenames.get(el.name)
            if not fname:
                continue
            path = os.path.join(self.image_dir, fname)
            full = load_and_preprocess(path)
            maps[el.name] = (full,
                             downsample(full, PREVIEW_SCALE),
                             downsample(full, COMBINED_SCALE))
            if ref is None:
                ref = full.shape
                ref_preview = maps[el.name][1].shape
                ref_combined = maps[el.name][2].shape

        if ref is None:
            raise RuntimeError("No TIFF files found in directory.")

        for i, el in enumerate(ELEMENTS):
            available = el.name in maps
            if available:
                full, preview, combined = maps[el.name]
                full = full[:ref[0], :ref[1]]
                preview = preview[:ref_preview[0], :ref_preview[1]]
                combined = combined[:ref_combined[0], :ref_combined[1]]
                self.used_elements.append(el.name)
            else:
                print(f"WARNING: Missing '{el.name}'")
                full = np.zeros(ref, dtype=np.float32)
                preview = np.zeros(ref_preview, dtype=np.float32)
                combined = np.zeros(ref_combined, dtype=np.float32)

            widget = ElementWidget(el, preview, combined, self, available=available)
            self.grid.addWidget(widget, i // GRID_COLUMNS, i % GRID_COLUMNS,
                                alignment=Qt.AlignTop)
            self.elements.append(widget)
            self.full_data.append((full, el.rgb))

        self._finalise_size()
        self.update_display()

    def _render_combined(self):
        """Crop the cached combined RGB to the current ROI and fill the preview."""
        if self._combined_rgb is None:
            return
        cropped = crop_to_roi(self._combined_rgb, self.view_roi)
        pixmap = QPixmap.fromImage(to_qimage(cropped))
        self.combined_label.setPixmap(pixmap.scaled(
            self.combined_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def _refresh_thumbs(self):
        """Re-crop every element thumbnail to the current ROI."""
        for w in self.elements:
            w._render_thumb()

    def set_roi(self, new_roi):
        """Set the shared ROI, then re-render every linked view.
        Clamps width and height to [MIN_ROI_SPAN, 1] and shifts so the box fits
        inside [0, 1].
        """
        x0, y0, x1, y1 = new_roi
        if x1 < x0:
            x0, x1 = x1, x0
        if y1 < y0:
            y0, y1 = y1, y0
        w = min(1.0, max(MIN_ROI_SPAN, x1 - x0))
        h = min(1.0, max(MIN_ROI_SPAN, y1 - y0))
        x0 = max(0.0, min(1.0 - w, x0))
        y0 = max(0.0, min(1.0 - h, y0))
        roi = (x0, y0, x0 + w, y0 + h)
        if roi == self.view_roi:
            return
        self.view_roi = roi
        self._refresh_thumbs()
        self._render_combined()

    def reset_view(self):
        self.set_roi((0.0, 0.0, 1.0, 1.0))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._render_combined()  # keep the preview filling its (resized) card

    def showEvent(self, event):
        super().showEvent(event)
        # The preview label only gets its final size after the first layout
        # pass. So need to defer one event-loop tick so the initial combined image scales
        # to the right size instead of a pre-layout default.
        # TODO: Probably a more efficient way to do this?
        QTimer.singleShot(0, self._render_combined)

    def update_display(self):
        """Recompute every element, then the combined image (load / reset-all)."""
        for w in self.elements:
            w.recompute()
        self.refresh_combined()

    def refresh_combined(self):
        """Sum the cached colour layers of the included elements and show them.
        """
        included = [w for w in self.elements if w.included and w.layer() is not None]
        if included:
            combined = included[0].layer().copy()
            for w in included[1:]:
                combined += w.layer()
            np.clip(combined, 0, 1, out=combined)
        else:
            h, w0 = self.elements[0].combined_img.shape[:2]
            combined = np.zeros((h, w0, 3), dtype=np.float32)
        self._combined_rgb = combined
        self._render_combined()

    def _apply_brightness_factor(self):
        try:
            factor = float(self.factor_box.text())
        except ValueError:
            factor = -1.0
        if factor < 0:
            self._reset_factor_box()
            return
        for w in self.elements:
            w.brightness_c.set_value(w.brightness_c.value() * factor)
        self.update_display()
        self._reset_factor_box()

    def _reset_factor_box(self):
        self.factor_box.blockSignals(True)
        self.factor_box.setText("1.0")
        self.factor_box.blockSignals(False)

    def reset_all(self):
        for w in self.elements:
            for c in w.controls:
                c.reset()
        self.update_display()

    @staticmethod
    def _export(img, filename):
        tifffile.imwrite(filename, (img * EXPORT_MAX).astype(np.uint16))
        print(f"Saved: {filename}")

    def save_full_res(self):
        save_dir = QFileDialog.getExistingDirectory(
            self, "Choose a folder to save results into", self.image_dir)
        if not save_dir:
            return
        save_dir = Path(save_dir)

        print("Generating full resolution images...")
        # Each used element's colour map is saved individually. The
        # combined image sums the elements toggled on, and its filename
        # lists the included elements.
        combined = None
        combined_names = []
        for (full, rgb), w in zip(self.full_data, self.elements):
            layer = render_layer(full, w, 1.0)
            if w.name in self.used_elements:
                self._export(layer, str(save_dir / f"{w.name}_colour.tif"))
            if w.included and w.name in self.used_elements:
                combined = layer if combined is None else combined + layer
                combined_names.append(w.name)

        self._write_settings(save_dir / "settings.json")

        if combined is None:
            print("No elements are included - skipping combined image.")
            return
        combined = np.clip(combined, 0, 1)

        output_path = save_dir / ("_".join(combined_names) + ".tif")
        self._export(combined, str(output_path))

    def _write_settings(self, path):
        """Save every element's processing settings + source dir (for reproducibility/applying to other images)."""
        elements = {}
        for w in self.elements:
            elements[w.name] = {
                "colour": _hex(w.rgb),
                "brightness": w.brightness,
                "black": w.black,
                "white": w.white,
                "gamma": w.gamma,
                "smoothing": w.smoothing,
                "included": w.included,
            }
        payload = {
            "schema": 1,
            "source_dir": self.image_dir,
            "elements": elements,
        }
        with open(path, "w") as f:
            json.dump(payload, f, indent=2)
        print(f"Saved: {path}")

    def load_settings(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose a settings.json", self.image_dir, "JSON (*.json)")
        if not path:
            return
        try:
            with open(path) as f:
                payload = json.load(f)
            if payload.get("schema") != 1 or "elements" not in payload:
                raise ValueError("Unrecognised settings file (schema mismatch).")
        except (OSError, ValueError, json.JSONDecodeError) as e:
            QMessageBox.warning(self, "Could not load settings", str(e))
            return

        loaded = payload["elements"]
        colour_mismatches = []
        for w in self.elements:
            entry = loaded.get(w.name)
            if entry is None:
                continue
            saved_colour = entry.get("colour")
            if saved_colour and saved_colour.lower() != _hex(w.rgb).lower():
                colour_mismatches.append(f"{w.name}: {saved_colour} vs {_hex(w.rgb)}")
            if not w.available:
                continue
            w.brightness_c.set_value(float(entry.get("brightness", w.brightness)))
            w.black_c.set_value(float(entry.get("black", w.black)))
            w.white_c.set_value(float(entry.get("white", w.white)))
            w.gamma_c.set_value(float(entry.get("gamma", w.gamma)))
            w.smooth_c.set_value(float(entry.get("smoothing", w.smoothing)))
            w.include_btn.blockSignals(True)
            w.include_btn.setChecked(bool(entry.get("included", True)))
            w.include_btn.blockSignals(False)
            w._apply_included_style()

        self.update_display()

        notes = []
        src = payload.get("source_dir")
        if src and src != self.image_dir:
            notes.append(f"Settings were saved against a different source folder:\n  {src}")
        if colour_mismatches:
            notes.append("Colour assignments differ from current setup:\n  "
                         + "\n  ".join(colour_mismatches))
        if notes:
            QMessageBox.information(self, "Settings loaded with warnings",
                                    "\n\n".join(notes))


# ---------- RUN ------------------------- #

def main():
    app = QApplication(sys.argv)

    # Prompt to select the directory containing the EDS maps
    start = DEFAULT_IMAGE_DIR if DEFAULT_IMAGE_DIR and os.path.isdir(DEFAULT_IMAGE_DIR) \
        else os.path.expanduser("~")

    QMessageBox.information(
        None, "Select folder",
        "Choose the folder containing the element maps (.tif).")
    viewer = None
    while viewer is None:
        image_dir = QFileDialog.getExistingDirectory(
            None, "Choose the folder containing the element maps (.tif)", start)
        if not image_dir:
            sys.exit(0)  # user cancelled
        try:
            viewer = Viewer(image_dir)
        except RuntimeError as e:
            QMessageBox.warning(None, "No element maps found", str(e))
            start = image_dir

    viewer.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
