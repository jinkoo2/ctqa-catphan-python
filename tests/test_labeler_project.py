from pathlib import Path

from ctqa_catphan.labeler_project import (
    PROJECT_JSON_NAME,
    csv_paths,
    read_csv_table,
    write_baseline_project,
    write_case_project,
)


def _touch_image(folder: Path, stem: str) -> Path:
    path = folder / f"{stem}.mha"
    path.write_bytes(b"not-a-real-image")
    return path


def test_write_baseline_project(tmp_path):
    baseline = tmp_path / "baseline"
    baseline.mkdir()
    _touch_image(baseline, "CT")
    _touch_image(baseline, "fuz_mask")
    _touch_image(baseline, "HU1")
    _touch_image(baseline, "HU2")
    machine = {"num_of_HU_masks": 2}
    dest = write_baseline_project(baseline, machine)
    assert dest.name == PROJECT_JSON_NAME
    text = dest.read_text(encoding="utf-8")
    assert '"image": "CT.mha"' in text
    assert '"file": "HU1.mha"' in text
    assert '"name": "fuz_mask"' in text


def test_write_case_project_uses_seg_subdir(tmp_path):
    case = tmp_path / "20260921_083102"
    seg = case / "2.seg"
    seg.mkdir(parents=True)
    _touch_image(case, "CT")
    _touch_image(seg, "HU1")
    dest = write_case_project(case, {"num_of_HU_masks": 1})
    text = dest.read_text(encoding="utf-8")
    assert '"image": "CT.mha"' in text
    assert '"file": "2.seg/HU1.mha"' in text
    dest = write_case_project(case, {"num_of_HU_masks": 1}, include_labels=False)
    text = dest.read_text(encoding="utf-8")
    assert '"image": "CT.mha"' in text
    assert "HU1" not in text


def test_write_case_project_uses_packed_labels(tmp_path):
    case = tmp_path / "20260921_083102"
    seg = case / "2.seg"
    seg.mkdir(parents=True)
    _touch_image(case, "CT")
    _touch_image(seg, "masks_packed")
    (seg / "masks_packed.json").write_text('{"labels": {"1": "HU1"}}\n', encoding="utf-8")
    dest = write_case_project(case, {"num_of_HU_masks": 1})
    text = dest.read_text(encoding="utf-8")
    assert '"file": "2.seg/masks_packed.mha"' in text
    assert '"label": 1' in text
    assert '"packed_labels"' in text
    assert "HU1.mha" not in text


def test_csv_paths_skips_copy(tmp_path):
    (tmp_path / "HU.csv").write_text("HU1\n1\n", encoding="utf-8")
    (tmp_path / "HU - Copy.csv").write_text("x\n", encoding="utf-8")
    (tmp_path / "geo.dist.csv").write_text("a\n1\n", encoding="utf-8")
    names = [p.name for p in csv_paths(tmp_path)]
    assert names == ["HU.csv", "geo.dist.csv"]
    rows = read_csv_table(tmp_path / "HU.csv")
    assert rows[0] == ["HU1"]
    assert rows[1] == ["1"]


def test_labeler_command_passes_project_flag(tmp_path, monkeypatch):
    from ctqa_catphan.labeler_project import labeler_command

    project = tmp_path / "vtk_image_labeler_3d.project.json"
    project.write_text("{}\n", encoding="utf-8")
    exe = tmp_path / "ImageLabeler3D.exe"
    exe.write_bytes(b"")
    cmd = labeler_command(project, {"Viewer": {"vtk_image_labeler_3d": str(exe)}})
    assert cmd[0] == str(exe)
    assert cmd[1] == "--project"
    assert cmd[2] == str(project.resolve())
