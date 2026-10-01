"""CatPhan measurements: HU/UF means, HC/LC std, geo/DT distances, UF INU, HC RMTF."""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import SimpleITK as sitk

from .app_settings import mask_count
from .image_io import find_image, read_image, write_mha

logger = logging.getLogger(__name__)


def masked_stats(image: sitk.Image, mask: sitk.Image) -> tuple[float, float, float, float]:
    img = sitk.GetArrayFromImage(image).astype(np.float64)
    m = sitk.GetArrayFromImage(mask)
    if m.shape != img.shape:
        mask = sitk.Resample(mask, image, sitk.Transform(), sitk.sitkNearestNeighbor, 0, mask.GetPixelID())
        m = sitk.GetArrayFromImage(mask)
    values = img[m > 0.5]
    if values.size == 0:
        return 0.0, 0.0, 0.0, 0.0
    return (
        float(np.min(values)),
        float(np.max(values)),
        float(np.mean(values)),
        float(np.std(values)),
    )


def write_csv(path: Path, headers: list[str], values: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        ",".join(headers) + "\n" + ",".join(str(v) for v in values) + "\n",
        encoding="utf-8",
    )


def _require_mask(mask_dir: Path, stem: str) -> Path:
    found = find_image(mask_dir, stem)
    if found is None:
        raise FileNotFoundError(f"mask not found: {mask_dir / stem}")
    return found


def measure_mean(ct: sitk.Image, mask_dir: Path, key: str, n: int, out_dir: Path) -> None:
    headers: list[str] = []
    values: list[float] = []
    for i in range(1, n + 1):
        stem = f"{key}{i}"
        mask = read_image(_require_mask(mask_dir, stem))
        _mn, _mx, mean, _std = masked_stats(ct, mask)
        headers.append(stem)
        values.append(mean)
        logger.info("%s mean=%s", stem, mean)
    write_csv(out_dir / f"{key}.csv", headers, values)


def measure_std(ct: sitk.Image, mask_dir: Path, key: str, n: int, out_dir: Path) -> None:
    headers: list[str] = []
    values: list[float] = []
    for i in range(1, n + 1):
        stem = f"{key}{i}"
        mask = read_image(_require_mask(mask_dir, stem))
        _mn, _mx, _mean, std = masked_stats(ct, mask)
        headers.append(stem)
        values.append(std)
        logger.info("%s std=%s", stem, std)
    write_csv(out_dir / f"{key}.csv", headers, values)


def bounding_box(mask: sitk.Image) -> tuple[int, int, int, int, int, int]:
    arr = sitk.GetArrayFromImage(mask)
    nz = np.nonzero(arr)
    size = mask.GetSize()
    if len(nz[0]) == 0:
        return 0, 0, 0, size[0], size[1], size[2]
    z0, z1 = int(np.min(nz[0])), int(np.max(nz[0])) + 1
    y0, y1 = int(np.min(nz[1])), int(np.max(nz[1])) + 1
    x0, x1 = int(np.min(nz[2])), int(np.max(nz[2])) + 1
    return x0, y0, z0, x1, y1, z1


def crop_bbox(image: sitk.Image, bbox: tuple[int, int, int, int, int, int]) -> sitk.Image:
    x0, y0, z0, x1, y1, z1 = bbox
    size = image.GetSize()
    x0 = max(0, min(x0, size[0] - 1))
    y0 = max(0, min(y0, size[1] - 1))
    z0 = max(0, min(z0, size[2] - 1))
    x1 = max(x0 + 1, min(x1, size[0]))
    y1 = max(y0 + 1, min(y1, size[1]))
    z1 = max(z0 + 1, min(z1, size[2]))
    extract = sitk.ExtractImageFilter()
    extract.SetSize([x1 - x0, y1 - y0, z1 - z0])
    extract.SetIndex([x0, y0, z0])
    return extract.Execute(image)


def threshold_levels(image: sitk.Image, level0: float, th: float, level1: float) -> sitk.Image:
    arr = sitk.GetArrayFromImage(image)
    out = np.where(arr < th, level0, level1).astype(np.float32)
    img = sitk.GetImageFromArray(out)
    img.CopyInformation(image)
    return img


