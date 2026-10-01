# CTQA-CatPhan Python Port Plan

## Goal

Port `_ref_projects/CTQA` (C# watcher / elastix / per-mask transformix / analysis / HTML / email) to Python under `projects/CTQA-CatPhan-python`.

The name **CTQA** is too general (any CT QA). This product is the **CatPhan morning-QA** pipeline currently running as a Windows service against one scanner, **CTSim1**. Folder and display name: **CTQA-CatPhan**.

---

## What the current system does

The C# solution is an **automated CatPhan CT QA pipeline**, not an interactive viewer.

1. A Windows service (`ctqa_service`) or console host (`ctqa_cmd`) watches an import share for a new folder whose name contains **`DailyQA`**.
2. Live clinic service param is `\\fileserver\QA\CTQA-CatPhan\param.txt` (also referenced from `ctqa_service\App.config`). It waits until the folder has **at least 401 files** (10 s poll, up to ~30 min).
3. DICOM files are sorted patient → study → series via `dicomtools\sort_files_by_patient_study_series.exe`.
4. Series with **≥ 100** `.dcm` files is treated as a CT volume. `StationName` (lowercased) maps to a machine root (`ctsim` / `daily qa_ctsim` / `dailyqa_sa_ctsim` → **CTSim1**). TrueBeam / CATPHAN604 keys exist in the older param file but are **out of scope** for the first port (one machine: CTSim1).
5. The series is converted to `CT.mhd` via `dicomtools\dicom_series_to_mhd.exe`.
6. `ctqa.run()`:
   - **Register** today’s CT (fixed, `CT.mhd`) to the baseline CT (moving, `baseline\CT.nrrd`) with **elastix** translation then rigid, using `baseline\fuz_mask.nrrd` as the **moving mask**.
   - **Transfer masks** with **transformix, one mask at a time** (HU, UF, HC, LC, geo, DT), then threshold + cast to uchar.
   - **Analyze** mean / std / center-of-mass distances.
   - Write `3.analysis\report.html` from the machine HTML template.
   - Email the report.
7. Results are copied to `{cases_dir}\{SeriesDate}_{StudyTime}`. Live completed cases sit under `CTSim1\cases\YYYYMMDD_HHMMSS`.

C# `dicomtools.cs` and `imagetools.cs` are **thin wrappers** that shell out to `_dep_apps` executables. Python should reimplement those operations in-process with SimpleITK / pydicom, not call those exes.

---

## Clinic data (first machine)

| Item | Path |
|---|---|
| App data root | `\\fileserver\QA\CTQA-CatPhan` |
| Service param (live) | `...\CTQA\param.txt` |
| Older service param | `...\CTQA\param.txt` (`watch_path=\\fileserver\QA\CT_Import`) |
| Machine | **CTSim1** only for v1 |
| Machine param | `...\CTQA\CTSim1\param.txt` |
| Baseline | `...\CTQA\CTSim1\baseline` (`CT.nrrd`, `fuz_mask.nrrd`, `HU1..9`, `UF1..5`, `HC1..15`, `LC1`, `geo1..4`, `DT1..2`, `id2label.txt`, CSV baselines) |
| Elastix params (C# only) | `...\CTQA\CTSim1\etx_params` |
| Report template | `...\CTQA\CTSim1\report_templates\full\report.html` |
| Completed cases | `...\CTQA\CTSim1\cases\` |
| Watch path (live) | `\\fileserver\QA\CT_Import` |
| Folder filter | name contains `DailyQA` |
| Min files | `401` |
| Temp DICOM sort | `C:\ctqa_tmp` |

**Decided paths (this review):**

| Setting | Value |
|---|---|
| Watch path | **`\\fileserver\QA\CT_Import`** (live `param.txt`) |
| Folder filter | name contains `DailyQA` |
| Min files | `401` |
| Case destination | **same live tree:** `CTSim1\cases\{SeriesDate}_{StudyTime}` (settings `cases_dir`). Dual-run with C# will share this folder — stop the C# service before the Python service cutover. |
| DICOM in case folder | **Keep** the archived series (all slices) plus `CT.mha` / `info.txt` |
| Baseline images | **Convert nrrd → compressed `.mha`** in `CTSim1\baseline` |

CTSim1 mask counts (from `param.txt`): **9 HU, 5 UF, 15 HC, 1 LC, 4 geo, 2 DT** → **36 binary masks**.

Tolerances: `HU_tol=40`, `geo_tol=1.0 mm`, `DT_tol=1.5 mm`, `UF_tol=20`, `LC_tol=10`, `UF.uniformity_tol=0.2`, `HC_RMTF_tol=0.5`, `HC_RMTF50_tol=2.0`.

---

## Daily phantom setup variation (from live elastix)

Elastix writes a two-stage Euler transform. Sample of 15 recent CTSim1 cases (`1.reg\TransformParameters.1.txt`, parameters `rx ry rz tx ty tz`; rotations in **radians**):

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

**Pack on the baseline only.** The 36 individual masks live in the machine `baseline` folder and **will change over time** (physicist edits, new CatPhan inserts, re-drawn ROIs). Packing is a **cache of that baseline**, not something rebuilt from scratch on every daily case.

Suggested files next to the baseline CT (compressed `.mha`):

```
CTSim1/baseline/
  HU1.nrrd … DT2.nrrd     # source of truth (or .mha after conversion)
  masks_packed.mha        # one label volume, pixel values 1…36
  masks_packed.json       # label → name map (HU1=1, …)
```

**When to (re)pack.** Before registration/transfer, and when the GUI saves baseline edits:

1. If `masks_packed.mha` is missing, pack now.
2. If **any** individual mask file (or the label-map JSON) has a **newer filesystem mtime** than `masks_packed.mha`, pack now.
3. Otherwise reuse the existing packed image.

Daily `analyze` then does **one** nearest-neighbor resample of `masks_packed.mha` onto today’s CT, writes `2.seg/masks_packed.mha`, and unpacks to individual `HU1.mha` … for analysis. It does **not** pack today’s masks.

- Reuse `combine_sitk_labels` from `_ref_projects/vtk_image_labeler_3d/.../itk_tools.py`. Pack **all groups in one volume**, not six composites.
- Stable map, e.g. `HU1=1 … HU9=9, UF1=10 …`. Keep the same IDs across baseline edits so historical cases stay comparable.
- Resample labels with `sitkNearestNeighbor`. Unpack by **integer equality**.
- Masks should not overlap. If two labels collide, last-write-wins and log a warning.
- After a GUI **Save** of baseline layers: write each edited individual mask, then pack immediately (or delete `masks_packed.mha` so the next run rebuilds).
- After a GUI **Save** of today’s transferred layers: write that case’s `2.seg` masks only; do **not** write back to baseline unless the user explicitly chooses “save as new baseline”.

DocuForms2 `python_app` is a **reference prototype** (SimpleITK rigid + composite transfer + compressed MHD). It is **not** the product: it packs **per key on every case**, still talks elastix-style `TransformParameters.txt`, and has no watcher/GUI.

---

## Image format rule

**All images written by this app use compressed MetaImage `.mha`** (`sitk.ImageFileWriter` + `SetUseCompression(True)`).

That includes:

- Today’s CT (`CT.mha`)
- **Baseline** packed labels (`masks_packed.mha`) and the **transferred** packed labels on today’s grid
- Unpacked binary masks (baseline after edit-save; today’s `2.seg`)
- Registration resample of the baseline onto today (for visual QA)
- Analysis crops / thresholded geo-DT helper images

Do **not** write uncompressed `.mhd` + `.zraw`, and do **not** write `.nrrd` for new outputs.

**Baseline conversion (decided):** convert existing CTSim1 `baseline\*.nrrd` (CT, `fuz_mask`, and all HU/UF/HC/LC/geo/DT masks) to compressed `.mha` in the **same baseline folder**. Runtime prefers `.mha`. Leave the original `.nrrd` files in place until a converted case is signed off, then they can be deleted. GUI paint-save writes `.mha` only.

**DICOM archive (decided):** keep the DailyQA **DICOM series in the case folder**, same as C# (`copy_files` of the sorted series). Also write `CT.mha` + `info.txt`. Do not drop slices after conversion.

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
| **GUI** | no args, or `--mode gui` | Load **baseline** or **today’s case** with mask layers; paint/pencil edit; inspect registration; run one case; settings / NSSM |
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
- Shared `_users` JSON profiles are **not** required for v1 unless you want per-user “My machines / All machines” subscriptions like Winston-Lutz. Clinic + per-machine email lists and chat webhooks **are** in v1 (see Notifications).

---

## Notifications (Winston-Lutz model)

Reuse the same **system vs machine** split and the same chat webhook channels as `projects/winstonlutz` (`Notifications` in `settings.json`, `emailer.post_chat_webhooks`, Settings → Notifications **Send test**). Copy/adapt that code rather than inventing a second scheme.

### Clinic-wide (`Notifications`)

| Channel | Winston-Lutz role | CTQA-CatPhan role |
|---|---|---|
| `email.error_email_to` | crashes, `logger.exception`, missing watch folder | same |
| `email.event_email_to` | watcher start/stop; archive events | watcher start/stop |
| `email.new_case_email_to` | clinic-wide report after watcher analysis | clinic-wide CatPhan `report.html` after a successful case |
| SMTP | `email_from`, `email_domain`, `email_host_address`, port, SSL, `email_from_enc_pw` | same clinic SMTP (`qa` / `smtp.example.edu`, empty password today) |
| `google_chat.webhook_url` | incoming webhook, `{"text": "..."}` | same |
| `slack.webhook_url` | `{"text": "..."}` | same |
| `microsoft_teams.webhook_url` | `{"text": "..."}` | same |
| `discord.webhook_url` | `{"content": "..."}` | same |

Empty email arrays or empty webhook URLs turn that channel **off**. Uncaught exceptions and analysis failures go to **error_email_to** and **every chat webhook that is filled in**. Settings UI includes **Send test** per email group and per chat channel (unsaved form values, like Winston-Lutz).

### Per machine (`MACHINES[]`, e.g. CTSim1)

| Field | Role |
|---|---|
| `new_case_email_to` | **Extra** report recipients, **added to** clinic `Notifications.email.new_case_email_to`. Empty = clinic list only. Seed CTSim1 from current `param.txt`: `physicists@example.edu`, `therapists@example.edu` (plus `email_domain`). |

GUI **Analyze** can skip sending the report (dry-run / checkbox). Watcher/service sends the report the same way C# `email_report` does today, using the lists above.

DocuForms2 IGRT post-process from Winston-Lutz is **not** part of this CatPhan port unless you ask for it later.

---

## GUI visualization (`vtk_image_labeler_3d`)

The user named `tvk_image_labeler_3d`; the tree is **`_ref_projects/vtk_image_labeler_3d`**.

Reuse a **subset** as a library inside this project (copy/adapt, do not depend on the full labeling app):

| Reuse | Purpose |
|---|---|
| `viewer2d.py` | Axial/sagittal/coronal slice, window/level, overlay |
| `viewer3d.py` / `reslicer.py` / `imageplanewidget*` | Optional 3D planes |
| `itk_tools.py` / `itkvtk.py` / `vtk_image_wrapper.py` | SITK ↔ VTK, `combine_sitk_labels` |
| `vtk_segmentation_list_manager.py` | Mask **layers**, **paint** brush, **pencil** stroke (already in that file) |

**Do not port** nnU-Net, Eclipse client, graph-cut, fill-between-slices, or the rest of the annotation editor (boxes/lines/points) unless we later need them.

### Two load modes

The same 2D/3D viewers, two datasets:

| Mode | Image | Segmentation layers |
|---|---|---|
| **Baseline** | `baseline/CT.mha` (read `.nrrd` only if `.mha` is missing) | Individual HU/UF/HC/LC/geo/DT masks (source of truth). Packed labels are a cache; the layer list is the unpacked names from `id2label.txt`. |
| **Today** | case `CT.mha` | Transferred masks in `2.seg` (unpacked). Optional extra overlay: resampled baseline CT (checkerboard / fade) to check the small rigid shift. |

Switching mode is a first-class action (menu or case browser: “Open baseline…” vs “Open case…”). Layers stay named `HU1`, `UF2`, … with toggle, color, and opacity like the labeler.

### Mask editing (paint and pencil)

Physicists must be able to **correct masks** when a ROI is wrong or the baseline is updated.

Bring over from `vtk_segmentation_list_manager.py`:

- **Paint / eraser brush** — circular brush on the current slice (2D); optional 3D brush later.
- **Pencil** — click-drag stroke (and erase) on the current slice.

Select the active layer in the mask list, then paint. Save:

- **Baseline session:** write each changed individual mask into `baseline/`, then rebuild `masks_packed.mha` (or bump mtimes so the timestamp check repacks). This is how the packed cache stays in sync as the baseline changes over months.
- **Today session:** write `2.seg/*.mha` for that case only. Offer **Re-analyze** using the edited masks (no re-registration required). Do not overwrite baseline unless the user confirms “promote to baseline”.

Undo of the last stroke is desirable if the labeler already has it; full history is not required for v1.

### Other GUI jobs

1. Run **Analyze** on a picked folder (DICOM dir or existing case).
2. Show the HTML report and pass/fail table.
3. Settings dialog (watch path, machine, **Notifications**: SMTP lists + Google Chat / Slack / Teams / Discord webhooks, Send test).
4. Later: NSSM install dialog, copyable error log (Winston-Lutz pattern).

Winston-Lutz GUI is 2D EPID; this GUI is **3D CT**. That is why the VTK labeler viewers (and its paint/pencil tools) are the right starting point, not `winstonlutz/gui.py`.

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
    dicom_io.py           # series sort + DICOM → compressed CT.mha + info.txt; keep DICOMs
    convert_baseline.py   # one-time nrrd → compressed .mha in baseline/
    registration.py       # SimpleITK Euler3D + MMI + fuz_mask
    masks.py              # baseline pack-if-stale; NN transfer; unpack; compressed .mha
    analysis.py           # mean, std, COM, distances, UF INU, HC RMTF
    pipeline.py           # ctqa.run equivalent
    report.py             # HTML from existing template
    emailer.py            # SMTP + Google Chat / Slack / Teams / Discord webhooks
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
| Chat webhooks | stdlib `urllib` POST, same payloads as Winston-Lutz |
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
| `email.cs` | `emailer.py` (SMTP + chat webhooks) |
| `ctqa_service` / `ctqa_cmd` | `cli.py --mode service` + NSSM |
| (new) | `gui.py` + VTK viewers |

---

## Phased work (no coding until review)

### Phase 0 — Scaffold

Package, CLI stub (`gui` / `service` / `analyze`), settings schema, logging, README. Document data-root and CTSim1 layout.

### Phase 1 — DICOM → compressed `CT.mha` + `info.txt`; baseline nrrd → mha

- Replace `dicom_series_to_mhd.exe`. Sort by `ImagePositionPatient`, apply rescale slope/intercept, write compressed `.mha`.
- **Archive DICOMs** into the case folder with the volume (same as C#).
- One-time **`convert_baseline`**: each baseline `.nrrd` → compressed `.mha` beside it. Prefer `.mha` at runtime.
- Compare geometry (size, spacing, origin, direction) to a historical `CT.mhd` on one CTSim1 case.

### Phase 2 — Baseline pack-if-stale + registration + one-shot transfer

- Pack **all 36 baseline masks** into `baseline/masks_packed.mha` only when missing or **stale vs individual mask mtimes**.
- Daily path: SimpleITK rigid, then **one** NN resample of that packed volume onto today; unpack to `2.seg`.
- SimpleITK vs historical elastix: translation within ~1 mm, rotation within ~0.5° is the expected ballpark.
- Visual check: overlay vs C# `2.seg\*.nrrd` Dice / COM.

**Acceptance:** Dice of transferred HU/UF masks vs C# nrrd **≥ 0.95** on 2–3 historical cases; geo/DT COM within **~0.5 mm**. Touching one baseline mask file forces a pack rebuild on the next run.

### Phase 3 — Analysis numbers

Port mean/std/COM/distances/UF INU/HC RMTF. Golden-compare CSV vs `3.analysis` on the same cases (after using **C# masks** first, then again with **Python masks**).

**Acceptance:** HU means within **1 HU** when using identical masks; distances within **0.2 mm**.

### Phase 4 — Report + notifications

Same HTML template tokens and pass/fail. SMTP + chat webhooks as in **Notifications (Winston-Lutz model)**. GUI analyze may skip send; watcher always notifies on success/failure according to the lists. Seed CTSim1 `new_case_email_to` from current machine `param.txt`.

### Phase 5 — Watcher / service

- Watch **`\\fileserver\QA\CT_Import`**, folder name contains `DailyQA`, min **401** files, 10 s wait.
- StationName → CTSim1. Process, then copy **DICOMs + results** to **`CTSim1\cases\{SeriesDate}_{StudyTime}`**.
- NSSM notes. Queue + lock so one case is not processed twice.

### Phase 6 — GUI (view + edit)

VTK 2D (required) + 3D planes (nice).

- Open **baseline** with mask layers, or open **today’s case** with transferred mask layers.
- Paint brush and pencil (from `vtk_segmentation_list_manager`) to edit the active layer.
- Save baseline → individual masks + rebuild packed cache. Save today → `2.seg` + optional re-analyze.
- Case browser, registration overlay, run analyze, settings. Service-install dialog can follow Winston-Lutz once the watcher is stable.

### Phase 7 — Packaging

One PyInstaller windowed `CTQA-CatPhan.exe`. Sample anonymized CatPhan volume in-repo if legal.

---

## Out of scope for v1

- Additional machines (TrueBeamSH / CATPHAN604).
- Keeping elastix as a fallback flag (unless Phase 2 fails parity).
- Trend charts / long-term DB (`analysis/` C# project).
- Rewriting clinic `baseline\*.nrrd` **in place as nrrd** (we convert **to `.mha`** beside them instead).
- nnU-Net / auto-segmentation of CatPhan inserts.
- OIDC / `_users` per-user report subscriptions (clinic + machine email lists and chat webhooks cover v1).
- Winston-Lutz DocuForms2 post-processing.

---

## Testing plan

1. Unit: pack/unpack round-trip; **pack-if-stale** (missing packed file; older packed file vs a newer individual mask; skip pack when packed is newest); RMTF 50% interpolation; INU formula; settings load.
2. DICOM: one anonymized 401-slice series → `CT.mha` geometry vs C# MHD.
3. Registration: 3 recent CTSim1 cases; save `baseline_on_today.mha`; you review overlay.
4. Masks: Dice vs C# `2.seg`.
5. Analysis: CSV vs C# `3.analysis` with frozen masks.
6. Watcher: temp dir named `*_DailyQA`, drop dummy files to `min_num_of_files`, assert one job.

No PHI in git. Copy fixtures locally from your clinic data root for development only.

---

## Risks

- **UNC watcher.** Same as Winston-Lutz: pair `watchdog` with a periodic scan of `watch_path`.
- **Incomplete DICOM write.** Keep the 401-file wait; add “file count stable for N seconds”.
- **SimpleITK vs elastix numeric drift.** Daily motion is small, so masks should still land on inserts; validate with Dice/COM, not pixel-identical warps.
- **VTK + PyInstaller.** Bundle VTK/Qt carefully (Winston-Lutz already solved windowed-stdio; 3D VTK is heavier).
- **Baseline nrrd vs new mha.** Convert beside the nrrd files; prefer `.mha`. Do not delete nrrd until a converted analysis is reviewed.
- **Shared `cases` with C#.** Two services writing the same case folder will collide. Cut over the watcher only after GUI parity is accepted.

---

## Success criteria

- `analyze` on a CTSim1 DailyQA series produces compressed `.mha` CT + transferred unpacked masks + CSV/HTML that match C# closely enough for clinical tols.
- Baseline packing is **cached** (`masks_packed.mha`) and rebuilt only when an individual mask is newer (or the packed file is missing). Daily transfer is **one** resample, not 36.
- `--mode service` watches `\\fileserver\QA\CT_Import` and writes to `CTSim1\cases`, same as live C#, without elastix/dicomtools/imagetools exes.
- Notifications: clinic `error` / `event` / `new_case` email lists, per-machine extra `new_case_email_to`, and Google Chat / Slack / Teams / Discord webhooks (Winston-Lutz payloads + Send test).
- `--mode gui` can open **baseline** or **today** with mask layers, **paint/pencil**-edit, save, and run one analysis.
- **No elastix, no C# imagetools/dicomtools exes** at runtime.

---

## Open points

All previous clinic options are **decided**:

1. Packed labels: one volume, baseline-only, pack-if-stale.
2. Keep DICOMs in the case folder.
3. Convert baseline nrrd → compressed `.mha` (leave nrrd until signed off).
4. Case destination: `CTSim1\cases\{SeriesDate}_{StudyTime}`.
5. Watch path: `\\fileserver\QA\CT_Import`.
6. Notifications: Winston-Lutz clinic/machine email + Google Chat / Slack / Teams / Discord webhooks.

Nothing else is blocking a coding start after you say to proceed.

---

## Recommended order after approval

1. Phase 1–2 (I/O + registration + packed masks) — this is the slow/fragile part of C#.
2. Phase 3–4 (numbers + HTML + Winston-Lutz-style notifications).
3. Phase 6 GUI: load baseline **or** today with layers; paint/pencil edit; registration overlay.
4. Phase 5 service + NSSM.
5. Phase 7 exe.

Ship a physicist-usable **analyze + GUI review** before cutting over the live service.
