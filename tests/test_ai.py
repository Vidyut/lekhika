"""2.4 layer 2: the AI client and the two menu actions.

The client runs against a canned loopback HTTP server (urllib only —
no network beyond 127.0.0.1); the window actions run with complete()
patched, proving the answer lands as a tracked change and never as a
direct write, tracking toggle or not.
"""

import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QTextCursor  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QDialog, QMessageBox, QPlainTextEdit, QPushButton,
)

from lekhika import ai  # noqa: E402
from lekhika.app import MainWindow  # noqa: E402
from lekhika.project import Project  # noqa: E402

_application = QApplication.instance() or QApplication([])
_application.setOrganizationName("Lekhika")
_application.setApplicationName("Lekhika")
_settings_dir = tempfile.mkdtemp(prefix="lekhika-ai-settings-")
QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, _settings_dir)


def wait_for_ai(window, timeout=5.0):
    """Spin the event loop until the AI reply lands on the UI thread."""
    deadline = time.monotonic() + timeout
    while window._ai_busy and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.005)
    QApplication.processEvents()


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.server.requests.append({
            "path": self.path,
            "headers": dict(self.headers),
            "body": json.loads(self.rfile.read(length).decode("utf-8")),
        })
        if self.server.force_status is not None:
            self.send_response(self.server.force_status)
            self.end_headers()
            self.wfile.write(b"boom")
            return
        payload = json.dumps({
            "choices": [{"message": {"content": self.server.reply}}],
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


class AIEndpointTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.server.requests = []
        self.server.reply = "canned reply"
        self.server.force_status = None
        threading.Thread(target=self.server.serve_forever,
                         daemon=True).start()
        port = self.server.server_address[1]
        self.settings = ai.AISettings(
            endpoint=f"http://127.0.0.1:{port}/v1",
            model="test-model",
        )

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_complete_returns_the_assistant_message(self):
        text = ai.complete("System prompt", "User prompt", self.settings)

        self.assertEqual(text, "canned reply")
        request = self.server.requests[0]
        self.assertEqual(request["path"], "/v1/chat/completions")
        self.assertEqual(request["body"]["model"], "test-model")
        self.assertEqual(request["body"]["messages"], [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "User prompt"},
        ])
        self.assertFalse(request["body"]["stream"])
        self.assertNotIn("authorization", {
            key.casefold() for key in request["headers"]})

    def test_key_becomes_a_bearer_header(self):
        settings = ai.AISettings(endpoint=self.settings.endpoint,
                                 model="test-model", key="sk-test")

        ai.complete("s", "u", settings)

        headers = self.server.requests[0]["headers"]
        self.assertEqual(headers.get("Authorization"), "Bearer sk-test")

    def test_http_error_becomes_aierror(self):
        self.server.force_status = 500

        with self.assertRaises(ai.AIError) as caught:
            ai.complete("s", "u", self.settings)

        self.assertIn("500", str(caught.exception))

    def test_unreachable_endpoint_becomes_aierror(self):
        settings = ai.AISettings(endpoint="http://127.0.0.1:1/v1",
                                 model="test-model")

        with self.assertRaises(ai.AIError) as caught:
            ai.complete("s", "u", settings)

        self.assertIn("Cannot reach", str(caught.exception))

    def test_only_remote_plain_http_counts_as_in_the_clear(self):
        self.assertTrue(ai._sends_key_in_the_clear("http://example.com/v1"))
        self.assertFalse(ai._sends_key_in_the_clear("http://localhost:11434/v1"))
        self.assertFalse(ai._sends_key_in_the_clear("http://127.0.0.1:1234/v1"))
        self.assertFalse(ai._sends_key_in_the_clear("https://example.com/v1"))

    def test_a_key_is_refused_over_plain_http_to_a_remote_host(self):
        settings = ai.AISettings(endpoint="http://example.com/v1",
                                 model="test-model", key="sk-secret")

        with self.assertRaises(ai.AIError) as caught:
            ai.complete("s", "u", settings)

        self.assertIn("unencrypted", str(caught.exception))
        self.assertIn("example.com", str(caught.exception))
        self.assertEqual(self.server.requests, [])   # refused before sending

    def test_empty_model_refuses_before_any_request(self):
        settings = ai.AISettings(endpoint=self.settings.endpoint, model="")

        with self.assertRaises(ai.AIError) as caught:
            ai.complete("s", "u", settings)

        self.assertIn("Preferences", str(caught.exception))
        self.assertEqual(self.server.requests, [])


class AIWindowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(
            Path(self.temporary.name) / "Novel", "Novel"
        )
        draft = self.project.documents()[0]
        chapter = self.project.add_document("Chapter One", draft.id, "chapter")
        self.scene = self.project.add_document("First Light", chapter.id,
                                               "scene")
        self.project.write_content(self.scene.id,
                                   "<p>First light prose.</p>")
        self.window = MainWindow()
        self.window._switch_project(self.project)
        self.window._select_document(self.scene.id)

    def tearDown(self):
        self.window.project = None
        self.project.close()
        self.temporary.cleanup()

    def _select(self, start, end):
        cursor = self.window.editor.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.KeepAnchor)
        self.window.editor.setTextCursor(cursor)

    def test_continue_scene_inserts_a_tracked_ai_change(self):
        self.assertFalse(self.window.editor.tracking)  # toggle not needed

        with patch("lekhika.ai.complete", return_value=" The wind rose.") as call:
            self.window._ai_continue_scene()
            wait_for_ai(self.window)

        self.assertTrue(call.called)
        self.assertIn("The wind rose.", self.window.editor.toPlainText())
        rows = self.project.revisions(self.scene.id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].kind, "insert")
        self.assertEqual(rows[0].author, "AI")

    def test_proofread_selection_replaces_as_a_tracked_pair(self):
        self._select(6, 11)  # "light"

        with patch("lekhika.ai.complete", return_value="Light"):
            self.window._ai_proofread_selection()
            wait_for_ai(self.window)

        # New words in front, old words struck behind — nothing lost.
        self.assertEqual(self.window.editor.toPlainText(),
                         "First Lightlight prose.")
        rows = self.project.revisions(self.scene.id)
        self.assertEqual([row.kind for row in rows], ["delete", "insert"])
        for row in rows:
            self.assertEqual(row.author, "AI")

    def test_proofread_without_a_selection_proofreads_the_whole_scene(self):
        cursor = self.window.editor.textCursor()
        cursor.clearSelection()
        self.window.editor.setTextCursor(cursor)

        with patch("lekhika.ai.complete",
                   return_value="First Light!") as call:
            self.window._ai_proofread_selection()
            wait_for_ai(self.window)

        call.assert_called_once()
        self.assertEqual(call.call_args[0][1], "First light prose.")
        rows = self.project.revisions(self.scene.id)
        self.assertEqual([row.kind for row in rows], ["delete", "insert"])

    def test_ai_error_shows_a_warning_and_writes_nothing(self):
        with patch("lekhika.ai.complete",
                   side_effect=ai.AIError("no model")), \
                patch.object(QMessageBox, "warning") as warning:
            self.window._ai_continue_scene()
            wait_for_ai(self.window)

        warning.assert_called_once()
        self.assertEqual(self.project.revisions(self.scene.id), [])


