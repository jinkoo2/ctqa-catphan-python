"""HTML report from the machine report template tokens."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from .app_settings import mask_count
from .param import Param

logger = logging.getLogger(__name__)


def _fmt(value: float, num_format: str) -> str:
    if num_format == "0.00":
        return f"{value:.2f}"
    if num_format == "0.0":
        return f"{value:.1f}"
    return str(value)


def _operator(info: Param) -> tuple[str, str, str]:
    name = info.get_value("PatientName")
    if "^" in name:
        user = name.split("^", 1)[0]
    elif "," in name:
        user = name.split(",", 1)[0]
    else:
        user = name or "NA"
    date = info.get_value("StudyDate") or info.get_value("SeriesDate")
    time = info.get_value("StudyTime") or info.get_value("SeriesTime")
    if not date:
        now = datetime.now()
        date = now.strftime("%Y%m%d")
        time = now.strftime("%H%M%S")
    return user, date, time


def gen_html_rows(
    case_result_dir: Path,
    baseline_dir: Path,
    num_of_masks: int,
    tol: float,
    filename: str,
    num_format: str = "0.0",
) -> str:
    id2label = Param(baseline_dir / "id2label.txt") if (baseline_dir / "id2label.txt").is_file() else Param()
    file0 = baseline_dir / filename
    file1 = case_result_dir / filename
    labels0 = file0.read_text(encoding="utf-8").splitlines()[0].split(",")
    values0 = file0.read_text(encoding="utf-8").splitlines()[1].split(",")
    values1 = file1.read_text(encoding="utf-8").splitlines()[1].split(",")
    rows: list[str] = []
    for i in range(min(num_of_masks, len(labels0), len(values0), len(values1))):
        mask_id = labels0[i].strip()
        label = id2label.get_value(mask_id).strip() or mask_id
        v0 = float(values0[i])
        v1 = float(values1[i])
        diff = v1 - v0
        passed = abs(diff) < tol
        cell = (
            '<td class="pass">Pass<span class="glyphicon glyphicon-ok" aria-hidden="true"></span></td>'
            if passed
            else '<td class="fail">Fail<span class="glyphicon glyphicon-remove" aria-hidden="true"></span></td>'
        )
        rows.append(
            "<tr>\n"
            f"    <td>{label}</td>\n"
            f"    <td>{_fmt(v1, num_format)}</td>\n"
            f"    <td>{_fmt(v0, num_format)}</td>\n"
            f"    <td>{_fmt(diff, num_format)}</td>\n"
            f"    {cell}\n"
            "</tr>"
        )
    return "\n".join(rows)


def _tol(machine: dict, key: str) -> float:
    return float(machine.get(key) or 0)


def write_report(
    case_dir: Path,
    baseline_dir: Path,
    result_dir: Path,
    machine: dict,
) -> Path:
    info_path = case_dir / "info.txt"
    info = Param(info_path) if info_path.is_file() else Param()
    user, study_date, study_time = _operator(info)
    template = Path(str(machine.get("html_report_template") or ""))
    if not template.is_file():
        raise FileNotFoundError(f"report template not found: {template}")
    html = template.read_text(encoding="utf-8")
    html = (
        html.replace("{{{date}}}", study_date)
        .replace("{{{time}}}", study_time)
        .replace("{{{user}}}", user)
        .replace("{{{HU_tol}}}", str(machine.get("HU_tol") or ""))
        .replace("{{{geo_tol}}}", str(machine.get("geo_tol") or ""))
        .replace("{{{DT_tol}}}", str(machine.get("DT_tol") or ""))
        .replace("{{{UF_tol}}}", str(machine.get("UF_tol") or ""))
        .replace("{{{LC_tol}}}", str(machine.get("LC_tol") or ""))
        .replace("{{{UF.uniformity_tol}}}", str(machine.get("UF.uniformity_tol") or ""))
        .replace("{{{HC_RMTF_tol}}}", str(machine.get("HC_RMTF_tol") or ""))
        .replace("{{{HC_RMTF50_tol}}}", str(machine.get("HC_RMTF50_tol") or ""))
    )
    html = html.replace(
        "{{{HU}}}",
        gen_html_rows(result_dir, baseline_dir, mask_count(machine, "HU"), _tol(machine, "HU_tol"), "HU.csv"),
    )
    html = html.replace(
        "{{{DT}}}",
        gen_html_rows(result_dir, baseline_dir, mask_count(machine, "DT"), _tol(machine, "DT_tol"), "DT.dist.csv"),
    )
    html = html.replace(
        "{{{geo}}}",
        gen_html_rows(result_dir, baseline_dir, mask_count(machine, "geo"), _tol(machine, "geo_tol"), "geo.dist.csv"),
    )
    html = html.replace(
        "{{{UF}}}",
        gen_html_rows(result_dir, baseline_dir, mask_count(machine, "UF"), _tol(machine, "UF_tol"), "UF.csv"),
    )
    html = html.replace(
        "{{{UF.uniformity}}}",
        gen_html_rows(
            result_dir,
            baseline_dir,
            1,
            _tol(machine, "UF.uniformity_tol"),
            "UF.uniformity.csv",
            "0.00",
        ),
    )
    html = html.replace(
        "{{{LC}}}",
        gen_html_rows(result_dir, baseline_dir, mask_count(machine, "LC"), _tol(machine, "LC_tol"), "LC.csv"),
    )
    html = html.replace(
        "{{{HC.RMTF}}}",
        gen_html_rows(
            result_dir,
            baseline_dir,
            mask_count(machine, "HC"),
            _tol(machine, "HC_RMTF_tol"),
            "HC.RMTF.csv",
            "0.00",
        ),
    )
    html = html.replace(
        "{{{HC.RMTF.50}}}",
        gen_html_rows(
            result_dir,
            baseline_dir,
            1,
            _tol(machine, "HC_RMTF50_tol"),
            "HC.RMTF.calc.csv",
            "0.0",
        ),
    )
    replace_words = str(machine.get("replace_words_for_report") or "").strip()
    if replace_words:
        for word in replace_words.split(","):
            word = word.strip()
            if not word:
                continue
            html = html.replace(word, str(machine.get(word) or ""))
    out = result_dir / "report.html"
    out.write_text(html, encoding="utf-8")
    logger.info("report written: %s", out)
    return out
