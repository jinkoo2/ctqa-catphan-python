from __future__ import annotations

import json

from PyQt5.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .app_settings import (
    ERROR_EMAIL_TO_KEY,
    EVENT_EMAIL_TO_KEY,
    NEW_CASE_EMAIL_TO_KEY,
    chat_webhook_urls,
    email_settings,
    load_settings,
    save_settings,
    settings_path,
)
from .emailer import format_error_email_to, send_test_chat, send_test_email


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("CTQA-CatPhan settings")
        self._data = load_settings()
        email = email_settings(self._data)
        hooks = chat_webhook_urls(self._data)

        self.error_to = QPlainTextEdit(format_error_email_to(email.get(ERROR_EMAIL_TO_KEY)))
        self.event_to = QPlainTextEdit(format_error_email_to(email.get(EVENT_EMAIL_TO_KEY)))
        self.new_case_to = QPlainTextEdit(format_error_email_to(email.get(NEW_CASE_EMAIL_TO_KEY)))
        for box in (self.error_to, self.event_to, self.new_case_to):
            box.setFixedHeight(72)
        self.email_from = QLineEdit(str(email.get("email_from") or ""))
        self.email_domain = QLineEdit(str(email.get("email_domain") or ""))
        self.email_host = QLineEdit(str(email.get("email_host_address") or ""))
        self.email_port = QLineEdit(str(email.get("email_host_port") or 25))

        self.google = QLineEdit(hooks.get("google_chat") or "")
        self.slack = QLineEdit(hooks.get("slack") or "")
        self.teams = QLineEdit(hooks.get("microsoft_teams") or "")
        self.discord = QLineEdit(hooks.get("discord") or "")

        viewer = self._data.get("Viewer") if isinstance(self._data.get("Viewer"), dict) else {}
        self.labeler_path = QLineEdit(str((viewer or {}).get("vtk_image_labeler_3d") or ""))
        self.labeler_path.setPlaceholderText(r"C:\apps\vtk_image_labeler_3d.exe")

        tabs = QTabWidget()
        email_tab = QWidget()
        ef = QFormLayout(email_tab)
        ef.addRow("error_email_to", self.error_to)
        ef.addRow("event_email_to", self.event_to)
        ef.addRow("new_case_email_to", self.new_case_to)
        ef.addRow("email_from", self.email_from)
        ef.addRow("email_domain", self.email_domain)
        ef.addRow("email_host_address", self.email_host)
        ef.addRow("email_host_port", self.email_port)
        test_email = QPushButton("Send test email")
        test_email.clicked.connect(self._test_email)
        ef.addRow("", test_email)
        tabs.addTab(email_tab, "Email")

        chat_tab = QWidget()
        cf = QFormLayout(chat_tab)
        for label, edit, channel in (
            ("Google Chat", self.google, "google_chat"),
            ("Slack", self.slack, "slack"),
            ("Microsoft Teams", self.teams, "microsoft_teams"),
            ("Discord", self.discord, "discord"),
        ):
            btn = QPushButton("Send test")
            btn.clicked.connect(lambda *_a, c=channel: self._test_chat(c))
            box = QGroupBox(label)
            inner = QFormLayout(box)
            inner.addRow("webhook_url", edit)
            inner.addRow("", btn)
            cf.addRow(box)
        tabs.addTab(chat_tab, "Chat webhooks")

        viewer_tab = QWidget()
        vf = QFormLayout(viewer_tab)
        vf.addRow("vtk_image_labeler_3d", self.labeler_path)
        tabs.addTab(viewer_tab, "Viewer")

        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(640, 520)

    def _email_block(self) -> dict:
        from .emailer import error_email_to_list

        return {
            ERROR_EMAIL_TO_KEY: error_email_to_list(self.error_to.toPlainText()),
            EVENT_EMAIL_TO_KEY: error_email_to_list(self.event_to.toPlainText()),
            NEW_CASE_EMAIL_TO_KEY: error_email_to_list(self.new_case_to.toPlainText()),
            "email_from": self.email_from.text().strip(),
            "email_domain": self.email_domain.text().strip(),
            "email_host_address": self.email_host.text().strip(),
            "email_host_port": int(self.email_port.text().strip() or 25),
            "enable_ssl": bool((email_settings(self._data) or {}).get("enable_ssl")),
            "email_from_enc_pw": str((email_settings(self._data) or {}).get("email_from_enc_pw") or ""),
        }

    def _preview(self) -> dict:
        data = json.loads(json.dumps(self._data))
        notes = data.setdefault("Notifications", {})
        notes["email"] = self._email_block()
        notes["google_chat"] = {"webhook_url": self.google.text().strip()}
        notes["slack"] = {"webhook_url": self.slack.text().strip()}
        notes["microsoft_teams"] = {"webhook_url": self.teams.text().strip()}
        notes["discord"] = {"webhook_url": self.discord.text().strip()}
        data["Viewer"] = {"vtk_image_labeler_3d": self.labeler_path.text().strip()}
        return data

    def _test_email(self):
        try:
            send_test_email(self._preview())
            QMessageBox.information(self, "Test email", "Sent.")
        except Exception as exc:
            QMessageBox.critical(self, "Test email", str(exc))

    def _test_chat(self, channel: str):
        try:
            send_test_chat(channel, self._preview())
            QMessageBox.information(self, "Test chat", "Posted.")
        except Exception as exc:
            QMessageBox.critical(self, "Test chat", str(exc))

    def _save(self):
        data = self._preview()
        save_settings(data, settings_path(writing=True))
        self.accept()
