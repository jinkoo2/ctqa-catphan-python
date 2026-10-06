"""PyInstaller entry: GUI by default; ``service`` / ``watch`` for the watcher."""

from ctqa_catphan.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
