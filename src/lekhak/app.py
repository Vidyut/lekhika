"""The Qt application: one window over one Project.

Everything user-visible lives here. The window owns a Project and a set of
views onto it; the Project owns storage. Nothing in this module writes
files or SQL directly — it calls into Project, which is where the storage
contract is enforced.

Layout of the window:

    left   binder tree (QTreeWidget, drag to reorder)
    center tabbed views: editor / corkboard / outliner
    right  inspector: synopsis, word target, book/document templates, metadata

Three rules that govern how views and Project interact, and where the
tricky parts of this file come from:

- Saving is debounced. The editor's textChanged starts a timer; when it
  fires, save_current() writes through Project. The binder, outliner and
  corkboard are refreshed from Project after any change, never updated in
  place — Project is the single source of truth.
- Display must never leak into storage. Binder numbers, resolved wiki
  links and interface fonts are all rendered on the way *out* (on load,
  on paint) and stripped on the way back in (canonicalize_links() before
  write_content()). Anything that reads a widget's text back into the
  project has to undo the display layer first.
- The editor font is pinned, not inherited. Interface font preferences
  change QApplication's font; the writing editor keeps an explicit 12 pt
  so preference changes never round-trip into document HTML.

Dialogs at the top of the file are self-contained: each gathers input and
returns it, with no Project access. MainWindow methods that open them
apply the result.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from html import escape
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import (
    QDate, QEvent, QItemSelectionModel, QLocale, QMimeData, QSettings, QSize,
    Qt, QTimer, QTime, QUrl, Signal,
)
from PySide6.QtGui import (
    QAction, QBrush, QColor, QDesktopServices, QFont, QFontDatabase,
    QKeySequence,
    QTextBlockFormat,
    QTextCharFormat, QTextCursor, QTextDocument, QTextDocumentFragment,
    QTextFormat, QTextListFormat, QTextOption, QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox,
    QDateEdit, QDialog,
    QDialogButtonBox, QDoubleSpinBox,
    QFileDialog, QFontComboBox, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout,
    QHeaderView,
    QFrame, QInputDialog, QLabel,
    QLineEdit, QMenu,
    QMainWindow, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit,
    QPushButton, QRadioButton, QSizePolicy, QSlider, QSpinBox, QSplitter, QStatusBar,
    QStyle, QTabWidget,
    QTableWidget, QTableWidgetItem, QTextEdit, QTextBrowser, QToolBar,
    QStyledItemDelegate,
    QToolButton, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from . import ai
from . import pdf
from . import tts
from . import spellcheck
from . import revisions
from .flowlayout import FlowLayout
from .project import (
    AMAZON_TRIMS, DEFAULT_PAGE_SETUP, EXPORT_KINDS,
    INSIDE_BACKUPS_PROBLEM,
    KDP_MARGIN_BANDS, KDP_MARGIN_MIN,
    PAGE_TRIMS, TEMPLATE_CONFIG_KEY, TEMPLATE_DOC_PREFIX,
    TEMPLATE_FILE_PREFIX, TEMPLATE_KEY, DeleteError, Project,
    NO_RUNNING_HEAD, clean_page_setup, config_headings, config_length_list,
    config_page_size, config_points, reading_time, trim_id_for,
)
from .preferences import (
    LINK_POLICY_CHOICES, PreferencesDialog, apply_update, newer_release,
    update_check_due, update_mode)
from .pageview import PageView
from .templates import (
    BUILTIN_PLACEHOLDERS, PLACEHOLDER_RE, TEMPLATE_CONFIG_NOTE,
    TEMPLATE_NAMING_NOTE,
    TemplateSet, _shipped_text, copy_default_set_to, copy_template_file, copy_template_set,
    discover_template_sets, document_template_stem, drop_config_line,
    escape_placeholders, file_paths, is_template_fragment, join_head,
    named_template_sets, parse_docx_config, project_template_root,
    set_template_flag, split_head, unescape_placeholders, validate_slot,
    write_config_line,
)


def _destructive_box(parent, title: str, question: str, *, confirm: str,
                     checkbox: str, detail: str = "",
                     gate: str = "I understand what will be lost") -> QDialog:
    """The confirmation for anything that deletes or overwrites content.

    A Yes/No dialog is clicked through on reflex, so the destructive button
    starts disabled and only the checkbox unarms it; Cancel is the default
    button, so Enter (and Escape) are safe. The question names the target,
    and the checkbox says what is about to be lost — destruction is confirmed,
    and never by name alone (ROADMAP working agreements).

    Built as an explicit dialog, not QMessageBox.setCheckBox, whose
    checkbox rendering the writer could not reach (buttons without a gate
    that can be armed are a confirmation that only looks like one).
    """
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    layout = QVBoxLayout(dialog)
    warn = QHBoxLayout()
    icon = QLabel("!")
    icon.setAlignment(Qt.AlignmentFlag.AlignTop)
    icon.setStyleSheet("font-size: 28pt; color: #e74c3c;")
    warn.addWidget(icon)
    text = QVBoxLayout()
    text.addWidget(QLabel(question))
    if detail:
        body = QPlainTextEdit()
        body.setReadOnly(True)
        body.setPlainText(detail)
        body.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        body.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        body.setMaximumHeight(120)
        text.addWidget(body)
    text.addWidget(QLabel(checkbox))
    warn.addLayout(text)
    layout.addLayout(warn)
    gate_box = QCheckBox(gate)
    layout.addWidget(gate_box)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
    destructive_button = QPushButton(confirm)
    destructive_button.setEnabled(False)
    gate_box.toggled.connect(destructive_button.setEnabled)
    buttons_row = QHBoxLayout()
    buttons_row.addStretch(1)
    buttons_row.addWidget(destructive_button)
    buttons_row.addWidget(buttons.button(QDialogButtonBox.StandardButton.Cancel))
    layout.addLayout(buttons_row)
    destructive_button.clicked.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    return dialog


def confirm_destructive(parent, title: str, question: str, *, confirm: str,
                        checkbox: str, detail: str = "",
                        gate: str = "I understand what will be lost") -> bool:
    """True only when the writer ticked the box *and* pressed the button."""
    dialog = _destructive_box(parent, title, question, confirm=confirm,
                               checkbox=checkbox, detail=detail, gate=gate)
    dialog.exec()
    return dialog.result() == dialog.DialogCode.Accepted


def _desktop_opener(path: Path) -> list[str]:
    """The command that hands a file to the desktop's own handler.

    ``xdg-open`` first (it waits for the viewer it starts on most
    desktops); ``gio open`` is GNOME's equivalent. macOS ``open`` and
    Windows ``cmd /c start`` are the equivalents there — ``print_via_desktop``
    appends the PDF path as the final argument.
    """
    if sys.platform == "win32":
        return ["cmd", "/c", "start", ""]
    if sys.platform == "darwin":
        found = shutil.which("open")
        if found:
            return [found]
    found = shutil.which("xdg-open")
    if found:
        return [found]
    found = shutil.which("gio")
    if found:
        return [found, "open"]
    raise RuntimeError(
        "No desktop handler found (install xdg-utils, or gio)."
    )


def print_via_desktop(path: Path, command: list[str] | None = None):
    """Open the PDF in the system print flow, then remove the temp folder.

    The handler *is* the print flow — the PDF viewer's own print dialog —
    and it must be free to read the file for as long as it likes, so the
    folder dies when the handler's process exits and not a moment before.
    The waiter runs on a daemon thread: quitting Lekhak never leaves the
    writer waiting on a viewer to close.
    """
    folder = path.parent
    if command is None:
        command = _desktop_opener(path)
    try:
        process = subprocess.Popen(command + [str(path)])
    except BaseException:
        # The handler never started: nothing can read the file now.
        shutil.rmtree(folder, ignore_errors=True)
        raise

    def _clean_when_done() -> None:
        try:
            process.wait()
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    threading.Thread(target=_clean_when_done, daemon=True).start()
    return process


class _SynopsisDelegate(QStyledItemDelegate):
    """Edit one thing on a corkboard card: its synopsis.

    A card shows three things — the title, the synopsis and the count —
    and only the synopsis is editable here: the title is the document's,
    and the count is the document's too. The editor is the card's own
    rectangle, so the typing happens where the card is.
    """

    edited = Signal(str, str)            # document id, the new synopsis

    def createEditor(self, parent, option, index):
        editor = QPlainTextEdit(parent)
        editor.setFrameShape(QFrame.Shape.NoFrame)
        # The card's own colour, so the editor does not flash white in a
        # dark theme.
        editor.setStyleSheet("background: transparent;")
        return editor

    def setEditorData(self, editor, index):
        editor.setPlainText(index.data(Qt.UserRole + 1) or "")

    def setModelData(self, editor, model, index):
        text = editor.toPlainText()
        model.setData(index, text, Qt.UserRole + 1)
        document_id = index.data(Qt.UserRole)
        if document_id:
            self.edited.emit(document_id, text)

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect)

    def eventFilter(self, editor, event):
        if (event.type() == QEvent.Type.KeyPress
                and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)):
            # A synopsis is a paragraph: Enter makes a new line, and the
            # editor closes when the writer clicks away. Letting the
            # default delegate have the key would commit on every line.
            return False
        return super().eventFilter(editor, event)


class FindReplaceDialog(QDialog):
    def __init__(self, editor: QTextEdit, parent=None) -> None:
        super().__init__(parent)
        self.editor = editor
        self.setWindowTitle("Find and Replace")
        self.find_text = QLineEdit()
        self.replace_text = QLineEdit()
        form = QFormLayout(self)
        form.addRow("Find:", self.find_text)
        form.addRow("Replace with:", self.replace_text)
        controls = QHBoxLayout()
        find_next = QPushButton("Find Next")
        replace = QPushButton("Replace")
        replace_all = QPushButton("Replace All")
        controls.addWidget(find_next)
        controls.addWidget(replace)
        controls.addWidget(replace_all)
        form.addRow(controls)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.close)
        form.addRow(buttons)
        find_next.clicked.connect(self.find_next)
        replace.clicked.connect(self.replace_one)
        replace_all.clicked.connect(self.replace_everywhere)
        self.find_text.returnPressed.connect(self.find_next)

    def find_next(self) -> bool:
        needle = self.find_text.text()
        if not needle:
            return False
        if self.editor.find(needle):
            return True
        cursor = self.editor.textCursor()
        cursor.movePosition(QTextCursor.Start)
        self.editor.setTextCursor(cursor)
        return self.editor.find(needle)

    def replace_one(self) -> None:
        cursor = self.editor.textCursor()
        if cursor.hasSelection() and cursor.selectedText() == self.find_text.text():
            cursor.insertText(self.replace_text.text())
        self.find_next()

    def replace_everywhere(self) -> None:
        needle = self.find_text.text()
        if not needle:
            return
        cursor = self.editor.textCursor()
        cursor.beginEditBlock()
        cursor.movePosition(QTextCursor.Start)
        self.editor.setTextCursor(cursor)
        count = 0
        while self.editor.find(needle):
            self.editor.textCursor().insertText(self.replace_text.text())
            count += 1
        cursor.endEditBlock()
        QMessageBox.information(self, "Replace All", f"Replaced {count} occurrence(s).")


class QuickOpenDialog(QDialog):
    """BUILD-NOW 2: one field over the binder's titles — navigate only.

    Substring matches come first, subsequence matches behind them, both in
    binder order, so the obvious title is the highlight the moment you
    type it. Enter jumps, Esc closes, and there is no editing surface
    anywhere in the dialog: it cannot touch the manuscript.
    """

    def __init__(self, entries: list[tuple[str, str]], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Quick Open")
        self.chosen_id: str | None = None
        self._entries = entries
        layout = QVBoxLayout(self)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("Title — Enter to jump, Esc to close")
        self.edit.setClearButtonEnabled(True)
        self.edit.installEventFilter(self)
        self.results = QListWidget()
        self.results.setAlternatingRowColors(True)
        layout.addWidget(self.edit)
        layout.addWidget(self.results)
        self.edit.textChanged.connect(self._refill)
        self.edit.returnPressed.connect(self._accept_current)
        self.results.itemActivated.connect(self._activate)
        self._refill("")
        self.resize(520, 380)

    @staticmethod
    def _matches(entries: list[tuple[str, str]], text: str) -> list[tuple[str, str]]:
        needle = text.strip().lower()
        if not needle:
            return list(entries)
        substrings = [e for e in entries if needle in e[1].lower()]
        placed = {e[0] for e in substrings}
        fuzzy = [
            e for e in entries
            if e[0] not in placed
            and QuickOpenDialog._subsequence(needle, e[1].lower())
        ]
        return substrings + fuzzy

    @staticmethod
    def _subsequence(needle: str, haystack: str) -> bool:
        """Every needle character in order, gaps allowed — a typo-tolerant
        fallback that can never outrank a real substring hit."""
        remaining = iter(haystack)
        return all(char in remaining for char in needle)

    def _refill(self, text: str) -> None:
        self.results.clear()
        for document_id, title in self._matches(self._entries, text):
            item = QListWidgetItem(title)
            item.setData(Qt.UserRole, document_id)
            self.results.addItem(item)
        if self.results.count():
            self.results.setCurrentRow(0)

    def _accept_current(self) -> None:
        item = self.results.currentItem()
        if item is not None:
            self._activate(item)

    def _activate(self, item: QListWidgetItem) -> None:
        self.chosen_id = item.data(Qt.UserRole)
        self.accept()

    def eventFilter(self, obj, event):
        # Up/Down from the text field walk the list — the caret has no
        # purpose there, and reaching for the mouse would defeat the point.
        if obj is self.edit and event.type() == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                step = 1 if event.key() == Qt.Key.Key_Down else -1
                last = self.results.count() - 1
                if last >= 0:
                    row = self.results.currentRow()
                    self.results.setCurrentRow(max(0, min(last, row + step)))
                return True
        return super().eventFilter(obj, event)


class ProjectReplaceDialog(QDialog):
    def __init__(self, project: Project, on_goto=None, parent=None) -> None:
        super().__init__(parent)
        self.project = project
        self.on_goto = on_goto
        self.setWindowTitle("Replace in Project")
        self.resize(640, 480)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.find_text = QLineEdit()
        self.replace_text = QLineEdit()
        form.addRow("Find:", self.find_text)
        form.addRow("Replace with:", self.replace_text)
        layout.addLayout(form)
        options = QHBoxLayout()
        self.regex_box = QCheckBox("Regular expressions")
        self.case_box = QCheckBox("Ignore case")
        self.case_box.setChecked(True)
        self.titles_box = QCheckBox("Titles")
        self.titles_box.setChecked(True)
        self.synopses_box = QCheckBox("Synopses")
        self.synopses_box.setChecked(True)
        for box in (self.regex_box, self.case_box, self.titles_box, self.synopses_box):
            options.addWidget(box)
        options.addStretch(1)
        layout.addLayout(options)
        self.preview = QListWidget()
        layout.addWidget(self.preview, 1)
        self.count_label = QLabel("")
        layout.addWidget(self.count_label)
        buttons = QDialogButtonBox()
        self.find_button = buttons.addButton("Find", QDialogButtonBox.ActionRole)
        self.replace_button = buttons.addButton("Replace All", QDialogButtonBox.ActionRole)
        buttons.addButton(QDialogButtonBox.Close)
        layout.addWidget(buttons)
        self.find_button.clicked.connect(self.refresh_preview)
        self.replace_button.clicked.connect(self.replace_all)
        buttons.rejected.connect(self.close)
        self.find_text.returnPressed.connect(self.refresh_preview)
        self.preview.itemDoubleClicked.connect(self._goto_hit)
        for box in (self.regex_box, self.case_box, self.titles_box, self.synopses_box):
            box.stateChanged.connect(self.refresh_preview)

    def _goto_hit(self, item: QListWidgetItem) -> None:
        if self.on_goto:
            self.on_goto(item.data(Qt.UserRole))

    def _fields(self) -> set[str]:
        fields = {"content"}
        if self.titles_box.isChecked():
            fields.add("title")
        if self.synopses_box.isChecked():
            fields.add("synopsis")
        return fields

    def refresh_preview(self) -> None:
        self.preview.clear()
        self.count_label.setText("")
        query = self.find_text.text()
        if not query:
            return
        try:
            hits = self.project.search_snippets(
                query, regex=self.regex_box.isChecked(),
                ignore_case=self.case_box.isChecked(), fields=self._fields(),
            )
        except re.error as error:
            self.count_label.setText(f"Invalid pattern: {error}")
            return
        for document, field, snippet in hits:
            item = QListWidgetItem(f"{document.title} · {field}: {snippet}")
            item.setData(Qt.UserRole, document.id)
            self.preview.addItem(item)
        self.count_label.setText(f"{len(hits)} match(es) (preview capped at 500)")

    def replace_all(self) -> None:
        query = self.find_text.text()
        if not query:
            return
        try:
            self.project.search_snippets(query, regex=self.regex_box.isChecked(),
                                         ignore_case=self.case_box.isChecked())
        except re.error as error:
            QMessageBox.warning(self, "Replace in Project", f"Invalid pattern: {error}")
            return
        if not confirm_destructive(
            self, "Replace in project",
            "Replace every match in the whole project?",
            confirm="Replace all",
            checkbox=(f"{self.preview.count()} matching line(s) in the preview; "
                      "every stored document that matches is rewritten on disk. "
                      "Undo does not reach project-wide replaces — a snapshot "
                      "of each changed document is taken first, which is the "
                      "way back."),
        ):
            return
        counts = self.project.replace(
            query, self.replace_text.text(), regex=self.regex_box.isChecked(),
            ignore_case=self.case_box.isChecked(),
            in_titles=self.titles_box.isChecked(),
            in_synopses=self.synopses_box.isChecked(),
        )
        QMessageBox.information(
            self, "Replace in Project",
            f"Replaced {counts['replacements']} occurrence(s) in "
            f"{counts['documents']} document(s).",
        )
        self.refresh_preview()


# Sentinel for "the project itself" in place of a document id — project
# metadata is edited whenever the inspector shows folder/root fields (1.12).
# Document ids are uuid4 hex, so the two vocabularies cannot collide.
PROJECT_METADATA_TARGET = "project"
# The Type dropdown's roles (1.17): two base ones, close to the truth on
# disk — a folder and a document — and every other role the writer adds,
# which the database keeps per group so a folder's roles never sit in a
# document's list. The list itself lives in the project, not here.

# The page starts the design panel offers (1.17), and the words a config
# may use for the same start: the design says where its pages begin.
PAGE_START_CHOICES = (
    ("Automatic", ""),
    ("Recto — a right-hand page", "Recto"),
    ("Verso — a left-hand page", "Verso"),
    ("Page break", "page-break"),
    ("Continuous", "continuous"),
)
PAGE_START_WORDS = {
    "right": "Recto", "recto": "Recto", "next-odd": "Recto",
    "left": "Verso", "verso": "Verso", "next-even": "Verso",
    "page": "page-break", "page-break": "page-break", "new-page": "page-break",
    "flow": "continuous", "continuous": "continuous", "run-on": "continuous",
}


class MetadataTable(QTableWidget):
    """The metadata grid: required, field, value, format.

    Column 0 is the "required" mark for the current Type (a checkbox):
    marking it makes the field mandatory for every document and folder of
    that Type. Columns 1-3 are the field name, its value and the value
    widget format (Text / Text area / Checkbox / Date).
    """

    changed = Signal()
    required_toggled = Signal(str, bool)  # (field name, required)

    def __init__(self, entries: dict[str, tuple[str, str, list[str]]],
                 parent=None) -> None:
        super().__init__(0, 4, parent)
        self.setHorizontalHeaderLabels(["", "Field", "Value", "Format"])
        self.horizontalHeader().setStretchLastSection(False)
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self._loading = False
        self._required_fields: set[str] = set()
        self._required_enabled = False
        self.itemChanged.connect(self._item_changed)
        for key, (value, value_type, options) in entries.items():
            self._append_row(key, value, value_type, options)

    def set_required_context(self, field_names: list[str], enabled: bool) -> None:
        self._required_fields = {name.casefold() for name in (field_names or [])}
        self._required_enabled = bool(enabled)
        previous, self._loading = self._loading, True
        try:
            for row in range(self.rowCount()):
                box = self.cellWidget(row, 0)
                key = self._field_name(row)
                if isinstance(box, QCheckBox):
                    box.blockSignals(True)
                    box.setChecked(key.casefold() in self._required_fields if key else False)
                    box.setEnabled(self._required_enabled and bool(key))
                    box.blockSignals(False)
        finally:
            self._loading = previous

    def set_entries(self, entries: dict[str, tuple[str, str, list[str]]]) -> None:
        previous, self._loading = self._loading, True
        try:
            self.setRowCount(0)
            for key, (value, value_type, options) in entries.items():
                self._append_row(key, value, value_type, options)
        finally:
            self._loading = previous
        self.set_required_context(list(self._required_fields), self._required_enabled)

    def add_field(self) -> None:
        self._append_row("", "", "text", [])

    def remove_selected(self) -> None:
        rows = sorted({item.row() for item in self.selectedItems()}, reverse=True)
        for row in rows:
            self.removeRow(row)
        if rows:
            self._notify()

    def _item_changed(self, item: QTableWidgetItem) -> None:
        if item and item.column() == 1 and self._required_enabled:
            box = self.cellWidget(item.row(), 0)
            if isinstance(box, QCheckBox):
                box.setEnabled(bool(item.text().strip()))
        self._notify()

    def _notify(self) -> None:
        if not self._loading:
            self.changed.emit()

    def _field_name(self, row: int) -> str:
        item = self.item(row, 1)
        return item.text().strip() if item else ""

    def _append_row(self, key: str, value: str, value_type: str,
                    options: list[str]) -> None:
        previous, self._loading = self._loading, True
        try:
            row = self.rowCount()
            self.insertRow(row)
            req = QCheckBox()
            req.setToolTip("Required for every document and folder of this Type")
            req.setChecked(key.casefold() in self._required_fields if key else False)
            req.setEnabled(self._required_enabled and bool(key.strip()))
            req.stateChanged.connect(lambda _state, r=req: self._required_changed(r))
            self.setCellWidget(row, 0, req)
            self.setItem(row, 1, QTableWidgetItem(key))
            self._set_value_widget(row, value_type, value)
            type_box = QComboBox()
            for label, data in (("Text", "text"), ("Text area", "textarea"),
                                ("Checkbox", "checkbox"), ("Date", "date")):
                type_box.addItem(label, data)
            type_box.setCurrentIndex(max(0, type_box.findData(value_type)))
            self.setCellWidget(row, 3, type_box)
            type_box.currentIndexChanged.connect(
                lambda _index, box=type_box: self._type_changed(box))
        finally:
            self._loading = previous

    def _required_changed(self, box: QCheckBox) -> None:
        if self._loading:
            return
        row = self.indexAt(box.pos()).row()
        name = self._field_name(row)
        if not name:
            box.blockSignals(True)
            box.setChecked(False)
            box.blockSignals(False)
            return
        self.required_toggled.emit(name, box.isChecked())

    def _type_changed(self, type_box: QComboBox) -> None:
        row = self.indexAt(type_box.pos()).row()
        if row < 0:
            return
        self._set_value_widget(row, type_box.currentData(), self.row_value(row))
        self._notify()

    def row_value(self, row: int) -> str:
        widget = self.cellWidget(row, 2)
        if isinstance(widget, QDateEdit):
            return widget.date().toString(Qt.ISODate)
        if isinstance(widget, QCheckBox):
            return "yes" if widget.isChecked() else "no"
        if isinstance(widget, QPlainTextEdit):
            return widget.toPlainText()
        item = self.item(row, 2)
        return item.text() if item else ""

    def eventFilter(self, watched, event):
        if event.type() == QEvent.FocusOut and isinstance(watched, QPlainTextEdit):
            self._notify()
        return super().eventFilter(watched, event)

    def _set_value_widget(self, row: int, value_type: str, value: str) -> None:
        self.removeCellWidget(row, 2)
        self.takeItem(row, 2)
        if value_type == "date":
            editor = QDateEdit()
            editor.setCalendarPopup(True)
            editor.setDisplayFormat("dd MMM yyyy")
            date = QDate.fromString(value, Qt.ISODate)
            if not date.isValid() and value.strip():
                date = QDate.fromString(value.strip(), "dddd, MMMM d, yyyy")
            editor.setDate(date if date.isValid() else QDate.currentDate())
            editor.dateChanged.connect(lambda _date: self._notify())
            self.setCellWidget(row, 2, editor)
            self.setRowHeight(row, self.verticalHeader().defaultSectionSize())
        elif value_type == "checkbox":
            editor = QCheckBox()
            editor.setChecked(value.strip().lower() in ("yes", "true", "1"))
            editor.stateChanged.connect(lambda _state: self._notify())
            self.setCellWidget(row, 2, editor)
            self.setRowHeight(row, self.verticalHeader().defaultSectionSize())
        elif value_type == "textarea":
            editor = QPlainTextEdit(value)
            editor.setMinimumHeight(56)
            editor.installEventFilter(self)
            self.setCellWidget(row, 2, editor)
            self.setRowHeight(row, 64)
        else:
            self.setItem(row, 2, QTableWidgetItem(value))
            self.setRowHeight(row, self.verticalHeader().defaultSectionSize())

    def values_types_options(self) -> tuple[
        dict[str, str], dict[str, str], dict[str, list[str]]
    ]:
        values, value_types, options = {}, {}, {}
        for row in range(self.rowCount()):
            key_item = self.item(row, 1)
            key = key_item.text().strip() if key_item else ""
            if key:
                type_box = self.cellWidget(row, 3)
                value_types[key] = type_box.currentData() if type_box else "text"
                values[key] = self.row_value(row)
                options[key] = []
        return values, value_types, options


class MetadataDialog(QDialog):
    def __init__(self, entries: dict[str, tuple[str, str, list[str]]], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Metadata")
        self.resize(520, 360)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Add any fields you need, such as POV, Location, Date, or Status."))
        self.table = MetadataTable(entries)
        layout.addWidget(self.table)
        controls = QHBoxLayout()
        add_button = QPushButton("Add field")
        remove_button = QPushButton("Remove selected")
        add_button.clicked.connect(self.table.add_field)
        remove_button.clicked.connect(self.table.remove_selected)
        controls.addWidget(add_button)
        controls.addWidget(remove_button)
        controls.addStretch()
        layout.addLayout(controls)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values_types_options(self) -> tuple[
        dict[str, str], dict[str, str], dict[str, list[str]]
    ]:
        return self.table.values_types_options()


class PlaceholderReferenceDialog(QDialog):
    """The ``${...}`` reference as a page (docs/TEMPLATES.md §3.2): what an
    export can fill, and which metadata keys this project actually carries.

    Shown, not exec'd — it is read while the writer keeps working, and it
    is closed when they are done. No Project access here: the window builds
    the page and hands it over. Every name is a link, so the page is also
    how a name gets *written*: clicking one hands it back to the window,
    which puts it at the cursor of whatever box was being written in.
    """

    chosen = Signal(str)

    def __init__(self, page: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Placeholder Reference")
        self.resize(600, 620)
        self.setAttribute(Qt.WA_DeleteOnClose)
        layout = QVBoxLayout(self)
        self.browser = QTextBrowser()
        # setOpenLinks(False): a name is not a URL, it is a string to
        # insert, so the browser hands the click over rather than trying
        # to navigate to `insert:Title`.
        self.browser.setOpenLinks(False)
        self.browser.setHtml(page)
        self.browser.anchorClicked.connect(self._anchor_clicked)
        layout.addWidget(self.browser, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)

    def _anchor_clicked(self, url) -> None:
        """The name behind ``insert:<Name>``, handed to the window. The
        dialog stays open: a head is usually more than one name, and
        re-opening the page for each is the kind of small insult that
        stops people using it."""
        path = url.toString()
        if path.startswith("insert:"):
            self.chosen.emit(path[len("insert:"):])


class RefreshCombo(QComboBox):
    """A dropdown that asks for a fresh list when it opens (1.17).

    The templates a project knows change while it is open — a document
    marked as a template, a one-file template dropped into templates/ —
    and a list frozen at selection time would hide them.
    """

    aboutToOpen = Signal()

    def showPopup(self) -> None:
        self.aboutToOpen.emit()
        super().showPopup()


class Fold(QWidget):
    """A titled, collapsible box for the inspector's metadata groups (1.16).

    The author's complaint was that the metadata was "undifferentiated text
    with no clear logic to it" — the groups give it that logic, and the fold
    keeps the long ones (a synopsis, a table of custom fields) from pushing
    the editor off-screen. The header is a QToolButton, the house's control
    for anything that has to stay usable at any width (2.4h).
    """

    def __init__(self, title: str, *, expanded: bool = True,
                 parent=None) -> None:
        super().__init__(parent)
        self.header = QToolButton()
        self.header.setText(title)
        self.header.setCheckable(True)
        self.header.setChecked(expanded)
        self.header.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self.header)
        layout.addWidget(self.body)
        # A group takes the height it needs and no more: the surplus in a
        # tall inspector belongs to the window, not to a box with a long
        # text area in it.
        self.setSizePolicy(QSizePolicy.Policy.Preferred,
                           QSizePolicy.Policy.Maximum)
        # Accordion headings span the metabox width — a button that
        # hugs its text looks like a button, not a section.
        self.header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.header.setMinimumWidth(200)
        self.header.toggled.connect(self._toggled)
        self._toggled(expanded)

    def _toggled(self, expanded: bool) -> None:
        self.body.setVisible(expanded)
        self.header.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)


# --- template sets in the binder ---------------------------------------
#
# A template set is a folder on disk, not a document: it carries no id,
# and the folder is the truth (docs/TEMPLATES.md §1.1). So the binder and
# the Outliner show it as a synthetic row — recognised by its own kind,
# carrying the resolved set in the next role, and never exporting or
# saving as prose.
TEMPLATE_GROUP_KIND = "template-group"     # the Templates area itself
TEMPLATE_SET_KIND = "template-set"         # one set on disk
TEMPLATE_FOLDER_KIND = "template-folder"   # a subfolder inside a set
TEMPLATE_FILE_KIND = "template-file"       # a file inside a set
TEMPLATE_SET_ROLE = Qt.UserRole + 2        # the TemplateSet object
TEMPLATE_PATH_ROLE = Qt.UserRole + 4       # the file's path inside its set
# What the thing is on disk: a directory is a folder, an .html file is a
# document. The icon says this, whatever role the row gives it.
FOLDER_ROLE = Qt.UserRole + 3

# The head-font row's first entry, and its size box's zero: both mean the
# same thing — the file states nothing, and the sample's own head stands
# (§1.2's `Head font:` line).
HEAD_FONT_SAMPLE = "(the sample's own)"


def template_set_where(template_set, project_root: Path) -> str:
    """Where a set lives, in the writer's words: shipped, this project,
    or the app library path."""
    if template_set.directory is None:
        return "built into Lekhak"
    if Path(template_set.directory).parent == project_template_root(project_root):
        return "this project"
    return str(template_set.directory)



class ExportOptionsDialog(QDialog):
    """Confirm scope and template (§2.1).

    Scope is whatever the binder and outliner have selected, gathered
    before this opens — empty selection means the whole project, not an
    error. The selector shows the answer: applicable templates nearest
    first, pre-selected, with the rest of the project's below. Changing
    it is an override and says so, in the terms that name the persistent
    place — because the pick is one-shot and nothing here is remembered.
    For EPUB, a cover line shows the detected cover or warns when none
    was found (the export itself uses the first cover in binder order).
    """

    OVERRIDE_NOTE = (
        "This applies to this export only. To make it stick, assign the "
        "template to the folder (Book template) or to the document."
    )

    def __init__(self, summary: str, template_sets, preselected_name: str,
                 warnings=None, cover=None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Export Options")
        self.resize(480, 300)
        self.template_sets = list(template_sets)
        self.preselected_name = preselected_name
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Scope"))
        self.summary_label = QLabel(summary)
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)
        layout.addSpacing(12)
        self.warning_label = QLabel("\n".join(warnings or []))
        self.warning_label.setWordWrap(True)
        self.warning_label.setVisible(bool(warnings))
        layout.addWidget(self.warning_label)
        if cover is not None:
            cover_label = QLabel(
                f"Cover: {cover}" if cover else "Cover: none found — this export will have no cover image.")
            cover_label.setWordWrap(True)
            layout.addWidget(cover_label)
        layout.addWidget(QLabel(
            "Template — slot files with ${Title}, ${Content}, "
            "${Synopsis}, ${WordCount}, ${Position}, ${WordsTotal}, "
            "${Metadata.Key}, and ${ToC} in a contents template. "
            "The template panel's Placeholders... button lists them all."
        ))
        set_row = QHBoxLayout()
        self.set_box = QComboBox()
        self.set_box.currentIndexChanged.connect(self._selection_changed)
        set_row.addWidget(self.set_box)
        new_set = QPushButton("New set…")
        new_set.clicked.connect(self._new_set)
        set_row.addWidget(new_set)
        open_folder = QPushButton("Open folder")
        open_folder.clicked.connect(self._open_folder)
        set_row.addWidget(open_folder)
        layout.addLayout(set_row)
        self.override_note = QLabel(self.OVERRIDE_NOTE)
        self.override_note.setWordWrap(True)
        self.override_note.setVisible(False)
        layout.addWidget(self.override_note)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._fill_box(preselected_name)

    def _fill_box(self, select_name: str) -> None:
        """List the sets in the order they were handed over — applicable
        first (§2.1) — and select the one that answers this export."""
        self.set_box.blockSignals(True)
        self.set_box.clear()
        for template_set in self.template_sets:
            self.set_box.addItem(template_set.name, template_set)
        index = next((i for i, s in enumerate(self.template_sets)
                      if s.name == select_name), 0)
        self.set_box.setCurrentIndex(index)
        self.set_box.blockSignals(False)
        self._selection_changed()

    def _selection_changed(self, *_args) -> None:
        """Say, when the writer overrides, that the override is one-shot
        and where the persistent place is (§2.1) — not merely that we
        refuse to remember."""
        self.override_note.setVisible(
            self.set_box.currentText() != self.preselected_name)

    def selected_template_set(self):
        return self.set_box.currentData()

    def _new_set(self) -> None:
        project = getattr(self.parent(), "project", None)
        if not project:
            return
        name, accepted = QInputDialog.getText(self, "New template set", "Set name:")
        if not accepted or not name.strip():
            return
        try:
            current = self.selected_template_set()
            created = copy_template_set(current, name.strip(), project.root)
        except (ValueError, FileExistsError) as error:
            QMessageBox.warning(self, "Cannot create set", str(error))
            return
        self.template_sets.append(created)
        self._fill_box(created.name)

    def _open_folder(self) -> None:
        project = getattr(self.parent(), "project", None)
        if not project:
            return
        root = project_template_root(project.root)
        root.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(root)))


class BulkTypeDialog(QDialog):
    """Bulk *Type* only: radio scope, never both lanes at once.

    Folders get folder Types, files get document Types — a folder Type
    can never land on a file, or the template slot it resolves to
    (`folder` vs `document`) would be the wrong dress. Recursive
    means: pick a folder, tick Include subfolders, choose Files, and
    every document inside becomes that document Type while the folder
    itself stays untouched.
    """
    def __init__(self, count: int, folder_types: list[str], document_types: list[str],
                 include_subfolders: bool, default_scope: str = "document", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Bulk Edit Type ({count} selected)")
        self.resize(520, 340)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Set one Type on every row in this lane. Blank = don't change. "
            "Folders only see folder Types, files only see document Types."))
        self.scope_group = QButtonGroup(self)
        self.scope_folders = QRadioButton("Folders only")
        self.scope_files = QRadioButton("Files only")
        self.scope_group.addButton(self.scope_folders, 0)
        self.scope_group.addButton(self.scope_files, 1)
        if default_scope == "document":
            self.scope_files.setChecked(True)
        else:
            self.scope_folders.setChecked(True)
        row_scope = QHBoxLayout()
        row_scope.addWidget(self.scope_folders)
        row_scope.addWidget(self.scope_files)
        row_scope.addStretch()
        layout.addLayout(row_scope)
        self.include_box = QCheckBox("Include subfolders (recursive)")
        self.include_box.setChecked(include_subfolders)
        layout.addWidget(self.include_box)
        self.type_box = QComboBox()
        self.type_box.setEditable(True)
        self.type_box.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.type_box.lineEdit().setPlaceholderText("Folder, Document, Scene…")
        self.type_box.addItem("(Don't change)", "")
        self._folder_types = list(folder_types)
        self._document_types = list(document_types)

        def _refill(*_args):
            cur = self.type_box.currentData()
            self.type_box.blockSignals(True)
            self.type_box.clear()
            self.type_box.addItem("(Don't change)", "")
            pool = self._folder_types if self.scope_folders.isChecked() else self._document_types
            for n in pool:
                self.type_box.addItem(n, n)
            if cur and cur in pool:
                self.type_box.setCurrentIndex(self.type_box.findData(cur))
            self.type_box.blockSignals(False)

        self.scope_group.buttonToggled.connect(_refill)
        _refill()
        layout.addWidget(QLabel("Type:"))
        layout.addWidget(self.type_box)
        self.checked_box = QCheckBox("I have checked this Type and it is correct")
        layout.addWidget(self.checked_box)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        save_button.setEnabled(False)
        self.checked_box.toggled.connect(lambda on: save_button.setEnabled(on))
        layout.addWidget(buttons)

    def type_value(self) -> str:
        # Editable combo: typed names (first-time Scene) have no data —
        # their text *is* the Type. (Don't change) is "".
        data = self.type_box.currentData()
        if data:
            return str(data)
        text = self.type_box.currentText().strip()
        return "" if text == "(Don't change)" else text

    def scope_group_name(self) -> str:
        return "folder" if self.scope_folders.isChecked() else "document"

    def subfolders(self) -> bool:
        return self.include_box.isChecked()


class BulkMetadataDialog(QDialog):
    """Bulk *Metadata* only: fields by Type-group, never both lanes.

    Keys come from `known_fields(group)` for the lane the radio picks —
    folder metadata is folder metadata, document metadata is document
    metadata. Metadata is by Type in the writer's mental model (Book
    ≠ Chapter ≠ Scene); for now the DB catalogues per group (folder
    vs document), and the lane radio is what keeps a Scene field from
    landing on a Book folder.
    """
    def __init__(self, count: int, folder_keys: list[str], document_keys: list[str],
                 include_subfolders: bool, default_scope: str = "document", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Bulk Edit Metadata ({count} selected)")
        self.resize(560, 460)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Set only the fields you tick — blanks leave values unchanged. "
            "Field names already exist in the DB; no invented keys appear."))
        self.scope_group = QButtonGroup(self)
        self.scope_folders = QRadioButton("Folders only")
        self.scope_files = QRadioButton("Files only")
        self.scope_group.addButton(self.scope_folders, 0)
        self.scope_group.addButton(self.scope_files, 1)
        if default_scope == "document":
            self.scope_files.setChecked(True)
        else:
            self.scope_folders.setChecked(True)
        row_scope = QHBoxLayout()
        row_scope.addWidget(self.scope_folders)
        row_scope.addWidget(self.scope_files)
        row_scope.addStretch()
        layout.addLayout(row_scope)
        self.include_box = QCheckBox("Include subfolders (recursive)")
        self.include_box.setChecked(include_subfolders)
        layout.addWidget(self.include_box)
        self.cover_edit = QLineEdit()
        self.cover_edit.setPlaceholderText("Auto (images/…/cover.*) — blank = don't change")
        layout.addWidget(QLabel("Cover (folders only):"))
        layout.addWidget(self.cover_edit)
        self.fields_container = QWidget()
        self.fields_layout = QVBoxLayout(self.fields_container)
        self.fields_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.fields_container)
        self._folder_keys = list(folder_keys)
        self._document_keys = list(document_keys)
        self.edits: dict[str, QLineEdit] = {}
        # Keep both sets of rows; toggle visibility by scope.
        self._built: dict[str, list[tuple]] = {"folder": [], "document": []}
        for group, keys in (("folder", self._folder_keys), ("document", self._document_keys)):
            for key in keys:
                edit = QLineEdit()
                edit.setPlaceholderText("leave unchanged")
                self.fields_layout.addWidget(QLabel(key))
                self.fields_layout.addWidget(edit)
                self.edits[f"{group}:{key}"] = edit
                self._built[group].append((key, edit))

        def _apply_scope(*_args):
            is_folder = self.scope_folders.isChecked()
            for group, rows in self._built.items():
                show = (group == "folder") == is_folder
                for _k, e in rows:
                    e.setVisible(show)
                    # Hide the label that precedes each edit via previous widget
                    # layout order: QLabel is before QLineEdit; QDialog layout
                    # hides by disabling? Simplify: we only toggle edit; label
                    # stays but user sees field name in edit tooltip fallback.
            self.cover_edit.setEnabled(is_folder)
        self.scope_group.buttonToggled.connect(_apply_scope)
        _apply_scope()
        self.checked_box = QCheckBox("I have checked these values and they are correct")
        layout.addWidget(self.checked_box)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        save_button.setEnabled(False)
        self.checked_box.toggled.connect(lambda on: save_button.setEnabled(on))
        layout.addWidget(buttons)

    def values(self) -> dict[str, str]:
        group = "folder" if self.scope_folders.isChecked() else "document"
        result: dict[str, str] = {}
        for key, edit in self.edits.items():
            g, k = key.split(":", 1)
            if g != group:
                continue
            if edit.text().strip():
                result[k] = edit.text().strip()
        return result

    def cover_value(self) -> str:
        return self.cover_edit.text().strip() if self.scope_folders.isChecked() else ""

    def scope_group_name(self) -> str:
        return "folder" if self.scope_folders.isChecked() else "document"

    def subfolders(self) -> bool:
        return self.include_box.isChecked()


# Backwards alias for anything still naming the old single dialog.
BulkEditDialog = BulkTypeDialog


class PageSetupDialog(QDialog):
    """Print geometry for one book (1.15): trim size, margins, mirror
    and gutter — the interior checklist every print-on-demand service
    asks before it will print. Values are inches (POD specs are in
    inches); export multiplies them into twips. Prefills the folder's
    saved setup, else a sensible print default; nothing is stored
    until the caller saves geometry(). Choosing a known trim fills the
    size, and editing the size switches the trim to Custom — the same
    two-way dance the other dialogs do, guarded by _loading."""

    def __init__(self, geometry: dict | None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Page setup")
        self.cleared = False
        start = clean_page_setup(geometry) or dict(DEFAULT_PAGE_SETUP)
        self._loading = True

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.trim_box = QComboBox()
        for trim_id, (label, _width, _height) in PAGE_TRIMS.items():
            self.trim_box.addItem(label, trim_id)
        self.trim_box.addItem("Custom size", "custom")
        self.width_spin = self._spin(3.0, 14.0, 2)
        self.height_spin = self._spin(3.0, 14.0, 2)
        self.top_spin = self._spin(0.0, 5.0, 3)
        self.bottom_spin = self._spin(0.0, 5.0, 3)
        self.inside_spin = self._spin(0.0, 5.0, 3)
        self.outside_spin = self._spin(0.0, 5.0, 3)
        self.gutter_spin = self._spin(0.0, 2.0, 3)
        self.mirror_box = QCheckBox(
            "Mirror margins (inside and outside swap on even pages)")
        form.addRow("Trim size", self.trim_box)
        form.addRow("Width", self.width_spin)
        form.addRow("Height", self.height_spin)
        form.addRow("Top margin", self.top_spin)
        form.addRow("Bottom margin", self.bottom_spin)
        form.addRow("Inside margin (spine)", self.inside_spin)
        form.addRow("Outside margin", self.outside_spin)
        form.addRow("Gutter (extra at spine)", self.gutter_spin)
        form.addRow(self.mirror_box)
        layout.addLayout(form)
        self.hint = QLabel("")
        layout.addWidget(self.hint)

        reset = QPushButton("Use default (no override)")
        reset.clicked.connect(self._use_default)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        bottom = QHBoxLayout()
        bottom.addWidget(reset)
        bottom.addStretch()
        bottom.addWidget(buttons)
        layout.addLayout(bottom)

        self.width_spin.setValue(start["width"])
        self.height_spin.setValue(start["height"])
        self.top_spin.setValue(start["top"])
        self.bottom_spin.setValue(start["bottom"])
        self.inside_spin.setValue(start["inside"])
        self.outside_spin.setValue(start["outside"])
        self.gutter_spin.setValue(start["gutter"])
        self.mirror_box.setChecked(start["mirror"])
        index = self.trim_box.findData(start["trim"])
        self.trim_box.setCurrentIndex(max(0, index))
        self._loading = False
        self.trim_box.currentIndexChanged.connect(self._trim_changed)
        self.width_spin.valueChanged.connect(self._dims_changed)
        self.height_spin.valueChanged.connect(self._dims_changed)

    @staticmethod
    def _spin(minimum: float, maximum: float, decimals: int) -> QDoubleSpinBox:
        box = QDoubleSpinBox()
        box.setRange(minimum, maximum)
        box.setDecimals(decimals)
        box.setSingleStep(0.0625)   # 1/16 in — the finest POD specs use
        box.setSuffix(" in")
        return box

    def _trim_changed(self) -> None:
        if self._loading:
            return
        trim_id = self.trim_box.currentData()
        if trim_id == "custom":
            return
        _label, width, height = PAGE_TRIMS[trim_id]
        self.width_spin.setValue(width)
        self.height_spin.setValue(height)

    def _dims_changed(self) -> None:
        if self._loading:
            return
        desired = trim_id_for(self.width_spin.value(), self.height_spin.value())
        if self.trim_box.currentData() != desired:
            self.trim_box.setCurrentIndex(self.trim_box.findData(desired))

    def geometry(self) -> dict:
        return {
            "trim": self.trim_box.currentData(),
            "width": self.width_spin.value(),
            "height": self.height_spin.value(),
            "top": self.top_spin.value(),
            "bottom": self.bottom_spin.value(),
            "inside": self.inside_spin.value(),
            "outside": self.outside_spin.value(),
            "mirror": self.mirror_box.isChecked(),
            "gutter": self.gutter_spin.value(),
        }

    def accept(self) -> None:
        # The same clean_page_setup storage will run, so an entry this
        # dialog accepts can never be rejected on the export side.
        if clean_page_setup(self.geometry()) is None:
            self.hint.setText(
                "Those margins leave no text area: inside + outside + gutter "
                "must be under the width, top + bottom under the height."
            )
            return
        super().accept()

    def _use_default(self) -> None:
        self.cleared = True
        super().accept()


class ColumnPickerDialog(QDialog):
    """Choose which columns the outliner shows: built-ins plus any metadata
    key used in the project."""

    def __init__(self, available: list[tuple[str, str]], selected: list[str],
                 parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Outliner Columns")
        self.resize(360, 420)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Show these columns (Title is always shown):"))
        self.columns = QListWidget()
        for column_id, label in available:
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, column_id)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if column_id in selected else Qt.Unchecked)
            if column_id == "title":
                item.setCheckState(Qt.Checked)
                item.setFlags(item.flags() & ~Qt.ItemIsUserCheckable)
            self.columns.addItem(item)
        layout.addWidget(self.columns)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_columns(self) -> list[str]:
        chosen = ["title"]
        for index in range(self.columns.count()):
            item = self.columns.item(index)
            column_id = item.data(Qt.UserRole)
            if column_id != "title" and item.checkState() == Qt.Checked:
                chosen.append(column_id)
        return chosen


def _roundtrip_issues(text: str) -> list[str]:
    """Tier-2 sanity for html slots (2.4): feed the candidate to a
    scratch QTextDocument and make sure every ${...} placeholder
    survived as visible text. The failure this catches is an unclosed
    tag that swallows the rest of the file — fill() would then print the
    swallowed part as nothing."""
    document = QTextDocument()
    document.setHtml(text)
    visible = document.toPlainText()
    issues = []
    for match in PLACEHOLDER_RE.finditer(text):
        if match.group(0) not in visible:
            issues.append(
                f"placeholder {match.group(0)} vanished in the HTML "
                "round-trip — check for an unclosed tag above it")
    return issues


class RevisionPassesDialog(QDialog):
    """List one document's pending revision passes and accept or reject
    the selected one whole (2.4 — v1 granularity is document + pass;
    per-change addressing ships with the data but gets no list yet)."""

    def __init__(self, sessions: list[dict], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Revision Passes")
        self.chosen_accept = True
        self._session_id = ""
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Accept or reject a whole pass:"))
        self.list = QListWidget()
        for session in sessions:
            stamp = session["created_at"][:19].replace("T", " ")
            item = QListWidgetItem(
                f"{session['count']} change(s) — {session['author']} — {stamp}")
            item.setData(Qt.UserRole, session["session_id"])
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(self.list.count() - 1)
        layout.addWidget(self.list)
        row = QHBoxLayout()
        accept = QPushButton("Accept")
        accept.clicked.connect(self._accept_pass)
        reject = QPushButton("Reject")
        reject.clicked.connect(self._reject_pass)
        row.addWidget(accept)
        row.addWidget(reject)
        row.addStretch()
        layout.addLayout(row)

    def selected_session(self) -> str:
        item = self.list.currentItem()
        return str(item.data(Qt.UserRole)) if item else ""

    def _accept_pass(self) -> None:
        self.chosen_accept = True
        self.accept()

    def _reject_pass(self) -> None:
        self.chosen_accept = False
        self.accept()


def load_paste_prefs() -> dict[str, bool]:
    """The paste policy from Preferences, read on every paste.

    Read live rather than cached, so ticking a box takes effect on the
    very next paste with no restart.
    """
    settings = QSettings()
    return {
        "fonts": settings.value("paste/fonts", False, type=bool),
        "paragraphs": settings.value("paste/paragraphs", False, type=bool),
        "inline": settings.value("paste/inline", True, type=bool),
        "alignment": settings.value("paste/alignment", True, type=bool),
    }


def revision_author() -> str:
    """The label stamped on each tracked change (2.4), live from
    Preferences like the paste policy. Single writer today; AI sessions
    pass their own label instead of reading this."""
    return str(QSettings().value("revisions/author", "Author")) or "Author"


def _scrub_char_format(source: QTextCharFormat, keep_fonts: bool,
                       keep_inline: bool) -> QTextCharFormat:
    """Rebuild one fragment's format holding only what the policy keeps.

    Rebuilt from a fresh format instead of deleting properties: a fresh
    QTextCharFormat already carries exactly the editor's defaults, and it
    sidesteps the property enums PySide6 doesn't expose by name. Image
    objects come back untouched — their format *is* the image, so a
    filtered copy would leave a missing-glyph box. Anchors ride along
    whatever the flags say: a link is content, not skin.
    """
    if source.objectType() != QTextFormat.NoObject:
        return source
    result = QTextCharFormat()
    if keep_inline:
        result.setFontWeight(source.fontWeight())
        result.setFontItalic(source.fontItalic())
        result.setFontUnderline(source.fontUnderline())
    if keep_fonts:
        families = source.fontFamilies()
        if families:
            result.setFontFamilies(families)
        if source.fontPointSize() > 0:
            result.setFontPointSize(source.fontPointSize())
        foreground = source.foreground()
        if foreground.style() != Qt.BrushStyle.NoBrush:
            result.setForeground(foreground)
        background = source.background()
        if background.style() != Qt.BrushStyle.NoBrush:
            result.setBackground(background)
    if source.isAnchor():
        result.setAnchor(True)
        href = source.anchorHref()
        if href:
            result.setAnchorHref(href)
    return result


def _scrub_block_format(source: QTextBlockFormat, keep_paragraphs: bool,
                        keep_alignment: bool) -> QTextBlockFormat:
    """Rebuild one block's format: editor defaults plus only what's kept.

    The fresh format *is* the default policy — a Word document's 72 pt
    "before paragraph" margin and its line height are what make a paste
    land at double height, and they all live on the block format.
    """
    result = QTextBlockFormat()
    if keep_alignment:
        alignment = source.alignment()
        if int(alignment):
            result.setAlignment(alignment)
    if keep_paragraphs:
        result.setTopMargin(source.topMargin())
        result.setBottomMargin(source.bottomMargin())
        result.setLeftMargin(source.leftMargin())
        result.setRightMargin(source.rightMargin())
        result.setIndent(source.indent())
        result.setTextIndent(source.textIndent())
        line_height = source.lineHeight()
        if line_height > 0:
            result.setLineHeight(line_height, source.lineHeightType())
    return result


def sanitize_pasted_html(html: str, *, fonts: bool, paragraphs: bool,
                         inline: bool, alignment: bool) -> QTextDocument:
    """Turn clipboard HTML into a scratch document that obeys the paste policy.

    Why: clipboard HTML drags its source application along — font families
    and sizes, indents, line spacing, block margins, blank-paragraph
    scaffolding — and lands in the manuscript as visual chaos. The pass:

        html ── setHtml ──▶ scratch document
              ── read ──▶ record every fragment (start, length, scrubbed
                          format) and every block (start, source format)
              ── apply ──▶ write the recorded formats back. Read first,
                          edit second: editing invalidates QTextBlock
                          handles, so the records hold plain integers and
                          implicitly-shared format copies.
              ── collapse ──▶ (unless paragraphs are kept) delete
                          blank-paragraph runs — leading and trailing runs
                          vanish, interior runs shrink to one blank line

    Object fragments (images) pass through with their format intact and
    never count as blank. QMimeData.html() decodes exactly what Qt's own
    paste path decodes, so there is no charset handling to redo.
    """
    document = QTextDocument()
    document.setHtml(html)
    character_ops: list[tuple[int, int, QTextCharFormat]] = []
    block_ops: list[tuple[int, QTextBlockFormat]] = []
    blank: list[bool] = []
    block = document.firstBlock()
    while block.isValid():
        block_ops.append((block.position(), block.blockFormat()))
        has_object = False
        fragment = block.begin()
        while not fragment.atEnd():
            item = fragment.fragment()
            if item.charFormat().objectType() != QTextFormat.NoObject:
                has_object = True
            else:
                character_ops.append(
                    (item.position(), item.length(),
                     _scrub_char_format(item.charFormat(), fonts, inline)))
            fragment += 1
        text = block.text()
        blank.append(not has_object and not text.replace("\u2028", "").strip())
        block = block.next()
    cursor = QTextCursor(document)
    cursor.beginEditBlock()
    for position, length, char_format in character_ops:
        cursor.setPosition(position)
        cursor.setPosition(position + length, QTextCursor.KeepAnchor)
        cursor.setCharFormat(char_format)
    for position, source_format in block_ops:
        cursor.setPosition(position)
        cursor.setBlockFormat(
            _scrub_block_format(source_format, paragraphs, alignment))
    cursor.endEditBlock()
    if not paragraphs:
        _collapse_blank_blocks(document, blank)
    return document


def _collapse_blank_blocks(document: QTextDocument, blank: list[bool]) -> None:
    """Delete blank-paragraph noise from the scratch document.

    A leading run of blanks (with real text after it) goes entirely, a
    trailing run goes entirely, an interior run shrinks to one blank line,
    and an all-blank document is left whole. Deletions run back to front
    and re-walk the live document each step: blocks before the one being
    cut never move, while the block *after* a cut is read fresh — by the
    time a trailing run is half-eaten its last survivor really is the tail,
    which is exactly when the separator in front of it has to go.
    """
    count = len(blank)
    if count < 2 or not any(blank) or all(blank):
        return
    doomed: list[int] = []
    index = 0
    while index < count:
        if not blank[index]:
            index += 1
            continue
        end = index
        while end < count and blank[end]:
            end += 1
        if index == 0 or end == count:
            # Leading run (real text follows) or trailing run: all of it.
            doomed.extend(range(index, end))
        else:
            # Interior run: keep one blank line between the paragraphs.
            doomed.extend(range(index + 1, end))
        index = end
    cursor = QTextCursor(document)
    cursor.beginEditBlock()
    for index in sorted(doomed, reverse=True):
        block = document.findBlockByNumber(index)
        following = block.next()
        if following.isValid():
            cursor.setPosition(block.position())
            cursor.setPosition(following.position(), QTextCursor.KeepAnchor)
        else:
            # Tail block: swallow the separator in front, or the emptied
            # block would survive as the new tail.
            cursor.setPosition(block.position() - 1)
            cursor.movePosition(QTextCursor.End, QTextCursor.KeepAnchor)
        cursor.removeSelectedText()
    cursor.endEditBlock()


# Editor zoom (display only). The live document is rendered at stored
# sizes × zoom so the writer can enlarge the type without changing a
# single formatting property: save_current() divides the factor back out
# before the HTML reaches Project. That is the same display-layer
# discipline binder numbers and resolved wiki links follow (see the module
# docstring) — and the reason this is a *scale*, not a font-size change.
ZOOM_ANCHOR_PT = 12.0      # the pinned editor size the factor multiplies
ZOOM_MIN = 0.5
ZOOM_MAX = 3.0
ZOOM_STEP = 0.1
FONT_SIZE_RE = re.compile(r"font-size:(\s*)([0-9]*\.?[0-9]+)pt")


def scaled_font_sizes(html: str, factor: float) -> str:
    """Every ``font-size`` in Qt's HTML multiplied by ``factor``.

    The one display-layer transform that has to run both ways: inbound
    (stored → editor, ×zoom) and outbound (editor → stored, ÷zoom). Only
    the numbers move — spacing and the rest of the CSS come back
    byte-for-byte, and a factor of exactly 1 returns the string it was
    given, so an unzoomed save cannot rewrite a file at all.
    """
    if factor == 1.0:
        return html

    def rewrite(match: re.Match) -> str:
        size = round(float(match.group(2)) * factor, 2)
        text = f"{size:.2f}".rstrip("0").rstrip(".")
        return f"font-size:{match.group(1)}{text}pt"

    return FONT_SIZE_RE.sub(rewrite, html)


class LinkEditor(QTextEdit):
    """QTextEdit that reports internal `lekhak://` anchor clicks and puts
    pasted HTML through the paste policy.

    Plain QTextEdit has no link-click support (that is QTextBrowser's API),
    so a click on an anchor is turned into a signal here instead, and its
    raw HTML import is replaced: Qt would happily paste a Word document's
    fonts and margins straight into the manuscript.
    """

    internal_link_clicked = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # Tracked-changes state (2.4). tracking is the Edit-menu toggle,
        # the session id starts a fresh revision pass every time the
        # toggle goes on, and the open-insert pair coalesces consecutive
        # typing into one change until the caret leaves its end.
        self.tracking = False
        self.revision_project: Project | None = None
        self.revision_document_id: str | None = None
        self._session_id = ""
        self._open_insert_id: str | None = None
        self._open_insert_end = -1
        # Display zoom (see ZOOM_ANCHOR_PT). MainWindow keeps this equal to
        # its own factor; the editor needs it only to bring pasted
        # formatting into display units, so it stays a plain attribute.
        self.display_scale = 1.0
        self.cursorPositionChanged.connect(self._close_open_insert_if_moved)

    # --- tracked changes (2.4) ---------------------------------------------

    def begin_document(self, project: Project | None, document_id) -> None:
        """Point the editor at a document and re-attach pending revision
        ids. Called right after setHtml: the stored file carries only the
        sentinel styles, so ids re-attach by span order here (and the
        scene's other half, reconcile, runs on save)."""
        self.revision_project = project
        self.revision_document_id = document_id
        self._close_open_insert()
        if project is not None and document_id:
            revisions.attach_ids(self.document(),
                                 project.revisions(document_id))

    def set_tracking(self, enabled: bool) -> None:
        """Edit → Track Changes. Each turn-on starts a new revision pass,
        so the passes dialog groups by intent rather than by eternity."""
        self.tracking = bool(enabled)
        self._close_open_insert()
        if enabled:
            self._session_id = uuid4().hex

    def _revision_live(self) -> bool:
        return bool(self.tracking and self.revision_project
                    and self.revision_document_id)

    def _close_open_insert(self) -> None:
        self._open_insert_id = None
        self._open_insert_end = -1

    def _close_open_insert_if_moved(self) -> None:
        if self._open_insert_id is None:
            return
        if (self.textCursor().position() != self._open_insert_end
                or self.textCursor().hasSelection()):
            self._close_open_insert()

    def _add_revision(self, kind: str, session_id: str = "",
                      author: str = ""):
        rows = self.revision_project.revisions(self.revision_document_id)
        sequence = max((row.sequence for row in rows), default=0) + 1
        return self.revision_project.add_revision(
            self.revision_document_id,
            session_id or self._session_id,
            kind,
            author or revision_author(),
            sequence,
        )

    def _mark_range_insert(self, start: int, end: int,
                           session_id: str = "", author: str = "") -> None:
        revision = self._add_revision(revisions.KIND_INSERT, session_id,
                                      author)
        cursor = QTextCursor(self.document())
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.KeepAnchor)
        cursor.mergeCharFormat(revisions.revision_char_format(
            revisions.KIND_INSERT, revision.id))

    def _delete_selection_tracked(self, cursor: QTextCursor) -> None:
        """Replace a selection under tracking: insert/delete marks inside
        it are removed outright (their words were never final), each
        unmarked run becomes one delete change of its own."""
        document = self.document()
        start, end = cursor.selectionStart(), cursor.selectionEnd()
        cursor.beginEditBlock()
        doomed: list[str] = []
        for kind, revision_id, position, length in reversed(
                revisions.segments(document, start, end)):
            if kind is None:
                revision = self._add_revision(revisions.KIND_DELETE)
                marked = QTextCursor(document)
                marked.setPosition(position)
                marked.setPosition(position + length,
                                   QTextCursor.KeepAnchor)
                marked.mergeCharFormat(revisions.revision_char_format(
                    revisions.KIND_DELETE, revision.id))
            else:
                revisions.remove_text(document, position, length)
                if revision_id:
                    doomed.append(revision_id)
        cursor.endEditBlock()
        self.revision_project.remove_revisions(doomed)
        self._close_open_insert()
        # Collapse the widget caret onto the range start: the marking
        # cursors above never move it, and a still-selected range would
        # make the next keystroke run this whole pass a second time.
        collapsed = QTextCursor(document)
        collapsed.setPosition(start)
        self.setTextCursor(collapsed)

    def _type_tracked(self, text: str) -> None:
        cursor = self.textCursor()
        if cursor.hasSelection():
            self._delete_selection_tracked(cursor)
            cursor = self.textCursor()
        kind, revision_id, _position = revisions.char_at(
            self.document(), cursor.position() - 1)
        if (self._open_insert_id and kind == revisions.KIND_INSERT
                and revision_id == self._open_insert_id
                and cursor.position() == self._open_insert_end):
            # Consecutive typing extends one change: the caret format
            # still carries the mark, so plain insertText inherits it.
            # The end is updated *before* the insert because the caret
            # signal fires inside insertText, mid-move, and would close
            # the burst against a stale end.
            self._open_insert_end = cursor.position() + len(text)
            cursor.insertText(text)
            return
        revision = self._add_revision(revisions.KIND_INSERT)
        self._open_insert_id = revision.id
        self._open_insert_end = cursor.position() + len(text)
        cursor.insertText(text, revisions.revision_char_format(
            revisions.KIND_INSERT, revision.id, cursor.charFormat()))

    def _erase_tracked(self, backward: bool) -> None:
        cursor = self.textCursor()
        if cursor.hasSelection():
            self._delete_selection_tracked(cursor)
            return
        position = cursor.position()
        if backward and position == 0:
            return
        target = position - 1 if backward else position
        kind, revision_id, char_position = revisions.char_at(
            self.document(), target)
        cursor.setPosition(char_position)
        cursor.setPosition(char_position + 1, QTextCursor.KeepAnchor)
        if kind is None:
            revision = self._add_revision(revisions.KIND_DELETE)
            cursor.mergeCharFormat(revisions.revision_char_format(
                revisions.KIND_DELETE, revision.id))
            return
        # Erasing marked text removes it outright: an insertion being
        # undone never existed, a deletion being erased stays erased.
        cursor.removeSelectedText()
        if revision_id and not revisions.find_id(self.document(),
                                                  revision_id):
            self.revision_project.remove_revisions([revision_id])
        if revision_id == self._open_insert_id:
            self._close_open_insert()

    def insert_tracked_text(self, text: str) -> None:
        """Menu-driven inserts (Insert Link…) go through the mark path
        when tracking is on, so structural inserts are tracked too."""
        if self._revision_live() and text:
            self._type_tracked(text)
        else:
            self.textCursor().insertText(text)

    def apply_tracked_replacement(self, start: int, end: int,
                                  new_text: str, session_id: str,
                                  author: str) -> None:
        """The AI path (2.4 layer 2): replace [start, end) as one
        tracked change pair under a given session and author, regardless
        of the tracking toggle — AI output never lands directly. Old
        unmarked text stays visible as a deletion; the new words go in
        front of it as an insertion, so accept keeps the new and reject
        restores the old exactly."""
        document = self.document()
        cursor = QTextCursor(document)
        cursor.beginEditBlock()
        doomed: list[str] = []
        for kind, revision_id, position, length in reversed(
                revisions.segments(document, start, end)):
            if kind is None:
                revision = self._add_revision(revisions.KIND_DELETE,
                                              session_id, author)
                marked = QTextCursor(document)
                marked.setPosition(position)
                marked.setPosition(position + length,
                                   QTextCursor.KeepAnchor)
                marked.mergeCharFormat(revisions.revision_char_format(
                    revisions.KIND_DELETE, revision.id))
            else:
                revisions.remove_text(document, position, length)
                if revision_id:
                    doomed.append(revision_id)
        if new_text:
            revision = self._add_revision(revisions.KIND_INSERT,
                                          session_id, author)
            insert_at = QTextCursor(document)
            insert_at.setPosition(start)
            insert_at.insertText(new_text, revisions.revision_char_format(
                revisions.KIND_INSERT, revision.id))
        cursor.endEditBlock()
        self.revision_project.remove_revisions(doomed)
        self._close_open_insert()

    def insertFromMimeData(self, source) -> None:
        """Paste under the Preferences policy; let Qt handle the rest.

        Clipboard HTML goes through `sanitize_pasted_html`; plain text,
        images and anything carrying no HTML fall through to Qt's own
        import, which is already correct for them. Under tracking the
        text route is re-routed through insertPlainText so the paste
        lands as one marked change (2.4).
        """
        if source is None:
            return
        html = source.html() if source.hasHtml() else ""
        if not html:
            if self._revision_live() and source.hasText():
                self.insertPlainText(source.text())
                return
            super().insertFromMimeData(source)
            return
        prefs = load_paste_prefs()
        self._insert_scratch(sanitize_pasted_html(
            html,
            fonts=prefs["fonts"],
            paragraphs=prefs["paragraphs"],
            inline=prefs["inline"],
            alignment=prefs["alignment"],
        ))

    def insertPlainText(self, text: str) -> None:
        """Plain-text insertion that tracks like typing would (2.4): a
        replaced selection first, then the whole paste as one insert
        change — pastes are one act, not one change per word."""
        if not self._revision_live() or not text:
            super().insertPlainText(text)
            return
        cursor = self.textCursor()
        if cursor.hasSelection():
            self._delete_selection_tracked(cursor)
            cursor = self.textCursor()
        revision = self._add_revision(revisions.KIND_INSERT)
        cursor.insertText(text, revisions.revision_char_format(
            revisions.KIND_INSERT, revision.id, cursor.charFormat()))
        # A paste is its own change; it never coalesces with typing.
        self._close_open_insert()

    def paste_as_plain_text(self) -> None:
        """Ctrl+Shift+V: take only the clipboard's text, never its format.

        QTextEdit exposes no plain-paste entry of its own, and this is
        what the shortcut has always meant: the words, in whatever
        formatting already surrounds the caret. Quirky sources can ship
        HTML with no text/plain twin — the words are still recoverable,
        and plain paste has to stay plain even then.
        """
        source = QApplication.clipboard().mimeData()
        if source is None:
            return
        text = source.text()
        if not text and source.hasHtml():
            scratch = QTextDocument()
            scratch.setHtml(source.html())
            text = scratch.toPlainText()
        if not text:
            self.paste()  # images and other non-text take the normal path
            return
        self.insertPlainText(text)

    def paste_keep_all(self) -> None:
        """Paste keeping everything — fonts, spacing, colours, alignment.

        The deliberate escape hatch (feeds 1.11): when a passage is pasted
        *for* its formatting, the policy must not touch it. No HTML means
        there is nothing to keep, so the regular paste path runs instead.
        """
        source = QApplication.clipboard().mimeData()
        html = source.html() if source is not None and source.hasHtml() else ""
        if not html:
            self.paste()
            return
        document = QTextDocument()
        document.setHtml(html)
        self._insert_scratch(document)

    def _insert_scratch(self, document: QTextDocument) -> None:
        """Drop a prepared scratch document in at the caret.

        insertFragment only moves the cursor it is given, never the
        widget's own, so the caret is handed back explicitly — otherwise
        typing would carry on at the pre-paste spot. One edit block keeps
        the whole paste a single undo step. Under tracking the pasted
        range becomes one insert change (2.4).
        """
        self._scale_scratch(document)
        cursor = self.textCursor()
        if cursor.hasSelection() and self._revision_live():
            self._delete_selection_tracked(cursor)
            cursor = self.textCursor()
        cursor.beginEditBlock()
        start = cursor.position()
        cursor.insertFragment(QTextDocumentFragment(document))
        end = cursor.position()
        cursor.endEditBlock()
        self.setTextCursor(cursor)
        if self._revision_live() and end > start:
            self._mark_range_insert(start, end)
            self._close_open_insert()

    def _scale_scratch(self, document: QTextDocument) -> None:
        """Bring a scratch (pasted) document into display units.

        A paste carries the sizes of wherever it came from; the live
        document runs at stored sizes × display_scale, so the paste has to
        be multiplied to match. Without this, pasted text would land at
        its source size while the prose around it is magnified.
        """
        if self.display_scale == 1.0:
            return
        cursor = QTextCursor(document)
        cursor.beginEditBlock()
        block = document.firstBlock()
        while block.isValid():
            fragment = block.begin()
            while not fragment.atEnd():
                item = fragment.fragment()
                if (item.isValid()
                        and item.charFormat().objectType() == QTextFormat.NoObject):
                    size = item.charFormat().fontPointSize()
                    if size > 0:
                        fmt = QTextCharFormat(item.charFormat())
                        fmt.setFontPointSize(
                            round(size * self.display_scale, 2))
                        cursor.setPosition(item.position())
                        cursor.setPosition(item.position() + item.length(),
                                           QTextCursor.KeepAnchor)
                        cursor.setCharFormat(fmt)
                fragment += 1
            block = block.next()
        cursor.endEditBlock()

    def copy(self) -> None:
        """Copy in *stored* units — the clipboard is a saved layer, not a
        display one.

        Qt's own copy exports the live document, which runs at stored
        sizes × display_scale. Putting that on the clipboard would hand
        other applications type the manuscript never had, and _scale_scratch
        would multiply it again on the way back in: at 150 %, a 12 pt
        paragraph copied here would paste at 27 pt and be *stored* as 18 pt
        after the next save. So the HTML goes out divided by the factor,
        the same way save_current() does, and a paste puts it back once.
        """
        if self.display_scale == 1.0 or not self.textCursor().hasSelection():
            super().copy()      # nothing selected: Qt leaves the clipboard alone
            return
        super().copy()      # Qt lays the selection out: text, HTML, images
        clipboard = QApplication.clipboard()
        source = clipboard.mimeData()
        if source is None or not source.hasHtml():
            return          # plain text and images carry no sizes to undo
        mime = QMimeData()
        mime.setText(source.text())
        mime.setHtml(scaled_font_sizes(source.html(), 1.0 / self.display_scale))
        clipboard.setMimeData(mime)

    def cut(self) -> None:
        """Cut is copy() plus a delete, so the clipboard half obeys copy().

        Qt's own cut builds the mime data itself and never calls the
        Python override, which would leave the magnified HTML on the
        clipboard exactly as copy() used to.
        """
        self.copy()
        self.textCursor().removeSelectedText()

    def wheelEvent(self, event) -> None:
        """Ctrl+wheel magnifies the writing area (display only)."""
        if event.modifiers() & Qt.ControlModifier:
            zoom_by = getattr(self.window(), "zoom_by", None)
            if zoom_by is not None:
                zoom_by(ZOOM_STEP if event.angleDelta().y() > 0 else -ZOOM_STEP)
                event.accept()
                return
        super().wheelEvent(event)

    def keyPressEvent(self, event) -> None:
        """Typing, erasing and selection replacement under tracking (2.4).

        Shortcuts and cursor motion fall through to Qt untouched (they
        just close the open insert burst); a paragraph-mark join at a
        block boundary also falls through — Qt owns block merging, and
        marking the invisible separator is not worth fighting it for.
        """
        if not self._revision_live():
            super().keyPressEvent(event)
            return
        key = event.key()
        if event.modifiers() & (Qt.KeyboardModifier.ControlModifier
                                | Qt.KeyboardModifier.AltModifier
                                | Qt.KeyboardModifier.MetaModifier):
            self._close_open_insert()
            super().keyPressEvent(event)
            return
        if key in (Qt.Key_Backspace, Qt.Key_Delete):
            cursor = self.textCursor()
            at_boundary = (
                key == Qt.Key_Backspace and cursor.atBlockStart()
            ) or (
                key == Qt.Key_Delete and cursor.atBlockEnd()
            )
            if not cursor.hasSelection() and at_boundary:
                self._close_open_insert()
                super().keyPressEvent(event)
                return
            self._erase_tracked(key == Qt.Key_Backspace)
            return
        text = event.text()
        if text:
            self._type_tracked(text)
            return
        self._close_open_insert()
        super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:
        anchor = self.anchorAt(event.position().toPoint())
        if anchor.startswith("lekhak://"):
            self.internal_link_clicked.emit(anchor)
            return
        super().mousePressEvent(event)


# The toolbar's six built-in paragraph styles as signatures — the seed
# list user styles join and may replace by name (1.11). Default dresses
# every blank document and carries the serif family name itself, so the
# platform's own serif (Times New Roman where it lives, its equivalent
# elsewhere) is the house voice — never a hard-pinned font that breaks
# when a project travels. Each signature carries exactly what
# apply_paragraph_style always set, made explicit so stored styles and
# seeds round-trip through the same code; margins and indent ride along
# on every seed so switching styles resets them instead of leaking the
# previous style's. Alignment, underline and line-height are
# deliberately absent: the built-ins never touched them.
SEED_STYLES: dict[str, dict] = {
    "Default": {"family": "serif", "size": 12.0, "weight": int(QFont.Normal),
                "italic": False, "top_margin": 0.0, "bottom_margin": 0.0,
                "left_margin": 0.0, "right_margin": 0.0, "indent": 0,
                "text_indent": 0.0},
    "Title": {"size": 28.0, "weight": int(QFont.Bold), "italic": False,
              "top_margin": 0.0, "bottom_margin": 0.0, "left_margin": 0.0,
              "right_margin": 0.0, "indent": 0, "text_indent": 0.0},
    "Heading 1": {"size": 22.0, "weight": int(QFont.Bold), "italic": False,
                  "top_margin": 0.0, "bottom_margin": 0.0, "left_margin": 0.0,
                  "right_margin": 0.0, "indent": 0, "text_indent": 0.0},
    "Heading 2": {"size": 18.0, "weight": int(QFont.Bold), "italic": False,
                  "top_margin": 0.0, "bottom_margin": 0.0, "left_margin": 0.0,
                  "right_margin": 0.0, "indent": 0, "text_indent": 0.0},
    "Heading 3": {"size": 15.0, "weight": int(QFont.Bold), "italic": False,
                  "top_margin": 0.0, "bottom_margin": 0.0, "left_margin": 0.0,
                  "right_margin": 0.0, "indent": 0, "text_indent": 0.0},
    "Quote": {"size": 12.0, "weight": int(QFont.Normal), "italic": True,
              "top_margin": 0.0, "bottom_margin": 0.0, "left_margin": 32.0,
              "right_margin": 32.0, "indent": 0, "text_indent": 0.0},
}

# The style box's own commands (2.4h): trailing entries that make a style
# from the paragraph under the caret, or re-capture the selected one.
# Custom data rather than a name, so no style anyone might one day call
# "/new-style" could be confused with the command. Delete stays in the
# context menu — it is a library operation, and Preferences grows the
# styles panel before anything else owns it.
NEW_STYLE_COMMAND = "/new-style"
UPDATE_STYLE_COMMAND = "/update-style"


# The Default style as a human file (docs/STORAGE.md): a CSS-flavoured
# text file at the project's root, written on every styles save and read
# on every load. The database holds its own copy for quick loading, but
# the file is what the writer can read, hand-edit and take with them —
# zip the project and the styling travels, even without Lekhak.
DEFAULT_CSS_FILENAME = "default.css"

_CSS_TO_SIGNATURE = {
    "font-family": "family",
    "font-size": "size",
    "font-weight": "weight",
    "font-style": "italic",
    "text-decoration": "underline",
    "margin-top": "top_margin",
    "margin-right": "right_margin",
    "margin-bottom": "bottom_margin",
    "margin-left": "left_margin",
    "text-indent": "text_indent",
    "text-align": "alignment",
    "line-height": "line_height",
    "indent": "indent",
}


def default_style_text(signature: dict) -> str:
    """The Default signature as CSS-ish text for default.css.

    Only keys the style actually carries are written, in one
    predictable order, so the file reads like the styling it records.
    `indent` is a Lekhak key rather than CSS, and travels as its own
    line — the database keeps bearing the rest either way.
    """
    name = signature.get("family", "serif")
    if signature.get("italic"):
        style_face = "italic"
    else:
        style_face = "normal"
    weight = signature.get("weight", int(QFont.Normal))
    if weight >= int(QFont.Bold):
        weight_face = "bold"
    elif weight <= int(QFont.Normal):
        weight_face = "normal"
    else:
        weight_face = str(weight)
    lines = [
        "/* The Default style — blank documents open dressed like this.",
        "   Edit it by hand and Lekhak reads it back on the next open.",
        "   Or style a paragraph and choose “Use as Default” in the editor.",
        "   Written and read by Lekhak; the database holds a copy too. */",
        f"font-family: {name};",
        f"font-size: {signature.get('size', 12.0):g}pt;",
        f"font-weight: {weight_face};",
        f"font-style: {style_face};",
    ]
    if "underline" in signature:
        lines.append(
            f"text-decoration: {'underline' if signature['underline'] else 'none'};")
    for key in ("top_margin", "right_margin", "bottom_margin", "left_margin"):
        lines.append(f"margin-{key.rsplit('_', 1)[0]}: "
                     f"{signature.get(key, 0.0):g}pt;")
    if signature.get("indent", 0):
        lines.append(f"indent: {int(signature['indent'])};")
    if signature.get("text_indent", 0.0):
        lines.append(f"text-indent: {float(signature['text_indent']):g}pt;")
    alignment = signature.get("alignment", 0)
    if alignment:
        for flag, keyword in (
                (int(Qt.AlignmentFlag.AlignHCenter), "center"),
                (int(Qt.AlignmentFlag.AlignRight), "right"),
                (int(Qt.AlignmentFlag.AlignJustify), "justify")):
            if alignment & flag:
                lines.append(f"text-align: {keyword};")
                break
    line_height = signature.get("line_height")
    if line_height:
        value, kind = line_height
        lines.append(f"line-height: {float(value):g}pt;"
                     if kind == QTextBlockFormat.LineHeightTypes.FixedHeight.value
                     else f"line-height: {float(value):g};")
    return "\n".join(lines) + "\n"


def parse_default_style(text: str) -> dict:
    """CSS-ish text back into the Default signature (partial on purpose:
    junk lines are ignored rather than failing the load)."""
    signature: dict = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("/*", "*", "*/")):
            continue
        if ":" not in line:
            continue
        property_, _, value = line.partition(":")
        key = _CSS_TO_SIGNATURE.get(property_.strip().lower())
        if key is None:
            continue
        value = value.strip().rstrip(";").strip().lower()
        if key == "family":
            signature[key] = value.strip("\"'") or "serif"
        elif key == "size":
            if value.endswith("pt"):
                try:
                    signature[key] = float(value[:-2])
                except ValueError:
                    continue
            else:
                continue
        elif key in ("top_margin", "right_margin", "bottom_margin",
                     "left_margin", "text_indent"):
            if value.endswith("pt"):
                try:
                    signature[key] = float(value[:-2])
                except ValueError:
                    continue
            else:
                continue
        elif key == "weight":
            if value == "bold":
                signature[key] = int(QFont.Bold)
            elif value == "normal":
                signature[key] = int(QFont.Normal)
            elif value.isdigit():
                signature[key] = int(value)
        elif key == "italic":
            signature[key] = value in ("italic", "oblique")
        elif key == "underline":
            signature[key] = value == "underline"
        elif key == "indent":
            try:
                signature[key] = int(value)
            except ValueError:
                continue
        elif key == "alignment":
            for keyword, flag in (
                    ("left", int(Qt.AlignmentFlag.AlignLeft)),
                    ("center", int(Qt.AlignmentFlag.AlignHCenter)),
                    ("right", int(Qt.AlignmentFlag.AlignRight)),
                    ("justify", int(Qt.AlignmentFlag.AlignJustify))):
                if value == keyword:
                    signature[key] = flag
                    break
        elif key == "line_height":
            try:
                number = float(value[:-2] if value.endswith("pt") else value)
            except ValueError:
                continue
            kind = (QTextBlockFormat.LineHeightTypes.FixedHeight.value
                    if value.endswith("pt")
                    else
                    QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
            signature[key] = [number, kind]
    return signature


def capture_paragraph_signature(cursor: QTextCursor) -> dict:
    """Snapshot the formatting of the paragraph under the cursor.

    The signature a saved style is: what you see is what a later
    application reproduces. The character side comes from the format at
    the caret (a mixed paragraph gives the part you are standing in);
    underline, family and line-height are recorded only when actually
    present, so a paragraph wearing none of them compares equal to the
    built-in seed it matches. Size falls back to the 12 pt the editor
    writes in when nothing has set one yet.
    """
    char_format = cursor.charFormat()
    block_format = cursor.blockFormat()
    signature = {
        "size": char_format.fontPointSize() or 12.0,
        "weight": int(char_format.fontWeight()),
        "italic": bool(char_format.fontItalic()),
        "top_margin": block_format.topMargin(),
        "bottom_margin": block_format.bottomMargin(),
        "left_margin": block_format.leftMargin(),
        "right_margin": block_format.rightMargin(),
        "indent": int(block_format.indent()),
        "text_indent": block_format.textIndent(),
        "alignment": int(block_format.alignment() or 0),
    }
    if char_format.fontUnderline():
        signature["underline"] = True
    families = char_format.fontFamilies()
    if families:
        signature["family"] = str(families[0])
    line_height = block_format.lineHeight()
    if line_height > 0:
        signature["line_height"] = [line_height, int(block_format.lineHeightType())]
    return signature


def block_uniform_flags(cursor: QTextCursor) -> dict[str, bool]:
    """Which char props are uniform across the whole block under cursor.

    Walks every text fragment in the block, ignoring object fragments
    and the trailing paragraph separator. If only one distinct value
    appears, the prop is uniform. Used only to pre-tick the style
    approval dialog — the writer still sees and approves each tick, so
    a mixed italic can never slip into a paragraph style unnoticed.
    """
    block = cursor.block()
    italics: set[bool] = set()
    weights: set[int] = set()
    underlines: set[bool] = set()
    sizes: set[float] = set()
    families: set[str] = set()
    fragment = block.begin()
    while not fragment.atEnd():
        item = fragment.fragment()
        if item.isValid() and item.length() > 0:
            char_format = item.charFormat()
            if char_format.objectType() == QTextFormat.NoObject:
                italics.add(bool(char_format.fontItalic()))
                weights.add(int(char_format.fontWeight()))
                underlines.add(bool(char_format.fontUnderline()))
                sizes.add(float(char_format.fontPointSize() or 0.0))
                fams = char_format.fontFamilies()
                families.add(str(fams[0]) if fams else "")
        fragment += 1
    if not italics:
        return {"italic": False, "weight": False, "underline": False,
                "size": False, "family": False}
    def _one(values) -> bool:
        return len(values) == 1
    return {
        "italic": _one(italics),
        "weight": _one(weights),
        "underline": _one(underlines),
        "size": _one(sizes),
        "family": _one(families),
    }


def style_signature_formats(signature: dict) -> tuple[QTextBlockFormat, QTextCharFormat]:
    """Rebuild the Qt formats a stored style signature stands for.

    A signature is explicit about what it does NOT touch: keys it doesn't
    carry are never written. Cardinal sin (MODEL.md): a paragraph style
    that omits italic/bold/underline leaves the prose exactly as it was —
    only properties the writer ticked in the approval dialog are written.
    The built-in seeds carry every reset key explicitly, so they still
    fully reset size/weight/italic/margins as before.
    """
    char_format = QTextCharFormat()
    if "size" in signature:
        char_format.setFontPointSize(signature["size"])
    if "weight" in signature:
        char_format.setFontWeight(signature["weight"])
    if "italic" in signature:
        char_format.setFontItalic(bool(signature["italic"]))
    if "underline" in signature:
        char_format.setFontUnderline(bool(signature["underline"]))
    family = signature.get("family")
    if family:
        char_format.setFontFamilies([family])
    block_format = QTextBlockFormat()
    if signature.get("alignment"):
        block_format.setAlignment(Qt.Alignment(signature["alignment"]))
    for key, setter in (("top_margin", block_format.setTopMargin),
                        ("bottom_margin", block_format.setBottomMargin),
                        ("left_margin", block_format.setLeftMargin),
                        ("right_margin", block_format.setRightMargin),
                        ("indent", block_format.setIndent),
                        ("text_indent", block_format.setTextIndent)):
        if key in signature:
            setter(signature[key])
    line_height = signature.get("line_height")
    if line_height:
        block_format.setLineHeight(line_height[0], line_height[1])
    return block_format, char_format


class MainWindow(QMainWindow):
    # The daily update check runs off the UI thread; the one sentence it
    # produces crosses back through this signal (docs/TEMPLATES.md §8).
    update_news = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.update_news.connect(self._say_update_news)
        self.project: Project | None = None
        self.current_id: str | None = None
        # What the binder selection is, when it is not a document: a
        # template set (the folder on disk) or the Templates area itself.
        self.current_template_set = None
        self.current_template_group = False
        # A template file open in the editor (§1.2): a fragment is an
        # HTML file and this is an HTML editor, so the file loads here
        # like any document and Save writes it back to its own path.
        self.current_template_file = None
        # The placeholder issues last reported for the open template file,
        # so a warning is given once per set of issues and not on every
        # autosave (see _warn_about_unknown_placeholders).
        self._template_issues: list[str] = []
        # Set while a refused title is put back, so the itemChanged it
        # raises does not ask again (the writer already said no).
        self._reverting_title = False
        # The document whose design the panel is showing (1.17): a
        # template document is designed in the sidebar like a template
        # file is, and this says which document that is.
        self._design_document_id = None
        self.loading = False
        # Set while a binder sweep is waiting for the event loop, so a
        # drop's several signals cost one sweep rather than several.
        self._tree_move_pending = False
        self.focus_mode = False
        self.zoom = 1.0            # display-only; see ZOOM_ANCHOR_PT
        self._project_total = 0
        # Session goal (BUILD-NOW 6): the project total when this
        # session opened — the baseline for the "+N this session" delta
        # shown beside the daily goal.
        self._session_start_total = 0
        # Paragraph styles (1.11): seeds plus this project's saved overrides.
        self._styles = dict(SEED_STYLES)
        self._user_styles: dict[str, dict] = {}
        # The style box's own last pick (2.4h) — what its Update command
        # re-captures, since the box's selection moves onto the command
        # itself the moment it is clicked.
        self._style_selection = "Default"
        # The bar's alignment buttons (2.4h), declared here because the
        # editor's first signals fire before the bar is built.
        self._alignment_actions = {}
        # Whose rows the inspector's metadata table is showing right now:
        # a document id, PROJECT_METADATA_TARGET, or None with no project.
        # Captured when the table is populated so a late edit (focus
        # leaving a cell mid-selection-change) still lands where it came
        # from instead of following current_id (1.12).
        self._metadata_target: str | None = None
        self._type_target_id: str | None = None
        self._type_snapping = False
        self._type_near_miss_text = ""
        # Read aloud (3.1): the engine connects on first use, not here —
        # a speech-dispatcher handshake costs real time and most
        # sessions never speak.
        self._reader: tts.SpeechReader | None = None
        self._speech_paused = False
        self.setWindowTitle("Lekhak")
        self.resize(1280, 820)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        # 2.1: exports compile from the tree selection, so both trees
        # allow ctrl/shift ranges instead of one item at a time.
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setDragDropMode(QTreeWidget.InternalMove)
        self.tree.currentItemChanged.connect(self._selection_changed)
        self.tree.itemChanged.connect(self._title_changed)
        # A drop reports itself as a removal plus an insertion, or as a
        # move, depending on how Qt chose to write it (1.18): listening
        # to only one of the three left a real drag unheard, and the
        # binder moved on screen while nothing was written down.
        model = self.tree.model()
        model.rowsMoved.connect(self._tree_structure_changed)
        model.rowsInserted.connect(self._tree_structure_changed)
        model.rowsRemoved.connect(self._tree_structure_changed)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_context_menu)

        self.editor = LinkEditor()
        self.editor.setAcceptRichText(True)
        self.editor.setPlaceholderText("Start writing…")
        self.editor.textChanged.connect(self._content_changed)
        self.editor.cursorPositionChanged.connect(self._update_word_count)
        # The bar's alignment buttons follow the caret (2.4h).
        self.editor.cursorPositionChanged.connect(
            self._refresh_alignment_buttons)
        self.editor.cursorPositionChanged.connect(
            self._refresh_font_boxes)
        self.editor.selectionChanged.connect(self._refresh_font_boxes)
        # Clearing a selection can leave the position where it was, so
        # cursor moves alone don't refresh the "N selected" count (3).
        self.editor.selectionChanged.connect(self._update_word_count)
        self.editor.internal_link_clicked.connect(self._link_clicked)
        # Qt's own right-click menu, extended with the style commands (1.11).
        self.editor.setContextMenuPolicy(Qt.CustomContextMenu)
        self.editor.customContextMenuRequested.connect(self._editor_context_menu)
        # Pin the writing font so interface-font preferences never leak into
        # document content (toHtml() round-trips the body size); 12 pt is
        # what reset_zoom already assumed.
        writing_font = self.editor.font()
        writing_font.setPointSize(12)
        self.editor.setFont(writing_font)

        self.corkboard = QListWidget()
        self.corkboard.setViewMode(QListWidget.IconMode)
        self.corkboard.setResizeMode(QListWidget.Adjust)
        self.corkboard.setMovement(QListWidget.Static)
        self.corkboard.setWordWrap(True)
        self.corkboard.setGridSize(QSize(220, 150))
        self.corkboard.itemDoubleClicked.connect(self._open_card)
        # A card is two things: a single click opens its synopsis to be
        # written on, a double click opens the document. Editing is ours
        # rather than Qt's, because Qt would spend the double click on
        # the editor.
        self.corkboard.itemClicked.connect(self._corkboard_clicked)
        self.corkboard.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._synopsis_delegate = _SynopsisDelegate(self.corkboard)
        self.corkboard.setItemDelegate(self._synopsis_delegate)
        self._synopsis_delegate.edited.connect(self._save_card_synopsis)
        self._corkboard_click_timer = QTimer(self)
        self._corkboard_click_timer.setSingleShot(True)
        self._corkboard_click_timer.timeout.connect(self._edit_card_synopsis)
        self._corkboard_pending_card = None
        self._style_corkboard()

        self.outliner = QTreeWidget()
        self.outliner.setUniformRowHeights(True)
        self.outliner.setSelectionMode(QAbstractItemView.ExtendedSelection)
        # Double-click only navigates. A single click used to call
        # _outliner_clicked too, which immediately opened the document
        # and yanked the view away — making Ctrl+click multi-select
        # impossible (each click opened and reset the selection).
        self.outliner.itemDoubleClicked.connect(self._outliner_clicked)
        self.outliner.itemSelectionChanged.connect(self._outliner_inspector_changed)
        # Template rows carry their own right-click actions (§1.1): a
        # file in a set's listing can be copied out into a set of its
        # own. Everything else keeps Qt's plain behaviour — the binder
        # owns the menu for manuscript.
        self.outliner.setContextMenuPolicy(Qt.CustomContextMenu)
        self.outliner.customContextMenuRequested.connect(
            self._outliner_context_menu)
        self.outliner.itemSelectionChanged.connect(self._update_bulk_button)

        self.views = QTabWidget()
        # The editor's own format bar sits inside the Editor tab, directly
        # above the prose it acts on — bold, italic, style, font and size are
        # editor-scoped, so they vanish with the tab instead of staying live
        # in the window chrome while the corkboard is showing. It flows
        # (2.4h): a QToolBar cannot wrap, so a full bar lost its tail past
        # the editor's width and the alignment buttons sat out of sight in
        # a narrow window; height-for-width is on so the bar wraps — and
        # grows — instead of clipping.
        self.format_bar = QWidget()
        self.format_bar.setObjectName("formatBar")
        formats = FlowLayout(self.format_bar, margin=2)
        policy = self.format_bar.sizePolicy()
        policy.setHeightForWidth(True)
        self.format_bar.setSizePolicy(policy)
        self.editor_page = QWidget()
        editor_layout = QVBoxLayout(self.editor_page)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(0)
        editor_layout.addWidget(self.format_bar)
        editor_layout.addWidget(self.editor, 1)
        self.views.addTab(self.editor_page, "Editor")
        self.views.addTab(self.corkboard, "Corkboard")
        # Outliner page: header bar with expand/collapse + bulk edit
        # visible only when rows are selected (nothing here means
        # nothing to act on, so no dead buttons).
        self.outliner_page = QWidget()
        _ol_layout = QVBoxLayout(self.outliner_page)
        _ol_layout.setContentsMargins(0, 0, 0, 0)
        _ol_layout.setSpacing(0)
        _ol_header = QHBoxLayout()
        self.expand_all_button = QPushButton("Expand all")
        self.collapse_all_button = QPushButton("Collapse all")
        self.bulk_edit_button = QPushButton("Bulk Edit…")
        self.bulk_edit_button.setToolTip("Edit metadata for the multi-selection")
        for _b in (self.expand_all_button, self.collapse_all_button, self.bulk_edit_button):
            _ol_header.addWidget(_b)
        _ol_header.addStretch()
        _ol_layout.addLayout(_ol_header)
        _ol_layout.addWidget(self.outliner, 1)
        self.views.addTab(self.outliner_page, "Outliner")
        self.expand_all_button.clicked.connect(self.outliner.expandAll)
        self.collapse_all_button.clicked.connect(self.outliner.collapseAll)
        self.bulk_edit_button.clicked.connect(self._open_bulk_edit)
        self.bulk_edit_button.setEnabled(False)
        # Page view (3.2): the projection canvas, second tab — it sits
        # next to the editor because it is the same document seen as
        # printed paper. The label says "preview" because the author of
        # the software had to ask what differed from the editor tab
        # (docs/UI-NOTES.md 6).
        self.page_view = PageView()
        self.page_view.jump_requested.connect(self._page_jump)
        self.views.insertTab(1, self.page_view, "Page preview")
        self.views.currentChanged.connect(lambda _index: self._refresh_corkboard())
        self.views.currentChanged.connect(
            lambda index: self._refresh_outliner() if self.views.widget(index) in (self.outliner, self.outliner_page) else None)
        self.views.currentChanged.connect(self._page_view_tab_changed)
        self.views.currentChanged.connect(self._view_tab_changed)

        self.synopsis = QPlainTextEdit()
        self.synopsis.setPlaceholderText("Synopsis…")
        # A synopsis is a paragraph, not a page: the box scrolls instead of
        # growing, so a long one cannot push the editor off-screen (1.16).
        self.synopsis.setMaximumHeight(120)
        self.synopsis.textChanged.connect(self._metadata_changed)

        self.target = QSpinBox()
        self.target.setRange(0, 1_000_000)
        self.target.setSingleStep(100)
        self.target.setSuffix(" words")
        self.target.setSpecialValueText("No goal")
        # It is a goal, not a target: the stored field is word_target, and
        # "section target" made nobody think of a word count (UI-NOTES 5).
        self.target.setToolTip(
            "A word-count goal for this document. Progress shows on the\n"
            "corkboard card and in the status bar. On a folder it counts\n"
            "only until the children's words are added up."
        )
        self.target.valueChanged.connect(self._metadata_changed)

        # Page starts left the inspector for good (1.17): the template's
        # design panel owns them — the design says where its own pages
        # begin — and the values older projects stored keep working
        # silently; the export path still reads them.

        # Book anatomy: folders carry the compile settings (which template
        # dresses them, where the cover is); chapters, scenes and notes
        # pick the set slot that renders each of them. There is no mark to
        # set: a folder is a template by living under templates/ (§1.1).
        self.book_template_label = QLabel("Book template")
        self.book_template_box = QComboBox()
        self.book_template_box.currentIndexChanged.connect(
            self._book_settings_changed)
        # From the consequence to the setting (docs/UI-NOTES.md 6): the
        # row shows a name, so hovering it must say where the name comes
        # from and where it is changed.
        self.book_template_box.setToolTip(
            "Which template dresses this book's chapters. Automatic "
            "follows the export's own choice — the most applicable "
            "template, pre-selected in the export dialog. Picking one "
            "here is what makes a look stick: the dialog's choice is "
            "one-shot and never remembered (docs/TEMPLATES.md §2.1). A "
            "template set is a folder of template files under the "
            "project's templates folder.")
        self.cover_label = QLabel("Cover")
        self.book_cover_edit = QLineEdit()
        self.book_cover_edit.setPlaceholderText("Auto (images/…/cover.*)")
        self.book_cover_edit.editingFinished.connect(self._book_settings_changed)
        self.book_cover_button = QPushButton("Browse…")
        self.book_cover_button.clicked.connect(self._browse_cover)
        cover_help = (
            "The cover image this book's exports use when a format "
            "wants one. It lives on the book folder, so it travels "
            "with the manuscript; empty means auto-detect under images/.")
        self.book_cover_edit.setToolTip(cover_help)
        self.book_cover_button.setToolTip(cover_help)
        cover_row = QHBoxLayout()
        cover_row.addWidget(self.book_cover_edit)
        cover_row.addWidget(self.book_cover_button)
        # 2.2: what this book's [[links]] to documents outside the current
        # export become; Automatic follows the project default.
        self.link_policy_label = QLabel("Unpublished links")
        self.link_policy_box = QComboBox()
        self.link_policy_box.currentIndexChanged.connect(
            self._book_settings_changed)
        # Print geometry (1.15): the button IS the summary — its text
        # shows the current trim/margins, clicking opens the dialog.
        self.page_setup_button = QPushButton("Page setup")
        self.page_setup_button.clicked.connect(self._open_page_setup)
        self.page_setup_button.setToolTip(
            "The paper, trim and margins this book prints with — set "
            "it on the book folder: the nearest book folder wins for "
            "everything inside it, and a series folder can set one "
            "house trim for all its books. Clearing the dialog returns "
            "to the built-in default (A4).")
        self.doc_template_label = QLabel("Apply template")
        self.doc_template_box = RefreshCombo()
        # The box carries the filename, the way the Type box carries a
        # type (docs/TEMPLATES.md §1.2): a pick shows the file, a typed
        # name assigns one that does not exist yet, and nothing is
        # inserted for it — the stored name keeps its own row on the
        # next refresh. editingFinished is where a typed name lands,
        # because the dropdown never moves for typing.
        self.doc_template_box.setEditable(True)
        self.doc_template_box.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.doc_template_box.currentIndexChanged.connect(
            self._doc_template_changed)
        self.doc_template_box.lineEdit().editingFinished.connect(
            self._doc_template_changed)
        self.doc_template_box.aboutToOpen.connect(
            self._refresh_template_choices)
        self.doc_template_box.lineEdit().setPlaceholderText(
            "folder.html — the filename dresses this document")
        self.doc_template_box.setToolTip(
            "Which template dresses this document (1.17). The list holds "
            "every template the project knows: the sets' own slots, the "
            "one-file templates in templates/, and the documents marked "
            "as templates. The box is editable — type any name even "
            "before the file exists; a name no file answers falls back "
            "to the set's own slot for this document's shape. A pick "
            "here is final: it is how THIS document renders, whatever "
            "the book would have chosen. Page size is not part of it — "
            "paper stays a book setting, on the folder.")

        self.duplicate_template_button = QPushButton("Duplicate this template into the project…")
        self.duplicate_template_button.setToolTip(
            "This template ships with Lekhak and cannot be edited in place. "
            "Duplicate it to get an editable copy in your project's "
            "templates/ folder.")
        self.duplicate_template_button.clicked.connect(
            self._duplicate_current_template)

        # The note beside the template dropdown (1.17), worded as the
        # author worded it: the defaults are Lekhak's own and cannot be
        # edited in place, so this is how a copy of them becomes the
        # writer's to change.
        self.copy_defaults_button = QPushButton(
            "Copy default templates to customize")
        self.copy_defaults_button.setToolTip(
            "The templates Lekhak ships with cannot be edited in place. "
            "This puts an editable copy of the default set in your "
            "project's templates/ folder, where you can change it.")
        self.copy_defaults_button.clicked.connect(self._copy_default_templates)

        self.scene_divider_label = QLabel("Divider between child documents")
        self.scene_divider_label.setToolTip(
            "Use this for exceptions. For consistent handling, set the "
            "divider in the template (print-config `Scene divider: …`) — "
            "this field overrides it for this folder only.")
        self.scene_divider_edit = QLineEdit()
        self.scene_divider_edit.setPlaceholderText("* * *  (empty = none)")
        self.scene_divider_edit.setToolTip(self.scene_divider_label.toolTip())
        self.scene_divider_edit.editingFinished.connect(
            self._scene_divider_changed)

        # Type (docs/MODEL.md): the protected role, as an editable
        # dropdown — the novel-writing defaults, plus every Type this
        # project already uses (so a once-typed value, typo included, is
        # selectable rather than re-typed), plus free text. Pre-filled
        # from type_of(), so it is never empty; clearing it returns the
        # item to its derived default. An unrecognised value is fine —
        # template matching falls through to the hardcoded baseline.
        self.type_label = QLabel("Type")
        self.type_label.setToolTip(
            "What this is. Type tells Lekhak how to format and export it — "
            "a folder with Type: Book is a book, Type: Chapter gets a "
            "chapter heading, and ‘Do not print’ with a blank template "
            "stays out of the book. The list is for SELECTING a type; the "
            "box is only for ADDING a new one — check first that what you "
            "want isn't already there, and select it if it is. A type you "
            "add looks the same as a built-in one until you give it its "
            "own template. Empty returns the derived default "
            "(Folder, Document, Chapter…)."
        )
        self.type_box = QComboBox()
        self.type_box.setEditable(True)
        self.type_box.setToolTip(self.type_label.toolTip())
        self.type_box.currentIndexChanged.connect(self._type_changed)
        self.type_box.lineEdit().setPlaceholderText("Folder, Document, Book…")
        self.type_box.lineEdit().editingFinished.connect(self._type_changed)
        self.type_warning = QLabel()
        self.type_warning.setWordWrap(True)
        self.type_warning.setVisible(False)
        self.type_keep_box = QCheckBox("Keep it as a separate type")
        self.type_keep_box.setVisible(False)
        self.type_keep_box.toggled.connect(self._type_keep_toggled)

        # A new role is added here (1.17): the Type box offers the roles
        # its own group knows, and this box is how the writer makes one
        # more available to the documents that follow.
        self.type_add_edit = QLineEdit()
        self.type_add_edit.setPlaceholderText("New type…")
        self.type_add_edit.setToolTip(
            "A new role for this kind of document — say ‘Scene’ or "
            "‘Part’. It joins the Type list for every document of the "
            "same kind, so the documents that follow can pick it.")
        self.type_add_button = QPushButton("Add")
        self.type_add_button.setToolTip(self.type_add_edit.toolTip())
        self.type_add_button.clicked.connect(self._add_type)
        self.type_add_edit.returnPressed.connect(self._add_type)

        # No template switch (retired): a content document cannot be
        # converted to a template. A template starts empty — its FIRST
        # metadata must be Template: yes — and the backend refuses the
        # conversion if the file or other metadata already holds content
        # (see Project.set_template, Preferences Links & export, docs).
        self.template_flag_box = None  # retired; kept only for compat

        # The lock (docs/STORAGE.md): a tick beside the Type row, because
        # a lock is not one of the writer's custom fields — it is a
        # marker file in the tree, and the tick only says whether that
        # file is there. It belongs to the *selected document*, even
        # where the fields below are the project's own.
        self.lock_box = QCheckBox("Locked")
        self.lock_box.setToolTip(
            "Lock this document against saving. A locked folder locks "
            "everything inside it. The lock is a marker file in the tree, "
            "so it travels with a copied folder."
        )
        self.lock_box.toggled.connect(self._lock_toggled)

        # Metadata (1.12): the same Field/Format/Options/Value rows the
        # Edit Metadata dialog edits, inline and live — project-level
        # fields on folders/root, the document's own rows everywhere
        # else. The Type row above owns the protected key, which never
        # appears as a table row.
        self.metadata_table = MetadataTable({})
        # The custom-fields table scrolls inside its box rather than
        # growing: a long list of fields must not push the editor
        # off-screen (1.16).
        self.metadata_table.setMaximumHeight(220)
        self.metadata_table.changed.connect(self._metadata_table_changed)
        self.metadata_table.required_toggled.connect(self._metadata_required_toggled)
        self.metadata_add_button = QPushButton("Add field")
        self.metadata_remove_button = QPushButton("Remove selected")
        self.metadata_add_button.clicked.connect(self.metadata_table.add_field)
        self.metadata_remove_button.clicked.connect(self.metadata_table.remove_selected)

        inspector = QWidget()
        inspector_layout = QVBoxLayout(inspector)
        # The document metadata as one container (§1.2): a template file
        # open in the editor swaps this out for its own panel, and the
        # file stopping being a template brings it straight back — one
        # show/hide rather than a widget at a time.
        self.metadata_section = QWidget()
        metadata_layout = QVBoxLayout(self.metadata_section)
        metadata_layout.setContentsMargins(0, 0, 0, 0)
        # The metadata, in boxes (1.16): it used to be one undifferentiated
        # stack of labels and fields. Four groups give it a logic, and the
        # fold keeps the long ones from pushing the editor off-screen.
        type_row = QHBoxLayout()
        type_row.addWidget(self.type_label)
        type_row.addWidget(self.type_box, 1)
        type_row.addWidget(self.lock_box)
        type_add_row = QHBoxLayout()
        type_add_row.addWidget(self.type_add_edit, 1)
        type_add_row.addWidget(self.type_add_button)
        metadata_buttons = QHBoxLayout()
        metadata_buttons.addWidget(self.metadata_add_button)
        metadata_buttons.addWidget(self.metadata_remove_button)
        metadata_buttons.addStretch()

        # Content related comes first — what is written.
        formatting_fold = Fold("Formatting related", expanded=False)
        self.format_fold = formatting_fold
        formatting_fold.body_layout.addLayout(type_row)
        formatting_fold.body_layout.addLayout(type_add_row)
        formatting_fold.body_layout.addWidget(self.type_warning)
        formatting_fold.body_layout.addWidget(self.type_keep_box)
        # Template selector sits under Type: same dress idea, one fold.
        formatting_fold.body_layout.addWidget(self.book_template_label)
        formatting_fold.body_layout.addWidget(self.book_template_box)
        # Info box for the advanced templates: what types and metadata
        # they expect, so a writer is not left guessing.
        self.book_template_help = QLabel("")
        self.book_template_help.setWordWrap(True)
        self.book_template_help.setStyleSheet("color: gray; font-size: 9pt;")
        formatting_fold.body_layout.addWidget(self.book_template_help)
        formatting_fold.body_layout.addWidget(self.doc_template_label)
        formatting_fold.body_layout.addWidget(self.doc_template_box)
        formatting_fold.body_layout.addWidget(self.copy_defaults_button)
        self.book_title_help = QLabel(
            "Running heads use ${BookTitle}: set a title on this folder "
            "(metadata or the folder's own) and a book template, and the "
            "print config names it ${BookTitle}.")
        self.book_title_help.setWordWrap(True)
        self.book_title_help.setStyleSheet("color: gray; font-size: 9pt;")
        formatting_fold.body_layout.addWidget(self.book_title_help)
        formatting_fold.body_layout.addWidget(self.duplicate_template_button)
        formatting_fold.body_layout.addWidget(self.scene_divider_label)
        formatting_fold.body_layout.addWidget(self.scene_divider_edit)
        formatting_fold.body_layout.addWidget(self.cover_label)
        formatting_fold.body_layout.addLayout(cover_row)
        formatting_fold.body_layout.addWidget(self.page_setup_button)
        formatting_fold.body_layout.addWidget(self.link_policy_label)
        formatting_fold.body_layout.addWidget(self.link_policy_box)
        # Added after Content below

        # Content related (top-level accordion): what is written.
        content_fold = Fold("Content related", expanded=False)
        # Word goal stands alone: one field, not a fold — always visible
        # apart from the accordions so a tracker spots it at a glance.
        self.word_goal_row = QWidget()
        self.word_goal_row.setObjectName("wordGoalRow")
        word_row_layout = QHBoxLayout(self.word_goal_row)
        word_row_layout.setContentsMargins(6, 4, 6, 4)
        self.word_goal_label = QLabel("Word count goal")
        word_row_layout.addWidget(self.word_goal_label)
        word_row_layout.addWidget(self.target, 1)
        # Insert the standalone row above Content (always visible with
        # the metadata, not inside a fold).
        metadata_layout.addWidget(self.word_goal_row)
        synopsis_fold = Fold("Synopsis")
        synopsis_fold.body_layout.addWidget(self.synopsis)
        content_fold.body_layout.addWidget(synopsis_fold)
        custom_fold = Fold("Advanced / Custom", expanded=False)
        custom_fold.body_layout.addWidget(self.metadata_table)
        custom_fold.body_layout.addLayout(metadata_buttons)
        content_fold.body_layout.addWidget(custom_fold)
        metadata_layout.addWidget(content_fold)
        metadata_layout.addWidget(formatting_fold)

        # Related files as the same accordion shape (uniform look).
        related_fold = Fold("Related files", expanded=False)
        self.related_fold = related_fold
        # Related's body is the tree below (wired after tree creation).
        metadata_layout.addWidget(related_fold)

        # Contents (${ToC}): configured on the document that holds the
        # placeholder. Each level gets a marker style; depth is capped at 4.
        toc_fold = Fold("Contents", expanded=True)
        self.toc_fold = toc_fold
        toc_form = QFormLayout()
        self.toc_depth_box = QComboBox()
        for depth in range(1, 5):
            self.toc_depth_box.addItem(str(depth), depth)
        self.toc_depth_box.setCurrentIndex(3)
        self.toc_depth_box.currentIndexChanged.connect(self._toc_config_changed)
        toc_form.addRow("Depth", self.toc_depth_box)
        self.toc_level_boxes: list[QComboBox] = []
        toc_styles = [
            ("None", "none"),
            ("1, 2, 3", "decimal"),
            ("a, b, c", "lower-alpha"),
            ("A, B, C", "upper-alpha"),
            ("i, ii, iii", "lower-roman"),
            ("I, II, III", "upper-roman"),
            ("Bullets", "bullet"),
        ]
        for level in range(1, 5):
            box = QComboBox()
            for label, value in toc_styles:
                box.addItem(label, value)
            box.currentIndexChanged.connect(self._toc_config_changed)
            self.toc_level_boxes.append(box)
            toc_form.addRow(f"Level {level}", box)
        toc_widget = QWidget()
        toc_widget.setLayout(toc_form)
        toc_fold.body_layout.addWidget(toc_widget)
        toc_fold.setVisible(False)
        metadata_layout.addWidget(toc_fold)

        # Media: images attached to this document, kept in the project
        # folder so the document travels with them. A new section rather
        # than a metadata field, because a thumbnail is not a string.
        media_fold = Fold("Media", expanded=False)
        self.media_fold = media_fold
        self.media_thumbs = QHBoxLayout()
        self.media_thumbs.setContentsMargins(4, 4, 4, 4)
        self.media_thumbs.setSpacing(6)
        media_body = QWidget()
        media_body_layout = QVBoxLayout(media_body)
        media_body_layout.setContentsMargins(0, 0, 0, 0)
        thumbs_row = QWidget()
        thumbs_row_layout = QHBoxLayout(thumbs_row)
        thumbs_row_layout.setContentsMargins(0, 0, 0, 0)
        thumbs_row_layout.addLayout(self.media_thumbs)
        thumbs_row_layout.addStretch()
        media_body_layout.addWidget(thumbs_row)
        self.media_add_button = QPushButton("Add image…")
        self.media_add_button.clicked.connect(self.add_media_image)
        media_body_layout.addWidget(self.media_add_button)
        media_fold.body_layout.addWidget(media_body)
        metadata_layout.addWidget(media_fold)

        self.metadata_folds = [content_fold, formatting_fold, related_fold, media_fold]
        metadata_layout.addStretch()
        # The section takes the height it needs; what is left in a tall
        # window belongs to the bottom, not to a stretched box.
        self.metadata_section.setSizePolicy(QSizePolicy.Policy.Preferred,
                                            QSizePolicy.Policy.Maximum)
        inspector_layout.addWidget(self.metadata_section)

        # The template file's own panel (§1.2): hidden until one opens.
        self.template_panel = self._build_template_panel()
        inspector_layout.addWidget(self.template_panel)
        self.template_panel.hide()

        # Related files (1.13): a fresh whole-word title scan shown on
        # view — never stored — as two groups, this document's mentions
        # and the documents that mention it. Hidden until a scan finds
        # something.
        self.related_tree = QTreeWidget()
        self.related_tree.setHeaderHidden(True)
        self.related_tree.itemClicked.connect(self._related_clicked)
        # Related is a Fold like the others — uniform accordion look.
        # Keep the old attribute for _refresh_related; the Fold is the section.
        self.related_fold.body_layout.addWidget(self.related_tree)
        self.related_section = self.related_fold
        self.related_section.setVisible(False)
        # metadata_layout already holds related_fold above — do not add twice.
        # (inspector_layout already has metadata_section which contains it)
        inspector_layout.addStretch()

        # Binder pane: slim header above the tree, not a window toolbar
        # — New/Delete belong to the binder, not to all three columns.
        self.binder_page = QWidget()
        binder_layout = QVBoxLayout(self.binder_page)
        binder_layout.setContentsMargins(0, 0, 0, 0)
        binder_layout.setSpacing(2)
        row1 = QHBoxLayout()
        row1.setSpacing(2)
        for text, slot in (("New Folder", self.add_folder),
                           ("New Document", self.add_scene),
                           ("Delete", self.delete_section)):
            action = QAction(text, self)
            action.triggered.connect(slot)
            btn = QToolButton()
            btn.setDefaultAction(action)
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            btn.setToolTip(text)
            row1.addWidget(btn)
        binder_header = QVBoxLayout()
        binder_header.setSpacing(2)
        binder_header.addLayout(row1)
        row2 = QHBoxLayout()
        row2.setSpacing(4)
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.returnPressed.connect(self.search)
        self.search_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        row2.addWidget(self.search_box, 1)
        self.search_button = QPushButton("Search")
        self.search_button.clicked.connect(self.search)
        row2.addWidget(self.search_button)
        binder_header.addLayout(row2)
        binder_layout.addLayout(binder_header)
        binder_layout.addWidget(self.tree, 1)

        splitter = QSplitter()
        splitter.addWidget(self.binder_page)
        splitter.addWidget(self.views)
        splitter.addWidget(inspector)
        splitter.setSizes([250, 760, 270])
        self.setCentralWidget(splitter)

        # (Writing toolbar retired into the binder header above —
        # binder actions belong to the binder, not the window chrome.)

        # Prose formatting goes into the Editor tab's bar (see editor_page
        # above), not the window toolbar: these commands act on the open
        # document only, and the window toolbar outlives the editor tab.
        # Every control is a QToolButton carrying its own QAction — the
        # action keeps the slot, the checkable state and the tooltip, the
        # flow keeps them all on screen at any width (2.4h).
        def _format_button(text, slot, *, checkable=False, tip=""):
            action = QAction(text, self)
            action.triggered.connect(slot)
            if checkable:
                action.setCheckable(True)
            if tip:
                action.setToolTip(tip)
            button = QToolButton()
            button.setDefaultAction(action)
            formats.addWidget(button)
            return action

        _format_button("B", self.bold)
        _format_button("I", self.italic)
        _format_button("U", self.underline)
        self.style_box = QComboBox()
        self.style_box.activated.connect(self.apply_paragraph_style)
        # The last real pick, so Update re-captures the style the box was
        # on before its own command was chosen (2.4h).
        self.style_box.currentIndexChanged.connect(
            self._style_box_selection_tracked)
        self._rebuild_style_box()
        formats.addWidget(self.style_box)
        self.font_box = QFontComboBox()
        def _font_changed(font) -> None:
            # Changing the family must not rewrite the size: the combo's
            # own point size (9–12) is not the document's. Preserve the
            # live display size so the size box stays truthful.
            size = self.editor.textCursor().charFormat().fontPointSize()
            new = QFont(font)
            if size > 0:
                new.setPointSizeF(size)
            self.editor.setCurrentFont(new)
        self.font_box.currentFontChanged.connect(_font_changed)
        formats.addWidget(self.font_box)
        self.size_box = QComboBox()
        self.size_box.setEditable(True)
        self.size_box.addItems(["9", "10", "11", "12", "14", "16", "18", "24", "32"])
        self.size_box.setCurrentText("12")
        self.size_box.currentTextChanged.connect(self.set_font_size)
        formats.addWidget(self.size_box)
        _format_button("• List", self.bullet_list)
        _format_button("1. List", self.numbered_list)
        # Alignment (2.4h): one checkable action per alignment, the
        # checked one following the caret's paragraph — the buttons say
        # what the paragraph is, and a mixed selection checks nothing.
        self._alignment_actions = {}
        for label, alignment, tip in (
                ("Left", Qt.AlignmentFlag.AlignLeft,
                 "Align the paragraph left"),
                ("Centre", Qt.AlignmentFlag.AlignCenter,
                 "Centre the paragraph"),
                ("Right", Qt.AlignmentFlag.AlignRight,
                 "Align the paragraph right"),
                ("Justify", Qt.AlignmentFlag.AlignJustify,
                 "Justify the paragraph")):
            action = _format_button(
                label,
                lambda *_args, align=alignment: self._set_paragraph_alignment(align),
                checkable=True, tip=tip)
            self._alignment_actions[alignment] = action
        _format_button("Clear", self.clear_formatting,
                       tip="Drop this paragraph back to plain")
        _format_button("Read aloud", self.speak_aloud,
                       tip="Read the selection aloud — or from the cursor, "
                       "or from the start when the caret is not in the text.")
        self.ai_buttons: list[QAction] = [
            _format_button(
                "Proofread", self._ai_proofread_selection,
                tip="Proofread the selection (or the scene) with the "
                "standard prompt; changes land as tracked edits."),
            _format_button(
                "Prompt…", self._ai_prompt,
                tip="Ask the model a question; the answer comes back "
                "as a tracked insertion at the caret."),
        ]
        # Zoom readout (2.x): display magnification of the writing area,
        # driven by Ctrl+scroll over the text and the View menu. It is a
        # readout rather than a setting — the value itself lives in the
        # project's session state beside last_document.
        self.zoom_label = QLabel(f"{round(self.zoom * 100)}%")
        self.zoom_label.setToolTip(
            "Editor zoom — display only; the document keeps its own sizes.\n"
            "Ctrl+scroll over the text, Ctrl+0 to reset."
        )
        formats.addWidget(self.zoom_label)

        self.setStatusBar(QStatusBar())
        # Word counts and goal progress ride a permanent widget (BUILD-NOW
        # 6). A showMessage would be displaced by the next "Saved" notice
        # and, once its timeout ran out, QStatusBar clears to nothing —
        # the daily goal has to stay readable while other messages pass.
        self.word_count_label = QLabel("")
        self.statusBar().addPermanentWidget(self.word_count_label)
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(int(QSettings().value(
            "editor/autosaveMs", 700, type=int)))
        self.save_timer.timeout.connect(self.save_current)
        # Spell squiggles (8): a debounced dictionary pass, off by
        # default — a fresh project lights up with invented names until
        # there is a project dictionary to feed it.
        self.spell_enabled = QSettings().value(
            "ui/spellSquiggles", False, type=bool)
        self._spell_cache: dict[str, bool] = {}
        self.spell_timer = QTimer(self)
        self.spell_timer.setSingleShot(True)
        self.spell_timer.setInterval(500)
        self.spell_timer.timeout.connect(self._update_spell_squiggles)
        self._build_menu()
        self._apply_interface_style()
        self._set_project_enabled(False)

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        self._action(file_menu, "&New Project…", QKeySequence.New, self.new_project)
        self._action(file_menu, "&Open Project…", QKeySequence.Open, self.open_project)
        # Open Recent (BUILD-NOW 1): filled on demand, so a project deleted
        # on disk between two openings never offers a broken entry.
        self.recent_menu = file_menu.addMenu("Open &Recent")
        self.recent_menu.aboutToShow.connect(self._populate_recent_menu)
        # The guide is the software's own manual, as a project: browsable
        # in the binder and exportable like any other.
        self._action(file_menu, "Open the Lekhak Guide…", None,
                     self.open_guide)
        self._action(file_menu, "Save Project As…", None,
                     self.save_project_as)
        # Ctrl+P stays with Print (item 4): the two BUILD-NOW items asked
        # for the same key, and Print is the one every other program owns.
        self.quick_open_action = self._action(
            file_menu, "&Quick Open…", QKeySequence("Ctrl+Shift+P"),
            self.quick_open)
        self.print_action = self._action(
            file_menu, "&Print…", QKeySequence.Print, self.print_manuscript)
        self._action(file_menu, "&Save and Back Up", QKeySequence.Save, self.manual_save)
        self._action(file_menu, "Choose Backup Folder…", None, self.choose_backup_folder)
        file_menu.addSeparator()
        self._action(file_menu, "Export Plain Text…", None, lambda: self.export("text"))
        self._action(file_menu, "Export HTML…", None, lambda: self.export("html"))
        self._action(file_menu, "Export DOCX…", None, lambda: self.export("docx"))
        self.export_pdf_action = self._action(
            file_menu, "Export PDF…", None, lambda: self.export("pdf"))
        self.export_pdf_action.setToolTip(pdf.pdf_export_help())
        self.export_pdf_action.setStatusTip(pdf.pdf_export_help())
        self._action(file_menu, "Export EPUB…", None, lambda: self.export("epub"))
        file_menu.addSeparator()
        self._action(file_menu, "Import Markdown...", None,
                     self.import_markdown)
        self._action(file_menu, "Import from Lekhak Project…", None,
                     self.import_from_project)
        file_menu.addSeparator()
        self._action(file_menu, "Quit", QKeySequence.Quit, self.close)

        edit_menu = self.menuBar().addMenu("&Edit")
        self._action(edit_menu, "Undo", QKeySequence.Undo, self.editor.undo)
        self._action(edit_menu, "Redo", QKeySequence.Redo, self.editor.redo)
        edit_menu.addSeparator()
        self._action(edit_menu, "Cut", QKeySequence.Cut, self.editor.cut)
        self._action(edit_menu, "Copy", QKeySequence.Copy, self.editor.copy)
        self._action(edit_menu, "Paste", QKeySequence.Paste, self.editor.paste)
        self._action(edit_menu, "Paste as Plain Text", "Ctrl+Shift+V",
                     self.editor.paste_as_plain_text)
        self._action(edit_menu, "Paste and Keep All Formatting", None,
                     self.editor.paste_keep_all)
        edit_menu.addSeparator()
        # The writer's own date, twice over (BUILD-NOW 5): into the prose
        # in the system locale's words, and into the document's Date
        # metadata as the ISO value the inspector's date widget stores —
        # one action, so the day is never typed by hand twice.
        self._action(edit_menu, "Insert Date", None,
                     lambda: self.insert_date(with_time=False))
        self._action(edit_menu, "Insert Date and Time", None,
                     lambda: self.insert_date(with_time=True))
        self._action(edit_menu, "Insert Page Break", "Ctrl+Enter",
                     self.insert_page_break)
        self._action(edit_menu, "Insert Image…", None,
                     self.insert_image)
        edit_menu.addSeparator()
        # the passes dialog sits between them and per-change granularity.
        self.track_action = QAction("Track Changes", self)
        self.track_action.setCheckable(True)
        self.track_action.setShortcut("Ctrl+Shift+T")
        self.track_action.toggled.connect(self._toggle_tracking)
        edit_menu.addAction(self.track_action)
        self._action(edit_menu, "Accept All Changes in Section", None,
                     self.accept_all_changes)
        self._action(edit_menu, "Reject All Changes in Section", None,
                     self.reject_all_changes)
        self._action(edit_menu, "Revision Passes…", None,
                     self.revision_passes)
        edit_menu.addSeparator()
        self._action(edit_menu, "Find / Replace in Section…", QKeySequence.Find, self.find_replace)
        self._action(edit_menu, "Find in Project", "Ctrl+Shift+F", self.search_box.setFocus)
        self._action(edit_menu, "Replace in Project…", "Ctrl+Shift+H",
                     self.replace_in_project)
        self._action(edit_menu, "Standardise…", None,
                     self.show_standardise_dialog)
        edit_menu.addSeparator()
        self._action(edit_menu, "Preferences…", "Ctrl+,", self.preferences)

        view_menu = self.menuBar().addMenu("&View")
        self._action(view_menu, "Zoom In", QKeySequence.ZoomIn, self.zoom_in)
        self._action(view_menu, "Zoom Out", QKeySequence.ZoomOut, self.zoom_out)
        self._action(view_menu, "Reset Zoom", "Ctrl+0", self.reset_zoom)
        self._action(view_menu, "Focus Mode", "F11", self.toggle_focus_mode)
        # The template sets (docs/TEMPLATES.md §1.1): they are folders on
        # disk, not documents, so they are listed here rather than in the
        # binder — the binder is the writer's own tree.
        self._action(view_menu, "Template Sets", None, self.show_template_sets)
        # Spell squiggles (8): deterministic dictionary pass — no model,
        # no network; red wavy underlines, off until asked for.
        self.spell_action = QAction("Spell-check Squiggles", self)
        self.spell_action.setCheckable(True)
        self.spell_action.setChecked(self.spell_enabled)
        self.spell_action.setToolTip(
            "Red wavy underlines for words the dictionary rejects "
            "(aspell or hunspell) — typos only; no model, no network.")
        self.spell_action.toggled.connect(self._toggle_spell_squiggles)
        view_menu.addAction(self.spell_action)

        document_menu = self.menuBar().addMenu("&Document")
        self._action(document_menu, "Split at Cursor", "Ctrl+K", self.split_section)
        self._action(document_menu, "Merge with Next Sibling", None,
                     self.merge_with_next)
        document_menu.addSeparator()
        self._action(document_menu, "Edit Metadata…", None, self.edit_metadata)
        self._action(document_menu, "Outliner Columns…", None, self.pick_outliner_columns)
        self._action(document_menu, "Create Snapshot…", None, self.create_snapshot)
        self._action(document_menu, "Restore Snapshot…", None, self.restore_snapshot)
        self._action(document_menu, "Link to Section…", "Ctrl+L", self._link_section)
        document_menu.addSeparator()
        self.set_daily_goal_action = self._action(
            document_menu, "Set Daily Goal…", "Ctrl+G", self.set_session_goal)

        # Read aloud (3.1): selection, else the whole scene, through
        # Qt's QTextToSpeech; the voice and rate live in Preferences.
        speak_menu = self.menuBar().addMenu("&Speak")
        self._action(speak_menu, "Read Aloud", "F8", self.speak_aloud)
        self._action(speak_menu, "Pause / Resume", "F9",
                     self.toggle_speech_pause)
        self._action(speak_menu, "Stop", None, self.stop_speech)
        self.speak_menu = speak_menu

        # AI (2.4, layer 2): a thin menu over the OpenAI-compatible
        # client; output only ever lands via the tracked-changes path.
        ai_menu = self.menuBar().addMenu("&AI")
        self._action(ai_menu, "Continue Scene", None,
                     self._ai_continue_scene)
        self.proofread_action = self._action(
            ai_menu, "Proofread Selection", None,
            self._ai_proofread_selection)
        self.proofread_action.setToolTip(
            "Bloopers, not prose: typos, grammar, doubled words and "
            "stray spaces as the minimal edit — always applied as a "
            "tracked change for you to accept or reject.")
        self.ai_menu = ai_menu
        self._refresh_optional_menus()

    def _refresh_optional_menus(self) -> None:
        """Speak and AI only exist while their boxes are ticked —
        unticked means the menus do not bother to appear, at all.
        The editor bar's AI buttons follow the AI menu, so bar and
        menu agree about what's present."""
        store = QSettings()
        self.speak_menu.setVisible(
            bool(store.value("tts/enabled", False, type=bool)))
        ai_on = bool(store.value("ai/enabled", False, type=bool))
        self.ai_menu.setVisible(ai_on)
        for action in self.ai_buttons:
            action.setVisible(ai_on)

    def _action(self, menu, text, shortcut, callback) -> QAction:
        action = QAction(text, self)
        if shortcut:
            action.setShortcut(shortcut)
        action.triggered.connect(callback)
        menu.addAction(action)
        return action

    def _set_project_enabled(self, enabled: bool) -> None:
        self.quick_open_action.setEnabled(enabled)
        self.print_action.setEnabled(enabled)
        self.set_daily_goal_action.setEnabled(enabled)
        self.tree.setEnabled(enabled)
        self.editor.setEnabled(enabled)
        self.page_view.setEnabled(enabled)
        self.corkboard.setEnabled(enabled)
        self.synopsis.setEnabled(enabled)
        self.book_template_box.setEnabled(enabled)
        self.book_cover_edit.setEnabled(enabled)
        self.book_cover_button.setEnabled(enabled)
        self.page_setup_button.setEnabled(enabled)
        self.link_policy_box.setEnabled(enabled)
        self.doc_template_box.setEnabled(enabled)
        self.type_box.setEnabled(enabled)
        self.metadata_table.setEnabled(enabled)
        self.metadata_add_button.setEnabled(enabled)
        self.metadata_remove_button.setEnabled(enabled)
        self.related_tree.setEnabled(enabled)

    def new_project(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Choose project location")
        if not selected:
            return
        title, accepted = QInputDialog.getText(self, "Project title", "Title:")
        if not accepted or not title.strip():
            return
        try:
            project = Project.create(Path(selected) / title.strip(), title.strip())
            self._switch_project(project)
        except Exception as error:
            QMessageBox.critical(self, "Could not create project", str(error))

    def print_manuscript(self) -> None:
        """BUILD-NOW 4: print through the PDF path the export already uses.

        No save dialog and no template dialog: the stored template set
        decides pagination — the same numbers an export would produce —
        and the scope is whatever the export would take (a binder or
        outliner selection, else the whole project). The PDF is built in
        a temp folder that print_via_desktop removes once the handler
        exits; every failure path removes it immediately.
        """
        if not self.project:
            self.statusBar().showMessage("Open a project first", 3000)
            return
        if not self.save_current():
            return                      # the dialog has explained
        raw_ids = self._gather_export_selection()
        # Print is the export dialog's answer without the dialog (§2):
        # the most applicable template for this scope. It used to borrow
        # whichever set the last export happened to pick — a preference
        # the writer never made for printing.
        _sets, templates, _warnings = self.project.template_choices(raw_ids)
        folder = Path(tempfile.mkdtemp(prefix="lekhak-print-"))
        target = folder / "lekhak-print.pdf"
        try:
            pdf.export_pdf(self.project, target, raw_ids, templates)
            print_via_desktop(target)
        except Exception as error:
            shutil.rmtree(folder, ignore_errors=True)
            QMessageBox.critical(self, "Print failed", str(error))
            return
        self.statusBar().showMessage("Handed to the system print flow", 5000)

    def insert_page_break(self) -> None:
        """Insert a manual page break at the caret (1.x).

        A new paragraph whose block format carries
        page-break-before: always — the editor (and Page preview)
        paginates there, the stored HTML carries the style, and
        DOCX/EPUB/HTML exports turn it into a real page turn. No
        new document, no Type change: a poem can sit inside a scene
        and still own a page.
        """
        cursor = self.editor.textCursor()
        if cursor.hasSelection():
            cursor.deleteChar()
        cursor.beginEditBlock()
        cursor.insertBlock()
        block_fmt = QTextBlockFormat()
        block_fmt.setPageBreakPolicy(QTextBlockFormat.PageBreakFlag.PageBreak_AlwaysBefore)
        cursor.setBlockFormat(block_fmt)
        cursor.endEditBlock()
        self._content_changed()

    def insert_image(self) -> None:
        """Insert an image at the caret, with basic alignment.

        The image is copied into the project's ``images/`` folder so the
        document stays portable; the stored HTML carries a relative path.
        Alignment is the only layout on offer — left, right, centre, or
        inline — because anything beyond that is a different kind of
        software.
        """
        if not self.project or not self.current_id:
            self.statusBar().showMessage(
                "Open a document before inserting an image.", 3000)
            return
        if self._project_is_read_only():
            return
        if not self.save_current():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Insert image", "",
            "Images (*.png *.jpg *.jpeg *.gif *.webp *.bmp);;All files (*)")
        if not path:
            return
        source = Path(path)
        if not source.is_file():
            return
        # Copy into the project so the document travels with its image.
        target_dir = self.project.root / "images" / self.current_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / source.name
        # Avoid clobbering an existing file of the same name.
        if target.exists() and target.read_bytes() != source.read_bytes():
            stem, suffix = source.stem, source.suffix
            counter = 1
            while True:
                candidate = target_dir / f"{stem}-{counter}{suffix}"
                if not candidate.exists():
                    target = candidate
                    break
                counter += 1
        try:
            shutil.copyfile(source, target)
        except OSError as error:
            QMessageBox.critical(self, "Could not copy image", str(error))
            return
        relative = target.relative_to(self.project.root).as_posix()

        # Ask how it sits.
        dialog = QDialog(self)
        dialog.setWindowTitle("Insert image")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(f"Image: {source.name}"))
        combo = QComboBox()
        for label in ("Inline (with the text)", "Left", "Right", "Centre"):
            combo.addItem(label)
        layout.addWidget(QLabel("Alignment"))
        layout.addWidget(combo)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        alignment = combo.currentText()

        cursor = self.editor.textCursor()
        if cursor.hasSelection():
            cursor.deleteChar()
        cursor.beginEditBlock()
        if alignment == "Left":
            cursor.insertHtml(
                f'<img src="{relative}" '
                f'style="float: left; margin: 0 1em 1em 0;" />')
        elif alignment == "Right":
            cursor.insertHtml(
                f'<img src="{relative}" '
                f'style="float: right; margin: 0 0 1em 1em;" />')
        elif alignment == "Centre":
            cursor.insertBlock()
            block_fmt = QTextBlockFormat()
            block_fmt.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cursor.setBlockFormat(block_fmt)
            cursor.insertHtml(f'<img src="{relative}" />')
        else:
            cursor.insertHtml(f'<img src="{relative}" />')
        cursor.endEditBlock()
        self._content_changed()
        self.statusBar().showMessage(f"Inserted {source.name}", 3000)

    def _refresh_media(self) -> None:
        """Show the images attached to the current document."""
        # Clear the thumbnails.
        while self.media_thumbs.count():
            item = self.media_thumbs.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if not self.project or not self.current_id:
            return
        document = next((d for d in self.project.documents()
                         if d.id == self.current_id), None)
        if document is None:
            return
        paths = [p.strip() for p in
                 self.project.metadata(document.id).get("Media", "").split(",")
                 if p.strip()]
        for relative in paths:
            full = self.project.root / relative
            if not full.is_file():
                continue
            label = QLabel()
            pixmap = QPixmap(str(full))
            if not pixmap.isNull():
                label.setPixmap(pixmap.scaled(
                    64, 64, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation))
            label.setToolTip(relative)
            self.media_thumbs.addWidget(label)

    def add_media_image(self) -> None:
        """Attach an image to the current document."""
        if not self.project or not self.current_id:
            self.statusBar().showMessage(
                "Open a document before adding an image.", 3000)
            return
        if self._project_is_read_only():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Add image", "",
            "Images (*.png *.jpg *.jpeg *.gif *.webp *.bmp);;All files (*)")
        if not path:
            return
        source = Path(path)
        if not source.is_file():
            return
        target_dir = self.project.root / "images" / self.current_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / source.name
        if target.exists() and target.read_bytes() != source.read_bytes():
            stem, suffix = source.stem, source.suffix
            counter = 1
            while True:
                candidate = target_dir / f"{stem}-{counter}{suffix}"
                if not candidate.exists():
                    target = candidate
                    break
                counter += 1
        try:
            shutil.copyfile(source, target)
        except OSError as error:
            QMessageBox.critical(self, "Could not copy image", str(error))
            return
        relative = target.relative_to(self.project.root).as_posix()
        # Append to the Media metadata key.
        existing = self.project.metadata(self.current_id).get("Media", "")
        paths = [p.strip() for p in existing.split(",") if p.strip()]
        paths.append(relative)
        self.project.set_metadata(
            self.current_id, {**self.project.metadata(self.current_id),
                             "Media": ",".join(paths)})
        self._refresh_media()
        self.statusBar().showMessage(f"Added {source.name}", 3000)

    def insert_date(self, with_time: bool = False) -> None:
        """BUILD-NOW 5: today at the caret, with the Date metadata fed too.

        The prose gets the system locale's own words ("22 October 2026"),
        optionally followed by the short time; the document's ``Date``
        metadata gets the ISO value the inspector's date widget parses —
        merged into the existing rows, because set_metadata replaces the
        whole set. One edit block: a single undo takes the whole thing
        back out.
        """
        document = self._current_document()
        if not self.project or document is None:
            self.statusBar().showMessage("Open a document first", 3000)
            return
        if document.kind == "folder":
            self.statusBar().showMessage(
                "Folders have no text to date", 3000)
            return
        date = QDate.currentDate()
        locale = QLocale.system()
        text = locale.toString(date, QLocale.FormatType.LongFormat)
        if with_time:
            text += ", " + locale.toString(
                QTime.currentTime(), QLocale.FormatType.ShortFormat)
        cursor = self.editor.textCursor()
        cursor.beginEditBlock()
        cursor.insertText(text)
        cursor.endEditBlock()
        self.editor.setTextCursor(cursor)   # caret after the date: keep typing
        # Feed Date beside whatever metadata the document already carries.
        entries = self.project.metadata_entries(self.current_id)
        values = {key: row[0] for key, row in entries.items()}
        types = {key: row[1] for key, row in entries.items()}
        options = {key: row[2] for key, row in entries.items()}
        values["Date"] = date.toString(Qt.ISODate)
        if "Date" not in types:
            types["Date"] = "date"     # the inspector's date widget
            options["Date"] = []
        self.project.set_metadata(self.current_id, values, types, options)
        self._populate_metadata_table()
        self.statusBar().showMessage(f"Inserted {text}", 5000)

    def quick_open(self) -> None:
        """BUILD-NOW 2: jump to a document by title.

        Navigate only — the palette selects and nothing else. With no
        project open there is no binder to search and no palette to show.
        """
        if not self.project:
            self.statusBar().showMessage("Open a project first", 3000)
            return
        entries = [(d.id, d.title) for d in self.project.documents()]
        dialog = QuickOpenDialog(entries, self)
        if not dialog.exec() or not dialog.chosen_id:
            return
        self._jump_to(dialog.chosen_id)

    def _jump_to(self, document_id: str) -> None:
        """Land on the target: binder selection, then the view it lives in.

        A document brings the editor forward (whatever tab was showing);
        a folder lets the folder-view logic take over, so a corkboard
        lover stays on the corkboard.
        """
        self._select_document(document_id)
        document = next(
            (d for d in self.project.documents() if d.id == document_id), None
        )
        if document is not None and document.kind != "folder":
            self.views.setCurrentWidget(self.editor_page)
            self.editor.setFocus()
        else:
            self.views.currentWidget().setFocus()

    def open_project(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Open .lekhak project")
        if not selected:
            return
        self._open_path(selected)

    def _open_path(self, selected: str) -> None:
        """Open a project from a path — the dialog and Open Recent share it.

        A folder that is already gone when the entry is clicked (deleted
        between menu build and click) is pruned from the list and reported
        in the status bar; there is nothing to open and no error to raise.
        """
        if not Path(selected).is_dir():
            self.statusBar().showMessage(
                f"{selected} no longer exists", 5000)
            entries = [p for p in self._recent_projects() if p != selected]
            QSettings().setValue("session/recentProjects", entries)
            return
        try:
            project = Project(Path(selected))
            project.open()
            self._switch_project(project)
        except Exception as error:
            QMessageBox.critical(self, "Could not open project", str(error))

    def _recent_projects(self) -> list[str]:
        raw = QSettings().value("session/recentProjects", [])
        if raw is None:
            return []
        if isinstance(raw, str):
            raw = [raw] if raw else []
        return [str(entry) for entry in raw]

    def _record_recent(self, path: str) -> None:
        """Newest first, no duplicates, eight at most.

        Called only from _switch_project's tail — a switch the writer
        cancelled (failed save) records nothing.
        """
        entries = [p for p in self._recent_projects() if p != path]
        entries.insert(0, path)
        QSettings().setValue("session/recentProjects", entries[:8])

    def _populate_recent_menu(self) -> None:
        self.recent_menu.clear()
        entries = self._recent_projects()
        live = [p for p in entries if Path(p).is_dir()]
        if live != entries:
            # The folder is gone: drop it now, so the list agrees with
            # disk rather than offering a broken entry.
            QSettings().setValue("session/recentProjects", live)
        if not live:
            empty = self.recent_menu.addAction("No recent projects")
            empty.setEnabled(False)
            return
        names = []
        for path in live:
            base = Path(path).name
            label = base[:-len(".lekhak")] if base.endswith(".lekhak") else base
            names.append(label or path)
        for path, name in zip(live, names):
            label = name if names.count(name) == 1 else f"{name} — {Path(path).parent.name}"
            action = self.recent_menu.addAction(label)
            action.setToolTip(path)
            action.triggered.connect(
                lambda _checked=False, p=path: self._open_path(p))

    def _resolve_structure_drift(self, project: Project) -> bool:
        """Ask the writer which side is right when the project's records
        disagree — before anything is synced or rewritten.

        Returns False when the writer cancels: the switch is aborted and
        nothing was changed (a backup was taken first, so even a wrong
        choice is recoverable).
        """
        problems = [problem for problem in project.validate_alignment()
                    if problem != INSIDE_BACKUPS_PROBLEM]
        print('DRIFT_PROBLEMS:', repr(problems), flush=True)
        if problems:
            try:
                project.backup()
            except Exception as error:
                self.statusBar().showMessage(
                    f"Could not take a backup before reconciling: {error}", 8000)
            choice = self._drift_dialog(problems)
            if choice is None:
                return False
            if choice == "manifest":
                project.apply_manifest()
            elif choice == "order":
                project.apply_order_files()
            project.sync_with_disk()
            project.reconcile()
            self.statusBar().showMessage(
                "Structure reconciled — a backup was taken first.", 5000)

        # Identity/title drift: the file's head title and the project
        # disagree. Report separately — automated links may break naturally
        # and manual/hardcoded links may need updating by the writer.
        identity = project.identity_drift()
        if identity:
            try:
                project.backup()
            except Exception:
                pass
            if not self._resolve_identity_drift(project, identity):
                return False

        # Metadata drift: a file's head and the project's record disagree
        # (edited outside Lekhak). Report it now, while it is fresh, and let
        # the writer say which side is right — never silently fixed.
        drift = project.metadata_drift()
        if drift:
            try:
                project.backup()
            except Exception:
                pass
            if not self._resolve_metadata_drift(project, drift):
                return False
        return True

    def _drift_dialog(self, problems: list[str]) -> str | None:
        """Which side is right? One popup, three truths, a Submit.

        Returns "database", "manifest" or "order" — or None when cancelled.
        """
        dialog = QDialog(self)
        dialog.setWindowTitle("Structure mismatch")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(
            "The database, the order files and the files on disk disagree "
            "about this project's structure. Nothing has been changed yet.\n"
            "A backup was taken, so whichever you choose you can undo it.\n"
            "Which should win?"))
        details = QPlainTextEdit()
        details.setReadOnly(True)
        details.setPlainText("\n".join(problems))
        # Capped height and wrapped text: many problems scroll inside the
        # box instead of pushing the buttons off the screen.
        details.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        details.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        details.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        details.setMaximumHeight(160)
        details.setMinimumHeight(40)
        layout.addWidget(details)
        choices = (
            ("database",
             "The database is right — rewrite manifest.json and .order.json from it"),
            ("manifest",
             "manifest.json is right — follow the root order it holds"),
            ("order",
             "The .order.json files are right — follow each folder's own sequence"),
        )
        radios: dict[str, QRadioButton] = {}
        for value, label in choices:
            radio = QRadioButton(label)
            radios[value] = radio
            layout.addWidget(radio)
        radios["database"].setChecked(True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Submit")
        submit = buttons.button(QDialogButtonBox.StandardButton.Ok)
        submit.clicked.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return next((value for value, radio in radios.items()
                     if radio.isChecked()), None)

    def _resolve_metadata_drift(self, project: Project, drift) -> bool:
        """Resolve file-vs-project disagreements for the writer's fields.

        Shown when detected so it is fresh. Each differing field shows both
        values; the writer can decide per field or accept all from one side
        for a document. Returns False if cancelled.
        """
        from collections import defaultdict
        by_doc: dict[str, list[tuple[str, str, str, str]]] = defaultdict(list)
        for doc_id, title, key, file_val, proj_val in drift:
            by_doc[doc_id].append((title, key, file_val, proj_val))

        dialog = QDialog(self)
        dialog.setWindowTitle("Metadata differs between file and project")
        layout = QVBoxLayout(dialog)
        intro = QLabel(
            "A document's file and the project disagree about some fields. "
            "This can happen when a file was edited outside Lekhak. Nothing "
            "has been changed yet.\n\n"
            "For each field, choose which value to keep. If both are partly "
            "right, pick the closest one and fix the rest by hand afterwards.")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        # choices[(doc_id, key)] = QComboBox with data "file"/"project"
        choices: dict[tuple[str, str], QComboBox] = {}
        for doc_id, fields in by_doc.items():
            title = fields[0][0]
            group = QGroupBox(title)
            g_layout = QVBoxLayout(group)
            for _title, key, file_val, proj_val in fields:
                row = QHBoxLayout()
                row.addWidget(QLabel(f"<b>{key}</b>"))
                file_label = QLabel(f"File: {file_val}")
                file_label.setWordWrap(True)
                file_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
                proj_label = QLabel(f"Project: {proj_val}")
                proj_label.setWordWrap(True)
                proj_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
                row.addWidget(file_label, 1)
                row.addWidget(proj_label, 1)
                box = QComboBox()
                box.addItem("Use file", "file")
                box.addItem("Use project", "project")
                choices[(doc_id, key)] = box
                row.addWidget(box)
                w = QWidget()
                w.setLayout(row)
                g_layout.addWidget(w)
            btn_row = QHBoxLayout()
            all_file = QPushButton("Accept all from file")
            all_proj = QPushButton("Accept all from project")
            def _set_all(did=doc_id, val="file"):
                for (d, _k), b in choices.items():
                    if d == did:
                        b.setCurrentIndex(b.findData(val))
            all_file.clicked.connect(lambda _c=False, d=doc_id: _set_all(d, "file"))
            all_proj.clicked.connect(lambda _c=False, d=doc_id: _set_all(d, "project"))
            btn_row.addWidget(all_file)
            btn_row.addWidget(all_proj)
            btn_row.addStretch()
            bw = QWidget()
            bw.setLayout(btn_row)
            g_layout.addWidget(bw)
            layout.addWidget(group)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Apply")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False

        # Apply choices per field.
        # "File wins": copy file value into project for that field.
        # "Project wins": rewrite head from current project values.
        from collections import defaultdict as _dd
        file_wins: dict[str, list[str]] = _dd(list)
        project_wins_docs: set[str] = set()
        for (doc_id, key), box in choices.items():
            if box.currentData() == "file":
                file_wins[doc_id].append(key)
            else:
                project_wins_docs.add((doc_id, key))
        # Group project-wins by doc
        proj_wins_by_doc: dict[str, list[str]] = _dd(list)
        for doc_id, key in project_wins_docs:
            proj_wins_by_doc[doc_id].append(key)

        for doc_id, keys in file_wins.items():
            for key in keys:
                project.apply_file_metadata(doc_id, key)
        # For fields where project wins, ensure head matches project:
        # re-save current metadata to refresh head.
        docs_needing_refresh = set(proj_wins_by_doc.keys()) - set(file_wins.keys())
        # If mixed (some file, some project) for same doc, apply_file_metadata
        # already saved; for project-win fields in mixed case the project value
        # was already in DB, so we need to ensure head is rewritten from DB.
        for doc_id in set(file_wins.keys()) | set(proj_wins_by_doc.keys()):
            entries = project.metadata_entries(doc_id)
            project.set_metadata(
                doc_id,
                {name: value for name, (value, _t, _o) in entries.items()},
                {name: vtype for name, (_v, vtype, _o) in entries.items()},
                {name: opts for name, (_v, _t, opts) in entries.items()})
        self.statusBar().showMessage("Metadata reconciled.", 5000)
        return True


    def _resolve_identity_drift(self, project: Project, drift) -> bool:
        """Resolve title/identity divergence between file head and project.

        Reported separately from metadata drift. Automated links may no
        longer resolve after a title/identity change; manual or hardcoded
        [[links]] in text may break and are only updated if the writer
        explicitly opts in to a project-wide find/replace.
        """
        dialog = QDialog(self)
        dialog.setWindowTitle("Title or identity differs between file and project")
        layout = QVBoxLayout(dialog)
        intro = QLabel(
            "Some documents disagree about their title/identity between the "
            "file and the project. This can happen when files were edited or "
            "renamed outside Lekhak. Nothing has been changed yet.\n\n"
            "Consequences: automated links may no longer find the document "
            "naturally after an identity change. Manual or hardcoded links "
            "(for example [[Old title]] written in text) may break. Lekhak "
            "can optionally update those hardcoded wiki-link texts "
            "project-wide if you choose; anything else must be fixed by hand.")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        # entries: (doc_id, file_title, proj_title, choice_box, update_links_cb)
        entries = []
        for doc_id, file_title, proj_title in drift:
            group = QGroupBox(proj_title or file_title or doc_id)
            g = QVBoxLayout(group)
            row = QHBoxLayout()
            f_lbl = QLabel(f"<b>File title:</b> {file_title}")
            f_lbl.setWordWrap(True)
            p_lbl = QLabel(f"<b>Project title:</b> {proj_title}")
            p_lbl.setWordWrap(True)
            row.addWidget(f_lbl, 1)
            row.addWidget(p_lbl, 1)
            box = QComboBox()
            box.addItem("Keep project title", "project")
            box.addItem("Use file title", "file")
            row.addWidget(box)
            w = QWidget(); w.setLayout(row)
            g.addWidget(w)
            cb = QCheckBox(
                "Update hardcoded [[wiki links]] project-wide for this title change")
            cb.setChecked(False)
            g.addWidget(cb)
            layout.addWidget(group)
            entries.append((doc_id, file_title, proj_title, box, cb))

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Apply")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False

        for doc_id, file_title, proj_title, box, cb in entries:
            choice = box.currentData()
            if choice == "file":
                old_title = proj_title
                new_title = file_title
                # Update DB title to file's title; keep file/head consistent.
                project.db.execute(
                    "UPDATE documents SET title=? WHERE id=?",
                    (file_title, doc_id))
                project.db.commit()
                project._rename_on_disk(doc_id, file_title)
                project._refresh_metadata_on_disk(doc_id)
            else:
                old_title = file_title
                new_title = proj_title
                # Project wins: rewrite head to match project title.
                project._refresh_metadata_on_disk(doc_id)
            if cb.isChecked() and old_title and new_title and old_title != new_title:
                project.replace_wiki_link_title(old_title, new_title)

        self.statusBar().showMessage("Title/identity reconciled.", 5000)
        return True


    def _warn_missing_files(self, project: Project) -> None:
        """Warn when project data expects files that are not on disk.

        A different problem from the three records disagreeing — there
        is nothing to reconcile, only missing files. OK removes their
        entries from the project (a backup is taken first); Recheck
        re-verifies after the writer puts files back; Cancel leaves the
        entries and the warning returns next time.
        """
        while True:
            missing = project.missing_files()
            if not missing:
                return
            choice = self._missing_dialog(missing)
            if choice == "leave":
                return
            if choice == "remove":
                try:
                    project.backup()
                except Exception as error:
                    self.statusBar().showMessage(
                        f"Could not take a backup before removing entries: {error}",
                        8000)
                    return
                project.forget_missing_files()
                return
            # "recheck": the writer may have put files back — loop and
            # measure again, or close when nothing is missing any more.
            if not project.missing_files():
                return

    def _warn_index_conflicts(self, project: Project) -> None:
        """Say so when a folder's index.html means two things.

        A folder's index.html is the container's own words (docs/STORAGE.md).
        When the file calls itself something else — a document called
        "index", made before the name was reserved — the writer is told,
        and nothing is changed: the file is read as the folder's page and
        left exactly as it is. Rewriting it would be the app's guess at
        the writer's intent, and a wrong guess loses their words.
        """
        conflicts = project.index_conflicts()
        if not conflicts:
            return
        QMessageBox.warning(
            self, "An index.html that says it is something else",
            "A folder's index.html is the folder's own words. These files "
            "say they are something else, so the folder is showing their "
            "words as its own:\n\n"
            + "\n\n".join(conflicts)
            + "\n\nNothing has been changed. Rename the file if it was "
              "meant to be a document of its own.")

    def _missing_dialog(self, missing: list[str]) -> str:
        """The missing-files warning. Returns "remove", "recheck" or
        "leave" (Cancel)."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Missing files")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(
            "The following project files are missing from the disk. "
            "Clicking OK will also remove their entries from the project.\n"
            "If they were accidentally moved, put them back and click Recheck."))
        listing = QPlainTextEdit()
        listing.setReadOnly(True)
        listing.setPlainText("\n".join(missing))
        # Capped height and wrapped text: many missing files scroll inside
        # the box instead of pushing the buttons off the screen.
        listing.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        listing.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        listing.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        listing.setMaximumHeight(160)
        listing.setMinimumHeight(40)
        layout.addWidget(listing)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setText("OK")
        cancel = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        cancel.setText("Leave entries")
        recheck = QPushButton("Recheck")
        recheck.clicked.connect(lambda: dialog.done(2))
        ok.clicked.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        row = QHBoxLayout()
        row.addWidget(recheck)
        row.addStretch(1)
        row.addWidget(buttons)
        layout.addLayout(row)
        result = dialog.exec()
        if result == QDialog.DialogCode.Accepted:
            return "remove"
        if result == 2:
            return "recheck"
        return "leave"

    def _switch_project(self, project: Project) -> None:
        # A failed save cancels the switch: the editor still holds the only
        # copy of the text, and closing the project would take it along.
        if not self.save_current():
            return
        # When the database, the order files and the disk disagree (the
        # writer edited JSON by hand, deleted the order files, or copied
        # folders around), ask which side is right before syncing anything
        # — silent sync is how a book gets mangled.
        if not self._resolve_structure_drift(project):
            project.close()
            return
        self._warn_missing_files(project)
        if self.project:
            self.project.close()
        self.project = project
        # The folder tree on disk is the truth (docs/STORAGE.md); the
        # database is the index over it. Anything the writer added by
        # hand is picked up here, so it shows in the binder. A read-only
        # project is left as it is: the guide is not ours to index.
        try:
            if not project.read_only:
                project.sync_with_disk()
        except Exception:
            pass
        self.current_id = None
        self.editor.begin_document(None, None)
        # Startup resume (1.9): which project was open is app-wide state —
        # written only on a successful switch, read by main() at startup.
        QSettings().setValue("session/lastProject", str(project.root))
        self.setWindowTitle(f"{project.get_setting('title', project.root.stem)} — Lekhak")
        last_document = project.get_setting("last_document")
        # No binder scaffolding is created for templates: the sets are the
        # folders under the project's templates/ root plus the shipped
        # ones, shown under the binder's Templates area and listed in the
        # Outliner (docs/TEMPLATES.md §1.1).
        # Session state (1.9): the zoom the last session ended on comes
        # back with the document and the caret, before anything loads.
        self._load_zoom()
        self.current_template_set = None
        self.current_template_group = False
        self.current_template_file = None
        self._show_template_panel(False)     # the metadata comes back
        self._reload_tree(select_id=last_document or None)
        self._set_project_enabled(True)
        self._apply_read_only()
        self._warn_index_conflicts(project)
        if not self.tree.currentItem() and self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
        # Restore the caret only into the document it was saved from —
        # a deleted last_document falls back to the first item above and
        # this cursor belongs to a document that isn't open.
        if last_document and self.current_id == last_document:
            self._restore_cursor()
        self._load_styles()
        # Selection may not have fired (empty tree, first document stays
        # unselected): seed the metadata panel anyway — root means the
        # project's own fields.
        self._populate_metadata_table()
        self._refresh_related()
        self._refresh_book_widgets(self._current_document())
        # Open Recent: written only here — at the tail, so a switch that
        # failed its save (early return above) records nothing.
        self._record_recent(str(project.root))
        # Session goal (6): fresh baseline for "+N this session", then
        # redraw so the label carries this project's numbers rather than
        # the ones gathered while the switch was still in flight.
        self._session_start_total = self._project_total
        self._update_word_count()

    def _restore_cursor(self) -> None:
        """Session resume (1.9): put the caret back where the last save left it.

        Offsets are plain-text characters (the units QTextCursor reports).
        Clamped because the text may have moved since; the format is
        transparent, so settings can be hand-edited — a junk value leaves
        the caret at the start rather than failing.
        """
        if not self.project or not self.current_id:
            return
        raw = self.project.get_setting("last_cursor", "0")
        try:
            offset = int(raw)
        except ValueError:
            return
        offset = min(max(offset, 0), self.editor.document().characterCount() - 1)
        cursor = self.editor.textCursor()
        cursor.setPosition(offset)
        self.editor.setTextCursor(cursor)

    def _reload_tree(self, select_id: str | None = None) -> None:
        if not self.project:
            return
        self.loading = True
        # Which folders were open: the tree is rebuilt below, and a
        # reload must not close what the writer was working in.
        expanded = {
            str(item.data(0, Qt.UserRole))
            for item in self._all_items()
            if item.isExpanded() and item.data(0, Qt.UserRole)
        }
        self.tree.clear()
        items: dict[str, QTreeWidgetItem] = {}
        # The folder tree on disk is the truth (docs/STORAGE.md), so the
        # binder is drawn from the files, with the index joined in for
        # the ids and titles. Anything the writer added by hand is
        # picked up here.
        try:
            self.project.sync_with_disk()
        except Exception:
            pass
        documents = {document.id: document
                     for document in self.project.documents()}

        def make_item(node) -> QTreeWidgetItem:
            item = QTreeWidgetItem([node.title])
            item.setData(0, Qt.UserRole, node.document_id)
            item.setData(0, Qt.UserRole + 1, node.kind)
            # What the thing *is* on disk, which is what the icon says:
            # a directory is a folder, an .html file is a document,
            # whatever role the row calls it.
            item.setData(0, FOLDER_ROLE, node.is_folder)
            document = documents.get(node.document_id)
            kind = self._row_kind(document) if document else "manuscript"
            note = self._KIND_NOTES[kind]
            item.setToolTip(0, (self._kind_label(document)
                                if document else "Folder"
                                if node.is_folder else "Document")
                            + f"\n{note}")
            item.setIcon(0, self.style().standardIcon(
                QStyle.SP_DirIcon if node.is_folder else QStyle.SP_FileIcon))
            item.setFlags(item.flags() | Qt.ItemIsEditable | Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled)
            item.setFont(0, self._serif(12, bold=document is not None
                                       and document.kind in ("folder", "chapter")))
            self._tint_row(item, kind)
            return item

        def add(node, parent: QTreeWidgetItem | None) -> None:
            item = make_item(node)
            if parent is None:
                self.tree.addTopLevelItem(item)
            else:
                parent.addChild(item)
            if node.document_id:
                items[node.document_id] = item
            for child in node.children:
                add(child, item)

        for node in self.project.binder_tree():
            add(node, None)
        self._apply_lock_marks()
        # Expansion is applied after the tree is built, so a folder opens
        # with its children already in place. What was open stays open;
        # on a first load everything starts closed (bug report: opening
        # every folder buried the structure under its contents).
        self.tree.collapseAll()
        for path in expanded:
            item = items.get(path)
            if item is not None:
                item.setExpanded(True)
        self.loading = False
        if select_id and select_id in items:
            self.tree.setCurrentItem(items[select_id])
        self._refresh_outliner()

    def _outliner_column_ids(self) -> list[str]:
        if not self.project:
            return ["title", "kind", "words", "target"]
        raw = self.project.get_setting("outliner_columns")
        if not raw:
            return ["title", "kind", "words", "target"]
        try:
            columns = json.loads(raw)
        except ValueError:
            return ["title", "kind", "words", "target"]
        return columns if isinstance(columns, list) and "title" in columns else ["title", "kind", "words", "target"]

    def _available_outliner_columns(self, documents) -> list[tuple[str, str]]:
        builtins = [
            ("title", "Title"),
            ("kind", "Kind"),
            ("words", "Words"),
            ("target", "Target"),
        ]
        if not self.project:
            return builtins
        children = {d.id for d in documents if d.parent_id}
        metadata = [
            (f"meta:{key}", key) for key in self.project.metadata_keys()
        ]
        return builtins + metadata

    def _refresh_outliner(self) -> None:
        if not self.project:
            self.outliner.clear()
            return
        documents = self.project.documents()
        # The project's own word count: every binder document's words —
        # templates are folders on disk and carry no prose of their own
        # to exclude (docs/TEMPLATES.md).
        self._project_total = sum(d.word_count for d in documents)
        # A template selection scopes the Outliner to that set's own
        # files; the Templates area scopes it to the sets themselves.
        if self.current_template_set is not None:
            self._outliner_show_template_files(self.current_template_set)
            return
        if self.current_template_group:
            self._outliner_show_template_sets()
            return
        rollups = self.project.word_rollups()
        metadata = self.project.metadata_entries_all()
        # A metadata column shows the value a document actually has: its
        # own, or the one it inherits from the folder above it.
        inherited = self.project.metadata_inherited_all()
        # Multi-select has to survive a refresh (2.1): remember the ids,
        # reselect the new items carrying them after the rebuild.
        keep_selected = {
            item.data(0, Qt.UserRole) for item in self.outliner.selectedItems()
        }
        # Expansion: remember the old state so a refresh keeps folders
        # as the writer left them (default: collapsed, or just the path
        # to the current document).
        _prev_expanded: set[str] = set()
        def _collect(item, acc):
            if item.isExpanded() and item.data(0, Qt.UserRole):
                acc.add(str(item.data(0, Qt.UserRole)))
            for i in range(item.childCount()):
                _collect(item.child(i), acc)
        for top in range(self.outliner.topLevelItemCount()):
            _collect(self.outliner.topLevelItem(top), _prev_expanded)
        column_ids = [c for c in self._outliner_column_ids()]
        labels = dict(self._available_outliner_columns(documents))
        self.outliner.setColumnCount(len(column_ids))
        self.outliner.setHeaderLabels([labels.get(c, c) for c in column_ids])
        # The title column takes whatever room is left — counts and kinds
        # keep their own widths, and a long title clips less.
        header = self.outliner.header()
        for index, column_id in enumerate(column_ids):
            header.setSectionResizeMode(
                index, QHeaderView.ResizeMode.Stretch
                if column_id == "title" else QHeaderView.ResizeMode.Interactive)
        self.outliner.clear()
        children: dict[str | None, list] = {}
        for document in documents:
            children.setdefault(document.parent_id, []).append(document)
        has_children = {d.id for d in documents if children.get(d.id)}

        self.outliner.blockSignals(True)
        items: dict[str, QTreeWidgetItem] = {}

        def add_row(document, parent_item=None):
            cells = []
            for column_id in column_ids:
                if column_id == "title":
                    cells.append(document.title)
                elif column_id == "kind":
                    # What the thing is on disk — a folder, a chapter, a
                    # scene, a note (1.17). The role (Type) is the
                    # writer's own label and lives in the inspector.
                    cells.append(self._kind_label(document))
                elif column_id == "words":
                    cells.append(f"{rollups.get(document.id, document.word_count):,}")
                elif column_id == "target":
                    cells.append(f"{document.word_target:,}" if document.word_target else "")
                elif column_id.startswith("meta:"):
                    key = column_id[5:]
                    own = metadata.get(document.id, {}).get(key, ("", "", []))[0]
                    cells.append(own or inherited.get(document.id, {}).get(key, ""))
                else:
                    cells.append("")
            item = QTreeWidgetItem(cells)
            item.setData(0, Qt.UserRole, document.id)
            if document.id in has_children:
                item.setText(0, document.title)
            self._tint_row(item, self._row_kind(document))
            item.setFont(0, self._serif(12, bold=document.id in has_children))
            item.setToolTip(0, self._KIND_NOTES[self._row_kind(document)])
            for index, column_id in enumerate(column_ids):
                if column_id == "words":
                    own = f"{document.word_count:,} own"
                    total = f"{rollups.get(document.id, document.word_count):,} total"
                    item.setToolTip(index, f"{own} ({total})" if document.id in has_children else own)
                    item.setTextAlignment(index, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if column_id == "target":
                    item.setTextAlignment(index, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if parent_item is None:
                self.outliner.addTopLevelItem(item)
            else:
                parent_item.addChild(item)
            items[document.id] = item
            for child in sorted(children.get(document.id, []), key=lambda d: d.position):
                add_row(child, item)

        # Scoped to the selection (docs/UI-NOTES.md 2): a folder shows its
        # contents, a document shows its siblings, and with nothing selected
        # the project appears whole in binder order, exactly as stored.
        # Without it, reaching the right folder still meant scrolling the
        # whole binder.
        selected = next((d for d in documents if d.id == self.current_id), None)
        if selected is None:
            roots = children.get(None, [])
        elif selected.id in has_children or selected.kind in ("folder", "chapter"):
            # A folder always shows its own contents — even when empty, so
            # an empty folder reads as a folder, not as the whole project.
            roots = children.get(selected.id, [])
        else:
            roots = children.get(selected.parent_id, [])
        for document in sorted(roots, key=lambda d: d.position):
            add_row(document)
        for document in documents:
            if document.id in keep_selected and document.id in items:
                items[document.id].setSelected(True)
        # Restore expansion: saved ids win, else collapsed except the
        # path to the current document (so you can find where you are).
        if _prev_expanded:
            for doc_id in _prev_expanded:
                if doc_id in items:
                    items[doc_id].setExpanded(True)
        elif self.current_id:
            for doc_id, item in items.items():
                # expand ancestors of current
                cur = next((d for d in documents if d.id == self.current_id), None)
                break
            # Walk ancestors of current_id and expand them
            cur = next((d for d in documents if d.id == self.current_id), None)
            if cur is not None:
                parent_of = {d.id: d.parent_id for d in documents}
                pid = parent_of.get(self.current_id)
                while pid:
                    if pid in items:
                        items[pid].setExpanded(True)
                    pid = parent_of.get(pid)
        self.outliner.blockSignals(False)
        # Persist for next refresh (per-project)
        _now: set[str] = set()
        def _now_collect(item, acc):
            if item.isExpanded() and item.data(0, Qt.UserRole):
                acc.add(str(item.data(0, Qt.UserRole)))
            for i in range(item.childCount()):
                _now_collect(item.child(i), acc)
        for top in range(self.outliner.topLevelItemCount()):
            _now_collect(self.outliner.topLevelItem(top), _now)
        try:
            QSettings().setValue(f"outliner/expanded/{self.project.root.name}", json.dumps(sorted(_now)))
        except Exception:
            pass
        self._sync_outliner_selection()

    def _sync_outliner_selection(self) -> None:
        if not self.current_id:
            return
        for index in range(self.outliner.topLevelItemCount()):
            item = self._find_outliner_item(self.outliner.topLevelItem(index))
            if item and item.data(0, Qt.UserRole) == self.current_id:
                self.outliner.blockSignals(True)
                # NoUpdate: move the current item without collapsing the
                # multi-selection the export gathers (2.1).
                self.outliner.setCurrentItem(
                    item, 0, QItemSelectionModel.NoUpdate
                )
                self.outliner.blockSignals(False)
                return

    def _find_outliner_item(self, item):
        if item.data(0, Qt.UserRole) == self.current_id:
            return item
        for index in range(item.childCount()):
            found = self._find_outliner_item(item.child(index))
            if found:
                return found
        return None

    def _outliner_show_template_files(self, template_set) -> None:
        """The Outliner as one set's own folder: its files, nested exactly
        as they sit on disk (or in the shipped map for a set with no
        folder).

        This is the listing (§1.1), and it is the folder's own truth: a
        stray ``notes.txt`` and a nested ``examples/`` appear where they
        are. A click opens the file in the editor — a fragment is HTML and
        the editor is an HTML editor, so there is nothing between the two.
        """
        self.outliner.setColumnCount(1)
        self.outliner.setHeaderLabels(["File"])
        self.outliner.clear()
        folders: dict[str, QTreeWidgetItem] = {}

        def inert(item, kind: str) -> None:
            # No document id: these rows are not documents, so nothing
            # that reads id-based selections can mistake them for one.
            item.setData(0, Qt.UserRole, None)
            item.setData(0, Qt.UserRole + 1, kind)
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)

        self.outliner.blockSignals(True)
        for relative in file_paths(template_set):
            parts = relative.split("/")
            parent_item = None
            for depth in range(len(parts) - 1):
                prefix = "/".join(parts[:depth + 1])
                folder = folders.get(prefix)
                if folder is None:
                    folder = QTreeWidgetItem([parts[depth]])
                    inert(folder, TEMPLATE_FOLDER_KIND)
                    folder.setIcon(0, self.style().standardIcon(
                        QStyle.SP_DirIcon))
                    folder.setToolTip(0, prefix)
                    if parent_item is None:
                        self.outliner.addTopLevelItem(folder)
                    else:
                        parent_item.addChild(folder)
                    folders[prefix] = folder
                parent_item = folder
            item = QTreeWidgetItem([parts[-1]])
            inert(item, TEMPLATE_FILE_KIND)
            item.setData(0, TEMPLATE_PATH_ROLE, relative)
            item.setToolTip(0, relative)
            if parent_item is None:
                self.outliner.addTopLevelItem(item)
            else:
                parent_item.addChild(item)
        self.outliner.expandAll()
        self.outliner.blockSignals(False)

    def _outliner_show_template_sets(self) -> None:
        """The Outliner as the Templates area's own listing: the sets, so
        the area's selection means something in both trees."""
        self.outliner.setColumnCount(2)
        self.outliner.setHeaderLabels(["Template", "Where"])
        self.outliner.clear()
        self.outliner.blockSignals(True)
        for template_set in discover_template_sets(self.project.root):
            item = QTreeWidgetItem(
                [template_set.name,
                 template_set_where(template_set, self.project.root)])
            item.setData(0, Qt.UserRole, None)
            item.setData(0, Qt.UserRole + 1, TEMPLATE_SET_KIND)
            item.setData(0, TEMPLATE_SET_ROLE, template_set)
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            if template_set.directory is None:
                item.setToolTip(0, "Built into Lekhak — duplicate it to edit it")
            else:
                item.setToolTip(0, str(template_set.directory))
            self.outliner.addTopLevelItem(item)
        self.outliner.blockSignals(False)

    def _outliner_inspector_changed(self, *_args) -> None:
        """Inspector previews the Outliner selection (single = that row,
        mixed = blank/grey) without opening it. Edits in the inspector
        therefore target the preview, not the open editor document —
        the same target the next Bulk dialog will use."""
        if not self.project:
            return
        selected = [item.data(0, Qt.UserRole)
                    for item in self.outliner.selectedItems()
                    if item.data(0, Qt.UserRole)]
        if not selected:
            return
        if len(selected) == 1:
            doc_id = selected[0]
            doc = next((d for d in self.project.documents() if d.id == doc_id), None)
            if doc is None:
                return
            # Preview only — the open editor document stays put; the
            # inspector table/type follow the row you clicked.
            self._metadata_target = doc_id if doc.kind != "folder" else PROJECT_METADATA_TARGET
            self._type_target_id = doc_id
            self._fill_type_box(self.project.type_of(doc))
            # Fill metadata rows for the preview doc directly (do not
            # go through _populate_metadata_table, which would snap
            # back to the open document).
            if self._metadata_target == PROJECT_METADATA_TARGET:
                entries = dict(self.project.project_metadata())
                for key in self.project.known_fields("folder"):
                    entries.setdefault(key, ("", "text", []))
                self.metadata_table.set_entries(entries)
            else:
                entries = self.project.metadata_entries(doc_id)
                rows = {k: e for k, e in entries.items() if k not in ("Type", TEMPLATE_KEY, TEMPLATE_CONFIG_KEY)}
                group = self.project.type_group(doc)
                for key in self.project.known_fields(group):
                    rows.setdefault(key, ("", "text", []))
                self.metadata_table.set_entries(rows)
            return
        # Multiple: mixed display, edit goes nowhere until you narrow it
        types = {self.project.type_of(next(d for d in self.project.documents() if d.id == i))
                 for i in selected if any(d.id == i for d in self.project.documents())}
        self._metadata_target = None
        self.type_box.blockSignals(True)
        self.type_box.clear()
        self.type_box.setCurrentText("")
        self.type_box.setPlaceholderText(
            f"Multiple values ({len(types)} Types)" if len(types) > 1 else next(iter(types)))
        self.type_box.blockSignals(False)
        # Blank metadata table = mixed, not empty
        self.metadata_table.set_entries({})

    def _outliner_clicked(self, item, _column) -> None:
        kind = item.data(0, Qt.UserRole + 1)
        if kind == TEMPLATE_SET_KIND:
            # A set listed in the Outliner: its own files are listed in
            # its place, and one of them opens in the editor (§1.2).
            self._open_template_set(item.data(0, TEMPLATE_SET_ROLE))
            return
        if kind == TEMPLATE_FILE_KIND:
            # A file of the chosen set opens in the editor (§1.2) — the
            # same editor as prose, because a fragment is an HTML file.
            self._open_template_file(item.data(0, TEMPLATE_PATH_ROLE))
            return
        if kind == TEMPLATE_FOLDER_KIND:
            # A folder row is structure, not a target: it is already
            # visible where it stands.
            return
        document_id = item.data(0, Qt.UserRole)
        if not document_id:
            # A synthetic row with nothing to select (the Templates area
            # itself): selecting it here would silently un-pick the set.
            return
        for tree_item in self._all_items():
            if tree_item.data(0, Qt.UserRole) == document_id:
                self.tree.setCurrentItem(tree_item)
                return

    def pick_outliner_columns(self) -> None:
        if not self.project:
            return
        documents = self.project.documents()
        available = self._available_outliner_columns(documents)
        current = self._outliner_column_ids()
        dialog = ColumnPickerDialog(available, current, self)
        if dialog.exec() == QDialog.Accepted:
            self.project.set_setting(
                "outliner_columns", json.dumps(dialog.selected_columns())
            )
            self._refresh_outliner()

    def _resolve_wiki_links(self) -> None:
        """Turn `[[Title]]` spans into clickable links in the editor.

        Runs on load only, so the stored document keeps the plain `[[Title]]`
        text (links survive a retitle and stay readable in the file).
        """
        if not self.project:
            return
        document = self.editor.document()
        link_color = self.palette().link().color().name()
        cursor = QTextCursor(document)
        cursor.beginEditBlock()
        text = self.editor.toPlainText()
        # Right-to-left so replacements never disturb pending positions.
        for match in reversed(list(self.project.WIKI_LINK_RE.finditer(text))):
            target = match.group(1).strip()
            resolved = self.project.resolve_link(target)
            # Select the whole token, brackets included; insertHtml then
            # replaces it outright, so no stray [[ ]] survive.
            span_cursor = QTextCursor(document)
            span_cursor.setPosition(match.start())
            span_cursor.setPosition(match.end(), QTextCursor.KeepAnchor)
            if len(resolved) == 1:
                html = (
                    f'<a href="lekhak://doc/{resolved[0].id}" '
                    f'style="color:{link_color}; text-decoration:underline">'
                    f"{escape(target)}</a>"
                )
            elif not resolved:
                html = (
                    '<a href="lekhak://missing" '
                    'style="color:#c0392b; text-decoration:underline">'
                    f"[missing: {escape(target)}]</a>"
                )
            else:
                html = (
                    f'<a href="lekhak://pick/{escape(target)}" '
                    f'style="color:{link_color}; text-decoration:underline">'
                    f"{escape(target)}</a>"
                )
            span_cursor.insertHtml(html)
        cursor.endEditBlock()

    def _link_clicked(self, address: str) -> None:
        if not self.project or address == "lekhak://missing":
            return
        if address.startswith("lekhak://doc/"):
            self.save_current()
            self._select_document(address[len("lekhak://doc/"):])
        elif address.startswith("lekhak://pick/"):
            self._pick_link_target(address[len("lekhak://pick/"):])

    @staticmethod
    def _kind_label(document) -> str:
        """What a document is, in the binder's own words (1.17): the
        physical kind — a folder, a chapter, a scene, a note. The role
        (Type) is the writer's own label and lives in the inspector.
        """
        return str(getattr(document, "kind", "") or "document").capitalize()

    def _pick_link_target(self, target: str) -> None:
        matches = self.project.resolve_link(target)
        labels = [f"{d.title}  ({self._kind_label(d)})" for d in matches]
        chosen, accepted = QInputDialog.getItem(
            self, "Follow link", f"‘{target}’ matches several sections:",
            labels, 0, False,
        )
        if accepted:
            self.save_current()
            self._select_document(matches[labels.index(chosen)].id)

    def _link_section(self) -> None:
        if not self.project:
            return
        cursor = self.editor.textCursor()
        selected = cursor.selectedText().strip()
        documents = [
            d for d in self.project.documents() if d.kind != "folder"
        ]
        if not documents:
            return
        titles = [d.title for d in documents]
        if selected and selected in titles:
            start = titles.index(selected)
        else:
            start = 0
        chosen, accepted = QInputDialog.getItem(
            self, "Insert link", "Link to section:", titles, start, False,
        )
        if not accepted:
            return
        self.save_current()
        self.editor.insert_tracked_text(f"[[{chosen}]]")
        self._content_changed()

    def _broken_links(self) -> list[str]:
        if not self.project:
            return []
        return [
            target for target, resolved in
            self.project.wiki_targets(self.current_id) if not resolved
        ]

    def _show_text_tabs(self, visible: bool) -> None:
        """Editor and Page belong to a document, not to a container.
        Corkboard and Outliner belong to a folder, not to a document."""
        for widget in (self.editor_page, self.page_view):
            index = self.views.indexOf(widget)
            if index >= 0:
                self.views.setTabVisible(index, visible)
        for widget in (self.corkboard, self.outliner):
            index = self.views.indexOf(widget)
            if index >= 0:
                self.views.setTabVisible(index, not visible)

    def _folder_view_widget(self):
        """The tab a folder selection lands on: the writer's last choice.

        Stored per project beside editor_zoom, defaulting to the outliner —
        the denser view earns its place by default, while a corkboard lover
        is not fought: their switch is remembered for the next folder and
        for the next open.
        """
        name = self.project.get_setting("folder_view", "") if self.project else ""
        return self.corkboard if name == "corkboard" else getattr(self, "outliner_page", self.outliner)

    def _view_tab_changed(self, index: int) -> None:
        """Remember the folder view the writer actually picked.

        Only the corkboard and outliner say anything about folders — Editor
        and Page are document views. The programmatic switch a folder
        selection causes runs while the selection is still loading, so it
        writes nothing: only a hand click on a tab moves the preference.
        """
        widget = self.views.widget(index)
        if self.loading or not self.project:
            return
        widget = self.views.widget(index)
        if widget not in (self.corkboard, self.outliner, getattr(self, "outliner_page", None)):
            return
        current = next(
            (d for d in self.project.documents() if d.id == self.current_id),
            None,
        )
        if current is not None and current.kind == "folder":
            self.project.set_setting(
                "folder_view",
                "corkboard" if widget is self.corkboard else "outliner",
            )

    def _project_is_read_only(self) -> bool:
        """Whether this project may be written to at all.

        The guide is the software's, not the writer's: it opens so it can
        be read and exported, and nothing in it can be edited — not the
        prose, not the metadata, not the install folder it came from.
        """
        return bool(self.project and self.project.read_only)

    def _apply_read_only(self) -> None:
        """Keep a read-only project's editing off, and say why.

        Browsing stays: the binder, the page, the corkboard and the
        outliner are how the guide is read.
        """
        read_only = self._project_is_read_only()
        for widget in (self.synopsis, self.target, self.type_box,
                       self.metadata_table, self.metadata_add_button,
                       self.metadata_remove_button, self.doc_template_box,
                       self.book_template_box, self.page_setup_button,
                       self.book_cover_edit, self.book_cover_button,
                       self.link_policy_box):
            widget.setEnabled(not read_only)
        if read_only:
            self.statusBar().showMessage(
                "The guide is read-only — it can be read and exported, "
                "but not changed.", 6000)

    def open_guide(self) -> None:
        """Open the guide: this software's own manual, as a project.

        A fresh install opens it before asking anyone to create a
        project, so the software can be looked at before it is used.

        It is opened where it ships, read-only: nothing is copied into
        the writer's disk, and nothing is written back into the install
        folder. A writer who wants their own version asks for a copy.
        """
        from lekhak.guide import guide_project

        try:
            path = guide_project()
        except Exception as error:
            QMessageBox.critical(self, "Could not open the guide",
                                 str(error))
            return
        if path is None:
            self.statusBar().showMessage(
                "This installation does not carry the guide.", 5000)
            return
        try:
            project = Project(path, read_only=True)
            project.open()
        except Exception as error:
            QMessageBox.critical(self, "Could not open the guide",
                                 str(error))
            return
        self._switch_project(project)

    def save_project_as(self) -> None:
        """Save this project under a new name, and open the copy.

        The ordinary gesture: a book you want to keep as it is, and
        work on separately. It is also how a writer takes their own copy
        of the guide — the guide opens read-only, and its copy is an
        ordinary project, writable.
        """
        if not self.project:
            return
        suggested = f"{self.project.get_setting('title') or self.project.root.stem}.lekhak"
        selected, _filter = QFileDialog.getSaveFileName(
            self, "Save project as", suggested, "Lekhak project (*.lekhak)")
        if not selected:
            return
        try:
            copy = self.project.copy_to(Path(selected))
        except Exception as error:
            QMessageBox.critical(self, "Could not save the project",
                                 str(error))
            return
        self.statusBar().showMessage(f"Saved as {copy.name}", 5000)
        self._open_path(str(copy))

    def _selection_changed(self, current, previous) -> None:
        if self.loading:
            return
        self.stop_speech()
        if not self.save_current():
            # Refuse the switch rather than reload from disk: the editor
            # holds the only copy of the unsaved text. Qt has already moved
            # the highlight, so put it back where it was.
            if previous is not None and previous in self._all_items():
                self.tree.blockSignals(True)
                self.tree.setCurrentItem(previous)
                self.tree.blockSignals(False)
            return
        kind = current.data(0, Qt.UserRole + 1) if current else None
        if kind == TEMPLATE_SET_KIND:
            # Not a document: the Outliner lists the set's files and no
            # prose panel is touched — a file of the set opens in the
            # editor when the writer picks one there.
            self._open_template_set(current.data(0, TEMPLATE_SET_ROLE))
            self._reveal_tree_path(current)
            return
        if kind == TEMPLATE_GROUP_KIND:
            self._open_template_group()
            self._reveal_tree_path(current)
            return
        self.current_template_set = None
        self.current_template_group = False
        self.current_template_file = None
        self._show_template_panel(False)     # the metadata comes back
        self.current_id = current.data(0, Qt.UserRole) if current else None
        self.loading = True
        if self.project and self.current_id:
            document = next(d for d in self.project.documents() if d.id == self.current_id)
            html = self.project.read_content(self.current_id)
            self.editor.setHtml(scaled_font_sizes(
                unescape_placeholders(html), self.zoom))
            self.editor.document().setBaseUrl(
                QUrl.fromLocalFile(str(self.project.root) + "/"))
            self._apply_zoom_base()
            self._apply_default_style(document, html)
            self.editor.setReadOnly(self._project_is_read_only())
            self.editor.begin_document(self.project, self.current_id)
            self._resolve_wiki_links()
            self.synopsis.setPlainText(document.synopsis)
            self.target.setValue(document.word_target)
            self._refresh_book_widgets(document)
            self._refresh_media()
            # A locked document is opened like any other, and says so:
            # the writer can read it, reference it, and even type in it
            # — but nothing is saved.
            holder = self.project.lock_holder(self.current_id)
            if holder is not None:
                self.statusBar().showMessage(
                    f"Locked by ‘{holder}’ — changes are not saved", 6000)
            # Capture before toggling visibility: hiding the current tab
            # makes Qt pick a neighbour, so the old widget is the only
            # reliable evidence of where the writer was. Signals are
            # blocked across the reconfiguration because that auto-switch
            # would otherwise fire currentChanged and refresh (and announce)
            # a tab the writer never chose.
            previous_view = self.views.currentWidget()
            # A container is anything that holds children — a folder, or a
            # chapter with scenes — not just kind == "folder"
            # (docs/STORAGE.md): the binder is a file manager, and a folder
            # is a folder whatever role it plays.
            is_container = self.current_id in self.project.container_ids()
            self.views.blockSignals(True)
            if is_container:
                # A container is not prose (docs/UI-NOTES.md 3): leaving the
                # text tabs live invites someone to type into a folder and meet
                # the formatting mess much later, in an export.
                self._show_text_tabs(False)
                if previous_view in (self.editor_page, self.page_view):
                    self.views.setCurrentWidget(self._folder_view_widget())
            else:
                # A leaf opens on the editor (docs/UI-NOTES.md 3): the
                # corkboard/outliner tabs are for containers, so a document
                # selection lands on the writing pane rather than on whatever
                # folder view was showing.
                self._show_text_tabs(True)
                if previous_view not in (self.editor_page, self.page_view):
                    self.views.setCurrentWidget(self.editor_page)
            self.views.blockSignals(False)
        else:
            self.editor.clear()
            self.editor.begin_document(self.project, None)
            self.synopsis.clear()
            self.target.setValue(0)
            self._refresh_book_widgets(None)
        self._populate_metadata_table()
        # A template document is designed in the sidebar (1.17): the
        # design panel stands where the metadata was, and the tick beside
        # the Type row is the way back to the metadata.
        self._design_document_id = None
        selected = self._current_document()
        if selected is not None and self.project.is_template(selected.id):
            self._design_document_id = selected.id
            self.template_check.blockSignals(True)
            self.template_check.setChecked(True)
            self.template_check.blockSignals(False)
            self._load_document_design(selected.id)
            self._show_template_panel(True)
        self._refresh_related()
        self._refresh_page_preview()
        self.loading = False
        self._update_word_count()
        # Squiggles go stale the moment the text changes underneath,
        # and this load path bypasses _content_changed's debounce.
        self._update_spell_squiggles()
        self._reveal_tree_path(current)
        self._refresh_corkboard()
        # Rebuild, don't just re-select: the scope follows the selection
        # (docs/UI-NOTES.md 2), so the rows change with it even while the
        # Outliner tab is already the one showing. Sync runs inside.
        self._refresh_outliner()

    def _content_changed(self) -> None:
        if not self.loading:
            self.save_timer.start()
            self._update_word_count()
            if self.spell_enabled:
                # Debounced: the dictionary pass spawns a process, so it
                # waits for the typing to pause instead of per keystroke.
                self.spell_timer.start()

    def _metadata_changed(self) -> None:
        if not self.loading:
            self.save_timer.start()

    # --- spell squiggles (8) ------------------------------------------------

    def _toggle_spell_squiggles(self, checked: bool) -> None:
        """View-menu switch for the dictionary underliner (8).

        A checker missing from the system is not a silently dead
        toggle: the box falls back to off, the setting stays off and
        the status bar says what to install.
        """
        if checked and spellcheck.find_engine() is None:
            self.spell_action.blockSignals(True)
            self.spell_action.setChecked(False)
            self.spell_action.blockSignals(False)
            self.spell_enabled = False
            QSettings().setValue("ui/spellSquiggles", False)
            self.statusBar().showMessage(
                "No spell checker found — install aspell or hunspell",
                6000)
            return
        self.spell_enabled = checked
        QSettings().setValue("ui/spellSquiggles", checked)
        if checked:
            self._update_spell_squiggles()
        else:
            self.spell_timer.stop()
            self.editor.setExtraSelections([])

    def _spell_dictionary_path(self) -> Path | None:
        """The writer's word list, resolved (docs/PREFERENCES.md):
        the configured file, or the project's own dictionary.txt when
        nothing is configured. None with no project open."""
        configured = QSettings().value("spell/dictionary", "", type=str)
        if configured.strip():
            return Path(configured).expanduser()
        if self.project:
            return self.project.root / "dictionary.txt"
        return None

    def _update_spell_squiggles(self) -> None:
        """Red wavy underlines under words the dictionary rejects (8).

        View-level only — ExtraSelections never touch the document, so
        squiggles cannot be undone, saved or seen by an export, and a
        missing or failing checker flags nothing rather than everything.
        """
        if not (self.project and self.current_id and self.spell_enabled):
            self.editor.setExtraSelections([])
            return
        selection_format = QTextCharFormat()
        selection_format.setUnderlineStyle(
            QTextCharFormat.UnderlineStyle.SpellCheckUnderline)
        selection_format.setUnderlineColor(QColor("#c62828"))
        selections = []
        spellcheck.set_word_list(self._spell_dictionary_path())
        for start, end in spellcheck.misspellings(
                self.editor.toPlainText(), self._spell_cache):
            selection = QTextEdit.ExtraSelection()
            cursor = QTextCursor(self.editor.document())
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.KeepAnchor)
            selection.cursor = cursor
            selection.format = selection_format
            selections.append(selection)
        self.editor.setExtraSelections(selections)

    def _selection_metadata_target(self) -> str | None:
        """What the metadata section (and Edit Metadata…) acts on: the
        project's own fields with nothing selected or a folder selected —
        the outliner root's "overall setup" — and the document's rows on
        chapters, scenes and notes (1.12)."""
        if not self.project:
            return None
        if not self.current_id:
            return PROJECT_METADATA_TARGET
        kind = next(
            (d.kind for d in self.project.documents() if d.id == self.current_id),
            None,
        )
        if kind is None:
            return None
        return PROJECT_METADATA_TARGET if kind == "folder" else self.current_id

    def _current_document(self):
        if not self.project or not self.current_id:
            return None
        return next(
            (d for d in self.project.documents() if d.id == self.current_id),
            None,
        )

    def _update_book_template_help(self) -> None:
        """Explain what an advanced template set expects."""
        if not hasattr(self, "book_template_help"):
            return
        name = ""
        if hasattr(self, "book_template_box"):
            data = self.book_template_box.currentData()
            name = (data or "").strip().lower()
        if name == "book":
            # Nothing here is Lekhak's vocabulary: the *set* names the words
            # it expects and the fields it reads, and the list below is that
            # set's own README in brief. A writer's own fields are theirs —
            # the app never invents one.
            text = ("Advanced template: the set names the words it expects, "
                    "and its README is the list. This one, for instance, "
                    "expects a folder typed 'book' holding 'chapter' and "
                    "'scene' documents, and reads its own fields if you give "
                    "them — Quote, Source and Date (an epigraph above the "
                    "prose), PoV and Location (a line naming who and where), "
                    "BookTagline and Author for the title page. The spelling "
                    "must match the set's, and the fields are yours to add or "
                    "ignore. An image uploaded as 'cover' is used for the EPUB "
                    "cover; failing that, the first image in the folder is "
                    "used. (May need updating once image handling is "
                    "settled.)")
        elif name == "guide":
            text = ("Advanced template: folders typed 'section', documents "
                    "inside them typed 'section'. Each document becomes one "
                    "section of the guide.")
        elif name == "short story":
            text = ("Document-level template that outputs Title, Author and "
                    "the text of the story. Create a document type "
                    "'short-story' or assign the template to a document "
                    "directly. If project preferences or metadata name an "
                    "author, it is used; if both do, the metadata author wins.")
        elif name == "default":
            text = ("Generic document. No special settings required. Also "
                    "used as a fallback when a template does not match the "
                    "content.")
        elif name == "no-export":
            text = ("Prints nothing: use it to keep a folder out of an "
                    "export, or to show that silence is a valid answer.")
        else:
            text = ""
        self.book_template_help.setText(text)
        self.book_template_help.setVisible(bool(text))

    def _refresh_book_widgets(self, document) -> None:
        """Show and fill the 2.1 sections for whatever is selected:
        folder → TEMPLATES flag, book template, cover; chapter/scene/note
        → the set slot that renders it; nothing selected → hide them all.

        Called under loading, so every widget this sets never reads back
        as an edit. Stored values the combos can no longer offer (a
        deleted template or slot) fall back to the first entry on screen
        and heal on the next actual change.
        """
        is_folder = bool(document and document.kind == "folder")
        is_prose = bool(document and document.kind != "folder")
        for widget in (
            self.book_template_label, self.book_template_box,
            self.cover_label, self.book_cover_edit, self.book_cover_button,
            self.link_policy_label, self.link_policy_box,
            self.page_setup_button,
        ):
            widget.setVisible(is_folder)
        self.doc_template_label.setVisible(is_prose)
        self.doc_template_box.setVisible(is_prose)
        # Copy-defaults works the same for a folder and for a document:
        # folder → inside it, document → next to it.
        self.copy_defaults_button.setVisible(bool(document))
        if document and is_folder:
            self.copy_defaults_button.setText("Place the default templates here")
            self.copy_defaults_button.setToolTip(
                "The templates Lekhak ships with cannot be edited in place. "
                "This puts an editable copy of the default set inside "
                "the selected folder, where you can change it.")
        elif document:
            self.copy_defaults_button.setText("Place the default templates next to this file")
            self.copy_defaults_button.setToolTip(
                "The templates Lekhak ships with cannot be edited in place. "
                "This puts an editable copy of the default set next to "
                "this file, where you can change it.")
        else:
            self.copy_defaults_button.setText("Copy default templates to customize")
        self.duplicate_template_button.setVisible(False)
        # The fold holds Type/template selection (always) perched above
        # folder-only furniture — so it stays visible with a document;
        # scene divider/cover/etc. hide individually below.
        if hasattr(self, "format_fold"):
            self.format_fold.setVisible(bool(document))
        self.scene_divider_label.setVisible(is_folder)
        self.scene_divider_edit.setVisible(is_folder)
        if not document or not self.project:
            return
        # Both rows speak the document's Type (docs/MODEL.md), so a
        # folder typed Book asks for a "Book template" and a document
        # typed Scene for a "Scene template" — only a document with no
        # Type of its own falls back to "Document template".
        document_type = self.project.type_of(document)
        type_label = f"{document_type} template"
        self.book_template_label.setText(type_label)
        self.doc_template_label.setText(type_label)
        settings = self.project.book_settings(document.id) if is_folder else {}
        self.book_template_box.blockSignals(True)
        self.book_template_box.clear()
        self.book_template_box.addItem("Automatic (nothing assigned)", "")
        # Templates by name, the way §2 resolves them: the discovered sets
        # (the project's templates/ folder and the app library). Binder
        # folders are never offered — a folder is a template only under
        # templates/ (§1.1), so there is nothing else for the chooser to
        # name. A stored name that no longer exists finds nothing and
        # falls back to Automatic, healing on the next change rather than
        # pointing at a folder id that means nothing.
        offered: list[str] = []
        # Nothing assigned: the set named after this folder's own Type is
        # the one it means (docs/TEMPLATES.md asks the writer to name
        # templates by Type). That is an assignment, not a suggestion
        # (1.18): the match is stored below, so the export reads the
        # same answer the row shows.
        wanted = settings.get("template", "").strip()
        for template_set in discover_template_sets(self.project.root):
            if template_set.name in offered:
                # A project folder named after a shipped set: listed in
                # the binder (§1.1 — its files are never hidden), but two
                # rows with one name cannot be told apart here, and the
                # name is the shipped set's (see named_template_sets).
                continue
            offered.append(template_set.name)
            self.book_template_box.addItem(template_set.name, template_set.name)
            if (not wanted
                    and template_set.name.casefold()
                    == document_type.casefold()):
                wanted = template_set.name
        self.book_template_box.setCurrentIndex(
            max(0, self.book_template_box.findData(wanted))
        )
        self.book_template_box.blockSignals(False)
        if is_folder and not settings.get("template", "").strip() and wanted:
            settings["template"] = wanted
            self.project.set_book_settings(document.id, settings)
        self._update_book_template_help()
        self.book_cover_edit.setText(settings.get("cover", ""))
        self.page_setup_button.setText(self._page_setup_text(document.id))
        self.link_policy_box.blockSignals(True)
        self.link_policy_box.clear()
        self.link_policy_box.addItem("Automatic (project default)", "")
        for label, value in LINK_POLICY_CHOICES:
            self.link_policy_box.addItem(label, value)
        self.link_policy_box.setCurrentIndex(
            max(0, self.link_policy_box.findData(settings.get("links", "")))
        )
        self.link_policy_box.blockSignals(False)
        self.doc_template_box.blockSignals(True)
        self.doc_template_box.clear()
        self.doc_template_box.addItem("Default (set's own slot)", "")
        # The box carries the filename — the file is the truth
        # (docs/TEMPLATES.md §1.2) — so every offered name shows the
        # file it stands for: .html where a set has that variant, .txt
        # only when plain text is all there is. A stored name with no
        # file behind it keeps its row too: a template may be asked for
        # before it exists, and the export falls back to the set's own
        # slot for the document's own shape until it does.
        faces = dict(TemplateSet.builtin().document_template_faces())
        for template_set in discover_template_sets(self.project.root):
            for stem, face in template_set.document_template_faces().items():
                previous = faces.get(stem)
                if previous is None or (previous.endswith(".txt")
                                        and face.endswith(".html")):
                    faces[stem] = face
        stored = (document.doc_template or "").strip()
        if not stored.startswith((TEMPLATE_FILE_PREFIX, TEMPLATE_DOC_PREFIX)):
            stored = document_template_stem(stored)
        for stem in sorted(faces):
            self.doc_template_box.addItem(faces[stem], stem)
        # The one-file templates (1.17): a template is one file with its
        # yaml in the head, and these are the ones the project's
        # templates/ folder holds.
        for path in self.project.single_file_templates():
            self.doc_template_box.addItem(
                f"{path.stem} (template file)",
                TEMPLATE_FILE_PREFIX + path.stem)
        # The documents marked as templates: their own bodies are the
        # markup, so one of them can dress this document.
        for template_document in self.project.template_documents():
            if template_document.id == document.id:
                continue        # a document does not dress itself
            self.doc_template_box.addItem(
                f"{template_document.title} (template)",
                TEMPLATE_DOC_PREFIX + template_document.id)
        if stored and self.doc_template_box.findData(stored) < 0:
            # A name no file answers keeps its own row, shown the way it
            # will be named on disk.
            label = stored
            if not stored.startswith((TEMPLATE_FILE_PREFIX, TEMPLATE_DOC_PREFIX)):
                label = stored + ".html"
            self.doc_template_box.addItem(label, stored)
        # Nothing stored: the template the parent folder chose may carry
        # a file named after this document's own Type — the naming
        # convention of docs/TEMPLATES.md — and that is the one to use.
        # With no such file the set's own slot stays, which is the
        # document template itself. Like the folder row, the match is
        # stored (1.18), so the export reads what the row shows.
        wanted = stored
        if not wanted:
            parent_id = self.project.parent_of(document.id)
            sets = named_template_sets(self.project.root)
            chosen_name = (self.project.book_settings(parent_id)
                           .get("template", "").strip()
                           if parent_id else "")
            if parent_id and not chosen_name:
                # The parent's own default is the set named after its
                # Type (1.18) — read it even when the folder itself has
                # not been opened this session.
                parent = next((d for d in self.project.documents()
                               if d.id == parent_id), None)
                parent_type = (self.project.type_of(parent)
                               if parent is not None else "")
                chosen_name = next(
                    (name for name in sets
                     if name.casefold() == parent_type.casefold()), "")
            chosen = sets.get(chosen_name) if chosen_name else None
            if chosen is not None:
                type_face = document_type.casefold()
                wanted = next(
                    (stem for stem in sorted(chosen.document_template_faces())
                     if stem.casefold() == type_face),
                    "")
        self.doc_template_box.setCurrentIndex(
            max(0, self.doc_template_box.findData(wanted))
        )
        self.doc_template_box.blockSignals(False)
        if not is_folder and not stored and wanted:
            self.project.update_document(document.id, doc_template=wanted)

        # Scene divider (per folder).
        if is_folder:
            self.scene_divider_edit.blockSignals(True)
            self.scene_divider_edit.setText(document.scene_divider or "")
            self.scene_divider_edit.blockSignals(False)
    def _book_settings_changed(self, *_args) -> None:
        if self.loading or not self.project or not self.current_id:
            return
        document = self._current_document()
        if not document or document.kind != "folder":
            return
        # Blanks remove the entry — the folder falls back to matching a
        # template by name, auto-detecting images/<id>/cover.* and
        # following the project link policy.
        self.project.set_book_settings(self.current_id, {
            "template": self.book_template_box.currentData() or "",
            "cover": self.book_cover_edit.text().strip(),
            "links": self.link_policy_box.currentData() or "",
        })
        self._update_book_template_help()

    def _browse_cover(self) -> None:
        if not self.project:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose cover image", str(self.project.root),
            "Images (*.png *.jpg *.jpeg *.webp *.gif)",
        )
        if path:
            self.book_cover_edit.setText(path)
            self._book_settings_changed()

    @staticmethod
    def _trim_label(geometry) -> str:
        """Human trim name for status lines and button labels: the
        preset's own label, or the raw inches for a custom size."""
        if not geometry:
            return "Default (A4)"
        trim = geometry["trim"]
        if trim in PAGE_TRIMS:
            return PAGE_TRIMS[trim][0]
        return f'{geometry["width"]:g} × {geometry["height"]:g} in'

    def _page_setup_text(self, document_id: str) -> str:
        """The inspector button's label: what geometry this book prints
        with, or that it has none."""
        geometry = self.project.page_setup(document_id) if self.project else None
        if not geometry:
            return "Page setup — Default (A4)"
        text = f"Page setup — {self._trim_label(geometry)}"
        if geometry["mirror"]:
            text += ", mirrored"
        if geometry["gutter"]:
            text += f", gutter {geometry['gutter']:g} in"
        return text

    def _open_page_setup(self) -> None:
        if not self.project or not self.current_id:
            return
        document = self._current_document()
        if not document or document.kind != "folder":
            return
        dialog = PageSetupDialog(
            self.project.page_setup(self.current_id), self)
        if dialog.exec() != QDialog.Accepted:
            return
        self.project.set_page_setup(
            self.current_id, None if dialog.cleared else dialog.geometry())
        self.page_setup_button.setText(self._page_setup_text(self.current_id))
        self.statusBar().showMessage(
            "Page setup cleared — exports use the default (A4)"
            if dialog.cleared else "Page setup saved", 3000)

    def _page_view_tab_changed(self, index: int) -> None:
        if self.views.widget(index) is self.page_view:
            self._refresh_page_preview()

    def _refresh_page_preview(self) -> None:
        """Project the current document onto real pages (3.2).

        Only runs while the Page tab is showing — a refresh rebuilds a
        whole QTextDocument, and while the tab shows nothing can change
        underneath (the canvas is read-only), so tab switches are the
        refresh trigger. Geometry is a page printed on its own (1.18):
        the nearest book's setup, but a design of this page's own has
        the last word on its size. The HTML comes from the live editor
        document, so unsaved edits appear."""
        if self.views.currentWidget() is not self.page_view:
            return
        if not self.project or not self.current_id:
            self.page_view.set_page("", None, None)
            return
        geometry = self.project.standalone_page_setup(self.current_id)
        # Stored sizes, not display ones: the projection has to paginate
        # the way the export will, so zoom is the canvas's own scale (see
        # PageView.set_user_scale) and never a font size.
        anchor = QFont(self.editor.font())
        anchor.setPointSizeF(ZOOM_ANCHOR_PT)
        self.page_view.set_page(
            scaled_font_sizes(self.editor.document().toHtml(), 1.0 / self.zoom),
            geometry,
            anchor,
        )
        count = self.page_view.page_count
        # Name the fallback as a fallback (docs/UI-NOTES.md 6): an
        # unconfigured preview is undecided, not a choice the writer
        # made — and the lever lives on the book folder.
        paper = (self._trim_label(geometry) if geometry else
                 "A4 (default — set Page setup on the book folder)")
        self.statusBar().showMessage(
            f"Page view — {count} page{'' if count == 1 else 's'}, "
            f"{paper}")

    def _page_jump(self, position: int) -> None:
        """Dblclick on the paper: show the editor at the same
        character — both documents share one HTML source, so the
        offset maps."""
        self.views.setCurrentWidget(self.editor_page)
        cursor = QTextCursor(self.editor.document())
        cursor.setPosition(min(position, self.editor.document().characterCount() - 1))
        self.editor.setTextCursor(cursor)
        self.editor.ensureCursorVisible()
        self.editor.setFocus()

    def _refresh_template_choices(self) -> None:
        """Rebuild the apply-template list when the box opens (1.17).

        What the project knows changes while a document is open — a
        document marked as a template, a one-file template dropped
        into templates/ — and the list must not be a snapshot of the
        moment the document was selected.
        """
        document = self._current_document()
        if document is not None:
            self._refresh_book_widgets(document)

    def _doc_template_changed(self, *_args) -> None:
        """Save the document's template from the box (1.17).

        The dropdown lists every template the project knows — the sets'
        own slots, the one-file templates and the documents marked as
        templates — and what is saved is what the pick stands for: the
        name for a set's slot, a prefixed name for the other two, so
        the three can never be mistaken for one another.

        The box is editable and wired to editingFinished like the Type
        box: a typed name reaches the database without the dropdown ever
        moving. A name no file answers saves just the same; the export
        falls back where it stands.
        """
        if self.loading or not self.project or not self.current_id:
            return
        line = self.doc_template_box.lineEdit()
        text = (line.text() if line is not None
                else self.doc_template_box.currentText())
        # An editable combo's currentText is whatever the line edit says,
        # so the pick is recognised against the item's own text.
        item_text = self.doc_template_box.itemText(
            self.doc_template_box.currentIndex())
        if text.strip() == item_text.strip():
            # A pick: what it stands for, prefixes and all.
            value = self.doc_template_box.currentData() or ""
        else:
            value = document_template_stem(text)
        self.project.update_document(self.current_id, doc_template=value)

    def _scene_divider_changed(self) -> None:
        if self.loading or not self.project or not self.current_id:
            return
        self.project.update_document(
            self.current_id,
            scene_divider=self.scene_divider_edit.text(),
        )

    def _document_has_toc(self, document) -> bool:
        """True when this document holds a ${ToC} placeholder.

        The contents settings (depth, marker style per level) live on the
        document that contains ${ToC}, so the metadata bar shows them when
        such a document is selected.
        """
        if document is None or self.project is None:
            return False
        try:
            content = self.project.read_content(document.id)
        except Exception:
            content = ""
        if "${ToC}" in content or "${toc}" in content.casefold():
            return True
        doc_template = (getattr(document, "doc_template", "") or "").strip().lower()
        return doc_template in {"contents", "toc"}

    def _refresh_toc_fold(self) -> None:
        """Show and populate the Contents settings for a ${ToC} document."""
        if not hasattr(self, "toc_fold"):
            return
        current = self._current_document()
        show = (self.project is not None and current is not None
                and self._document_has_toc(current))
        self.toc_fold.setVisible(show)
        if not show:
            return
        meta = self.project.metadata(current.id)
        try:
            depth = int(str(meta.get("ToC depth", "4")).strip() or "4")
        except Exception:
            depth = 4
        depth = max(1, min(depth, 4))
        self.toc_depth_box.blockSignals(True)
        self.toc_depth_box.setCurrentIndex(max(0, self.toc_depth_box.findData(depth)))
        self.toc_depth_box.blockSignals(False)
        for index, box in enumerate(self.toc_level_boxes, start=1):
            style = str(meta.get(f"ToC level {index}", "none")).strip() or "none"
            box.blockSignals(True)
            pos = box.findData(style)
            if pos < 0:
                pos = box.findData("none")
            box.setCurrentIndex(max(0, pos))
            box.setEnabled(index <= depth)
            box.blockSignals(False)

    def _toc_config_changed(self) -> None:
        if self.loading or not self.project:
            return
        current = self._current_document()
        if current is None or not self._document_has_toc(current):
            return
        depth = int(self.toc_depth_box.currentData() or 4)
        depth = max(1, min(depth, 4))
        entries = self.project.metadata_entries(current.id)
        values = {name: value for name, (value, _t, _o) in entries.items()}
        types = {name: vtype for name, (_v, vtype, _o) in entries.items()}
        options = {name: opts for name, (_v, _t, opts) in entries.items()}
        values["ToC depth"] = str(depth)
        types.setdefault("ToC depth", "text")
        options.setdefault("ToC depth", [])
        for index, box in enumerate(self.toc_level_boxes, start=1):
            key = f"ToC level {index}"
            values[key] = str(box.currentData() or "none")
            types.setdefault(key, "text")
            options.setdefault(key, [])
            box.setEnabled(index <= depth)
        self.project.set_metadata(current.id, values, types, options)
        self.statusBar().showMessage("Contents settings saved.", 2000)

    def _populate_metadata_table(self) -> None:
        """Point the inspector's metadata section at the current selection.

        Silent on its own: set_entries swallows the table's own signals,
        so a repopulation never reads as an edit. The Type row follows
        the *document* (folders included, where the table below shows
        project fields); it is always filled — stored Type, else the
        derived default — and the dropdown seeds itself with every Type
        the project already uses, so a typed value is selectable next
        time.
        """
        target = self._selection_metadata_target()
        self._metadata_target = target
        current = self._current_document()
        self._type_target_id = current.id if current else None
        if current is None or self.project is None:
            self.type_box.hide()
            self.type_label.hide()
            self.lock_box.hide()
            self.type_add_edit.hide()
            self.type_add_button.hide()
        else:
            self.type_box.show()
            self.type_label.show()
            self.lock_box.show()
            self.type_add_edit.show()
            self.type_add_button.show()
            self._fill_type_box(self.project.type_of(current))
            # The tick follows the marker file, which is the truth.
            self.lock_box.blockSignals(True)
            self.lock_box.setChecked(self.project.has_own_lock(current.id))
            self.lock_box.blockSignals(False)
        if target is None:
            self.metadata_table.set_entries({})
            self.metadata_table.set_required_context([], False)
        elif target == PROJECT_METADATA_TARGET:
            entries = dict(self.project.project_metadata())
            # A field created for a folder shows, empty, on every folder
            # (1.17): the catalogue names the fields, the values are the
            # project's own.
            for key in self.project.known_fields("folder"):
                entries.setdefault(key, ("", "text", []))
            self.metadata_table.set_entries(entries)
            self.metadata_table.set_required_context([], False)
        else:
            # Type is never a table row: the Type row above owns the
            # protected key (docs/MODEL.md). The template's own mark and
            # design are the design panel's business, not the table's.
            entries = self.project.metadata_entries(target)
            rows = {key: entry for key, entry in entries.items()
                    if key not in ("Type", TEMPLATE_KEY, TEMPLATE_CONFIG_KEY)}
            # A field created on one document of the kind shows, empty,
            # on every document of the kind (1.17): the catalogue names
            # the fields, the documents hold the values.
            group = (self.project.type_group(current) if current
                     else "document")
            for key in self.project.known_fields(group):
                rows.setdefault(key, ("", "text", []))
            # A document that holds ${ToC} carries its own contents settings
            # (depth and marker style per level), stored in its head/metadata
            # so each contents page can differ. Show them in the metadata bar
            # when the placeholder is present.
            if self._document_has_toc(current):
                rows.setdefault("ToC depth", ("4", "text", []))
                try:
                    depth = int(str(rows.get("ToC depth", ("4",))[0] or "4"))
                except Exception:
                    depth = 4
                depth = max(1, min(depth, 4))
                for level in range(1, depth + 1):
                    rows.setdefault(f"ToC level {level}", ("none", "text", []))
            self.metadata_table.set_entries(rows)
            doc_type = self.project.type_of(current) if current else ""
            self.metadata_table.set_required_context(
                self.project.required_fields_for_type(doc_type), True)
        self._refresh_toc_fold()

    def _lock_toggled(self, locked: bool) -> None:
        """Lock or unlock the selected document.

        The tick writes the marker file and takes it away again; the
        file is the truth, and the binder says so straight away.
        """
        if self.loading or not self.project or not self._type_target_id:
            return
        self.project.set_locked(self._type_target_id, locked)
        self._apply_lock_marks()

    def _apply_lock_marks(self) -> None:
        """Say, in the binder, which rows are locked.

        The lock is a marker file in the tree (docs/STORAGE.md), so the
        binder's own mark follows the file. A row that carries the lock
        says so outright; a row inside a locked folder says so in its
        tooltip, because the lock is the folder's and the writer should
        know which.

        The icon keeps saying what the thing *is* — a folder or a
        document — so the lock never makes a folder look like a file.
        """
        if not self.project:
            return
        locked = self.project.locked_ids()
        own = {document.id for document in self.project.documents()
               if self.project.has_own_lock(document.id)}
        for item in self._all_items():
            document_id = item.data(0, Qt.UserRole)
            if not document_id:
                continue
            if document_id in own:
                item.setToolTip(0, "Locked — changes are not saved")
                font = item.font(0)
                font.setBold(True)
                item.setFont(0, font)
            elif document_id in locked:
                item.setToolTip(0, "Locked by the folder it sits in — "
                                   "changes are not saved")

    def _fill_type_box(self, value: str) -> None:
        """Rebuild the Type dropdown and set it, without firing saves.

        The list is this document's own group's roles (1.17): a folder's
        dropdown never offers a document's role, and the other way
        round. The current value is always in the list — dropping it
        would leave free text that findText cannot resolve, so the
        exact-match snap could never fire.
        """
        previous, self.loading = self.loading, True
        try:
            self.type_box.clear()
            # Every option stays in the list, the current value included:
            # dropping it would leave free text that findText cannot
            # resolve, so the exact-match snap could never fire. The
            # current value shows through the line edit, not the index.
            seen: set[str] = set()
            # Preview target (Outliner selection) wins over open doc, so
            # the Type box says what the row you clicked actually is.
            document = self._current_document()
            preview_id = getattr(self, "_type_target_id", None)
            if preview_id and self.project:
                preview_doc = next((d for d in self.project.documents() if d.id == preview_id), None)
                if preview_doc is not None:
                    document = preview_doc
            group = "document"
            if self.project is not None and document is not None:
                group = self.project.type_group(document)
            if self.project is not None:
                for item in self.project.known_types(group):
                    self.type_box.addItem(item)
                    seen.add(item.casefold())
            if value and value.casefold() not in seen:
                # A role from another group, or one the writer typed and
                # stored: it stays visible, so the box says what it is.
                self.type_box.addItem(value)
            self.type_box.setCurrentText(value)
            self.type_warning.setVisible(False)
            self.type_keep_box.setChecked(False)
            self.type_keep_box.setVisible(False)
            self._type_near_miss_text = ""
        finally:
            self.loading = previous

    def _add_type(self) -> None:
        """Add a new role for this document's group (1.17).

        The catalogue is what the dropdown reads, so a role added here
        is offered to every document of the kind that follows — storing
        it on this one document alone would offer it to that one only.
        """
        if self.loading or not self.project or not self._type_target_id:
            return
        document = self._current_document()
        group = self.project.type_group(document) if document else "document"
        name = self.project.add_type(group, self.type_add_edit.text())
        if not name:
            return
        self.type_add_edit.clear()
        self._fill_type_box(name)
        self._snap_to_type(self.type_box.findText(name))
        self._save_metadata()

    def _type_changed(self) -> None:
        if self.loading or self._type_snapping:
            return
        self._type_snap_check()
        self._save_metadata()

    def _type_keep_toggled(self, checked: bool) -> None:
        """The near-miss checkbox: ticked = keep the typed variant.

        The default snapped it to the existing option; ticking restores
        what was typed (and saves it). Unticking offers the snap again.
        """
        if self.loading:
            return
        if checked and self._type_near_miss_text:
            self._type_snapping = True
            try:
                self.type_box.setCurrentText(self._type_near_miss_text)
            finally:
                self._type_snapping = False
            self._save_metadata()
        elif not checked:
            self._type_snap_check()

    def _type_snap_check(self) -> None:
        """Idiot-proof the Type box (docs/MODEL.md).

        An exact match selects that option — nothing new is added. A
        case-only variant ('book' when 'Book' exists) snaps to the
        existing option too, and the warning says so, offering the
        'Keep it as a separate type' checkbox to restore the variant.
        """
        text = self.type_box.currentText().strip()
        if self.type_keep_box.isChecked() and text:
            # The writer chose this variant; leave it alone.
            return
        self.type_warning.setVisible(False)
        self.type_keep_box.setVisible(False)
        self._type_near_miss_text = ""
        if not text:
            return
        exact = -1
        near = -1
        for index in range(self.type_box.count()):
            item = self.type_box.itemText(index)
            if item == text:
                exact = index
                break
            if near < 0 and item.casefold() == text.casefold():
                near = index
        if exact >= 0:
            self._snap_to_type(exact)
            return
        if near >= 0:
            existing = self.type_box.itemText(near)
            self._type_near_miss_text = text
            self.type_warning.setText(
                f"“{text}” looks like the existing type “{existing}”, "
                "so that is what was selected. Tick below only if you "
                "really want both — this box is for ADDING new types, "
                "and a new type looks the same as a built-in one until "
                "a template says otherwise."
            )
            self.type_warning.setVisible(True)
            self.type_keep_box.setVisible(True)
            self._snap_to_type(near)

    def _snap_to_type(self, index: int) -> None:
        """Select an existing option instead of storing a duplicate."""
        # An editable box keeps a stale index while free text is typed,
        # so the guard is on the text: a differing text means the snap
        # has real work even when the index already matches.
        if (self.type_box.currentIndex() == index
                and self.type_box.currentText() == self.type_box.itemText(index)):
            return
        self._type_snapping = True
        try:
            self.type_box.setCurrentIndex(index)
        finally:
            self._type_snapping = False

    def _metadata_table_changed(self) -> None:
        self._save_metadata()

    def _metadata_required_toggled(self, field: str, required: bool) -> None:
        """Mark a field required for every document/folder of this Type.

        A field named in one document belongs to that document's Type, so
        making it required makes it required for all of that Type (and the
        writer is told). The mark is stored on the project, not the row.
        """
        if self.loading or not self.project:
            return
        current = self._current_document()
        if current is None:
            return
        doc_type = self.project.type_of(current)
        if required:
            answer = QMessageBox.warning(
                self,
                "Make field required?",
                f'"{field}" will become mandatory for ALL documents and '
                f'folders of type "{doc_type}".\n\n'
                "An export will be blocked until the field is filled for "
                "every document of that type in the export (or you mark it "
                "not required).",
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Ok:
                # Revert the checkbox to match the stored state.
                self._populate_metadata_table()
                return
        self.project.set_type_required(doc_type, field, required)
        state = "required" if required else "not required"
        self.statusBar().showMessage(
            f'"{field}" is now {state} for all "{doc_type}" documents.', 4000)

    def _confirm_template_entry(self, document_id: str) -> bool:
        """The warning behind Template: yes (no button needed).

        Appears the instant Template: yes is entered in the metadata
        table. Returning False reverts the entry; True lets the caller
        persist the mark. The Confirm button reads "Yes, I am sure" —
        the deliberate act itself.
        """
        if not self.project:
            return False
        try:
            body_words = len(self.project.plain_text(
                self.project.read_content(document_id)).split())
        except Exception:
            body_words = 0
        try:
            other_fields = max(
                0, len(self.project.metadata(document_id)) - 1)
        except Exception:
            other_fields = 0
        detail = (
            "Converting a document to a template will no longer treat it "
            "as manuscript content. "
            f"This document has {body_words} words and {other_fields} "
            "non-template metadata. "
            "Are you sure you want to convert it to a template?")
        dlg = QDialog(self)
        dlg.setWindowTitle("Mark as template?")
        layout = QVBoxLayout(dlg)
        note = QLabel(detail)
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel, dlg)
        yes_button = QPushButton("Yes, I am sure")
        buttons.addButton(yes_button, QDialogButtonBox.ButtonRole.AcceptRole)
        yes_button.clicked.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)
        return dlg.exec() == QDialog.DialogCode.Accepted

    def _save_metadata(self) -> None:
        """Live save of the metadata section: the Type row plus the table.

        Type is protected (docs/MODEL.md): it always lives on the
        document's own metadata — folders included, where the table
        itself edits project fields — and never as a table row. A row
        named Type in the table is absorbed into the Type row rather
        than stored twice.
        """
        if self.loading or not self.project or not self._metadata_target:
            return
        table = self.metadata_table
        values, value_types, options = table.values_types_options()
        for row in range(table.rowCount()):
            key_item = table.item(row, 1)
            key = key_item.text().strip() if key_item else ""
            if not key and table.row_value(row):
                # A row that lost its name but still holds a value: mid-
                # rename, or a field whose key was cleared first. Writing
                # the named subset would drop that field from storage —
                # the write waits until the row names itself, is emptied
                # (the blank row then simply drops out of the subset), or
                # is removed outright.
                return
        values.pop("Type", None)
        value_types.pop("Type", None)
        options.pop("Type", None)
        target = self._metadata_target
        # Template: yes typed into the table pops up the moment it is
        # entered — no button needed. Even a knowing conversion must
        # pass a checkbox; a cancelled popup reverts the row.
        if target != PROJECT_METADATA_TARGET and self.project is not None:
            template_key = next(
                (k for k in list(values) if k.lower() == "template"), None)
            if template_key is not None:
                if template_key != TEMPLATE_KEY:
                    values[TEMPLATE_KEY] = values.pop(template_key)
                    if template_key in value_types:
                        value_types[TEMPLATE_KEY] = value_types.pop(template_key)
                    if template_key in options:
                        options[TEMPLATE_KEY] = options.pop(template_key)
                raw = str(values.get(template_key, "")).strip().lower()
                if raw in ("yes", "true", "1") and not self.project.is_template(target):
                    if not self._confirm_template_entry(target):
                        # Cancelled: revert the row to the DB state and
                        # stop the save before it lands anywhere.
                        self._populate_metadata_table()
                        return
                    # Confirmed: persist the mark through the backend,
                    # then let the normal save run (it carries Template
                    # from storage anyway) and swap the sidebar.
                    self.project.set_template(target, True, confirmed=True)
                    self.template_check.blockSignals(True)
                    self.template_check.setChecked(True)
                    self.template_check.blockSignals(False)
                    self._design_document_id = target
                    self._load_document_design(target)
                    self._show_template_panel(True)
                    self._refresh_outliner()
                    self.statusBar().showMessage(
                        "Template: yes confirmed — the document is now a template.",
                        5000)
        # A field the writer names joins its group's catalogue (1.17), so
        # it shows — empty — on every other document of the kind, even
        # when this one is left blank.
        group = "folder" if target == PROJECT_METADATA_TARGET else "document"
        if target != PROJECT_METADATA_TARGET:
            document = self._current_document()
            if document is not None:
                group = self.project.type_group(document)
        for key in values:
            if key not in ("Type", TEMPLATE_KEY, TEMPLATE_CONFIG_KEY):
                self.project.add_field(group, key)
        if target == PROJECT_METADATA_TARGET:
            self.project.set_project_metadata(
                {key: (values[key], value_types[key], options[key]) for key in values}
            )
            if self._type_target_id:
                self._write_document_type(self._type_target_id, None)
        else:
            self._write_document_type(target, (values, value_types, options))
        # The template rows are titled and defaulted from the Type, so
        # the panel is re-read after a save that may have changed it.
        # Nothing is stored by the refresh itself.
        self._refresh_book_widgets(self._current_document())

    def _write_document_type(self, document_id: str,
                             fields: tuple | None) -> None:
        """Write one document's own metadata, with Type from the Type row.

        ``fields`` is the (values, value_types, options) triple to store
        beside Type; None means keep every stored key and rewrite only
        Type — the folder case, where the table edits project fields.
        An empty Type row means unset: the key is dropped and type_of
        derives the default again. A value equal to the derived default
        is *not* written — storage stays clean and type_of shows the
        same word either way; an explicit change is stored.
        """
        document = next(
            (d for d in self.project.documents() if d.id == document_id), None)
        stored = self.project.metadata_entries(document_id)
        if fields is None:
            values = {k: v[0] for k, v in stored.items() if k != "Type"}
            value_types = {k: v[1] for k, v in stored.items() if k != "Type"}
            options = {k: v[2] for k, v in stored.items() if k != "Type"}
        else:
            values, value_types, options = (
                dict(fields[0]), dict(fields[1]), dict(fields[2]))
        values.pop("Type", None)
        value_types.pop("Type", None)
        options.pop("Type", None)
        # The template's own mark and design are not the table's business
        # (1.17): the design panel owns them, so they are carried over
        # as they stand rather than written from the rows above.
        for key in (TEMPLATE_KEY, TEMPLATE_CONFIG_KEY):
            if key in stored:
                values[key] = stored[key][0]
                value_types[key] = stored[key][1]
                options[key] = stored[key][2]
            else:
                values.pop(key, None)
                value_types.pop(key, None)
                options.pop(key, None)
        type_value = self.type_box.currentText().strip()
        if type_value and document is not None:
            if ("Type" in stored
                    or type_value != self.project.default_type(document)):
                values["Type"] = type_value
                value_types["Type"] = "text"
                options["Type"] = []
        self.project.set_metadata(document_id, values, value_types, options)

    def _refresh_related(self) -> None:
        """Fill the Related files section (1.13): a fresh scan, never
        stored. Two groups — titles found in this document, and
        documents containing this one's title — each shown only when it
        has members; no matches collapses the whole section. The whole
        scan is off when Preferences says so (related/enabled) — for a
        project where it is more noise than help, off means off: no
        scan runs and the section stays gone.
        """
        self.related_tree.clear()
        mentions: list = []
        mentioned_by: list = []
        if (self.project and self.current_id
                and QSettings().value("related/enabled", True, type=bool)):
            mentions, mentioned_by = self.project.related_documents(self.current_id)
        for label, documents in (("Mentions", mentions), ("Mentioned by", mentioned_by)):
            if not documents:
                continue
            group = QTreeWidgetItem([label])
            for document in documents:
                child = QTreeWidgetItem(
                    [f"{document.title}  ({self._kind_label(document)})"])
                child.setData(0, Qt.UserRole, document.id)
                child.setToolTip(0, self._kind_label(document))
                group.addChild(child)
            self.related_tree.addTopLevelItem(group)
            group.setExpanded(True)
        self.related_section.setVisible(self.related_tree.topLevelItemCount() > 0)

    def _related_clicked(self, item, _column) -> None:
        """Jump to a related document — group headers carry no id, so a
        click on "Mentions" itself does nothing. Same jump as a wiki
        link: save here first, then switch."""
        document_id = item.data(0, Qt.UserRole)
        if document_id:
            self.save_current()
            self._select_document(document_id)

    # --- tracked changes (2.4) ---------------------------------------------

    def _toggle_tracking(self, checked: bool) -> None:
        self.editor.set_tracking(checked)
        if checked:
            self.statusBar().showMessage(
                "Tracking changes — new revision pass started", 3000)
        else:
            self.statusBar().showMessage("Tracking changes off", 3000)

    def accept_all_changes(self) -> None:
        self._resolve_all_changes(accept=True)

    def reject_all_changes(self) -> None:
        self._resolve_all_changes(accept=False)

    def _drop_rows_then_save(self, change_ids: set) -> bool:
        """Delete the resolved rows, write, and put the rows back on failure.

        The rows have to go *before* the save, not after: save_current()
        runs reconcile_revisions, which prunes pending rows by counting the
        marks left in the stored HTML and keeping the first N in sequence
        order. That count is only right when exactly the resolved rows are
        already gone — resolving the earlier of two passes would otherwise
        prune the later one's row instead. And a failed write puts the
        rows straight back under their own ids: the marks are still on
        disk, and those rows are the only thing that can ever accept or
        reject them again (the live document still carries the same ids
        in its surviving marks).
        """
        rows = [revision for revision in self.project.revisions(self.current_id)
                if revision.id in change_ids]
        self.project.remove_revisions(list(change_ids))
        if self.save_current():
            return True
        self.project.restore_revisions(rows)
        return False

    def _resolve_all_changes(self, accept: bool) -> None:
        """Accept or reject every pending change in the open document.
        The editor is mutated first, the rows go, then one save writes
        the resolved form and its recomputed word count."""
        if not self.project or not self.current_id:
            return
        if not self.save_current():
            return
        pending = self.project.revisions(self.current_id)
        if not pending:
            self.statusBar().showMessage("No pending changes in this section",
                                         3000)
            return
        change_ids = {revision.id for revision in pending}
        if accept:
            revisions.apply_accept(self.editor.document(), change_ids)
        else:
            revisions.apply_reject(self.editor.document(), change_ids)
        if not self._drop_rows_then_save(change_ids):
            return
        self._update_word_count()
        verb = "accepted" if accept else "rejected"
        self.statusBar().showMessage(
            f"{len(change_ids)} change(s) {verb}", 3000)

    def revision_passes(self) -> None:
        """Accept or reject one whole revision pass of this section."""
        if not self.project or not self.current_id:
            return
        if not self.save_current():
            return
        sessions = self.project.revision_sessions(self.current_id)
        if not sessions:
            self.statusBar().showMessage("No pending changes in this section",
                                         3000)
            return
        dialog = RevisionPassesDialog(sessions, self)
        if dialog.exec() != QDialog.Accepted:
            return
        session_id = dialog.selected_session()
        if not session_id:
            return
        change_ids = {
            revision.id
            for revision in self.project.revisions(self.current_id)
            if revision.session_id == session_id
        }
        if not change_ids:
            return
        if dialog.chosen_accept:
            revisions.apply_accept(self.editor.document(), change_ids)
        else:
            revisions.apply_reject(self.editor.document(), change_ids)
        if not self._drop_rows_then_save(change_ids):
            return
        self._update_word_count()
        verb = "accepted" if dialog.chosen_accept else "rejected"
        self.statusBar().showMessage(
            f"Pass {verb} ({len(change_ids)} change(s))", 3000)

    # --- AI (2.4, layer 2) --------------------------------------------------

    def _ai_continue_scene(self) -> None:
        """Ask the model to continue the section at the caret. The
        answer lands as a tracked insertion with its own session and the
        "AI" byline — the tracking toggle does not gate it, and the
        model never writes the document directly."""
        if not self.project or not self.current_id:
            return
        self.save_current()
        # Tail, not head: what matters for a continuation is the prose
        # just before the caret, not the chapter opening.
        context = Project.plain_text(self.editor.toHtml())[-4000:]
        cursor = self.editor.textCursor()
        position = cursor.position()
        try:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                text = ai.complete(
                    "You are a co-author continuing a novel scene. Keep "
                    "the established voice, tense and point of view. "
                    "Return only the continuation prose — no preamble, no "
                    "quotation marks, no commentary.",
                    context,
                )
            finally:
                QApplication.restoreOverrideCursor()
        except ai.AIError as error:
            QMessageBox.warning(self, "AI request failed", str(error))
            return
        if not text:
            self.statusBar().showMessage("AI returned nothing", 3000)
            return
        self.editor.apply_tracked_replacement(position, position, text,
                                              uuid4().hex, "AI")
        self._content_changed()
        self.statusBar().showMessage(
            "AI continuation inserted as a tracked change", 4000)

    def _ai_prompt(self) -> None:
        """Ask the AI whatever is on the writer's mind: a small popup,
        one message in, and the reply back as a tracked insertion at the
        caret. The scene before the caret still gives it the thread —
        a question out of nowhere answers out of nowhere."""
        if not self.project or not self.current_id:
            self.statusBar().showMessage(
                "Open a scene before prompting", 4000)
            return
        popup = QDialog(self)
        popup.setWindowTitle("Ask the AI")
        layout = QVBoxLayout(popup)
        prompt_area = QPlainTextEdit()
        prompt_area.setPlaceholderText("Your prompt…")
        layout.addWidget(QLabel("Prompt"))
        layout.addWidget(prompt_area)
        reply_area = QPlainTextEdit()
        reply_area.setReadOnly(True)
        reply_area.setPlaceholderText("The answer shows here.")
        layout.addWidget(QLabel("Answer (tracked insertion)"))
        layout.addWidget(reply_area)
        status = QLabel()
        layout.addWidget(status)
        buttons = QHBoxLayout()
        ask = QPushButton("Ask")
        insert = QPushButton("Insert at caret")
        insert.setEnabled(False)
        close = QPushButton("Close")
        close.clicked.connect(popup.accept)
        buttons.addWidget(ask)
        buttons.addWidget(insert)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        latest: dict[str, str] = {}

        def ask() -> None:
            prompt = prompt_area.toPlainText().strip()
            if not prompt:
                return
            context = Project.plain_text(self.editor.toHtml())[-4000:]
            try:
                QApplication.setOverrideCursor(Qt.WaitCursor)
                try:
                    text = ai.complete(
                        "You are a co-author helping with a novel scene. "
                        "Keep the established voice, tense and point of "
                        "view of the context that follows.\n\n"
                        "Context (the scene so far):\n" + context,
                        prompt,
                    )
                finally:
                    QApplication.restoreOverrideCursor()
            except ai.AIError as error:
                status.setText(str(error))
                return
            latest["text"] = text
            reply_area.setPlainText(text)
            insert.setEnabled(True)
            status.setText("Ready to insert as a tracked change.")

        ask.clicked.connect(ask)
        insert.clicked.connect(
            lambda: (self.editor.apply_tracked_replacement(
                self.editor.textCursor().position(),
                self.editor.textCursor().position(),
                latest.get("text", ""), uuid4().hex, "AI"),
                self._content_changed(),
                popup.accept()))
        popup.exec()

    def _ai_proofread_selection(self) -> None:
        """Proofread in place as one tracked pair: the old words stay
        struck behind the corrected ones until accepted. Like read
        aloud, the scope is the selection — or the whole scene when
        nothing is selected."""
        if not self.project or not self.current_id:
            self.statusBar().showMessage(
                "Select the text to proofread first", 4000)
            return
        cursor = self.editor.textCursor()
        document = self.editor.document()
        if cursor.hasSelection():
            start, end = cursor.selectionStart(), cursor.selectionEnd()
        else:
            start, end = 0, len(document.toPlainText())
        text = document.toPlainText()[start:end].replace("\u2029", "\n")
        if not text.strip():
            self.statusBar().showMessage(
                "This scene is empty", 3000)
            return
        self.save_current()
        try:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                text = ai.complete(
                    ai.proofread_system(),
                    text,
                )
            finally:
                QApplication.restoreOverrideCursor()
        except ai.AIError as error:
            QMessageBox.warning(self, "AI request failed", str(error))
            return
        if not text:
            self.statusBar().showMessage("AI returned nothing", 3000)
            return
        # save_current never moves the live document, so the captured
        # selection offsets still hold.
        self.editor.apply_tracked_replacement(start, end, text,
                                              uuid4().hex, "AI")
        self._content_changed()
        self.statusBar().showMessage(
            "Proofread text applied as a tracked change", 4000)

    # --- read aloud (3.1) ----------------------------------------------------

    def _speech(self) -> tts.SpeechReader | None:
        """The window's engine, connecting it on first use."""
        if self._reader is None:
            self._reader = tts.SpeechReader(self)
        return self._reader if self._reader.ensure_engine() else None

    def speak_aloud(self) -> None:
        """Read aloud (3.1): the selection if there is one, from the
        cursor onwards when there isn't, from the beginning of the scene
        when the caret is not in the text at all."""
        reader = self._speech()
        if reader is None:
            self.statusBar().showMessage(
                "No text-to-speech engine on this system", 4000)
            return
        text = tts.speech_text(
            self.editor.textCursor().selectedText()).strip()
        if not text:
            cursor = self.editor.textCursor()
            whole = self.editor.toPlainText()
            position = min(cursor.position(), len(whole))
            text = tts.speech_text(whole[position:]).strip()
            if not text:
                text = tts.speech_text(whole).strip()
            if not text:
                self.statusBar().showMessage(
                    "Nothing to read — open a scene first"
                    if not self.project or not self.current_id
                    else "This scene is empty", 3000)
                return
        self._speech_paused = False
        if reader.speak(text):
            self.statusBar().showMessage("Reading aloud", 2000)
        else:
            self.statusBar().showMessage(
                "Could not speak — check the TTS settings", 4000)

    def toggle_speech_pause(self) -> None:
        """F9: pause while speaking, resume while paused, nothing else."""
        reader = self._speech()
        if reader is None:
            return
        if self._speech_paused:
            reader.resume()
            self._speech_paused = False
            self.statusBar().showMessage("Resumed", 1500)
        else:
            reader.pause()
            self._speech_paused = True
            self.statusBar().showMessage("Paused", 1500)

    def stop_speech(self) -> None:
        """Stop the engine; safe to call when nothing is connected —
        document switches and window close run it unconditionally."""
        if self._reader is not None:
            self._reader.stop()
        self._speech_paused = False

    def save_current(self) -> bool:
        """Write the open document through Project; False means it failed.

        The return value is the contract that keeps a failed write from
        costing the writer their text: a dialog has already explained the
        failure, and every caller that would destroy or outlive the
        in-memory copy — a document switch, a project switch, closing, a
        backup, an export that reads stored HTML — has to stop rather than
        carry on against stale disk state. Nothing to save (no project,
        no document, mid-load) is a success, not a failure.
        """
        if not self.project or self.loading:
            return True
        if self.current_template_file is not None:
            return self._save_template_file()
        if not self.current_id:
            return True
        self.save_timer.stop()
        # A locked document is not written. The writing stays exactly as
        # it is — which is the point of locking a published book — and
        # the writer is told, so nothing is ever lost silently.
        if self.project.is_locked(self.current_id):
            holder = self.project.lock_holder(self.current_id)
            self.statusBar().showMessage(
                f"Locked{'' if holder is None else f' ({holder})'} — "
                "not saved", 5000)
            return True
        # A container is not prose: the editor is empty for it, so
        # writing would put an empty index.html in the folder. Its own
        # words, if it has any, are not edited from here.
        is_container = self.current_id in self.project.container_ids()
        try:
            if not is_container:
                # The editor shows the prose as the writer means it, so a
                # placeholder they are talking about reads `${Title}`; the
                # file keeps the escape that protects it from the export.
                html = self.project.canonicalize_links(escape_placeholders(
                    scaled_font_sizes(self.editor.toHtml(), 1.0 / self.zoom)))
                self.project.write_content(self.current_id, html)
            # Tracked changes (2.4): the stored HTML is the truth after
            # every save — undo can orphan rows by removing marks behind
            # the table's back, and this is where that heals.
            if not is_container:
                self.project.reconcile_revisions(self.current_id)
            self.project.update_document(
                self.current_id,
                synopsis=self.synopsis.toPlainText(),
                word_target=self.target.value(),
            )
            # 1.9 session resume: written on every save path (debounce,
            # document switch, close) so a crash still resumes — the
            # caret offset is plain-text characters, same units on read.
            self.project.set_setting("last_document", self.current_id)
            self.project.set_setting(
                "last_cursor", str(self.editor.textCursor().position())
            )
            # The order files on disk follow the binder order, so the
            # sequence can never be lost (docs/STORAGE.md).
            self.project.write_order_files()
            self.statusBar().showMessage("Saved", 1500)
            self._refresh_outliner()
            # Counts follow the just-saved totals (6): the label is
            # permanent, so this leaves the "Saved" notice alone.
            self._update_word_count()
        except Exception as error:
            QMessageBox.critical(self, "Save failed", str(error))
            return False
        return True

    def _save_template_file(self) -> bool:
        """Write the open template file back to its own path.

        The file is the truth (§1.1): the editor held a copy of it and
        this is the copy going home, written whole and in place — no
        DB, no reconciliation, no canonicalisation of links, because
        none of that is a fragment's business. The stored text is kept
        as written: the compile is what strips the editor's furniture
        (§3.2), so what lands on disk stays readable by any editor.
        """
        path = self.current_template_file
        if path is None:
            return True
        if self._project_is_read_only():
            self.statusBar().showMessage(
                "This project's templates are read-only.", 4000)
            return False
        self.save_timer.stop()
        if path.suffix == ".txt":
            fragment = self.editor.toPlainText()
        else:
            # No zoom unscale here, unlike a document save: the file was
            # loaded as it stands, so what the editor hands back is what
            # went in — unscaling would shrink it on every save.
            fragment = self.editor.toHtml()
        # Head and fragment back together (§3.1): the config box holds
        # the file's head, the editor its fragment, and what lands on
        # disk is the two in their own places.
        text = join_head(self.template_config_box.toPlainText(), fragment)
        try:
            temporary = path.with_name(path.name + ".tmp")
            temporary.write_text(text, encoding="utf-8")
            temporary.replace(path)
        except OSError as error:
            QMessageBox.critical(self, "Save failed", str(error))
            return False
        self._save_template_css()
        self.statusBar().showMessage("Saved", 1500)
        self._warn_about_unknown_placeholders(fragment, path)
        return True

    def _warn_about_unknown_placeholders(self, fragment: str,
                                         path: Path) -> None:
        """Say so when a template names a placeholder Lekhak does not know.

        An unknown name prints nothing, so a misspelling is silent: the
        file saves, the export runs, and a page comes out empty. The
        writer is told once per file per set of issues — the file is still
        written, because a tripwire is not a gate and one wrong name does
        not make the rest of the template wrong.
        """
        issues = validate_slot(fragment, html=path.suffix != ".txt",
                               known_keys=self._known_placeholder_keys())
        if issues == self._template_issues:
            return
        self._template_issues = issues
        if not issues:
            return
        QMessageBox.warning(
            self, "Check the placeholders",
            "This template names a placeholder Lekhak does not know, so it "
            "will print nothing where it appears:\n\n"
            + "\n".join(f"• {issue}" for issue in issues)
            + "\n\nThe file has been saved as it stands. The names Lekhak "
              "knows are in the Placeholders reference.")

    def _known_placeholder_keys(self) -> tuple[str, ...]:
        """The names a template may use besides the built-ins: the
        project's own fields, and the Types it knows — each Type answers
        to its own word and to the plurals a writer would reach for."""
        if not self.project:
            return ()
        names = set(self.project.metadata_keys())
        names.update(self.project.project_metadata())
        for group in ("folder", "document"):
            names.update(self.project.known_fields(group))
            for type_name in self.project.known_types(group):
                # The word the writer gave the type, used as it stands.
                names.add("children-" + type_name.casefold())
        return tuple(sorted(names))

    def manual_save(self) -> None:
        if not self.project:
            return
        if self.project.is_locked(self.current_id or ""):
            # Saving a locked document would change a book the writer
            # has deliberately set aside. Say so plainly, and say what
            # to do about it.
            holder = self.project.lock_holder(self.current_id or "")
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Information)
            box.setWindowTitle("Locked")
            box.setText("This document is locked, so it was not saved.")
            box.setInformativeText(
                (f"‘{holder}’ is locked" if holder else "This document is locked")
                + ", so nothing Lekhak does will change it.\n\n"
                "You can still write in the editor — the words stay in the "
                "window until you move on, but they are not saved to the "
                "file.\n\n"
                "To change it, untick Locked beside the Type row.")
            box.exec()
            return
        if not self.save_current():
            return          # the dialog has explained; no backup either
        self._make_backup("Saved and backed up", force=True)

    def _make_backup(self, message: str = "Backed up", *, force: bool = False) -> None:
        """Take a backup of the project.

        The manuscript autosaves, so a backup is not a save: it is the
        copy the writer would mourn losing. Backups may be skipped when
        nothing has changed since the last backup.
        """
        if not self.project:
            return
        try:
            destination = self.project.backup(force=force)
            if destination is None:
                return          # a read-only project has nothing to copy
            self.statusBar().showMessage(f"{message} to {destination.name}",
                                         5000)
        except Exception as error:
            QMessageBox.critical(
                self, "Backup failed",
                f"Your document was saved, but the backup could not be "
                f"created.\n\n{error}",
            )

    def _backup_is_due(self, *, closing: bool = False) -> bool:
        """Whether an automatic backup is due under the chosen frequency.

        The project's own setting wins; otherwise the app-wide default
        applies. "on-close" backs up when closing. Manual Ctrl+S is an
        explicit request handled separately.
        """
        if not self.project:
            return False
        frequency = self.project.get_setting("backup_frequency") or \
            QSettings().value("files/backupFrequency", "on-close", type=str)
        if frequency == "never":
            return False
        if frequency == "every-save":
            return True
        if frequency == "on-close":
            return closing
        last = self.project.get_setting("last_backup")
        if not last:
            return True
        try:
            when = datetime.fromisoformat(last)
        except ValueError:
            return True
        delta = datetime.now(timezone.utc) - when
        if frequency == "hourly":
            return delta >= timedelta(hours=1)
        if frequency == "daily":
            return delta >= timedelta(days=1)
        if frequency == "weekly":
            return delta >= timedelta(days=7)
        return closing

    def start_update_check(self) -> None:
        """Auto and Check look once a day, on a worker thread.

        Manual (the default) and Nothing never get here: the writer has
        not consented, so nothing goes online. When they have, nothing on
        the path blocks the window — the network call and any ``git pull``
        happen off the UI thread, and the result arrives as one sentence
        in the status bar. An install that cannot be updated in place
        (pip, zip, a checkout with local edits, a major version) is told
        about the release instead of being rewritten.
        """
        if not update_check_due():
            return
        QSettings().setValue("update/lastCheck", datetime.now().isoformat())
        mode = update_mode()

        def work() -> None:
            try:
                found = newer_release()
            except Exception:
                return                  # offline is not news
            if found is None:
                return
            latest, current = found["latest"], found["current"]
            if mode == "auto" and apply_update(current, latest):
                self.update_news.emit(
                    f"Lekhak {latest} was installed — restart to use it.")
                return
            self.update_news.emit(
                f"Lekhak {latest} is available (you have {current}) — "
                "Preferences → Updates installs it.")

        threading.Thread(target=work, daemon=True).start()

    def _say_update_news(self, message: str) -> None:
        self.statusBar().showMessage(message, 30000)

    def reopen_last_project(self) -> None:
        """1.9 resume: reopen the project that was open at last close.

        App-level QSettings points at the project; the document and caret
        inside it come from that project's own settings (see
        _switch_project). Any failure — moved, deleted, corrupt — falls
        back to the empty window; no dialog at startup.
        """
        if not QSettings().value("session/reopenLast", True, type=bool):
            return
        path = QSettings().value("session/lastProject", "", type=str)
        if not path or not Path(path).exists():
            # A fresh install: open the guide, so the software can be
            # looked at before anyone is asked to create a project.
            self.open_guide()
            return
        project = Project(Path(path))
        try:
            project.open()
            self._switch_project(project)
        except Exception:
            # _switch_project may have half-assigned state before failing;
            # rewind so the fallback really is the empty window. close() is
            # safe even if open() never got far (connection starts None).
            project.close()
            self.project = None
            self.current_id = None
            return

    def choose_backup_folder(self) -> None:
        """Point this project's backups at a folder of the writer's choosing.

        Backups live outside the project: a project that is lost must not
        take its backups with it.
        """
        if not self.project:
            return
        current = self.project.get_setting("backup_folder") or \
            QSettings().value("files/backupFolder", "", type=str) or \
            str(self.project.root.parent)
        selected = QFileDialog.getExistingDirectory(
            self, "Choose backup folder", current,
        )
        if not selected:
            return
        self.project.set_backup_folder(Path(selected))
        destination = self.project.backup_directory()
        QMessageBox.information(
            self, "Backup folder changed",
            f"Backups for this project will be stored in:\n\n{destination}",
        )

    def _confirm_reserved_name(self, title: str) -> bool:
        """True when the writer is happy with a reserved name.

        ``index`` is Lekhak's own: in every folder, ``index.html`` holds
        the folder's own text — its own words — and not a child document.
        A document with that title is written as ``_index.html``, so
        nothing collides and nothing is lost; the writer is told what the
        name means and asked to say they know it. Nothing is created or
        renamed until they have.
        """
        if title.strip().casefold() != "index":
            return True
        return confirm_destructive(
            self, "A reserved name",
            "“index” is a reserved name in every folder.",
            confirm="Use it",
            checkbox="A folder's index.html holds the folder's own text — "
                     "its own words — and not a child document. A document "
                     "called “index” is written as _index.html, and the "
                     "title still reads “index” in the binder.",
            gate="I know this will contain the folder's own text and not be "
                 "a child document.")

    def _title_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self.loading or not self.project or self._reverting_title:
            return
        document_id = item.data(0, Qt.UserRole)
        title = item.text(0).strip() or "Untitled"
        if self._project_is_read_only():
            # The guide is not ours to rename: put the title back and say
            # why, rather than letting the change vanish silently.
            current = next((d for d in self.project.documents()
                            if d.id == document_id), None)
            self._reverting_title = True
            item.setText(0, current.title if current else "Untitled")
            self._reverting_title = False
            self.statusBar().showMessage(
                "The guide is read-only — take a copy if you want to change "
                "it.", 5000)
            return
        if not self._confirm_reserved_name(title):
            # The writer said no: the title stays what it was, and
            # nothing is written.
            current = next((d for d in self.project.documents()
                            if d.id == document_id), None)
            self._reverting_title = True
            item.setText(0, current.title if current else "Untitled")
            self._reverting_title = False
            return
        item.setText(0, title)
        self.project.update_document(document_id, title=title)
        self._refresh_corkboard()
        self._refresh_outliner()

    def _tree_structure_changed(self, *args) -> None:
        """A drop rewrote the binder: persist it once the tree settles.

        The signals arrive while the tree is still half-written — the
        source rows are already gone and the destination's children
        are not all in yet — so sweeping then would read a transient
        shape. One pass on the next turn of the event loop sees the
        tree as it ended up.
        """
        if self.loading or not self.project or self._tree_move_pending:
            return
        self._tree_move_pending = True
        QTimer.singleShot(0, self._run_tree_sweep)

    def _run_tree_sweep(self) -> None:
        self._tree_move_pending = False
        self._tree_moved()

    def _tree_moved(self, *args) -> None:
        if self.loading or not self.project:
            return
        # Synthetic rows (the Templates area) are furniture, not binder
        # documents: their data(0, Qt.UserRole) is None, and a None id
        # reaching reorder() raises "cannot contain itself" — which
        # used to abort the sweep mid-way, leaving the tree showing one
        # order and the database another until the next reload. They are
        # skipped as parents *and* never renumbered as children.
        failed: str | None = None

        def sweep(parent_item) -> None:
            nonlocal failed
            count = (self.tree.topLevelItemCount()
                     if parent_item is None else parent_item.childCount())
            parent_id = (None if parent_item is None
                         else parent_item.data(0, Qt.UserRole))
            for position in range(count):
                item = (self.tree.topLevelItem(position)
                        if parent_item is None
                        else parent_item.child(position))
                document_id = item.data(0, Qt.UserRole)
                if document_id is None:
                    continue          # a row of furniture, not a document
                # A row at the top level has no parent id, which is a
                # real answer (the project root), not a reason to skip
                # it: skipping here used to leave a drag to the root
                # written nowhere at all.
                if not self.project.reorder(document_id, parent_id, position):
                    failed = failed or item.text(0)

        for parent_item in [None] + self._all_items():
            if parent_item is not None and parent_item.data(0, Qt.UserRole) is None:
                continue              # the Templates group holds no documents
            sweep(parent_item)
        if failed is not None:
            # The folder tree would not take the move — a name already
            # taken in the destination, most often — so nothing about
            # that row was written. Put the binder back to the files,
            # which are the truth, and say why.
            self.statusBar().showMessage(
                f"Cannot move “{failed}”: that name is already in the "
                "destination folder", 6000)
            self._reload_tree()
            return
        # The order files follow the binder, so a drag is not lost the
        # next time the project is opened (docs/STORAGE.md).
        self.project.write_order_files()
        self._refresh_outliner()

    def _all_items(self) -> list[QTreeWidgetItem]:
        result = []
        def collect(item):
            result.append(item)
            for index in range(item.childCount()):
                collect(item.child(index))
        for index in range(self.tree.topLevelItemCount()):
            collect(self.tree.topLevelItem(index))
        return result

    def _select_document(self, document_id: str) -> None:
        for item in self._all_items():
            if item.data(0, Qt.UserRole) == document_id:
                self.tree.setCurrentItem(item)
                return

    def _style_corkboard(self) -> None:
        """Cards read as cards, without trusting the theme (UI-NOTES 3).

        The colours are derived from the palette and inverted rather than
        chosen: a dark desktop gets light cards, a light one gets dark cards,
        so a card can never blend into its own background because a theme we
        do not control rendered our colours against us. Only the framing —
        padding, border, radius, spacing — is ours.
        """
        # Application palette, never a widget's own: a widget the
        # stylesheet styles answers palette queries with the stylesheet's
        # last literals, and every rebuild would freeze the previous
        # theme's colours forever (the selection stuck on System's
        # orange through a switch to Light).
        pal = QApplication.palette()
        chrome = pal.window().color()
        ink_default = pal.windowText().color()
        accent = pal.highlight().color().name()
        dark_chrome = chrome.lightness() < 128
        card = "#f4f4f4" if dark_chrome else "#232323"
        ink = "#1b1b1b" if dark_chrome else "#f2f2f2"
        self.corkboard.setSpacing(8)
        self.corkboard.setStyleSheet(
            f"QListWidget {{ background: {chrome.name()};"
            f" color: {ink_default.name()}; }}\n"
            f"QListWidget::item {{ background: {card}; color: {ink};"
            " border: 1px solid rgba(128, 128, 128, 0.35);"
            " border-radius: 8px; padding: 10px 12px; margin: 2px; }\n"
            f"QListWidget::item:selected {{ border: 2px solid {accent}; }}\n"
        )

    # -- the interface look ------------------------------------------------
    #
    # One surface theory for the whole window (UI-NOTES 3): manuscript is
    # ink on brushed metal, proxies are warm sand glass, ambience is grey
    # glass. Rows carry the theory room by room: the binder, outliner and
    # corkboard tint row by row, the inspector's folds keep their indigo
    # headers, and the chrome (toolbar, tabs) stays neutral so prose
    # frames never compete with prose.

    # Sand for proxy rows, grey for ambience — translucent so both read
    # as glass over whatever the theme puts behind them.
    _TINT_PROXY = QColor(201, 164, 92, 52)
    _TINT_AMBIENCE = QColor(128, 128, 128, 40)
    _TINT_WALL = "rgba(120, 150, 110, 0.05)"
    _ink_ambience = QColor(128, 128, 128, 180)  # until the theme is read

    _KIND_NOTES = {
        "manuscript": "Manuscript — printed text.",
        "proxy": "Proxy — formats this material; never printed itself.",
        "ambience": "Ambience — housekeeping; the export never reads it.",
    }

    def _row_kind(self, document) -> str:
        """What sort of row this is, for the interface's one theory:
        manuscript (prose and structure), proxy (template-marked markup),
        ambience (notes and empty leaves)."""
        if document is None:
            return "manuscript"
        if self.project is not None and self.project.is_template(document.id):
            return "proxy"
        if document.kind not in ("folder", "chapter") and not document.word_count:
            return "ambience"
        return "manuscript"

    def _serif(self, size: int, bold: bool = False) -> QFont:
        """A serif for titles and headings, whatever the system lacks:
        Vera when it is there, else the first serif family to hand."""
        families = QFontDatabase().families()
        family = next(
            (name for name in families if "vera serif" in name.casefold()),
            next((name for name in families if "serif" in name.casefold()),
                 "serif"))
        font = QFont(family, size)
        font.setBold(bold)
        return font

    @staticmethod
    def _blend(color: QColor, toward: QColor, amount: float) -> QColor:
        return QColor(
            round(color.red() + (toward.red() - color.red()) * amount),
            round(color.green() + (toward.green() - color.green()) * amount),
            round(color.blue() + (toward.blue() - color.blue()) * amount))

    @staticmethod
    def _theme_palette(name: str):
        """A fixed palette for a named theme, or None for System."""
        from PySide6.QtGui import QPalette
        if name in ("system", "", None):
            return None
        pal = QPalette()
        # Palettes picked for readability first: light themes keep text
        # >= 4.5:1 on the window, dark themes >= 7:1, highlights >= 4.5:1
        # against the text they sit on.
        # Accents must read as accents: grey sky (#9db0c3) set the bar —
        # selection, tab underline and focus all show it clearly. Each
        # highlight has real chroma at >=4.5:1 against its own window,
        # with highlighted text >=4.5:1 on the accent. The chrome itself
        # stays calm — only the accent carries the colour.
        # Earth is the one allowed to be a touch more visible: its
        # swatches (clay, fresh bark, olive, sand, herb, seed puff) are
        # the source — seed-puff window, clay ink, clay accent.
        # Light follows a blue palette: ivory window, blue-black ink,
        # turquoise accent (#1a7f7f — the swatch's teal idea, dark enough
        # that white text on it clears 4.5:1).
        schemes = {
            "light": ("#f8f5f1", "#263340", "#ffffff", "#1a7f7f", "#ffffff"),
            "light-accents": ("#faf2f1", "#3d3336", "#fffcfb", "#c492b1", "#2d2023"),
            "earth": ("#f6ece1", "#41302c", "#fdfaf4", "#8f5b53", "#ffffff"),
            "dark": ("#2a2e35", "#e9e7e4", "#171b20", "#9db0c3", "#12161c"),
            "dark-accents": ("#2f2729", "#f0e8e8", "#1c1618", "#c77f8f", "#241819"),
        }
        window, text, base, highlight, hilight_text = schemes.get(name, schemes["light"])
        pal.setColor(QPalette.ColorRole.Window, QColor(window))
        pal.setColor(QPalette.ColorRole.WindowText, QColor(text))
        pal.setColor(QPalette.ColorRole.Base, QColor(base))
        pal.setColor(QPalette.ColorRole.AlternateBase, QColor(base))
        pal.setColor(QPalette.ColorRole.Text, QColor(text))
        pal.setColor(QPalette.ColorRole.Highlight, QColor(highlight))
        pal.setColor(QPalette.ColorRole.HighlightedText, QColor(hilight_text))
        # Buttons carry a tint of the accent, not the full accent: full
        # accent made every control one solid colour block and the
        # selections beside them disappeared into it. A window-ward tint
        # plus an accent border (see _apply_interface_style) keeps each
        # theme's feel while buttons stay separate objects. Text stays
        # the window's ink — the tint never darkens enough to lose it.
        pal.setColor(QPalette.ColorRole.Button,
                     MainWindow._blend(QColor(window), QColor(highlight), 0.18))
        pal.setColor(QPalette.ColorRole.ButtonText, QColor(text))
        return pal

    def apply_theme(self) -> None:
        name = QSettings().value("ui/theme", "system", type=str)
        pal = self._theme_palette(name)
        if pal is not None:
            QApplication.instance().setPalette(pal)
        else:
            # System: reset to the style's standard palette
            QApplication.instance().setPalette(QApplication.style().standardPalette())
        self._apply_interface_style()
        self._style_corkboard()

    def _apply_interface_style(self) -> None:
        """The window's look: serif titles, kinded rows, calm chrome.

        Colours are derived from the palette (like _style_corkboard) so a
        light or dark desktop both work; only the tints and the indigo
        are house choices.
        """
        tree = self.tree
        # Application palette, not the widget's — see _style_corkboard:
        # a styled widget answers with the stylesheet's own literals.
        pal = QApplication.palette()
        ink = pal.windowText().color()
        accent = pal.highlight().color()
        accent_hex = accent.name()
        title_ink = pal.highlightedText().color().name()
        ink_hex = ink.name()
        # Indigo headings: ink pulled halfway toward a calm blue.
        indigo = self._blend(ink, QColor(91, 125, 177), 0.55).name()
        self._ink_ambience = QColor(ink)
        self._ink_ambience.setAlpha(180)
        window = pal.window().color()
        # Button furniture derived from the palette, so every theme gets
        # it: an accent-tinted face with a real accent edge (the tint
        # alone is nearly the window), stronger on hover, full accent
        # when pressed or checked.
        face = self._blend(window, accent, 0.18).name()
        hover = self._blend(window, accent, 0.42).name()
        # The word-goal row keeps its always-visible tint, derived, so
        # dark themes don't get light text on a hard-coded beige.
        goal_tint = self._blend(window, accent, 0.12).name()
        self.word_goal_row.setStyleSheet(
            f"QWidget#wordGoalRow {{ background: {goal_tint}; "
            "border-radius: 4px; }")
        # Scrollbars: derived groove + handle so they are findable on
        # both the tinted binder and the white editor page. Light
        # windows get a greyed track with the accent deepened toward the
        # ink (a pale accent alone vanishes on a near-white track);
        # dark windows get a darker-than-window track with the light
        # accent as the handle. Handle/track lands ~3:1 or better.
        if window.lightness() > 128:
            groove = self._blend(window, ink, 0.30).name()
            sbar = self._blend(accent, ink, 0.55).name()
        else:
            groove = self._blend(window, QColor(0, 0, 0), 0.35).name()
            sbar = accent_hex
        for widget in (self.tree, self.outliner, self.related_tree):
            widget.setFont(self._serif(12))
        self.outliner.header().setFont(self._serif(12, bold=True))
        self.views.tabBar().setFont(self._serif(12))
        self.setStyleSheet(
            f"QTreeWidget {{ background: {self._TINT_WALL}; border: none; "
            "outline: none; }\n"
            "QTreeWidget::item { padding: 3px 6px; border: none; }\n"
            "QTreeWidget::item:hover { background: rgba(128, 128, 128, 0.10); }\n"
            f"QTreeWidget::item:selected {{ background: {accent_hex}; "
            f"color: {title_ink}; }}\n"
            f"QHeaderView::section {{ background: transparent; border: none; "
            "padding: 5px 10px; font-weight: bold; }\n"
            f"QTabBar::tab {{ padding: 6px 16px; }}\n"
            f"QTabBar::tab:selected {{ border-bottom: 2px solid {accent_hex}; }}\n"
            f"QToolBar {{ background: rgba(120, 150, 110, 0.10); border: none; }}\n"
            f"Fold > QToolButton {{ font-weight: bold; padding: 4px 2px; "
            f"color: {indigo}; }}\n"
            f"QPushButton, QToolBar QToolButton {{ background: {face}; "
            f"border: 1px solid {accent_hex}; color: {ink_hex}; "
            "padding: 3px 8px; }\n"
            f"QPushButton:hover, QToolBar QToolButton:hover "
            f"{{ background: {hover}; }}\n"
            f"QPushButton:pressed, QToolBar QToolButton:pressed, "
            f"QPushButton:checked, QToolBar QToolButton:checked "
            f"{{ background: {accent_hex}; color: {title_ink}; }}\n"
            f"QScrollBar:vertical {{ background: {groove}; width: 11px; "
            "border: none; }\n"
            f"QScrollBar:horizontal {{ background: {groove}; height: 11px; "
            "border: none; }\n"
            f"QScrollBar::handle:vertical {{ background: {sbar}; "
            f"border: 1px solid {accent_hex}; border-radius: 4px; "
            "min-height: 24px; }\n"
            f"QScrollBar::handle:horizontal {{ background: {sbar}; "
            f"border: 1px solid {accent_hex}; border-radius: 4px; "
            "min-width: 24px; }\n"
            f"QScrollBar::handle:vertical:hover {{ background: {accent_hex}; }}\n"
            f"QScrollBar::handle:horizontal:hover {{ background: {accent_hex}; }}\n"
            "QScrollBar::add-line, QScrollBar::sub-line "
            "{ background: transparent; height: 0; width: 0; }\n"
            "QScrollBar::add-page, QScrollBar::sub-page "
            "{ background: transparent; }\n"
        )

    def _tint_row(self, item, kind: str) -> None:
        """Glass over the row for its kind: sand for proxies, grey for
        ambience; manuscript keeps the brushed surface (and a fainter
        ink for ambience)."""
        if kind == "proxy":
            item.setData(0, Qt.ItemDataRole.BackgroundRole,
                         QBrush(self._TINT_PROXY))
        elif kind == "ambience":
            item.setData(0, Qt.ItemDataRole.BackgroundRole,
                         QBrush(self._TINT_AMBIENCE))
            item.setData(0, Qt.ItemDataRole.ForegroundRole,
                         QBrush(self._ink_ambience))

    def _reveal_tree_path(self, item) -> None:
        """Open the ancestors of the selected item, and nothing else.

        The selection has to be visible; its siblings' contents are the
        writer's business only when they ask. Collapsed everywhere else is
        what keeps the binder showing structure rather than text.
        """
        if item is None:
            return
        parent = item.parent()
        while parent is not None:
            parent.setExpanded(True)
            parent = parent.parent()

    def _refresh_corkboard(self) -> None:
        self.corkboard.clear()
        if not self.project:
            return
        documents = self.project.documents()
        selected = next((d for d in documents if d.id == self.current_id), None)
        if selected and any(d.parent_id == selected.id for d in documents):
            cards = [d for d in documents if d.parent_id == selected.id]
        else:
            parent_id = selected.parent_id if selected else None
            cards = [d for d in documents if d.parent_id == parent_id]
        for document in sorted(cards, key=lambda item: item.position):
            card = QListWidgetItem(self._card_text(document))
            card.setData(Qt.UserRole, document.id)
            # The synopsis is kept beside the id: it is what the card's
            # editor works on.
            card.setData(Qt.UserRole + 1, document.synopsis)
            card.setFlags(card.flags() | Qt.ItemIsEditable)
            card.setTextAlignment(Qt.AlignLeft | Qt.AlignTop)
            card.setSizeHint(QSize(200, 130))
            self.corkboard.addItem(card)

    def _card_text(self, document) -> str:
        """What a card says: the title, the synopsis, the count."""
        synopsis = document.synopsis.strip() or "No synopsis"
        progress = ""
        if document.word_target:
            percent = min(100, round(document.word_count * 100 / document.word_target))
            progress = f"\n{document.word_count:,} / {document.word_target:,} ({percent}%)"
        elif document.word_count:
            progress = f"\n{document.word_count:,} words"
        return f"{document.title}\n\n{synopsis}{progress}"

    def _corkboard_clicked(self, card: QListWidgetItem) -> None:
        """A single click on a card opens its synopsis to be written on.

        The click waits out the double-click interval first: a double
        click means "open the document", and a card that opened its
        editor on the first click would never see the second.
        """
        if self._project_is_read_only() or card is None:
            return
        self._corkboard_pending_card = card
        self._corkboard_click_timer.start(QApplication.doubleClickInterval())

    def _edit_card_synopsis(self) -> None:
        """Put the synopsis of the clicked card in an editor, in place."""
        card = self._corkboard_pending_card
        self._corkboard_pending_card = None
        if card is None:
            return
        index = self.corkboard.indexFromItem(card)
        if index.isValid():
            self.corkboard.setCurrentItem(card)
            self.corkboard.editItem(card)

    def _save_card_synopsis(self, document_id: str, synopsis: str) -> None:
        """A synopsis written on a card is the document's own."""
        if not self.project or self._project_is_read_only():
            return
        self.project.update_document(document_id, synopsis=synopsis)
        document = next((d for d in self.project.documents()
                         if d.id == document_id), None)
        for row in range(self.corkboard.count()):
            card = self.corkboard.item(row)
            if card.data(Qt.UserRole) == document_id:
                card.setData(Qt.UserRole + 1, synopsis)
                if document is not None:
                    card.setText(self._card_text(document))
                break
        if document_id == self.current_id:
            # The inspector shows the same document: it must not keep an
            # older synopsis waiting to be saved over the new one.
            self.synopsis.setPlainText(synopsis)

    def _open_card(self, card: QListWidgetItem) -> None:
        # The double click wins over the pending single click: the
        # document is what was asked for.
        self._corkboard_click_timer.stop()
        self._corkboard_pending_card = None
        document_id = card.data(Qt.UserRole)
        for item in self._all_items():
            if item.data(0, Qt.UserRole) == document_id:
                self.tree.setCurrentItem(item)
                self.views.setCurrentWidget(self.editor_page)
                self.editor.setFocus()
                return

    def _new_document(self, title: str, kind: str) -> None:
        """Create a document of the given kind at the sensible place.

        The binder is neutral: folders contain anything, chapters and scenes
        may nest arbitrarily, and several roots may sit side by side. The
        kind only decides where a fresh item lands relative to the selection:
        containers receive children; a scene prefers a chapter parent; a leaf
        selection gets a sibling.
        """
        if not self.project:
            return
        current = self.tree.currentItem()
        current_id = current.data(0, Qt.UserRole) if current else None
        current_kind = current.data(0, Qt.UserRole + 1) if current else None
        parent_id = None
        if current is not None:
            accepts_child = (
                kind == "folder"
                or (kind == "chapter" and current_kind == "folder")
                or (kind == "scene" and current_kind in {"folder", "chapter"})
            )
            if accepts_child:
                parent_id = current_id
            else:
                parent_id = self.project.parent_of(current_id)
        document = self.project.add_document(title, parent_id, kind)
        if self._nearest_template(parent_id):
            # Made inside a template (1.18): its body is markup like its
            # neighbours', so it is a template too. The tick is the
            # writer's to clear.
            self.project.set_template(document.id, True)
        self._reload_tree(document.id)
        item = self.tree.currentItem()
        if item:
            self.tree.editItem(item, 0)

    def add_folder(self) -> None:
        self._new_document("New Folder", "folder")

    def add_chapter(self) -> None:
        self._new_document("New Chapter", "chapter")

    def add_scene(self) -> None:
        self._new_document("New Document", "scene")

    def _binder_path(self, document_id: str) -> str:
        """'Series → Book → Chapter → Scene' — how a dialog names a target
        unambiguously, which matters because titles may repeat (§ working
        agreements: never by name alone)."""
        if not self.project:
            return ""
        by_id = {document.id: document for document in self.project.documents()}
        parts: list[str] = []
        current = by_id.get(document_id)
        while current is not None:
            parts.append(current.title)
            current = by_id.get(current.parent_id) if current.parent_id else None
        return " → ".join(reversed(parts))

    def delete_section(self) -> None:
        """Delete the selected document.

        The same path as the binder's Delete, so the confirmation and
        what it removes are the same wherever the writer asks from.
        """
        self.delete_selected()

    def search(self) -> None:
        if not self.project:
            return
        query = self.search_box.text().strip()
        if not query:
            for item in self._all_items():
                item.setHidden(False)
            self.statusBar().showMessage("Search cleared", 1500)
            return
        results = self.project.search(query)
        if not results:
            self.statusBar().showMessage("No matches", 3000)
            return
        matches = {document.id for document in results}
        for item in self._all_items():
            item.setHidden(item.data(0, Qt.UserRole) not in matches)
        self.statusBar().showMessage(f"{len(results)} matching section(s); clear search to restore")

    def create_snapshot(self) -> None:
        if not self.project or not self.current_id:
            return
        if not self.save_current():
            return
        name, accepted = QInputDialog.getText(self, "Create snapshot", "Snapshot name:")
        if accepted and name.strip():
            self.project.create_snapshot(self.current_id, name.strip())
            self.statusBar().showMessage(f"Snapshot ‘{name.strip()}’ created", 3000)

    def edit_metadata(self) -> None:
        """Dialog for whatever the inspector shows: project-level fields
        on folders/root, the document's rows otherwise (1.12) — one
        mental model, menu and panel always editing the same target."""
        if not self.project:
            return
        target = self._selection_metadata_target()
        if target is None:
            return
        if target == PROJECT_METADATA_TARGET:
            entries = self.project.project_metadata()
        else:
            # Type is the inspector's row (docs/MODEL.md); the dialog
            # edits the rest and must not drop the protected key.
            entries = {
                key: entry for key, entry in
                self.project.metadata_entries(target).items()
                if key != "Type"
            }
        dialog = MetadataDialog(entries, self)
        if dialog.exec() == QDialog.Accepted:
            values, value_types, options = dialog.values_types_options()
            if target == PROJECT_METADATA_TARGET:
                self.project.set_project_metadata(
                    {key: (values[key], value_types[key], options[key]) for key in values}
                )
            else:
                stored_type = self.project.metadata(target).get("Type", "").strip()
                if stored_type:
                    values["Type"] = stored_type
                    value_types["Type"] = "text"
                    options["Type"] = []
                self.project.set_metadata(target, values, value_types, options)
            self.statusBar().showMessage("Metadata saved", 2000)
            self._populate_metadata_table()
            self._refresh_outliner()

    def show_placeholder_reference(self, insert: bool = True) -> None:
        """Publish the placeholder vocabulary (docs/TEMPLATES.md §3.2).

        One line per built-in value, then the metadata keys this project
        actually carries, grouped by the Type that carries them: the useful
        answer to "what can I write" is what an export of *this* project can
        fill. Shown, not exec'd — a reference is read while working.

        ``insert`` is what the reader is doing: from a box being written,
        every name on it goes in at the cursor, so the page is also the way
        to write one without a typo. From Preferences → Placeholders there
        is no such box, and the page is a reference only.
        """
        if not self.project:
            return
        self.placeholder_dialog = PlaceholderReferenceDialog(
            self._placeholder_reference_page(insert=insert), self)
        if insert:
            self.placeholder_dialog.chosen.connect(self.insert_placeholder)
        self.placeholder_dialog.show()

    def insert_placeholder(self, name: str) -> None:
        """A name chosen on the reference page, into the box the writer
        was in (§3.2, §1.2): a head or foot box while a design is open,
        else the config box — the box that had the cursor when the page
        was opened. Nothing takes the dialog's focus away from it, so the
        page can stay open for the next name."""
        token = "${" + name + "}"
        edit = (self._head_focus
                if self._design_open() and self._head_focus is not None
                else None)
        if edit is not None and edit.isEnabled():
            edit.insert(token)
            self._heads_changed()
            return
        self.template_config_box.insertPlainText(token)

    def eventFilter(self, watched, event):
        """Which head box the last cursor was in (§1.2): the reference
        page inserts into it, and by the time the click arrives the box
        has long lost the focus to the dialog."""
        if (event.type() == QEvent.Type.FocusIn
                and watched in self.head_edits.values()):
            self._head_focus = watched
        return super().eventFilter(watched, event)

    def _placeholder_reference_page(self, insert: bool = True) -> str:
        def name_cell(name: str) -> str:
            """One name, as the writer may click it: the anchor carries
            the bare name, never the `${...}` spelling, so nothing has to
            be unescaped on the way back (§3.2). Read-only, a name is
            plain text — a link that does nothing is worse than none."""
            code = f"<code>${{{escape(name)}}}</code>"
            if not insert:
                return code
            return f"<a href='insert:{escape(name)}'>{code}</a>"

        rows = "".join(
            f"<tr><td>{name_cell(name)}</td>"
            f"<td>{escape(meaning)}</td></tr>"
            for name, meaning in BUILTIN_PLACEHOLDERS)
        type_rows = ""
        if self.project:
            names: list[str] = []
            for group in ("folder", "document"):
                for type_name in self.project.known_types(group):
                    if type_name not in names:
                        names.append(type_name)
            for type_name in names:
                spellings = name_cell(f"children-{type_name}")
                type_rows += (
                    f"<tr><td>{spellings}</td>"
                    f"<td>the children of this Type</td></tr>")
        groups = []
        for type_name, keys in self.project.metadata_placeholders().items():
            items = []
            for key, count in keys.items():
                if type_name == "Project":
                    where = "on the project"
                else:
                    plural = "" if count == 1 else "s"
                    where = f"on {count} {type_name} document{plural}"
                items.append(
                    f"<tr><td>{name_cell(key)}</td>"
                    f"<td>{escape(where)}</td></tr>")
            title = "Project" if type_name == "Project" else f"Type {type_name}"
            groups.append(f"<h2>{escape(title)}</h2>"
                          f"<table cellspacing='6'>{''.join(items)}</table>")
        if not groups:
            groups.append(
                "<h2>Your metadata</h2><p>No metadata keys yet. "
                "<i>Document &gt; Edit Metadata...</i> adds one; with nothing "
                "selected (or a folder selected) the field belongs to the "
                "whole project.</p>")
        return (
            "<h1>Placeholders</h1>"
            "<p>A placeholder is written <code>${Name}</code>. It stays "
            "exactly as typed in the editor and in the saved file; an export "
            "replaces it — in a template and in the manuscript alike. An "
            "unknown name prints nothing, so a typo is silent: copy a name "
            "from this page. Saving a template that names one Lekhak does "
            "not know says so.</p>"
            + ("<p><b>Click any name</b> below to put it, complete, at the "
               "cursor of the box you were writing in — a page's Header or "
               "Footer line, or the template config.</p>"
               if insert else
               "<p>Everything this project can fill, as it stands now: the "
               "built-in names, the children of each Type, and the metadata "
               "keys the project, its folders and its documents actually "
               "carry. To add a key of your own, use <i>Preferences → "
               "Placeholders</i> for the whole project, or <i>Document &gt; "
               "Edit Metadata…</i> for one document or folder.</p>")
            + "<h2>Built in</h2>"
            f"<table cellspacing='6'>{rows}</table>"
            + (f"<h2>Your types</h2><table cellspacing='6'>{type_rows}</table>"
               if type_rows else "")
            + "".join(groups) +
            "<h2>Naming</h2>"
            "<p>A name is matched as written, case and spaces included. A "
            "built-in name wins over a metadata key of the same name, so "
            "<code>${Title}</code> is the binder title and <code>${title}</code> "
            "is the writer's own field. The <code>Metadata.</code> spelling "
            "(<code>${Metadata.PoV}</code>) means the same key.</p>"
            "<p>A type answers to its own word, used as it stands: "
            "<code>${children-leaf}</code> lists the children of the type "
            "called <code>leaf</code>. No plurals, no guessing — the type "
            "list in the sidebar is the truth.</p>"
        )

    def restore_snapshot(self) -> None:
        if not self.project or not self.current_id:
            return
        snapshots = self.project.snapshots(self.current_id)
        if not snapshots:
            QMessageBox.information(self, "Snapshots", "This section has no snapshots yet.")
            return
        labels = [f"{item.name} — {item.created_at[:19].replace('T', ' ')}" for item in snapshots]
        selected, accepted = QInputDialog.getItem(
            self, "Restore snapshot", "Choose a snapshot:", labels, 0, False
        )
        if not accepted:
            return
        index = labels.index(selected)
        if not confirm_destructive(
            self, "Restore snapshot",
            f"Replace the open section with the snapshot "
            f"‘{snapshots[index].name}’?",
            confirm="Restore",
            checkbox=(f"{self._binder_path(self.current_id)}\n\nThe stored "
                      "text is overwritten with the snapshot's. The version "
                      "open now is snapshotted first, so it can be found "
                      "again in this list."),
            detail=labels[index],
        ):
            return
        # restore_snapshot overwrites the open document, and the promise that
        # the current version survives depends on this save actually landing.
        if not self.save_current():
            return
        self.project.restore_snapshot(snapshots[index].id)
        current = self.tree.currentItem()
        self._selection_changed(current, current)
        self.statusBar().showMessage("Snapshot restored", 3000)

    def split_section(self) -> None:
        if not self.project or not self.current_id:
            return
        if not self.save_current():
            return
        document = self.editor.document()
        position = self.editor.textCursor().position()
        first = QTextCursor(document)
        first.setPosition(0)
        first.setPosition(position, QTextCursor.KeepAnchor)
        second = QTextCursor(document)
        second.setPosition(position)
        second.setPosition(document.characterCount() - 1, QTextCursor.KeepAnchor)
        before = QTextDocumentFragment(first)
        after = QTextDocumentFragment(second)
        if not before.toPlainText().strip() or not after.toPlainText().strip():
            QMessageBox.information(
                self, "Split Section",
                "Both halves need some text — put the cursor between them.",
            )
            return
        try:
            # The halves come from the editor, which shows the prose
            # unescaped; the files keep the escape (as a save does).
            before_html = self.project.canonicalize_links(
                escape_placeholders(before.toHtml()))
            after_html = self.project.canonicalize_links(
                escape_placeholders(after.toHtml()))
            new = self.project.split_document(
                self.current_id, before_html, after_html)
        except (ValueError, KeyError) as error:
            QMessageBox.warning(self, "Split Section", str(error))
            return
        title = next(d.title for d in self.project.documents()
                     if d.id == self.current_id)
        self._reload_tree(self.current_id)
        self.statusBar().showMessage(
            f"Split ‘{title}’ and ‘{new.title}’", 4000)

    def merge_with_next(self) -> None:
        if not self.project or not self.current_id:
            return
        if not self.save_current():
            return
        following = self.project.next_sibling(self.current_id)
        if following is None:
            QMessageBox.information(
                self, "Merge Sections", "No following sibling to merge with.")
            return
        document = next((d for d in self.project.documents()
                         if d.id == self.current_id), None)
        if document is None:
            return
        dropped = sorted(self.project.metadata_entries(following.id))
        lost = (f"‘{following.title}’s metadata ({', '.join(dropped)}) is "
                "dropped" if dropped else "no metadata is dropped")
        if not confirm_destructive(
            self, "Merge sections",
            f"Append ‘{following.title}’ to ‘{document.title}’ and remove it "
            "from the binder?",
            confirm="Merge",
            checkbox=(f"{self._binder_path(following.id)}\n\ninto "
                      f"{self._binder_path(document.id)}\n\nA snapshot of "
                      f"‘{document.title}’ is taken first and its history "
                      f"moves across, but {lost}."),
        ):
            return
        try:
            merged = self.project.merge_documents(self.current_id, following.id)
        except (ValueError, KeyError) as error:
            QMessageBox.warning(self, "Merge Sections", str(error))
            return
        self._reload_tree(self.current_id)
        self.statusBar().showMessage(
            f"Merged into ‘{merged.title}’", 4000)

    def _open_template_set(self, template_set) -> None:
        """Stand on one template set: no document is open, and the
        Outliner lists the set's own files (§1.1). The editor stays with
        the last document's text cleared rather than a second template
        surface — a file of the set is opened there by picking it.
        """
        if not self.project or template_set is None:
            return
        self.current_template_set = template_set
        self.current_template_group = False
        self.current_template_file = None
        self._show_template_panel(False)     # the metadata comes back
        self.current_id = None
        self._clear_binder_selection()
        self.loading = True
        self.editor.clear()
        self.editor.begin_document(self.project, None)
        self.synopsis.clear()
        self.target.setValue(0)
        self._refresh_book_widgets(None)
        # A shipped (non-editable) set: offer a visible Duplicate button.
        # Its files are listed in the Outliner and open read-only when
        # clicked; the editor stays empty until one is picked.
        if template_set.directory is None:
            self.duplicate_template_button.setVisible(True)
            self.editor.setReadOnly(True)
        else:
            self.duplicate_template_button.setVisible(False)
            self.editor.setReadOnly(False)
            self.template_config_box.setReadOnly(False)
        self._populate_metadata_table()
        self._refresh_related()
        self._refresh_outliner()
        self.loading = False
        self._update_word_count()

    def _clear_binder_selection(self) -> None:
        """Let go of the binder's current item.

        The editor has been emptied — no document is open — so the
        binder must not keep a document highlighted: the writer would
        click it again, the tree would say "already selected", and
        nothing would happen.
        """
        self.tree.blockSignals(True)
        self.tree.setCurrentItem(None)
        self.tree.clearSelection()
        self.tree.blockSignals(False)

    def _open_template_group(self) -> None:
        """Stand on the Templates area: no document is open and the
        Outliner lists the sets — 'which set' answered in the Outliner,
        the way a folder answers 'which chapters'."""
        if not self.project:
            return
        self.current_template_set = None
        self.current_template_group = True
        self.current_template_file = None
        self._show_template_panel(False)     # the metadata comes back
        self.current_id = None
        self._clear_binder_selection()
        self.loading = True
        self.editor.clear()
        self.editor.begin_document(self.project, None)
        self.synopsis.clear()
        self.target.setValue(0)
        self._refresh_book_widgets(None)
        self._populate_metadata_table()
        self._refresh_related()
        self._refresh_outliner()
        self.loading = False
        self._update_word_count()

    def show_template_sets(self) -> None:
        """List the template sets in the Outliner (docs/TEMPLATES.md §1.1).

        The sets are folders on disk rather than documents, so they do
        not clutter the binder — the writer's own tree — and are reached
        from here instead.
        """
        self._open_template_group()

    def _open_template_file(self, relative: str) -> None:
        """Open one file of the chosen set in the regular editor (§1.2).

        A template file is an HTML file — a fragment of markup with a
        placeholder or two — and this is an HTML editor, so there is
        nothing between the two: the file loads here and Save (and the
        debounce, and every switch) writes it back to its own path. A
        set that ships in code has no folder to write to, so its files
        say so and offer Duplicate rather than opening an unwritable
        copy.

        The file splits at its head (§3.1): the head — config lines,
        the Template flag — goes to the sidebar panel, the fragment to
        the editor, and Save puts the two back together. The metadata
        steps aside for the panel while the file is a template.
        """
        if not self.save_current():
            return
        template_set = self.current_template_set
        if not self.project or template_set is None or not relative:
            return
        if template_set.directory is None:
            # A set that ships in code has no folder to write to: show its
            # contents read-only so the reader can see what it does, and
            # offer Duplicate for an editable copy. The head (config +
            # notes) goes to the sidebar panel, the fragment to the
            # editor — the same split as an editable file (§1.2).
            try:
                text = _shipped_text(template_set, relative)
            except (ValueError, KeyError):
                text = ""
            self.loading = True
            self.current_id = None
            self.current_template_file = None
            head, fragment = split_head(text)
            self.template_config_box.setPlainText(head)
            self._prefill_preset_rows(head)
            self.template_check.setChecked(True)
            self._load_template_css()
            self.editor.setReadOnly(True)
            self.template_config_box.setReadOnly(True)
            if relative.endswith(".txt"):
                self.editor.setPlainText(fragment)
            else:
                # Read-only preview: show the raw fragment so the reader
                # can see exactly what the template contains, even when
                # the body is empty (as in `no-export`).
                self.editor.setPlainText(fragment)
            self.editor.begin_document(self.project, None)
            self.synopsis.clear()
            self.target.setValue(0)
            self._refresh_book_widgets(None)
            self._populate_metadata_table()
            self._refresh_related()
            self.duplicate_template_button.setVisible(True)
            self._show_template_panel(True)
            self.loading = False
            # The editor is the destination (§1.2).
            for widget, visible in ((self.editor_page, True),
                                    (self.page_view, False),
                                    (self.corkboard, False),
                                    (self.outliner, True)):
                index = self.views.indexOf(widget)
                if index >= 0:
                    self.views.setTabVisible(index, visible)
            self.views.setCurrentWidget(self.editor_page)
            self.statusBar().showMessage(
                "This template ships with Lekhak — read-only. "
                "Duplicate it into the project to edit its files.", 5000)
            return
        path = Path(template_set.directory) / relative
        if not path.is_file():
            self.statusBar().showMessage(
                f"‘{relative}’ is not on disk any more.", 4000)
            return
        self.editor.setReadOnly(False)
        if path.suffix not in (".html", ".txt"):
            # Anything else (a README, a stray config) is not markup:
            # saving it through an HTML editor would rewrite it as a
            # document, and the compile never reads it anyway.
            self.statusBar().showMessage(
                f"‘{relative}’ is not an HTML fragment — "
                "open its folder to edit it.", 4000)
            return
        self.loading = True
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as error:
            self.loading = False
            QMessageBox.warning(self, "Cannot open template file",
                                str(error))
            return
        self.current_id = None
        self.current_template_file = path
        # A read-only project — the guide — carries its template sets so
        # they can be examined, not edited: the editor opens them to read.
        read_only = self._project_is_read_only()
        # A new file, a new set of issues to report (see
        # _warn_about_unknown_placeholders).
        self._template_issues = []
        self._clear_binder_selection()
        head, fragment = split_head(text)
        self.template_config_box.setReadOnly(read_only)
        self.editor.setReadOnly(read_only)
        self.template_config_box.setPlainText(head)
        self._prefill_preset_rows(head)
        self.template_check.setChecked(is_template_fragment(text))
        self._load_template_css()
        if path.suffix == ".txt":
            self.editor.setPlainText(fragment)
        else:
            self.editor.setHtml(fragment)
        self.editor.begin_document(self.project, None)
        self.synopsis.clear()
        self.target.setValue(0)
        self._refresh_book_widgets(None)
        self._populate_metadata_table()
        self._refresh_related()
        self._show_template_panel(True)
        self.loading = False
        # The editor is the destination; the Outliner stays where it is
        # because it is the file listing the writer clicks through.
        for widget, visible in ((self.editor_page, True),
                                (self.page_view, False),
                                (self.corkboard, False),
                                (self.outliner, True)):
            index = self.views.indexOf(widget)
            if index >= 0:
                self.views.setTabVisible(index, visible)
        self.views.setCurrentWidget(self.editor_page)
        if read_only:
            self.statusBar().showMessage(
                "This project's templates are read-only — duplicate the set "
                "into a project of your own to edit it.", 5000)
        self._update_word_count()

    def _build_template_panel(self) -> QWidget:
        """The sidebar of an open template file (§1.2), in place of the
        document metadata: one big Template checkbox over the file's own
        facts — its head as print/docx config, the set's CSS, and the
        reminder that a file is named after the folder or document Type.

        The checkbox is the panel's switch: unticked, the metadata
        section comes back and the file leaves the Templates listing
        (`Template: no` in its head — the flag lives in the head, so
        the box holds it too and Save writes it where it belongs).
        """
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        self.template_check = QCheckBox("Template")
        font = self.template_check.font()
        font.setPointSize(font.pointSize() + 4)
        font.setBold(True)
        self.template_check.setFont(font)
        self.template_check.setToolTip(
            "Untick to keep this file in the folder without it being\n"
            "used as a template — the document metadata comes back.")
        self.template_check.toggled.connect(self._template_flag_toggled)
        layout.addWidget(self.template_check)

        self.template_config_note = QLabel(TEMPLATE_CONFIG_NOTE)
        self.template_config_note.setWordWrap(True)
        layout.addWidget(self.template_config_note)

        # A template made inside a template folder does not choose the
        # book's paper (1.18): page size and mirroring stay the folder's,
        # and this note stands in for the rows it takes away. Margins
        # and page start remain below — they are the page's own.
        self.template_geometry_note = QLabel("")
        self.template_geometry_note.setWordWrap(True)
        self.template_geometry_note.setVisible(False)
        layout.addWidget(self.template_geometry_note)

        # The two preset rows (§1.2): trim size and margins — the
        # settings a print file must not get wrong, picked rather than
        # typed. A pick writes nothing of its own: it says what a
        # hand-typed line would have said, into the config box below,
        # which still holds the whole head (and is still the truth).
        self.size_preset_label = QLabel("Page size")
        self.size_preset_label.setToolTip(
            "The trim line in one pick — the Amazon sizes a text novel\n"
            "prints on, or your own measurement as Custom.")
        self.size_preset_box = QComboBox()
        self.size_preset_box.addItem("Pick a trim size…", "")
        for trim_id in AMAZON_TRIMS:
            self.size_preset_box.addItem(PAGE_TRIMS[trim_id][0], trim_id)
        self.size_preset_box.addItem("Custom size…", "custom")
        self.size_preset_box.setToolTip(
            "Writes `Page size (inches - horizontal, vertical): W,H` into\n"
            "the config below — the same line Page setup speaks, in the\n"
            "same numbers. Custom shows two boxes: your width and height,\n"
            "written the same way.")
        self.size_preset_box.currentIndexChanged.connect(
            self._size_preset_changed)
        self.size_custom_row = QWidget()
        custom_layout = QHBoxLayout(self.size_custom_row)
        custom_layout.setContentsMargins(0, 0, 0, 0)
        self.size_width_spin = PageSetupDialog._spin(3.0, 14.0, 2)
        self.size_height_spin = PageSetupDialog._spin(3.0, 14.0, 2)
        self.size_width_spin.setToolTip(
            "Trim width and height, inches — written as the Page size "
            "line when you leave the box.")
        self.size_height_spin.setToolTip(self.size_width_spin.toolTip())
        self.size_width_spin.editingFinished.connect(self._custom_size_changed)
        self.size_height_spin.editingFinished.connect(self._custom_size_changed)
        custom_layout.addWidget(self.size_width_spin)
        times = QLabel("×")
        times.setAlignment(Qt.AlignmentFlag.AlignCenter)
        custom_layout.addWidget(times)
        custom_layout.addWidget(self.size_height_spin)
        self.size_custom_row.setVisible(False)

        self.margin_preset_label = QLabel("Margins")
        self.margin_preset_label.setToolTip(
            "KDP's inside (gutter) margin table, by page count.")
        self.margin_preset_box = QComboBox()
        self.margin_preset_box.addItem("Pick the page-count band…", "")
        for label, inside in KDP_MARGIN_BANDS:
            self.margin_preset_box.addItem(
                f"{label} — {inside:g} in inside", inside)
        self.margin_preset_box.addItem("Custom margins…", "custom")
        self.margin_preset_box.setToolTip(
            "Writes `Margins (L,T,R,B/I,T,O,B): in, top, out, bottom` —\n"
            "the band's inside (gutter) margin first, then 0.25 in top,\n"
            "outside and bottom: the table KDP prints a novel against.\n"
            "Custom shows four boxes for margins of your own, and grey\n"
            "is the selector standing down while they are filled.")
        self.margin_preset_box.currentIndexChanged.connect(
            self._margin_preset_changed)
        self.margin_custom_row = QWidget()
        margin_form = QFormLayout(self.margin_custom_row)
        margin_form.setContentsMargins(0, 0, 0, 0)
        self.margin_inside_spin = PageSetupDialog._spin(0.0, 2.0, 3)
        self.margin_top_spin = PageSetupDialog._spin(0.0, 2.0, 3)
        self.margin_outside_spin = PageSetupDialog._spin(0.0, 2.0, 3)
        self.margin_bottom_spin = PageSetupDialog._spin(0.0, 2.0, 3)
        for spin in (self.margin_inside_spin, self.margin_top_spin,
                     self.margin_outside_spin, self.margin_bottom_spin):
            spin.setValue(KDP_MARGIN_MIN)
            spin.setToolTip(
                "Inside, top, outside and bottom in inches — written as "
                "the Margins line when you leave the box.")
            spin.editingFinished.connect(self._custom_margins_changed)
        # Named, not literal: the two edge boxes say Inside/Outside while the
        # file lays itself out mirrored and Left/Right while it does not —
        # the same two slots either way, since the parser reads position.
        self.margin_inside_label = QLabel("Left")
        self.margin_outside_label = QLabel("Right")
        margin_form.addRow(self.margin_inside_label, self.margin_inside_spin)
        margin_form.addRow("Top", self.margin_top_spin)
        margin_form.addRow(self.margin_outside_label, self.margin_outside_spin)
        margin_form.addRow("Bottom", self.margin_bottom_spin)
        self.margin_custom_row.setVisible(False)
        self.margin_mirror_box = QCheckBox("Mirror margins")
        self.margin_mirror_box.setToolTip(
            "This file's own `Mirror margins` line — ticked writes\n"
            "`yes`, unticked writes `no`, and the export's geometry\n"
            "follows the file. On a mirrored layout even pages swap\n"
            "inside and outside, so the boxes above name the edges\n"
            "Inside/Outside while ticked, Left/Right while not.")
        self.margin_mirror_box.toggled.connect(self._mirror_toggled)
        presets = QFormLayout()
        presets.addRow(self.size_preset_label, self.size_preset_box)
        presets.addRow("", self.size_custom_row)
        presets.addRow(self.margin_preset_label, self.margin_preset_box)
        presets.addRow("", self.margin_mirror_box)
        presets.addRow("", self.margin_custom_row)
        # Page start (1.17): the design says where its own pages begin.
        self.page_start_label = QLabel("Page start")
        self.page_start_box = QComboBox()
        for label, value in PAGE_START_CHOICES:
            self.page_start_box.addItem(label, value)
        self.page_start_box.setToolTip(
            "Where this design's pages begin: recto is a right-hand "
            "page, verso a left-hand one. The pick writes the "
            "`New Chapter:` and `New Scene:` lines into the design "
            "below — the vocabulary the print config speaks.")
        self.page_start_box.currentIndexChanged.connect(self._page_start_changed)
        presets.addRow(self.page_start_label, self.page_start_box)
        layout.addLayout(presets)
        # The rows the note above takes away when this design is a
        # sub-template (1.18): the book's paper and its mirroring are
        # the template folder's to set. Margins and page start stay —
        # a title page's own margins, a dedication's page start, are
        # this design's business.
        self.template_geometry_widgets = (
            self.size_preset_label, self.size_preset_box, self.size_custom_row,
            self.margin_mirror_box,
        )
        self.template_geometry_hidden = False
        self.template_inherited_mirror = False

        # Headers and footers (§1.2, §3.1): the running-head lines as the
        # three pieces a head is read in — left, middle, right, the tab
        # stops a piece can sit at — so the writer fills in a page's
        # furniture instead of learning the comma syntax. The pick
        # switches which page set is being spoken for: the book's own
        # pages, or the facing (even) ones a mirrored book prints
        # differently. Behind it all is one `Header:`/`Footer:` line in
        # the config box, which is still the truth.
        self.heads_group = QWidget()
        self.heads_group.setToolTip(
            "A page's running head and foot, written as the config's\n"
            "`Header:` and `Footer:` lines. Left, middle and right are\n"
            "the three places a piece can sit. `${PageNo}` in a piece\n"
            "prints the page number; `${BookTitle}`, `${Author}` and any\n"
            "metadata key fill as they do anywhere.")
        heads_layout = QVBoxLayout(self.heads_group)
        heads_layout.setContentsMargins(0, 0, 0, 0)
        heads_layout.addWidget(QLabel("Header and footer"))
        self.heads_pages_box = QComboBox()
        self.heads_pages_box.addItem("Main body", "")
        self.heads_pages_box.addItem("Facing pages (even)", " left")
        self.heads_pages_box.addItem("First page", " first page")
        self.heads_pages_box.setToolTip(
            "Which pages this head speaks for. A mirrored book prints a\n"
            "different head on its even pages (`Header left`), so fill one\n"
            "set here, then switch and fill the other. `First page` is\n"
            "each section's opening page — the head a chapter opener\n"
            "usually should not wear.")
        self.heads_pages_box.currentIndexChanged.connect(
            self._heads_pages_changed)
        heads_layout.addWidget(self.heads_pages_box)
        heads_grid = QGridLayout()
        heads_grid.setContentsMargins(0, 0, 0, 0)
        heads_grid.setHorizontalSpacing(4)
        self.head_edits: dict[tuple[str, str], QLineEdit] = {}
        self.head_none_boxes: dict[str, QCheckBox] = {}
        # The box the Placeholders page inserts into (§3.2): the last one
        # the cursor was in. None until the writer has been in one, and
        # then the config box below answers for it.
        self._head_focus: QLineEdit | None = None
        for row, noun in enumerate(("Header", "Footer")):
            heads_grid.addWidget(QLabel(noun), row, 0)
            for column, piece in enumerate(("left", "middle", "right"),
                                           start=1):
                edit = QLineEdit()
                # The caption lives in the box: three columns of headings
                # would not fit the sidebar, and an empty box says where
                # it sits as clearly as a heading does.
                edit.setPlaceholderText(piece.title())
                edit.setMinimumWidth(28)
                edit.setToolTip(
                    f"The {piece} piece of the {noun.lower()} — where it\n"
                    "sits on the page; leave it empty for nothing there.\n"
                    "The Placeholders... button inserts a name here.")
                edit.editingFinished.connect(self._heads_changed)
                # Which box the Placeholders page inserts into (§3.2):
                # the one that last had the cursor.
                edit.installEventFilter(self)
                heads_grid.addWidget(edit, row, column)
                heads_grid.setColumnStretch(column, 1)
                self.head_edits[(noun.casefold(), piece)] = edit
            none_box = QCheckBox("none")
            none_box.setToolTip(
                f"These pages carry no {noun.lower()} at all.\n"
                f"Clearing the boxes instead keeps the sample's own "
                f"{noun.lower()}.")
            none_box.toggled.connect(self._heads_none_toggled)
            heads_grid.addWidget(none_box, row, 4)
            self.head_none_boxes[noun.casefold()] = none_box
        heads_layout.addLayout(heads_grid)
        heads_hint = QLabel("Empty keeps the sample's; none removes it.")
        heads_hint.setWordWrap(True)
        heads_hint.setEnabled(False)     # a quiet note, not a control
        heads_layout.addWidget(heads_hint)
        # The running heads' own type (§3.1's `Head font:` line): the
        # sample's head is often the body's size, and a book's folio
        # usually is not. One row, both parts — the header and the
        # footer are the same furniture, and a file that wants them
        # different says so in its line. The first entry in each is
        # "nothing stated": the row then writes no line at all.
        heads_font_row = QHBoxLayout()
        heads_font_row.setContentsMargins(0, 0, 0, 0)
        self.head_font_box = QFontComboBox()
        self.head_font_box.setEditable(False)   # a pick, never half a name
        self.head_font_box.insertItem(0, HEAD_FONT_SAMPLE)
        # A font combo's natural width is wider than the sidebar: the
        # minimum-contents policy lets it shrink and elide the name.
        self.head_font_box.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.head_font_box.setMinimumContentsLength(10)
        self.head_font_box.setToolTip(
            "The running heads' own family — written as the config's\n"
            "`Head font: name, size` line. The first entry states\n"
            "nothing, so the sample's own head stands.")
        self._head_font_foreign: str | None = None
        self.head_size_box = QSpinBox()
        self.head_size_box.setRange(0, 72)
        self.head_size_box.setSpecialValueText("—")
        self.head_size_box.setToolTip(
            "The running heads' own point size. “—” leaves the size to\n"
            "the sample's own head.")
        for box in (self.head_font_box, self.head_size_box):
            heads_font_row.addWidget(box)
        self.head_font_box.currentIndexChanged.connect(self._head_font_changed)
        self.head_size_box.valueChanged.connect(self._head_font_changed)
        heads_layout.addWidget(QLabel("Header and footer font"))
        heads_layout.addLayout(heads_font_row)
        layout.addWidget(self.heads_group)

        default_file = QPushButton("Look at the default file")
        default_file.setToolTip(
            "Open this set's print-config.txt — the reference for the\n"
            "tags a print config is written with.")
        default_file.clicked.connect(self._open_default_config_file)
        layout.addWidget(default_file)
        reference = QPushButton("Placeholders...")
        reference.setToolTip(
            "Every ${...} an export fills, one line each, plus the\n"
            "metadata keys this project actually carries.")
        reference.clicked.connect(self.show_placeholder_reference)
        layout.addWidget(reference)
        self.template_config_box = QPlainTextEdit()
        self.template_config_box.setPlaceholderText(
            "# comment lines and `key: value` lines — this is the head "
            "of the file, saved back to the top of the file.")
        self.template_config_box.setTabChangesFocus(False)
        self.template_config_box.textChanged.connect(self._design_changed)
        layout.addWidget(self.template_config_box, 1)

        self.template_css_label = QLabel("CSS (style.css)")
        self.template_css_label.setToolTip(
            "Dresses HTML and EPUB exports; written to style.css in "
            "this set's folder. A format folder's own style.css wins "
            "over this one (docs/TEMPLATES.md 1.3).")
        layout.addWidget(self.template_css_label)
        self.template_css_box = QPlainTextEdit()
        self.template_css_box.setPlaceholderText(
            "/* CSS for HTML and EPUB exports — leave empty for none. */")
        self.template_css_box.setTabChangesFocus(False)
        self.template_css_box.textChanged.connect(self._metadata_changed)
        layout.addWidget(self.template_css_box, 1)

        self.template_naming_note = QLabel(TEMPLATE_NAMING_NOTE)
        self.template_naming_note.setWordWrap(True)
        layout.addWidget(self.template_naming_note)
        self.template_panel_body = (
            self.template_config_note,
            self.template_geometry_note,
            self.size_preset_label, self.size_preset_box,
            self.size_custom_row,
            self.margin_preset_label, self.margin_preset_box,
            self.margin_mirror_box, self.margin_custom_row,
            self.page_start_label, self.page_start_box,
            self.heads_group,
            default_file, reference, self.template_config_box,
            self.template_css_label, self.template_css_box,
            self.template_naming_note,
        )
        # Greying (§1.2): while a row's custom boxes hold the focus —
        # are being filled — their selector stands down; leaving them
        # puts it back, after editingFinished has written the line.
        application = QApplication.instance()
        if application is not None:
            application.focusChanged.connect(self._preset_focus_changed)
        return panel

    def _show_template_panel(self, on: bool) -> None:
        """Swap the sidebar between the template panel and the metadata
        (§1.2) — and, while the panel is up, let the Template checkbox
        say whether its body or the metadata is showing: ticked, only
        the panel's body; unticked, only the metadata, with the
        checkbox left up so the file can be made a template again.
        """
        self.template_panel.setVisible(on)
        if not on:
            self.metadata_section.setVisible(True)
            return
        checked = self.template_check.isChecked()
        self._apply_template_body(checked)
        if self._design_document_id is not None:
            # CSS belongs to a template set, not to one document (1.17).
            self.template_css_label.setVisible(False)
            self.template_css_box.setVisible(False)
        self.metadata_section.setVisible(not checked)

    def _apply_template_body(self, shown: bool) -> None:
        """What the design panel shows — and what a sub-template is spared.

        A template made inside a template folder (1.18) does not choose
        the book's paper: page size and mirroring are set on the folder
        that dresses the book, so those rows stand down and the note
        says where they are set. Its margins and page start are its own
        — a title page's wider margins are the reason.
        """
        folder = (self._nearest_template(self.project.parent_of(
                      self._design_document_id))
                  if shown and self.project and self._design_document_id
                  else None)
        self.template_geometry_hidden = bool(folder)
        self.template_inherited_mirror = False
        for widget in self.template_panel_body:
            widget.setVisible(shown)
        self._refresh_custom_rows(shown)
        self.template_geometry_note.setVisible(bool(folder))
        if folder:
            self.template_geometry_note.setText(
                f"Page size and mirroring are set on the template folder "
                f"‘{folder}’ — this template prints on that paper. Its own "
                f"margins and page start are below, and a `Header:` or "
                f"`Footer:` line here speaks for its own pages.")
            self.template_inherited_mirror = bool(
                (self.project.effective_page_setup(
                    self._design_document_id) or {}).get("mirror"))
            self.margin_mirror_box.blockSignals(True)
            self.margin_mirror_box.setChecked(self.template_inherited_mirror)
            self.margin_mirror_box.blockSignals(False)
            for widget in self.template_geometry_widgets:
                widget.setVisible(False)
        self._refresh_margin_labels()

    def _nearest_template(self, document_id: str | None) -> str | None:
        """The title of the nearest template at or above a document (1.18).

        A document made inside a template is markup like its neighbours,
        and a sub-template takes the paper of the template folder it
        lives in (1.18).
        """
        if not self.project or document_id is None:
            return None
        by_id = {document.id: document
                 for document in self.project.documents()}
        seen: set[str] = set()
        while document_id and document_id not in seen:
            seen.add(document_id)
            node = by_id.get(document_id)
            if node is None:
                return None
            if self.project.is_template(document_id):
                return node.title
            document_id = node.parent_id
        return None

    def _template_flag_toggled(self, checked: bool) -> None:
        """The template switch (§1.2, 1.17).

        On a template file it is the big checkbox in the design panel:
        untick and the file stops being a template — `Template: no`
        goes into its head, the metadata comes back, and the file leaves
        the Templates listing; tick and it all reverses. The write
        happens here rather than on the debounce because the listing
        follows it: what is on disk is what the listing says.

        On a document it is the tick beside the Type row: the body
        becomes markup rather than manuscript, and the sidebar swaps to
        the design panel.
        """
        if self.loading:
            return
        if self.current_template_file is None:
            self._document_template_toggled(checked)
            return
        head, _rest = split_head(set_template_flag(
            join_head(self.template_config_box.toPlainText(), ""), checked))
        # Signals blocked, or the box's own write would arm the debounce
        # for a save that has already run.
        self.template_config_box.blockSignals(True)
        self.template_config_box.setPlainText(head)
        self.template_config_box.blockSignals(False)
        if not self._save_template_file():
            return
        checked_now = self.template_check.isChecked()
        self._apply_template_body(checked_now)
        self.metadata_section.setVisible(not checked_now)
        if self.current_template_set is not None:
            self._refresh_outliner()
        name = self.current_template_file.name
        self.statusBar().showMessage(
            f"‘{name}’ is a template." if checked_now else
            f"‘{name}’ is kept in the folder but is no longer a template.",
            4000)

    def _document_template_toggled(self, checked: bool) -> None:
        """Mark the selected document as a template, or take the mark away.

        The mark is the document's own metadata (and the file's head
        beside it): it says the body is markup rather than manuscript,
        so the document stays out of the book and the sidebar swaps to
        the design panel. Unticking brings the metadata back.
        """
        if not self.project or not self._type_target_id:
            return
        document_id = self._type_target_id
        try:
            self.project.set_template(document_id, checked)
        except ValueError as error:
            self.statusBar().showMessage(str(error), 6000)
            # Re-sync the panel's checkbox to what is on disk.
            self.template_check.blockSignals(True)
            self.template_check.setChecked(self.project.is_template(document_id))
            self.template_check.blockSignals(False)
            return
        # The big checkbox in the design panel is the same switch.
        self.template_check.blockSignals(True)
        self.template_check.setChecked(checked)
        self.template_check.blockSignals(False)
        if checked:
            self._design_document_id = document_id
            self._load_document_design(document_id)
            self._show_template_panel(True)
        else:
            self._design_document_id = None
            self._show_template_panel(False)
        self.save_timer.start()          # the file follows on the next save
        self._refresh_outliner()
        document = self._current_document()
        title = document.title if document else "This document"
        self.statusBar().showMessage(
            f"‘{title}’ is a template." if checked else
            f"‘{title}’ is a document again.", 4000)

    def _load_document_design(self, document_id: str) -> None:
        """Put the document's own design into the panel (1.17).

        The config box holds the yaml the design is written in — the
        same lines a template file's head carries — and the CSS box
        stands down: CSS belongs to a template set, not to one document.
        """
        config = self.project.metadata(document_id).get(TEMPLATE_CONFIG_KEY, "")
        self.template_config_box.blockSignals(True)
        self.template_config_box.setPlainText(config)
        self.template_config_box.blockSignals(False)
        self._prefill_preset_rows(config)

    def _store_document_design(self, document_id: str) -> None:
        """The design panel's yaml lines onto the document (1.17).

        The lines are the template's design: they live with the document
        and the file's head carries them too, so they travel with it.
        The metadata table never shows them.
        """
        entries = self.project.metadata_entries(document_id)
        values = {key: entry[0] for key, entry in entries.items()}
        value_types = {key: entry[1] for key, entry in entries.items()}
        options = {key: entry[2] for key, entry in entries.items()}
        values[TEMPLATE_CONFIG_KEY] = self.template_config_box.toPlainText()
        value_types[TEMPLATE_CONFIG_KEY] = "textarea"
        options[TEMPLATE_CONFIG_KEY] = []
        self.project.set_metadata(document_id, values, value_types, options)

    def _design_changed(self) -> None:
        """A change in the design box (§1.2, 1.17).

        A template file's head is saved by the file's own save; a
        document's design is stored on the document first, so the head
        the next save writes carries it.
        """
        if self.loading:
            return
        if self._design_document_id is not None:
            self._store_document_design(self._design_document_id)
        # The rows follow the box: a line typed by hand shows in the
        # pick that speaks for it, the way an opened file's rows do.
        self._prefill_preset_rows(self.template_config_box.toPlainText())
        self._metadata_changed()

    # -- the preset rows (1.2) ----------------------------------------------

    def _write_preset_line(self, key: str, line: str) -> None:
        """One preset row's write (§1.2): the line into the config box,
        which arms the normal save like any other keystroke there. The
        row owns no file and no state of its own — the box holds the
        head, and what lands on disk is what a hand would have typed."""
        current = self.template_config_box.toPlainText()
        text = write_config_line(current, key, line)
        if text != current:
            # textChanged arms save; nothing else to do.
            self.template_config_box.setPlainText(text)

    def _page_start_changed(self, *_args) -> None:
        """The page-start pick, written into the design (1.17).

        The design says where its pages begin: `New Chapter: Recto` and
        `New Scene: Recto` are the lines the print-config vocabulary
        uses, and the export reads them. Automatic takes both lines
        away, so the design says nothing it does not mean.
        """
        value = self.page_start_box.currentData() or ""
        current = self.template_config_box.toPlainText()
        text = current
        for key in ("new chapter", "new scene"):
            if value:
                text = write_config_line(text, key, f"{key.title()}: {value}")
            else:
                text = drop_config_line(text, key)
        if text != current:
            # textChanged arms save; nothing else to do.
            self.template_config_box.setPlainText(text)

    # -- headers and footers (1.2, 3.1) --------------------------------------

    def _heads_spelled(self, noun: str) -> str:
        """The config's own spelling of a head key: `Header`, `Footer`,
        and their ` left` (verso) twins."""
        suffix = self.heads_pages_box.currentData() or ""
        return ("Header" if noun == "header" else "Footer") + suffix

    def _heads_changed(self, *_args) -> None:
        """A piece typed into a head or foot box (1.2): its whole line is
        rewritten in the config box, which arms the normal save."""
        if self.loading or not self._design_open():
            return
        self._write_heads()

    def _heads_pages_changed(self, *_args) -> None:
        """The page-set pick: the boxes follow the set being spoken for.
        Reading, not choosing — switching writes no line."""
        if self.loading:
            return
        self._prefill_heads()

    def _heads_none_toggled(self, _checked: bool) -> None:
        """`Header: none` (3.1): these pages carry no head at all, which
        is not what an empty line says — that keeps the sample's own.
        Written straight away, like the mirror box."""
        if self.loading or not self._design_open():
            return
        self._write_heads()

    def _write_heads(self) -> None:
        """The six boxes (and their `none` ticks) as the page set's two
        lines (1.2): a filled row writes `Header: left, middle, right`, a
        ticked `none` writes `Header: none`, and a row that is empty and
        unticked takes its line away — nothing said, so the sample's own
        head stands, exactly as a hand-cleared line would mean."""
        if self._project_is_read_only():
            return
        current = self.template_config_box.toPlainText()
        text = current
        for noun in ("header", "footer"):
            label = self._heads_spelled(noun)
            pieces = [self.head_edits[(noun, piece)].text().strip()
                      for piece in ("left", "middle", "right")]
            if self.head_none_boxes[noun].isChecked():
                line = f"{label}: none"
            elif any(pieces):
                line = f"{label}: {', '.join(pieces)}".strip()
            else:
                line = ""
            key = label.lower()
            text = (write_config_line(text, key, line) if line
                    else drop_config_line(text, key))
        self._heads_enable()
        if text != current:
            # textChanged arms save; nothing else to do.
            self.template_config_box.setPlainText(text)

    def _heads_enable(self) -> None:
        """A ticked `none` row greys its three boxes: the line says the
        pages carry no head, so what they hold is not being read."""
        for noun in ("header", "footer"):
            dead = self.head_none_boxes[noun].isChecked()
            for piece in ("left", "middle", "right"):
                self.head_edits[(noun, piece)].setEnabled(not dead)

    def _head_font_changed(self, *_args) -> None:
        """The head font row (§1.2): one `Head font:` line for the
        running heads both — the family the combo shows and the size the
        box holds. The first entry of each says nothing, and a row with
        nothing stated takes the line away rather than leaving a
        half-stated one."""
        if self.loading or not self._design_open():
            return
        name = ("" if self.head_font_box.currentIndex() == 0
                else self.head_font_box.currentText().strip())
        size = self.head_size_box.value()
        if not name and not size:
            current = self.template_config_box.toPlainText()
            text = drop_config_line(current, "head font")
            if text != current:
                self.template_config_box.setPlainText(text)
            return
        stem = f"{name}, {size}" if size else name
        self._write_preset_line("head font", f"Head font: {stem}")

    def _prefill_head_font(self, config: dict) -> None:
        """The font row following the line it opens with (§1.2). Reading
        is not choosing: no line leaves the row saying nothing.

        A family this machine does not have still shows, as the entry
        after the first: the file says it, and a size edited beside a
        pick that had silently fallen back to "the sample's own" would
        drop the family the writer never touched.
        """
        if not self._design_open():
            return
        raw = str(config.get("head font", "")).strip()
        pieces = [piece.strip() for piece in raw.split(",")]
        name = pieces[0] if pieces else ""
        size = config_points(pieces[1]) if len(pieces) > 1 else None
        self.head_font_box.blockSignals(True)
        if self._head_font_foreign is not None:
            self.head_font_box.removeItem(1)
            self._head_font_foreign = None
        index = (self.head_font_box.findText(name, Qt.MatchFixedString)
                 if name else -1)
        if name and index < 1:
            self.head_font_box.insertItem(1, name)
            self._head_font_foreign = name
            index = 1
        self.head_font_box.setCurrentIndex(max(0, index))
        self.head_font_box.blockSignals(False)
        self.head_size_box.blockSignals(True)
        self.head_size_box.setValue(int(size) if size else 0)
        self.head_size_box.blockSignals(False)

    def _prefill_heads(self) -> None:
        """The head and foot boxes following the lines they open with
        (1.2). Reading is not choosing: an absent line leaves a row empty
        (it means "keep the sample's"), a `none` line ticks the box, and
        a three-piece line fills the boxes.

        A `none` line says nothing about the pieces, so the boxes keep
        what they hold rather than being blanked: ticking the box does
        not throw away what was typed, and unticking writes it back. A
        different design does start clean, so nothing of the last one
        shows through.
        """
        if not self._design_open():
            return
        design = self.current_template_file or self._design_document_id
        if design != getattr(self, "_heads_design", None):
            self._heads_design = design
            for edit in self.head_edits.values():
                edit.setText("")
        config = parse_docx_config(self.template_config_box.toPlainText())
        for noun in ("header", "footer"):
            value = str(config.get(self._heads_spelled(noun).lower(), "")
                        or "").strip()
            none = value.lower() in NO_RUNNING_HEAD
            if not none:
                pieces = config_headings(value) if value else ["", "", ""]
                for piece, held in zip(("left", "middle", "right"), pieces):
                    edit = self.head_edits[(noun, piece)]
                    if edit.text() != held:
                        edit.setText(held)
            box = self.head_none_boxes[noun]
            box.blockSignals(True)
            box.setChecked(none)
            box.blockSignals(False)
        self._heads_enable()
        self._prefill_head_font(config)
        # The guide's template sets open to be read: the rows stand down
        # as the config box beside them does.
        self.heads_group.setEnabled(not self._project_is_read_only())

    def _head_page_size(self) -> tuple[float, float] | None:
        """The size the open file's head says, or None when it says
        nothing readable — one answer for the custom boxes to avoid
        rewriting a line that already carries their numbers."""
        head = self.template_config_box.toPlainText()
        return config_page_size(parse_docx_config(head).get("page size", ""))

    def _size_preset_changed(self, *_args) -> None:
        """The trim picker (§1.2): a pick writes the `Page size` line.

        The entries are PAGE_TRIMS ids, so a preset writes the exact
        numbers trim_id_for, Page setup and the export know — a pick
        cannot drift from the rest of the app's paper. `Custom size…`
        only reveals its two boxes: their numbers are written on
        editingFinished, so merely opening a file never writes.
        """
        if self.loading:
            return
        trim_id = self.size_preset_box.currentData()
        self.size_custom_row.setVisible(
            trim_id == "custom" and not self.template_geometry_hidden)
        if not trim_id:
            return
        if trim_id == "custom":
            # Custom opens on whatever the head already says, so its
            # boxes edit the current size rather than overwrite it.
            known = self._head_page_size()
            if known is not None:
                self.size_width_spin.setValue(known[0])
                self.size_height_spin.setValue(known[1])
            return
        _label, width, height = PAGE_TRIMS[trim_id]
        self._write_preset_line(
            "page size",
            f"Page size (inches - horizontal, vertical): {width:g},{height:g}")

    def _custom_size_changed(self, *_args) -> None:
        """The custom boxes (§1.2): their turn comes on editingFinished
        and only in Custom mode — the spin boxes fire that signal on a
        mere focus-out too, and stepping back to a preset is not a
        choice to write what the head already says."""
        if (self.loading or not self._design_open()
                or self.size_preset_box.currentData() != "custom"):
            return
        size = (self.size_width_spin.value(), self.size_height_spin.value())
        if size == self._head_page_size():
            return                     # the head already says these
        self._write_preset_line(
            "page size",
            "Page size (inches - horizontal, vertical): "
            f"{size[0]:g},{size[1]:g}")

    def _margin_preset_changed(self, *_args) -> None:
        """The margins row (§1.2): a band writes the whole KDP line —
        the band's inside (gutter) margin first, then KDP's 0.25 in
        top, outside and bottom, the four-value spelling
        config_geometry reads back as inside, top, outside, bottom —
        and `Custom margins…` only reveals its four boxes, carrying
        the head's own numbers in like the size row does: their turn
        comes on editingFinished, so merely opening never writes. No
        bleed variant: Lekhak sets no bleed."""
        if self.loading:
            return
        mode = self.margin_preset_box.currentData()
        self.margin_custom_row.setVisible(mode == "custom")
        if not mode:
            return                    # the prompt entry says nothing
        if mode == "custom":
            current = self._head_margins()
            if current is not None:
                self._set_margin_spins(current)
            return
        self._write_preset_line(
            "margins",
            f"Margins (L,T,R,B/I,T,O,B): {mode:g}, {KDP_MARGIN_MIN:g}, "
            f"{KDP_MARGIN_MIN:g}, {KDP_MARGIN_MIN:g}")

    def _head_margins(self) -> tuple[float, float, float, float] | None:
        """The four margins the open head says — inside, top, outside,
        bottom — or None when it says nothing readable. A mirrored pair
        reads as its two numbers with KDP's minimum for the two it does
        not name; the custom boxes and their no-op guard both speak in
        this one shape."""
        head = self.template_config_box.toPlainText()
        values = config_length_list(
            parse_docx_config(head).get("margins", ""))
        if not values or len(values) not in (2, 4):
            return None
        if len(values) == 4:
            return (values[0], values[1], values[2], values[3])
        return (values[0], KDP_MARGIN_MIN, values[1], KDP_MARGIN_MIN)

    def _set_margin_spins(self, values) -> None:
        """The four custom boxes carrying some head's margins (§1.2) —
        a four-value line goes in as it stands, a mirrored pair fills
        inside and outside, the two it does not name staying at KDP's
        minimum: the spins' own start values, so no write is armed."""
        if len(values) == 4:
            inside, top, outside, bottom = values
        else:
            inside, outside = values
            top = bottom = KDP_MARGIN_MIN
        self.margin_inside_spin.setValue(inside)
        self.margin_top_spin.setValue(top)
        self.margin_outside_spin.setValue(outside)
        self.margin_bottom_spin.setValue(bottom)

    def _custom_margins_changed(self, *_args) -> None:
        """The custom margin boxes (§1.2): their turn comes on
        editingFinished and only in Custom mode, same as the size row's
        — a focus-out after no edit writes nothing, because the head
        already says those numbers."""
        if (self.loading or not self._design_open()
                or self.margin_preset_box.currentData() != "custom"):
            return
        values = (self.margin_inside_spin.value(),
                  self.margin_top_spin.value(),
                  self.margin_outside_spin.value(),
                  self.margin_bottom_spin.value())
        if values == self._head_margins():
            return
        numbers = ", ".join(f"{value:g}" for value in values)
        self._write_preset_line(
            "margins", f"Margins (L,T,R,B/I,T,O,B): {numbers}")

    def _mirror_toggled(self, checked: bool) -> None:
        """The mirror checkbox (§1.2): this file's own `Mirror margins`
        line, written both ways — ticked `yes`, unticked `no` — so the
        head carries what the edge boxes are named by, and the export's
        geometry follows the file rather than a guess at the folder.
        Written straight away, unlike a spin: a toggle is a decision,
        and there is no half-typed state to wait out."""
        if self.loading or not self._design_open():
            return
        self._write_preset_line(
            "mirror margins",
            f"Mirror margins: {'yes' if checked else 'no'}")
        self._refresh_margin_labels()

    def _design_open(self) -> bool:
        """True while a design is on screen (1.17): a template file's
        head, or a template document's own design. The preset rows write
        into the config box either way, and the box's own signal saves
        it — to the file on save, to the document at once."""
        return (self.current_template_file is not None
                or self._design_document_id is not None)

    def _refresh_margin_labels(self) -> None:
        """The edge boxes naming themselves the way the book lays pages
        out (§1.2): mirrored, inside/outside — even pages swap — and
        plainly, the left and right of each page. On a sub-template
        (1.18) the mirroring is the template folder's, not a line this
        design writes, so the labels follow the book."""
        mirrored = (self.template_inherited_mirror
                    if self.template_geometry_hidden
                    else self.margin_mirror_box.isChecked())
        self.margin_inside_label.setText(
            "Inside (spine)" if mirrored else "Left")
        self.margin_outside_label.setText(
            "Outside" if mirrored else "Right")

    def _refresh_custom_rows(self, body_shown: bool) -> None:
        """The two Custom reveals follow their own mode (§1.2) — the
        body's general visibility must not override it, or a re-show
        would open boxes for a row that is sitting on a preset."""
        self.size_custom_row.setVisible(
            body_shown
            and not self.template_geometry_hidden
            and self.size_preset_box.currentData() == "custom")
        self.margin_custom_row.setVisible(
            body_shown
            and self.margin_preset_box.currentData() == "custom")

    def _preset_focus_changed(self, _old, new) -> None:
        """Grey (§1.2): while a row's custom boxes hold the focus — are
        being filled — that row's selector stands down: the boxes are
        what is speaking, and a preset one keypress away would throw
        away what is being typed. Focus leaving them re-enables the
        selector; by then editingFinished has already written the
        line."""
        for row, selector in ((self.size_custom_row, self.size_preset_box),
                              (self.margin_custom_row,
                               self.margin_preset_box)):
            busy = (isinstance(new, QWidget)
                    and (new is row or row.isAncestorOf(new)))
            selector.setEnabled(not busy)

    def _prefill_preset_rows(self, head: str) -> None:
        """The preset rows following the head they open with (§1.2).

        Reading is not choosing: an Amazon trim selects itself, any
        other real size selects Custom and carries its numbers (without
        writing them back), a margins line that is a band's whole KDP
        row selects that band, and any other readable margins line
        selects Custom and carries its numbers — the mirror checkbox
        follows its own line — and where nothing is said, both rows sit
        on their prompts. Signals blocked throughout: a prefill must
        never fire a write.
        """
        config = parse_docx_config(head)
        size = config_page_size(config.get("page size", ""))
        trim_id = trim_id_for(*size) if size else ""
        self.size_preset_box.blockSignals(True)
        if size is None:
            index = 0
        elif trim_id in AMAZON_TRIMS:
            index = self.size_preset_box.findData(trim_id)
        else:                          # a size the Amazon row doesn't carry
            index = self.size_preset_box.findData("custom")
            self.size_width_spin.setValue(size[0])
            self.size_height_spin.setValue(size[1])
        self.size_preset_box.setCurrentIndex(max(0, index))
        self.size_preset_box.blockSignals(False)
        self.size_custom_row.setVisible(
            not self.template_geometry_hidden
            and size is not None and trim_id not in AMAZON_TRIMS)

        mirror = str(config.get("mirror margins", "")).strip().lower()
        self.margin_mirror_box.blockSignals(True)
        self.margin_mirror_box.setChecked(
            mirror in {"yes", "true", "on", "1"})
        self.margin_mirror_box.blockSignals(False)
        self._refresh_margin_labels()

        margins = config_length_list(config.get("margins", ""))
        readable = margins if margins and len(margins) in (2, 4) else None
        band_index = 0
        if readable is not None:
            # Only a line that IS a band's whole KDP row selects the
            # band — anything else carries different numbers and the
            # boxes must show them (a band selected over 0.3s would be
            # a claim the file does not make).
            for position, (_label, value) in enumerate(
                    KDP_MARGIN_BANDS, start=1):
                if readable == [value, KDP_MARGIN_MIN,
                                KDP_MARGIN_MIN, KDP_MARGIN_MIN]:
                    band_index = position
                    break
        custom_margins = readable is not None and not band_index
        self.margin_preset_box.blockSignals(True)
        if band_index:
            index = band_index
        elif custom_margins:
            index = self.margin_preset_box.findData("custom")
        else:
            index = 0
        self.margin_preset_box.setCurrentIndex(max(0, index))
        self.margin_preset_box.blockSignals(False)
        if custom_margins:
            self._set_margin_spins(readable)
        self.margin_custom_row.setVisible(custom_margins)

        # The page start the design already says (1.17): the chapter
        # line speaks for the design, and the scene line answers when
        # there is no chapter line.
        start = str(config.get("new chapter", "")
                    or config.get("new scene", "")).strip().lower()
        self.page_start_box.blockSignals(True)
        self.page_start_box.setCurrentIndex(
            max(0, self.page_start_box.findData(PAGE_START_WORDS.get(start, ""))))
        self.page_start_box.blockSignals(False)
        self._prefill_heads()

    def _open_default_config_file(self) -> None:
        """The config note's 'look at the default file' (§3.1), made a
        button: this set's print-config.txt — the commented reference
        every set is created with — opened like any other file."""
        template_set = self.current_template_set
        if template_set is None or template_set.directory is None:
            return
        if not (Path(template_set.directory) / "print-config.txt").is_file():
            self.statusBar().showMessage(
                "This set has no print-config.txt — Duplicate the "
                "shipped default set to get one.", 5000)
            return
        self._open_template_file("print-config.txt")

    def _load_template_css(self) -> None:
        """Fill the CSS box from the set's own style.css and remember
        the bytes it arrived in — Save writes the box back only when it
        has moved, so opening a file never mints an empty style.css."""
        template_set = self.current_template_set
        self._template_css_loaded = ""
        self.template_css_box.setReadOnly(self._project_is_read_only())
        if template_set is None or template_set.directory is None:
            self.template_css_box.clear()
            return
        path = Path(template_set.directory) / "style.css"
        try:
            text = path.read_text(encoding="utf-8") if path.is_file() else ""
        except OSError:
            text = ""
        self._template_css_loaded = text
        self.template_css_box.setPlainText(text)

    def _save_template_css(self) -> None:
        """Write the CSS box to the set's style.css, but only when it
        differs from what was loaded — the file is the truth, and an
        untouched box must not create or rewrite one."""
        template_set = self.current_template_set
        if (template_set is None or template_set.directory is None
                or not hasattr(self, "_template_css_loaded")):
            return
        if self._project_is_read_only():
            # The guide's sets are there to be read; nothing is written
            # back into the folder the software came from.
            return
        text = self.template_css_box.toPlainText()
        if text == self._template_css_loaded:
            return
        path = Path(template_set.directory) / "style.css"
        try:
            temporary = path.with_name(path.name + ".tmp")
            temporary.write_text(text, encoding="utf-8")
            temporary.replace(path)
        except OSError as error:
            QMessageBox.critical(self, "Save failed", str(error))
            return
        self._template_css_loaded = text

    def _duplicate_template(self, source, name: str) -> None:
        """Fork a set into a project and land in the copy, ready to
        edit — the whole point of duplicating one.

        The guide is read-only, so a set taken from it goes into a
        project of the writer's choosing: the copy is theirs, and it can
        be edited.
        """
        if not self.project or source is None:
            return
        root = self.project.root
        if self.project.read_only:
            chosen = QFileDialog.getExistingDirectory(
                self, "Duplicate into which project?")
            if not chosen:
                return
            root = Path(chosen)
            if not (root / "project.sqlite3").is_file():
                QMessageBox.warning(
                    self, "Not a Lekhak project",
                    f"“{root}” is not a Lekhak project. Choose the folder "
                    "of the book you want the copy in.")
                return
        try:
            created = copy_template_set(source, name, root)
        except (ValueError, FileExistsError) as error:
            QMessageBox.warning(self, "Cannot duplicate template", str(error))
            return
        self.statusBar().showMessage(
            f"Duplicated into {project_template_root(root)}"
            f"/{created.name}", 5000)
        self._reload_tree()
        # The sets are not in the binder, so the copy is opened where it
        # is listed — the Outliner (§1.1).
        self._open_template_set(created)

    def _update_bulk_button(self, *_args) -> None:
        if not hasattr(self, "bulk_edit_button"):
            return
        count = len(self.outliner.selectedItems())
        self.bulk_edit_button.setEnabled(count >= 1)

    def _bulk_expanded_ids(self, raw_ids: list[str], include_subfolders: bool) -> list[str]:
        if not self.project:
            return []
        ordered: dict[str, None] = {}
        for document_id in raw_ids:
            ordered.setdefault(document_id, None)
            if include_subfolders:
                for descendant in self.project._descendant_ids(document_id):
                    ordered.setdefault(descendant, None)
        # Binder order, so the summary reads in book order.
        order = {d.id: idx for idx, d in enumerate(self.project.documents())}
        return sorted(ordered, key=lambda x: order.get(x, 0))

    def _bulk_default_scope(self, raw_ids: list[str]) -> str:
        all_docs = {d.id: d for d in self.project.documents()}
        peek = self._bulk_expanded_ids(raw_ids, True)
        has_folders = any(all_docs.get(i) and all_docs[i].kind == "folder" for i in peek)
        has_docs = any(all_docs.get(i) and all_docs[i].kind != "folder" for i in peek)
        if has_folders and not has_docs:
            return "folder"
        if has_docs and not has_folders:
            return "document"
        # Mixed: folders-only is the safer default — files already own
        # document Types, so surprising them is the usual complaint.
        return "folder" if has_folders else "document"

    def _open_bulk_edit(self) -> None:
        """Entry point: choose Type vs Metadata. Both are separate
        dialogs, separate lanes — never one dialog mixing the two.
        """
        if not self.project:
            return
        raw_ids = [item.data(0, Qt.UserRole)
                   for item in self.outliner.selectedItems()
                   if item.data(0, Qt.UserRole)]
        if not raw_ids:
            return
        choice, ok = QInputDialog.getItem(
            self, "Bulk Edit",
            "What should this bulk edit change?",
            ["Type — just the Type (Folder or Document lane)",
             "Metadata — fields for that lane (Cover for folders)"],
            0, False)
        if not ok:
            return
        if choice.startswith("Type"):
            self._open_bulk_type_edit(raw_ids)
        else:
            self._open_bulk_metadata_edit(raw_ids)

    def _open_bulk_type_edit(self, raw_ids: list[str]) -> None:
        if not self.project:
            return
        dialog = BulkTypeDialog(
            len(raw_ids),
            sorted(self.project.known_types("folder")),
            sorted(self.project.known_types("document")),
            True, self._bulk_default_scope(raw_ids), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        type_value = dialog.type_value()
        if not type_value:
            return
        expanded = self._bulk_expanded_ids(raw_ids, dialog.subfolders())
        group = dialog.scope_group_name()
        # Also add the chosen Type to its catalogue so it sticks for
        # the next plain edit.
        self.project.add_type(group, type_value)
        applied = 0
        for document_id in expanded:
            doc = next((d for d in self.project.documents() if d.id == document_id), None)
            if doc is None:
                continue
            is_folder = doc.kind == "folder"
            if (group == "folder") != is_folder:
                continue
            try:
                entries = self.project.metadata_entries(document_id)
                cur_values = {k: v[0] for k, v in entries.items()}
                cur_types = {k: v[1] for k, v in entries.items()}
                cur_opts = {k: v[2] for k, v in entries.items()}
                cur_values["Type"] = type_value
                cur_types["Type"] = "text"
                self.project.set_metadata(document_id, cur_values, cur_types, cur_opts)
                applied += 1
            except Exception as err:
                QMessageBox.warning(self, "Bulk edit", str(err))
                break
        self._reload_tree()
        self.statusBar().showMessage(f"Set Type on {applied} item(s)", 5000)

    def _open_bulk_metadata_edit(self, raw_ids: list[str]) -> None:
        if not self.project:
            return
        dialog = BulkMetadataDialog(
            len(raw_ids),
            sorted(self.project.known_fields("folder")),
            sorted(self.project.known_fields("document")),
            True, self._bulk_default_scope(raw_ids), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        expanded = self._bulk_expanded_ids(raw_ids, dialog.subfolders())
        group = dialog.scope_group_name()
        cover_value = dialog.cover_value()
        values = dialog.values()
        if not values and not cover_value:
            return
        applied = 0
        for document_id in expanded:
            doc = next((d for d in self.project.documents() if d.id == document_id), None)
            if doc is None:
                continue
            is_folder = doc.kind == "folder"
            if (group == "folder") != is_folder:
                continue
            try:
                entries = self.project.metadata_entries(document_id)
                cur_values = {k: v[0] for k, v in entries.items()}
                cur_types = {k: v[1] for k, v in entries.items()}
                cur_opts = {k: v[2] for k, v in entries.items()}
                for key, value in values.items():
                    cur_values[key] = value
                    cur_types.setdefault(key, "text")
                self.project.set_metadata(document_id, cur_values, cur_types, cur_opts)
                applied += 1
            except Exception as err:
                QMessageBox.warning(self, "Bulk edit", str(err))
                break
            if cover_value and is_folder:
                settings = dict(self.project.book_settings(document_id))
                settings["cover"] = cover_value
                self.project.set_book_settings(document_id, settings)
        self._reload_tree()
        self.statusBar().showMessage(f"Updated metadata on {applied} item(s)", 5000)

    def _outliner_context_menu(self, position) -> None:
        """Right-click in Outliner.

        Template-file rows keep Copy-out; manuscript rows offer Bulk
        Edit for the current multi-selection (the only way to reach
        bulk edit without hunting the toolbar).
        """
        if not self.project:
            return
        item = self.outliner.itemAt(position)
        if item is None:
            return
        # Manuscript rows (have a document id): bulk menu
        doc_id = item.data(0, Qt.UserRole)
        if doc_id:
            menu = QMenu(self)
            sel = self.outliner.selectedItems()
            raw = [it.data(0, Qt.UserRole) for it in sel if it.data(0, Qt.UserRole)]
            menu.addAction(f"Bulk Edit Type ({len(raw)} selected)…", lambda: self._open_bulk_type_edit(list(raw)))
            menu.addAction(f"Bulk Edit Metadata ({len(raw)} selected)…", lambda: self._open_bulk_metadata_edit(list(raw)))
            menu.exec(self.outliner.viewport().mapToGlobal(position))
            return
        if item.data(0, Qt.UserRole + 1) != TEMPLATE_FILE_KIND:
            return
        relative = item.data(0, TEMPLATE_PATH_ROLE)
        if not relative:
            return
        menu = QMenu(self)
        menu.addAction("Copy out as its own template…",
                       lambda: self._copy_template_prompt(relative))
        menu.exec(self.outliner.viewport().mapToGlobal(position))

    def _copy_template_prompt(self, relative: str) -> None:
        """Ask for the new set's name, suggested from the file so the
        usual answer is a keystroke away."""
        if not self.project or self.current_template_set is None:
            return
        suggestion = " ".join(Path(relative).with_suffix("").parts)
        name, accepted = QInputDialog.getText(
            self, "Copy template out", "Name for the new template set:",
            text=suggestion)
        if accepted and name.strip():
            self._copy_template_file(relative, name.strip())

    def _copy_template_file(self, relative: str, name: str) -> None:
        """Copy one file of the open set into a new sibling set (§1.1)
        and land in it, ready to edit — the whole point of copying one
        file rather than the folder."""
        if not self.project or self.current_template_set is None:
            return
        try:
            created = copy_template_file(
                self.current_template_set, relative, name,
                self.project.root)
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "Cannot copy template", str(error))
            return
        self.statusBar().showMessage(
            f"Copied into {project_template_root(self.project.root)}"
            f"/{created.name}", 5000)
        self._reload_tree()
        self._open_template_set(created)

    def _tree_context_menu(self, position) -> None:
        item = self.tree.itemAt(position)
        if item is None or not self.project:
            return
        kind = item.data(0, Qt.UserRole + 1)
        if kind == TEMPLATE_SET_KIND:
            self._template_set_menu(item, position)
            return
        if kind == TEMPLATE_GROUP_KIND:
            return
        if item.data(0, Qt.UserRole) != self.current_id:
            self.tree.setCurrentItem(item)
        menu = QMenu(self)
        # The binder is a file manager (docs/STORAGE.md): a folder makes
        # folders and files, a file is a file. Split and merge are the
        # editor's business and have no place here.
        in_trash = self._is_in_trash(doc_id)
        is_trash = self._is_trash_folder(doc_id)
        if is_trash:
            # The trash is a normal folder you can look inside, but it
            # also offers the two things only it can do: restore what it
            # holds, and empty it when you are sure.
            menu.addAction("Empty trash…", self._empty_trash)
            menu.addSeparator()
            menu.addAction("Show in File Manager", self._show_in_file_manager)
        elif in_trash:
            menu.addAction("Restore", self._restore_selected)
            menu.addAction("Rename...", self._rename_selected)
            menu.addAction("Delete", self.delete_selected)
            menu.addSeparator()
            menu.addAction("Show in File Manager", self._show_in_file_manager)
        elif kind == "folder":
            menu.addAction("New Folder", self.add_folder)
            menu.addAction("New Document", self.add_scene)
            menu.addSeparator()
            menu.addAction("Rename...", self._rename_selected)
            menu.addAction("Delete", self.delete_selected)
            menu.addSeparator()
            menu.addAction("Show in File Manager", self._show_in_file_manager)
        else:
            menu.addAction("Rename...", self._rename_selected)
            menu.addAction("Delete", self.delete_selected)
            menu.addSeparator()
            menu.addAction("Show in File Manager", self._show_in_file_manager)
        menu.exec(self.tree.viewport().mapToGlobal(position))

    def _is_trash_folder(self, document_id: str | None) -> bool:
        if not self.project or not document_id:
            return False
        document = next((d for d in self.project.documents()
                         if d.id == document_id), None)
        return document is not None and document.path == "trash"

    def _is_in_trash(self, document_id: str | None) -> bool:
        if not self.project or not document_id:
            return False
        by_id = {d.id: d for d in self.project.documents()}
        current = by_id.get(document_id)
        while current is not None and current.parent_id:
            current = by_id.get(current.parent_id)
        return current is not None and current.path == "trash"

    def _empty_trash(self) -> None:
        if not self.project:
            return
        if not confirm_destructive(
            self, "Empty trash",
            "Empty the trash? Everything in it is deleted for good.",
            confirm="Empty trash",
            checkbox="The files in the trash are removed permanently — "
                     "they can no longer be restored.",
            gate="I understand that I will no longer be able to restore "
                 "these files if needed.",
        ):
            return
        try:
            self.project.backup()
            self.project.empty_trash()
        except Exception as error:
            QMessageBox.critical(self, "Could not empty the trash",
                                 str(error))
            return
        self._reload_tree()

    def _restore_selected(self) -> None:
        if not self.project or not self.current_id:
            return
        try:
            self.project.restore_from_trash(self.current_id)
        except Exception as error:
            QMessageBox.critical(self, "Could not restore", str(error))
            return
        self._reload_tree(self.current_id)

    def delete_selected(self) -> None:
        """Delete the selected document — its row, and its place on disk.

        No confirmation: deleting is a specific decision, not an accident,
        and the document lands in the trash where it can be restored. A
        container goes with everything under it, and their files go with
        them.
        """
        if not self.project or not self.current_id:
            return
        document = next((d for d in self.project.documents()
                         if d.id == self.current_id), None)
        if document is None:
            return
        try:
            self.project.delete_document(document.id)
        except DeleteError as error:
            # Nothing was deleted: the document is still here, and the
            # writer is told what stopped it and what they can do.
            reasons = "\n".join(f"{path}\n    {reason}"
                                 for path, reason in error.failures)
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle("Could not delete")
            box.setText(f"‘{document.title}’ was not deleted.")
            box.setInformativeText(
                "Lekhak could not remove the file from disk, so nothing was "
                "deleted — the document is still in your binder, exactly as "
                "it was.\n\n"
                "This is usually a permission, a read-only folder, or a file "
                "another program is holding open. You can delete it by hand "
                "in your file manager, or change the permissions and try "
                "again.\n\n"
                "If you think this is a bug, please report it:\n"
                "https://github.com/Vidyut/lekhak/issues")
            box.setDetailedText(reasons)
            box.setStandardButtons(QMessageBox.Ok)
            box.exec()
            self._reload_tree()
            return
        self.current_id = None
        self._reload_tree()
        self._refresh_outliner()

    def _show_in_file_manager(self) -> None:
        """Open the selected item's own place on disk."""
        if not self.project or not self.current_id:
            return
        path = self.project.content_path(self.current_id)
        target = path if path.exists() else path.parent
        if target.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def _rename_selected(self) -> None:
        """Rename the selected document — its title and its file."""
        if not self.project or not self.current_id:
            return
        item = self.tree.currentItem()
        if item is None:
            return
        document = next((d for d in self.project.documents()
                         if d.id == self.current_id), None)
        if document is None:
            return
        name, accepted = QInputDialog.getText(
            self, "Rename", "New name:", text=document.title)
        if not accepted or not name.strip():
            return
        if self._project_is_read_only():
            self.statusBar().showMessage(
                "The guide is read-only — take a copy if you want to change "
                "it.", 5000)
            return
        if not self._confirm_reserved_name(name.strip()):
            return          # the name is reserved and the writer said no
        self.project.update_document(document.id, title=name.strip())
        self._reload_tree(document.id)
        item = self.tree.currentItem()
        if item:
            self.tree.editItem(item, 0)

    def _template_set_menu(self, item, position) -> None:
        """Actions for a template set row: open its folder, fork a copy
        into the project. The row's own selection already stands on the
        set and lists its files, so it offers no action of its own."""
        template_set = item.data(0, TEMPLATE_SET_ROLE)
        if template_set is None:
            return
        self.tree.setCurrentItem(item)
        menu = QMenu(self)
        if template_set.directory is not None:
            menu.addAction(
                "Open folder",
                lambda: QDesktopServices.openUrl(
                    QUrl.fromLocalFile(str(template_set.directory))))
        menu.addSeparator()
        menu.addAction("Duplicate into project...",
                       lambda: self._duplicate_template_prompt(template_set))
        menu.exec(self.tree.viewport().mapToGlobal(position))

    def _duplicate_template_prompt(self, template_set) -> None:
        name, accepted = QInputDialog.getText(
            self, "Duplicate template", "Name for your copy:",
            text=f"{template_set.name} copy")
        if accepted and name.strip():
            self._duplicate_template(template_set, name.strip())

    def _duplicate_current_template(self) -> None:
        if self.current_template_set is not None:
            self._duplicate_template_prompt(self.current_template_set)

    def _copy_default_templates(self) -> None:
        """Copy the shipped default templates where the writer is looking.

        Folder selected → ``<folder>/default``; document selected →
        ``<parent-of-document>/default``; nothing selected →
        ``templates/default`` (the historic landing). Files are written
        to disk either way, so the reader can see and edit them where
        they live and copy the whole folder to another project.
        """
        if not self.project:
            return
        document = self._current_document()
        target: str | None = None
        container = None
        if document is not None:
            if document.kind == "folder":
                parent_dir = self.project.root / document.path
                container = document
            else:
                parent_dir = self.project.content_path(document.id).parent
                container = next(
                    (d for d in self.project.documents()
                     if d.id == document.parent_id), None)
            target = str(parent_dir / "default")
            # Sub-folder warning: templates copied here dress only this
            # subtree, not documents sitting in an ancestor folder.
            if container is not None:
                by_id = {d.id: d for d in self.project.documents()}
                ancestor_id = container.parent_id
                branch_id = container.id
                while ancestor_id:
                    siblings = [d for d in self.project.documents()
                                if d.parent_id == ancestor_id
                                and d.id != branch_id]
                    if siblings:
                        ancestor = by_id.get(ancestor_id)
                        name = ancestor.title if ancestor else "the parent"
                        answer = QMessageBox.question(
                            self, "Copy default templates here?",
                            f"This is a sub folder. These templates "
                            f"will not apply to the documents in "
                            f"“{name}”. Do you want to proceed?",
                            QMessageBox.StandardButton.Yes
                            | QMessageBox.StandardButton.No,
                            QMessageBox.StandardButton.No)
                        if answer != QMessageBox.StandardButton.Yes:
                            return
                        break
                    parent = by_id.get(ancestor_id)
                    branch_id = ancestor_id
                    ancestor_id = parent.parent_id if parent else None
        try:
            if target is None:
                created = copy_template_set(TemplateSet.builtin("default"),
                                            "default", self.project.root)
            else:
                created = copy_default_set_to(Path(target))
        except FileExistsError:
            where = (
                f"{target}" if target else
                "templates/default"
            )
            self.statusBar().showMessage(
                f"This project already has a {where} folder — "
                "that is the copy to change.", 5000)
            return
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Cannot copy the default templates",
                                str(error))
            return
        # Same landing as Duplicate (§1.1): the tree redrawn so the copy
        # stops looking like the un-editable shipped set, and the writer
        # standing in the copy that was just made — files and all.
        self._reload_tree()
        if target is not None:
            # A set copied into the binder is tracked in the DB like any
            # other template: every file under it carries the Template
            # mark, so the export keeps reading it as format, never as
            # manuscript (templates/ copies are already skipped by path).
            rel = created.directory.relative_to(self.project.root).as_posix()
            if rel.split("/")[0].casefold() != "templates":
                for copied in self.project.documents():
                    if copied.path and (
                            copied.path == rel
                            or copied.path.startswith(rel + "/")):
                        self.project.set_template(copied.id, True, _force=True)
        self._open_template_set(created)
        self._refresh_book_widgets(self._current_document())
        self.statusBar().showMessage(
            f"The default templates are yours now — in {created.directory}.",
            5000)

    def _maybe_pick_epub_cover(self, raw_ids) -> bool:
        """EPUB cover prompt: use the first detected cover, or ask.

        Returns True to proceed (with or without a cover). Shows the
        detected cover name or warns when none was found; "Choose..."
        copies the picked image to images/<folder_id>/cover.* where
        effective_cover() will find it, so the same export uses it.
        """
        if not self.project:
            return True
        folders = self.project._cover_folders(raw_ids)
        found = None
        for folder in folders:
            found = self.project.effective_cover(folder.id)
            if found is not None:
                break
        if found is not None:
            return True
        # No cover anywhere in scope.
        box = QMessageBox(self)
        box.setWindowTitle("No cover found")
        box.setText("This EPUB has no cover image and will export without one.")
        box.setInformativeText(
            "Choose a cover now? It will be saved as "
            "images/<book>/cover.jpg and used by this export.")
        choose = box.addButton("Choose cover…", QMessageBox.ActionRole)
        without = box.addButton("Export without cover", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Cancel)
        box.setDefaultButton(without)
        box.exec()
        clicked = box.clickedButton()
        if clicked == choose:
            target_folder = folders[0] if folders else None
            if target_folder is None:
                return True
            path, _ = QFileDialog.getOpenFileName(
                self, "Choose cover image", str(self.project.root),
                "Images (*.png *.jpg *.jpeg *.webp *.gif)")
            if not path:
                return True
            suffix = Path(path).suffix.lower().lstrip(".") or "jpg"
            if suffix not in ("jpg", "jpeg", "png", "webp", "gif"):
                QMessageBox.warning(self, "Cover type",
                    "EPUB covers must be jpg, png, webp or gif.")
                return True
            dest = self.project.root / "images" / target_folder.id / f"cover.{suffix}"
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(path, dest)
            except OSError as error:
                QMessageBox.warning(self, "Cover", str(error))
            return True
        return clicked == without


    def import_from_project(self) -> None:
        """Import folders/documents from another Lekhak project."""
        if not self.project:
            return
        source_dir = QFileDialog.getExistingDirectory(
            self, "Choose a Lekhak project to import from")
        if not source_dir:
            return
        source_path = Path(source_dir)
        try:
            src = Project(source_path, read_only=True)
            src.open()
        except Exception as error:
            QMessageBox.critical(self, "Could not open project", str(error))
            return
        try:
            dialog = QDialog(self)
            dialog.setWindowTitle("Import from Lekhak project")
            layout = QVBoxLayout(dialog)
            layout.addWidget(QLabel(
                "Select the folders or documents to import. "
                "Importing a folder includes everything inside it."))
            tree = QTreeWidget()
            tree.setHeaderLabels(["Name", "Kind"])
            tree.setColumnWidth(0, 320)
            items_by_id: dict[str, QTreeWidgetItem] = {}
            docs = src.documents()
            by_parent = {}
            for d in docs:
                by_parent.setdefault(d.parent_id, []).append(d)
            def add_items(parent_item, parent_id):
                for d in sorted(by_parent.get(parent_id, []), key=lambda x: x.position):
                    item = QTreeWidgetItem([d.title, d.kind])
                    item.setData(0, Qt.UserRole, d.id)
                    item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                    item.setCheckState(0, Qt.Unchecked)
                    if parent_item is None:
                        tree.addTopLevelItem(item)
                    else:
                        parent_item.addChild(item)
                    items_by_id[d.id] = item
                    add_items(item, d.id)
            add_items(None, None)
            tree.expandAll()
            layout.addWidget(tree)
            buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            buttons.button(QDialogButtonBox.Ok).setText("Import")
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            selected = [did for did, it in items_by_id.items()
                        if it.checkState(0) == Qt.Checked]
            if not selected:
                return
            current = self._current_document()
            parent_id = None
            if current is not None:
                parent_id = (current.id if current.kind in ("folder", "chapter")
                             else current.parent_id)
            count = self.project.import_from_project(source_path, set(selected), parent_id)
            self._reload_tree()
            self.statusBar().showMessage(
                f"Imported {count} item(s) from {source_path.name}.", 6000)
        except Exception as error:
            QMessageBox.critical(self, "Import failed", str(error))
        finally:
            try:
                src.close()
            except Exception:
                pass


    def import_markdown(self) -> None:
        """File → Import Markdown: bring a markdown export back in.

        Import lands in the open project, under the container selected
        (or the top of the tree when a leaf or nothing is selected).
        Lekhak's own export and Scrivener's both read; every metadata
        key becomes a field or fills one.
        """
        if not self.project:
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Import markdown", str(Path.home()),
            "Markdown (*.md *.markdown *.txt);;All files (*)")
        if not paths:
            return
        current = self._current_document()
        parent_id = None
        if current is not None:
            parent_id = (current.id if current.kind in ("folder", "chapter")
                         else current.parent_id)
        try:
            summary = self.project.import_markdown(
                [Path(path) for path in paths], parent_id)
        except Exception as error:
            QMessageBox.critical(self, "Import failed", str(error))
            return
        self._reload_tree()
        lines = [f"{summary['documents']} document(s) imported."]
        if summary["fields"]:
            lines.append("New metadata fields: "
                         + ", ".join(summary["fields"]))
        if summary["values"]:
            lines.append(f"{summary['values']} metadata value(s) filled.")
        if summary["kept"]:
            lines.append("Kept the project's own "
                         + ", ".join(summary["kept"]) + " (already set).")
        QMessageBox.information(self, "Import markdown", "\n\n".join(lines))

    def export(self, kind: str) -> None:
        if not self.project:
            return
        # Exports read stored HTML, so unsaved editor text makes the file
        # wrong; a failed save cancels the export.
        if not self.save_current():
            return
        # Raw scope from both trees (2.1): folders expand and book
        # chrome fires at compile time; None means the whole project.
        raw_ids = self._gather_export_selection()
        template_sets, preselected, warnings = self.project.template_choices(
            raw_ids)
        # EPUB cover: ask up-front so a missing cover is a decision,
        # not a surprise in the reader.
        cover_text = None
        if kind == "epub":
            if not self._maybe_pick_epub_cover(raw_ids):
                return
            found = None
            for folder in self.project._cover_folders(raw_ids):
                found = self.project.effective_cover(folder.id)
                if found is not None:
                    break
            cover_text = str(found.name) if found else None
        options = ExportOptionsDialog(
            self._export_summary(raw_ids), template_sets,
            preselected.name, warnings, cover=cover_text, parent=self
        )
        if options.exec() != QDialog.Accepted:
            return
        # One-shot (§2.1): the pick lives in this export alone and is
        # never written to settings — an override that lingered would
        # govern a later export the writer had forgotten about. A look
        # that should stick is assigned to the folder or the document.
        templates = options.selected_template_set()
        suffix = {"text": ".txt", "html": ".html", "docx": ".docx",
                  "epub": ".epub", "pdf": ".pdf"}[kind]
        selected, _ = QFileDialog.getSaveFileName(
            self, "Export manuscript", "lekhak" + suffix)
        if selected:
            try:
                if kind == "text":
                    self.project.export_text(Path(selected), raw_ids, templates)
                elif kind == "html":
                    self.project.export_html(Path(selected), raw_ids, templates)
                elif kind == "docx":
                    self.project.export_docx(Path(selected), raw_ids, templates)
                elif kind == "pdf":
                    # Same DOCX, converted by LibreOffice (1.6's route).
                    pdf.export_pdf(self.project, Path(selected), raw_ids,
                                   templates)
                else:
                    self.project.export_epub(Path(selected), raw_ids, templates)
                self.statusBar().showMessage(f"Exported to {selected}", 5000)
            except Exception as error:
                QMessageBox.critical(self, "Export failed", str(error))

    def export_cover_pdf(self) -> None:
        """The wraparound cover as a PDF: the outer sheet any printer
        wraps the inner copy in (docs/BUILD-NOW.md — print copies).
        Trim comes from the book's page setup; the page count from
        the selected text; the artwork from the book's cover."""
        if not self.project:
            return
        if not self.save_current():
            return
        raw_ids = self._gather_export_selection()
        selected, _ = QFileDialog.getSaveFileName(
            self, "Export cover wraparound", "cover.pdf",
            "PDF (*.pdf);;All files (*)")
        if not selected:
            return
        from lekhak import cover as _cover
        # The first cover-carrying folder in the selection's resolution
        # carries the trim and the artwork — the wraparound is of that
        # book, not of whatever happens to be selected.
        folders = self.project._cover_folders(raw_ids)
        folder = folders[0] if folders else None
        trim = (152.4, 228.6)
        if folder is not None:
            setup = self.project.page_setup(folder.id)
            if setup:
                from lekhak.project import PAGE_TRIMS
                label, width_in, height_in = PAGE_TRIMS.get(
                    setup.get("trim", ""), ("", 6.0, 9.0))
                trim = (round(width_in * 25.4, 1), round(height_in * 25.4, 1))
        art = self.project.effective_cover(
            folder.id) if folder is not None else None
        try:
            _cover.compose_cover_pdf(
                self.project, raw_ids, trim, art, Path(selected))
        except Exception as error:
            QMessageBox.critical(self, "Cover export failed", str(error))
            return
        self.statusBar().showMessage(f"Cover exported to {selected}", 5000)

    def _gather_export_selection(self) -> set[str] | None:
        """Raw export scope (2.1): the union of the binder's and the
        outliner's selections, or None — the whole project — when
        nothing is selected anywhere."""
        ids: set[str] = set()
        for widget in (self.tree, self.outliner):
            for item in widget.selectedItems():
                value = item.data(0, Qt.UserRole)
                if value:
                    ids.add(value)
        return ids or None

    def _export_summary(self, raw_ids: set[str] | None) -> str:
        """One scope line for the export dialog."""
        if raw_ids is None:
            count = sum(
                1 for d in self.project.documents() if d.kind in EXPORT_KINDS
            )
            return f"Whole project — every chapter and scene ({count})."
        documents = {d.id: d for d in self.project.documents()}
        titles = [documents[i].title for i in raw_ids if i in documents][:5]
        more = len(raw_ids) - len(titles)
        if more > 0:
            titles.append(f"+{more} more")
        return f"Selection — {len(raw_ids)} item(s): {', '.join(titles)}"

    def _set_paragraph_alignment(self, alignment) -> None:
        """The bar's alignment buttons (2.4h): set the paragraph, then
        show it — a click must answer itself even in the odd corner
        where moving a block format does not move the caret."""
        self.editor.setAlignment(alignment)
        self._refresh_alignment_buttons()

    def _refresh_font_boxes(self, *_args) -> None:
        """Font family + size boxes follow the caret (2.4h).

        Without this the boxes are write-only: after you reformat one
        paragraph, clicking another still shows the previous size
        while the text looks right — exactly the "24 but tiny" trap.
        Size is the *stored* number (live ÷ zoom), family is the caret's
        first family; signals are blocked so a programmatic update
        can't re-trigger a format change.
        """
        if not hasattr(self, "size_box"):
            return
        fmt = self.editor.textCursor().charFormat()
        fams = fmt.fontFamilies()
        if fams:
            self.font_box.blockSignals(True)
            try:
                self.font_box.setCurrentFont(QFont(str(fams[0])))
            finally:
                self.font_box.blockSignals(False)
        size = fmt.fontPointSize()
        if size > 0:
            stored = round(size / self.zoom, 2)
            text = f"{stored:g}"
            if self.size_box.currentText() != text:
                self.size_box.blockSignals(True)
                try:
                    self.size_box.setCurrentText(text)
                finally:
                    self.size_box.blockSignals(False)

    def _refresh_alignment_buttons(self, *_args) -> None:
        """Which way the caret's paragraph sits (2.4h) — the checked
        button is the paragraph's, so when the selection spans two
        differently-aligned paragraphs nothing is checked, which is the
        truthful answer.
        """
        alignment = int(self.editor.alignment() or 0)
        for flag, action in self._alignment_actions.items():
            action.setChecked(alignment and int(flag) == alignment)

    def bold(self) -> None:
        fmt = QTextCharFormat()
        fmt.setFontWeight(QFont.Normal if self.editor.fontWeight() > QFont.Normal else QFont.Bold)
        self.editor.mergeCurrentCharFormat(fmt)

    def italic(self) -> None:
        fmt = QTextCharFormat()
        fmt.setFontItalic(not self.editor.fontItalic())
        self.editor.mergeCurrentCharFormat(fmt)

    def underline(self) -> None:
        fmt = QTextCharFormat()
        fmt.setFontUnderline(not self.editor.fontUnderline())
        self.editor.mergeCurrentCharFormat(fmt)

    def set_font_size(self, value: str) -> None:
        try:
            size = float(value)
        except ValueError:
            return
        if 6 <= size <= 144:
            # The box asks for a *document* size; the live document runs
            # at stored sizes × zoom, so the display gets the product.
            self.editor.setFontPointSize(size * self.zoom)

    def apply_paragraph_style(self, index: int) -> None:
        name = self.style_box.itemData(index)
        # The box's own commands (2.4h): make a style from the paragraph
        # under the caret, or re-capture the one the box was showing.
        if name == NEW_STYLE_COMMAND:
            self._create_style_dialog()
            self._restore_style_box()
            return
        if name == UPDATE_STYLE_COMMAND:
            self._update_style_from_box()
            return
        style = self._styles.get(name)
        if style is None:
            return
        scaled = dict(style)
        if "size" in scaled:
            scaled["size"] = round(float(scaled["size"]) * self.zoom, 2)
        block_format, char_format = style_signature_formats(scaled)
        cursor = self.editor.textCursor()
        cursor.mergeBlockFormat(block_format)
        cursor.mergeCharFormat(char_format)
        self.editor.mergeCurrentCharFormat(char_format)

    # --- paragraph styles (1.11) ------------------------------------------
    # Named styles are formatting metadata about a manuscript — never
    # prose — so one JSON string in the existing project-settings table
    # is the whole storage; seeds live in code and need no file.

    def _load_styles(self) -> None:
        """Seeds plus this project's saved overrides; box rebuilt to match."""
        raw = self.project.get_setting("paragraph_styles") if self.project else ""
        try:
            stored = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            stored = {}  # hand-edited settings may be junk; seeds still work
        if not isinstance(stored, dict):
            stored = {}
        self._user_styles = {name: signature for name, signature in stored.items()
                             if isinstance(name, str) and isinstance(signature, dict)}
        # The root's default.css is the written record of Default: when
        # it parses, it is what Default wears, even when the database
        # copy disagrees — a hand edit is never a stale cache.
        if self.project:
            path = self.project.root / DEFAULT_CSS_FILENAME
            if path.is_file():
                try:
                    parsed = parse_default_style(path.read_text(
                        encoding="utf-8"))
                except OSError:
                    parsed = {}
                if parsed:
                    self._user_styles["Default"] = parsed
        self._refresh_styles()

    def _default_style(self) -> dict | None:
        """The signature blank documents open in (PREFERENCES.md, Editor).

        "Default" is always the default: if the writer used some other
        formatting as the default, its details were copied into Default
        (the original style untouched). Whatever Default currently
        carries is what a fresh page wears.
        """
        return self._styles.get("Default")

    def _apply_default_style(self, document, html: str) -> None:
        """An empty document starts dressed in Default. Non-empty
        documents are left exactly as they are — a default is a first
        paragraph, not a reformatting."""
        if self.project.plain_text(html).strip():
            return
        style = self._default_style()
        if style is None:
            return
        scaled = dict(style)
        if "size" in scaled:
            scaled["size"] = round(float(scaled["size"]) * self.zoom, 2)
        block_format, char_format = style_signature_formats(scaled)
        cursor = self.editor.textCursor()
        cursor.mergeBlockFormat(block_format)
        cursor.mergeCharFormat(char_format)
        self.editor.mergeCurrentCharFormat(char_format)

    def _use_as_default_style(self, grade: str) -> None:
        """Copy the paragraph under the caret's formatting into Default.

        No named style is needed: the formatting itself becomes the
        default, and whatever it was (a named style, or nothing yet) is
        left exactly as it was. A heading default is only taken from a
        paragraph already dressed as one."""
        if not self.project:
            self.statusBar().showMessage(
                "Open a project before setting a default", 3000)
            return
        signature = self._capture_signature()
        if grade == "heading" and not (
                signature.get("weight") == int(QFont.Bold)
                and float(signature.get("size", 0) or 0) >= 15):
            self.statusBar().showMessage(
                "Not dressed as a heading — style it as a heading first",
                4000)
            return
        self._user_styles["Default"] = signature
        self._refresh_styles()
        self._persist_styles()
        self.statusBar().showMessage(
            f'New {"headings" if grade == "heading" else "paragraphs"} '
            "start in “Default”", 3000)

    def _refresh_styles(self) -> None:
        """Effective list: a saved style keeps its seed's place when it
        replaces the seed by name; new names join at the end."""
        self._styles = {**SEED_STYLES, **self._user_styles}
        self._rebuild_style_box()

    def _rebuild_style_box(self) -> None:
        current = self.style_box.currentData()
        self.style_box.clear()
        for name in self._styles:
            self.style_box.addItem(name, name)
        # The box's own way to make a style (2.4h): the capture commands
        # the right-click menu owns, reachable where the styles live.
        # Delete is deliberately not here — it is a library operation,
        # and Preferences grows the styles panel before anything else
        # owns it (the menu keeps Delete until then).
        self.style_box.insertSeparator(self.style_box.count())
        self.style_box.addItem("+ New style from paragraph…",
                               NEW_STYLE_COMMAND)
        self.style_box.addItem("Update style from paragraph…",
                               UPDATE_STYLE_COMMAND)
        index = self.style_box.findData(current)
        if index >= 0:
            self.style_box.setCurrentIndex(index)

    def _style_box_selection_tracked(self, index: int) -> None:
        """The last real style in the box (2.4h) — what the box's Update
        command re-captures, since the selection moves onto the command
        itself the moment the command is clicked."""
        name = self.style_box.itemData(index)
        if name in self._styles:
            self._style_selection = name

    def _restore_style_box(self) -> None:
        """After a command, back to the style the caret wears (2.4h) —
        the box must never sit on '+ New style…' as if it were one."""
        index = self.style_box.findData(self.style_at_cursor())
        if index < 0:
            index = self.style_box.findData(self._style_selection)
        index = max(0, index)
        self.style_box.blockSignals(True)
        self.style_box.setCurrentIndex(index)
        self.style_box.blockSignals(False)

    def _update_style_from_box(self) -> None:
        """The box's Update command (2.4h): re-capture the style the box
        was showing from the paragraph under the caret — the menu's
        Update, aimed at the style you were just looking at."""
        target = self._style_selection
        if not target:
            self.statusBar().showMessage(
                "Open a project, then pick a style to update", 4000)
            self._restore_style_box()
            return
        approved = self._approve_style_signature(target, self._capture_signature())
        if approved is not None and self.update_style_from_cursor(
                target, signature=approved):
            self.statusBar().showMessage(f'Style "{target}" updated', 3000)
        elif approved is not None:
            self.statusBar().showMessage(
                "Open a project, then pick a style to update", 4000)
        self._restore_style_box()

    def _persist_styles(self) -> bool:
        if not self.project:
            return False
        self.project.set_setting("paragraph_styles",
                                 json.dumps(self._user_styles, indent=1))
        # The root file carries the same record in human CSS: a writer
        # reading default.css by hand can take their styling with them
        # wherever they copy the project (docs/STORAGE.md).
        try:
            path = self.project.root / DEFAULT_CSS_FILENAME
            default = self._styles.get("Default") or SEED_STYLES["Default"]
            path.write_text(default_style_text(default), encoding="utf-8")
        except OSError:
            # The database copy still saved; the file re-writes itself
            # on the next styles save.
            pass
        return True

    def _capture_signature(self) -> dict:
        """The caret paragraph's style signature in *stored* units.

        capture_paragraph_signature reads the live formats, which carry
        display sizes (×zoom); a style is stored, so the factor is divided
        back out here — saving, updating and matching a style then all
        speak the same numbers, at any zoom.
        """
        signature = capture_paragraph_signature(self.editor.textCursor())
        if "size" in signature:
            signature["size"] = round(float(signature["size"]) / self.zoom, 2)
        return signature

    def save_style_from_cursor(self, name: str, signature: dict | None = None) -> bool:
        """Create: capture the paragraph under the caret under a new name.

        False means nothing happened — no project, empty name, or the
        name is already taken (that is what Update is for). ``signature``
        is the writer-approved subset from the approval dialog; when
        None, the full caret capture is saved (tests call this directly).
        """
        name = name.strip()
        if not self.project or not name or name in self._styles:
            return False
        self._user_styles[name] = (
            dict(signature) if signature is not None else self._capture_signature())
        self._refresh_styles()
        self._persist_styles()
        return True

    def update_style_from_cursor(self, name: str, signature: dict | None = None) -> bool:
        """Update: the named style's signature becomes the paragraph's now."""
        if not self.project or name not in self._styles:
            return False
        self._user_styles[name] = (
            dict(signature) if signature is not None else self._capture_signature())
        self._refresh_styles()
        self._persist_styles()
        return True

    def remove_style(self, name: str) -> bool:
        """Remove the shortcut only: formatting already written with it
        keeps wearing itself. Deleting with reversion travels through
        delete_style(), one level up."""
        if not self.project or name not in self._user_styles:
            return False
        del self._user_styles[name]
        self._refresh_styles()
        self._persist_styles()
        return True

    def _worn_documents(self, name: str) -> dict[str, int]:
        """document id → count of paragraphs wearing this style.

        Compared on the style's own keys only, so a paragraph visually
        wearing it counts even decorated with more. Empty documents
        are skipped.
        """
        style = self._styles.get(name)
        if not self.project or style is None:
            return {}
        worn: dict[str, int] = {}
        for document in self.project.documents():
            html = self.project.read_content(document.id)
            if not html.strip():
                continue
            scratch = QTextDocument()
            scratch.setHtml(html)
            count = 0
            for index in range(scratch.blockCount()):
                block = scratch.findBlockByNumber(index)
                cursor = QTextCursor(scratch)
                # One step in, so the run itself answers, not the
                # block's left edge (empty paragraph: the edge stands).
                if block.length() > 1:
                    cursor.setPosition(block.position() + 1)
                else:
                    cursor.setPosition(block.position())
                captured = capture_paragraph_signature(cursor)
                if all(captured.get(key) == value
                       for key, value in style.items()):
                    count += 1
            if count:
                worn[document.id] = count
        return worn

    def _strip_style_from_documents(self, worn: dict[str, int]) -> None:
        """Return every wearing paragraph to Default, in place, in the
        stored files — so deleting a style also takes the "css" with
        it instead of leaving text dressed in a style no list
        mentions. Locked documents are left alone."""
        default = self._default_style()
        if default is None or not self.project:
            return
        scaled = dict(default)
        if "size" in scaled:
            scaled["size"] = round(float(scaled["size"]) * self.zoom, 2)
        block_format, char_format = style_signature_formats(scaled)
        for document_id in worn:
            if self.project.is_locked(document_id):
                continue
            scratch = QTextDocument()
            scratch.setHtml(self.project.read_content(document_id))
            for index in range(scratch.blockCount()):
                block = scratch.findBlockByNumber(index)
                cursor = QTextCursor(scratch)
                cursor.setPosition(block.position())
                cursor.select(QTextCursor.BlockUnderCursor)
                cursor.mergeBlockFormat(block_format)
                cursor.mergeCharFormat(char_format)
            self.project.write_content(
                document_id,
                self.project.canonicalize_links(
                    scaled_font_sizes(scratch.toHtml(), 1.0 / self.zoom)))

    def _delete_with_consequences(self, name: str, revert: bool) -> bool:
        """The Delete dialog's path: revert wearing paragraphs to
        Default first — a delete that changes the "css" along with the
        shortcut, so no writer is puzzled afterwards — then the
        shortcut itself goes."""
        worn = self._worn_documents(name) if revert else {}
        if worn:
            self._strip_style_from_documents(worn)
        removed = self.remove_style(name)
        if removed and worn:
            docs = len(worn)
            self.statusBar().showMessage(
                f"“{name}” deleted — its formatting went {sum(worn.values())} "
                f"paragraph(s) back to Default across {docs} document(s)",
                5000)
        return removed

    def delete_style(self, name: str, revert: bool = False) -> bool:
        """Delete a saved style; deleting a seed's override brings the
        seed back, seeds themselves are never stored or removable."""
        if not self.project or name not in self._user_styles:
            return False
        return self._delete_with_consequences(name, revert)

    # --- standardising text: quotes and dashes (Edit menu) -----------

    @staticmethod
    def _quote_repairs(text: str, mode: str) -> list[tuple[int, str]]:
        """(position, replacement) pairs to apply on ``text``.

        Straight → curly is the tidy direction — it guesses openers and
        closers from what precedes each mark — while curly → straight
        is one to one and safe. Every replacement is one character for
        one character, so positions stay valid while they are applied.
        """
        if mode == "straight":
            swapped = {"“": '"', "”": '"', "‘": "'", "’": "'"}
            return [(index, swapped[char]) for index, char in enumerate(text)
                    if char in swapped]
        opens = ("", "(", "[", "{", "<", "—", "–", "-", "“", "‘", "«", "‹")
        repairs: list[tuple[int, str]] = []
        previous = ""
        for index, char in enumerate(text):
            if char == '"':
                opens_too = (index == 0 or previous.isspace()
                             or previous in opens)
                repairs.append(
                    (index, "“" if opens_too else "”"))
            elif char == "'":
                repairs.append(
                    (index, "’" if previous.isalnum() else "‘"))
            previous = char
        return repairs

    @staticmethod
    def _dash_pairs(text: str) -> list[int]:
        """Positions of every ``--`` in text (for the em-dash pass)."""
        return [match.start() for match in re.finditer(r"--", text)]

    def _standardise_qdocument(self, document, quotes: str,
                               dashes: bool) -> int:
        """Apply the conversions in place on a QTextDocument; returns
        the number of replacements made."""
        full = document.toPlainText()
        made = 0
        if quotes != "keep":
            for position, replacement in sorted(
                    self._quote_repairs(full, quotes), reverse=True):
                cursor = QTextCursor(document)
                cursor.setPosition(position)
                cursor.movePosition(QTextCursor.MoveOperation.NextCharacter,
                                    QTextCursor.MoveMode.KeepAnchor, 1)
                cursor.insertText(replacement)
                made += 1
        if dashes:
            full = document.toPlainText()
            for position in reversed(self._dash_pairs(full)):
                cursor = QTextCursor(document)
                cursor.setPosition(position)
                cursor.movePosition(QTextCursor.MoveOperation.NextCharacter,
                                    QTextCursor.MoveMode.KeepAnchor, 2)
                cursor.insertText("—")
                made += 1
        return made

    def _standardise_offline(self, quotes: str, dashes: bool,
                             ids: set[str] | None = None) -> int:
        """The same conversions on stored files — all documents if
        ``ids`` is None — reloaded into the editor if one was open."""
        if not self.project:
            return 0
        total = 0
        changed: set[str] = set()
        targets = ([document.id for document in self.project.documents()]
                   if ids is None else list(ids))
        for document_id in targets:
            if self.project.is_locked(document_id):
                continue
            html = self.project.read_content(document_id)
            if not html.strip():
                continue
            scratch = QTextDocument()
            scratch.setHtml(html)
            made = self._standardise_qdocument(scratch, quotes, dashes)
            if made:
                total += made
                changed.add(document_id)
                self.project.write_content(
                    document_id,
                    self.project.canonicalize_links(
                        scaled_font_sizes(scratch.toHtml(), 1.0 / self.zoom)))
        if changed and self.current_id in changed:
            reloaded = self.project.read_content(self.current_id)
            self.editor.setHtml(scaled_font_sizes(reloaded, self.zoom))
        return total

    def show_standardise_dialog(self) -> None:
        """Quotes restyle and the two-hyphen dash (Edit → Standardise):
        the manuscript's typography, standardised in one place."""
        if not self.project:
            self.statusBar().showMessage(
                "Open a project before standardising", 3000)
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Standardise text")
        layout = QVBoxLayout(dialog)
        quotes_box = QButtonGroup(dialog)
        keep = QRadioButton("Quotes: keep as they are")
        curly = QRadioButton("Quotes: make curly (“ ” ‘ ’)")
        straight = QRadioButton("Quotes: make straight (\" ')")
        keep.setChecked(True)
        quotes_box.addButton(keep)
        quotes_box.addButton(curly)
        quotes_box.addButton(straight)
        layout.addWidget(QLabel("Quotes"))
        layout.addWidget(keep)
        layout.addWidget(curly)
        layout.addWidget(straight)
        dashes = QCheckBox("Replace two hyphens (--) with an em dash (—)")
        layout.addWidget(dashes)
        scope_box = QButtonGroup(dialog)
        here = QRadioButton("This document only")
        everywhere = QRadioButton("The whole project")
        everywhere.setChecked(True)
        scope_box.addButton(here)
        scope_box.addButton(everywhere)
        layout.addWidget(QLabel("Where"))
        layout.addWidget(here)
        layout.addWidget(everywhere)
        note = QLabel("Whole-project changes cannot be undone.")
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Apply | QDialogButtonBox.Close)
        apply_button = buttons.button(QDialogButtonBox.Apply)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        def run() -> None:
            if not self.save_current():
                return
            mode = ("keep" if keep.isChecked()
                    else "curly" if curly.isChecked() else "straight")
            made = 0
            if here.isChecked():
                if self.current_id:
                    made = self._standardise_qdocument(
                        self.editor.document(), mode, dashes.isChecked())
            else:
                made = self._standardise_offline(
                    mode, dashes.isChecked())
            self.statusBar().showMessage(
                f"{made} replacement(s) made", 5000)

        apply_button.clicked.connect(run)
        dialog.exec()

    def _style_usage(self, name: str) -> tuple[int, int]:
        """(documents, paragraphs) this style dresses — what a delete
        would say."""
        worn = self._worn_documents(name)
        return (len(worn), sum(worn.values()))

    def _current_default_css(self) -> str:
        """The Default style as human-level file text (default.css),
        generated from what the app currently has when no file exists."""
        if not self.project:
            return ""
        path = self.project.root / DEFAULT_CSS_FILENAME
        if path.is_file():
            try:
                raw = path.read_text(encoding="utf-8")
                if raw.strip():
                    return raw
            except OSError:
                pass
        return default_style_text(
            self._styles.get("Default") or SEED_STYLES["Default"])

    def _apply_default_css(self, text: str) -> bool:
        """Write hand text into Default — parsed back into its
        signature, saved to the database and the file together."""
        parsed = parse_default_style(text)
        if not parsed or not self.project:
            return False
        self._user_styles["Default"] = parsed
        self._refresh_styles()
        self._persist_styles()
        return True

    def _save_style_from_preferences(self, name: str, signature: dict) -> None:
        """Preferences' style editor arrives with a signature; it lands
        through the same save as the editor's own."""
        if not self.project:
            return
        self._user_styles[name] = signature
        self._refresh_styles()
        self._persist_styles()

    def style_at_cursor(self) -> str | None:
        """The style the paragraph under the caret already wears, or None.

        Compared on the style's own keys only, so a built-in seed (which
        never mentions alignment or family) matches the very paragraph it
        just formatted even though the capture recorded more.
        """
        captured = self._capture_signature()
        for name, style in self._styles.items():
            if all(captured.get(key) == value for key, value in style.items()):
                return name
        return None

    def _editor_context_menu(self, position) -> None:
        menu = self._build_editor_menu()
        menu.exec(self.editor.mapToGlobal(position))

    def _build_editor_menu(self) -> QMenu:
        """Qt's own right-click menu, extended with the style commands.

        Split from the exec so tests can inspect the actions; the dialogs
        stay thin — capture and persistence live in
        save_style_from_cursor and friends, which tests call directly
        instead of driving a dialog.
        """
        menu = self.editor.createStandardContextMenu()
        menu.addSeparator()
        create = menu.addAction("Create Style from Paragraph…")
        update = menu.addAction("Update Style from Paragraph…")
        delete = menu.addAction("Delete Style…")
        update.setEnabled(bool(self._styles))
        delete.setEnabled(bool(self._user_styles))
        create.triggered.connect(self._create_style_dialog)
        update.triggered.connect(self._update_style_dialog)
        delete.triggered.connect(self._delete_style_dialog)
        default_paragraph = menu.addAction("Use as Default Paragraph…")
        default_heading = menu.addAction("Use as Default Heading…")
        default_paragraph.triggered.connect(
            lambda: self._use_as_default_style("paragraph"))
        default_heading.triggered.connect(
            lambda: self._use_as_default_style("heading"))
        return menu

    def _approve_style_signature(self, name: str, signature: dict) -> dict | None:
        """Design-time approval sheet for a new/updated style (1.0).

        Shows every property `capture_paragraph_signature` found,
        ticked to sensible delta-capture defaults, and returns only
        the ticked subset — or None on Cancel. The writer's tick is
        the record of what the style may change; unticked formatting
        is never written. Cardinal sin: italic/bold/underline start
        unticked unless uniform across the block, so a mixed accent
        can't become paragraph-wrapping by a missed click.
        """
        uniform = block_uniform_flags(self.editor.textCursor())
        def _bold(v) -> bool:
            return int(v) >= int(QFont.Bold)
        # (key, label, sensible default)
        defaults: dict[str, bool] = {}
        # Block layout: always part of a paragraph style.
        for key in ("top_margin", "bottom_margin", "left_margin",
                    "right_margin", "indent", "text_indent", "alignment"):
            if key in signature:
                defaults[key] = True
        # Family/size: only if uniform across the block (else capturing
        # one fragment's size would overwrite the rest).
        if "family" in signature:
            defaults["family"] = bool(uniform.get("family"))
        if "size" in signature:
            defaults["size"] = bool(uniform.get("size"))
        # Accents: only if uniform AND actually on. Normal/plain is
        # never a paragraph style's to enforce.
        if "italic" in signature:
            defaults["italic"] = bool(uniform.get("italic")) and bool(signature["italic"])
        if "weight" in signature:
            defaults["weight"] = bool(uniform.get("weight")) and _bold(signature["weight"])
        if "underline" in signature:
            defaults["underline"] = bool(uniform.get("underline")) and bool(signature["underline"])
        # Line-height: keep off by default (rarely part of a style name).
        if "line_height" in signature:
            defaults["line_height"] = False

        def _label(key: str, value) -> str:
            if key == "size":
                return f"Font size: {float(value):g} pt"
            if key == "family":
                return f"Font family: {value}"
            if key == "weight":
                return "Bold" if _bold(value) else f"Weight: {value}"
            if key == "italic":
                return "Italic" if value else "Italic: off"
            if key == "underline":
                return "Underline"
            if key == "alignment":
                names = {int(Qt.AlignmentFlag.AlignCenter): "Centre",
                         int(Qt.AlignmentFlag.AlignRight): "Right",
                         int(Qt.AlignmentFlag.AlignJustify): "Justify",
                         int(Qt.AlignmentFlag.AlignLeft): "Left"}
                return f"Alignment: {names.get(int(value), 'Left')}"
            if key == "line_height":
                return f"Line height: {float(value[0]):g}"
            return f"{key.replace('_', ' ').title()}: {value}"

        dialog = QDialog(self)
        dialog.setWindowTitle(f"Style: {name}")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(
            "Tick only what this style should change. Unchecked "
            "formatting stays as-is — a paragraph style never rewrites "
            "italics/bold/underline you didn't tick."))
        boxes: dict[str, QCheckBox] = {}
        for key in ("family", "size", "weight", "italic", "underline",
                    "top_margin", "bottom_margin", "left_margin",
                    "right_margin", "indent", "text_indent", "alignment",
                    "line_height"):
            if key not in signature:
                continue
            box = QCheckBox(_label(key, signature[key]))
            box.setChecked(bool(defaults.get(key, False)))
            layout.addWidget(box)
            boxes[key] = box
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        self._style_approve_dialog = dialog
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        approved = {key: signature[key]
                    for key, box in boxes.items() if box.isChecked()}
        if not approved:
            QMessageBox.warning(
                self, f"Style: {name}",
                "Nothing was ticked — nothing was saved.")
            return None
        return approved

    def _create_style_dialog(self) -> None:
        if not self.project:
            self.statusBar().showMessage("Open a project before saving styles", 3000)
            return
        name, ok = QInputDialog.getText(self, "Create Style", "Style name:")
        if not ok or not name or not name.strip():
            return
        name = name.strip()
        if name in self._styles:
            QMessageBox.warning(
                self, "Style exists",
                f'A style called "{name}" already exists — use '
                "Update Style to replace it.",
            )
            return
        approved = self._approve_style_signature(name, self._capture_signature())
        if approved is None:
            return
        if not self.save_style_from_cursor(name, signature=approved):
            QMessageBox.warning(
                self, "Style exists",
                f'A style called "{name}" already exists — use '
                "Update Style to replace it.",
            )
            return
        self.statusBar().showMessage(f'Style "{name}" saved', 3000)

    def _update_style_dialog(self) -> None:
        if not self.project:
            self.statusBar().showMessage("Open a project before saving styles", 3000)
            return
        names = list(self._styles)
        current = self.style_at_cursor()
        index = names.index(current) if current is not None else 0
        name, ok = QInputDialog.getItem(
            self, "Update Style",
            "Replace this style with the current paragraph:", names, index, False,
        )
        if not ok:
            return
        approved = self._approve_style_signature(name, self._capture_signature())
        if approved is None:
            return
        self.update_style_from_cursor(name, signature=approved)
        self.statusBar().showMessage(f'Style "{name}" updated', 3000)

    def _delete_style_dialog(self) -> None:
        if not self.project or not self._user_styles:
            return
        name, ok = QInputDialog.getItem(
            self, "Delete Style", "Style to delete:",
            list(self._user_styles), 0, False,
        )
        if not ok:
            return
        self.remove_style(name)
        self.statusBar().showMessage(f'Style "{name}" deleted', 3000)

    def bullet_list(self) -> None:
        self.editor.textCursor().createList(QTextListFormat.ListDisc)

    def numbered_list(self) -> None:
        self.editor.textCursor().createList(QTextListFormat.ListDecimal)

    def clear_formatting(self) -> None:
        cursor = self.editor.textCursor()
        fmt = QTextCharFormat()
        fmt.setFontPointSize(ZOOM_ANCHOR_PT * self.zoom)
        cursor.mergeCharFormat(fmt)
        cursor.setBlockFormat(QTextBlockFormat())
        self.editor.setTextCursor(cursor)

    def preferences(self) -> None:
        current = QApplication.font().pointSize()
        # The voice list needs a connected engine; _speech caches one
        # for the window, so this handshake is once per session.
        reader = self._speech()
        dialog = PreferencesDialog(current, load_paste_prefs(),
                                   revision_author(), ai.load_settings(),
                                   tts.load_settings(),
                                   reader.voice_names() if reader else [],
                                   project_root=self.project.root
                                   if self.project else None,
                                   styles=dict(self._styles),
                                   default_css_text=self._current_default_css(),
                                   apply_css=self._apply_default_css,
                                   style_names=lambda: list(self._styles),
                                   delete_style=self.delete_style,
                                   style_usage=self._style_usage,
                                   save_style=self._save_style_from_preferences,
                                   project=self.project,
                                   open_guide=self.open_guide,
                                   show_placeholders=lambda:
                                   self.show_placeholder_reference(
                                       insert=False),
                                   parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        size = dialog.size_slider.value()
        font = QApplication.font()
        font.setPointSize(size)
        QApplication.setFont(font)
        # QApplication.setFont alone doesn't reach widgets that already
        # exist; set it on the window so it cascades. The editor is pinned
        # with an explicit font, so it ignores the cascade and keeps its
        # 12 pt writing size.
        self.setFont(font)
        settings = QSettings()
        settings.setValue("ui/fontSize", size)
        settings.setValue("ui/theme", str(dialog.theme_box.currentData() or "system"))
        self.apply_theme()
        settings.setValue("paste/fonts", dialog.paste_fonts.isChecked())
        settings.setValue("paste/paragraphs", dialog.paste_paragraphs.isChecked())
        settings.setValue("paste/inline", dialog.paste_inline.isChecked())
        settings.setValue("paste/alignment", dialog.paste_alignment.isChecked())
        settings.setValue("revisions/author",
                          dialog.author_edit.text().strip() or "Author")
        settings.setValue("spell/dictionary",
                          dialog.dictionary_edit.text().strip())
        settings.setValue("spell/language",
                          str(dialog.language_combo.currentData() or ""))
        from lekhak import spellcheck as _sc
        _sc.set_language(str(dialog.language_combo.currentData() or ""))
        settings.setValue(
            "update/mode", str(dialog.update_mode_box.currentData() or "manual"))
        settings.setValue("ai/enabled", dialog.ai_enabled_box.isChecked())
        settings.setValue("tts/enabled", dialog.tts_enabled_box.isChecked())
        settings.setValue("editor/autosaveMs", dialog.autosave_spin.value())
        settings.setValue("session/reopenLast",
                          dialog.reopen_last_box.isChecked())
        settings.setValue("files/backupFrequency",
                          dialog.backup_frequency_box.currentData())
        settings.setValue("files/backupFolder",
                          dialog.backup_folder_edit.text().strip())
        # A project that has never chosen a folder follows the new
        # default; one that has chosen keeps its own.
        if self.project and not self.project.get_setting("backup_folder"):
            self.project.set_setting("backup_folder",
                                     settings.value("files/backupFolder",
                                                    "", type=str))
        settings.setValue("link_policy/default",
                          dialog.link_default_box.currentData())
        settings.setValue("related/enabled",
                          dialog.related_box.isChecked())
        # Live like the autosave interval: the section may be on screen
        # under the dialog. The metadata table matters for the same
        # reason and more: Placeholders adds project-wide keys directly,
        # and this table writes its whole row set back when a cell is
        # edited, so a stale table would drop them.
        self._refresh_related()
        self._populate_metadata_table()
        self._refresh_optional_menus()
        self.save_timer.setInterval(dialog.autosave_spin.value())
        # Squiggles share one toggle with the View menu action; going
        # through it keeps the menu check and the persisted setting
        # honest, including the no-engine-on-this-system case.
        self._toggle_spell_squiggles(dialog.spell_check_box.isChecked())
        ai.save_settings(ai.AISettings(
            endpoint=dialog.ai_endpoint_edit.text().strip(),
            model=dialog.ai_model_edit.text().strip(),
            key=dialog.ai_key_edit.text(),
            prompt=dialog.ai_prompt_edit.toPlainText().strip(),
        ))
        tts.save_settings(tts.TTSSettings(
            voice=dialog.tts_voice_box.currentText(),
            rate=dialog.tts_rate_slider.value() / 100.0,
            engine=tts.load_settings().engine,
            source=next(value for value, radio
                        in dialog.tts_source_buttons.items()
                        if radio.isChecked()),
            piper_binary=dialog.piper_binary_edit.text().strip(),
            piper_voice=dialog.piper_voice_edit.text().strip(),
            api_endpoint=dialog.api_endpoint_edit.text().strip(),
            api_model=dialog.api_model_edit.text().strip(),
            api_key=dialog.api_key_edit.text(),
            api_voice=dialog.api_voice_edit.text().strip(),
        ))
        # The live engine (if one is up) picks the change up at the
        # next Read Aloud; apply it here too so a voice swap is instant.
        if self._reader is not None:
            self._reader.apply_settings()
        self.statusBar().showMessage(
            f"Interface font set to {size} pt; unpublished links "
            f"default to {dialog.link_default_box.currentText()}", 3000)

    def replace_in_project(self) -> None:
        if not self.project:
            return
        # Replace writes the stored files; letting it run after a failed
        # save would leave the editor holding pre-replace text that the
        # next successful save would write back over the replacements.
        if not self.save_current():
            return
        dialog = ProjectReplaceDialog(self.project, on_goto=self._select_document,
                                      parent=self)
        dialog.exec()
        self._reload_tree(self.current_id)

    def find_replace(self) -> None:
        dialog = FindReplaceDialog(self.editor, self)
        dialog.show()
        dialog.find_text.setFocus()
        self._find_dialog = dialog

    # --- editor zoom (display only) ----------------------------------------
    # The live document is rendered at stored sizes × self.zoom. Nothing
    # here may reach a file: save_current() and _refresh_page_preview()
    # divide the factor back out, and every method that turns live formats
    # into stored ones (a style signature, a pasted fragment) undoes it too.

    def _apply_zoom_base(self) -> None:
        """Point the editor's own font, and its display scale, at the zoom.

        The base font is what an unformatted paragraph inherits, so it has
        to carry the factor as well — otherwise newly typed text would sit
        at the stored size while everything around it is magnified.
        """
        font = self.editor.font()
        font.setPointSizeF(ZOOM_ANCHOR_PT * self.zoom)
        self.editor.setFont(font)
        self.editor.display_scale = self.zoom
        if hasattr(self, "zoom_label"):     # built after the window body
            self.zoom_label.setText(f"{round(self.zoom * 100)}%")

    def _rescale_editor(self, ratio: float) -> None:
        """Scale the live document's explicit sizes by ``ratio``, in place.

        In place rather than by reloading stored HTML, so unsaved edits,
        the caret and the undo history all survive a zoom change — a
        reload would throw away every one of them. Text that inherits the
        base font is not touched here; _apply_zoom_base moves that.
        """
        if ratio == 1.0:
            return
        document = self.editor.document()
        cursor = QTextCursor(document)
        cursor.beginEditBlock()
        block = document.firstBlock()
        while block.isValid():
            fragment = block.begin()
            while not fragment.atEnd():
                item = fragment.fragment()
                if (item.isValid()
                        and item.charFormat().objectType() == QTextFormat.NoObject):
                    size = item.charFormat().fontPointSize()
                    if size > 0:
                        fmt = QTextCharFormat(item.charFormat())
                        fmt.setFontPointSize(round(size * ratio, 2))
                        cursor.setPosition(item.position())
                        cursor.setPosition(item.position() + item.length(),
                                           QTextCursor.KeepAnchor)
                        cursor.setCharFormat(fmt)
                fragment += 1
            block = block.next()
        cursor.endEditBlock()

    def set_zoom(self, value: float) -> None:
        """Set the display zoom, and remember it as session state (1.9).

        Zoom is a property of the writing session, not of the manuscript:
        it travels with the project the way last_document and last_cursor
        do, and it is divided out again before anything is stored.
        """
        value = min(max(round(value, 2), ZOOM_MIN), ZOOM_MAX)
        if value == self.zoom:
            return
        self._rescale_editor(value / self.zoom)
        self.zoom = value
        self._apply_zoom_base()
        self.page_view.set_user_scale(value)
        if self.project:
            self.project.set_setting("editor_zoom", f"{value:.2f}")
        self._refresh_page_preview()
        self.statusBar().showMessage(f"Zoom {round(value * 100)}%", 1500)

    def zoom_by(self, delta: float) -> None:
        self.set_zoom(self.zoom + delta)

    def zoom_in(self) -> None:
        self.zoom_by(ZOOM_STEP)

    def zoom_out(self) -> None:
        self.zoom_by(-ZOOM_STEP)

    def reset_zoom(self) -> None:
        self.set_zoom(1.0)

    def _load_zoom(self) -> None:
        """Restore the zoom the project was last left at (session state)."""
        raw = self.project.get_setting("editor_zoom", "1") if self.project else "1"
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = 1.0     # hand-edited settings may be junk; 100% still works
        self.zoom = min(max(round(value, 2), ZOOM_MIN), ZOOM_MAX)
        self._apply_zoom_base()
        self.page_view.set_user_scale(self.zoom)

    def toggle_focus_mode(self) -> None:
        self.focus_mode = not self.focus_mode
        self.tree.setVisible(not self.focus_mode)
        if hasattr(self, "binder_page"):
            self.binder_page.setVisible(not self.focus_mode)
        self.synopsis.parentWidget().setVisible(not self.focus_mode)
        self.menuBar().setVisible(not self.focus_mode)
        for toolbar in self.findChildren(QToolBar):
            toolbar.setVisible(not self.focus_mode)
        # The Editor tab's flowing format bar is a plain widget now
        # (2.4h), so the sweep above must name it too.
        self.format_bar.setVisible(not self.focus_mode)
        self.statusBar().setVisible(not self.focus_mode)
        if self.focus_mode:
            self.views.setCurrentWidget(self.editor_page)
            self.editor.setFocus()

    def _update_word_count(self) -> None:
        words = len(self.editor.toPlainText().split())
        target = self.target.value()
        cursor = self.editor.textCursor()
        parts = []
        if target:
            percent = min(100, round(words * 100 / target))
            parts.append(f"{words:,} / {target:,} words ({percent}%)")
        else:
            parts.append(f"{words:,} words")
        # Reading time, from the same count: an estimate, and the words
        # it came from are right beside it.
        estimate = reading_time(words)
        if estimate:
            parts.append(f"~{estimate} reading")
        if cursor.hasSelection():
            selected = len(cursor.selectedText().split())
            parts.append(f"{selected:,} selected")
        if self._project_total:
            parts.append(f"{self._project_total:,} in project")
        # Daily goal (6): progress toward today's target, plus how far
        # this session has moved the project — two different baselines
        # once a goal outlives a restart.
        goal, progress = self._session_progress()
        if goal:
            parts.append(f"{progress:,} / {goal:,} today")
            delta = self._project_total - self._session_start_total
            parts.append(f"{delta:+,} this session")
        if self.current_id:
            broken = self._broken_links()
            if broken:
                parts.append(f"broken link(s): {', '.join(broken)}")
        self.word_count_label.setText(" · ".join(parts))

    def _session_progress(self) -> tuple[int, int]:
        """(daily goal, words toward it today) — BUILD-NOW 6.

        Three project settings carry it: the target itself, the day the
        count belongs to, and the project total today's count started
        from. A stored day that isn't today re-bases silently, so each
        day starts at zero while the target carries over — and all three
        survive a restart, which is the whole point of a per-day goal
        that nobody wants to set twice.
        """
        if not self.project:
            return 0, 0
        try:
            goal = int(self.project.get_setting("session_goal", "0"))
        except ValueError:
            return 0, 0
        if goal <= 0:
            return 0, 0
        today = QDate.currentDate().toString(Qt.ISODate)
        if self.project.get_setting("session_goal_day") != today:
            # Re-base from a freshly read total, never _project_total:
            # this can run mid-switch, while that field still holds the
            # previous project's sum.
            documents = self.project.documents()
            fresh = sum(d.word_count for d in documents)
            self.project.set_setting("session_goal_day", today)
            self.project.set_setting("session_goal_baseline", str(fresh))
        try:
            baseline = int(
                self.project.get_setting("session_goal_baseline", "0"))
        except ValueError:
            baseline = 0
        return goal, max(0, self._project_total - baseline)

    def set_session_goal(self) -> None:
        """Pick today's word target; 0 clears it (BUILD-NOW 6)."""
        if not self.project:
            self.statusBar().showMessage("Open a project first", 3000)
            return
        try:
            current = int(self.project.get_setting("session_goal", "0"))
        except ValueError:
            current = 0
        value, accepted = QInputDialog.getInt(
            self, "Daily goal", "Words to write today:",
            current or 2000, 0, 1_000_000, 100)
        if not accepted:
            return
        self.project.set_setting("session_goal", str(value))
        self.project.set_setting(
            "session_goal_day", QDate.currentDate().toString(Qt.ISODate))
        # Today's count starts where the saved totals stand right now.
        self.project.set_setting(
            "session_goal_baseline", str(self._project_total))
        self._update_word_count()
        if value:
            self.statusBar().showMessage(f"Daily goal: {value:,} words", 3000)
        else:
            self.statusBar().showMessage("Daily goal cleared", 3000)

    def closeEvent(self, event) -> None:
        self.stop_speech()
        if not self.save_current():
            answer = QMessageBox.warning(
                self, "Save failed",
                "Your latest changes could not be written to disk.\n\n"
                "Quit anyway and lose them?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                event.ignore()
                return
        if self.project:
            # Closing is one of the moments an automatic backup may be due.
            if self._backup_is_due(closing=True):
                try:
                    self.project.backup()
                except Exception:
                    pass        # a failed backup must not block the close
            self.project.close()
        event.accept()


def main() -> int:
    application = QApplication(sys.argv)
    application.setApplicationName("Lekhak")
    application.setOrganizationName("Lekhak")
    application.setDesktopFileName("lekhak")  # matches lekhak.desktop on Wayland/GNOME
    # Interface font only; the editor is pinned to 12 pt independently, so
    # this never reaches document content.
    stored_size = QSettings().value("ui/fontSize", type=int)
    # Apply theme before the window builds, so first paint already wears it
    try:
        from PySide6.QtGui import QPalette as _QP
        from lekhak.app import MainWindow as _MW
        _theme = QSettings().value("ui/theme", "system", type=str)
        _pal = _MW._theme_palette(_theme)
        if _pal is not None:
            application.setPalette(_pal)
    except Exception:
        pass
    if stored_size:
        interface_font = application.font()
        interface_font.setPointSize(stored_size)
        application.setFont(interface_font)
    window = MainWindow()
    window.show()
    window.reopen_last_project()  # 1.9 resume-on-start
    window.start_update_check()   # Auto/Check only; Manual is the default
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
