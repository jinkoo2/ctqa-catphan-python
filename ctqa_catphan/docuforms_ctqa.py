"""Upload one CatPhan case to DocuForms2 (upload_ctqa post-processing)."""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
import shutil
import ssl
import subprocess
import tempfile
import urllib.error
import urllib.request
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

from .app_settings import form_id_for_machine, mask_count
from .identity import session_operator
from .report import analysis_is_done, apply_report_label, read_case_header

logger = logging.getLogger(__name__)

MARKER_NAME = ".docuforms2_ctqa.json"

HU_ORDER = (
    "Air",
    "PMP",
    "Bone50",
    "LDPE",
    "Polystyrene",
    "Acrylic",
    "Bone20",
    "Delrin",
    "Teflon",
)
HU_MATERIALS = {name: f"hu_{name.lower()}" for name in HU_ORDER}
HU_MATERIALS.update({f"HU{i}": HU_MATERIALS[name] for i, name in enumerate(HU_ORDER, start=1)})

GEO_IN_MEASUREMENTS = {
    "pt1->pt2": "geo_in_pt1_pt2",
    "pt2->pt3": "geo_in_pt2_pt3",
    "pt3->pt4": "geo_in_pt3_pt4",
    "pt4->pt1": "geo_in_pt4_pt1",
    "geo1->geo2": "geo_in_pt1_pt2",
    "geo2->geo3": "geo_in_pt2_pt3",
    "geo3->geo4": "geo_in_pt3_pt4",
    "geo4->geo1": "geo_in_pt4_pt1",
}
GEO_OUT_MEASUREMENTS = {
    "pt5->pt6": "geo_out_pt5_pt6",
    "pt6->pt5": "geo_out_pt6_pt5",
    "DT1->DT2": "geo_out_pt5_pt6",
    "DT2->DT1": "geo_out_pt6_pt5",
}
UNIFORMITY_POSITIONS = {
    "CTR": "uniformity_hu_ctr",
    "ANT": "uniformity_hu_ant",
    "RT": "uniformity_hu_rt",
    "PST": "uniformity_hu_pst",
    "LT": "uniformity_hu_lt",
    "UF1": "uniformity_hu_ctr",
    "UF2": "uniformity_hu_ant",
    "UF3": "uniformity_hu_rt",
    "UF4": "uniformity_hu_pst",
    "UF5": "uniformity_hu_lt",
}