def center_of_gravity(image: sitk.Image) -> tuple[float, float, float]:
    arr = sitk.GetArrayFromImage(image).astype(np.float64)
    mass = float(np.sum(arr))
    if mass == 0:
        return 0.0, 0.0, 0.0
    z, y, x = np.meshgrid(
        np.arange(arr.shape[0]),
        np.arange(arr.shape[1]),
        np.arange(arr.shape[2]),
        indexing="ij",
    )
    cx = float(np.sum(x * arr) / mass)
    cy = float(np.sum(y * arr) / mass)
    cz = float(np.sum(z * arr) / mass)
    origin = image.GetOrigin()
    spacing = image.GetSpacing()
    return (
        origin[0] + cx * spacing[0],
        origin[1] + cy * spacing[1],
        origin[2] + cz * spacing[2],
    )


def measure_dist(
    ct: sitk.Image,
    mask_dir: Path,
    key: str,
    n: int,
    level0: float,
    th: float,
    level1: float,
    out_dir: Path,
) -> None:
    points: list[tuple[str, float, float, float]] = []
    for i in range(1, n + 1):
        stem = f"{key}{i}"
        mask = read_image(_require_mask(mask_dir, stem))
        bbox = bounding_box(mask)
        crop = crop_bbox(ct, bbox)
        th_img = threshold_levels(crop, level0, th, level1)
        write_mha(crop, out_dir / f"{stem}.crop.mha")
        write_mha(th_img, out_dir / f"{stem}.crop.th.mha")
        x, y, z = center_of_gravity(th_img)
        points.append((stem, x, y, z))
        logger.info("%s COM=%s %s %s", stem, x, y, z)
    lines = [",x[mm],y[mm],z[mm]"]
    lines.extend(f"{name},{x},{y},{z}" for name, x, y, z in points)
    (out_dir / f"{key}.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")

    dist_labels: list[str] = []
    dist_values: list[float] = []
    for i in range(len(points)):
        a = points[i]
        b = points[(i + 1) % len(points)]
        dx, dy, dz = a[1] - b[1], a[2] - b[2], a[3] - b[3]
        dist = (dx * dx + dy * dy + dz * dz) ** 0.5
        dist_labels.append(f"{a[0]}->{b[0]}")
        dist_values.append(dist)
    write_csv(out_dir / f"{key}.dist.csv", dist_labels, dist_values)


def integral_non_uniformity(values: list[float]) -> float:
    if not values:
        return 0.0
    mx, mn = max(values), min(values)
    denom = mx + mn
    if denom == 0:
        return 0.0
    return (mx - mn) / denom


def relative_mtf(values: list[float]) -> tuple[list[float], float]:
    if not values or values[0] == 0:
        raise ValueError("HC values empty or HC1 is zero")
    norm = [v / values[0] for v in values]
    index = next((i for i, v in enumerate(norm) if v < 0.5), -1)
    if index <= 0:
        raise ValueError("failed to find HC RMTF 50% crossing")
    y1, y2 = norm[index - 1], norm[index]
    x1, x2 = float(index - 1), float(index)
    a = (y2 - y1) / (x2 - x1) if x2 != x1 else 0.0
    b = y1 - a * x1
    x = (0.5 - b) / a if a != 0 else 0.0
    return norm, x


def _read_value_row(path: Path) -> tuple[list[str], list[float]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    headers = [p.strip() for p in lines[0].split(",")]
    values = [float(p) for p in lines[1].split(",")]
    return headers, values


def analyze(ct: sitk.Image, mask_dir: Path, out_dir: Path, machine: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    measure_mean(ct, mask_dir, "HU", mask_count(machine, "HU"), out_dir)
    measure_mean(ct, mask_dir, "UF", mask_count(machine, "UF"), out_dir)
    measure_std(ct, mask_dir, "HC", mask_count(machine, "HC"), out_dir)
    measure_std(ct, mask_dir, "LC", mask_count(machine, "LC"), out_dir)
    measure_dist(ct, mask_dir, "geo", mask_count(machine, "geo"), 1.0, -500.0, 0.0, out_dir)
    measure_dist(ct, mask_dir, "DT", mask_count(machine, "DT"), 0.0, 200.0, 1.0, out_dir)

    _headers, uf = _read_value_row(out_dir / "UF.csv")
    inu = integral_non_uniformity(uf)
    write_csv(out_dir / "UF.uniformity.csv", ["Uniformity"], [inu])

    hc_headers, hc = _read_value_row(out_dir / "HC.csv")
    norm, x50 = relative_mtf(hc)
    write_csv(out_dir / "HC.RMTF.csv", hc_headers, norm)
    write_csv(out_dir / "HC.RMTF.calc.csv", ["RMTF=0.5"], [x50])
