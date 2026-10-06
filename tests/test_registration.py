import math
import tempfile
from pathlib import Path

import SimpleITK as sitk
import pytest

from ctqa_catphan.registration import (
    build_elastix_args,
    build_transformix_args,
    case_has_registration,
    default_elastix_param_dir,
    elastix_param_files,
    find_elastix_exe,
    find_registered_result,
    is_unc_path,
    parameter_map_to_transform,
    read_elastix_param_file,
    registration_summary,
    resample_onto,
    resolve_elastix_dir,
    resolve_elastix_param_dir,
    rewrite_copied_elastix_paths,
    scratch_parent,
    transform_from_elastix_file,
    write_label_transform_param,
)


def test_bundled_elastix_params_exist():
    translation, rigid = elastix_param_files()
    assert translation.name == "Parameters_Translation.txt"
    assert rigid.name == "Parameters_Rigid.txt"
    trans = read_elastix_param_file(translation)
    rig = read_elastix_param_file(rigid)
    assert trans["Transform"] == ["TranslationTransform"]
    assert rig["Transform"] == ["EulerTransform"]
    assert trans["Metric"] == ["AdvancedMattesMutualInformation"]
    assert default_elastix_param_dir().is_dir()


def test_resolve_elastix_param_dir_override(tmp_path: Path):
    (tmp_path / "Parameters_Translation.txt").write_text('(Transform "TranslationTransform")\n', encoding="utf-8")
    (tmp_path / "Parameters_Rigid.txt").write_text('(Transform "EulerTransform")\n', encoding="utf-8")
    assert resolve_elastix_param_dir({"elastix_param_dir": str(tmp_path)}) == tmp_path
    with pytest.raises(FileNotFoundError):
        resolve_elastix_param_dir({"elastix_param_dir": str(tmp_path / "missing")})


def test_resolve_elastix_dir_from_settings():
    assert resolve_elastix_dir({"Elastix": {"elastix_dir": r"C:\elastix"}}) == Path(r"C:\elastix")
    assert resolve_elastix_dir({}) is None


def test_find_elastix_exe_in_folder(tmp_path: Path):
    fake = tmp_path / "elastix.exe"
    fake.write_bytes(b"")
    (tmp_path / "transformix.exe").write_bytes(b"")
    assert find_elastix_exe(tmp_path) == fake
    with pytest.raises(FileNotFoundError, match="not found"):
        find_elastix_exe(tmp_path / "missing")


def test_build_elastix_args_matches_csharp():
    args = build_elastix_args(
        r"C:\case\CT.mha",
        r"C:\base\CT.nrrd",
        r"C:\case\1.reg",
        [Path(r"C:\p\Parameters_Translation.txt"), Path(r"C:\p\Parameters_Rigid.txt")],
        moving_mask=r"C:\base\fuz_mask.nrrd",
    )
    assert args[:6] == ["-f", r"C:\case\CT.mha", "-m", r"C:\base\CT.nrrd", "-out", r"C:\case\1.reg"]
    assert "-mMask" in args
    assert args[args.index("-mMask") + 1] == r"C:\base\fuz_mask.nrrd"
    assert "-fMask" not in args
    assert args.count("-p") == 2


def test_write_label_transform_param_forces_nearest_neighbor(tmp_path: Path):
    src = tmp_path / "TransformParameters.1.txt"
    src.write_text(
        "\n".join(
            [
                '(InitialTransformParametersFileName "TransformParameters.0.txt")',
                '(ResampleInterpolator "FinalBSplineInterpolator")',
                "(FinalBSplineInterpolationOrder 1)",
                '(ResultImagePixelType "float")',
                "",
            ]
        ),
        encoding="utf-8",
    )
    dest = write_label_transform_param(src, tmp_path / "TransformParameters.labels.txt")
    text = dest.read_text(encoding="utf-8")
    assert '(ResampleInterpolator "FinalNearestNeighborInterpolator")' in text
    assert "(FinalBSplineInterpolationOrder 0)" in text
    assert '(ResultImagePixelType "short")' in text
    assert "TransformParameters.0.txt" in text
    assert "FinalBSplineInterpolator" not in text


def test_is_unc_path():
    assert is_unc_path(r"\\uhmc-fs-share\shares\RadOnc\CTQA\1.reg")
    assert is_unc_path("//fileserver/QA/CTQA")
    assert is_unc_path(r"\\?\UNC\fileserver\QA")
    assert not is_unc_path(r"C:\ctqa_tmp\elastix_out")
    assert not is_unc_path(r"\\?\C:\elastix")


def test_scratch_parent_defaults_to_system_temp():
    used = scratch_parent()
    assert used.is_dir()
    assert used.resolve() == Path(tempfile.gettempdir()).resolve()


def test_scratch_parent_uses_writable_override(tmp_path: Path):
    used = scratch_parent(tmp_path)
    assert used.resolve() == tmp_path.resolve()


def test_scratch_parent_falls_back_when_override_unusable(tmp_path: Path):
    blocker = tmp_path / "not_a_directory"
    blocker.write_text("x", encoding="utf-8")
    used = scratch_parent(blocker)
    assert used.is_dir()
    assert used.resolve() == Path(tempfile.gettempdir()).resolve()