# Default DocuForms2 error ranges (upload_ctqa import.py).
ERROR_RANGES = {
    "hu_air_error": {"pass": "-40:40", "warning": "-60:60"},
    "hu_pmp_error": {"pass": "-40:40", "warning": "-60:60"},
    "hu_bone50_error": {"pass": "-40:40", "warning": "-60:60"},
    "hu_ldpe_error": {"pass": "-40:40", "warning": "-60:60"},
    "hu_polystyrene_error": {"pass": "-40:40", "warning": "-60:60"},
    "hu_acrylic_error": {"pass": "-40:40", "warning": "-60:60"},
    "hu_bone20_error": {"pass": "-40:40", "warning": "-60:60"},
    "hu_delrin_error": {"pass": "-40:40", "warning": "-60:60"},
    "hu_teflon_error": {"pass": "-40:40", "warning": "-60:60"},
    "geo_in_pt1_pt2_error": {"pass": "-1:1", "warning": "-1.5:1.5"},
    "geo_in_pt2_pt3_error": {"pass": "-1:1", "warning": "-1.5:1.5"},
    "geo_in_pt3_pt4_error": {"pass": "-1:1", "warning": "-1.5:1.5"},
    "geo_in_pt4_pt1_error": {"pass": "-1:1", "warning": "-1.5:1.5"},
    "geo_out_pt5_pt6_error": {"pass": "-1.5:1.5", "warning": "-2:2"},
    "geo_out_pt6_pt5_error": {"pass": "-1.5:1.5", "warning": "-2:2"},
    "uniformity_hu_ctr_error": {"pass": "-20:20", "warning": "-30:30"},
    "uniformity_hu_ant_error": {"pass": "-20:20", "warning": "-30:30"},
    "uniformity_hu_rt_error": {"pass": "-20:20", "warning": "-30:30"},
    "uniformity_hu_pst_error": {"pass": "-20:20", "warning": "-30:30"},
    "uniformity_hu_lt_error": {"pass": "-20:20", "warning": "-30:30"},
    "uniformity_integral_error": {"pass": "-0.2:0.2"},
    "low_contrast_std_error": {"pass": "-10:10"},
    "high_contrast_rmtf_1_error": {"pass": "-0.5:0.5"},
    "high_contrast_rmtf_2_error": {"pass": "-0.5:0.5"},
    "high_contrast_rmtf_3_error": {"pass": "-0.5:0.5"},
    "high_contrast_rmtf_4_error": {"pass": "-0.5:0.5"},
    "high_contrast_rmtf_5_error": {"pass": "-0.5:0.5"},
    "high_contrast_rmtf_6_error": {"pass": "-0.5:0.5"},
    "high_contrast_rmtf_7_error": {"pass": "-0.5:0.5"},
    "high_contrast_rmtf_8_error": {"pass": "-0.5:0.5"},
    "high_contrast_rmtf_9_error": {"pass": "-0.5:0.5"},
    "high_contrast_rmtf_10_error": {"pass": "-0.5:0.5"},
    "high_contrast_rmtf_11_error": {"pass": "-0.5:0.5"},
    "high_contrast_rmtf_12_error": {"pass": "-0.5:0.5"},
    "high_contrast_rmtf_13_error": {"pass": "-0.5:0.5"},
    "high_contrast_rmtf_14_error": {"pass": "-0.5:0.5"},
    "high_contrast_rmtf_15_error": {"pass": "-0.5:0.5"},
    "high_contrast_rmtf50_error": {"pass": "-2.0:2.0"},
}

_FIELD_GROUPS = (
    (tuple(f"hu_{name.lower()}_error" for name in HU_ORDER), "HU_tol", 1.5),
    (
        ("geo_in_pt1_pt2_error", "geo_in_pt2_pt3_error", "geo_in_pt3_pt4_error", "geo_in_pt4_pt1_error"),
        "geo_tol",
        1.5,
    ),
    (("geo_out_pt5_pt6_error", "geo_out_pt6_pt5_error"), "DT_tol", 4.0 / 3.0),
    (
        (
            "uniformity_hu_ctr_error",
            "uniformity_hu_ant_error",
            "uniformity_hu_rt_error",
            "uniformity_hu_pst_error",
            "uniformity_hu_lt_error",
        ),
        "UF_tol",
        1.5,
    ),
    (("uniformity_integral_error",), "UF.uniformity_tol", None),
    (("low_contrast_std_error",), "LC_tol", None),
    (tuple(f"high_contrast_rmtf_{i}_error" for i in range(1, 16)), "HC_RMTF_tol", None),
    (("high_contrast_rmtf50_error",), "HC_RMTF50_tol", None),
)


def _tol(machine: dict | None, key: str) -> float | None:
    if not machine or machine.get(key) in (None, ""):
        return None
    try:
        return abs(float(machine[key]))
    except (TypeError, ValueError):
        return None


def error_ranges_for_machine(machine: dict | None) -> dict:
    ranges = {key: dict(val) for key, val in ERROR_RANGES.items()}
    for field_ids, tol_key, warn_scale in _FIELD_GROUPS:
        tol = _tol(machine, tol_key)
        if tol is None:
            continue
        pass_range = f"{-tol}:{tol}"
        warn_range = f"{-tol * warn_scale}:{tol * warn_scale}" if warn_scale else ""
        for field_id in field_ids:
            block = ranges.setdefault(field_id, {})
            block["pass"] = pass_range
            if warn_range:
                block["warning"] = warn_range
    return ranges


