from pathlib import Path

import numpy as np
import SimpleITK as sitk

from ctqa_catphan.analysis import (
    CASE_RESULT_NAME,
    RESULT_JSON_NAME,
    analyze,
    convert_csvs_to_json,
    evaluate_against_baseline,
    integral_non_uniformity,
    load_analysis_result,
    read_case_result,
    relative_mtf,
    result_from_csv_folder,
    table_labels_values,
    write_analysis_result,
    write_case_result,
    write_csv,
)


def test_integral_non_uniformity():
    assert abs(integral_non_uniformity([10.0, 8.0, 12.0]) - (4.0 / 20.0)) < 1e-9
    assert integral_non_uniformity([]) == 0.0


def test_relative_mtf_50():
    values = [10.0, 8.0, 6.0, 4.0, 2.0]
    norm, x50 = relative_mtf(values)
    assert abs(norm[0] - 1.0) < 1e-9
    assert 2.0 < x50 < 3.0


def test_convert_csvs_to_json(tmp_path: Path):
    write_csv(tmp_path / "HU.csv", ["HU1", "HU2"], [1.5, 2.5])
    (tmp_path / "geo.csv").write_text(
        ",x[mm],y[mm],z[mm]\ngeo1,1,2,3\ngeo2,4,5,6\n",
        encoding="utf-8",
    )
    (tmp_path / "HU - Copy.csv").write_text("x\n1\n", encoding="utf-8")
    dest = convert_csvs_to_json(tmp_path)
    assert dest is not None
    assert dest.name == RESULT_JSON_NAME
    data = load_analysis_result(tmp_path)
    labels, values = table_labels_values(data, "HU.csv")
    assert labels == ["HU1", "HU2"]
    assert values == [1.5, 2.5]
    assert data["geo"]["points"][0]["id"] == "geo1"
    assert "HU - Copy" not in data
    assert result_from_csv_folder(tmp_path)["HU"]["values"] == [1.5, 2.5]


def test_analyze_writes_json_not_csv(tmp_path: Path):
    ct = sitk.GetImageFromArray(np.zeros((4, 8, 8), dtype=np.int16))
    ct.SetSpacing((1.0, 1.0, 1.0))
    mask = np.zeros((4, 8, 8), dtype=np.uint8)
    mask[:, 1:3, 1:3] = 1
    img = sitk.GetImageFromArray(mask)
    img.CopyInformation(ct)
    sitk.WriteImage(img, str(tmp_path / "HU1.mha"), True)
    out = tmp_path / "3.analysis"
    dest = analyze(ct, tmp_path, out, {"num_of_HU_masks": 1})
    assert dest.name == RESULT_JSON_NAME
    assert dest.is_file()
    assert not (out / "HU.csv").exists()
    data = load_analysis_result(out)
    assert data["HU"]["labels"] == ["HU1"]
    assert len(data["HU"]["values"]) == 1


def test_analyze_from_packed_labels(tmp_path: Path):
    ct = sitk.GetImageFromArray(np.zeros((4, 8, 8), dtype=np.int16))
    ct.SetSpacing((1.0, 1.0, 1.0))
    packed = np.zeros((4, 8, 8), dtype=np.uint16)
    packed[:, 1:3, 1:3] = 1
    img = sitk.GetImageFromArray(packed)
    img.CopyInformation(ct)
    sitk.WriteImage(img, str(tmp_path / "masks_packed.mha"), True)
    (tmp_path / "masks_packed.json").write_text('{"labels": {"1": "HU1"}}\n', encoding="utf-8")
    dest = analyze(ct, tmp_path, tmp_path / "3.analysis", {"num_of_HU_masks": 1})
    data = load_analysis_result(dest.parent)
    assert data["HU"]["labels"] == ["HU1"]
    assert len(data["HU"]["values"]) == 1


def test_html_rows_from_json(tmp_path: Path):
    from ctqa_catphan.report import gen_html_rows

    from ctqa_catphan.analysis import write_analysis_result

    baseline = tmp_path / "baseline"
    case = tmp_path / "case"
    baseline.mkdir()
    case.mkdir()
    write_analysis_result(baseline, {"HU": {"labels": ["HU1"], "values": [10.0]}})
    write_analysis_result(case, {"HU": {"labels": ["HU1"], "values": [12.0]}})
    html = gen_html_rows(case, baseline, 1, 5.0, "HU.csv")
    assert "12.0" in html
    assert "10.0" in html
    assert "Pass" in html


