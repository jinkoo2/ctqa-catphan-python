from pathlib import Path
import json

from ctqa_catphan.app_settings import (
    form_id_for_machine,
    format_form_ids,
    parse_form_ids_text,
)
from ctqa_catphan.docuforms_ctqa import (
    MARKER_NAME,
    calculate_overall_result,
    field_id_for_row,
    performed_at_from_case,
    upload_case,
    values_from_case,
)
from ctqa_catphan.postprocess import run_post_processing

MACHINE = {
    "NAME": "CTSim1",
    "num_of_HU_masks": 9,
    "num_of_UF_masks": 5,
    "num_of_HC_masks": 15,
    "num_of_LC_masks": 1,
    "num_of_geo_masks": 4,
    "num_of_DT_masks": 2,
    "HU_tol": 40.0,
    "geo_tol": 1.0,
    "DT_tol": 1.5,
    "UF_tol": 20,
    "LC_tol": 10.0,
    "UF.uniformity_tol": 0.2,
    "HC_RMTF_tol": 0.5,
    "HC_RMTF50_tol": 2.0,
    "replace_words_for_report": "geo1->geo2,geo2->geo3,geo3->geo4,geo4->geo1,DT1->DT2,DT2->DT1",
    "geo1->geo2": "pt1->pt2",
    "geo2->geo3": "pt2->pt3",
    "geo3->geo4": "pt3->pt4",
    "geo4->geo1": "pt4->pt1",
    "DT1->DT2": "pt5->pt6",
    "DT2->DT1": "pt6->pt5",
}

CASE_RESULT = {
    "HU": {"labels": [f"HU{i}" for i in range(1, 10)], "values": [-967.2] + [0.0] * 8},
    "geo.dist": {
        "labels": ["geo1->geo2", "geo2->geo3", "geo3->geo4", "geo4->geo1"],
        "values": [50.1, 50.0, 50.0, 50.0],
    },
    "DT.dist": {"labels": ["DT1->DT2", "DT2->DT1"], "values": [118.4, 118.4]},
    "UF": {"labels": ["UF1", "UF2", "UF3", "UF4", "UF5"], "values": [16.9, 14.9, 16.2, 17.9, 15.8]},
    "UF.uniformity": {"labels": ["Uniformity"], "values": [0.09]},
    "LC": {"labels": ["LC1"], "values": [12.8]},
    "HC.RMTF": {"labels": [f"HC{i}" for i in range(1, 16)], "values": [1.0] + [0.5] * 14},
    "HC.RMTF.calc": {"labels": ["RMTF=0.5"], "values": [3.2]},
}

BASELINE_RESULT = {
    "HU": {"labels": [f"HU{i}" for i in range(1, 10)], "values": [-967.0] + [0.0] * 8},
    "geo.dist": {
        "labels": ["geo1->geo2", "geo2->geo3", "geo3->geo4", "geo4->geo1"],
        "values": [50.0, 50.0, 50.0, 50.0],
    },
    "DT.dist": {"labels": ["DT1->DT2", "DT2->DT1"], "values": [118.2, 118.2]},
    "UF": {"labels": ["UF1", "UF2", "UF3", "UF4", "UF5"], "values": [13.7, 11.5, 13.4, 14.9, 13.4]},
    "UF.uniformity": {"labels": ["Uniformity"], "values": [0.13]},
    "LC": {"labels": ["LC1"], "values": [11.5]},
    "HC.RMTF": {"labels": [f"HC{i}" for i in range(1, 16)], "values": [1.0] + [0.5] * 14},
    "HC.RMTF.calc": {"labels": ["RMTF=0.5"], "values": [2.4]},
}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _make_case(tmp_path: Path) -> tuple[Path, dict]:
    case = tmp_path / "20260928_075003"
    analysis = case / "3.analysis"
    baseline = tmp_path / "baseline"
    _write_json(analysis / "analysis.result.json", CASE_RESULT)
    _write_json(baseline / "analysis.result.json", BASELINE_RESULT)
    (case / "info.txt").write_text(
        "PatientName=JK^Kim\nStudyDate=20260928\nStudyTime=075003\n",
        encoding="utf-8",
    )
    (case / "CT.1.dcm").write_bytes(b"DICM")
    (analysis / "report.html").write_text("<html>report</html>", encoding="utf-8")
    machine = dict(MACHINE)
    machine["baseline_dir"] = str(baseline)
    return case, machine


