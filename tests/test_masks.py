from __future__ import annotations

import os
import shutil
from pathlib import Path

import numpy as np
import SimpleITK as sitk

from ctqa_catphan.app_settings import label_map
from ctqa_catphan.masks import (
    pack_baseline,
    packed_is_stale,
    unpack_labels,
    load_label_map,
    load_named_masks,
    transfer_masks_transformix,
)


def _machine():
    return {
        "num_of_HU_masks": 2,
        "num_of_UF_masks": 0,
        "num_of_HC_masks": 0,
        "num_of_LC_masks": 0,
        "num_of_geo_masks": 0,
        "num_of_DT_masks": 0,
    }


def _write_mask(folder: Path, name: str, arr: np.ndarray) -> None:
    img = sitk.GetImageFromArray(arr.astype(np.uint8))
    img.SetSpacing((1.0, 1.0, 1.0))
    sitk.WriteImage(img, str(folder / f"{name}.mha"), True)


def test_pack_unpack_roundtrip(tmp_path: Path):
    hu1 = np.zeros((4, 8, 8), dtype=np.uint8)
    hu2 = np.zeros((4, 8, 8), dtype=np.uint8)
    hu1[:, 1:3, 1:3] = 1
    hu2[:, 5:7, 5:7] = 1
    _write_mask(tmp_path, "HU1", hu1)
    _write_mask(tmp_path, "HU2", hu2)
    machine = _machine()
    packed = pack_baseline(tmp_path, machine, force=True)
    mapping = load_label_map(tmp_path / "masks_packed.json")
    image = sitk.ReadImage(str(packed))
    out = unpack_labels(image, mapping)
    assert np.array_equal(sitk.GetArrayFromImage(out["HU1"]) > 0, hu1 > 0)
    assert np.array_equal(sitk.GetArrayFromImage(out["HU2"]) > 0, hu2 > 0)
    assert label_map(machine)[1] == "HU1"


def test_pack_if_stale(tmp_path: Path, monkeypatch):
    hu1 = np.zeros((2, 4, 4), dtype=np.uint8)
    hu1[0, 0, 0] = 1
    hu2 = np.zeros((2, 4, 4), dtype=np.uint8)
    hu2[1, 1, 1] = 1
    _write_mask(tmp_path, "HU1", hu1)
    _write_mask(tmp_path, "HU2", hu2)
    machine = _machine()
    pack_baseline(tmp_path, machine, force=True)
    assert packed_is_stale(tmp_path, machine) is False
    packed = tmp_path / "masks_packed.mha"
    hu1 = tmp_path / "HU1.mha"
    newer = packed.stat().st_mtime + 5
    os.utime(hu1, (newer, newer))
    assert packed_is_stale(tmp_path, machine) is True


def test_transfer_masks_transformix_once(tmp_path: Path, monkeypatch):
    hu1 = np.zeros((2, 4, 4), dtype=np.uint8)
    hu2 = np.zeros((2, 4, 4), dtype=np.uint8)
    hu1[0, 0, 0] = 1
    hu2[1, 1, 1] = 1
    _write_mask(tmp_path, "HU1", hu1)
    _write_mask(tmp_path, "HU2", hu2)
    packed = pack_baseline(tmp_path, _machine(), force=True)
    tp = tmp_path / "TransformParameters.1.txt"
    tp.write_text(
        '(ResampleInterpolator "FinalBSplineInterpolator")\n'
        "(FinalBSplineInterpolationOrder 1)\n"
        '(ResultImagePixelType "short")\n',
        encoding="utf-8",
    )
    calls: list[tuple[str, str]] = []

    def fake_transformix(moving, out_dir, transform_param, *, elastix_dir=None):
        calls.append((str(moving), str(transform_param)))
        dest = Path(out_dir) / "result.mha"
        shutil.copy2(packed, dest)
        return dest

    monkeypatch.setattr("ctqa_catphan.masks.run_transformix", fake_transformix)
    seg = tmp_path / "2.seg"
    written = transfer_masks_transformix(tmp_path, _machine(), seg, tp)
    assert len(calls) == 1
    assert Path(calls[0][0]).name == "masks_packed.mha"
    assert Path(calls[0][1]).name == "TransformParameters.labels.txt"
    labels_tp = (tmp_path / "TransformParameters.labels.txt").read_text(encoding="utf-8")
    assert "FinalNearestNeighborInterpolator" in labels_tp
    assert "FinalBSplineInterpolationOrder 0" in labels_tp
    assert (seg / "masks_packed.mha").is_file()
    assert (seg / "masks_packed.json").is_file()
    assert not (seg / "HU1.mha").exists()
    loaded = load_named_masks(seg, _machine())
    assert np.array_equal(sitk.GetArrayFromImage(loaded["HU1"]) > 0, hu1 > 0)
    assert np.array_equal(sitk.GetArrayFromImage(loaded["HU2"]) > 0, hu2 > 0)