class AIThreadTests(unittest.TestCase):
    """The request runs off the UI thread; the answer lands on it.

    A model can take a minute or two, and the app must not sit frozen
    for every one of them — but the editor does not live in a worker,
    so the reply comes back through a signal and is applied here.
    """

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(
            Path(self.temporary.name) / "Novel", "Novel"
        )
        draft = self.project.documents()[0]
        chapter = self.project.add_document("Chapter One", draft.id, "chapter")
        self.scene = self.project.add_document("First Light", chapter.id,
                                               "scene")
        self.other = chapter
        self.project.write_content(self.scene.id,
                                   "<p>First light prose.</p>")
        self.window = MainWindow()
        self.window._switch_project(self.project)
        self.window._select_document(self.scene.id)

    def tearDown(self):
        self.window.project = None
        self.project.close()
        self.temporary.cleanup()

    def _slow(self, reply="Answer."):
        """A complete() that blocks until the test lets it through."""
        release = threading.Event()
        started = threading.Event()

        def slow(system, user):
            started.set()
            release.wait(5)
            return reply

        return slow, release, started

    def test_the_request_runs_off_the_ui_thread(self):
        seen = {}

        def fake(system, user):
            seen["request"] = threading.current_thread()
            return "Answer."

        with patch("lekhika.ai.complete", side_effect=fake):
            self.window._ai_continue_scene()
            wait_for_ai(self.window)

        self.assertIsNot(seen["request"], threading.main_thread())
        self.assertIn("Answer.", self.window.editor.toPlainText())

    def test_the_answer_is_applied_on_the_ui_thread(self):
        seen = {}
        real = self.window.editor.apply_tracked_replacement

        def spy(*args, **kwargs):
            seen["applied"] = threading.current_thread()
            return real(*args, **kwargs)

        with patch("lekhika.ai.complete", return_value="Answer."), \
                patch.object(self.window.editor,
                             "apply_tracked_replacement", side_effect=spy):
            self.window._ai_continue_scene()
            wait_for_ai(self.window)

        self.assertEqual(seen["applied"], threading.main_thread())

    def test_the_app_stays_responsive_while_the_request_runs(self):
        slow, release, started = self._slow()
        ran = []

        with patch("lekhika.ai.complete", side_effect=slow):
            self.window._ai_continue_scene()
            self.assertTrue(started.wait(5))
            QTimer.singleShot(0, lambda: ran.append(True))
            QApplication.processEvents()
            self.assertEqual(ran, [True])      # the UI thread was not blocked
            release.set()
            wait_for_ai(self.window)

        self.assertIn("Answer.", self.window.editor.toPlainText())

    def test_a_second_request_while_one_is_running_is_refused(self):
        slow, release, started = self._slow()

        with patch("lekhika.ai.complete", side_effect=slow) as call:
            self.window._ai_continue_scene()
            self.assertTrue(started.wait(5))
            self.window._ai_continue_scene()
            release.set()
            wait_for_ai(self.window)

        self.assertEqual(call.call_count, 1)

    def test_an_answer_for_a_scene_the_writer_left_goes_to_the_clipboard(self):
        slow, release, started = self._slow()

        with patch("lekhika.ai.complete", side_effect=slow):
            self.window._ai_continue_scene()
            self.assertTrue(started.wait(5))
            self.window._select_document(self.other.id)
            release.set()
            wait_for_ai(self.window)

        self.assertEqual(self.project.revisions(self.scene.id), [])
        self.assertEqual(QApplication.clipboard().text(), "Answer.")

    def test_a_proofread_answer_is_not_dropped_into_changed_text(self):
        slow, release, started = self._slow()
        cursor = self.window.editor.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.window.editor.setTextCursor(cursor)

        with patch("lekhika.ai.complete", side_effect=slow):
            self.window._ai_proofread_selection()
            self.assertTrue(started.wait(5))
            self.window.editor.insertPlainText(" More prose.")
            release.set()
            wait_for_ai(self.window)

        self.assertEqual(self.project.revisions(self.scene.id), [])
        self.assertEqual(QApplication.clipboard().text(), "Answer.")

    def test_the_ask_popup_asks_and_inserts(self):
        class _NoExecDialog(QDialog):
            """The popup without the modal wait — the test drives it."""

            def exec(self):
                return 0

        with patch("lekhika.app.QDialog", _NoExecDialog), \
                patch("lekhika.ai.complete", return_value="An answer."):
            self.window._ai_prompt()
            popup = self.window._ai_popup
            areas = popup.findChildren(QPlainTextEdit)
            buttons = {button.text(): button
                       for button in popup.findChildren(QPushButton)}
            areas[0].setPlainText("What happens next?")
            buttons["Ask"].click()
            wait_for_ai(self.window)

            self.assertEqual(areas[1].toPlainText(), "An answer.")
            buttons["Insert at caret"].click()

        self.assertIn("An answer.", self.window.editor.toPlainText())
        rows = self.project.revisions(self.scene.id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].author, "AI")