def parse_float(value) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def performed_at_from_case(case_dir: Path) -> datetime | None:
    """Study date/time from info.txt, else the case folder name."""
    case_dir = Path(case_dir)
    _user, date, time = read_case_header(case_dir)
    digits_d = re.sub(r"\D", "", date or "")
    digits_t = re.sub(r"\D", "", time or "")[:6].ljust(6, "0") if time else "000000"
    if len(digits_d) == 8:
        try:
            return datetime.strptime(digits_d + digits_t, "%Y%m%d%H%M%S")
        except ValueError:
            pass
    name = case_dir.name
    for fmt in ("%Y%m%d_%H%M%S", "%Y%m%d_DailyQA", "%m%d%Y_DailyQA"):
        try:
            return datetime.strptime(name, fmt)
        except ValueError:
            continue
    return None


def _lp_cm(label: str, index: int) -> int | None:
    text = str(label or "").strip()
    match = re.search(r"(\d+)", text)
    if match:
        n = int(match.group(1))
        if 1 <= n <= 15:
            return n
    n = index + 1
    return n if 1 <= n <= 15 else None


def field_id_for_row(section_key: str, label: str, index: int) -> str | None:
    name = str(label or "").strip()
    if section_key == "HU":
        if name in HU_MATERIALS:
            return HU_MATERIALS[name]
        if 0 <= index < len(HU_ORDER):
            return HU_MATERIALS[HU_ORDER[index]]
        return None
    if section_key == "geo.dist":
        return GEO_IN_MEASUREMENTS.get(name)
    if section_key == "DT.dist":
        return GEO_OUT_MEASUREMENTS.get(name)
    if section_key == "UF":
        return UNIFORMITY_POSITIONS.get(name) or UNIFORMITY_POSITIONS.get(name.upper())
    if section_key == "UF.uniformity":
        return "uniformity_integral"
    if section_key == "LC":
        return "low_contrast_std"
    if section_key == "HC.RMTF":
        n = _lp_cm(name, index)
        return f"high_contrast_rmtf_{n}" if n else None
    if section_key == "HC.RMTF.calc":
        return "high_contrast_rmtf50"
    return None


def _set_triplet(values: dict, field_id: str, value, baseline, error) -> None:
    v = parse_float(value)
    b = parse_float(baseline)
    e = parse_float(error)
    if v is not None:
        values[field_id] = str(v)
    if b is not None:
        values[f"{field_id}_baseline"] = str(b)
    if e is None and v is not None and b is not None:
        e = v - b
    if e is not None:
        values[f"{field_id}_error"] = f"{e:.2f}"


def values_from_case(
    case_dir: str | Path,
    machine: dict | None,
    *,
    performed_by: str = "",
) -> dict:
    """Form values from analysis.result.json + baseline (same labels as the HTML report)."""
    from .analysis import load_analysis_result, table_labels_values
    from .param import Param
    from .report import REPORT_SECTIONS

    case = Path(case_dir)
    machine = machine or {}
    analysis_dir = case / "3.analysis" if (case / "3.analysis").is_dir() else case
    baseline_dir = Path(str(machine.get("baseline_dir") or ""))
    case_data = load_analysis_result(analysis_dir, write_json_from_csv=False)
    baseline_data = (
        load_analysis_result(baseline_dir, write_json_from_csv=False) if baseline_dir.is_dir() else {}
    )
    id2label = Param(baseline_dir / "id2label.txt") if (baseline_dir / "id2label.txt").is_file() else Param()
    values: dict = {}
    stamp = performed_at_from_case(case)
    if stamp:
        values["performed_at"] = stamp.isoformat()
    operator = str(performed_by or "").strip()
    if not operator:
        user, _date, _time = read_case_header(case)
        operator = session_operator(user if user and user != "NA" else "")
    if operator:
        values["performed_by"] = operator
    for spec in REPORT_SECTIONS:
        labels, case_vals = table_labels_values(case_data, spec["key"])
        base_labels, base_vals = table_labels_values(baseline_data, spec["key"])
        n = min(len(base_labels), len(base_vals), len(case_vals) or len(base_vals))
        if spec["mask_key"]:
            limit = mask_count(machine, spec["mask_key"])
            if limit:
                n = min(n, limit)
        elif n:
            n = min(n, 1)
        for i in range(n):
            raw = (base_labels[i] if i < len(base_labels) else "") or (
                labels[i] if i < len(labels) else ""
            )
            label = apply_report_label(raw, machine, id2label)
            field_id = field_id_for_row(spec["key"], label, i) or field_id_for_row(
                spec["key"], raw, i
            )
            if not field_id:
                continue
            value = case_vals[i] if i < len(case_vals) else None
            baseline = base_vals[i] if i < len(base_vals) else None
            error = None
            if value is not None and baseline is not None:
                error = float(value) - float(baseline)
            _set_triplet(values, field_id, value, baseline, error)
    return values


