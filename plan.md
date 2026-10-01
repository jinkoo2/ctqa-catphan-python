# CTQA-CatPhan Python Port Plan

## Goal

Port `_ref_projects/CTQA` (C# watcher / elastix / per-mask transformix / analysis / HTML / email) to Python under `projects/CTQA-CatPhan-python`.

The name **CTQA** is too general (any CT QA). This product is the **CatPhan morning-QA** pipeline currently running as a Windows service against one scanner, **GECTSH**. Folder and display name: **CTQA-CatPhan**.

Do **not** start coding until this plan is reviewed.

---

## What the current system does

The C# solution is an **automated CatPhan CT QA pipeline**, not an interactive viewer.

1. A Windows service (`ctqa_service`) or console host (`ctqa_cmd`) watches an import share for a new folder whose name contains **`DailyQA`**.
2. Live clinic service param is `W:\RadOnc\Applications\Morning QA\CTQA\param_rophysicsqa.txt` (also referenced from `ctqa_service\App.config`). It waits until the folder has **at least 401 files** (10 s poll, up to ~30 min).
3. DICOM files are sorted patient → study → series via `dicomtools\sort_files_by_patient_study_series.exe`.
4. Series with **≥ 100** `.dcm` files is treated as a CT volume. `StationName` (lowercased) maps to a machine root (`ctsim` / `daily qa_ctsim` / `dailyqa_sa_ctsim` → **GECTSH**). TrueBeam / CATPHAN604 keys exist in the older param file but are **out of scope** for the first port (one machine: GECTSH).
5. The series is converted to `CT.mhd` via `dicomtools\dicom_series_to_mhd.exe`.
6. `ctqa.run()`:
   - **Register** today’s CT (fixed, `CT.mhd`) to the baseline CT (moving, `baseline\CT.nrrd`) with **elastix** translation then rigid, using `baseline\fuz_mask.nrrd` as the **moving mask**.
   - **Transfer masks** with **transformix, one mask at a time** (HU, UF, HC, LC, geo, DT), then threshold + cast to uchar.
   - **Analyze** mean / std / center-of-mass distances.
   - Write `3.analysis\report.html` from the machine HTML template.
   - Email the report.
7. Results are copied to `{cases_dir}\{SeriesDate}_{StudyTime}`. Live completed cases sit under `GECTSH\cases\imported\YYYYMMDD_HHMMSS`.

C# `dicomtools.cs` and `imagetools.cs` are **thin wrappers** that shell out to `_dep_apps` executables. Python should reimplement those operations in-process with SimpleITK / pydicom, not call those exes.

---

## Clinic data (first machine)

| Item | Path |
|---|---|
| App data root | `W:\RadOnc\Applications\Morning QA\CTQA` |
| Service param (live) | `...\CTQA\param_rophysicsqa.txt` |
| Older service param | `...\CTQA\param.txt` (`watch_path=\\rovelocity\import`) |
| Machine | **GECTSH** only for v1 |
| Machine param | `...\CTQA\GECTSH\param.txt` |
| Baseline | `...\CTQA\GECTSH\baseline` (`CT.nrrd`, `fuz_mask.nrrd`, `HU1..9`, `UF1..5`, `HC1..15`, `LC1`, `geo1..4`, `DT1..2`, `id2label.txt`, CSV baselines) |
| Elastix params (C# only) | `...\CTQA\GECTSH\etx_params` |
| Report template | `...\CTQA\GECTSH\report_templates\full\report.html` |
| Completed cases | `...\CTQA\GECTSH\cases\imported\` |
| Watch path (live) | `\\varianfs\Velocity_Import` |
| Folder filter | name contains `DailyQA` |
| Min files | `401` |
| Temp DICOM sort | `C:\ctqa_tmp` |

GECTSH mask counts (from `param.txt`): **9 HU, 5 UF, 15 HC, 1 LC, 4 geo, 2 DT** → **36 binary masks**.

Tolerances: `HU_tol=40`, `geo_tol=1.0 mm`, `DT_tol=1.5 mm`, `UF_tol=20`, `LC_tol=10`, `UF.uniformity_tol=0.2`, `HC_RMTF_tol=0.5`, `HC_RMTF50_tol=2.0`.

---

## Daily phantom setup variation (from live elastix)

Elastix writes a two-stage Euler transform. Sample of 15 recent GECTSH cases (`1.reg\TransformParameters.1.txt`, parameters `rx ry rz tx ty tz`; rotations in **radians**):

| Date | rx (rad) | ry | rz | tx (mm) | ty | tz |
|---|---|---|---|---|---|---|
| 20260928 | 0.0068 | 0.0015 | −0.0020 | 0.08 | 0.07 | 0.07 |
| 20260925 | 0.0076 | −0.0057 | −0.0011 | 0.09 | 0.03 | 0.10 |
| 20260918 | 0.0080 | −0.0127 | −0.0021 | −0.10 | −0.02 | −0.05 |
| 20260825 | 0.0081 | −0.0007 | −0.0018 | 0.08 | 0.13 | 0.11 |

The **translation-only** stage (`TransformParameters.0.txt`) absorbs most of the couch/phantom offset, typically **~0.05–2.1 mm** per axis (e.g. 20260928: `−1.17, 1.52, 0.66` mm). After that, the rigid stage rotations are **~0.4°** on the dominant axis and **≪ 1°** on the others; residual translations are **sub-millimeter**.

**Conclusion:** daily CatPhan setup variation is small. A **single SimpleITK rigid (Euler3D)** registration, with `fuz_mask` as the moving mask, is enough. Two-stage elastix is not required. Keep the search range modest so it cannot jump to a wrong local minimum.

---

## Why mask transfer is slow today, and the fix

C# `transfer_masks()` loops every mask and runs **transformix** on a full 512×512×401 volume (then threshold 0–0.5, cast uchar). That is **36 independent 3D resamples** plus process startup.

**Plan:** pack **all 36** baseline masks into **one** label image (distinct integer pixel values 1…36), resample **once** onto today’s CT with **nearest-neighbor**, then unpack to individual binary masks.

- Reuse the packing idea already in `_ref_projects/vtk_image_labeler_3d/.../itk_tools.py` (`combine_sitk_labels`) and the per-group composite in `_ref_projects/DocuForms2/scripts/upload_ctqa/python_app` — but pack **all groups in one volume**, not six composites.
- Assign a stable map, e.g. `HU1=1 … HU9=9, UF1=10 …`, written as `2.seg/label_map.txt` (or JSON) so unpack is deterministic.
- Resample labels with `sitkNearestNeighbor` (not linear/BSpline). Unpack by **integer equality**, not `value ± 0.5` (that band is only needed if someone interpolates floats).
- Masks in a group must not overlap. If two labels collide on a voxel, last-write-wins; log a warning. Baseline CatPhan ROIs are disjoint in practice.

DocuForms2 `python_app` is a **reference prototype** (SimpleITK rigid + composite transfer + compressed MHD). It is **not** the product: no watcher/GUI/NSSM, still talks elastix-style `TransformParameters.txt`, packs **per key** (still six resamples), and was written for a DocuForms upload path.

---

## Image format rule

**All images written by this app use compressed MetaImage `.mha`** (`sitk.ImageFileWriter` + `SetUseCompression(True)`).

That includes:

- Today’s CT (`CT.mha`)
- Packed baseline labels and the transferred packed labels
- Unpacked binary masks
- Registration resample of the baseline onto today (for visual QA)
- Analysis crops / thresholded geo-DT helper images

Do **not** write uncompressed `.mhd` + `.zraw`, and do **not** write `.nrrd` for new outputs.

**Baseline on disk today is `.nrrd`.** Phase 1 may **read** those files in place. Optionally convert a **copy** of the baseline tree to compressed `.mha` (do not overwrite clinic nrrd until you say so).

C# currently also copies **all 401 DICOM slices** into the case folder. That dominates disk use. Proposal: after a successful `CT.mha` conversion, **do not keep the DICOMs** in the archived case (keep `info.txt` + maybe one representative DICOM for tags). Confirm before implementation.

---

## Analysis contract (keep)

Port `ctqa.cs` `analize` / `report` / `email_report` so existing HTML templates and physicist expectations still work.

| Key | Measurement | Output |
|---|---|---|
| HU | mean HU in mask | `HU.csv` |
| UF | mean HU; then integral non-uniformity `(max−min)/(max+min)` | `UF.csv`, `UF.uniformity.csv` |
| HC | std in mask; RMTF = std / std(HC1); 50% crossing interpolated | `HC.csv`, `HC.RMTF.csv`, `HC.RMTF.calc.csv` |
| LC | std | `LC.csv` |
| geo | COM of thresholded crop (`level0=1, th=−500, level1=0` — air holes); pairwise + last-to-first distances | `geo.csv`, `geo.dist.csv` |
| DT | COM (`level0=0, th=200, level1=1` — high-HU beads); distances | `DT.csv`, `DT.dist.csv` |

HTML: same `{{{HU}}}`, `{{{geo}}}`, `{{{DT}}}`, `{{{UF}}}`, `{{{UF.uniformity}}}`, `{{{LC}}}`, `{{{HC.RMTF}}}`, `{{{HC.RMTF.50}}}` tokens and `replace_words_for_report` (geo/DT label remapping). Pass/fail vs machine tols.

User initial comes from DICOM `PatientName` last-name / `^` split, same as C#.

---

## Dual modes (like Winston-Lutz)

One exe / one CLI, two modes:

| Mode | How | Role |
|---|---|---|
| **GUI** | no args, or `--mode gui` | Browse GECTSH cases, overlay CT + masks, inspect registration, run one case, edit settings, optional NSSM install |
| **Service** | `--mode service` (alias `watch`) | Headless folder watcher + queue + process + email |

CLI sketch (mirror Winston-Lutz `prepare_argv`):

```
CTQA-CatPhan.exe                         → GUI
CTQA-CatPhan.exe --mode gui
CTQA-CatPhan.exe --mode service
CTQA-CatPhan.exe --settings <settings.json>
CTQA-CatPhan.exe analyze <case_dir>
CTQA-CatPhan.exe --help
```

- Default settings file next to the exe (or `--settings`).
- Env override e.g. `CTQA_CATPHAN_SETTINGS`.
- NSSM: default service name/display **CTQA-CatPhan**; `AppParameters --mode service --settings ...`.
- Windowed exe must tolerate `sys.stdout is None` (same `ensure_stdio` lesson as Winston-Lutz 0.5.1).
- Shared `_users` JSON profiles are **not** required for v1 unless you want the same notify pattern as Winston-Lutz.

---

## GUI visualization (`vtk_image_labeler_3d`)

The user named `tvk_image_labeler_3d`; the tree is **`_ref_projects/vtk_image_labeler_3d`**.

Reuse a **subset** as a library inside this project (copy/adapt, do not depend on the full labeling app):

| Reuse | Purpose |
|---|---|
| `viewer2d.py` | Axial/sagittal/coronal slice, window/level, overlay |
| `viewer3d.py` / `reslicer.py` / `imageplanewidget*` | Optional 3D planes |
| `itk_tools.py` / `itkvtk.py` / `vtk_image_wrapper.py` | SITK ↔ VTK, `combine_sitk_labels` |
| `vtk_segmentation_list_manager.py` | Toggle HU/UF/HC/… overlays |

**Do not port** nnU-Net, Eclipse client, graph-cut, fill-between-slices, or the full annotation editor.

GUI jobs for this product:

1. Open a case: today’s `CT.mha` + transferred masks (or packed labels).
2. Overlay **resampled baseline CT** vs today (checkerboard / fade) to confirm the small rigid shift.
3. Run **Analyze** on a picked folder (DICOM dir or existing case).
4. Show the HTML report and pass/fail table.
5. Settings dialog (watch path, machine root, email, tols).
6. Later: NSSM install dialog, copyable error log (Winston-Lutz pattern).

Winston-Lutz GUI is 2D EPID; this GUI is **3D CT**. That is why the VTK labeler viewers are the right starting point, not `winstonlutz/gui.py`.

---

## Proposed layout

```
projects/CTQA-CatPhan-python/
  plan.md                 # this file
  pyproject.toml
  README.md
  settings.sample.json
  ctqa_catphan/
    __init__.py
    cli.py                # --mode gui|service, analyze, --settings
    app_settings.py       # JSON settings (import from existing param.txt once)
    logutil.py
    dicom_io.py           # series sort + DICOM → compressed CT.mha + info.txt
    registration.py       # SimpleITK Euler3D + MMI + fuz_mask
    masks.py              # pack / resample NN / unpack; compressed .mha
    analysis.py           # mean, std, COM, distances, UF INU, HC RMTF
    pipeline.py           # ctqa.run equivalent
    report.py             # HTML from existing template
    emailer.py
    watcher.py            # DailyQA folder, min files, queue
    gui.py                # PyQt + VTK viewers
    viewers/              # adapted vtk_image_labeler_3d subset
  packaging/
    build_app.py          # one windowed exe
    install_watch_service.ps1
  tests/
  sample_data/            # anonymized small volume + baseline masks (later)
```

Python package name: `ctqa_catphan`. Exe / NSSM display: **CTQA-CatPhan**.

---

## Recommended libraries

| Concern | Library |
|---|---|
| Registration, resample, stats, `.mha` I/O | **SimpleITK** |
| DICOM tags / series | **pydicom** (+ SimpleITK `ImageSeriesReader` as fallback) |
| Pack/unpack arrays | **numpy** |
| Watcher | **watchdog** + 10 s poll backup (UNC shares miss events) |
| GUI | **PyQt5** + **VTK** (from vtk_image_labeler_3d) |
| Config | JSON settings; one-time import of `key=value` `param.txt` |
| Email | **smtplib** (clinic `email_from_enc_pw` is empty today → unauthenticated SMTP) |
| Tests | **pytest** |
| Service host | NSSM, not a .NET `ServiceBase` |

**No elastix / transformix / dicomtools.exe / imagetools_3d.exe at runtime.**

---

## SimpleITK registration (proposed)

Match elastix intent, not its files:

- **Fixed:** today’s CT. **Moving:** baseline `CT.nrrd` / `.mha`.
- **Moving mask:** `fuz_mask` (C# does not set a fixed mask).
- **Transform:** `Euler3DTransform` (6 DOF).
- **Metric:** Mattes mutual information (64 bins like `Parameters_Rigid.txt`, or 50 as in the DocuForms2 prototype — pick one and lock it in tests).
- **Optimizer:** gradient descent / regular step, ~200 iterations, multi-resolution pyramid (elastix uses recursive pyramids).
- **Init:** geometric centers or the identity (daily offset is a few mm; identity + multi-res should suffice).
- **Interpolator during metric:** linear. **Final CT resample:** linear, cast back to int16. **Label resample:** nearest neighbor.
- Write `1.reg/transform.tfm` (or JSON of 6 parameters + center) and `1.reg/baseline_on_today.mha` (compressed) for GUI review. Do not require elastix `TransformParameters.*.txt`.

---

## C# → Python mapping

| C# | Python |
|---|---|
| `param.cs` | `app_settings.py` |
| `dicomtools.cs` + `sort_files_*` / `dicom_series_to_mhd` | `dicom_io.py` |
| `etx.elastix` / `transformix` | `registration.py` + `masks.py` |
| `imagetools.cs` (mean/std, bbox, crop, threshold, moments, cast) | SimpleITK in `analysis.py` |
| `ctqa.run` / `analize` / `report` | `pipeline.py` + `report.py` |
| `fswatcher` | `watcher.py` |
| `email.cs` | `emailer.py` |
| `ctqa_service` / `ctqa_cmd` | `cli.py --mode service` + NSSM |
| (new) | `gui.py` + VTK viewers |

---

## Phased work (no coding until review)

### Phase 0 — Scaffold

Package, CLI stub (`gui` / `service` / `analyze`), settings schema, logging, README. Document data-root and GECTSH layout.

### Phase 1 — DICOM → compressed `CT.mha` + `info.txt`

Replace `dicom_series_to_mhd.exe`. Sort by `ImagePositionPatient`, apply rescale slope/intercept, write compressed `.mha`. Compare geometry (size, spacing, origin, direction) to a historical `CT.mhd` on one GECTSH case.

### Phase 2 — Registration + packed-mask transfer (highest leverage)

- SimpleITK rigid vs historical elastix: translation within ~1 mm, rotation within ~0.5° on recent cases is the expected ballpark (elastix itself varies day to day).
- Pack 36 masks once (can precompute packed labels next to baseline).
- One NN resample; unpack; save compressed `.mha`.
- Visual check in a notebook or early GUI: overlay vs C# `2.seg\*.nrrd` Dice / COM.

**Acceptance:** Dice of transferred HU/UF masks vs C# nrrd **≥ 0.95** on 2–3 historical cases; geo/DT COM within **~0.5 mm**.

### Phase 3 — Analysis numbers

Port mean/std/COM/distances/UF INU/HC RMTF. Golden-compare CSV vs `3.analysis` on the same cases (after using **C# masks** first, then again with **Python masks**).

**Acceptance:** HU means within **1 HU** when using identical masks; distances within **0.2 mm**.

### Phase 4 — Report + email

Same template tokens, pass/fail, SMTP. Optional: skip email in GUI dry-run.

### Phase 5 — Watcher / service

DailyQA folder filter, min 401 files, 10 s wait, StationName → GECTSH, process, copy into `cases` (or `cases/imported`). NSSM notes. Queue + lock so one case is not processed twice.

### Phase 6 — GUI

VTK 2D (required) + 3D planes (nice). Case browser, overlay, run analyze, settings. Service-install dialog can follow Winston-Lutz once the watcher is stable.

### Phase 7 — Packaging

One PyInstaller windowed `CTQA-CatPhan.exe`. Sample anonymized CatPhan volume in-repo if legal.

---

## Out of scope for v1

- Additional machines (TrueBeamSH / CATPHAN604).
- Keeping elastix as a fallback flag (unless Phase 2 fails parity).
- Trend charts / long-term DB (`analysis/` C# project).
- Rewriting clinic `baseline\*.nrrd` in place.
- nnU-Net / auto-segmentation of CatPhan inserts.
- OIDC / `_users` profiles (add later if you want Winston-Lutz parity).

---

## Testing plan

1. Unit: pack/unpack round-trip, RMTF 50% interpolation, INU formula, settings load.
2. DICOM: one anonymized 401-slice series → `CT.mha` geometry vs C# MHD.
3. Registration: 3 recent GECTSH cases; save `baseline_on_today.mha`; you review overlay.
4. Masks: Dice vs C# `2.seg`.
5. Analysis: CSV vs C# `3.analysis` with frozen masks.
6. Watcher: temp dir named `*_DailyQA`, drop dummy files to `min_num_of_files`, assert one job.

No PHI in git. Copy fixtures locally from `W:\...` for development only.

---

## Risks

- **UNC watcher.** Same as Winston-Lutz: pair `watchdog` with a periodic scan of `watch_path`.
- **Incomplete DICOM write.** Keep the 401-file wait; add “file count stable for N seconds”.
- **SimpleITK vs elastix numeric drift.** Daily motion is small, so masks should still land on inserts; validate with Dice/COM, not pixel-identical warps.
- **VTK + PyInstaller.** Bundle VTK/Qt carefully (Winston-Lutz already solved windowed-stdio; 3D VTK is heavier).
- **Baseline nrrd vs new mha.** Dual-read in v1; convert baseline only after you approve.

---

## Success criteria

- `analyze` on a GECTSH DailyQA series produces compressed `.mha` CT + packed/unpacked masks + CSV/HTML that match C# closely enough for clinical tols.
- Mask transfer is **one** resample, not 36.
- `--mode service` can replace `CTQA_GECTSH_Service` without changing the W: data root or the DailyQA import workflow.
- `--mode gui` (default) can open a case, show CT + masks + registration overlay, and run one analysis.
- **No elastix, no C# imagetools/dicomtools exes** at runtime.

---

## Open points for your review

1. **Packed labels:** one volume for all 36 masks (recommended) vs six per-type composites (DocuForms2).
2. **Archive DICOMs** in the case folder, or drop them after `CT.mha`?
3. **Baseline conversion** to compressed `.mha` now, or read nrrd until cutover?
4. **Case destination:** write new Python cases under `GECTSH\cases\imported` (current live tree) vs a new `GECTSH\cases_python` until dual-running is done?
5. **Watch path:** keep `\\varianfs\Velocity_Import` from `param_rophysicsqa.txt` as the settings default?
6. **Email:** keep sending `report.html` to `RadOnc_Physicists,SBSH_RadOnc_Radiation_Therapists` from day one, or GUI-only until parity is signed off?

---

## Recommended order after approval

1. Phase 1–2 (I/O + registration + packed masks) — this is the slow/fragile part of C#.
2. Phase 3–4 (numbers + HTML) on historical cases.
3. Phase 6 GUI enough to **see** registration/masks (you asked for visualization early).
4. Phase 5 service + NSSM.
5. Phase 7 exe.

Ship a physicist-usable **analyze + GUI review** before cutting over the live service.
