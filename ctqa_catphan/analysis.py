"""CatPhan measurements: HU/UF means, HC/LC std, geo/DT distances, UF INU, HC RMTF."""

from __future__ import annotations

import csv
import json
import logging
import re
from pathlib import Path

import numpy as np
import SimpleITK as sitk

from .app_settings import mask_count
from .image_io import write_mha
from .masks import load_named_masks

logger = logging.getLogger(__name__)

RESULT_JSON_NAME = "analysis.result.json"
CASE_RESULT_NAME = "result.json"
COMPARE_SPECS = (
    ("HU", "HU_tol", "HU"),
    ("UF", "UF_tol", "UF"),
    ("UF.uniformity", "UF.uniformity_tol", ""),
    ("LC", "LC_tol", "LC"),
    ("geo.dist", "geo_tol", "geo"),
    ("DT.dist", "DT_tol", "DT"),
    ("HC.RMTF", "HC_RMTF_tol", "HC"),
    ("HC.RMTF.calc", "HC_RMTF50_tol", ""),
)
VALUE_TABLES = (
    "HU",
    "UF",
    "UF.uniformity",
    "HC",
    "HC.RMTF",
    "HC.RMTF.calc",
    "LC",
    "geo.dist",
    "DT.dist",
)
POINT_TABLES = ("geo", "DT")
TABLE_ORDER = (
    "HU",
    "UF",
    "UF.uniformity",
    "HC",
    "HC.RMTF",
    "HC.RMTF.calc",
    "LC",
    "geo",
    "geo.dist",
    "DT",
    "DT.dist",
)
CSV_SKIP_SUBSTR = ("copy",)


def analysis_result_path(folder: str | Path) -> Path:
    return Path(folder) / RESULT_JSON_NAME


def case_result_path(folder: str | Path) -> Path:
    folder = Path(folder)
    nested = folder / "3.analysis" / CASE_RESULT_NAME
    if nested.is_file():
        return nested
    return folder / CASE_RESULT_NAME


_RESULT_HEAD_RE = re.compile(r'"result"\s*:\s*"(pass|fail)"', re.IGNORECASE)


def csv_key(filename: str) -> str:
    name = Path(filename).name
    if name.lower().endswith(".csv"):
        return name[:-4]
    return name


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


def _need_mask(masks: dict[str, sitk.Image], stem: str) -> sitk.Image:
    mask = masks.get(stem)
    if mask is None:
        raise FileNotFoundError(f"mask not found: {stem}")
    return mask


def measure_mean(ct: sitk.Image, masks: dict[str, sitk.Image], key: str, n: int) -> dict:
    headers: list[str] = []
    values: list[float] = []
    for i in range(1, n + 1):
        stem = f"{key}{i}"
        mask = _need_mask(masks, stem)
        _mn, _mx, mean, _std = masked_stats(ct, mask)
        headers.append(stem)
        values.append(mean)
        logger.info("%s mean=%s", stem, mean)
    return {"labels": headers, "values": values}


def measure_std(ct: sitk.Image, masks: dict[str, sitk.Image], key: str, n: int) -> dict:
    headers: list[str] = []
    values: list[float] = []
    for i in range(1, n + 1):
        stem = f"{key}{i}"
        mask = _need_mask(masks, stem)
        _mn, _mx, _mean, std = masked_stats(ct, mask)
        headers.append(stem)
        values.append(std)
        logger.info("%s std=%s", stem, std)
    return {"labels": headers, "values": values}


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
    masks: dict[str, sitk.Image],
    key: str,
    n: int,
    level0: float,
    th: float,
    level1: float,
    out_dir: Path,
) -> tuple[dict, dict]:
    points: list[dict] = []
    for i in range(1, n + 1):
        stem = f"{key}{i}"
        mask = _need_mask(masks, stem)
        bbox = bounding_box(mask)
        crop = crop_bbox(ct, bbox)
        th_img = threshold_levels(crop, level0, th, level1)
        write_mha(crop, out_dir / f"{stem}.crop.mha")
        write_mha(th_img, out_dir / f"{stem}.crop.th.mha")
        x, y, z = center_of_gravity(th_img)
        points.append({"id": stem, "x": x, "y": y, "z": z})
        logger.info("%s COM=%s %s %s", stem, x, y, z)
    dist_labels: list[str] = []
    dist_values: list[float] = []
    for i in range(len(points)):
        a = points[i]
        b = points[(i + 1) % len(points)]
        dx, dy, dz = a["x"] - b["x"], a["y"] - b["y"], a["z"] - b["z"]
        dist = (dx * dx + dy * dy + dz * dz) ** 0.5
        dist_labels.append(f"{a['id']}->{b['id']}")
        dist_values.append(dist)
    return {"points": points}, {"labels": dist_labels, "values": dist_values}


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


