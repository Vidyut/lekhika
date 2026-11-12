"""BUILD-NOW 8 (AI half): the local-model default per docs/AI.md.

Acceptance: with a small model named, Proofread Selection lands exactly
one tracked change per edit; with no model, the AI menu explains the
setup instead of failing silently. The client itself runs against a
patched complete() — no network in the committed suite, same house rule
as the rest of the tests.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtGui import QTextCursor  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QToolButton  # noqa: E402

from lekhika import ai  # noqa: E402
from lekhika.app import MainWindow, PreferencesDialog  # noqa: E402
from lekhika.project import Project  # noqa: E402

_application = QApplication.instance() or QApplication([])
_application.setOrganizationName("Lekhika")
_application.setApplicationName("Lekhika")
_settings_dir = tempfile.mkdtemp(prefix="lekhika-ai-local-")
QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, _settings_dir)


class _FakeResponse:
    """A canned urlopen result: context manager with .read()."""

    def __init__(self, body):
        self._body = json.dumps(body).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self._body


class PromptTests(unittest.TestCase):
    def test_the_proofread_prompt_bans_more_than_typos_fix(self):
        prompt = ai.PROOFREAD_SYSTEM.lower()

        self.assertIn("minimal edit", prompt)
        self.assertIn("bloopers", prompt)
        self.assertIn("do not polish", prompt)
        self.assertIn("only the corrected text", prompt)

    def test_the_no_model_message_is_the_shortest_setup_path(self):
        self.assertIn("ollama pull", ai.NO_MODEL_MESSAGE)
        self.assertIn("Preferences", ai.NO_MODEL_MESSAGE)
        self.assertIn("http://localhost:11434/v1", ai.NO_MODEL_MESSAGE)
        self.assertIn("error-prone", ai.NO_MODEL_MESSAGE)


class PreferencesNoteTests(unittest.TestCase):
    def test_preferences_carry_the_pull_command_and_the_read_this(self):
        dialog = PreferencesDialog(
            12, {"fonts": True, "paragraphs": True, "inline": True,
                 "alignment": True}, "Author",
            ai.AISettings(endpoint=ai.DEFAULT_ENDPOINT, model=""),
            None, [])

        # The explanation lives behind the "?" (docs/TEMPLATES.md §7) —
        # visible on hover, not filling the page.
        tips = "\n".join(widget.toolTip()
                         for widget in dialog.findChildren(QLabel)
                         + dialog.findChildren(QToolButton))
        self.assertIn("ollama pull", tips)
        self.assertIn("Read this before you rely on it", tips)
        self.assertIn("minimal edits", tips)


class ThinkingModelTests(unittest.TestCase):
    def test_a_reply_that_is_all_thinking_explains_itself(self):
        # Seen against a live Ollama: qwen3.5:2b spent 3,983 tokens on
        # the reasoning field and finished "length" with empty content.
        body = {"choices": [{
            "message": {"role": "assistant", "content": "",
                        "reasoning": "Thinking Process: fix the typos…"},
            "finish_reason": "length",
        }]}
        response = _FakeResponse(body)

        with patch.object(ai.urllib.request, "urlopen",
                          return_value=response):
            with self.assertRaises(ai.AIError) as caught:
                ai.complete("s", "u", ai.AISettings(model="qwen3.5:2b"))

        self.assertIn("thinking", str(caught.exception))
        self.assertIn("phi4-mini", str(caught.exception))

    def test_an_ordinary_answer_passes_through(self):
        body = {"choices": [{
            "message": {"role": "assistant",
                        "content": "  the quick brown fox  "},
            "finish_reason": "stop",
        }]}

        with patch.object(ai.urllib.request, "urlopen",
                          return_value=_FakeResponse(body)):
            text = ai.complete("s", "u", ai.AISettings(model="phi4-mini"))

        self.assertEqual(text, "the quick brown fox")


class LocalModelWindowTests(unittest.TestCase):
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
        QSettings().remove("ai/model")

    def _select(self, start, end):
        cursor = self.window.editor.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.KeepAnchor)
        self.window.editor.setTextCursor(cursor)

    def test_one_selection_is_exactly_one_tracked_change(self):
        # The acceptance case: a small model is named, ...
        QSettings().setValue("ai/model", "qwen3.5:2b")
        self.assertEqual(ai.load_settings().model, "qwen3.5:2b")
        self._select(6, 11)  # "light"

        with patch("lekhika.ai.complete", return_value="Light") as call, \
                patch.object(
                    self.window.editor, "apply_tracked_replacement",
                    wraps=self.window.editor.apply_tracked_replacement,
                ) as apply:
            self.window._ai_proofread_selection()

        # ... one ask carrying the shipped prompt, ...
        self.assertEqual(call.call_count, 1)
        self.assertEqual(call.call_args[0][0], ai.PROOFREAD_SYSTEM)
        self.assertEqual(call.call_args[0][1], "light")
        # ... and exactly one replacement: the tracked pair, never an
        # in-place write, tracking toggle or not.
        self.assertEqual(apply.call_count, 1)
        rows = self.project.revisions(self.scene.id)
        self.assertEqual([row.kind for row in rows], ["delete", "insert"])
        for row in rows:
            self.assertEqual(row.author, "AI")

    def test_without_a_model_proofread_explains_the_setup(self):
        QSettings().setValue("ai/model", "")
        self._select(6, 11)

        with patch.object(QMessageBox, "warning") as warning:
            self.window._ai_proofread_selection()   # real client, no network

        warning.assert_called_once()
        message = warning.call_args.args[2]
        self.assertIn("ollama pull", message)
        self.assertIn("Preferences", message)
        self.assertEqual(self.project.revisions(self.scene.id), [])

    def test_without_a_model_continue_explains_the_setup(self):
        QSettings().setValue("ai/model", "")

        with patch.object(QMessageBox, "warning") as warning:
            self.window._ai_continue_scene()

        warning.assert_called_once()
        self.assertIn("ollama pull", warning.call_args.args[2])
        self.assertEqual(self.project.revisions(self.scene.id), [])

    def test_the_proofread_action_says_bloopers_not_prose(self):
        self.assertIn("Bloopers, not prose",
                      self.window.proofread_action.toolTip())


if __name__ == "__main__":
    unittest.main()