def calculate_result(error_value: float, error_field_id: str, ranges: dict) -> str:
    block = ranges.get(error_field_id) or {}
    pass_range = str(block.get("pass") or "").split(":")
    warning_range = str(block.get("warning") or "").split(":")
    if len(pass_range) == 2:
        try:
            if float(pass_range[0]) <= error_value <= float(pass_range[1]):
                return "PASS"
        except (TypeError, ValueError):
            pass
    if len(warning_range) == 2:
        try:
            if float(warning_range[0]) <= error_value <= float(warning_range[1]):
                return "WARNING"
        except (TypeError, ValueError):
            pass
    return "FAIL"


def build_metadata(values: dict, machine: dict | None = None) -> dict:
    ranges = error_ranges_for_machine(machine)
    metadata: dict = {}
    for field_id, field_value in values.items():
        if field_id.endswith("_baseline"):
            base_field = field_id[: -len("_baseline")]
            metadata[field_id] = {"script": f"autofill_baseline('{base_field}', '{field_id}')"}
        elif field_id.endswith("_error"):
            base_field = field_id[: -len("_error")]
            error_value = parse_float(field_value)
            result = calculate_result(error_value, field_id, ranges) if error_value is not None else ""
            error_metadata = {
                "script": (
                    "calc_physical_error({inputId: "
                    f"'{base_field}', baselineField: '{base_field}', "
                    f"baselineInputId: '{base_field}_baseline', outputId: '{field_id}', "
                    f"resultId: '{base_field}_result'}});"
                )
            }
            if field_id in ranges:
                error_metadata["passRange"] = ranges[field_id].get("pass", "")
                if ranges[field_id].get("warning"):
                    error_metadata["warningRange"] = ranges[field_id]["warning"]
            if result:
                error_metadata["result"] = result
            metadata[field_id] = error_metadata
        else:
            metadata[field_id] = {"result": ""}
    return metadata


def calculate_overall_result(metadata: dict) -> str:
    results = [
        str(meta.get("result") or "").upper()
        for meta in metadata.values()
        if isinstance(meta, dict) and meta.get("result")
    ]
    if not results:
        return ""
    if any(r == "FAIL" for r in results):
        return "FAIL"
    if any(r == "WARNING" for r in results):
        return "WARNING"
    if all(r == "PASS" for r in results):
        return "PASS"
    return ""


def _ssl_context(verify: bool):
    if verify:
        return ssl.create_default_context()
    return ssl._create_unverified_context()


def _urlopen(req: urllib.request.Request, timeout: float, verify: bool):
    return urllib.request.urlopen(req, timeout=timeout, context=_ssl_context(verify))