def test_form_id_for_machine():
    step = {
        "form_ids": [
            {"machine": "CTSim1", "form_id": "ctqa_catphan_604"},
            {"machine": "GECTSH", "form_id": "pfcc_gectsh_catphan604"},
        ]
    }
    assert form_id_for_machine(step, {"NAME": "GECTSH"}) == "pfcc_gectsh_catphan604"
    assert form_id_for_machine(step, {"NAME": "Other"}) == ""
    assert (
        form_id_for_machine(
            step, {"NAME": "CTSim1", "docuforms2_form_id": "ignored"}
        )
        == "ctqa_catphan_604"
    )
    assert (
        form_id_for_machine({}, {"NAME": "CTSim1", "docuforms2_form_id": "legacy"})
        == "legacy"
    )
    assert parse_form_ids_text("CTSim1  ctqa_catphan_604\nGECTSH=pfcc_gectsh_catphan604") == [
        {"machine": "CTSim1", "form_id": "ctqa_catphan_604"},
        {"machine": "GECTSH", "form_id": "pfcc_gectsh_catphan604"},
    ]
    assert format_form_ids(
        [{"machine": "CTSim1", "form_id": "ctqa_catphan_604"}]
    ) == "CTSim1 = ctqa_catphan_604"


def test_field_id_for_row():
    assert field_id_for_row("HU", "Air", 0) == "hu_air"
    assert field_id_for_row("HU", "HU1", 0) == "hu_air"
    assert field_id_for_row("geo.dist", "pt1->pt2", 0) == "geo_in_pt1_pt2"
    assert field_id_for_row("DT.dist", "pt5->pt6", 0) == "geo_out_pt5_pt6"
    assert field_id_for_row("UF", "UF1", 0) == "uniformity_hu_ctr"
    assert field_id_for_row("HC.RMTF", "HC3", 2) == "high_contrast_rmtf_3"
    assert field_id_for_row("HC.RMTF.calc", "RMTF=0.5", 0) == "high_contrast_rmtf50"


def test_performed_at_and_values(tmp_path):
    case, machine = _make_case(tmp_path)
    stamp = performed_at_from_case(case)
    assert stamp is not None
    assert stamp.isoformat() == "2026-09-28T07:50:03"
    values = values_from_case(case, machine, performed_by="phys")
    assert values["performed_by"] == "phys"
    assert values["performed_at"] == "2026-09-28T07:50:03"
    assert values["hu_air"].startswith("-967.")
    assert values["hu_air_baseline"].startswith("-967.")
    assert "hu_air_error" in values
    assert values["geo_in_pt1_pt2"] == "50.1"
    assert values["geo_out_pt5_pt6"].startswith("118.")
    assert values["uniformity_hu_ctr"].startswith("16.")
    assert "uniformity_integral" in values
    assert "low_contrast_std" in values
    assert values["high_contrast_rmtf_1"] == "1.0"
    assert values["high_contrast_rmtf50"] == "3.2"


