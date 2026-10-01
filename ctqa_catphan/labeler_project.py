"""vtk_image_labeler_3d project JSON next to a CT and its mask files."""

from __future__ import annotations

import csv
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .app_settings import load_settings, mask_stems
from .image_io import find_image

logger = logging.getLogger(__name__)

PROJECT_JSON_NAME = "vtk_image_labeler_3d.project.json"
PROJECT_KIND = "vtk_image_labeler_3d_project"
CSV_SKIP_SUBSTR = ("copy",)
CSV_ORDER = (
    "HU.csv",
    "UF.csv",
    "UF.uniformity.csv",
    "HC.csv",
    "HC.RMTF.csv",
    "HC.RMTF.calc.csv",
    "LC.csv",
    "geo.csv",
    "geo.dist.csv",
    "DT.csv",
    "DT.dist.csv",
)
LAYER_COLORS = [
    [255, 0, 0],
    [0, 255, 0],
    [0, 128, 255],
    [255, 255, 0],
    [255, 0, 255],
    [0, 255, 255],
    [255, 128, 0],
    [128, 0, 255],
    [180, 80, 40],
    [80, 180, 80],
    [80, 80, 180],
    [200, 200, 80],
]


def project_json_path(folder: str | Path) -> Path:
    return Path(folder) / PROJECT_JSON_NAME


def _rel(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def write_project_json(
    folder: str | Path,
    machine: dict,
    *,
    image_dir: str | Path | None = None,
    mask_dir: str | Path | None = None,
    include_fuz: bool = False,
) -> Path:
    """Write ``vtk_image_labeler_3d.project.json`` with paths relative to *folder*."""
    root = Path(folder)
    root.mkdir(parents=True, exist_ok=True)
    image_root = Path(image_dir) if image_dir else root
    mask_root = Path(mask_dir) if mask_dir else root
    ct = find_image(image_root, "CT")
    if ct is None:
        raise FileNotFoundError(f"no CT image in {image_root}")
    layers: list[dict] = []
    stems = list(mask_stems(machine) if machine else [])
    if include_fuz:
        stems = ["fuz_mask"] + stems
    for i, stem in enumerate(stems):
        found = find_image(mask_root, stem)
        if found is None:
            continue
        color = LAYER_COLORS[i % len(LAYER_COLORS)]
        layers.append(
            {
                "name": stem,
                "file": _rel(found, root),
                "color": color,
                "alpha": 0.45,
            }
        )
    payload = {
        "kind": PROJECT_KIND,
        "image": _rel(ct, root),
        "window_settings": {
            "level": 40,
            "width": 400,
        },
        "segmentations": layers,
    }
    dest = project_json_path(root)
    dest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    logger.info("wrote %s (%s layers)", dest, len(layers))
    return dest


def write_baseline_project(baseline_dir: str | Path, machine: dict) -> Path:
    return write_project_json(baseline_dir, machine, include_fuz=True)


def write_case_project(case_dir: str | Path, machine: dict) -> Path:
    case = Path(case_dir)
    mask_dir = case / "2.seg" if (case / "2.seg").is_dir() else case
    return write_project_json(case, machine, image_dir=case, mask_dir=mask_dir)


def csv_paths(folder: str | Path) -> list[Path]:
    folder = Path(folder)
    if not folder.is_dir():
        return []
    files = [
        p
        for p in folder.glob("*.csv")
        if p.is_file() and not any(s in p.name.lower() for s in CSV_SKIP_SUBSTR)
    ]
    rank = {name.lower(): i for i, name in enumerate(CSV_ORDER)}
    files.sort(key=lambda p: (rank.get(p.name.lower(), 1000), p.name.lower()))
    return files


def read_csv_table(path: str | Path) -> list[list[str]]:
    text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    rows = list(csv.reader(text.splitlines()))
    return [[cell.strip() for cell in row] for row in rows if any(cell.strip() for cell in row)]


def labeler_command(project_json: str | Path, data: dict | None = None) -> list[str]:
    """argv to launch Image Labeler 3D with *project_json*."""
    settings = data if data is not None else load_settings()
    configured = ""
    viewer = settings.get("Viewer") if isinstance(settings.get("Viewer"), dict) else {}
    configured = str(
        (viewer or {}).get("vtk_image_labeler_3d")
        or settings.get("vtk_image_labeler_3d")
        or os.environ.get("VTK_IMAGE_LABELER_3D")
        or ""
    ).strip()
    json_path = str(Path(project_json).resolve())
    if configured:
        exe = Path(configured)
        if exe.suffix.lower() == ".py":
            return [sys.executable, str(exe), json_path]
        return [str(exe), json_path]
    found = shutil.which("vtk-image-labeler-3d") or shutil.which("ImageLabeler3D")
    if found:
        return [found, json_path]
    app_py = _bundled_labeler_app()
    if app_py is not None:
        return [sys.executable, str(app_py), json_path]
    return [sys.executable, "-m", "vtk_image_labeler_3d", json_path]


def _bundled_labeler_app() -> Path | None:
    here = Path(__file__).resolve()
    # ctqa_catphan / CTQA-CatPhan-python / projects / MachineQA
    candidates = [
        here.parents[3] / "_ref_projects" / "vtk_image_labeler_3d" / "src" / "vtk_image_labeler_3d" / "app.py",
        here.parents[2] / "_ref_projects" / "vtk_image_labeler_3d" / "src" / "vtk_image_labeler_3d" / "app.py",
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def launch_labeler(project_json: str | Path, data: dict | None = None) -> subprocess.Popen:
    cmd = labeler_command(project_json, data)
    logger.info("launch Image Labeler 3D: %s", " ".join(cmd))
    cwd = None
    if len(cmd) >= 2 and cmd[1].lower().endswith("app.py"):
        cwd = str(Path(cmd[1]).parent)
    return subprocess.Popen(cmd, cwd=cwd)
