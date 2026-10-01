"""Baseline mask packing (stale-check) and transfer onto today's CT."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import SimpleITK as sitk

from .app_settings import label_map, mask_stems
from .image_io import find_image, read_image, write_mha

logger = logging.getLogger(__name__)

PACKED_STEM = "masks_packed"
PACKED_JSON = "masks_packed.json"


def packed_paths(baseline_dir: Path) -> tuple[Path, Path]:
    return baseline_dir / f"{PACKED_STEM}.mha", baseline_dir / PACKED_JSON


def mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def individual_mask_files(baseline_dir: Path, machine: dict) -> list[Path]:
    files: list[Path] = []
    missing: list[str] = []
    for stem in mask_stems(machine):
        found = find_image(baseline_dir, stem)
        if found is None:
            missing.append(stem)
        else:
            files.append(found)
    if missing:
        raise FileNotFoundError(
            f"baseline masks missing in {baseline_dir}: {', '.join(missing)}"
        )
    return files


def packed_is_stale(baseline_dir: Path, machine: dict) -> bool:
    packed, mapping = packed_paths(Path(baseline_dir))
    if not packed.is_file() or not mapping.is_file():
        return True
    packed_time = mtime(packed)
    sources = individual_mask_files(Path(baseline_dir), machine)
    sources.append(mapping)
    return any(mtime(path) > packed_time for path in sources)


def combine_labels(mask_images: list[sitk.Image], values: list[int]) -> sitk.Image:
    if not mask_images:
        raise ValueError("no masks to pack")
    if len(mask_images) != len(values):
        raise ValueError("mask_images and values length mismatch")
    ref = mask_images[0]
    combined = np.zeros(sitk.GetArrayFromImage(ref).shape, dtype=np.uint16)
    collisions = 0
    for image, value in zip(mask_images, values):
        arr = sitk.GetArrayFromImage(image)
        if arr.shape != combined.shape:
            raise ValueError(f"mask geometry mismatch for label {value}")
        occupied = combined > 0
        hit = arr > 0.5
        collisions += int(np.count_nonzero(occupied & hit))
        combined[hit] = int(value)
    if collisions:
        logger.warning("packed masks overlap on %s voxels (last label wins)", collisions)
    out = sitk.GetImageFromArray(combined)
    out.CopyInformation(ref)
    return out


def pack_baseline(baseline_dir: str | Path, machine: dict, *, force: bool = False) -> Path:
    folder = Path(baseline_dir)
    packed, mapping_path = packed_paths(folder)
    if not force and not packed_is_stale(folder, machine):
        logger.info("reusing packed baseline masks: %s", packed)
        return packed
    files = individual_mask_files(folder, machine)
    images = [read_image(path) for path in files]
    mapping = label_map(machine)
    values = list(mapping.keys())
    packed_img = combine_labels(images, values)
    payload = {"labels": {str(k): v for k, v in mapping.items()}}
    mapping_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    write_mha(packed_img, packed)
    logger.info("wrote packed baseline masks: %s (%s labels)", packed, len(values))
    return packed


def load_label_map(path: Path) -> dict[int, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    labels = data.get("labels") or {}
    return {int(k): str(v) for k, v in labels.items()}


def unpack_labels(packed: sitk.Image, mapping: dict[int, str]) -> dict[str, sitk.Image]:
    arr = sitk.GetArrayFromImage(packed)
    out: dict[str, sitk.Image] = {}
    for value, name in mapping.items():
        binary = (arr == int(value)).astype(np.uint8)
        image = sitk.GetImageFromArray(binary)
        image.CopyInformation(packed)
        out[name] = image
    return out


def transfer_packed(
    packed_path: Path,
    mapping_path: Path,
    fixed_ct: sitk.Image,
    transform: sitk.Transform,
    seg_dir: Path,
) -> dict[str, Path]:
    packed = read_image(packed_path)
    mapping = load_label_map(mapping_path)
    resampler = sitk.ResampleImageFilter()
    resampler.SetReferenceImage(fixed_ct)
    resampler.SetInterpolator(sitk.sitkNearestNeighbor)
    resampler.SetDefaultPixelValue(0)
    resampler.SetTransform(transform)
    transferred = resampler.Execute(packed)
    transferred = sitk.Cast(transferred, sitk.sitkUInt16)
    seg_dir.mkdir(parents=True, exist_ok=True)
    write_mha(transferred, seg_dir / f"{PACKED_STEM}.mha")
    (seg_dir / PACKED_JSON).write_text(mapping_path.read_text(encoding="utf-8"), encoding="utf-8")
    written: dict[str, Path] = {}
    for name, mask in unpack_labels(transferred, mapping).items():
        written[name] = write_mha(mask, seg_dir / f"{name}.mha")
        count = int(np.count_nonzero(sitk.GetArrayFromImage(mask)))
        if count == 0:
            logger.warning("transferred mask %s is empty", name)
    return written


def convert_nrrd_dir(folder: str | Path) -> list[Path]:
    """Write compressed .mha next to each .nrrd / uncompressed image in folder."""
    folder = Path(folder)
    written: list[Path] = []
    for path in sorted(folder.iterdir()):
        if not path.is_file():
            continue
        if path.suffix.lower() not in (".nrrd", ".mhd"):
            continue
        dest = path.with_suffix(".mha")
        image = read_image(path)
        written.append(write_mha(image, dest))
        logger.info("converted %s -> %s", path.name, dest.name)
    return written
