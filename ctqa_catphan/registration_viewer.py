"""Blend today's CT with the registered (or original) baseline CT."""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import SimpleITK as sitk
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
)

from .image_io import find_image, read_image
from .registration import (
    case_has_registration,
    find_registered_result,
    registration_summary,
    resample_onto,
)

logger = logging.getLogger(__name__)


def _fmt3(values: list[float], decimals: int) -> str:
    if not values:
        return "—"
    return ", ".join(f"{v:.{decimals}f}" for v in values)


def extract_plane(arr: np.ndarray, plane: str, index: int) -> tuple[np.ndarray, int, int]:
    """Return (slice, clamped_index, axis_length). Array is z, y, x."""
    plane = (plane or "axial").lower()
    if plane == "coronal":
        n = int(arr.shape[1])
        i = min(max(index, 0), max(n - 1, 0))
        return arr[::-1, i, :], i, n
    if plane == "sagittal":
        n = int(arr.shape[2])
        i = min(max(index, 0), max(n - 1, 0))
        return arr[::-1, :, i], i, n
    n = int(arr.shape[0])
    i = min(max(index, 0), max(n - 1, 0))
    return arr[i, :, :], i, n


def _window_array(arr: np.ndarray, level: float, width: float) -> np.ndarray:
    lo = level - width / 2.0
    hi = level + width / 2.0
    scale = 255.0 / max(hi - lo, 1e-6)
    out = np.clip((arr.astype(np.float32) - lo) * scale, 0, 255).astype(np.uint8)
    return np.ascontiguousarray(out)


class SliceView(QLabel):
    slice_delta = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(480, 480)
        self.setStyleSheet("background: #111827; color: #94a3b8;")
        self.setText("Loading…")
        self._pixmap: QPixmap | None = None

    def set_slice_pixmap(self, pixmap: QPixmap) -> None:
        self._pixmap = pixmap
        self._refit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refit()

    def wheelEvent(self, event):
        steps = int(event.angleDelta().y() / 120) or (1 if event.angleDelta().y() > 0 else -1)
        self.slice_delta.emit(steps)
        event.accept()

    def _refit(self) -> None:
        if self._pixmap is None or self._pixmap.isNull():
            return
        self.setPixmap(
            self._pixmap.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )


class RegistrationViewer(QDialog):
    def __init__(self, case_dir: str | Path, machine: dict | None = None, parent=None):
        super().__init__(parent)
        self.case_dir = Path(case_dir)
        self.machine = machine or {}
        self.setWindowTitle(f"Registration — {self.case_dir.name}")
        self.resize(1080, 820)
        self.setWindowFlag(Qt.Window, True)

        self._fixed_img = None
        self._fixed: np.ndarray | None = None
        self._registered: np.ndarray | None = None
        self._original: np.ndarray | None = None
        self._moving_path: Path | None = None
        self._mode = "registered"
        self._plane = "axial"
        self._qimg_buf: np.ndarray | None = None

        self.view = SliceView()
        self.view.slice_delta.connect(self._nudge_slice)
        self.mode_label = QLabel()
        self.mode_label.setStyleSheet("font-weight: 600;")
        self.params = QLabel()
        self.params.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.params.setStyleSheet("color: #334155;")

        self.reset_btn = QPushButton("Reset")
        self.registered_btn = QPushButton("Registered")
        self.reset_btn.clicked.connect(lambda: self._set_mode("original"))
        self.registered_btn.clicked.connect(lambda: self._set_mode("registered"))

        self.plane_combo = QComboBox()
        for name, key in (("Axial", "axial"), ("Sagittal", "sagittal"), ("Coronal", "coronal")):
            self.plane_combo.addItem(name, key)
        self.plane_combo.currentIndexChanged.connect(self._on_plane_changed)

        self.blend = QSlider(Qt.Horizontal)
        self.blend.setRange(0, 100)
        self.blend.setValue(50)
        self.blend.valueChanged.connect(self._refresh)
        self.blend_label = QLabel("(Baseline)")

        self.slice = QSlider(Qt.Horizontal)
        self.slice.setRange(0, 0)
        self.slice.valueChanged.connect(self._refresh)
        self.slice_label = QLabel()

        self.window = QSlider(Qt.Horizontal)
        self.window.setRange(50, 2000)
        self.window.setValue(400)
        self.window.valueChanged.connect(self._refresh)
        self.window_label = QLabel()

        self.level = QSlider(Qt.Horizontal)
        self.level.setRange(-1000, 1000)
        self.level.setValue(40)
        self.level.valueChanged.connect(self._refresh)
        self.level_label = QLabel()

        row = QHBoxLayout()
        row.addWidget(QLabel("View"))
        row.addWidget(self.plane_combo)
        row.addWidget(self.reset_btn)
        row.addWidget(self.registered_btn)
        row.addWidget(self.mode_label, 1)
        sliders1 = QHBoxLayout()
        sliders1.addLayout(self._slider_row("Blend: (Today)", self.blend, self.blend_label), 1)
        sliders1.addLayout(self._slider_row("Slice", self.slice, self.slice_label), 1)
        sliders2 = QHBoxLayout()
        sliders2.addLayout(self._slider_row("Window", self.window, self.window_label), 1)
        sliders2.addLayout(self._slider_row("Level", self.level, self.level_label), 1)

        root = QVBoxLayout(self)
        root.addLayout(row)
        root.addLayout(sliders1)
        root.addLayout(sliders2)
        root.addWidget(self.view, 1)
        root.addWidget(self.params)
        self._load()

    def _slider_row(self, title: str, slider: QSlider, value: QLabel) -> QHBoxLayout:
        row = QHBoxLayout()
        name = QLabel(title)
        name.setFixedWidth(100)
        slider.setFixedWidth(180)
        value.setFixedWidth(80)
        row.addWidget(name)
        row.addWidget(slider)
        row.addWidget(value)
        row.addStretch(1)
        return row

    def _load(self) -> None:
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            if not case_has_registration(self.case_dir):
                raise FileNotFoundError(
                    "No registration images yet. Run Analysis first "
                    "(needs case CT.mha and 1.reg/result.1.mha or result.0.mha)."
                )
            fixed_path = find_image(self.case_dir, "CT")
            reg_path = find_registered_result(self.case_dir / "1.reg")
            if fixed_path is None or reg_path is None:
                raise FileNotFoundError("CT.mha or registration result not found.")
            fixed = read_image(fixed_path)
            registered = read_image(reg_path)
            if (
                registered.GetSize() != fixed.GetSize()
                or registered.GetOrigin() != fixed.GetOrigin()
                or registered.GetSpacing() != fixed.GetSpacing()
            ):
                registered = resample_onto(registered, fixed)
            self._fixed_img = fixed
            self._fixed = np.ascontiguousarray(sitk.GetArrayFromImage(fixed))
            self._registered = np.ascontiguousarray(sitk.GetArrayFromImage(registered))
            baseline = Path(str(self.machine.get("baseline_dir") or "")).expanduser()
            self._moving_path = find_image(baseline, "CT") if baseline.is_dir() else None
            self.reset_btn.setEnabled(self._moving_path is not None)
            self._sync_slice_range(center=True)
            self._fill_params()
            self._set_mode("registered")
        except Exception:
            QApplication.restoreOverrideCursor()
            raise
        QApplication.restoreOverrideCursor()

    def _fill_params(self) -> None:
        summary = registration_summary(self.case_dir / "1.reg")
        lines: list[str] = []
        for i, stage in enumerate(summary.get("stages") or []):
            kind = "Translation" if stage.get("type") == "TranslationTransform" else (
                "Rigid (Euler)" if stage.get("type") == "EulerTransform" else stage.get("type")
            )
            lines.append(f"Stage {i}  {kind}")
            rot = stage.get("rotation_deg") or []
            trans = stage.get("translation_mm") or []
            if rot and any(abs(v) > 1e-9 for v in rot):
                lines.append(f"    R (deg)   {_fmt3(rot, 3)}")
            if trans:
                lines.append(f"    T (mm)    {_fmt3(trans, 3)}")
        if not lines:
            lines.append("No TransformParameters.*.txt in 1.reg")
        self.params.setText("\n".join(lines))

    def _set_mode(self, mode: str) -> None:
        if mode == "original" and self._original is None:
            if self._moving_path is None:
                return
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                moving = read_image(self._moving_path)
                resampled = resample_onto(moving, self._fixed_img)
                self._original = np.ascontiguousarray(sitk.GetArrayFromImage(resampled))
            finally:
                QApplication.restoreOverrideCursor()
        self._mode = mode
        registered = mode == "registered"
        self.registered_btn.setEnabled(not registered)
        self.reset_btn.setEnabled(registered and self._moving_path is not None)
        if registered:
            self.mode_label.setText("Registered baseline on today")
        else:
            self.mode_label.setText("Original baseline (unregistered)")
        self._refresh()

    def _on_plane_changed(self, *_args) -> None:
        self._plane = str(self.plane_combo.currentData() or "axial")
        self._sync_slice_range(center=True)
        self._refresh()

    def _sync_slice_range(self, *, center: bool) -> None:
        if self._fixed is None:
            return
        _slc, _i, n = extract_plane(self._fixed, self._plane, 0)
        self.slice.blockSignals(True)
        self.slice.setRange(0, max(n - 1, 0))
        if center:
            self.slice.setValue(n // 2)
        else:
            self.slice.setValue(min(self.slice.value(), max(n - 1, 0)))
        self.slice.blockSignals(False)

    def _nudge_slice(self, delta: int) -> None:
        self.slice.setValue(self.slice.value() + delta)

    def _other_array(self) -> np.ndarray | None:
        if self._mode == "original":
            return self._original
        return self._registered

    def _refresh(self) -> None:
        if self._fixed is None:
            return
        other = self._other_array()
        if other is None:
            return
        t = self.blend.value() / 100.0
        fixed_slc, index, n = extract_plane(self._fixed, self._plane, self.slice.value())
        other_slc, _, _ = extract_plane(other, self._plane, index)
        blended = (1.0 - t) * fixed_slc.astype(np.float32) + t * other_slc.astype(np.float32)
        u8 = _window_array(blended, float(self.level.value()), float(self.window.value()))
        self._qimg_buf = u8
        height, width = u8.shape
        image = QImage(u8.data, width, height, width, QImage.Format_Grayscale8)
        self.view.set_slice_pixmap(QPixmap.fromImage(image.copy()))
        self.blend_label.setText("(Baseline)")
        self.slice_label.setText(f"{index + 1}/{n}")
        self.window_label.setText(str(self.window.value()))
        self.level_label.setText(str(self.level.value()))


def open_registration_viewer(case_dir: str | Path, machine: dict | None = None, parent=None) -> RegistrationViewer:
    win = RegistrationViewer(case_dir, machine, parent)
    win.show()
    return win