def test_write_report_phantom_tokens(tmp_path: Path):
    from ctqa_catphan.report import write_report

    case = tmp_path / "20240101"
    baseline = tmp_path / "baseline"
    result = tmp_path / "3.analysis"
    case.mkdir()
    baseline.mkdir()
    result.mkdir()
    (case / "info.txt").write_text("PatientName=Kim\nStudyDate=20240101\nStudyTime=080000\n", encoding="utf-8")
    template = tmp_path / "report.html"
    template.write_text("{{{machine}}} {{{phantom}}} {{{phantom_id}}}", encoding="utf-8")
    dest = write_report(
        case,
        baseline,
        result,
        {
            "NAME": "CTSim1",
            "html_report_template": str(template),
            "phantom": {"id": "catphan604", "name": "Catphan 604"},
        },
    )
    text = dest.read_text(encoding="utf-8")
    assert text == "CTSim1 Catphan 604 catphan604"


def test_evaluate_any_fail_is_case_fail():
    case = {"HU": {"labels": ["HU1", "HU2"], "values": [10.0, 20.0]}}
    baseline = {"HU": {"labels": ["HU1", "HU2"], "values": [10.0, 10.0]}}
    summary = evaluate_against_baseline(case, baseline, {"HU_tol": 5.0})
    assert summary["result"] == "fail"
    assert summary["n_fail"] == 1
    assert summary["n_pass"] == 1
    assert "HU.HU2" in summary["failed"]


def test_write_and_read_case_result(tmp_path: Path):
    case = tmp_path / "20240101"
    analysis = case / "3.analysis"
    baseline = tmp_path / "baseline"
    analysis.mkdir(parents=True)
    baseline.mkdir()
    write_analysis_result(baseline, {"HU": {"labels": ["HU1"], "values": [10.0]}})
    write_analysis_result(analysis, {"HU": {"labels": ["HU1"], "values": [12.0]}})
    dest = write_case_result(analysis, baseline, {"HU_tol": 5.0})
    assert dest is not None
    assert dest.name == CASE_RESULT_NAME
    assert read_case_result(case) == "pass"
    write_case_result(analysis, baseline, {"HU_tol": 1.0})
    assert read_case_result(case) == "fail"


def test_case_status_pass_fail(tmp_path: Path):
    from ctqa_catphan.gui import case_status

    case = tmp_path / "20240101"
    analysis = case / "3.analysis"
    analysis.mkdir(parents=True)
    (analysis / CASE_RESULT_NAME).write_text('{"result": "fail"}\n', encoding="utf-8")
    assert case_status(case) == "fail"
    (analysis / CASE_RESULT_NAME).write_text('{"result": "pass"}\n', encoding="utf-8")
    assert case_status(case) == "pass"
    (analysis / CASE_RESULT_NAME).unlink()
    (analysis / "report.html").write_text("<html></html>", encoding="utf-8")
    assert case_status(case) == "new"


def test_build_case_report_baseline_and_values(tmp_path: Path):
    from ctqa_catphan.report import build_case_report

    baseline = tmp_path / "baseline"
    case = tmp_path / "20260928_075003"
    analysis = case / "3.analysis"
    baseline.mkdir()
    analysis.mkdir(parents=True)
    (case / "info.txt").write_text("PatientName=Kim^Jinkoo\nStudyDate=20260928\nStudyTime=075003\n", encoding="utf-8")
    (baseline / "id2label.txt").write_text("HU1=Air\n", encoding="utf-8")
    write_analysis_result(baseline, {"HU": {"labels": ["HU1"], "values": [10.0]}})
    machine = {"baseline_dir": str(baseline), "HU_tol": 40.0, "num_of_HU_masks": 1}
    report = build_case_report(case, machine)
    assert report["operator"] == "Kim"
    assert report["datetime"] == "20260928 / 075003"
    assert report["analyzed"] is False
    hu = report["sections"][0]
    assert hu["title"] == "HU Consistancy"
    assert hu["rows"][0]["label"] == "Air"
    assert hu["rows"][0]["baseline"] == 10.0
    assert hu["rows"][0]["value"] is None
    assert "40" in hu["tolerance"]
    write_analysis_result(analysis, {"HU": {"labels": ["HU1"], "values": [12.0]}})
    report = build_case_report(case, machine)
    assert report["analyzed"] is True
    assert report["result"] == "pass"
    assert report["sections"][0]["rows"][0]["value"] == 12.0
    assert report["sections"][0]["rows"][0]["diff"] == 2.0
    assert report["sections"][0]["rows"][0]["result"] == "pass"