def test_rewrite_copied_elastix_paths(tmp_path: Path):
    old = tmp_path / "work"
    new = tmp_path / "dest"
    old.mkdir()
    new.mkdir()
    (new / "TransformParameters.1.txt").write_text(
        f'(InitialTransformParametersFileName "{old / "TransformParameters.0.txt"}")\n',
        encoding="utf-8",
    )
    rewrite_copied_elastix_paths(new, old, new)
    text = (new / "TransformParameters.1.txt").read_text(encoding="utf-8")
    assert str(old) not in text
    assert str(new / "TransformParameters.0.txt") in text


def test_parameter_map_to_translation():
    t = parameter_map_to_transform(
        {"Transform": ["TranslationTransform"], "TransformParameters": ["1.5", "-2", "0.25"]}
    )
    assert isinstance(t, sitk.TranslationTransform)
    assert list(t.GetOffset()) == pytest.approx([1.5, -2.0, 0.25])


def test_transform_from_elastix_files_compose(tmp_path: Path):
    t0 = tmp_path / "TransformParameters.0.txt"
    t1 = tmp_path / "TransformParameters.1.txt"
    t0.write_text(
        "\n".join(
            [
                '(Transform "TranslationTransform")',
                "(TransformParameters 10 0 0)",
                '(InitialTransformParametersFileName "NoInitialTransform")',
                "",
            ]
        ),
        encoding="utf-8",
    )
    t1.write_text(
        "\n".join(
            [
                '(Transform "EulerTransform")',
                "(TransformParameters 0 0 1.5707963267948966 0 0 0)",
                f'(InitialTransformParametersFileName "{t0}")',
                "(CenterOfRotationPoint 0 0 0)",
                '(ComputeZYX "false")',
                "",
            ]
        ),
        encoding="utf-8",
    )
    transform = transform_from_elastix_file(t1)
    moved = transform.TransformPoint((0.0, 0.0, 0.0))
    assert moved == pytest.approx((0.0, 10.0, 0.0), abs=1e-6)


def test_find_registered_result_prefers_stage1(tmp_path: Path):
    (tmp_path / "result.0.mha").write_bytes(b"0")
    (tmp_path / "baseline_on_today.mha").write_bytes(b"b")
    assert find_registered_result(tmp_path).name == "result.0.mha"
    (tmp_path / "result.1.mha").write_bytes(b"1")
    assert find_registered_result(tmp_path).name == "result.1.mha"


def test_registration_summary_two_stage(tmp_path: Path):
    (tmp_path / "TransformParameters.0.txt").write_text(
        '(Transform "TranslationTransform")\n(TransformParameters -1.169049 1.522838 0.664079)\n',
        encoding="utf-8",
    )
    (tmp_path / "TransformParameters.1.txt").write_text(
        '(Transform "EulerTransform")\n(TransformParameters 0.006838 0.001497 -0.002043 0.083128 0.067853 0.071988)\n',
        encoding="utf-8",
    )
    summary = registration_summary(tmp_path)
    assert summary["stages"][0]["type"] == "TranslationTransform"
    assert summary["stages"][0]["translation_mm"] == pytest.approx([-1.169049, 1.522838, 0.664079])
    assert summary["stages"][1]["type"] == "EulerTransform"
    assert summary["stages"][1]["rotation_deg"][0] == pytest.approx(math.degrees(0.006838))
    assert summary["stages"][1]["translation_mm"] == pytest.approx([0.083128, 0.067853, 0.071988])


def test_resample_onto_matches_fixed_grid():
    fixed = sitk.Image(8, 8, 4, sitk.sitkInt16)
    fixed.SetSpacing((1.0, 1.0, 2.0))
    moving = sitk.Image(4, 4, 2, sitk.sitkInt16)
    moving.SetSpacing((2.0, 2.0, 4.0))
    out = resample_onto(moving, fixed)
    assert out.GetSize() == fixed.GetSize()
    assert out.GetSpacing() == fixed.GetSpacing()


def test_extract_plane_axes():
    import numpy as np

    from ctqa_catphan.registration_viewer import extract_plane

    arr = np.arange(2 * 3 * 4, dtype=np.int16).reshape(2, 3, 4)
    axial, i, n = extract_plane(arr, "axial", 1)
    assert n == 2 and i == 1
    assert axial.shape == (3, 4)
    sag, i, n = extract_plane(arr, "sagittal", 0)
    assert n == 4 and sag.shape == (2, 3)
    cor, i, n = extract_plane(arr, "coronal", 1)
    assert n == 3 and cor.shape == (2, 4)


def test_case_has_registration(tmp_path: Path):
    case = tmp_path / "20260101_000000"
    (case / "1.reg").mkdir(parents=True)
    assert case_has_registration(case) is False
    sitk.WriteImage(sitk.Image(2, 2, 2, sitk.sitkInt16), str(case / "CT.mha"))
    sitk.WriteImage(sitk.Image(2, 2, 2, sitk.sitkInt16), str(case / "1.reg" / "result.1.mha"))
    assert case_has_registration(case) is True
