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
3. External **elastix.exe** (translation then rigid, moving mask `fuz_mask`), same command line as C#. Set `Elastix.elastix_dir` to the folder that contains `elastix.exe` and `transformix.exe`. Parameter files default to bundled `etx_params`; set `MACHINES[].elastix_param_dir` to override.
4. External **transformix.exe** warps that **one** packed label image (nearest-neighbor) onto today as `2.seg/masks_packed.mha`. Analysis and vtk_image_labeler_3d read labels from that packed volume (no per-mask unpack).
5. Analysis `analysis.result.json` + `3.analysis/report.html`. Existing CSV result files are left in place; `convert-results` (or the first report/GUI open) writes the matching JSON next to them. New analysis does not write CSVs.
6. Email (`error` / `event` / `new_case`, plus per-machine extras) and optional Google Chat / Slack / Teams / Discord webhooks.
7. Optional **PostProcessing**: push the case to DocuForms2 (`docuforms2_ctqa`) with `input_dcm.zip` and `report.pdf`. A `.docuforms2_ctqa.json` marker in the case folder prevents double submit. Cases are not moved.

Completed watcher cases go to `{machine}\cases\{SeriesDate}_{StudyTime}`.

## GUI

The window matches the Winston-Lutz layout: a navy heading with **CTQA-CatPhan**, `Institution`, the signed-in user, **User settings**, and **Login** / **Logout** (OIDC). Then a gray toolbar (Show Baseline, Open Case, Analyze Case, View Report, Settings, Help). Font size is the same bump as Winston-Lutz (at least 13 pt). **Settings** (`Ctrl+,`) has **General** (`Institution`, `RunMode`), **Identity**, **Email**, **Chat webhooks**, **Viewer**, **Post-processing**, and **Watcher**.

**Identity** (`user_id_method`) defaults to **OSUser** (Windows / Linux / macOS login, no extra prompt). **None** skips a user id. **OIDC** opens a Sign in window, then Keycloak in the browser. Per-user JSON profiles live in `_users` next to the executable (or `--users DIR`). **User settings** (heading bar) is email, My machines, and new QA case emails (Off / My machines / All machines). Those subscriber addresses are added when a case report is emailed.

**Open Case** depends on **RunMode**:

- **Clinic** (default): pick a machine from `MACHINES`, then a case under that machine's `cases_dir`.
- **Simple**: pick a case folder. The parent folder is the machine name. Simple is also used when `settings.json` is missing or `MACHINES` is empty.

**Show Baseline** is per machine: in **Clinic** you pick a machine and the app opens that machine's `baseline_dir`; in **Simple** you pick the baseline folder. Either way, the CSV window can **Edit masks**, which writes `vtk_image_labeler_3d.project.json` and launches **vtk_image_labeler_3d**. Set `Viewer.vtk_image_labeler_3d` if the labeler is not on PATH. **Analyze Case** uses the same Clinic/Simple picker and runs the pipeline without email. **View Report** opens `3.analysis/report.html` in the browser for the current case (enabled after Open Case or Analyze Case when that file exists). Analysis still needs a machine (parent-folder name, or the first named machine) for baseline masks.

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

## CI / CD

GitHub Actions runs tests on **Linux, Windows, and macOS** for every push and pull request (`.github/workflows/ci.yml`).

Pushing a version tag builds desktop apps and a Python package, then attaches them to a GitHub Release (`.github/workflows/release.yml`):

```
# bump version in pyproject.toml first
git add pyproject.toml
git commit -m "Release 0.1.1"
git tag v0.1.1
git push origin HEAD
git push origin v0.1.1
```

The tag must match `vMAJOR.MINOR.PATCH` (for example `v0.1.1`). Each release includes:

- `CTQACatPhan-<version>-windows-x64.exe`
- `CTQACatPhan-<version>-linux-x64`
- `CTQACatPhan-<version>-macos-arm64` (Apple Silicon runner)
- `ctqa_catphan-<version>-py3-none-any.whl` and source tarball

You can also run **Actions → Release → Run workflow** without a tag; that only uploads build artifacts, it does not create a GitHub Release.
