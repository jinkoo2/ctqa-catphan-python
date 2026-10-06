"""Register baseline CT onto today's CT with external elastix.exe / transformix.exe.

Matches C# ``etx.elastix`` / ``etx.transformix``: translation then rigid parameter
files, optional ``-fMask`` / ``-mMask``, output ``1.reg/TransformParameters.1.txt``.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import SimpleITK as sitk

from .app_settings import elastix_dir_setting
from .image_io import find_image, read_image, write_mha

logger = logging.getLogger(__name__)

TRANSLATION_PARAM = "Parameters_Translation.txt"
RIGID_PARAM = "Parameters_Rigid.txt"
_PARAM_LINE = re.compile(r"^\((?P<key>\w+)\s+(?P<vals>.*)\)\s*$")
ELASTIX_TIMEOUT_SEC = 30 * 60
TRANSFORMIX_TIMEOUT_SEC = 10 * 60


def default_elastix_param_dir() -> Path:
    return Path(__file__).resolve().parent / "etx_params"


def resolve_elastix_param_dir(machine: dict | None = None) -> Path:
    raw = str((machine or {}).get("elastix_param_dir") or "").strip()
    if raw:
        path = Path(raw)
        if not path.is_dir():
            raise FileNotFoundError(f"elastix_param_dir not found: {path}")
        return path
    return default_elastix_param_dir()


def elastix_param_files(param_dir: str | Path | None = None) -> tuple[Path, Path]:
    folder = Path(param_dir) if param_dir else default_elastix_param_dir()
    translation = folder / TRANSLATION_PARAM
    rigid = folder / RIGID_PARAM
    missing = [str(p) for p in (translation, rigid) if not p.is_file()]
    if missing:
        raise FileNotFoundError("elastix parameter file not found: " + ", ".join(missing))
    return translation, rigid


def resolve_elastix_dir(data: dict | None = None, elastix_dir: str | Path | None = None) -> Path | None:
    raw = str(elastix_dir or "").strip() or elastix_dir_setting(data)
    return Path(raw) if raw else None


def find_elastix_exe(elastix_dir: str | Path | None = None, name: str = "elastix") -> Path:
    folder = Path(elastix_dir) if elastix_dir else None
    if folder is not None:
        for path in (folder / f"{name}.exe", folder / name):
            if path.is_file():
                return path
        raise FileNotFoundError(
            f"{name}.exe not found in {folder}. Set Elastix.elastix_dir to the folder "
            "that contains elastix.exe and transformix.exe."
        )
    which = shutil.which(f"{name}.exe") or shutil.which(name)
    if which:
        return Path(which)
    raise FileNotFoundError(
        f"{name}.exe not found on PATH. Set Elastix.elastix_dir in settings.json "
        "(folder that contains elastix.exe and transformix.exe)."
    )


def find_transformix_exe(elastix_dir: str | Path | None = None) -> Path:
    return find_elastix_exe(elastix_dir, name="transformix")


def build_elastix_args(
    fixed: str | Path,
    moving: str | Path,
    out_dir: str | Path,
    param_files: list[Path],
    *,
    fixed_mask: str | Path | None = None,
    moving_mask: str | Path | None = None,
) -> list[str]:
    args = ["-f", str(fixed), "-m", str(moving), "-out", str(out_dir)]
    if fixed_mask:
        args += ["-fMask", str(fixed_mask)]
    if moving_mask:
        args += ["-mMask", str(moving_mask)]
    for param in param_files:
        args += ["-p", str(param)]
    return args


def build_transformix_args(
    moving: str | Path,
    out_dir: str | Path,
    transform_param: str | Path,
) -> list[str]:
    return ["-in", str(moving), "-out", str(out_dir), "-tp", str(transform_param)]


def is_unc_path(path: str | Path) -> bool:
    text = str(path).replace("/", "\\")
    if text.upper().startswith("\\\\?\\UNC\\"):
        return True
    return text.startswith("\\\\") and not text.startswith("\\\\?\\")


def scratch_parent(preferred: str | Path | None = None) -> Path:
    """Writable work folder: optional override, else the user system temp directory."""
    candidates: list[Path] = []
    raw = str(preferred or "").strip()
    if raw:
        candidates.append(Path(raw))
    candidates.append(Path(tempfile.gettempdir()))
    seen: set[Path] = set()
    for path in candidates:
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if resolved in seen:
            continue
        seen.add(resolved)
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError:
            logger.warning("scratch dir not usable: %s", path)
            continue
        if path.is_dir():
            return path
    raise RuntimeError("no writable temp directory")


def _path_spellings(path: Path) -> list[str]:
    raw = str(path)
    seen: list[str] = []
    for item in (raw, raw.replace("\\", "/"), raw.replace("/", "\\")):
        if item and item not in seen:
            seen.append(item)
    return seen


def rewrite_copied_elastix_paths(folder: Path, old_root: Path, new_root: Path) -> None:
    olds = _path_spellings(Path(old_root))
    new = str(Path(new_root))
    for path in folder.glob("TransformParameters*.txt"):
        text = path.read_text(encoding="utf-8", errors="replace")
        updated = text
        for old in olds:
            updated = updated.replace(old, new)
        if updated != text:
            path.write_text(updated, encoding="utf-8")


def _copy_work_to_dest(work: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for item in work.iterdir():
        target = dest / item.name
        if item.is_file():
            shutil.copy2(item, target)
        elif item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)


def _tail_file(path: Path, limit: int = 4000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[-limit:].strip()
    except OSError:
        return ""


def _run_exe(exe: Path, args: list[str], *, timeout: int, log_dir: Path) -> None:
    cmd = [str(exe), *args]
    logger.info("%s", " ".join(cmd))
    env = os.environ.copy()
    env["PATH"] = str(exe.parent) + os.pathsep + env.get("PATH", "")
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = log_dir / f"{exe.stem}_stdout.txt"
    stderr_path = log_dir / f"{exe.stem}_stderr.txt"
    with stdout_path.open("wb") as out, stderr_path.open("wb") as err:
        proc = subprocess.run(
            cmd,
            cwd=str(exe.parent),
            env=env,
            stdout=out,
            stderr=err,
            timeout=timeout,
            check=False,
        )
    if proc.returncode != 0:
        tail = _tail_file(stderr_path, 2000) or _tail_file(stdout_path, 2000)
        raise RuntimeError(f"{exe.name} failed (exit {proc.returncode}): {tail}")


def _local_out_dir(dest: Path) -> tuple[Path, Path | None]:
    dest.mkdir(parents=True, exist_ok=True)
    if not is_unc_path(dest):
        return dest, None
    work = Path(tempfile.mkdtemp(prefix="elastix_out_", dir=str(scratch_parent())))
    logger.info("UNC -out %s; running locally in %s", dest, work)
    return work, work


def run_elastix(
    fixed: str | Path,
    moving: str | Path,
    out_dir: str | Path,
    param_files: list[Path],
    *,
    fixed_mask: str | Path | None = None,
    moving_mask: str | Path | None = None,
    elastix_dir: str | Path | None = None,
) -> Path:
    dest = Path(out_dir)
    work, cleanup = _local_out_dir(dest)
    try:
        exe = find_elastix_exe(elastix_dir)
        _run_exe(
            exe,
            build_elastix_args(
                fixed,
                moving,
                work,
                param_files,
                fixed_mask=fixed_mask,
                moving_mask=moving_mask,
            ),
            timeout=ELASTIX_TIMEOUT_SEC,
            log_dir=work,
        )
        log = work / "elastix.log"
        if not log.is_file():
            extra = _tail_file(work / "elastix_stdout.txt") or _tail_file(work / "elastix_stderr.txt")
            detail = f"\n{extra}" if extra else ""
            if cleanup is not None:
                try:
                    _copy_work_to_dest(work, dest)
                except OSError:
                    pass
            raise RuntimeError(f"elastix log not found: {dest / 'elastix.log'}{detail}")
        tp_name = f"TransformParameters.{len(param_files) - 1}.txt"
        tp = work / tp_name
        if not tp.is_file():
            raise FileNotFoundError(f"Output transformation file not found: {dest / tp_name}")
        if cleanup is not None:
            _copy_work_to_dest(work, dest)
            rewrite_copied_elastix_paths(dest, work, dest)
            tp = dest / tp_name
        return tp
    finally:
        if cleanup is not None:
            shutil.rmtree(cleanup, ignore_errors=True)


def find_registered_result(reg_dir: str | Path) -> Path | None:
    """Final elastix result: two-stage ``result.1.mha``, else one-stage ``result.0.mha``."""
    dest = Path(reg_dir)
    for name in ("result.1.mha", "result.0.mha", "result.mha", "baseline_on_today.mha"):
        path = dest / name
        if path.is_file():
            return path
    return None


def list_transform_param_files(reg_dir: str | Path) -> list[Path]:
    folder = Path(reg_dir)
    found: list[tuple[int, Path]] = []
    for path in folder.glob("TransformParameters.*.txt"):
        parts = path.name.split(".")
        if len(parts) < 3:
            continue
        try:
            found.append((int(parts[1]), path))
        except ValueError:
            continue
    found.sort()
    return [path for _idx, path in found]


def _stage_from_pmap(pmap: dict[str, list[str]]) -> dict:
    name = _first(pmap, "Transform")
    params = _floats(pmap, "TransformParameters")
    if name == "TranslationTransform" and len(params) >= 3:
        return {
            "type": name,
            "translation_mm": [params[0], params[1], params[2]],
            "rotation_deg": [0.0, 0.0, 0.0],
        }
    if name == "EulerTransform" and len(params) >= 6:
        return {
            "type": name,
            "translation_mm": [params[3], params[4], params[5]],
            "rotation_deg": [math.degrees(params[0]), math.degrees(params[1]), math.degrees(params[2])],
        }
    return {
        "type": name or "Unknown",
        "translation_mm": params[:3] if len(params) >= 3 else [],
        "rotation_deg": [],
        "parameters": params,
    }


def registration_summary(reg_dir: str | Path) -> dict:
    """Parse elastix ``TransformParameters.*.txt`` into mm / deg stages."""
    folder = Path(reg_dir)
    stages: list[dict] = []
    for path in list_transform_param_files(folder):
        pmap = read_elastix_param_file(path)
        stage = _stage_from_pmap(pmap)
        stage["file"] = path.name
        stages.append(stage)
    result = find_registered_result(folder)
    return {
        "reg_dir": str(folder),
        "result": str(result) if result is not None else "",
        "stages": stages,
    }


def resample_onto(moving: sitk.Image, fixed: sitk.Image, default_value: float = -1024.0) -> sitk.Image:
    return sitk.Resample(moving, fixed, sitk.Transform(), sitk.sitkLinear, default_value, sitk.sitkInt16)


def case_has_registration(case_dir: str | Path) -> bool:
    case = Path(case_dir)
    return find_image(case, "CT") is not None and find_registered_result(case / "1.reg") is not None


def find_transformix_result(out_dir: str | Path) -> Path:
    dest = Path(out_dir)
    for name in ("result.mha", "result.1.mha", "result.0.mha", "result.nii", "result.nii.gz"):
        path = dest / name
        if path.is_file():
            return path
    raise FileNotFoundError(f"transformix/elastix result image not found in {dest}")


def write_label_transform_param(src: str | Path, dest: str | Path) -> Path:
    """Copy an elastix transform file and force nearest-neighbor resampling for label maps."""
    src = Path(src)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    found_interp = False
    found_order = False
    lines: list[str] = []
    for raw in src.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = raw.strip()
        if stripped.startswith("(ResampleInterpolator"):
            lines.append('(ResampleInterpolator "FinalNearestNeighborInterpolator")')
            found_interp = True
            continue
        if stripped.startswith("(FinalBSplineInterpolationOrder"):
            lines.append("(FinalBSplineInterpolationOrder 0)")
            found_order = True
            continue
        if stripped.startswith("(ResultImagePixelType"):
            lines.append('(ResultImagePixelType "short")')
            continue
        lines.append(raw)
    if not found_interp:
        lines.append('(ResampleInterpolator "FinalNearestNeighborInterpolator")')
    if not found_order:
        lines.append("(FinalBSplineInterpolationOrder 0)")
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return dest


def run_transformix(
    moving: str | Path,
    out_dir: str | Path,
    transform_param: str | Path,
    *,
    elastix_dir: str | Path | None = None,
) -> Path:
    dest = Path(out_dir)
    work, cleanup = _local_out_dir(dest)
    try:
        exe = find_transformix_exe(elastix_dir)
        _run_exe(
            exe,
            build_transformix_args(moving, work, transform_param),
            timeout=TRANSFORMIX_TIMEOUT_SEC,
            log_dir=work,
        )
        result = find_transformix_result(work)
        if cleanup is not None:
            _copy_work_to_dest(work, dest)
            result = dest / result.name
        return result
    finally:
        if cleanup is not None:
            shutil.rmtree(cleanup, ignore_errors=True)


def read_elastix_param_file(path: str | Path) -> dict[str, list[str]]:
    """Parse an elastix ``(Key value ...)`` text file."""
    out: dict[str, list[str]] = {}
    for raw in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("//") or line.startswith("#"):
            continue
        match = _PARAM_LINE.match(line)
        if match is None:
            continue
        tokens = [quoted or bare for quoted, bare in re.findall(r'"([^"]*)"|([^\s]+)', match.group("vals"))]
        out[match.group("key")] = tokens
    return out


def _first(pmap: dict[str, list[str]], key: str, default: str = "") -> str:
    values = pmap.get(key) or []
    return str(values[0]) if values else default


def _floats(pmap: dict[str, list[str]], key: str) -> list[float]:
    return [float(v) for v in (pmap.get(key) or [])]


def parameter_map_to_transform(pmap: dict[str, list[str]]) -> sitk.Transform:
    name = _first(pmap, "Transform")
    params = _floats(pmap, "TransformParameters")
    if name == "TranslationTransform":
        if len(params) != 3:
            raise ValueError(f"TranslationTransform needs 3 parameters, got {params}")
        return sitk.TranslationTransform(3, params)
    if name == "EulerTransform":
        if len(params) != 6:
            raise ValueError(f"EulerTransform needs 6 parameters, got {params}")
        euler = sitk.Euler3DTransform()
        euler.SetComputeZYX(_first(pmap, "ComputeZYX").lower() == "true")
        center = _floats(pmap, "CenterOfRotationPoint")
        if len(center) == 3:
            euler.SetCenter(center)
        euler.SetParameters(params)
        return euler
    raise ValueError(f"unsupported elastix transform: {name}")


def transform_from_elastix_file(path: str | Path) -> sitk.Transform:
    """Compose this elastix transform with ``InitialTransformParametersFileName``."""
    path = Path(path)
    pmap = read_elastix_param_file(path)
    current = parameter_map_to_transform(pmap)
    initial_name = _first(pmap, "InitialTransformParametersFileName")
    if not initial_name or initial_name == "NoInitialTransform":
        return current
    initial_path = Path(initial_name)
    if not initial_path.is_file():
        initial_path = path.parent / Path(initial_name).name
    if not initial_path.is_file():
        raise FileNotFoundError(f"InitialTransformParametersFileName not found: {initial_name}")
    initial = transform_from_elastix_file(initial_path)
    composite = sitk.CompositeTransform(3)
    composite.AddTransform(current)
    composite.AddTransform(initial)
    return composite


def _write_transform_json(dest: Path, transform: sitk.Transform) -> None:
    actual = transform
    if hasattr(transform, "GetNumberOfTransforms") and transform.GetNumberOfTransforms() > 0:
        actual = transform.GetNthTransform(transform.GetNumberOfTransforms() - 1)
    params = list(actual.GetParameters()) if hasattr(actual, "GetParameters") else []
    center = list(actual.GetCenter()) if hasattr(actual, "GetCenter") else []
    (dest / "transform.json").write_text(
        json.dumps(
            {
                "type": type(actual).__name__,
                "parameters": params,
                "center": center,
                "backend": "elastix.exe",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _save_baseline_on_today(
    dest: Path,
    moving: Path,
    transform_param: Path,
    elastix_dir: str | Path | None,
) -> None:
    try:
        result = find_transformix_result(dest)
    except FileNotFoundError:
        result = run_transformix(moving, dest, transform_param, elastix_dir=elastix_dir)
    image = read_image(result)
    if image.GetPixelID() != sitk.sitkInt16:
        image = sitk.Cast(image, sitk.sitkInt16)
    write_mha(image, dest / "baseline_on_today.mha")


def register_rigid(
    fixed: str | Path,
    moving: str | Path,
    out_dir: str | Path,
    *,
    fixed_mask: str | Path | None = None,
    moving_mask: str | Path | None = None,
    param_dir: str | Path | None = None,
    elastix_dir: str | Path | None = None,
    number_of_iterations: int = 200,
    histogram_bins: int = 50,
) -> sitk.Transform:
    """Translation then rigid via ``elastix.exe``, matching C# ``etx.elastix``."""
    del number_of_iterations, histogram_bins
    dest = Path(out_dir)
    dest.mkdir(parents=True, exist_ok=True)
    translation_file, rigid_file = elastix_param_files(param_dir)
    logger.info("starting elastix.exe translation+rigid (moving mask=%s)", bool(moving_mask))
    tp = run_elastix(
        fixed,
        moving,
        dest,
        [translation_file, rigid_file],
        fixed_mask=fixed_mask,
        moving_mask=moving_mask,
        elastix_dir=elastix_dir,
    )
    logger.info("elastix.exe finished: %s", tp)
    transform = transform_from_elastix_file(tp)
    sitk.WriteTransform(transform, str(dest / "transform.tfm"))
    _write_transform_json(dest, transform)
    _save_baseline_on_today(dest, Path(moving), tp, elastix_dir)
    return transform


def load_transform(reg_dir: str | Path) -> sitk.Transform:
    folder = Path(reg_dir)
    tfm = folder / "transform.tfm"
    if tfm.is_file():
        return sitk.ReadTransform(str(tfm))
    last = folder / "TransformParameters.1.txt"
    if last.is_file():
        return transform_from_elastix_file(last)
    raise FileNotFoundError(tfm)
