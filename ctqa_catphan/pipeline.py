"""One-case CatPhan pipeline: convert, pack-if-stale, register, transfer, analyze, report."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from .analysis import analyze, write_case_result
from .app_settings import default_machine, load_settings, machine_by_name
from .dicom_io import ensure_ct_mha
from .image_io import find_image, read_image
from .masks import pack_baseline, transfer_masks_transformix
from .registration import register_rigid, resolve_elastix_dir, resolve_elastix_param_dir
from .report import write_report

logger = logging.getLogger(__name__)


def copy_tree_files(src: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for path in src.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(src)
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.resolve() == path.resolve():
            continue
        shutil.copy2(path, target)


def run_case(
    case_dir: str | Path,
    *,
    machine_name: str = "",
    send_email: bool = False,
    data: dict | None = None,
) -> Path:
    settings = data if data is not None else load_settings()
    machine = machine_by_name(machine_name, settings) if machine_name else default_machine(settings)
    if machine is None:
        raise RuntimeError("no machine in settings.json (MACHINES)")
    case = Path(case_dir)
    baseline = Path(str(machine.get("baseline_dir") or ""))
    if not baseline.is_dir():
        raise FileNotFoundError(f"baseline_dir not found: {baseline}")

    ct_path = ensure_ct_mha(case)
    ct = read_image(ct_path)
    moving_path = find_image(baseline, "CT")
    if moving_path is None:
        raise FileNotFoundError(f"baseline CT not found in {baseline}")
    fuz = find_image(baseline, "fuz_mask")

    pack_baseline(baseline, machine)
    elastix_dir = resolve_elastix_dir(settings)
    reg_dir = case / "1.reg"
    register_rigid(
        ct_path,
        moving_path,
        reg_dir,
        moving_mask=fuz,
        param_dir=resolve_elastix_param_dir(machine),
        elastix_dir=elastix_dir,
    )

    seg_dir = case / "2.seg"
    transfer_masks_transformix(
        baseline,
        machine,
        seg_dir,
        reg_dir / "TransformParameters.1.txt",
        elastix_dir=elastix_dir,
    )

    result_dir = case / "3.analysis"
    analyze(ct, seg_dir, result_dir, machine)
    write_case_result(result_dir, baseline, machine)
    report = write_report(case, baseline, result_dir, machine)
    if send_email:
        from .emailer import send_report_file

        send_report_file(
            report,
            str(machine.get("NAME") or "CTQA"),
            extra_to=machine.get("new_case_email_to"),
            data=settings,
        )
    try:
        from .postprocess import run_post_processing

        run_post_processing(case, machine, data=settings)
    except Exception:
        logger.exception("post-processing failed for %s", case)
    from .temp_cleanup import start_post_analysis_cleanup

    start_post_analysis_cleanup(settings)
    logger.info("case complete: %s", case)
    return report
