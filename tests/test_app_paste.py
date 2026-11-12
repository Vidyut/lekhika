"""1.10 paste sanitizer: policy scrubbing, blank-line collapse, editor glue.

QSettings is redirected into a temp dir (same contract as the resume
tests) so the suite never touches the user's real Lekhika configuration.
"""

import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMimeData, QSettings, Qt  # noqa: E402
from PySide6.QtGui import QAction, QTextCursor, QTextDocument  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from lekhika.app import (  # noqa: E402
    LinkEditor, MainWindow, PreferencesDialog, load_paste_prefs,
    sanitize_pasted_html,
)

_application = QApplication.instance() or QApplication([])
_application.setOrganizationName("Lekhika")
_application.setApplicationName("Lekhika")
_settings_dir = tempfile.mkdtemp(prefix="lekhika-paste-settings-")
# QSettings objects created from now on read/write under _settings_dir.
QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, _settings_dir)

_PASTE_KEYS = ("paste/fonts", "paste/paragraphs", "paste/inline",
               "paste/alignment")
POLICY = dict(fonts=False, paragraphs=False, inline=True, alignment=True)


def fragments(document):
    """Every QTextFragment in the document, in order."""
    result = []
    block = document.firstBlock()
    while block.isValid():
        iterator = block.begin()
        while not iterator.atEnd():
            result.append(iterator.fragment())
            iterator += 1
        block = block.next()
    return result


def _clear_paste_keys():
    for key in _PASTE_KEYS:
        QSettings().remove(key)


def _html_mime(html):
    mime = QMimeData()
    mime.setHtml(html)
    return mime


class PastePrefsTests(unittest.TestCase):
    def setUp(self):
        _clear_paste_keys()

    def tearDown(self):
        _clear_paste_keys()

    def test_defaults_keep_emphasis_and_alignment_drop_the_rest(self):
        self.assertEqual(load_paste_prefs(), {
            "fonts": False,
            "paragraphs": False,
            "inline": True,
            "alignment": True,
        })

    def test_reads_what_preferences_wrote(self):
        settings = QSettings()
        settings.setValue("paste/fonts", True)
        settings.setValue("paste/inline", False)

        prefs = load_paste_prefs()

        self.assertTrue(prefs["fonts"])
        self.assertFalse(prefs["inline"])
        self.assertFalse(prefs["paragraphs"])
        self.assertTrue(prefs["alignment"])