def post_json(url: str, payload: dict, *, timeout: float, verify: bool) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with _urlopen(req, timeout, verify) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"HTTP {exc.code} {exc.reason}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"request failed: {exc.reason}") from exc
    if not body.strip():
        return {}
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def upload_file(
    backend_url: str,
    file_path: Path,
    original_name: str,
    *,
    timeout: float,
    verify: bool,
) -> dict | None:
    raw = file_path.read_bytes()
    boundary = "----CTQACatPhan" + uuid.uuid4().hex
    ctype = mimetypes.guess_type(original_name)[0] or "application/octet-stream"
    header = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{original_name}"\r\n'
        f"Content-Type: {ctype}\r\n\r\n"
    ).encode("utf-8")
    footer = f"\r\n--{boundary}--\r\n".encode("utf-8")
    req = urllib.request.Request(
        f"{backend_url.rstrip('/')}/api/upload",
        data=header + raw + footer,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with _urlopen(req, timeout, verify) as resp:
            parsed = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as exc:
        logger.warning("DocuForms2 upload %s failed: %s", original_name, exc)
        return None
    url = str((parsed or {}).get("url") or "")
    if not url:
        return None
    return {"url": url, "originalName": original_name}


def zip_dicoms(case_dir: Path, dest: Path) -> bool:
    files = sorted(p for p in case_dir.rglob("*.dcm") if p.is_file())
    if not files:
        return False
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            try:
                arc = path.relative_to(case_dir).as_posix()
            except ValueError:
                arc = path.name
            zf.write(path, arc)
    return True


def case_report_html(case_dir: Path) -> Path | None:
    for path in (Path(case_dir) / "3.analysis" / "report.html", Path(case_dir) / "report.html"):
        if path.is_file():
            return path
    return None


def html_to_pdf(html_path: Path, dest: Path) -> bool:
    """Convert report.html to PDF (WeasyPrint, else Chrome/Edge)."""
    html_path = Path(html_path)
    dest = Path(dest)
    if not html_path.is_file():
        return False
    try:
        text = html_path.read_text(encoding="utf-8")
    except OSError:
        logger.warning("could not read %s for PDF", html_path)
        return False
    text = text.replace(".\\", "./").replace("\\", "/")
    tmp_html = html_path.parent / f".{html_path.stem}.pdfsrc.html"
    local_dir = None
    try:
        tmp_html.write_text(text, encoding="utf-8")
        if _try_write_pdf(tmp_html, dest):
            return True
        local_html, local_dir = _local_pdf_workspace(html_path, text)
        if local_html is not None and _try_write_pdf(local_html, dest):
            return True
    except Exception:
        logger.warning("report.pdf conversion failed", exc_info=True)
    finally:
        try:
            tmp_html.unlink(missing_ok=True)
        except OSError:
            pass
        if local_dir is not None:
            shutil.rmtree(local_dir, ignore_errors=True)
    logger.warning("report.pdf conversion failed for %s", html_path)
    return False


def _try_write_pdf(html_path: Path, dest: Path) -> bool:
    if not (_pdf_weasyprint(html_path, dest) or _pdf_chromium(html_path, dest)):
        return False
    size = dest.stat().st_size if dest.is_file() else 0
    if size <= 0:
        return False
    logger.info("created report.pdf (%s KB)", f"{size / 1024:.1f}")
    return True


def _local_pdf_workspace(html_path: Path, rewritten_html: str) -> tuple[Path | None, Path | None]:
    try:
        work = Path(tempfile.mkdtemp(prefix="ctqa_pdf_"))
        dest_html = work / html_path.name
        dest_html.write_text(rewritten_html, encoding="utf-8")
        for src in html_path.parent.iterdir():
            if not src.is_file() or src.suffix.lower() in (".html", ".htm"):
                continue
            shutil.copy2(src, work / src.name)
        return dest_html, work
    except OSError:
        logger.warning("could not copy report assets for PDF", exc_info=True)
        return None, None


def _pdf_weasyprint(html_path: Path, dest: Path) -> bool:
    try:
        from weasyprint import HTML as WeasyHTML
    except ImportError:
        return False
    try:
        WeasyHTML(string=html_path.read_text(encoding="utf-8"), base_url=str(html_path.parent)).write_pdf(
            str(dest)
        )
    except Exception:
        logger.warning("weasyprint PDF failed", exc_info=True)
        return False
    return dest.is_file() and dest.stat().st_size > 0


def _chromium_exe() -> str | None:
    roots = (
        os.environ.get("PROGRAMFILES", r"C:\Program Files"),
        os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
        os.environ.get("LOCALAPPDATA", ""),
    )
    rels = (
        Path("Microsoft/Edge/Application/msedge.exe"),
        Path("Google/Chrome/Application/chrome.exe"),
    )
    for root in roots:
        if not root:
            continue
        for rel in rels:
            path = Path(root) / rel
            if path.is_file():
                return str(path)
    return shutil.which("msedge") or shutil.which("chrome") or shutil.which("chromium")


def _pdf_chromium(html_path: Path, dest: Path) -> bool:
    exe = _chromium_exe()
    if not exe:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file():
        try:
            dest.unlink()
        except OSError:
            return False
    cmd = [
        exe,
        "--headless",
        "--disable-gpu",
        "--no-pdf-header-footer",
        f"--print-to-pdf={dest}",
        html_path.resolve().as_uri(),
    ]
    try:
        proc = subprocess.run(cmd, check=False, timeout=180, capture_output=True)
    except Exception:
        logger.warning("Chrome/Edge PDF conversion failed", exc_info=True)
        return False
    if proc.returncode != 0 or not dest.is_file() or dest.stat().st_size <= 0:
        err = (proc.stderr or b"").decode("utf-8", errors="replace")[:400]
        logger.warning("Chrome/Edge PDF conversion failed: %s", err or proc.returncode)
        return False
    return True


def submit_form(
    backend_url: str,
    form_id: str,
    values: dict,
    metadata: dict,
    result: str,
    attachments: list[dict] | None,
    *,
    timeout: float,
    verify: bool,
) -> dict:
    payload = {
        "values": values,
        "metadata": metadata,
        "result": result,
        "comments": "",
        "submissionHtml": "",
        "attachments": attachments or None,
    }
    return post_json(
        f"{backend_url.rstrip('/')}/api/forms/{form_id}/submit",
        payload,
        timeout=timeout,
        verify=verify,
    )


def already_imported(case_dir: Path) -> bool:
    return (case_dir / MARKER_NAME).is_file()


def write_marker(case_dir: Path, payload: dict) -> None:
    path = case_dir / MARKER_NAME
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def notify_docuforms_event(
    step: dict | None,
    status: str,
    case_dir: Path,
    machine_cfg: dict | None,
    extra: dict | None = None,
) -> None:
    """Email success or failure lists for this DocuForms2 step."""
    from .emailer import send_list_email

    step = step or {}
    if status in ("ok", "dry-run"):
        to = step.get("email_success_event_to")
    elif status == "failed":
        to = step.get("email_failure_event_to")
    else:
        return
    machine = str((machine_cfg or {}).get("NAME") or "").strip()
    label = f"{machine}/{case_dir.name}" if machine else case_dir.name
    lines = [
        f"status={status}",
        f"machine={machine}",
        f"case={case_dir.name}",
        f"path={case_dir}",
    ]
    for key, value in (extra or {}).items():
        if value is None or str(value).strip() == "":
            continue
        lines.append(f"{key}={value}")
    send_list_email(
        to,
        f"DocuForms2 {status}: {label}",
        "\n".join(lines),
        context="postprocess.docuforms2_ctqa",
        subject=f"CTQA-CatPhan DocuForms2 {status}: {label}"[:180],
        blocking=True,
    )


def upload_case(
    case_dir: str | Path,
    machine_cfg: dict | None,
    step: dict,
) -> str:
    """Upload one case. Returns 'ok', 'skipped', or 'dry-run'. Raises on hard failure."""
    case_dir = Path(case_dir)
    step = step or {}
    form_id = form_id_for_machine(step, machine_cfg)
    backend = str(step.get("backend_url") or "").strip().rstrip("/")
    if not form_id:
        logger.info("DocuForms2 skipped: no form_id for this machine")
        notify_docuforms_event(step, "skipped", case_dir, machine_cfg, {"reason": "no form_id"})
        return "skipped"
    if not backend:
        logger.info("DocuForms2 skipped: empty backend_url")
        notify_docuforms_event(
            step, "skipped", case_dir, machine_cfg, {"reason": "empty backend_url"}
        )
        return "skipped"
    if not analysis_is_done(case_dir):
        logger.info("DocuForms2 skipped: no analysis result in %s", case_dir)
        notify_docuforms_event(
            step, "skipped", case_dir, machine_cfg, {"reason": "no analysis result"}
        )
        return "skipped"
    resubmit = bool(step.get("resubmit", False))
    if already_imported(case_dir) and not resubmit:
        logger.info("DocuForms2 skipped (already imported): %s", case_dir)
        notify_docuforms_event(
            step, "skipped", case_dir, machine_cfg, {"reason": "already imported"}
        )
        return "skipped"

    values = values_from_case(case_dir, machine_cfg)
    if not any(k for k in values if k not in ("performed_at", "performed_by")):
        logger.info("DocuForms2 skipped: no measurement fields in %s", case_dir)
        notify_docuforms_event(
            step, "skipped", case_dir, machine_cfg, {"reason": "no measurement fields"}
        )
        return "skipped"
    metadata = build_metadata(values, machine_cfg)
    result = calculate_overall_result(metadata)
    timeout = float(step.get("timeout_sec") or 300)
    verify = bool(step.get("verify_ssl", False))
    dry_run = bool(step.get("dry_run", False))
    attachments: list[dict] = []
    tmp_zip = None
    tmp_pdf = None
    try:
        if not dry_run and bool(step.get("attach_dcm_zip", True)):
            tmp_zip = Path(tempfile.gettempdir()) / f"ctqa_dcm_{case_dir.name}_{uuid.uuid4().hex}.zip"
            if zip_dicoms(case_dir, tmp_zip):
                info = upload_file(
                    backend, tmp_zip, "input_dcm.zip", timeout=timeout, verify=verify
                )
                if info:
                    attachments.append(info)
        if not dry_run and bool(step.get("attach_pdf", True)):
            report = case_report_html(case_dir)
            if report is None:
                raise RuntimeError(f"report.html missing in {case_dir}; cannot attach report.pdf")
            tmp_pdf = Path(tempfile.gettempdir()) / f"ctqa_pdf_{case_dir.name}_{uuid.uuid4().hex}.pdf"
            if not html_to_pdf(report, tmp_pdf):
                raise RuntimeError("could not convert report.html to report.pdf")
            case_pdf = report.parent / "report.pdf"
            try:
                shutil.copy2(tmp_pdf, case_pdf)
            except OSError:
                logger.warning("could not copy report.pdf into %s", report.parent)
            info = upload_file(backend, tmp_pdf, "report.pdf", timeout=timeout, verify=verify)
            if not info:
                raise RuntimeError("report.pdf upload failed")
            attachments.append(info)
        if dry_run:
            logger.info(
                "DocuForms2 dry-run %s form=%s fields=%s result=%s",
                case_dir.name,
                form_id,
                len(values),
                result,
            )
            notify_docuforms_event(
                step,
                "dry-run",
                case_dir,
                machine_cfg,
                {"form_id": form_id, "backend_url": backend, "result": result},
            )
            return "dry-run"
        response = submit_form(
            backend,
            form_id,
            values,
            metadata,
            result,
            attachments,
            timeout=timeout,
            verify=verify,
        )
        submission_id = str((response or {}).get("_id") or (response or {}).get("id") or "")
        write_marker(
            case_dir,
            {
                "form_id": form_id,
                "backend_url": backend,
                "result": result,
                "performed_at": values.get("performed_at") or "",
                "submission_id": submission_id,
                "submitted_at": datetime.now().isoformat(timespec="seconds"),
                "attachments": [
                    a.get("originalName") for a in attachments if a.get("originalName")
                ],
            },
        )
        logger.info(
            "DocuForms2 submitted %s to %s result=%s performed_at=%s id=%s",
            case_dir.name,
            form_id,
            result,
            values.get("performed_at") or "",
            submission_id,
        )
        notify_docuforms_event(
            step,
            "ok",
            case_dir,
            machine_cfg,
            {
                "form_id": form_id,
                "backend_url": backend,
                "result": result,
                "performed_at": values.get("performed_at") or "",
                "submission_id": submission_id,
                "attachments": ",".join(a.get("originalName") or "" for a in attachments),
            },
        )
        return "ok"
    finally:
        for path in (tmp_zip, tmp_pdf):
            if path is None:
                continue
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
