# CTQA-CatPhan

Python port of the CatPhan CT morning-QA pipeline (C# `CTQA`). One process, two modes:

```
python -m ctqa_catphan                 # GUI
python -m ctqa_catphan --mode service  # DailyQA folder watcher
python -m ctqa_catphan analyze <case>
python -m ctqa_catphan convert-baseline
```

Copy `settings.sample.json` to `settings.json` next to the project (or pass `--settings`) and fill in your institution, share paths, SMTP, and machine folders. `settings.json` is gitignored.

## Pipeline

1. DICOM series → compressed `CT.mha` + `info.txt` (DICOMs stay in the case folder).
2. Pack baseline HU/UF/HC/LC/geo/DT masks into `baseline/masks_packed.mha` **only if missing or stale** (any individual mask newer than the packed file).
3. SimpleITK rigid registration (Euler3D + Mattes MI, moving mask `fuz_mask`).
4. One nearest-neighbor resample of packed labels onto today; unpack to `2.seg/*.mha`.
5. Analysis CSVs + `3.analysis/report.html`.
6. Email (`error` / `event` / `new_case`, plus per-machine extras) and optional Google Chat / Slack / Teams / Discord webhooks.

Completed watcher cases go to `{machine}\cases\{SeriesDate}_{StudyTime}`.

## GUI

**Show baseline** or **Open case** opens a window of the `*.csv` values (baseline folder, or `3.analysis` for a case). **Edit masks** writes `vtk_image_labeler_3d.project.json` next to the CT and launches **vtk_image_labeler_3d** with that file. Set `Viewer.vtk_image_labeler_3d` to the labeler executable if it is not on PATH. **Analyze case** runs the pipeline without email.

## Convert baseline nrrd

```
python -m ctqa_catphan convert-baseline
```

Writes compressed `.mha` beside existing `.nrrd` files. Runtime prefers `.mha`.

## Tests

```
pip install -e .[dev,gui]
pytest
```
