"""SimpleITK rigid (Euler3D) registration of baseline CT onto today's CT."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import SimpleITK as sitk

from .image_io import write_mha

logger = logging.getLogger(__name__)


def _as_float(image: sitk.Image) -> sitk.Image:
    if image.GetPixelID() == sitk.sitkFloat32:
        return image
    return sitk.Cast(image, sitk.sitkFloat32)


def _maybe_mask(path: Path | None, reference: sitk.Image) -> sitk.Image | None:
    if path is None or not path.is_file():
        return None
    mask = sitk.ReadImage(str(path))
    if (
        mask.GetSize() != reference.GetSize()
        or mask.GetSpacing() != reference.GetSpacing()
        or mask.GetOrigin() != reference.GetOrigin()
    ):
        mask = sitk.Resample(
            mask,
            reference,
            sitk.Transform(),
            sitk.sitkNearestNeighbor,
            0.0,
            mask.GetPixelID(),
        )
    return mask


def register_rigid(
    fixed: sitk.Image,
    moving: sitk.Image,
    out_dir: str | Path,
    *,
    fixed_mask: Path | None = None,
    moving_mask: Path | None = None,
    number_of_iterations: int = 200,
    histogram_bins: int = 50,
) -> sitk.Transform:
    dest = Path(out_dir)
    dest.mkdir(parents=True, exist_ok=True)
    fixed_f = _as_float(fixed)
    moving_f = _as_float(moving)

    method = sitk.ImageRegistrationMethod()
    method.SetMetricAsMattesMutualInformation(numberOfHistogramBins=histogram_bins)
    method.SetInterpolator(sitk.sitkLinear)
    method.SetOptimizerAsGradientDescent(
        learningRate=1.0,
        numberOfIterations=number_of_iterations,
        convergenceMinimumValue=1e-6,
        convergenceWindowSize=10,
    )
    method.SetOptimizerScalesFromPhysicalShift()
    method.SetShrinkFactorsPerLevel([4, 2, 1])
    method.SetSmoothingSigmasPerLevel([2, 1, 0])
    method.SmoothingSigmasAreSpecifiedInPhysicalUnitsOn()

    initial = sitk.CenteredTransformInitializer(
        fixed_f,
        moving_f,
        sitk.Euler3DTransform(),
        sitk.CenteredTransformInitializerFilter.GEOMETRY,
    )
    method.SetInitialTransform(initial, inPlace=False)

    fmask = _maybe_mask(fixed_mask, fixed_f)
    mmask = _maybe_mask(moving_mask, moving_f)
    if fmask is not None:
        method.SetMetricFixedMask(fmask)
    if mmask is not None:
        method.SetMetricMovingMask(mmask)

    logger.info("starting rigid registration (MMI, Euler3D, 3-level pyramid)")
    transform = method.Execute(fixed_f, moving_f)
    metric = method.GetMetricValue()
    logger.info("registration finished, metric=%s", metric)

    actual = transform
    if hasattr(transform, "GetNumberOfTransforms") and transform.GetNumberOfTransforms() > 0:
        actual = transform.GetNthTransform(0)

    sitk.WriteTransform(actual, str(dest / "transform.tfm"))
    params = list(actual.GetParameters()) if hasattr(actual, "GetParameters") else []
    center = list(actual.GetCenter()) if hasattr(actual, "GetCenter") else []
    (dest / "transform.json").write_text(
        json.dumps(
            {
                "type": type(actual).__name__,
                "parameters": params,
                "center": center,
                "metric": float(metric),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    resampled = sitk.Resample(
        moving_f,
        fixed_f,
        transform,
        sitk.sitkLinear,
        0.0,
        sitk.sitkFloat32,
    )
    resampled_int = sitk.Cast(resampled, sitk.sitkInt16)
    write_mha(resampled_int, dest / "baseline_on_today.mha")
    return transform


def load_transform(reg_dir: str | Path) -> sitk.Transform:
    path = Path(reg_dir) / "transform.tfm"
    if not path.is_file():
        raise FileNotFoundError(path)
    return sitk.ReadTransform(str(path))