def _as_number(text: str):
    try:
        return float(text)
    except (TypeError, ValueError):
        return text


def _read_csv_rows(path: Path) -> list[list[str]]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    rows = []
    for row in csv.reader(text.splitlines()):
        cells = [c.strip() for c in row]
        if any(cells):
            rows.append(cells)
    return rows


def _value_table_from_rows(rows: list[list[str]]) -> dict:
    labels = rows[0] if rows else []
    values = [_as_number(c) for c in rows[1]] if len(rows) > 1 else []
    return {"labels": labels, "values": values}


def _point_table_from_rows(rows: list[list[str]]) -> dict:
    points = []
    for row in rows[1:]:
        if not row:
            continue
        ident = row[0]
        nums = [_as_number(c) for c in row[1:4]]
        while len(nums) < 3:
            nums.append(0.0)
        points.append({"id": ident, "x": nums[0], "y": nums[1], "z": nums[2]})
    return {"points": points}


def result_from_csv_folder(folder: str | Path) -> dict:
    folder = Path(folder)
    if not folder.is_dir():
        return {}
    out: dict = {}
    for path in sorted(folder.glob("*.csv")):
        if any(s in path.name.lower() for s in CSV_SKIP_SUBSTR):
            continue
        key = csv_key(path.name)
        rows = _read_csv_rows(path)
        if not rows:
            continue
        if key in POINT_TABLES:
            out[key] = _point_table_from_rows(rows)
        else:
            out[key] = _value_table_from_rows(rows)
    return out


def write_analysis_result(folder: str | Path, data: dict) -> Path:
    dest = analysis_result_path(folder)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    logger.info("wrote %s", dest)
    return dest


def convert_csvs_to_json(folder: str | Path, *, overwrite: bool = False) -> Path | None:
    folder = Path(folder)
    dest = analysis_result_path(folder)
    if dest.is_file() and not overwrite:
        return dest
    data = result_from_csv_folder(folder)
    if not data:
        return None
    return write_analysis_result(folder, data)


def load_analysis_result(folder: str | Path, *, write_json_from_csv: bool = True) -> dict:
    folder = Path(folder)
    dest = analysis_result_path(folder)
    if dest.is_file():
        loaded = json.loads(dest.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}
    data = result_from_csv_folder(folder)
    if data and write_json_from_csv:
        write_analysis_result(folder, data)
    return data


def evaluate_against_baseline(case_data: dict, baseline_data: dict, machine: dict) -> dict:
    """Pass/fail vs baseline using the same tables and tols as the HTML report."""
    items: list[dict] = []
    for key, tol_key, mask_key in COMPARE_SPECS:
        labels, case_vals = table_labels_values(case_data, key)
        base_labels, base_vals = table_labels_values(baseline_data, key)
        try:
            tol = float(machine.get(tol_key) or 0)
        except (TypeError, ValueError):
            tol = 0.0
        n = min(len(base_labels), len(labels), len(case_vals), len(base_vals))
        if mask_key:
            limit = mask_count(machine, mask_key)
            if limit:
                n = min(n, limit)
        elif n:
            n = min(n, 1)
        for i in range(n):
            value = float(case_vals[i])
            baseline = float(base_vals[i])
            diff = value - baseline
            ok = abs(diff) < tol
            items.append(
                {
                    "table": key,
                    "label": base_labels[i] or labels[i],
                    "value": value,
                    "baseline": baseline,
                    "diff": diff,
                    "tol": tol,
                    "result": "pass" if ok else "fail",
                }
            )
    n_fail = sum(1 for item in items if item["result"] == "fail")
    n_pass = sum(1 for item in items if item["result"] == "pass")
    if not items:
        overall = "partial"
    elif n_fail:
        overall = "fail"
    else:
        overall = "pass"
    return {
        "result": overall,
        "n_pass": n_pass,
        "n_fail": n_fail,
        "failed": [f"{item['table']}.{item['label']}" for item in items if item["result"] == "fail"],
        "items": items,
    }


def write_case_result(result_dir: str | Path, baseline_dir: str | Path, machine: dict) -> Path | None:
    case_data = load_analysis_result(result_dir, write_json_from_csv=True)
    baseline_data = load_analysis_result(baseline_dir, write_json_from_csv=True)
    if not case_data or not baseline_data:
        return None
    summary = evaluate_against_baseline(case_data, baseline_data, machine)
    dest = Path(result_dir) / CASE_RESULT_NAME
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    logger.info("wrote %s result=%s", dest, summary["result"])
    return dest


