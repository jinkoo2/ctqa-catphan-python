"""Baseline mask packing (stale-check) and transfer onto today's CT."""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
from pathlib import Path

import numpy as np
import SimpleITK as sitk

from .app_settings import label_map, mask_stems
from .image_io import find_image, read_image, write_mha
from .registration import is_unc_path, run_transformix, scratch_parent, write_label_transform_param

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
    if np.issubdtype(arr.dtype, np.floating):
        arr = np.rint(arr)
    arr = arr.astype(np.int32, copy=False)
    out: dict[str, sitk.Image] = {}
    for value, name in mapping.items():
        binary = (arr == int(value)).astype(np.uint8)
        image = sitk.GetImageFromArray(binary)
        image.CopyInformation(packed)
        out[name] = image
    return out


def load_named_masks(mask_dir: str | Path, machine: dict | None = None) -> dict[str, sitk.Image]:
    """In-memory binary masks from packed labels, else individual files."""
    folder = Path(mask_dir)
    packed = find_image(folder, PACKED_STEM)
    mapping_path = folder / PACKED_JSON
    if packed is not None and mapping_path.is_file():
        mapping = load_label_map(mapping_path)
        masks = unpack_labels(read_image(packed), mapping)
        logger.info("loaded %s packed labels from %s", len(masks), packed.name)
        return masks
    stems = mask_stems(machine or {})
    if not stems:
        return {}
    out: dict[str, sitk.Image] = {}
    missing: list[str] = []
    for stem in stems:
        found = find_image(folder, stem)
        if found is None:
            missing.append(stem)
            continue
        out[stem] = read_image(found)
    if missing:
        raise FileNotFoundError(f"mask not found: {folder} ({', '.join(missing)})")
    return out


def _warn_empty_labels(packed: sitk.Image, mapping: dict[int, str]) -> None:
    arr = sitk.GetArrayFromImage(packed)
    if np.issubdtype(arr.dtype, np.floating):
        arr = np.rint(arr)
    for value, name in mapping.items():
        if int(np.count_nonzero(arr == int(value))) == 0:
            logger.warning("transferred mask %s is empty", name)


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
    dest = write_mha(transferred, seg_dir / f"{PACKED_STEM}.mha")
    json_dest = seg_dir / PACKED_JSON
    json_dest.write_text(mapping_path.read_text(encoding="utf-8"), encoding="utf-8")
    _warn_empty_labels(transferred, mapping)
    return {PACKED_STEM: dest, "labels": json_dest}


def transfer_masks_transformix(
    baseline_dir: str | Path,
    machine: dict,
    seg_dir: str | Path,
    transform_param: str | Path,
    *,
    elastix_dir: str | Path | None = None,
) -> dict[str, Path]:
    """Warp packed baseline labels once with transformix.exe (no unpack to disk)."""
    dest = Path(seg_dir)
    dest.mkdir(parents=True, exist_ok=True)
    packed_path = pack_baseline(baseline_dir, machine)
    mapping_path = packed_paths(Path(baseline_dir))[1]
    mapping = load_label_map(mapping_path)
    work = Path(tempfile.mkdtemp(prefix="tfx_", dir=str(scratch_parent())))
    try:
        moving = packed_path
        if is_unc_path(packed_path):
            moving = work / packed_path.name
            shutil.copy2(packed_path, moving)
            logger.info("copied packed masks locally for transformix: %s", moving)
        tp_labels = write_label_transform_param(
            Path(transform_param),
            Path(transform_param).with_name("TransformParameters.labels.txt"),
        )
        logger.info("transformix packed labels %s -> %s", packed_path.name, dest)
        result = run_transformix(moving, work, tp_labels, elastix_dir=elastix_dir)
        transferred = read_image(result)
        if transferred.GetPixelID() != sitk.sitkUInt16:
            transferred = sitk.Cast(
                sitk.Round(sitk.Cast(transferred, sitk.sitkFloat32)),
                sitk.sitkUInt16,
            )
        packed_dest = write_mha(transferred, dest / f"{PACKED_STEM}.mha")
        json_dest = dest / PACKED_JSON
        json_dest.write_text(mapping_path.read_text(encoding="utf-8"), encoding="utf-8")
        _warn_empty_labels(transferred, mapping)
        return {PACKED_STEM: packed_dest, "labels": json_dest}
    finally:
        shutil.rmtree(work, ignore_errors=True)


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