def test_upload_case_dry_run_and_skip(tmp_path):
    case, machine = _make_case(tmp_path)
    step = {
        "backend_url": "https://example.invalid",
        "dry_run": True,
        "attach_dcm_zip": False,
        "form_ids": [{"machine": "CTSim1", "form_id": "ctqa_catphan_604"}],
    }
    other = dict(machine)
    other["NAME"] = "Other"
    assert upload_case(case, other, step) == "skipped"
    assert upload_case(case, machine, step) == "dry-run"
    (case / MARKER_NAME).write_text("{}", encoding="utf-8")
    step_live = {
        "backend_url": "https://example.invalid",
        "dry_run": False,
        "resubmit": False,
        "form_ids": [{"machine": "CTSim1", "form_id": "ctqa_catphan_604"}],
    }
    assert upload_case(case, machine, step_live) == "skipped"


def test_run_post_processing_disabled(tmp_path, monkeypatch):
    called = []

    def boom(*_args, **_kwargs):
        called.append(True)
        raise AssertionError("should not run")

    monkeypatch.setattr("ctqa_catphan.docuforms_ctqa.upload_case", boom)
    run_post_processing(
        tmp_path,
        {"NAME": "CTSim1"},
        data={
            "PostProcessing": [
                {
                    "type": "docuforms2_ctqa",
                    "enabled": False,
                    "backend_url": "https://example.invalid",
                }
            ]
        },
    )
    assert called == []


def test_upload_case_posts(tmp_path, monkeypatch):
    case, machine = _make_case(tmp_path)
    posted = []

    def fake_post(url, payload, *, timeout, verify):
        posted.append((url, payload))
        return {"ok": True, "_id": "sub1"}

    def fake_upload(*_a, **_k):
        return {"url": "/uploads/x.zip", "originalName": "input_dcm.zip"}

    monkeypatch.setattr("ctqa_catphan.docuforms_ctqa.post_json", fake_post)
    monkeypatch.setattr("ctqa_catphan.docuforms_ctqa.upload_file", fake_upload)
    status = upload_case(
        case,
        machine,
        {
            "backend_url": "https://docuforms.example.edu:9001",
            "form_ids": [{"machine": "CTSim1", "form_id": "ctqa_catphan_604"}],
            "attach_dcm_zip": True,
            "attach_pdf": False,
            "verify_ssl": False,
        },
    )
    assert status == "ok"
    assert (case / MARKER_NAME).is_file()
    assert posted
    url, payload = posted[0]
    assert url.endswith("/api/forms/ctqa_catphan_604/submit")
    assert payload["values"]["hu_air"].startswith("-967.")
    assert payload["attachments"][0]["originalName"] == "input_dcm.zip"
    meta = payload["metadata"]
    assert meta["hu_air_error"]["result"] == "PASS"
    assert calculate_overall_result(meta) in ("PASS", "FAIL", "WARNING")


def test_upload_case_attaches_full_report_pdf(tmp_path, monkeypatch):
    case, machine = _make_case(tmp_path)
    uploaded = []

    def fake_post(_url, payload, *, timeout, verify):
        uploaded.append(("submit", [a.get("originalName") for a in payload.get("attachments") or []]))
        return {"_id": "abc"}

    def fake_upload(_backend, path, name, **_k):
        uploaded.append(name)
        return {"url": f"/uploads/{name}", "originalName": name}

    def fake_html_to_pdf(html_path, dest):
        assert Path(html_path).name == "report.html"
        Path(dest).write_bytes(b"%PDF-1.4 dummy")
        return True

    monkeypatch.setattr("ctqa_catphan.docuforms_ctqa.post_json", fake_post)
    monkeypatch.setattr("ctqa_catphan.docuforms_ctqa.upload_file", fake_upload)
    monkeypatch.setattr("ctqa_catphan.docuforms_ctqa.html_to_pdf", fake_html_to_pdf)
    status = upload_case(
        case,
        machine,
        {
            "backend_url": "https://docuforms.example.edu:9001",
            "form_ids": [{"machine": "CTSim1", "form_id": "ctqa_catphan_604"}],
            "attach_dcm_zip": False,
            "attach_pdf": True,
        },
    )
    assert status == "ok"
    assert "report.pdf" in uploaded
    assert (case / "3.analysis" / "report.pdf").is_file()
