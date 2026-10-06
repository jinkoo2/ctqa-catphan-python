"""HTML report from the machine report template tokens."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from .analysis import RESULT_JSON_NAME, load_analysis_result, table_labels_values
from .app_settings import mask_count, machine_name, phantom_id, phantom_name
from .param import Param

REPORT_SECTIONS = (
    {
        "key": "HU",
        "title": "HU Consistancy",
        "label_header": "",
        "value_header": "HU",
        "tol_key": "HU_tol",
        "tol_suffix": " HU",
        "mask_key": "HU",
        "num_format": "0.0",
    },
    {
        "key": "geo.dist",
        "title": "Geometric Accuracy (in-plane)",
        "label_header": "",
        "value_header": "Distance",
        "tol_key": "geo_tol",
        "tol_suffix": " mm",
        "mask_key": "geo",
        "num_format": "0.0",
    },
    {
        "key": "DT.dist",
        "title": "Geometric Accuracy (out-of-plane)",
        "label_header": "",
        "value_header": "Distance",
        "tol_key": "DT_tol",
        "tol_suffix": " mm",
        "mask_key": "DT",
        "num_format": "0.0",
    },
    {
        "key": "UF",
        "title": "Uniformity (HU)",
        "label_header": "",
        "value_header": "HU",
        "tol_key": "UF_tol",
        "tol_suffix": "",
        "mask_key": "UF",
        "num_format": "0.0",
    },
    {
        "key": "UF.uniformity",
        "title": "Uniformity (Integral)",
        "label_header": "",
        "value_header": "Integral Uniformity",
        "tol_key": "UF.uniformity_tol",
        "tol_suffix": ", Uniformity = (HUmax-HUmin)/(HUmax+HUmin)",
        "mask_key": "",
        "num_format": "0.00",
    },
    {
        "key": "LC",
        "title": "Low Contrast (STD)",
        "label_header": "",
        "value_header": "HU STD",
        "tol_key": "LC_tol",
        "tol_suffix": " HU",
        "mask_key": "LC",
        "num_format": "0.0",
    },
    {
        "key": "HC.RMTF",
        "title": "High Contrast (RMTF)",
        "label_header": "LP/CM",
        "value_header": "RMTF",
        "tol_key": "HC_RMTF_tol",
        "tol_suffix": "",
        "mask_key": "HC",
        "num_format": "0.00",
    },
    {
        "key": "HC.RMTF.calc",
        "title": "High Contrast (RMTF=50%)",
        "label_header": "",
        "value_header": "LP/CM",
        "tol_key": "HC_RMTF50_tol",
        "tol_suffix": " line-pair(LP) / cm",
        "mask_key": "",
        "num_format": "0.0",
    },
)

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
    labels0, values0 = table_labels_values(load_analysis_result(baseline_dir), filename)
    _labels1, values1 = table_labels_values(load_analysis_result(case_result_dir), filename)
    rows: list[str] = []
    n = min(num_of_masks, len(labels0), len(values0), len(values1))
    for i in range(n):
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
    try:
        return float(machine.get(key) or 0)
    except (TypeError, ValueError):
        return 0.0


def analysis_is_done(case_dir: str | Path) -> bool:
    folder = Path(case_dir)
    analysis = folder / "3.analysis" if (folder / "3.analysis").is_dir() else folder
    if (analysis / RESULT_JSON_NAME).is_file():
        return True
    return bool(load_analysis_result(analysis, write_json_from_csv=False))


def _date_time_from_name(name: str) -> tuple[str, str]:
    try:
        datetime.strptime(name, "%Y%m%d_%H%M%S")
        return name[:8], name[9:]
    except ValueError:
        return "", ""


def read_case_header(case_dir: str | Path) -> tuple[str, str, str]:
    folder = Path(case_dir)
    info_path = folder / "info.txt"
    info = Param(info_path) if info_path.is_file() else Param()
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
        date, time = _date_time_from_name(folder.name)
    return user, date, time


def apply_report_label(label: str, machine: dict, id2label: Param) -> str:
    text = id2label.get_value(label).strip() or label
    replace_words = str(machine.get("replace_words_for_report") or "").strip()
    if not replace_words:
        return text
    for word in replace_words.split(","):
        word = word.strip()
        if not word:
            continue
        text = text.replace(word, str(machine.get(word) or ""))
    return text


def build_case_report(case_dir: str | Path, machine: dict) -> dict:
    """Report-style sections for the case tab (baseline always; values if analyzed)."""
    case = Path(case_dir)
    baseline_dir = Path(str(machine.get("baseline_dir") or ""))
    analysis_dir = case / "3.analysis" if (case / "3.analysis").is_dir() else case
    analyzed = analysis_is_done(case)
    case_data = load_analysis_result(analysis_dir, write_json_from_csv=False) if analyzed else {}
    baseline_data = load_analysis_result(baseline_dir, write_json_from_csv=False) if baseline_dir.is_dir() else {}
    id2label = Param(baseline_dir / "id2label.txt") if (baseline_dir / "id2label.txt").is_file() else Param()
    user, date, time = read_case_header(case)
    from .analysis import read_case_result

    sections: list[dict] = []
    for spec in REPORT_SECTIONS:
        labels, case_vals = table_labels_values(case_data, spec["key"])
        base_labels, base_vals = table_labels_values(baseline_data, spec["key"])
        if not base_labels and not base_vals:
            continue
        tol = _tol(machine, spec["tol_key"])
        n = min(len(base_labels), len(base_vals))
        if spec["mask_key"]:
            limit = mask_count(machine, spec["mask_key"])
            if limit:
                n = min(n, limit)
        elif n:
            n = min(n, 1)
        rows: list[dict] = []
        for i in range(n):
            raw_label = (base_labels[i] if i < len(base_labels) else "") or (labels[i] if i < len(labels) else "")
            baseline = float(base_vals[i])
            value = None
            diff = None
            result = ""
            if analyzed and i < len(case_vals):
                value = float(case_vals[i])
                diff = value - baseline
                result = "pass" if abs(diff) < tol else "fail"
            rows.append(
                {
                    "label": apply_report_label(raw_label, machine, id2label),
                    "value": value,
                    "baseline": baseline,
                    "diff": diff,
                    "result": result,
                }
            )
        if not rows:
            continue
        sections.append(
            {
                "key": spec["key"],
                "title": spec["title"],
                "label_header": spec["label_header"],
                "value_header": spec["value_header"],
                "num_format": spec["num_format"],
                "tolerance": f"Tolerance = {machine.get(spec['tol_key']) if machine.get(spec['tol_key']) not in (None, '') else tol}{spec['tol_suffix']}",
                "tol": tol,
                "rows": rows,
            }
        )
    n_fail = sum(1 for sec in sections for row in sec["rows"] if row["result"] == "fail")
    n_pass = sum(1 for sec in sections for row in sec["rows"] if row["result"] == "pass")
    if analyzed and n_fail:
        overall = "fail"
    elif analyzed and n_pass:
        overall = "pass"
    else:
        overall = read_case_result(case) or "new"
    return {
        "operator": user,
        "date": date,
        "time": time,
        "datetime": f"{date} / {time}".strip(" /"),
        "analyzed": analyzed,
        "result": overall,
        "sections": sections,
    }


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
        .replace("{{{machine}}}", machine_name(machine))
        .replace("{{{phantom}}}", phantom_name(machine))
        .replace("{{{phantom_id}}}", phantom_id(machine))
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
