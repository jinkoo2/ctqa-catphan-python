"""Ordered post-analysis steps from settings ``PostProcessing``."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from .app_settings import DOCUFORMS2_CTQA_TYPE, post_processing_steps
from .emailer import send_error_email

logger = logging.getLogger(__name__)

StepFn = Callable[[dict, Path, dict | None], None]


def _run_docuforms2_ctqa(step: dict, case_dir: Path, machine_cfg: dict | None) -> None:
    from .docuforms_ctqa import upload_case

    upload_case(case_dir, machine_cfg, step)


REGISTRY: dict[str, StepFn] = {
    DOCUFORMS2_CTQA_TYPE: _run_docuforms2_ctqa,
}


def run_post_processing(
    case_dir: str | Path,
    machine_cfg: dict | None,
    data: dict | None = None,
) -> None:
    """Run enabled PostProcessing steps. Failures are logged and emailed; analysis still counts as done."""
    case_dir = Path(case_dir)
    for step in post_processing_steps(data):
        kind = str(step.get("type") or "").strip()
        if not kind:
            continue
        enabled = step.get("enabled", True)
        if isinstance(enabled, str):
            enabled = enabled.strip().lower() in ("true", "1", "yes")
        if not enabled:
            logger.info("post-process %s disabled", kind)
            continue
        runner = REGISTRY.get(kind)
        if runner is None:
            logger.warning("unknown post-process type %s", kind)
            continue
        try:
            runner(step, case_dir, machine_cfg)
        except Exception as exc:
            logger.exception("post-process %s failed for %s", kind, case_dir)
            send_error_email(
                str(exc) or f"{kind} failed",
                context=f"postprocess.{kind}",
                blocking=True,
            )
            if kind == DOCUFORMS2_CTQA_TYPE:
                from .docuforms_ctqa import notify_docuforms_event

                notify_docuforms_event(
                    step,
                    "failed",
                    case_dir,
                    machine_cfg,
                    {"error": str(exc) or f"{kind} failed"},
                )