class SanitizerTests(unittest.TestCase):
    def _sanitize(self, html, **overrides):
        return sanitize_pasted_html(html, **{**POLICY, **overrides})

    def test_emphasis_survives_and_font_is_dropped(self):
        document = self._sanitize(
            '<p><b style="font-family:Courier;font-size:30pt">hi</b> '
            "<i>there</i> <u>you</u></p>"
        )

        formats = [fragment.charFormat() for fragment in fragments(document)]
        self.assertTrue(any(fmt.fontWeight() > 400 for fmt in formats))
        self.assertTrue(any(fmt.fontItalic() for fmt in formats))
        self.assertTrue(any(fmt.fontUnderline() for fmt in formats))
        for fmt in formats:
            self.assertEqual(fmt.fontPointSize(), 0.0)
            self.assertFalse(fmt.fontFamilies())

    def test_emphasis_stripped_when_inline_disabled(self):
        document = self._sanitize("<p><b>hi</b> <i>there</i></p>",
                                  inline=False)

        for fragment in fragments(document):
            fmt = fragment.charFormat()
            self.assertFalse(fmt.fontWeight() > 400)
            self.assertFalse(fmt.fontItalic())

    def test_strikethrough_never_survives(self):
        raw = QTextDocument()
        raw.setHtml("<p><s>x</s></p>")
        # The parser must understand <s>, or the assertion below is vacuous.
        self.assertTrue(
            any(f.charFormat().fontStrikeOut() for f in fragments(raw))
        )

        document = self._sanitize("<p><s>x</s></p>")

        for fragment in fragments(document):
            self.assertFalse(fragment.charFormat().fontStrikeOut())

    def test_font_family_size_and_colour_kept_when_asked(self):
        document = self._sanitize(
            '<p style="font-family:Courier;font-size:30pt;color:#ff0000">x</p>',
            fonts=True,
        )

        fmt = fragments(document)[0].charFormat()
        self.assertIn("Courier", [str(name) for name in fmt.fontFamilies()])
        self.assertEqual(fmt.fontPointSize(), 30.0)
        self.assertNotEqual(fmt.foreground().style(), Qt.BrushStyle.NoBrush)

    def test_alignment_survives_by_default(self):
        document = self._sanitize('<p style="text-align:center">c</p>')

        self.assertEqual(
            int(document.firstBlock().blockFormat().alignment()),
            int(Qt.AlignmentFlag.AlignCenter),
        )

    def test_alignment_left_alone_when_disabled(self):
        document = self._sanitize('<p style="text-align:right">r</p>',
                                  alignment=False)

        self.assertEqual(
            int(document.firstBlock().blockFormat().alignment()),
            int(Qt.AlignmentFlag.AlignLeft),
        )

    def test_paragraph_margins_stripped_by_default(self):
        # Qt stamps every parsed <p> with 12 pt top/bottom margins of its
        # own; pasting a chapter of them must not double the spacing.
        document = self._sanitize("<p>m</p>")

        block_format = document.firstBlock().blockFormat()
        self.assertEqual(block_format.topMargin(), 0.0)
        self.assertEqual(block_format.bottomMargin(), 0.0)

    def test_paragraph_margins_kept_when_asked(self):
        document = self._sanitize("<p>m</p>", paragraphs=True)

        block_format = document.firstBlock().blockFormat()
        self.assertEqual(block_format.topMargin(), 12.0)
        self.assertEqual(block_format.bottomMargin(), 12.0)

    def test_line_height_stripped_by_default(self):
        document = self._sanitize('<p style="line-height:200%">x</p>')

        self.assertEqual(document.firstBlock().blockFormat().lineHeight(), 0.0)

    def test_line_height_kept_when_asked(self):
        document = self._sanitize('<p style="line-height:200%">x</p>',
                                  paragraphs=True)

        self.assertEqual(document.firstBlock().blockFormat().lineHeight(), 200.0)

    def test_double_blank_run_collapses_to_one(self):
        document = self._sanitize(
            "<p>A</p><p><br></p><p><br></p><p>B</p>"
        )

        # One blank line stays between A and B; the extra one was noise.
        self.assertEqual(document.toPlainText(), "A\n\n\nB")

    def test_interior_run_keeps_exactly_one_blank_line(self):
        document = self._sanitize(
            "<p>A</p><p><br></p><p><br></p><p><br></p><p>B</p>"
        )

        self.assertEqual(document.toPlainText(), "A\n\n\nB")

    def test_leading_and_trailing_blanks_removed_entirely(self):
        document = self._sanitize(
            "<p><br></p><p><br></p><p>A</p><p><br></p><p><br></p>"
        )

        self.assertEqual(document.blockCount(), 1)
        self.assertEqual(document.toPlainText(), "A")

    def test_paragraphs_kept_leaves_blank_structure_alone(self):
        document = self._sanitize(
            "<p>A</p><p><br></p><p><br></p><p>B</p>", paragraphs=True
        )

        # Four block separators plus two line separators: nothing collapsed.
        self.assertEqual(document.toPlainText(), "A\n\n\n\n\nB")

    def test_all_blank_document_left_whole(self):
        document = self._sanitize("<p><br></p><p><br></p>")

        self.assertEqual(document.blockCount(), 2)

    def test_image_only_block_is_not_blank(self):
        html = (
            '<p><img src="data:image/png;base64,'
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z"
            '8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="/></p><p>B</p>'
        )

        document = self._sanitize(html)

        self.assertEqual(document.blockCount(), 2)
        self.assertIn("￼", document.firstBlock().text())

    def test_anchor_survives_every_policy(self):
        document = self._sanitize(
            '<p><a href="https://example.com/x">link</a></p>'
        )

        fmt = fragments(document)[0].charFormat()
        self.assertTrue(fmt.isAnchor())
        self.assertEqual(fmt.anchorHref(), "https://example.com/x")

    def test_junk_html_does_not_raise(self):
        document = self._sanitize("<p>unterminated <b>bold")

        self.assertIn("unterminated", document.toPlainText())
        self.assertIn("bold", document.toPlainText())