class ServerListTests(unittest.TestCase):
    """save_servers and load_servers must agree on one stored shape —
    a mismatch silently wiped every saved server on the next load."""

    def tearDown(self):
        QSettings().remove("ai/servers")

    def test_saved_servers_round_trip_as_dicts(self):
        ai.save_servers([("Hosted", "https://api.example.com/v1")])
        raw = json.loads(str(QSettings().value("ai/servers")))
        self.assertEqual(raw, [{"name": "Hosted",
                                "endpoint": "https://api.example.com/v1"}])
        self.assertEqual(
            ai.load_servers(),
            [("Ollama (local)", ai.DEFAULT_ENDPOINT),
             ("Hosted", "https://api.example.com/v1")])

    def test_servers_saved_by_an_older_build_still_load(self):
        QSettings().setValue(
            "ai/servers", json.dumps([["Hosted",
                                       "https://api.example.com/v1"]]))
        self.assertEqual(
            ai.load_servers(),
            [("Ollama (local)", ai.DEFAULT_ENDPOINT),
             ("Hosted", "https://api.example.com/v1")])


class EndpointGuardTests(unittest.TestCase):
    """Only http(s) endpoints reach the network, key or no key."""

    def test_a_non_http_endpoint_is_refused_before_any_request(self):
        settings = ai.AISettings(endpoint="file:///etc/passwd", model="m")
        with self.assertRaises(ai.AIError):
            ai.complete("system", "user", settings)

    def test_detect_remote_models_ignores_a_non_http_endpoint(self):
        self.assertEqual(ai.detect_remote_models("ftp://example.com"), [])


if __name__ == "__main__":
    unittest.main()