def read_case_result(folder: str | Path) -> str | None:
    """Pass/fail from the start of result.json (avoid parsing the full items list)."""
    folder = Path(folder)
    for path in (folder / "3.analysis" / CASE_RESULT_NAME, folder / CASE_RESULT_NAME):
        try:
            with path.open("r", encoding="utf-8") as fh:
                head = fh.read(256)
        except OSError:
            continue
        match = _RESULT_HEAD_RE.search(head)
        if match:
            return match.group(1).lower()
    return None


def table_labels_values(data: dict, filename: str) -> tuple[list[str], list[float]]:
    block = data.get(csv_key(filename)) or {}
    if "points" in block:
        points = block.get("points") or []
        labels = [str(p.get("id") or "") for p in points]
        values = [float(p.get("x") or 0) for p in points]
        return labels, values
    labels = [str(x) for x in (block.get("labels") or [])]
    values = [float(x) for x in (block.get("values") or [])]
    return labels, values


def table_as_rows(data: dict, key: str) -> list[list[str]]:
    block = data.get(key) or {}
    if "points" in block:
        rows = [["id", "x[mm]", "y[mm]", "z[mm]"]]
        for point in block.get("points") or []:
            rows.append(
                [
                    str(point.get("id") or ""),
                    str(point.get("x") or ""),
                    str(point.get("y") or ""),
                    str(point.get("z") or ""),
                ]
            )
        return rows
    labels = [str(x) for x in (block.get("labels") or [])]
    values = [str(x) for x in (block.get("values") or [])]
    if not labels and not values:
        return []
    return [labels, values]


def analysis_tables_for_display(folder: str | Path) -> list[tuple[str, list[list[str]]]]:
    data = load_analysis_result(folder)
    tables: list[tuple[str, list[list[str]]]] = []
    seen = set()
    for key in TABLE_ORDER:
        if key not in data:
            continue
        rows = table_as_rows(data, key)
        if rows:
            tables.append((key, rows))
            seen.add(key)
    for key in data:
        if key in seen:
            continue
        rows = table_as_rows(data, key)
        if rows:
            tables.append((key, rows))
    return tables


def convert_existing_results(data: dict | None = None) -> list[Path]:
    from .app_settings import list_case_folders, load_settings, named_machines

    settings = data if data is not None else load_settings()
    written: list[Path] = []
    for machine in named_machines(settings):
        baseline = Path(str(machine.get("baseline_dir") or ""))
        path = convert_csvs_to_json(baseline) if baseline.is_dir() else None
        if path is not None:
            written.append(path)
        for case in list_case_folders(machine):
            analysis = case / "3.analysis"
            path = convert_csvs_to_json(analysis) if analysis.is_dir() else None
            if path is not None:
                written.append(path)
            if analysis.is_dir() and baseline.is_dir():
                result = write_case_result(analysis, baseline, machine)
                if result is not None:
                    written.append(result)
    return written


def analyze(ct: sitk.Image, mask_dir: Path, out_dir: Path, machine: dict) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    masks = load_named_masks(mask_dir, machine)
    result: dict = {}
    if mask_count(machine, "HU"):
        result["HU"] = measure_mean(ct, masks, "HU", mask_count(machine, "HU"))
    if mask_count(machine, "UF"):
        result["UF"] = measure_mean(ct, masks, "UF", mask_count(machine, "UF"))
        inu = integral_non_uniformity([float(v) for v in result["UF"]["values"]])
        result["UF.uniformity"] = {"labels": ["Uniformity"], "values": [inu]}
    if mask_count(machine, "HC"):
        result["HC"] = measure_std(ct, masks, "HC", mask_count(machine, "HC"))
        norm, x50 = relative_mtf([float(v) for v in result["HC"]["values"]])
        result["HC.RMTF"] = {"labels": list(result["HC"]["labels"]), "values": norm}
        result["HC.RMTF.calc"] = {"labels": ["RMTF=0.5"], "values": [x50]}
    if mask_count(machine, "LC"):
        result["LC"] = measure_std(ct, masks, "LC", mask_count(machine, "LC"))
    if mask_count(machine, "geo"):
        geo, geo_dist = measure_dist(
            ct, masks, "geo", mask_count(machine, "geo"), 1.0, -500.0, 0.0, out_dir
        )
        result["geo"] = geo
        result["geo.dist"] = geo_dist
    if mask_count(machine, "DT"):
        dt, dt_dist = measure_dist(
            ct, masks, "DT", mask_count(machine, "DT"), 0.0, 200.0, 1.0, out_dir
        )
        result["DT"] = dt
        result["DT.dist"] = dt_dist
    return write_analysis_result(out_dir, result)
