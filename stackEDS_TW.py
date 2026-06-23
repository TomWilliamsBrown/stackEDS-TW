"""Stack individual EDS element maps (TIFF) into a false-colour composite.

Each element has an adjustable processing pipeline:
    smooth -> black level -> gamma -> brightness (gain) -> colour
"""

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from dataclasses import dataclass
from skimage.color import rgb2gray

import cv2
import numpy as np
import tifffile
from PIL import ImageColor

from PyQt5.QtCore import Qt, QTimer, QSettings
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import (
    QApplication, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout,
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
    smoothing: float = 0.0  # Gaussian sigma (full-res pixels)
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

SILICATE_ELEMENTS = [
    Element("Al", "white", brightness=1, smoothing=0),
    Element("Ca", "yellow", brightness=1, smoothing=0),
    Element("Cr", "orange", brightness=1, smoothing=0),
    Element("Fe", "red", brightness=1, smoothing=0),
    Element("K", "cyan", brightness=1, smoothing=0),
    Element("Mg", "green", brightness=1, smoothing=0),
    Element("Si", "blue", brightness=1, smoothing=0),
    Element("Ti", "magenta", brightness=1, smoothing=0),
]

# Zr- and phosphate-phase maps.
PHOSPHATE_ELEMENTS = [
    Element("Ca", "blue", brightness=1, smoothing=0),
    Element("Fe", "red", brightness=1, smoothing=0),
    Element("P", "green", brightness=1, smoothing=0),
]

ELEMENT_SETS = [
    ("Make False-colour Mineral Maps for silicates", SILICATE_ELEMENTS),
    ("Make Mineral Maps for Zr- and phosphate phases", PHOSPHATE_ELEMENTS),
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
SCROLL_GUTTER = 14  # px, width reserved beside the element grid for its scrollbar
HIST_W = PREVIEW_SIZE  # px, histogram width
HIST_H = 20  # px, histogram height
TUNING_FIELD_W = 96  # px, numeric entry width in the mask-tuning popup
EXPORT_MAX = 65535  # 16-bit TIFF export range

# adjustable-control ranges
BRIGHTNESS_MAX = 100.0
MAX_SMOOTHING = 10.0
GAMMA_MIN, GAMMA_MAX = 0.2, 5.0
MASK_SENS_MIN, MASK_SENS_MAX = 0.0, 3.0  # background-mask threshold multiplier

# Zoom/pan: smallest fraction of the image that can fill the view
MIN_ROI_SPAN = 0.01
ZOOM_STEP = 1.2

# Background mask:
MASK_BLUR_FRAC = 0.002      # Gaussian sigma applied before thresholding
MASK_KERNEL_FRAC = 0.001    # morphological open/close kernel size
MASK_MIN_AREA_FRAC = 0.0002  # ignore detected blobs smaller than this
MASK_FEATHER_FRAC = 0.000  # soft-edge sigma on the final mask (0 = hard edge)

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
QPushButton#masktoggle:checked {{
    background: #1f3a5c;
    border: 1px solid {ACCENT};
    color: #dceaff;
}}
"""


SCROLL_AREA_QSS = f"""
QScrollArea {{ background: transparent; border: none; }}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px 2px 2px 0;
}}
QScrollBar::handle:vertical {{
    background: {BORDER_HOVER};
    border-radius: 5px;
    min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{ background: #4a4f57; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
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
    # channels (saved as RGB/RGBA by accident) converts to greyscale.
    if img.ndim == 3:
        if img.shape[2] == 4:
            img = img[:, :, :3]
        print(f"NOTICE: Converting {Path(path).name} from RGB channels to single grayscale channel")
        # img = img[:, :, 0]
        img = rgb2gray(img)

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


def sample_mask(maps, sensitivity=1.0, blur_frac=MASK_BLUR_FRAC,
                kernel_frac=MASK_KERNEL_FRAC, min_area_frac=MASK_MIN_AREA_FRAC,
                feather_frac=MASK_FEATHER_FRAC):
    """Estimate a solid sample silhouette from a list of raw element maps.

    Steps: sum -> blur -> Otsu threshold (scaled by `sensitivity`) -> morphology
    to drop speckle and fill pinholes -> fill the outer contour(s) into a solid
    silhouette -> feather the edge. Returns a float mask in [0, 1] (1 = sample)
    the same HxW as the inputs.

    `sensitivity` multiplies the auto threshold: >1 trims more background
    (tighter mask), <1 keeps more of the sample.
    """
    if not maps:
        return None
    h, w = maps[0].shape[:2]

    total = np.zeros((h, w), dtype=np.float32)
    for m in maps:
        total += m
    peak = total.max()
    if peak <= 0:
        return np.ones((h, w), dtype=np.float32)  # no signal: hide nothing
    total /= peak

    short = min(h, w)
    blur = blur_frac * short
    if blur > 0:
        total = cv2.GaussianBlur(total, (0, 0), blur)

    u8 = np.clip(total * 255.0, 0, 255).astype(np.uint8)
    otsu_t, _ = cv2.threshold(u8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    binary = (u8 >= np.clip(otsu_t * sensitivity, 0, 255)).astype(np.uint8)

    # Open then close: remove isolated background specks, then fill small
    # pinholes inside the sample.
    k = max(3, int(round(short * kernel_frac)) | 1)  # odd kernel
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)

    # Fill the outer contours into a solid silhouette (this is the "edges of the
    # sample, filled in"). RETR_EXTERNAL ignores interior holes so internal
    # phases/voids don't punch through the mask.
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return np.ones((h, w), dtype=np.float32)  # threshold cleared everything
    min_area = min_area_frac * h * w
    kept = [c for c in contours if cv2.contourArea(c) >= min_area]
    if not kept:
        kept = [max(contours, key=cv2.contourArea)]  # keep at least the biggest

    solid = np.zeros((h, w), dtype=np.uint8)
    cv2.drawContours(solid, kept, -1, 255, thickness=cv2.FILLED)
    mask = solid.astype(np.float32) / 255.0

    feather = feather_frac * short
    if feather > 0:
        mask = cv2.GaussianBlur(mask, (0, 0), feather)
    return mask


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

    def __init__(self, name, vmin, vmax, value, on_change, fmt="{:.3f}", box_width=72):
        super().__init__()
        self.name = name
        self.vmin, self.vmax = vmin, vmax
        self.fmt = fmt
        self.on_change = on_change
        self.default = value
        self._value = self._clamp(value)

        self.label = QLabel(name)

        self.box = QLineEdit()
        self.box.setFixedWidth(box_width)
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


# ---------- GUI: MASK TUNING POPUP ------ #

class MaskSettingsPopup(QFrame):
    """A small popup panel holding the background-mask tuning knobs.
    """

    def __init__(self, viewer):
        super().__init__(viewer, Qt.Popup)
        self.setObjectName("card")
        self.viewer = viewer

        title = QLabel("Mask tuning")
        title.setStyleSheet(f"color: {TEXT}; font-weight: 600;")
        hint = QLabel("Values are fractions of the image size.")
        hint.setWordWrap(True)

        self.blur_c = Control("Pre-blur", 0.0, 0.05, viewer.mask_blur,
                              self._changed, fmt="{:.5f}", box_width=TUNING_FIELD_W)
        self.blur_c.setToolTip(
            "Gaussian sigma applied before thresholding. Higher = smoother, "
            "more noise-tolerant edges.")
        self.kernel_c = Control("Cleanup", 0.0, 0.05, viewer.mask_kernel,
                                self._changed, fmt="{:.5f}", box_width=TUNING_FIELD_W)
        self.kernel_c.setToolTip(
            "Open/close kernel size. Higher removes more speckle and fills "
            "bigger gaps inside the sample.")
        self.area_c = Control("Min blob", 0.0, 0.05, viewer.mask_min_area,
                              self._changed, fmt="{:.5f}", box_width=TUNING_FIELD_W)
        self.area_c.setToolTip(
            "Ignore detected regions smaller than this fraction of the image "
            "area. Higher drops larger stray specks.")
        self.feather_c = Control("Feather", 0.0, 0.02, viewer.mask_feather,
                                 self._changed, fmt="{:.5f}", box_width=TUNING_FIELD_W)
        self.feather_c.setToolTip(
            "Soft-edge sigma on the final mask. 0 = a hard edge.")
        self.controls = [self.blur_c, self.kernel_c, self.area_c, self.feather_c]

        remask_btn = QPushButton("Remask")
        remask_btn.setObjectName("primary")
        remask_btn.setToolTip(
            "Recompute the sample outline from the current adjusted maps and "
            "tuning. The mask stays fixed until you click this.")
        remask_btn.clicked.connect(viewer.remask)

        reset_btn = QPushButton("Reset tuning")
        reset_btn.setToolTip("Restore the default mask tuning values.")
        reset_btn.clicked.connect(self._reset)

        lay = QVBoxLayout()
        lay.setContentsMargins(CARD_PAD, CARD_PAD, CARD_PAD, CARD_PAD)
        lay.setSpacing(6)
        lay.addWidget(title)
        lay.addWidget(hint)
        for c in self.controls:
            lay.addWidget(c)
        lay.addWidget(remask_btn)
        lay.addWidget(reset_btn)
        self.setLayout(lay)
        self.setFixedWidth(262)

    def sync_from_viewer(self):
        """Refresh the controls from the viewer (e.g. after Load Settings)."""
        self.blur_c.set_value(self.viewer.mask_blur)
        self.kernel_c.set_value(self.viewer.mask_kernel)
        self.area_c.set_value(self.viewer.mask_min_area)
        self.feather_c.set_value(self.viewer.mask_feather)

    def _changed(self):
        # Tuning values apply on the next Remask; don't recompute the mask now.
        self.viewer.mask_blur = self.blur_c.value()
        self.viewer.mask_kernel = self.kernel_c.value()
        self.viewer.mask_min_area = self.area_c.value()
        self.viewer.mask_feather = self.feather_c.value()

    def _reset(self):
        self.blur_c.set_value(MASK_BLUR_FRAC)
        self.kernel_c.set_value(MASK_KERNEL_FRAC)
        self.area_c.set_value(MASK_MIN_AREA_FRAC)
        self.feather_c.set_value(MASK_FEATHER_FRAC)
        self._changed()

    def popup_under(self, button):
        """Show the popup right-aligned just under `button`."""
        self.adjustSize()
        corner = button.mapToGlobal(button.rect().bottomRight())
        corner.setX(corner.x() - self.width())
        self.move(corner)
        self.show()


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
        self._fiji_tmp = None  # temp TIFF currently checked out to Fiji, if any

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

        # Round-trip this element's map through Fiji for manual editing.
        self.fiji_btn = QPushButton("Edit in Fiji")
        self.fiji_btn.setToolTip(
            "Send this element's full-resolution map to Fiji (ImageJ) for "
            "manual editing, then reload it with ↻.")
        self.fiji_btn.clicked.connect(lambda: self.viewer.edit_element_in_fiji(self))
        self.reload_btn = QPushButton("↻")  # clockwise arrow
        self.reload_btn.setFixedWidth(34)
        self.reload_btn.setEnabled(False)
        self.reload_btn.setToolTip(
            "Reload this map from Fiji after you've edited and saved it.")
        self.reload_btn.clicked.connect(lambda: self.viewer.reload_element_from_fiji(self))
        fiji_row = QHBoxLayout()
        fiji_row.setContentsMargins(0, 0, 0, 0)
        fiji_row.setSpacing(6)
        fiji_row.addWidget(self.fiji_btn, stretch=1)
        fiji_row.addWidget(self.reload_btn)
        fiji_row_widget = QWidget()
        fiji_row_widget.setLayout(fiji_row)

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
        layout.addWidget(fiji_row_widget)
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
            self.fiji_btn.setEnabled(False)
            self.reload_btn.setEnabled(False)
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

    def _set_source_maps(self, preview_img, combined_img):
        """Swap in new source maps and drop the stale caches (no re-render).

        The smoothing caches key only off the sigma value, so they must be
        cleared explicitly or an unchanged sigma would keep blurring the old
        image. The current control values (black/white/gamma/brightness) are
        deliberately preserved and re-applied to the new data.
        """
        self.preview_img = preview_img
        self.combined_img = combined_img
        self._sm_p = self._sm_c = None
        self._sm_p_sigma = self._sm_c_sigma = None
        self._layer = None
        self._thumb_rgb = None

    def update_source_maps(self, preview_img, combined_img):
        """Swap in freshly edited maps (e.g. from Fiji) and re-render."""
        self._set_source_maps(preview_img, combined_img)
        self.recompute()

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
        # Undo any Fiji edit first (back to the map loaded from disk); no-op if
        # this element was never sent to Fiji. notify_change() re-renders.
        self.viewer.restore_original_image(self)
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
        return "default", default

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


# ---------- FIJI (IMAGEJ) HANDOFF ------- #
#
# Round-trip a single element's full-res map out to Fiji for manual editing and
# back into the pipeline. Fiji runs as its own process. THis code writes a temp
# TIFF, opens it, and re-reads it.

FIJI_PATH_ENV = "FIJI_PATH"        # optional override: full path to the launcher / .app
FIJI_SETTINGS_ORG = "stackEDS-TW"  # QSettings org/app, used to remember the located Fiji


def _fiji_executable_names():
    """Candidate launcher filenames for the running platform.

    Covers both the modern jaunch-based Fiji (``fiji-<os>-<arch>``) and the
    legacy ImageJ launcher (``ImageJ-<os>``).
    """
    system = platform.system()
    if system == "Darwin":
        return ["fiji-macos-arm64", "fiji-macos-x64", "fiji-macos", "ImageJ-macosx"]
    if system == "Windows":
        return ["fiji-windows-x64.exe", "fiji-windows-x86.exe", "fiji.exe",
                "ImageJ-win64.exe", "ImageJ-win32.exe"]
    return ["fiji-linux-x64", "fiji-linux-x86", "ImageJ-linux64", "ImageJ-linux32", "fiji"]


def _fiji_search_roots():
    """Directories that commonly contain a ``Fiji.app`` install."""
    home = Path.home()
    roots = [
        Path("/Applications"), home / "Applications",
        home / "Desktop", home / "Downloads", home / "Documents", home,
        Path("/opt"), Path("/usr/local"),
    ]
    for var in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        val = os.environ.get(var)
        if val:
            roots.append(Path(val))
    roots.append(Path("C:/"))
    return roots


def find_fiji():
    """Locate the Fiji launcher without prompting. Returns a Path or None.

    Order: ``FIJI_PATH`` env var, a previously remembered choice, a scan of
    common install locations, then anything named like Fiji on ``PATH``.
    """
    override = os.environ.get(FIJI_PATH_ENV)
    if override and Path(override).exists():
        return Path(override)

    saved = QSettings(FIJI_SETTINGS_ORG, FIJI_SETTINGS_ORG).value("fiji_path", "")
    if saved and Path(saved).exists():
        return Path(saved)

    names = _fiji_executable_names()
    on_mac = platform.system() == "Darwin"
    for root in _fiji_search_roots():
        try:
            if not root.is_dir():
                continue
            # Match both a bare Fiji.app and the common wrapper-folder layout
            # (e.g. ~/Desktop/Fiji/Fiji.app) one level down, any case.
            app_dirs = set()
            for pattern in ("Fiji.app", "[Ff]iji*/Fiji.app"):
                app_dirs.update(root.glob(pattern))
        except OSError:
            continue
        for app in sorted(app_dirs):
            launcher_dir = (app / "Contents" / "MacOS") if on_mac else app
            if launcher_dir.is_dir():
                for name in names:
                    candidate = launcher_dir / name
                    if candidate.exists():
                        return candidate

    for name in names + ["fiji", "ImageJ"]:
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


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
    def __init__(self, image_dir, elements):
        super().__init__()
        self.setObjectName("viewer")
        self.setStyleSheet(STYLESHEET)
        self.image_dir = image_dir
        self.element_defs = elements  # chosen Element set (silicate or phosphate)
        self.columns = min(GRID_COLUMNS, len(elements))  # 8 -> 4 (2 rows); 3 -> 3 (1 row)
        self.elements = []  # ElementWidget per element
        self.full_data = []  # (full_res_map, rgb) per element
        self.used_elements = []  # names actually found on disk
        self._combined_rgb = None  # float RGB of the full combined image; cropped on render
        self.view_roi = (0.0, 0.0, 1.0, 1.0)  # shared zoom/pan rectangle
        self.mask_enabled = False  # background mask on/off
        self.mask_sensitivity = 0.8
        self.mask_blur = MASK_BLUR_FRAC
        self.mask_kernel = MASK_KERNEL_FRAC
        self.mask_min_area = MASK_MIN_AREA_FRAC
        self.mask_feather = MASK_FEATHER_FRAC
        self._mask_combined = None  # cached combined-scale silhouette
        self._mask_popup = None  # lazily created tuning popup
        self._fiji_exe = None  # resolved Fiji launcher (cached after first use)
        self._fiji_dir = None  # temp dir holding maps checked out to Fiji
        self._fiji_help_shown = False  # one-time editing-workflow reminder
        self._fiji_original_full = {}  # idx -> pristine full-res map, kept so Reset
        #                                can undo a Fiji edit (populated on reload)
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
        self._grid_w = self.columns * CARD_WIDTH + (self.columns - 1) * 14
        self.grid_container = QWidget()
        self.grid_container.setStyleSheet("background: transparent;")
        self.grid_container.setFixedWidth(self._grid_w)
        self.grid_container.setLayout(self.grid)

        # Host tiles in a vertical scroll area that
        # keeps every tile reachable. Fixed width = grid + scrollbar gutter so
        # the scrollbar never overlaps the cards.
        self.grid_scroll = QScrollArea()
        self.grid_scroll.setWidget(self.grid_container)
        self.grid_scroll.setWidgetResizable(True)
        self.grid_scroll.setFrameShape(QFrame.NoFrame)
        self.grid_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.grid_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.grid_scroll.setFixedWidth(self._grid_w + SCROLL_GUTTER)
        self.grid_scroll.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.grid_scroll.setStyleSheet(SCROLL_AREA_QSS)
        self.grid_scroll.viewport().setStyleSheet("background: transparent;")

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

        # Background mask: estimate the sample outline from the combined signal
        # of every element and hide everything outside it.
        self.mask_btn = QPushButton("Mask background")
        self.mask_btn.setObjectName("masktoggle")
        self.mask_btn.setCheckable(True)
        self.mask_btn.setCursor(Qt.PointingHandCursor)
        self.mask_btn.setToolTip(
            "Detect the sample outline from the combined image you've adjusted "
            "(included elements only) and hide the noisy background outside it.")
        self.mask_btn.toggled.connect(self._on_mask_toggle)

        # gear button opens the advanced tuning popup
        self.mask_adv_btn = QPushButton("⚙")
        self.mask_adv_btn.setObjectName("toggle")
        self.mask_adv_btn.setFixedSize(28, 28)
        self.mask_adv_btn.setCursor(Qt.PointingHandCursor)
        self.mask_adv_btn.setEnabled(False)
        self.mask_adv_btn.setToolTip("Advanced mask tuning")
        self.mask_adv_btn.clicked.connect(self._open_mask_settings)

        self.mask_sens_c = Control(
            "Mask sensitivity", MASK_SENS_MIN, MASK_SENS_MAX, 1.0,
            self._on_mask_change, fmt="{:.2f}")
        self.mask_sens_c.setToolTip(
            "Multiplies the auto-detected threshold. Above 1 trims more "
            "background (tighter mask); below 1 keeps more of the sample.")
        self.mask_sens_c.setEnabled(False)

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
        mask_row = QHBoxLayout()
        mask_row.setContentsMargins(0, 0, 0, 0)
        mask_row.setSpacing(6)
        mask_row.addWidget(self.mask_btn, stretch=1)
        mask_row.addWidget(self.mask_adv_btn)
        mask_row_widget = QWidget()
        mask_row_widget.setLayout(mask_row)
        cc_layout.addWidget(mask_row_widget)
        cc_layout.addWidget(self.mask_sens_c)
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
        layout.addWidget(self.grid_scroll, stretch=0)
        layout.addWidget(right_container, stretch=1)
        self.setLayout(layout)

        self.setWindowTitle("Element Map Viewer")

    def _finalise_size(self):
        """Size the window to show as many cards as the screen allows.

        Aim to show every tile, but cap the height to the screen's
        working area and let the grid scroll when it can't fit
        """
        # Collect any spare vertical space (window taller than the grid) in an
        # empty row below the cards, so the rows stay tucked at the top instead
        # of spreading apart.
        self.grid.setRowStretch(self.grid.rowCount(), 1)
        self.grid.activate()
        self.grid_container.adjustSize()

        min_w = self._grid_w + SCROLL_GUTTER + 16 + COMBINED_MIN + 28  # grid + gutter + gap + preview + margins

        # Height that would show the whole grid at once, including window margins.
        m = self.layout().contentsMargins()
        want_h = self.grid_container.sizeHint().height() + m.top() + m.bottom()

        # Never taller than the screen: the grid scrolls to cover any overflow.
        screen = self.screen() or QApplication.primaryScreen()
        if screen is not None:
            want_h = min(want_h, screen.availableGeometry().height() - 60)

        # Keep a sensible floor so the combined panel stays usable; Qt still
        # clamps up to whatever the right-hand column needs as a minimum.
        self.setMinimumSize(min_w, min(want_h, 540))
        self.resize(min_w + 220, want_h)  # a little extra width for the preview

    def _resolve_naming(self):
        """Pick the on-disk filename for each element.

        Uses the default "<symbol>.tif" naming when present. If not, and a
        consistent prefix/suffix pattern is detected, ask the user whether to
        use it instead.
        """
        names = [el.name for el in self.element_defs]
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
        for el in self.element_defs:
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

        for i, el in enumerate(self.element_defs):
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
            self.grid.addWidget(widget, i // self.columns, i % self.columns,
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

    def _build_combined(self):
        """Unmasked, clipped sum of the included elements' colour layers."""
        included = [w for w in self.elements if w.included and w.layer() is not None]
        if included:
            combined = included[0].layer().copy()
            for w in included[1:]:
                combined += w.layer()
            np.clip(combined, 0, 1, out=combined)
        else:
            h, w0 = self.elements[0].combined_img.shape[:2]
            combined = np.zeros((h, w0, 3), dtype=np.float32)
        return combined

    def refresh_combined(self):
        """Re-apply the (frozen) mask to the current combined image and show it.

        The silhouette itself is only recomputed by remask(); edits here just
        re-apply the cached mask, so tuning the maps doesn't move the edge.
        """
        combined = self._build_combined()
        if self.mask_enabled and self._mask_combined is not None:
            combined = combined * self._mask_combined[..., None]
        self._combined_rgb = combined
        self._render_combined()

    def remask(self):
        """Recompute the silhouette from the current adjusted combined image.

        Triggered only explicitly: the Remask button, enabling the mask, or
        loading settings with the mask on.
        """
        if not self.mask_enabled:
            return
        self._mask_combined = self._compute_mask(self._build_combined())
        self.refresh_combined()

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

    def _on_mask_toggle(self, checked):
        self.mask_enabled = checked
        self.mask_sens_c.setEnabled(checked)
        self.mask_adv_btn.setEnabled(checked)
        self.mask_btn.setText("Mask background: on" if checked else "Mask background")
        if checked:
            self.remask()  # compute the initial silhouette
        else:
            self._mask_combined = None
            self.refresh_combined()

    def _on_mask_change(self):
        # Sensitivity applies on the next Remask; don't recompute the mask now.
        self.mask_sensitivity = self.mask_sens_c.value()

    def _open_mask_settings(self):
        """Pop up the advanced mask-tuning panel under the gear button."""
        if self._mask_popup is None:
            self._mask_popup = MaskSettingsPopup(self)
        self._mask_popup.sync_from_viewer()
        self._mask_popup.popup_under(self.mask_adv_btn)

    def _compute_mask(self, combined_rgb):
        """Silhouette from the *adjusted* combined image (what you see).

        Derives the mask from the summed colour layers of the included elements,
        reduced to a single intensity, so every per-element edit feeds the edge
        detection: excluding a noisy channel drops it, raising an element's black
        level removes its noise floor, and brightness rebalances the (otherwise
        qualitative) per-map scaling.
        """
        intensity = combined_rgb.max(axis=2)
        return sample_mask(
            [intensity], self.mask_sensitivity, self.mask_blur, self.mask_kernel,
            self.mask_min_area, self.mask_feather)

    def reset_all(self):
        for w in self.elements:
            self.restore_original_image(w)  # also undo any Fiji edits
            for c in w.controls:
                c.reset()
        self.mask_sens_c.reset()
        self.mask_sensitivity = self.mask_sens_c.value()
        self.mask_blur = MASK_BLUR_FRAC
        self.mask_kernel = MASK_KERNEL_FRAC
        self.mask_min_area = MASK_MIN_AREA_FRAC
        self.mask_feather = MASK_FEATHER_FRAC
        if self._mask_popup is not None:
            self._mask_popup.sync_from_viewer()
        self.mask_btn.blockSignals(True)
        self.mask_btn.setChecked(False)
        self.mask_btn.blockSignals(False)
        self.mask_enabled = False
        self.mask_sens_c.setEnabled(False)
        self.mask_adv_btn.setEnabled(False)
        self._mask_combined = None
        self.mask_btn.setText("Mask background")
        self.update_display()

    # ---- Fiji (ImageJ) round-trip ---- #

    def _fiji_tmpdir(self):
        """Session-scoped temp dir for maps handed off to Fiji."""
        if self._fiji_dir is None or not Path(self._fiji_dir).is_dir():
            self._fiji_dir = Path(tempfile.mkdtemp(prefix="stackEDS_fiji_"))
        return Path(self._fiji_dir)

    def _resolve_fiji(self):
        """Return a usable Fiji launcher Path, prompting + remembering if needed."""
        if self._fiji_exe is not None and Path(self._fiji_exe).exists():
            return self._fiji_exe
        exe = find_fiji() or self._prompt_for_fiji()
        if exe is not None:
            self._fiji_exe = exe
            QSettings(FIJI_SETTINGS_ORG, FIJI_SETTINGS_ORG).setValue("fiji_path", str(exe))
        return exe

    def _prompt_for_fiji(self):
        """Ask the user to locate Fiji when auto-detection fails."""
        QMessageBox.information(
            self, "Locate Fiji",
            "Couldn't find Fiji automatically. Please point to your install:\n\n"
            "  • macOS: choose Fiji.app\n"
            "  • Windows: choose the launcher inside Fiji.app "
            "(e.g. fiji-windows-x64.exe)\n"
            "  • Linux: choose the Fiji launcher\n\n"
            "Your choice is remembered for next time. You can also set the "
            f"{FIJI_PATH_ENV} environment variable.")
        system = platform.system()
        if system == "Darwin":
            path, _ = QFileDialog.getOpenFileName(
                self, "Select Fiji.app", "/Applications", "Application bundle (*.app)")
        elif system == "Windows":
            path, _ = QFileDialog.getOpenFileName(
                self, "Select the Fiji launcher", "", "Executables (*.exe)")
        else:
            path, _ = QFileDialog.getOpenFileName(self, "Select the Fiji launcher", "")
        return Path(path) if path else None

    def _launch_fiji(self, exe, image_path):
        """Open ``image_path`` in Fiji. Returns True on a successful launch."""
        try:
            if platform.system() == "Darwin":
                # Prefer LaunchServices: it reuses a running Fiji and opens the
                # file via Apple Events. Works whether the user picked the .app
                # bundle or auto-found the launcher binary inside it.
                app = next((p for p in [Path(exe), *Path(exe).parents]
                            if p.suffix == ".app"), None)
                if app is not None:
                    result = subprocess.run(
                        ["open", "-a", str(app), str(image_path)],
                        capture_output=True, text=True)
                    if result.returncode != 0:
                        raise RuntimeError(result.stderr.strip() or
                                           "macOS could not open Fiji.")
                    return True
            subprocess.Popen([str(exe), str(image_path)])
            return True
        except (OSError, RuntimeError, subprocess.SubprocessError) as e:
            QMessageBox.warning(self, "Could not launch Fiji", str(e))
            # Forget a bad path so the next attempt re-resolves / re-prompts.
            self._fiji_exe = None
            QSettings(FIJI_SETTINGS_ORG, FIJI_SETTINGS_ORG).remove("fiji_path")
            return False

    def edit_element_in_fiji(self, widget):
        """Write this element's full-res map to a temp TIFF and open it in Fiji."""
        if not widget.available:
            return
        exe = self._resolve_fiji()
        if exe is None:
            return

        idx = self.elements.index(widget)
        full = self.full_data[idx][0]
        # Distinct from the user's own "<element>.tif" so a stray "Save As" in
        # Fiji can't be mistaken for (and overwrite) the original map.
        tmp = self._fiji_tmpdir() / f"{widget.name}__stackEDS_edit.tif"
        try:
            # 32-bit float preserves the normalised [0, 1] data exactly; Fiji
            # (seems to?) open 32-bit TIFFs natively
            # TODO: is 32 bit best? Need to test.
            tifffile.imwrite(str(tmp), full.astype(np.float32))
        except OSError as e:
            QMessageBox.warning(self, "Could not write temp file", str(e))
            return

        if not self._fiji_help_shown:
            QMessageBox.information(
                self, "Editing in Fiji",
                "Fiji will open this element's map.\n\n"
                "1. Edit the image in Fiji.\n"
                "2. Save it back over the SAME file (File ▸ Save, or "
                "Ctrl/Cmd+S).\n"
                "3. Return to here and click ↻ on this card to load the edited version.")
            self._fiji_help_shown = True

        if self._launch_fiji(exe, tmp):
            widget._fiji_tmp = tmp
            widget.reload_btn.setEnabled(True)
            print(f"Opened '{widget.name}' in Fiji: {tmp}")

    def reload_element_from_fiji(self, widget):
        """Re-read this element's map after a Fiji edit and rebuild its layers."""
        tmp = widget._fiji_tmp
        if tmp is None or not Path(tmp).exists():
            QMessageBox.information(
                self, "Nothing to reload",
                "No edited file found yet. In Fiji, save your changes back over "
                "the same file first, then click ↻ again.")
            return
        try:
            new_full = load_and_preprocess(str(tmp))
        except Exception as e:  # noqa: BLE001 - surface any read/decode failure
            QMessageBox.warning(self, "Could not read edited map", str(e))
            return

        idx = self.elements.index(widget)
        rgb = self.full_data[idx][1]
        h, w0 = self.full_data[idx][0].shape[:2]
        if new_full.shape[:2] != (h, w0):
            print(f"NOTICE: '{widget.name}' came back from Fiji at "
                  f"{new_full.shape[:2]}; resizing to {(h, w0)} to match the "
                  f"other maps.")
            new_full = cv2.resize(new_full, (w0, h))

        # Snapshot the pristine (pre-Fiji) map the first time it is overwritted, so
        # Reset can restore the original. setdefault keeps the true original even
        # across repeated edit/reload cycles. The stored array is never mutated
        # in place (the pipeline only ever reads it), so a reference is safe.
        self._fiji_original_full.setdefault(idx, self.full_data[idx][0])
        self.full_data[idx] = (new_full, rgb)
        # Rebuild the downsampled copies at the exact shapes the widget already
        # uses, so thumbnails/combined stay pixel-aligned with every other map.
        ph, pw = widget.preview_img.shape[:2]
        ch, cw = widget.combined_img.shape[:2]
        widget.update_source_maps(cv2.resize(new_full, (pw, ph)),
                                  cv2.resize(new_full, (cw, ch)))
        # Follow the app's frozen-edge rule: re-apply the cached mask rather than
        # recomputing the silhouette. Use the mask popup's Remask to update it.
        self.refresh_combined()
        print(f"Reloaded '{widget.name}' from Fiji.")

    def restore_original_image(self, widget):
        """Revert a Fiji-edited element to the map originally loaded from disk.

        Returns True if a Fiji edit was undone, False if the element was never
        edited (then there is nothing to restore). Does not re-render itself —
        the caller refreshes (e.g. reset()'s notify_change / reset_all's
        update_display). The Fiji temp file is left intact, so can re-pull the
        edit if it was reset by mistake.
        """
        idx = self.elements.index(widget)
        orig = self._fiji_original_full.get(idx)
        if orig is None:
            return False
        rgb = self.full_data[idx][1]
        self.full_data[idx] = (orig, rgb)
        ph, pw = widget.preview_img.shape[:2]
        ch, cw = widget.combined_img.shape[:2]
        widget._set_source_maps(cv2.resize(orig, (pw, ph)),
                                cv2.resize(orig, (cw, ch)))
        print(f"Restored '{widget.name}' to its original (pre-Fiji) map.")
        return True

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
        # Use the exact silhouette shown in the preview, scaled up to full
        # resolution.
        mask_full = None
        if self.mask_enabled and self._mask_combined is not None:
            fh, fw = self.full_data[0][0].shape[:2]
            mask_full = cv2.resize(self._mask_combined, (fw, fh),
                                   interpolation=cv2.INTER_NEAREST)

        # Each used element's colour map is saved individually. The
        # combined image sums the elements toggled on, and its filename
        # lists the included elements.
        combined = None
        combined_names = []
        for (full, rgb), w in zip(self.full_data, self.elements):
            layer = render_layer(full, w, 1.0)
            # Sum the *unmasked* layers; the mask is applied once after clipping
            # (below) to mirror the preview's clip -> mask order exactly.
            if w.included and w.name in self.used_elements:
                combined = layer.copy() if combined is None else combined + layer
                combined_names.append(w.name)
            if w.name in self.used_elements:
                out_layer = layer if mask_full is None else layer * mask_full[..., None]
                self._export(out_layer, str(save_dir / f"{w.name}_colour.tif"))

        self._write_settings(save_dir / "settings.json")

        if combined is None:
            print("No elements are included - skipping combined image.")
            return
        np.clip(combined, 0, 1, out=combined)
        if mask_full is not None:
            combined *= mask_full[..., None]

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
            "mask_enabled": self.mask_enabled,
            "mask_sensitivity": self.mask_sensitivity,
            "mask_blur": self.mask_blur,
            "mask_kernel": self.mask_kernel,
            "mask_min_area": self.mask_min_area,
            "mask_feather": self.mask_feather,
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

        # Restore the global background-mask state
        mask_on = bool(payload.get("mask_enabled", False))
        self.mask_sens_c.set_value(float(payload.get("mask_sensitivity", 1.0)))
        self.mask_sensitivity = self.mask_sens_c.value()
        self.mask_blur = float(payload.get("mask_blur", MASK_BLUR_FRAC))
        self.mask_kernel = float(payload.get("mask_kernel", MASK_KERNEL_FRAC))
        self.mask_min_area = float(payload.get("mask_min_area", MASK_MIN_AREA_FRAC))
        self.mask_feather = float(payload.get("mask_feather", MASK_FEATHER_FRAC))
        if self._mask_popup is not None:
            self._mask_popup.sync_from_viewer()
        self.mask_btn.blockSignals(True)
        self.mask_btn.setChecked(mask_on)
        self.mask_btn.blockSignals(False)
        self.mask_enabled = mask_on
        self.mask_sens_c.setEnabled(mask_on)
        self.mask_adv_btn.setEnabled(mask_on)
        self.mask_btn.setText("Mask background: on" if mask_on else "Mask background")

        self.update_display()
        if self.mask_enabled:
            self.remask()

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

def _choose_element_set():
    """Ask which kind of map to make. Returns the chosen Element list, or None
    if the user closed the dialog without picking.
    """
    box = QMessageBox()
    box.setWindowTitle("Choose map type")
    box.setText("Which kind of mineral map do you want to make?")
    buttons = [(box.addButton(label, QMessageBox.AcceptRole), elements)
               for label, elements in ELEMENT_SETS]
    box.exec_()
    clicked = box.clickedButton()
    return next((elements for btn, elements in buttons if btn is clicked), None)


def main():
    app = QApplication(sys.argv)

    # Ask which element set to load before anything else, so the choice persists
    # across the folder-retry loop below.
    elements = _choose_element_set()
    if elements is None:
        sys.exit(0)  # chooser closed without a choice

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
            viewer = Viewer(image_dir, elements)
        except RuntimeError as e:
            QMessageBox.warning(None, "No element maps found", str(e))
            start = image_dir

    viewer.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
