"""App settings in settings.json next to the executable."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SETTINGS_NAME = "settings.json"
ENV_SETTINGS = "CTQA_CATPHAN_SETTINGS"
MACHINES_KEY = "MACHINES"
INSTITUTION_KEY = "Institution"
NOTIFICATIONS_KEY = "Notifications"
WATCHER_KEY = "Watcher"
ERROR_EMAIL_TO_KEY = "error_email_to"
EVENT_EMAIL_TO_KEY = "event_email_to"
NEW_CASE_EMAIL_TO_KEY = "new_case_email_to"
CHAT_CHANNELS = ("google_chat", "slack", "microsoft_teams", "discord")

MASK_GROUPS = (
    ("HU", "num_of_HU_masks"),
    ("UF", "num_of_UF_masks"),
    ("HC", "num_of_HC_masks"),
    ("LC", "num_of_LC_masks"),
    ("geo", "num_of_geo_masks"),
    ("DT", "num_of_DT_masks"),
)


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    argv0 = Path(sys.argv[0]) if sys.argv and sys.argv[0] not in ("", "-c") else Path()
    try:
        argv0 = argv0.expanduser().resolve()
    except OSError:
        argv0 = Path()
    if argv0.suffix.lower() == ".exe" and argv0.is_file():
        return argv0.parent
    pkg = Path(__file__).resolve().parent
    root = pkg.parent
    if root.name.lower() in ("site-packages", "dist-packages"):
        return argv0.parent if argv0.is_file() else Path.cwd()
    return root


def settings_path(*, writing: bool = False) -> Path:
    override = os.environ.get(ENV_SETTINGS, "").strip()
    if override:
        return Path(override)
    return app_dir() / SETTINGS_NAME


def strip_jsonc(text: str) -> str:
    out: list[str] = []
    i = 0
    n = len(text)
    in_string = False
    escape = False
    in_line = False
    in_block = False
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if in_line:
            if ch in "\r\n":
                in_line = False
                out.append(ch)
            i += 1
            continue
        if in_block:
            if ch == "*" and nxt == "/":
                in_block = False
                i += 2
                continue
            i += 1
            continue
        if in_string:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and nxt == "/":
            in_line = True
            i += 2
            continue
        if ch == "/" and nxt == "*":
            in_block = True
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def load_settings(path: str | Path | None = None) -> dict:
    file = Path(path) if path else settings_path()
    if not file.is_file():
        return {}
    text = strip_jsonc(file.read_text(encoding="utf-8"))
    data = json.loads(text) if text.strip() else {}
    return data if isinstance(data, dict) else {}


def save_settings(data: dict, path: str | Path | None = None) -> Path:
    file = Path(path) if path else settings_path(writing=True)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return file


def get_institution(data: dict | None = None) -> str:
    return str((data or load_settings()).get(INSTITUTION_KEY) or "").strip()


def get_machines(data: dict | None = None) -> list[dict]:
    machines = (data or load_settings()).get(MACHINES_KEY) or []
    if not isinstance(machines, list):
        return []
    return [m for m in machines if isinstance(m, dict)]


def named_machines(data: dict | None = None) -> list[dict]:
    out = []
    for machine in get_machines(data):
        name = str(machine.get("NAME") or "").strip()
        if name:
            out.append(machine)
    return out


def machine_by_name(name: str, data: dict | None = None) -> dict | None:
    want = (name or "").strip().lower()
    for machine in named_machines(data):
        if str(machine.get("NAME") or "").strip().lower() == want:
            return machine
    return None


def machine_by_station(station: str, data: dict | None = None) -> dict | None:
    key = (station or "").strip().lower()
    if not key:
        return None
    for machine in named_machines(data):
        names = machine.get("station_names") or []
        if isinstance(names, str):
            names = [names]
        aliases = [str(n).strip().lower() for n in names]
        if key in aliases:
            return machine
        if str(machine.get("NAME") or "").strip().lower() == key:
            return machine
    return None


def default_machine(data: dict | None = None) -> dict | None:
    machines = named_machines(data)
    return machines[0] if machines else None


def watcher_settings(data: dict | None = None) -> dict:
    block = (data or load_settings()).get(WATCHER_KEY) or {}
    return block if isinstance(block, dict) else {}


def notifications(data: dict | None = None) -> dict:
    block = (data or load_settings()).get(NOTIFICATIONS_KEY) or {}
    return block if isinstance(block, dict) else {}


def email_settings(data: dict | None = None) -> dict:
    block = notifications(data).get("email") or {}
    return block if isinstance(block, dict) else {}


def chat_webhook_urls(data: dict | None = None) -> dict[str, str]:
    notes = notifications(data)
    out: dict[str, str] = {}
    for name in CHAT_CHANNELS:
        block = notes.get(name) or {}
        url = ""
        if isinstance(block, dict):
            url = str(block.get("webhook_url") or "").strip()
        elif isinstance(block, str):
            url = block.strip()
        out[name] = url
    return out


def mask_count(machine: dict, key: str) -> int:
    field = f"num_of_{key}_masks"
    try:
        return int(machine.get(field) or 0)
    except (TypeError, ValueError):
        return 0


def mask_stems(machine: dict) -> list[str]:
    stems: list[str] = []
    for key, _field in MASK_GROUPS:
        n = mask_count(machine, key)
        for i in range(1, n + 1):
            stems.append(f"{key}{i}")
    return stems


def label_map(machine: dict) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for index, stem in enumerate(mask_stems(machine), start=1):
        mapping[index] = stem
    return mapping
