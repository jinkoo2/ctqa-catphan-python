import os

from ctqa_catphan.cli import prepare_argv


def test_prepare_argv_defaults_to_gui(monkeypatch):
    monkeypatch.setattr("sys.argv", ["ctqa-catphan"])
    assert prepare_argv([])[0] == "gui"


def test_prepare_argv_mode_service(monkeypatch):
    monkeypatch.delenv("CTQA_CATPHAN_SETTINGS", raising=False)
    out = prepare_argv(["--mode", "service"])
    assert "watch" in out


def test_prepare_argv_settings_env(monkeypatch, tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text("{}", encoding="utf-8")
    prepare_argv(["--settings", str(settings), "analyze", "x"])
    assert os.environ["CTQA_CATPHAN_SETTINGS"].endswith("settings.json")