class LinkEditorPasteTests(unittest.TestCase):
    def setUp(self):
        _clear_paste_keys()
        self.editor = LinkEditor()

    def tearDown(self):
        _clear_paste_keys()

    def test_paste_goes_through_the_policy(self):
        self.editor.insertFromMimeData(_html_mime(
            '<p style="font-family:Courier;font-size:30pt;'
            'text-align:center"><b>hi</b></p>'
        ))

        self.assertIn("hi", self.editor.toPlainText())
        formats = [f.charFormat() for f in fragments(self.editor.document())]
        self.assertTrue(any(fmt.fontWeight() > 400 for fmt in formats))
        for fmt in formats:
            self.assertFalse(fmt.fontFamilies())
            self.assertEqual(fmt.fontPointSize(), 0.0)
        # Alignment is kept by default.
        self.assertEqual(
            int(self.editor.document().firstBlock().blockFormat().alignment()),
            int(Qt.AlignmentFlag.AlignCenter),
        )

    def test_caret_moves_after_the_paste(self):
        self.editor.insertFromMimeData(_html_mime("<p>one two</p>"))

        self.assertGreater(self.editor.textCursor().position(), 0)

    def test_paste_replaces_the_selection(self):
        self.editor.setPlainText("hello world")
        cursor = self.editor.textCursor()
        cursor.select(QTextCursor.SelectionType.Document)
        self.editor.setTextCursor(cursor)

        self.editor.insertFromMimeData(_html_mime("<p><b>bye</b></p>"))

        self.assertEqual(self.editor.toPlainText().strip(), "bye")

    def test_plain_text_falls_through_to_qt_untouched(self):
        mime = QMimeData()
        mime.setText("plain text\nsecond line")

        self.editor.insertFromMimeData(mime)

        self.assertEqual(self.editor.toPlainText(), "plain text\nsecond line")

    def test_preference_can_disable_inline_formatting(self):
        QSettings().setValue("paste/inline", False)

        self.editor.insertFromMimeData(_html_mime("<p><b>hi</b></p>"))

        for fragment in fragments(self.editor.document()):
            self.assertFalse(fragment.charFormat().fontWeight() > 400)

    def test_preference_can_keep_fonts(self):
        QSettings().setValue("paste/fonts", True)

        self.editor.insertFromMimeData(
            _html_mime('<p style="font-family:Courier">hi</p>')
        )

        fmt = fragments(self.editor.document())[0].charFormat()
        self.assertIn("Courier", [str(name) for name in fmt.fontFamilies()])

    def test_plain_text_paste_takes_only_the_words(self):
        QApplication.clipboard().setMimeData(_html_mime(
            '<p style="font-family:Courier;font-size:30pt"><b>hi</b></p>'
        ))

        self.editor.paste_as_plain_text()

        self.assertEqual(self.editor.toPlainText().strip(), "hi")
        for fragment in fragments(self.editor.document()):
            self.assertFalse(fragment.charFormat().fontWeight() > 400)
            self.assertFalse(fragment.charFormat().fontFamilies())

    def test_keep_all_formatting_keeps_everything(self):
        QApplication.clipboard().setMimeData(_html_mime(
            '<p style="font-family:Courier;font-size:30pt"><b>hi</b></p>'
        ))

        self.editor.paste_keep_all()

        formats = [f.charFormat() for f in fragments(self.editor.document())]
        self.assertTrue(any(fmt.fontWeight() > 400 for fmt in formats))
        self.assertTrue(any(fmt.fontPointSize() == 30.0 for fmt in formats))
        self.assertTrue(any(
            "Courier" in [str(name) for name in (fmt.fontFamilies() or [])]
            for fmt in formats
        ))

    def test_keep_all_without_html_pastes_normally(self):
        QApplication.clipboard().setText("plain from clipboard")

        self.editor.paste_keep_all()

        self.assertEqual(self.editor.toPlainText(), "plain from clipboard")


class PreferencesDialogTests(unittest.TestCase):
    def tearDown(self):
        _clear_paste_keys()

    def test_dialog_reads_the_paste_policy(self):
        _clear_paste_keys()
        QSettings().setValue("paste/fonts", True)

        dialog = PreferencesDialog(11, load_paste_prefs())

        self.assertEqual(dialog.size_slider.value(), 11)
        self.assertTrue(dialog.paste_fonts.isChecked())
        self.assertTrue(dialog.paste_inline.isChecked())
        self.assertTrue(dialog.paste_alignment.isChecked())
        self.assertFalse(dialog.paste_paragraphs.isChecked())


class EditMenuTests(unittest.TestCase):
    def setUp(self):
        self.window = MainWindow()

    def tearDown(self):
        # Never close() the window; dropping the reference is enough.
        self.window.project = None

    def test_paste_actions_exist_with_shortcuts(self):
        self.window = MainWindow()
        actions = {action.text(): action
                   for action in self.window.findChildren(QAction)}

        self.assertIn("Paste", actions)
        self.assertEqual(actions["Paste"].shortcut().toString(), "Ctrl+V")
        self.assertEqual(
            actions["Paste as Plain Text"].shortcut().toString(), "Ctrl+Shift+V"
        )
        self.assertIn("Paste and Keep All Formatting", actions)
        self.assertEqual(
            actions["Paste and Keep All Formatting"].shortcut().toString(), ""
        )


def tearDownModule():
    """Hand the clipboard back empty before the interpreter goes away.

    PySide6 6.12 frees a Python-owned QMimeData that is still on the
    clipboard a second time during shutdown, so a suite whose every test
    passed still exits 139 — which any CI that trusts the exit code reads
    as a failure. Dropping it here, while Qt is still alive, is the fix.
    """
    QApplication.clipboard().clear()


if __name__ == "__main__":
    unittest.main()
