"""CTQA GUI: baseline/case CSV values, Edit masks via vtk_image_labeler_3d."""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from .app_settings import default_machine, load_settings
from .labeler_project import (
    csv_paths,
    launch_labeler,
    read_csv_table,
    write_baseline_project,
    write_case_project,
)

logger = logging.getLogger(__name__)


def _table_widget(rows: list[list[str]]) -> QTableWidget:
    table = QTableWidget()
    if not rows:
        table.setRowCount(0)
        table.setColumnCount(0)
        return table
    headers = rows[0]
    body = rows[1:] if len(rows) > 1 else []
    table.setColumnCount(len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setRowCount(len(body) if body else 0)
    if not body and len(rows) == 1:
        table.setRowCount(1)
        for col, cell in enumerate(headers):
            table.setItem(0, col, QTableWidgetItem(cell))
        table.horizontalHeader().hide()
    else:
        for r, row in enumerate(body):
            for c, cell in enumerate(row):
                table.setItem(r, c, QTableWidgetItem(cell))
    table.resizeColumnsToContents()
    table.setEditTriggers(QTableWidget.NoEditTriggers)
    table.setAlternatingRowColors(True)
    return table


class ValuesWindow(QDialog):
    def __init__(
        self,
        parent,
        *,
        title: str,
        csv_dir: Path,
        project_writer,
        settings: dict,
        can_edit_masks: bool,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self._project_writer = project_writer
        self._settings = settings
        self._csv_dir = csv_dir
        self.resize(980, 640)
        self.setWindowFlag(Qt.Window, True)

        tabs = QTabWidget()
        files = csv_paths(csv_dir)
        if not files:
            tabs.addTab(QLabel(f"No CSV files in {csv_dir}"), "—")
        for path in files:
            try:
                rows = read_csv_table(path)
            except OSError as exc:
                logger.exception("read csv %s", path)
                tabs.addTab(QLabel(str(exc)), path.name)
                continue
            tabs.addTab(_table_widget(rows), path.stem)

        edit = QPushButton("Edit masks")
        edit.setEnabled(can_edit_masks)
        edit.clicked.connect(self._edit_masks)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        buttons = QHBoxLayout()
        buttons.addWidget(edit)
        buttons.addStretch(1)
        buttons.addWidget(close)

        root = QVBoxLayout(self)
        hint = QLabel(str(csv_dir))
        hint.setTextInteractionFlags(Qt.TextSelectableByMouse)
        hint.setWordWrap(True)
        root.addWidget(hint)
        root.addWidget(tabs, 1)
        root.addLayout(buttons)

    def _edit_masks(self):
        try:
            project = self._project_writer()
            launch_labeler(project, self._settings)
        except Exception as exc:
            logger.exception("launch Image Labeler 3D")
            QMessageBox.critical(self, "Edit masks", str(exc))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CTQA-CatPhan")
        self.settings = load_settings()
        self.machine = default_machine(self.settings)
        self._child_windows: list[QDialog] = []
        bar = QToolBar()
        self.addToolBar(bar)
        for text, slot in (
            ("Show baseline", self.show_baseline),
            ("Open case", self.open_case),
            ("Analyze case", self.analyze_case),
            ("Settings", self.open_settings),
        ):
            act = QAction(text, self)
            act.triggered.connect(slot)
            bar.addAction(act)

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.addWidget(
            QLabel(
                "Show baseline or Open case to review CSV values.\n"
                "Edit masks launches vtk_image_labeler_3d with vtk_image_labeler_3d.project.json."
            )
        )
        layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        self.setCentralWidget(scroll)
        self.resize(640, 280)

    def open_settings(self):
        from .settings_dialog import SettingsDialog

        dlg = SettingsDialog(self)
        if dlg.exec_():
            self.settings = load_settings()
            self.machine = default_machine(self.settings)

    def show_baseline(self):
        if not self.machine:
            QMessageBox.warning(self, "CTQA-CatPhan", "No machine in settings.json")
            return
        folder = Path(str(self.machine.get("baseline_dir") or ""))
        if not folder.is_dir():
            chosen = QFileDialog.getExistingDirectory(self, "Baseline folder")
            if not chosen:
                return
            folder = Path(chosen)
        self._open_values(
            title=f"Baseline — {folder.name}",
            csv_dir=folder,
            project_writer=lambda: write_baseline_project(folder, self.machine),
            can_edit_masks=True,
        )

    def open_case(self):
        chosen = QFileDialog.getExistingDirectory(self, "Case folder")
        if not chosen:
            return
        folder = Path(chosen)
        csv_dir = folder / "3.analysis" if (folder / "3.analysis").is_dir() else folder
        self._open_values(
            title=f"Case — {folder.name}",
            csv_dir=csv_dir,
            project_writer=lambda: write_case_project(folder, self.machine or {}),
            can_edit_masks=True,
        )

    def _open_values(self, *, title: str, csv_dir: Path, project_writer, can_edit_masks: bool):
        win = ValuesWindow(
            self,
            title=title,
            csv_dir=csv_dir,
            project_writer=project_writer,
            settings=self.settings,
            can_edit_masks=can_edit_masks,
        )
        self._child_windows.append(win)
        win.show()

    def analyze_case(self):
        folder = QFileDialog.getExistingDirectory(self, "Case to analyze")
        if not folder:
            return
        try:
            from .pipeline import run_case

            run_case(folder, send_email=False, data=self.settings)
            QMessageBox.information(self, "CTQA-CatPhan", "Analysis finished.")
            case = Path(folder)
            csv_dir = case / "3.analysis" if (case / "3.analysis").is_dir() else case
            self._open_values(
                title=f"Case — {case.name}",
                csv_dir=csv_dir,
                project_writer=lambda: write_case_project(case, self.machine or {}),
                can_edit_masks=True,
            )
        except Exception as exc:
            logger.exception("analyze failed")
            QMessageBox.critical(self, "CTQA-CatPhan", str(exc))


def run_app() -> int:
    app = QApplication.instance() or QApplication([])
    win = MainWindow()
    win.show()
    return app.exec_()
