"""Preferences storage, dialogs and related helpers."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QSettings, QStandardPaths, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QGuiApplication
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QProgressDialog,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPlainTextEdit, QPushButton, QRadioButton, QSlider,
    QSpinBox, QStackedWidget, QTextEdit, QToolButton, QVBoxLayout, QWidget,
)

from . import __version__, ai, tts
from .project import DEFAULT_LINK_POLICY
from .templates import BUILTIN_PLACEHOLDERS

# Display labels for the strip policies, in UI order; the values are the
# strings book_settings / the project setting store.
LINK_POLICY_CHOICES = (
    ("Strip unpublished", "strip"),
    ("Plain text", "plain-text"),
    ("Keep as note", "keep-as-note"),
)

_AUTOSAVE_DEFAULT_MS = 700

# How often automatic checks may look online.
UPDATE_CHECK_INTERVAL = timedelta(days=1)

# Check automatically (notify when an update is available), only when asked,
# or never. Older "auto" maps to automatic checking; in-place install remains
# available only for git checkouts via the update action.
UPDATE_MODES = (
    ("Check automatically", "check"),
    ("Check only when I ask", "manual"),
    ("Never check", "never"),
)


def update_mode() -> str:
    """Return the stored update-check mode (normalised for older values)."""
    mode = str(QSettings().value("update/mode", "manual", type=str))
    if mode == "ask":
        return "manual"
    if mode == "auto":
        return "check"
    return mode


def git_toplevel() -> Path | None:
    """The git checkout this install lives in, or None.

    A pip, pipx or zip install has no working tree, so there is nothing
    to pull and the writer is told about the release instead.
    """
    import shutil
    import subprocess
    if not shutil.which("git"):
        return None
    here = Path(__file__).resolve()
    for parent in (here.parent, here.parent.parent, here.parent.parent.parent):
        try:
            run = subprocess.run(
                ["git", "-C", str(parent), "rev-parse", "--show-toplevel"],
                capture_output=True, text=True, timeout=5)
        except Exception:
            continue
        if run.returncode == 0:
            return Path(run.stdout.strip())
    return None


def release_repo() -> str:
    """Which GitHub repo to ask: this checkout's own remote, else the
    author's, so a fork reads its own releases rather than ours."""
    import re
    import subprocess
    toplevel = git_toplevel()
    if toplevel is not None:
        try:
            remote = subprocess.run(
                ["git", "-C", str(toplevel), "remote", "get-url", "origin"],
                capture_output=True, text=True, timeout=5).stdout.strip()
        except Exception:
            remote = ""
        found = re.search(r"github\.com[:/]([^/]+)/([^/\.]+)", remote)
        if found:
            return f"{found.group(1)}/{found.group(2)}"
    return "Vidyut/lekhika"


def numeric_version(text: str) -> tuple[int, ...]:
    """The numbers in a version, without a leading "v" or a pre-release
    suffix — so "1.0.0" and "v1.0.0" compare equally, and "1.1.0-rc1"
    is not read as newer than "1.1.0"."""
    return tuple(int(part) for part in text.lstrip("v").split(".")
                 if part.isdigit())


def _preferred_download_asset(assets: list[dict]) -> str | None:
    """Pick the best release asset download URL for this platform/arch."""
    import platform
    plat = sys.platform
    machine = platform.machine().lower()
    names = [(str(a.get("name", "")), str(a.get("browser_download_url", "")))
             for a in assets if isinstance(a, dict)]
    def pick(*needles):
        for name, url in names:
            lname = name.lower()
            if all(n in lname for n in needles) and url:
                return url
        return None
    if plat == "win32":
        if machine in ("arm64", "aarch64"):
            return pick("windows", "arm64") or pick("windows")
        return pick("windows", "x64") or pick("windows")
    if plat == "darwin":
        if machine in ("arm64", "aarch64"):
            return pick("macos", "arm64") or pick("macos")
        return pick("macos", "intel") or pick("macos", "x64") or pick("macos")
    # linux/other
    if machine in ("aarch64", "arm64"):
        return (pick("linux", "aarch64", ".appimage") or pick("linux", "arm64", ".appimage")
                or pick("linux", "arm64", ".zip") or pick("linux", "aarch64"))
    return (pick("linux", "x86_64", ".appimage") or pick("linux", "x64", ".appimage")
            or pick("linux", "x64", ".zip") or pick("linux"))


def newer_release() -> dict | None:
    """Info for a newer version than this build, else None.

    Read-only API calls. Checks tags for the latest version and tries to
    resolve a direct download asset for this platform from the matching
    GitHub release. Keys: current, latest, page, download (optional).
    """
    import json
    import urllib.request
    current = __version__
    repo = release_repo()
    headers = {"Accept": "application/vnd.github+json",
               "User-Agent": "Lekhika"}
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/tags?per_page=100",
        headers=headers)
    with urllib.request.urlopen(request, timeout=10) as response:
        tags = json.loads(response.read().decode("utf-8"))
    names = [str(tag.get("name", ""))
             for tag in tags if isinstance(tag, dict)]
    latest_name = max(names, key=numeric_version, default="")
    latest = latest_name.lstrip("v").strip()
    if not (latest and numeric_version(latest) > numeric_version(current)):
        return None
    page = f"https://github.com/{repo}/releases/tag/{latest_name or ('v' + latest)}"
    download = None
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{repo}/releases/tags/{latest_name}",
            headers=headers)
        with urllib.request.urlopen(req, timeout=10) as response:
            release = json.loads(response.read().decode("utf-8"))
        assets = release.get("assets", []) if isinstance(release, dict) else []
        download = _preferred_download_asset(assets) or str(release.get("html_url") or "") or None
        if isinstance(release, dict) and release.get("html_url"):
            page = str(release["html_url"])
    except Exception:
        page = f"https://github.com/{repo}/releases"
    return {"current": current, "latest": latest, "page": page, "download": download}


def apply_update(current: str, latest: str) -> bool:
    """Take a newer release for the writer who chose Automatic.

    Only a clean git checkout, and never across a major version: a new
    major may change the shape of project files, so that one waits for
    the writer's click (docs/TEMPLATES.md §8). Anything else — pip, zip,
    local edits, a failed pull — is hands off.
    """
    import subprocess
    if numeric_version(latest)[:1] != numeric_version(current)[:1]:
        return False
    toplevel = git_toplevel()
    if toplevel is None:
        return False
    state = subprocess.run(
        ["git", "-C", str(toplevel), "status", "--porcelain"],
        capture_output=True, text=True, timeout=10)
    if state.stdout.strip():
        return False
    pulled = subprocess.run(
        ["git", "-C", str(toplevel), "pull", "--ff-only"],
        capture_output=True, text=True, timeout=60)
    return pulled.returncode == 0


def update_check_due() -> bool:
    """Return True if an automatic update check may run now (at most once a day)."""
    if update_mode() != "check":
        return False
    last = str(QSettings().value("update/lastCheck", "", type=str))
    try:
        when = datetime.fromisoformat(last)
    except ValueError:
        return True
    return datetime.now() - when >= UPDATE_CHECK_INTERVAL


def tidy_path(path: Path | str) -> str:
    """A path as the writer would say it: the home folder is "~"."""
    return str(path).replace(str(Path.home()), "~")


def diagnostics(project=None) -> str:
    """What a bug report needs first, and nothing of the book.

    Version, machine, install kind and the settings folder, pasted into
    a GitHub issue. It carries no manuscript text and no project path —
    which has the writer's name in it as often as not — and the home
    folder is shortened to "~" wherever it appears.
    """
    import platform
    import shutil
    from PySide6 import __version__ as pyside_version
    from PySide6.QtCore import qVersion

    checkout = git_toplevel()
    config = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppConfigLocation)
    libreoffice = shutil.which("soffice") or shutil.which("libreoffice")
    lines = [
        f"Lekhika {__version__}",
        ("Install: git checkout at " + tidy_path(checkout)) if checkout
        else "Install: package (pip, pipx or zip)",
        f"Python {platform.python_version()} · PySide6 {pyside_version} "
        f"· Qt {qVersion()}",
        f"System: {platform.platform()} · {QGuiApplication.platformName()}",
        f"PDF: {'LibreOffice found' if libreoffice else 'no LibreOffice'}",
        f"Settings: {tidy_path(config)}",
    ]
    if project is not None:
        try:
            lines.append(f"Project: {len(project.documents())} documents")
        except Exception:
            pass
    return "\n".join(lines)


def _link_row(text: str, url: str, tooltip: str) -> QWidget:
    """A link styled as a line — the shape every pointer out of the
    dialog takes (the blog, GitHub, the issue tracker)."""
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    link = QPushButton(text)
    link.setFlat(True)
    link.setToolTip(tooltip)
    link.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(url)))
    layout.addWidget(link)
    layout.addStretch()
    return row


def _help(text: str) -> QToolButton:
    """Level-1 help (docs/TEMPLATES.md §7): a help button — the hover is
    the explanation, one line per control. A plain "?" label gets read
    as text; a button reads as the thing that teaches."""
    button = QToolButton()
    button.setText("?")
    button.setToolTip(text)
    button.setCheckable(False)
    button.setStyleSheet("font-weight: bold;")
    return button


# The Editor page's styles box: one dropdown, one editing surface. The
# special entry after the actual styles opens full CSS editing.
CSS_EDIT = "__css_edit__"


def _row(label_text: str, control, help_text: str):
    """One line: label, "?", and the control in a two-column form row."""
    label_row = QHBoxLayout()
    label_row.addWidget(QLabel(label_text))
    label_row.addWidget(_help(help_text))
    label_row.addStretch()
    container = QWidget()
    container.setLayout(label_row)
    return container, control


class PreferencesDialog(QDialog):
    def __init__(self, font_size: int, paste: dict[str, bool],
                 revision_author: str = "Author",
                 ai_settings: "ai.AISettings | None" = None,
                 tts_settings: "tts.TTSSettings | None" = None,
                 tts_voices: list[str] | None = None,
                 project_root: Path | None = None,
                 styles: dict | None = None,
                 default_css_text: str = "",
                 apply_css: Callable[[str], bool] | None = None,
                 style_names: Callable[[], list[str]] | None = None,
                 delete_style: Callable[[str, bool], bool] | None = None,
                 style_usage: Callable[[str], tuple[int, int]] | None = None,
                 save_style: Callable[[str, dict], None] | None = None,
                 project=None,
                 open_guide: Callable[[], None] | None = None,
                 show_placeholders: Callable[[], None] | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Preferences")
        store = QSettings()
        self.project = project
        self.open_guide = open_guide
        self.project_root = project_root if project_root is not None else (getattr(project, 'root', None) if project else None)
        self.styles = styles
        self.default_css_text = default_css_text
        self.apply_css = apply_css
        self.style_names = style_names
        self.delete_style = delete_style
        self.style_usage = style_usage
        self.save_style = save_style
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        # Cap the dialog: without this the widest page (AI/Updates long
        # help) stretches the window to the screen and buries the
        # category list in dark emptiness.
        self.resize(860, 540)
        self.setMinimumSize(720, 440)
        self.setMaximumSize(980, 680)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Filter categories")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.textChanged.connect(self._filter_categories)
        outer.addWidget(self.search_box)

        body = QHBoxLayout()
        outer.addLayout(body, 1)

        self.categories = QListWidget()
        self.categories.setFixedWidth(170)
        self.categories.setMovement(QListWidget.Static)
        self.categories.currentRowChanged.connect(self._show_category)
        self.pages = QStackedWidget()
        body.addWidget(self.categories)
        body.addWidget(self.pages, 1)

        # General: the settings that are about the writer, not a feature —
        # who they are, and whether the optional machinery below is wanted
        # at all. Optional features keep their own categories, shown while
        # (and only while) their box is ticked.
        general = self._page("General")
        general_form = QFormLayout(general)
        self.author_edit = QLineEdit(revision_author)
        self.author_edit.setMaxLength(80)
        general_form.addRow(*_row(
            "Author:", self.author_edit,
            "Author for all documents in this project. To override it, "
            "set the \"Author\" metadata on the document."))
        self.spell_check_box = QCheckBox("Spell-check squiggles in the editor")
        self.spell_check_box.setChecked(
            bool(store.value("ui/spellSquiggles", False, type=bool)))
        general_form.addRow(*_row(
            "Spell-check", self.spell_check_box,
            "Red wavy underlines under words the system dictionary "
            "rejects. Needs aspell or hunspell installed; View → "
            "Spell-check Squiggles toggles the same thing while writing."))
        self.ai_enabled_box = QCheckBox("AI integration")
        self.ai_enabled_box.setChecked(
            bool(store.value("ai/enabled", False, type=bool)))
        general_form.addRow(*_row(
            "AI integration", self.ai_enabled_box,
            "The AI menu — continuing scenes and proofreading with a "
            "model. Off by default; tick it to open the AI category."))
        self.tts_enabled_box = QCheckBox("Text to speech")
        self.tts_enabled_box.setChecked(
            bool(store.value("tts/enabled", False, type=bool)))
        general_form.addRow(*_row(
            "Text to speech", self.tts_enabled_box,
            "Read Aloud, in a system voice. Off by default; tick it to "
            "open the Read aloud category."))
        self._add_category("General", general)

        # Editor: styles and the rest of the writing surface — reserved
        # and empty until there is something to put in it.
        editor_page = self._page("Editor")
        editor_form = QFormLayout(editor_page)
        style_row = QHBoxLayout()
        self.style_box = QComboBox()
        edit_button = QPushButton("Edit…")
        edit_button.clicked.connect(self._edit_style)
        delete_button = QPushButton("Delete…")
        delete_button.clicked.connect(self._delete_style)
        style_row.addWidget(self.style_box, 1)
        style_row.addWidget(edit_button)
        style_row.addWidget(delete_button)
        style_field = QWidget()
        style_field.setLayout(style_row)
        editor_form.addRow(*_row(
            "Styles", style_field,
            "Paragraph styles live in the editor: select styled text for "
            "Create Style, or use “Use as Default” to make it the "
            "default. A deliberately simple start usually rests in "
            "Edit — the CSS stays behind the text file below."))
        delete_note = QLabel(
            "Deleting removes the shortcut only; tick the box to also "
            "return dressed text to Default. If you're not sure a style "
            "is unused, leave it in.")
        delete_note.setWordWrap(True)
        editor_form.addRow(delete_note)
        self._refresh_delete_box()
        self.style_box.setCurrentIndex(
            max(0, self.style_box.findData("Default")))
        # Connected only now: the box starts on the CSS entry during its
        # first rebuild, and opening the popup from construction would
        # hang the dialog before it is even shown.
        self.style_box.currentTextChanged.connect(self._style_box_changed)
        self._add_category("Editor", editor_page)

        # Dictionary: enabled while spell-check is ticked — system
        # dictionary language, word-list home, and the harvest button.
        dictionary_page = self._page("Dictionary")
        dictionary_layout = QVBoxLayout(dictionary_page)
        from PySide6.QtCore import QLocale
        self.language_combo = QComboBox()
        self.language_combo.setEditable(True)
        system_lang = QLocale.system().name().split("_")[0]
        stored_lang = str(store.value("spell/language", "", type=str)).strip()
        self.language_combo.addItem(f"System default ({system_lang})", "")
        for code, label in (("en", "English (en)"), ("de", "German (de)"),
                            ("es", "Spanish (es)"), ("fr", "French (fr)"),
                            ("it", "Italian (it)"), ("nl", "Dutch (nl)"),
                            ("ru", "Russian (ru)"), ("ar", "Arabic (ar)"),
                            ("he", "Hebrew (he)"), ("hi", "Hindi (hi)")):
            self.language_combo.addItem(label, code)
        if stored_lang:
            if self.language_combo.findData(stored_lang) >= 0:
                self.language_combo.setCurrentIndex(
                    self.language_combo.findData(stored_lang))
            else:
                self.language_combo.addItem(stored_lang, stored_lang)
                self.language_combo.setCurrentIndex(
                    self.language_combo.count() - 1)
        lang_row = QHBoxLayout()
        lang_row.addWidget(QLabel("System dictionary"))
        lang_row.addWidget(_help(
            "The aspell/hunspell language Lekhika checks against — "
            "one choice for the whole install (aspell first for nuance, "
            "hunspell as fallback). The project word list below is added "
            "on top of this, whatever language is chosen."))
        lang_row.addWidget(self.language_combo, 1)
        dictionary_layout.addLayout(lang_row)
        self.dictionary_edit = QLineEdit(
            store.value("spell/dictionary", "", type=str))
        self.dictionary_edit.setPlaceholderText(
            "dictionary.txt at the project root (default)")
        dictionary_browse = QPushButton("Browse…")
        dictionary_browse.setToolTip(
            "Pick the word list file. Empty means dictionary.txt at the "
            "project root.")
        dictionary_browse.clicked.connect(
            lambda: self.dictionary_edit.setText(QFileDialog.getOpenFileName(
                self, "Choose word list file", self.dictionary_edit.text()
                or ".", "Word lists (*.txt)")[0]))
        dictionary_edit_button = QPushButton("Edit…")
        dictionary_edit_button.setToolTip(
            "Edit the same file you chose in the word list field above. "
            "If you didn't select a separate file, a dictionary.txt is "
            "created at the root of the project folder when you add words "
            "here.")
        dictionary_edit_button.clicked.connect(self._edit_word_list)
        dictionary_choice = QHBoxLayout()
        dictionary_choice.addWidget(QLabel("Word list"))
        dictionary_choice.addWidget(_help(
            "Use this to add words unique to your project, so the "
            "dictionary recognizes their spellings and will only flag "
            "if they are spelled wrong."))
        dictionary_choice.addWidget(self.dictionary_edit, 1)
        dictionary_choice.addWidget(dictionary_browse)
        dictionary_choice.addWidget(dictionary_edit_button)
        dictionary_layout.addLayout(dictionary_choice)
        self.word_list_path_label = QLabel()
        dictionary_layout.addWidget(self.word_list_path_label)
        self.word_list_count_label = QLabel()
        dictionary_layout.addWidget(self.word_list_count_label)
        dictionary_layout.addWidget(QLabel(
            "One word per line. Saved when the editor closes."))
        rtl_note = QLabel(
            "Note: right-to-left (Arabic/Hebrew) text renders, but "
            "paragraph RTL direction control is coming soon. "
            "Spell-checking respects the system language as installed.")
        rtl_note.setWordWrap(True)
        dictionary_layout.addWidget(rtl_note)
        harvest_row = QHBoxLayout()
        harvest_row.addWidget(QLabel("Project names"))
        harvest_row.addWidget(_help(
            "Scans titles, synopses, metadata and prose for capitalised "
            "words the dictionary doesn't know, and lets you tick the "
            "ones that belong there into dictionary.txt. One pass, no AI."))
        scan_button = QPushButton("Scan this project for in-world terms and names to add to the dictionary")
        scan_button.clicked.connect(self._scan_entities)
        harvest_row.addWidget(scan_button)
        harvest_row.addStretch()
        dictionary_layout.addLayout(harvest_row)
        dictionary_layout.addStretch()
        self.dictionary_category = self._add_category(
            "Dictionary", dictionary_page)
        self._refresh_word_list_summary()
        self.spell_check_box.toggled.connect(self._spell_check_toggled)

        self.size_slider = QSlider(Qt.Horizontal)
        self.size_slider.setRange(8, 24)
        self.size_slider.setValue(font_size)
        self.size_label = QLabel(f"{font_size} pt")
        self.size_sample = QLabel(
            "The quick brown fox — menus, binder, corkboard.\n"
            "This doesn't alter the font in your document, only your "
            "view of the software.")
        self.size_sample.setWordWrap(True)
        self.size_slider.valueChanged.connect(self._update_size_sample)
        self._update_size_sample(font_size)
        appearance = self._page("Appearance")
        appearance_layout = QVBoxLayout(appearance)
        label_row = QHBoxLayout()
        label_row.addWidget(QLabel("Interface font size"))
        label_row.addWidget(_help(
            "Menus, binder, corkboard, outliner and panels — this "
            "installation's chrome. The writing editor keeps its own "
            "12 pt size and zoom (View → Zoom), deliberately: a second "
            "place to change type size is a second thing to get wrong."))
        label_row.addStretch()
        appearance_layout.addLayout(label_row)
        slider_row = QHBoxLayout()
        slider_row.addWidget(self.size_slider, 1)
        slider_row.addWidget(self.size_label)
        appearance_layout.addLayout(slider_row)
        appearance_layout.addWidget(self.size_sample)
        # Theme (deliberately small — see MODEL.md): system keeps the
        # desktop's palette so light/dark desktop always works; the rest
        # are fixed QPalettes Lekhika owns.
        theme_row = QHBoxLayout()
        theme_row.addWidget(QLabel("Theme"))
        theme_row.addWidget(_help(
            "System follows your desktop light/dark. The named themes are "
            "fixed palettes — same accent shapes, different backgrounds. "
            "If none of them suit you, complain on GitHub and we will "
            "figure it out with you."))
        self.theme_box = QComboBox()
        for label, value in (("System (desktop)", "system"),
                             ("Light", "light"),
                             ("Rose (dusky red)", "light-accents"),
                             ("Earth (ochre/olive)", "earth"),
                             ("Dark (grey sky)", "dark"),
                             ("Night rose", "dark-accents")):
            self.theme_box.addItem(label, value)
        stored_theme = str(store.value("ui/theme", "system", type=str))
        idx = self.theme_box.findData(stored_theme)
        self.theme_box.setCurrentIndex(max(0, idx))
        theme_row.addWidget(self.theme_box, 1)
        appearance_layout.addLayout(theme_row)
        appearance_layout.addStretch()
        self._add_category("Appearance", appearance)

        files_page = self._page("Files && saving")
        files_form = QFormLayout(files_page)
        self.autosave_spin = QSpinBox()
        self.autosave_spin.setRange(100, 60000)
        self.autosave_spin.setSingleStep(100)
        self.autosave_spin.setSuffix(" ms")
        self.autosave_spin.setValue(int(store.value(
            "editor/autosaveMs", _AUTOSAVE_DEFAULT_MS, type=int)))
        files_form.addRow(*_row(
            "Autosave delay", self.autosave_spin,
            "How long typing must pause before the document is written "
            "to disk. Shorter is safer if the machine crashes; longer "
            "avoids disk chatter mid-sentence. Takes effect at once."))
        self.reopen_last_box = QCheckBox(
            "Reopen the last project on start")
        self.reopen_last_box.setChecked(
            bool(store.value("session/reopenLast", True, type=bool)))
        files_form.addRow(*_row(
            "On start", self.reopen_last_box,
            "Uncheck for a fresh empty window every launch, whatever "
            "was open last."))
        self.backup_frequency_box = QComboBox()
        self.backup_frequency_box.addItem(
            "Only when I ask (Ctrl+S) and when closing", "on-close")
        self.backup_frequency_box.addItem("Every hour", "hourly")
        self.backup_frequency_box.addItem("Every day", "daily")
        self.backup_frequency_box.addItem("Every week", "weekly")
        self.backup_frequency_box.addItem("On every save", "every-save")
        self.backup_frequency_box.addItem("Never automatically", "never")
        current = store.value("files/backupFrequency", "on-close", type=str)
        index = self.backup_frequency_box.findData(current)
        self.backup_frequency_box.setCurrentIndex(max(0, index))
        files_form.addRow(*_row(
            "Backups", self.backup_frequency_box,
            "A backup is a full copy of the project kept outside it. "
            "Writing is saved on its own; backups are extra copies. "
            "Ctrl+S saves and creates a backup when you ask for one. "
            "If you use Ctrl+S constantly, a less frequent automatic "
            "schedule (such as on close, daily or weekly) avoids filling "
            "your backup folder with many near-identical copies."))
        self.backup_folder_edit = QLineEdit(
            store.value("files/backupFolder", "", type=str))
        self.backup_folder_edit.setPlaceholderText(
            "Beside the project (default)")
        # A chosen folder has to be undoable: empty is the default.
        self.backup_folder_edit.setClearButtonEnabled(True)
        # A folder is chosen, not typed: the path is long, and a typo
        # would only show up as a backup in the wrong place (or none).
        folder_row = QWidget()
        folder_layout = QHBoxLayout(folder_row)
        folder_layout.setContentsMargins(0, 0, 0, 0)
        folder_layout.addWidget(self.backup_folder_edit, 1)
        browse = QPushButton("Choose...")
        browse.clicked.connect(self._choose_backup_folder)
        folder_layout.addWidget(browse)
        files_form.addRow(*_row(
            "Backup folder", folder_row,
            "Where the copies go. Leave it empty to keep a folder "
            "beside the project; name a folder (a Dropbox or other "
            "synced place, say) to keep them there instead. A project "
            "may override this for itself."))
        backups_note = QLabel(
            "The folder and how many backups to keep for one manuscript "
            "live with the book (File → Choose Backup Folder); these "
            "are the defaults for new projects.")
        backups_note.setWordWrap(True)
        files_form.addRow(backups_note)
        self._add_category("Files & saving", files_page)

        paste_page = self._page("Pasting")
        paste_layout = QVBoxLayout(paste_page)
        keep_row = QHBoxLayout()
        keep_row.addWidget(QLabel("When pasting, keep:"))
        keep_row.addWidget(_help(
            "Unchecked items are stripped when you paste. Ctrl+Shift+V "
            "always pastes as plain text, and Edit → Paste and Keep "
            "All Formatting ignores every box."))
        keep_row.addStretch()
        paste_layout.addLayout(keep_row)
        self.paste_fonts = QCheckBox("Fonts and colours")
        self.paste_paragraphs = QCheckBox(
            "Paragraph formatting — indents, line spacing, margins, "
            "blank-line structure")
        self.paste_inline = QCheckBox("Bold, italics and underline")
        self.paste_alignment = QCheckBox("Alignment")
        self.paste_fonts.setChecked(paste["fonts"])
        self.paste_paragraphs.setChecked(paste["paragraphs"])
        self.paste_inline.setChecked(paste["inline"])
        self.paste_alignment.setChecked(paste["alignment"])
        for box in (self.paste_fonts, self.paste_paragraphs,
                    self.paste_inline, self.paste_alignment):
            paste_layout.addWidget(box)
        paste_layout.addStretch()
        self._add_category("Pasting", paste_page)

        links_page = self._page("Links")
        links_form = QFormLayout(links_page)
        self.link_default_box = QComboBox()
        for label, value in LINK_POLICY_CHOICES:
            self.link_default_box.addItem(label, value)
        self.link_default_box.setCurrentIndex(max(
            0, self.link_default_box.findData(
                str(store.value("link_policy/default", DEFAULT_LINK_POLICY)))))
        links_form.addRow(*_row(
            "Unpublished links", self.link_default_box,
            "What a [[link]] pointing outside the current export "
            "becomes. This is the default for new work; each book can "
            "override it in the inspector, and the book's setting wins."))
        self.related_box = QCheckBox("Show the Mentions / Mentioned by scan")
        self.related_box.setChecked(
            bool(store.value("related/enabled", True, type=bool)))
        links_form.addRow(*_row(
            "Related files", self.related_box,
            "The inspector's auto-link scan: documents whose titles this "
            "one's prose names, and documents naming this one. Helpful in "
            "some projects, noise in others — switch it off and the "
            "section disappears entirely."))
        self._add_category("Links", links_page)

        placeholders_page = self._page("Placeholders")
        placeholders_layout = QVBoxLayout(placeholders_page)
        what = QLabel(
            "A placeholder is written ${Name}. It stays exactly as typed in "
            "the editor and in the saved file, and an export replaces it — "
            "in a template and in the manuscript alike. The names below are "
            "the project's own: a key added here answers in every document "
            "of the project, unless a document, or a folder above it, sets "
            "the same name.")
        what.setWordWrap(True)
        placeholders_layout.addWidget(what)

        built_in_caption = QLabel("Built in — the defaults, in every export")
        placeholders_layout.addWidget(built_in_caption)
        # Names only: what each one means is the reference page's job, and
        # a second copy of that prose is a second thing to drift.
        self.builtin_placeholders = QListWidget()
        self.builtin_placeholders.setWordWrap(True)
        self.builtin_placeholders.setMaximumHeight(150)
        for built_in, _meaning in BUILTIN_PLACEHOLDERS:
            self.builtin_placeholders.addItem(f"${{{built_in}}}")
        placeholders_layout.addWidget(self.builtin_placeholders)

        own_caption = QLabel("Your own — project-wide")
        placeholders_layout.addWidget(own_caption)
        self.project_placeholders = QListWidget()
        self.project_placeholders.setMaximumHeight(110)
        placeholders_layout.addWidget(self.project_placeholders)

        add_row = QHBoxLayout()
        self.placeholder_name_edit = QLineEdit()
        self.placeholder_name_edit.setPlaceholderText("Name")
        self.placeholder_value_edit = QLineEdit()
        self.placeholder_value_edit.setPlaceholderText("Value")
        add_row.addWidget(self.placeholder_name_edit, 1)
        add_row.addWidget(self.placeholder_value_edit, 2)
        self.placeholder_add_button = QPushButton("Add")
        self.placeholder_add_button.clicked.connect(self._add_placeholder)
        add_row.addWidget(self.placeholder_add_button)
        self.placeholder_remove_button = QPushButton("Remove")
        self.placeholder_remove_button.clicked.connect(self._remove_placeholder)
        add_row.addWidget(self.placeholder_remove_button)
        references = QPushButton("Every placeholder…")
        references.setToolTip(
            "The read-only reference: every name this project can fill, "
            "built-in, project-wide, and by the Type that carries it.")
        self.placeholder_reference_button = references
        references.setEnabled(show_placeholders is not None
                              and self.project is not None)
        references.clicked.connect(
            lambda: show_placeholders and show_placeholders())
        add_row.addWidget(references)
        placeholders_layout.addLayout(add_row)

        self.placeholder_note = QLabel(
            "No project is open, so there is nothing to add: placeholders "
            "belong to a project, and a project's keys travel with it.")
        self.placeholder_note.setWordWrap(True)
        self.placeholder_note.setVisible(self.project is None)
        placeholders_layout.addWidget(self.placeholder_note)
        placeholders_layout.addStretch()
        for control in (self.placeholder_add_button,
                        self.placeholder_remove_button):
            control.setEnabled(self.project is not None)
        self._refresh_placeholders()
        self._add_category("Placeholders", placeholders_page)


        active_ai = ai_settings or ai.load_settings()
        ai_page = self._page("AI")
        ai_layout = QVBoxLayout(ai_page)
        # The contract with the writer, on the page itself — not behind
        # a hover: the AI never writes the manuscript directly.
        note = QLabel(
            "Regardless of the settings on this page, at no point does "
            "the AI edit your text directly. All its replies are added "
            "as tracked changes, for you to accept or reject.")
        note.setWordWrap(True)
        ai_layout.addWidget(note)
        ai_form = QFormLayout()
        self.ai_server_box = QComboBox()
        # The handler reads the endpoint and model boxes, so it is
        # connected at the end of __init__ with the rest that need the
        # page built: seeding the box during construction used to raise
        # AttributeError before the page existed.
        self._populate_ai_servers()
        ai_form.addRow("Server:", self.ai_server_box)
        ai_form.addRow(self._ai_server_add_row())
        self.ai_endpoint_edit = QLineEdit(active_ai.endpoint)
        ai_form.addRow("Endpoint:", self.ai_endpoint_edit)
        self.ai_model_box = QComboBox()
        self.ai_model_box.setEditable(True)
        self.ai_model_box.setPlaceholderText("e.g. qwen2.5:7b")
        self.ai_model_edit = self.ai_model_box.lineEdit()
        if active_ai.model:
            self.ai_model_box.setEditText(active_ai.model)
        self._populate_ai_models()
        ai_form.addRow("Model:", self.ai_model_box)
        self.ai_key_edit = QLineEdit(active_ai.key)
        self.ai_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.ai_key_edit.setPlaceholderText("empty for local servers")
        ai_form.addRow("API key:", self.ai_key_edit)
        ai_layout.addLayout(ai_form)
        detect_row = QHBoxLayout()
        detect = QPushButton("Detect local servers")
        detect.clicked.connect(self._detect_local_servers)
        install = QPushButton("Install Ollama + phi4-mini…")
        install.clicked.connect(self._install_ollama)
        detect_row.addWidget(detect)
        detect_row.addWidget(install)
        detect_row.addStretch()
        ai_layout.addLayout(detect_row)
        # One short line in the page; the long explanation lives behind
        # the "?" (docs/TEMPLATES.md §7), like every control in this dialog.
        help_row = QHBoxLayout()
        help_row.addWidget(QLabel(
            "Small local models are not very powerful and ideally should "
            "not be used beyond proof reading. If you use one for "
            "anything else, know the risks and check its suggested edits "
            "carefully."))
        help_row.addWidget(_help(
            "Used by AI → Continue Scene and Proofread Selection. The "
            "answer is always applied as a tracked change, never written "
            "into the manuscript directly — and proofreading asks for "
            "minimal edits: bloopers, not prose.\n"
            "Local model (nothing leaves your computer): install Ollama, "
            "then  ollama pull <model-name>  (e.g. ollama pull qwen2.5:7b). "
            "The default endpoint http://localhost:11434/v1 already "
            "matches Ollama.\n"
            "Read this before you rely on it: a model that fits an "
            "ordinary computer is a very small model, and small models "
            "are error-prone — they manage typos and simple bloopers, "
            "not unsupervised work, and prompt wording will need tuning."))
        help_row.addStretch()
        ai_layout.addLayout(help_row)
        self.prompt_toggle = QPushButton("Proofreading prompt — advanced")
        self.prompt_toggle.setCheckable(True)
        self.prompt_toggle.setStyleSheet("text-align: left;")
        self.prompt_toggle.toggled.connect(self._toggle_prompt_editor)
        ai_layout.addWidget(self.prompt_toggle)
        prompt_warning = QLabel(
            "Do not edit unless you know what you are doing: this text "
            "is the contract with the model — bloopers, minimal edits, "
            "never a rewrite. Widening it invites the model to polish "
            "your prose behind your back.")
        prompt_warning.setWordWrap(True)
        prompt_warning.setVisible(False)
        ai_layout.addWidget(prompt_warning)
        self.prompt_warning = prompt_warning
        self.ai_prompt_edit = QTextEdit()
        self.ai_prompt_edit.setPlainText(
            active_ai.prompt or ai.PROOFREAD_SYSTEM)
        self.ai_prompt_edit.setAcceptRichText(False)
        self.ai_prompt_edit.setVisible(False)
        self.ai_prompt_edit.setMinimumHeight(120)
        ai_layout.addWidget(self.ai_prompt_edit)
        ai_layout.addStretch()
        self.ai_category = self._add_category("AI", ai_page)

        active_tts = tts_settings or tts.load_settings()
        tts_page = self._page("Read aloud")
        tts_form = QFormLayout(tts_page)

        from lekhika import tts as _tts
        engines = _tts.available_system_engines()
        if engines:
            status = (f"System speech detected ({engines[0]}). It "
                      "works with nothing installed.")
        else:
            status = ("No speech engine found on this system — the "
                      "options below can fill the gap.")
        status_row = QLabel(status)
        status_row.setWordWrap(True)
        tts_form.addRow(*_row(
            "On this system", status_row,
            "What this installation of Lekhika can speak with, "
            "checked where Lekhika runs. Never baked in from another machine."))

        self.tts_source_buttons = {}
        tts_source_group = QButtonGroup(self)
        for value, label, hint in (
                ("system", "System speech", "The operating system's own voices."),
                ("piper", "Piper (offline)", "A small neural voice you download."),
                ("api", "External API", "A TTS service you configured yourself.")):
            radio = QRadioButton(label)
            radio.setToolTip(hint)
            radio.setProperty("source", value)
            tts_source_group.addButton(radio)
            self.tts_source_buttons[value] = radio
        if active_tts.source in self.tts_source_buttons:
            self.tts_source_buttons[active_tts.source].setChecked(True)
        else:
            self.tts_source_buttons["system"].setChecked(True)
        tts_source_widget = QWidget()
        source_layout = QVBoxLayout(tts_source_widget)
        for radio in self.tts_source_buttons.values():
            source_layout.addWidget(radio)
        self._tts_source_group = tts_source_group
        tts_form.addRow(*_row(
            "Speech source", tts_source_widget,
            "Where the voice comes from. System speech needs nothing "
            "installed; Piper is a light offline download; an external "
            "API is for services you have set up yourself."))

        self.tts_voice_box = QComboBox()
        for name in (tts_voices or []):
            self.tts_voice_box.addItem(name)
        if active_tts.voice:
            index = self.tts_voice_box.findText(active_tts.voice)
            if index >= 0:
                self.tts_voice_box.setCurrentIndex(index)
        tts_form.addRow(*_row(
            "Voice", self.tts_voice_box,
            "Speak → Read Aloud reads the selection, else the whole "
            "scene, in this voice. The list is whatever this system "
            "offers; an empty list means no speech engine is installed."))

        self.tts_rate_slider = QSlider(Qt.Horizontal)
        self.tts_rate_slider.setRange(-100, 100)
        self.tts_rate_slider.setValue(
            round(max(-1.0, min(1.0, active_tts.rate)) * 100))
        self.tts_rate_label = QLabel()
        self._update_rate_label()
        self.tts_rate_slider.valueChanged.connect(self._update_rate_label)
        tts_form.addRow(*_row(
            "Rate", self.tts_rate_slider,
            "Left is slower, right is faster; ×1.00 is the voice's "
            "normal pace."))
        tts_form.addRow(self.tts_rate_label)

        import sys as _sys
        piper_supported = _sys.platform == "linux"
        self.piper_binary_edit = QLineEdit(active_tts.piper_binary)
        self.piper_binary_edit.setPlaceholderText(
            "Binary from the one-click install, or your own Piper")
        piper_browse = QPushButton("Browse…")
        piper_browse.clicked.connect(
            lambda: self.piper_binary_edit.setText(QFileDialog.getOpenFileName(
                self, "Choose Piper binary", self.piper_binary_edit.text()
                or ".", "Executables (*)")[0]))
        piper_row = QHBoxLayout()
        piper_row.addWidget(self.piper_binary_edit, 1)
        piper_row.addWidget(piper_browse)
        piper_field = QWidget()
        piper_field.setLayout(piper_row)
        tts_form.addRow(*_row(
            "Piper binary", piper_field,
            "Piper is a small offline neural voice system — a few dozen "
            "megabytes, and nothing leaves the machine. The official "
            "releases are Linux-only; on Windows or macOS, pip install "
            "piper-tts gives the same engine and you point this there."))
        self.piper_voice_edit = QLineEdit(active_tts.piper_voice)
        self.piper_voice_edit.setPlaceholderText("Voice model file (.onnx)")
        piper_voice_browse = QPushButton("Browse…")
        piper_voice_browse.clicked.connect(
            lambda: self.piper_voice_edit.setText(QFileDialog.getOpenFileName(
                self, "Choose Piper voice", self.piper_voice_edit.text()
                or ".", "Voice models (*.onnx)")[0]))
        piper_voice_row = QHBoxLayout()
        piper_voice_row.addWidget(self.piper_voice_edit, 1)
        piper_voice_row.addWidget(piper_voice_browse)
        piper_voice_field = QWidget()
        piper_voice_field.setLayout(piper_voice_row)
        tts_form.addRow(*_row(
            "Piper voice", piper_voice_field,
            "The voice model to speak with — downloads with the "
            "one-click install, or your own download. Voices live at "
            "huggingface.co/rhasspy/piper-voices."))
        install_button = QPushButton(
            "Install Piper (one click)"
            if piper_supported
            else "Install Piper with pip (one click)")
        install_button.setToolTip(
            "Downloads and installs Piper and one good small voice. "
            "On Linux a release binary is used; everywhere else it "
            "installs the piper-tts package. Nothing leaves the machine "
            "afterwards, and more voices live at "
            "huggingface.co/rhasspy/piper-voices.")
        install_button.clicked.connect(self._install_piper_and_use)
        tts_form.addRow("Piper install", install_button)
        self.piper_status = QLabel()
        self.piper_status.setWordWrap(True)
        tts_form.addRow(self.piper_status)

        self.api_endpoint_edit = QLineEdit(active_tts.api_endpoint)
        self.api_endpoint_edit.setPlaceholderText(
            "https://api.example.com/v1/audio/speech")
        api_endpoint_field = QWidget()
        api_endpoint_field.setLayout(QHBoxLayout())
        api_endpoint_field.layout().addWidget(self.api_endpoint_edit)
        tts_form.addRow(*_row(
            "API endpoint", api_endpoint_field,
            "An HTTP endpoint that accepts a JSON speech request "
            "(OpenAI-shaped: model and voice in, audio bytes out) and "
            "returns audio. A free tier at low volume works as well "
            "as any paid one for reading drafts aloud — more on my "
            "blog, linked from the bottom."))
        self.api_model_edit = QLineEdit(active_tts.api_model)
        self.api_model_edit.setPlaceholderText("tts-1")
        tts_form.addRow(*_row("API model", self.api_model_edit,
                              "The service's own model name. Empty "
                              "means the service's usual default."))
        self.api_key_edit = QLineEdit(active_tts.api_key)
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_edit.setPlaceholderText("Not stored in the project")
        tts_form.addRow(*_row("API key", self.api_key_edit,
                              "Used only to read aloud, and only from here."))
        self.api_voice_edit = QLineEdit(active_tts.api_voice)
        self.api_voice_edit.setPlaceholderText("alloy")
        tts_form.addRow(*_row(
            "API voice", self.api_voice_edit,
            "The service's own voice name. Empty means its default."))

        test_button = QPushButton("Hear a sample")
        test_button.setToolTip(
            "Speak “The quick brown fox jumps over the lazy dog.” in the "
            "current source, so the choice can be heard before it's kept.")
        test_button.clicked.connect(self._tts_test)
        tts_form.addRow("Try it", test_button)
        tts_form.addWidget(self._blog_row())
        self.tts_category = self._add_category("Read aloud", tts_page)
        self._update_tts_controls()
        for button in self.tts_source_buttons.values():
            button.toggled.connect(self._update_tts_controls)

        updates_page = self._page("Updates")
        updates_layout = QVBoxLayout(updates_page)
        update_form = QFormLayout()
        self.update_mode_box = QComboBox()
        for label, value in UPDATE_MODES:
            self.update_mode_box.addItem(label, value)
        self.update_mode_box.setCurrentIndex(max(
            0, self.update_mode_box.findData(update_mode())))
        update_form.addRow(*_row(
            "Updates", self.update_mode_box,
            "Check automatically to look for new versions once a day and "
            "show when an update is available (with a download link for "
            "packaged installs). Git checkouts can install updates in place "
            "from this page when possible. Choose manual to check only when "
            "you ask, or never to make no update checks."))
        self.update_status = QLabel("No check yet.")
        self.update_mode_box.currentIndexChanged.connect(
            self._update_update_controls)
        self.update_status.setWordWrap(True)
        update_form.addRow("Status", self.update_status)
        self.update_check_button = QPushButton("Check now")
        self.update_button = QPushButton("Update now")
        self.update_button.setEnabled(False)
        self.update_button.clicked.connect(self._run_update)
        self._update_update_controls()
        row = QHBoxLayout()
        row.addWidget(self.update_check_button)
        row.addWidget(self.update_button)
        row.addStretch()
        updates_layout.addLayout(update_form)
        recommendation = QLabel(
            "Lekhika is young, so a new version can carry a fix you need. "
            "Automatic checks let you know when an update is available.")
        recommendation.setWordWrap(True)
        updates_layout.addWidget(recommendation)
        updates_layout.addLayout(row)
        updates_layout.addStretch()
        self._add_category("Updates", updates_page)
        self.update_check_button.clicked.connect(self._check_updates)
        advanced_page = self._page("Help")
        help_layout = QVBoxLayout(advanced_page)
        about_form = QFormLayout()
        selectable = Qt.TextInteractionFlag.TextSelectableByMouse

        version = QLabel(f"Lekhika {__version__}")
        version.setTextInteractionFlags(selectable)
        about_form.addRow(*_row(
            "Version", version,
            "The version you are running. A bug report is much easier to "
            "act on when it names this."))

        checkout = git_toplevel()
        installed = QLabel(tidy_path(checkout) if checkout is not None
                           else "Installed as a package (pip, pipx or zip)")
        installed.setWordWrap(True)
        installed.setTextInteractionFlags(selectable)
        about_form.addRow(*_row(
            "Installed in", installed,
            "Where the program itself lives. Only a git checkout can be "
            "updated by pulling; a package install updates with its own "
            "command."))

        from lekhika import pdf as _pdf
        self.pdf_status_label = QLabel(_pdf.libreoffice_status_text())
        self.pdf_status_label.setWordWrap(True)
        about_form.addRow(*_row(
            "PDF export", self.pdf_status_label,
            _pdf.pdf_export_help()))

        config_dir = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppConfigLocation)
        folder_row = QWidget()
        folder_layout = QHBoxLayout(folder_row)
        folder_layout.setContentsMargins(0, 0, 0, 0)
        folder = QLabel(tidy_path(config_dir))
        folder.setWordWrap(True)
        folder.setTextInteractionFlags(selectable)
        open_config = QPushButton("Open")
        open_config.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(config_dir)))
        folder_layout.addWidget(folder, 1)
        folder_layout.addWidget(open_config)
        about_form.addRow(*_row(
            "Settings folder", folder_row,
            "Everything in this dialog is stored here. Copy that folder to "
            "move a setup to another machine."))
        help_layout.addLayout(about_form)

        copy_row = QHBoxLayout()
        self.copy_details_button = QPushButton("Copy system details")
        self.copy_details_button.clicked.connect(self._copy_diagnostics)
        copy_row.addWidget(self.copy_details_button)
        copy_row.addWidget(_help(
            "Copies the version, system, install kind and settings folder "
            "as text, ready to paste into a GitHub issue. It copies nothing "
            "from your manuscript and no project path — the home folder "
            "shows as ~."))
        copy_row.addStretch()
        help_layout.addLayout(copy_row)

        help_layout.addWidget(_link_row(
            "Report a problem — GitHub issues →",
            "https://github.com/Vidyut/lekhika/issues/new",
            "Opens a new issue. Press Copy system details first, paste "
            "that in, and say what you did, what happened, and what you "
            "expected."))
        help_layout.addWidget(_link_row(
            "Source code and releases — GitHub →",
            "https://github.com/Vidyut/lekhika",
            "The code, the tags, and the releases: version history and "
            "what changed between them."))
        help_layout.addWidget(_link_row(
            "Newest manual and notes — my blog →",
            "https://vidyut.net/",
            "The blog, where the newest version of the manual is posted "
            "once it is published, with notes that belong to no single "
            "version of the software."))
        manual = QLabel(
            "The manual ships with Lekhika, as a project of its own and "
            "read-only: nothing is written back into the install folder.")
        manual.setWordWrap(True)
        help_layout.addWidget(manual)
        self.open_manual_button = QPushButton("Open the Lekhika Guide")
        self.open_manual_button.setEnabled(self.open_guide is not None)
        self.open_manual_button.clicked.connect(
            lambda: self.open_guide and self.open_guide())
        help_layout.addWidget(self.open_manual_button,
                              alignment=Qt.AlignmentFlag.AlignLeft)
        licence = QLabel(
            "Lekhika is free software, under the GNU GPL version 3 or "
            "later, and comes with no warranty.")
        licence.setWordWrap(True)
        help_layout.addWidget(licence)
        help_layout.addStretch()
        self._add_category("Help", advanced_page)

        support = self._page("Support Lekhika")
        support_layout = QVBoxLayout(support)
        why = QLabel(
            "Lekhika is written by one person: no company behind it, no "
            "marketing budget. If it earns a place in your writing life, "
            "these are the ways to keep it going.")
        why.setWordWrap(True)
        support_layout.addWidget(why)
        support_layout.addWidget(_link_row(
            "Send a gift — PayPal →",
            "https://paypal.me/VidyutG",
            "A one-off gift, entirely optional: the software is free and "
            "open source whether you send one or not."))
        support_layout.addWidget(_link_row(
            "My books — Abrahamha's Strays →",
            "https://vidyut.net/category/books/",
            "The science fiction I write, set in a world of dorms, domes "
            "and immortal sisters. Buying or reviewing them is the other "
            "way this work gets paid for; both are on Kindle Unlimited, so "
            "reading them costs nothing extra if you subscribe."))
        support_layout.addWidget(_link_row(
            "The newsletter — subscribe →",
            "https://vidyut.net/newsletter-signup/",
            "Occasional notes on the writing, the books and this software: "
            "what changed and what is coming."))
        tell = QLabel(
            "Tell someone. There is no marketing budget here, so a "
            "recommendation goes further than anything else on this page: "
            "the software to a writer who would use it, and the books to "
            "someone who reads this kind of science fiction.")
        tell.setWordWrap(True)
        support_layout.addWidget(tell)
        support_layout.addWidget(_link_row(
            "Report a bug or ask for a feature →",
            "https://github.com/Vidyut/lekhika/issues/new",
            "Problems and requests collect in one place, so they can be "
            "fixed and built. Copy the system details from the Help page "
            "first, then say what you did, what happened, and what you "
            "expected."))
        support_layout.addStretch()
        self._add_category("Support Lekhika", support)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self.ai_enabled_box.toggled.connect(self._ai_check_toggled)
        self.tts_enabled_box.toggled.connect(self._tts_check_toggled)
        self.ai_server_box.currentIndexChanged.connect(self._ai_server_changed)
        self._update_feature_categories()
        self.categories.setCurrentRow(0)

    def _update_install_text(self) -> str:
        import sys
        if sys.platform == "win32":
            return "winget install --id Git.Git"
        if sys.platform == "darwin":
            return "brew install git  (or: xcode-select --install)"
        return "sudo apt install git  (Debian/Ubuntu)"

    def _check_updates(self):
        self.update_status.setText("Checking GitHub for the latest version…")
        QSettings().setValue("update/lastCheck", datetime.now().isoformat())
        try:
            found = newer_release()
        except Exception as error:
            self.update_status.setText(f"Check failed (offline?): {error}")
            self.update_button.setEnabled(False)
            return
        if found is None:
            self.update_status.setText(
                f"You're up to date (current {__version__}).")
            self.update_button.setEnabled(False)
            self.update_button.setText("Update now")
            return
        self._latest_release_page = found["page"]
        self._latest_download_url = found.get("download") or found["page"]
        if git_toplevel() is not None:
            self.update_status.setText(
                f"Update available: {found['current']} → {found['latest']}. "
                "Press Update now.")
            self.update_button.setText("Update now")
        else:
            self.update_status.setText(
                f"Update available: {found['current']} → {found['latest']}. "
                "Download the latest version.")
            self.update_button.setText("Download update")
        self.update_button.setEnabled(True)
        self.update_button.setToolTip(self._latest_download_url)

    def _update_update_controls(self) -> None:
        """The mode decides whether Lekhika may talk to the network.

        "Nothing" means nothing: the button that would go online is
        disabled, not merely discouraged.
        """
        never = self.update_mode_box.currentData() == "never"
        self.update_check_button.setEnabled(not never)
        if never:
            self.update_status.setText(
                "Checks are off — Lekhika will not go online.")
        elif self.update_status.text().startswith("Checks are off"):
            self.update_status.setText("No check yet.")

    def _run_update(self):
        import subprocess
        toplevel = git_toplevel()
        if toplevel is None:
            url = getattr(self, "_latest_download_url", None) or getattr(self, "_latest_release_page", None)
            if not url or not str(url).startswith("http"):
                url = f"https://github.com/{release_repo()}/releases"
            QDesktopServices.openUrl(QUrl(url))
            self.update_status.setText(
                "Download and install the latest version.")
            return
        status = subprocess.run(["git", "-C", str(toplevel), "status", "--porcelain"],
                                capture_output=True, text=True, timeout=10)
        if status.stdout.strip():
            QMessageBox.warning(self, "Dirty tree",
                "Your checkout has local edits — Lekhika will not stash or overwrite them. Commit, stash, or pull manually, then try again.")
            return
        result = subprocess.run(["git", "-C", str(toplevel), "pull", "--ff-only"],
                                capture_output=True, text=True, timeout=60)
        QMessageBox.information(self, "Update",
            (result.stdout + result.stderr).strip()[:2000] or "Pull finished — restart Lekhika.")

    def _refresh_placeholders(self) -> None:
        """Show the project-wide keys as the project stands.

        Read, not remembered: the inspector adds and removes the same
        fields, and the project is the truth (docs/STORAGE.md).
        """
        self.project_placeholders.clear()
        if self.project is None:
            return
        for key, entry in sorted(self.project.project_metadata().items()):
            item = QListWidgetItem(f"${{{key}}} — {entry[0] if entry else ''}")
            item.setData(Qt.UserRole, key)
            self.project_placeholders.addItem(item)

    def _add_placeholder(self) -> None:
        """Add one project-wide placeholder: a metadata key of the project.

        Written at once rather than on OK, so the reference button beside
        it shows the key it has just been given.
        """
        # The marker's own characters cannot be part of a name, or the
        # page would list a ${name} that can never be written and never
        # fill. Strip them as typed.
        name = (self.placeholder_name_edit.text().strip()
                .replace("$", "").replace("{", "").replace("}", ""))
        if self.project is None or not name:
            return
        entries = dict(self.project.project_metadata())
        # The value is the writer's; a type or an options list the same
        # key already carries in the inspector is left alone.
        _value, value_type, options = entries.get(name, ("", "text", []))
        entries[name] = (self.placeholder_value_edit.text().strip(),
                         value_type, options)
        self.project.set_project_metadata(entries)
        self.placeholder_name_edit.clear()
        self.placeholder_value_edit.clear()
        self._refresh_placeholders()

    def _remove_placeholder(self) -> None:
        row = self.project_placeholders.currentRow()
        if self.project is None or row < 0:
            return
        key = self.project_placeholders.item(row).data(Qt.UserRole)
        entries = dict(self.project.project_metadata())
        entries.pop(key, None)
        self.project.set_project_metadata(entries)
        self._refresh_placeholders()

    def _copy_diagnostics(self) -> None:
        """Put the bug-report block on the clipboard, and say so.

        The button's own label is the confirmation: a message box for a
        copy is a box to dismiss.
        """
        QGuiApplication.clipboard().setText(diagnostics(self.project))
        self.copy_details_button.setText("Copied to the clipboard")

    def _page(self, title: str) -> QWidget:
        page = QWidget()
        self.pages.addWidget(page)
        return page

    def _choose_backup_folder(self) -> None:
        """Pick the backup folder instead of typing its path.

        Starts where the field points, else at home — never at the
        project, which is the one place backups must not go.
        """
        typed = self.backup_folder_edit.text().strip()
        start = typed if typed and Path(typed).is_dir() else str(Path.home())
        selected = QFileDialog.getExistingDirectory(
            self, "Choose backup folder", start,
            QFileDialog.Option.ShowDirsOnly)
        if selected:
            self.backup_folder_edit.setText(selected)

    def _add_category(self, title: str, page: QWidget) -> QListWidgetItem:
        item = QListWidgetItem(title)
        item.setData(Qt.UserRole, self.pages.indexOf(page))
        self.categories.addItem(item)
        return item

    def _show_category(self, row: int) -> None:
        if row < 0:
            return
        item = self.categories.item(row)
        if item is not None:
            self.pages.setCurrentIndex(item.data(Qt.UserRole))

    def _filter_categories(self, text: str) -> None:
        needle = text.strip().lower()
        for index in range(self.categories.count()):
            item = self.categories.item(index)
            item.setHidden(bool(needle) and needle not in item.text().lower())

    def _update_size_sample(self, value: int) -> None:
        self.size_label.setText(f"{value} pt")
        font = QFont(self.size_sample.font())
        font.setPointSize(value)
        self.size_sample.setFont(font)

    def _toggle_prompt_editor(self, visible: bool) -> None:
        self.ai_prompt_edit.setVisible(visible)
        self.prompt_warning.setVisible(visible)

    def _update_rate_label(self) -> None:
        self.tts_rate_label.setText(
            f"×{self.tts_rate_slider.value() / 100.0:.2f}")

    # -- the AI page --------------------------------------------------------

    def _ai_server_add_row(self) -> QWidget:
        """The way up more than one hat: add an API to the servers
        dropdown so adding another one never removes the last."""
        field = QWidget()
        row = QHBoxLayout(field)
        row.setContentsMargins(0, 0, 0, 0)
        name = QLineEdit()
        name.setPlaceholderText("API name (e.g. Cloud)")
        endpoint = QLineEdit()
        endpoint.setPlaceholderText("https://api.example.com/v1")
        add = QPushButton("Add API…")
        row.addWidget(name)
        row.addWidget(endpoint, 1)
        row.addWidget(add)

        def add_server() -> None:
            if not name.text().strip() or not endpoint.text().strip():
                QMessageBox.warning(
                    self, "Add API",
                    "Give the API a name and an endpoint.")
                return
            from lekhika import ai as _ai
            servers = _ai.load_servers()
            servers.append((name.text().strip(),
                            endpoint.text().strip()))
            _ai.save_servers(servers)
            self._populate_ai_servers()
            self.ai_server_box.setCurrentIndex(
                self.ai_server_box.findData(endpoint.text().strip()))
            name.clear()
            endpoint.clear()

        add.clicked.connect(add_server)
        return field

    def _populate_ai_servers(self) -> None:
        """The server dropdown: the configured ones, the local suspect,
        and the API hat going in. A detected local server appears
        without needing configuration."""
        from lekhika import ai as _ai
        servers = _ai.load_servers()
        index_choice = None
        combo = self.ai_server_box
        combo.clear()
        endpoint_now = self.ai_endpoint_edit.text() if hasattr(
            self, "ai_endpoint_edit") else ""
        for name, endpoint in servers:
            combo.addItem(f"{name}  ({endpoint})", endpoint)
            if endpoint == endpoint_now:
                index_choice = combo.count() - 1
        combo.setCurrentIndex(
            index_choice if index_choice is not None
            else combo.findData(ai.DEFAULT_ENDPOINT))

    def _ai_server_changed(self, _index: int = -1) -> None:
        """Choosing a server sets its endpoint and offers the models it
        has — detected both for local servers and cloud ones."""
        endpoint = self.ai_server_box.currentData()
        if endpoint and self.ai_endpoint_edit.text() != endpoint:
            self.ai_endpoint_edit.setText(endpoint)
        self._populate_ai_models()

    def _populate_ai_models(self) -> None:
        """The models dropdown: what the chosen server offers, plus the
        typed model. Detection (``ollama list`` or ``/v1/models``)
        fills it whenever the server is known."""
        from lekhika import ai as _ai
        endpoint = self.ai_endpoint_edit.text()
        models: list[str] = []
        if endpoint == ai.DEFAULT_ENDPOINT:
            models = _ai.detect_local_models()
        elif endpoint:
            models = _ai.detect_remote_models(endpoint)
        combo = self.ai_model_box
        combo.clear()
        current = combo.lineEdit().text()
        for model in models:
            combo.addItem(model)
        if current:
            combo.setEditText(current)
        elif models:
            combo.setCurrentIndex(0)

    def _detect_local_servers(self) -> None:
        """Ask the machine what local AI it runs — click and see."""
        from lekhika import ai as _ai
        found = _ai.detect_local_servers()
        if not found:
            QMessageBox.information(
                self, "No local servers found",
                "No Ollama client or local server answered. Install "
                "Ollama below, or add an API instead.")
            return
        known = {name for name, _ in _ai.load_servers()}
        servers = _ai.load_servers()
        for name, endpoint in found:
            if name not in known:
                servers.append((name, endpoint))
        _ai.save_servers(servers)
        self._populate_ai_servers()
        self._populate_ai_models()

    def _install_ollama(self) -> None:
        """Install Ollama and a lightweight model (phi4-mini by default).
        Lipstick here: operating system quirks live in ai.install_ollama."""
        from lekhika import ai as _ai
        if QMessageBox.question(
                self, "Install Ollama",
                "Install Ollama, then download a lightweight model "
                "(phi4-mini)? This fetches a few gigabytes. Press Yes to "
                "go ahead.",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No
        ) != QMessageBox.StandardButton.Yes:
            return
        self.setEnabled(False)
        try:
            _ai.install_ollama(silent=True)
        finally:
            self.setEnabled(True)
        QMessageBox.information(
            self, "Install finished",
            "Pull a lightweight model in a terminal with "
            "«ollama pull phi4-mini», then press Detect local servers.")

    # -- the Read aloud page ------------------------------------------------

    def _blog_row(self) -> QWidget:
        """A link, styled like a line: detailed TTS write-ups on the
        author's site, one click away from the basics here."""
        return _link_row(
            "Full write-ups, voice picks and API notes — my blog →",
            "https://vidyut.net/",
            "How to get the most from Read aloud: voices worth their "
            "weight, free options at low volume, and the detailed "
            "possibilities beyond the basic settings here.")

    def _update_tts_controls(self) -> None:
        """Show only what the chosen source needs — system controls for
        system, Piper paths for Piper, API fields for the API."""
        source = next((value for value, radio
                       in self.tts_source_buttons.items()
                       if radio.isChecked()), "system")
        self.tts_voice_box.setEnabled(source == "system")
        self.tts_rate_slider.setEnabled(source == "system")
        self.piper_binary_edit.setEnabled(source == "piper")
        self.piper_voice_edit.setEnabled(source == "piper")

    def _tts_test(self) -> None:
        """Speak the quick brown fox through the current source, so the
        choice can be heard before it's kept."""
        source = next((value for value, radio
                       in self.tts_source_buttons.items()
                       if radio.isChecked()), "system")
        settings = tts.TTSSettings(
            voice=self.tts_voice_box.currentText(),
            rate=self.tts_rate_slider.value() / 100.0,
            engine=tts.load_settings().engine,
            source=source,
            piper_binary=self.piper_binary_edit.text(),
            piper_voice=self.piper_voice_edit.text(),
            api_endpoint=self.api_endpoint_edit.text(),
            api_model=self.api_model_edit.text(),
            api_key=self.api_key_edit.text(),
            api_voice=self.api_voice_edit.text(),
        )
        tts.save_settings(settings)
        reader = tts.SpeechReader(self)
        if not reader.speak(
                "The quick brown fox jumps over the lazy dog."):
            QMessageBox.warning(
                self, "Nothing to hear",
                "That route could not speak just now — the paths or "
                "the API details above are probably off.")

    def _install_piper_and_use(self) -> None:
        """One-click Piper: download the binary and a voice into the
        config folder, verify the binary runs, select Piper as the
        source and point everywhere at it."""
        from lekhika import tts as _tts
        config_dir = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppConfigLocation)
        if not config_dir:
            QMessageBox.warning(self, "Nowhere to install",
                                "This installation has no config folder "
                                "to download into.")
            return
        piper_dir = Path(config_dir) / "lekhika" / "piper"
        state: dict[str, bool] = {"cancelled": False}
        progress = QProgressDialog("Downloading Piper…", "Cancel",
                                   0, 100, self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setValue(0)

        def cancel() -> bool:
            state["cancelled"] = progress.wasCanceled()
            return state["cancelled"]

        def on_progress(done: int, total: int) -> None:
            if progress.wasCanceled():
                return
            progress.setMaximum(total if total else 0)
            progress.setValue(done if total else 0)

        from PySide6.QtCore import QObject, QThread, Signal

        class Worker(QObject):
            updated = Signal(int, int)
            finished = Signal()

            def run(self) -> None:
                self.result = _tts.install_piper_with_voice(
                    piper_dir,
                    progress=lambda done, total: self.updated.emit(done, total),
                    cancelled=cancel,
                    pip=_tts.piper_download() is None)
                self.finished.emit()

        worker = Worker()
        thread = QThread(self)
        worker.moveToThread(thread)
        worker.updated.connect(
            lambda done, total: (progress.setMaximum(max(1, total)),
                                 progress.setValue(done)))
        worker.finished.connect(
            lambda: (thread.quit(), thread.wait(),
                     self._piper_install_done(worker.result)))
        thread.finished.connect(worker.deleteLater)
        thread.started.connect(worker.run)
        thread.start()
        progress.exec()

    def _piper_install_done(self, result) -> None:
        if result is None:
            QMessageBox.warning(self, "Piper did not install",
                                "Check your connection and try again, "
                                "or install Piper by hand from "
                                "github.com/rhasspy/piper.")
            return
        binary, voice = result
        self.piper_binary_edit.setText(str(binary))
        self.piper_voice_edit.setText(str(voice))
        self.tts_source_buttons["piper"].setChecked(True)
        QMessageBox.information(
            self, "Piper ready",
            "Piper and a voice are installed. Hear a sample above to "
            "try it out.")

    def _spell_check_toggled(self, checked: bool) -> None:
        """Spell-check ticked: the Dictionary category appears and the
        dialog stands on it, so the word list can be configured straight
        away."""
        self._update_feature_categories()
        if checked:
            self.categories.setCurrentItem(self.dictionary_category)

    def _ai_check_toggled(self, checked: bool) -> None:
        self._update_feature_categories()
        if checked:
            self.categories.setCurrentItem(self.ai_category)

    def _tts_check_toggled(self, checked: bool) -> None:
        self._update_feature_categories()
        if checked:
            self.categories.setCurrentItem(self.tts_category)

    def _update_feature_categories(self) -> None:
        """Show an optional feature's category only while it is wanted —
        spell-check opens Dictionary, AI integration opens AI, text to
        speech opens Read aloud. An unticked box means those pages would
        be clutter, so they wait in the dialog's storage, not on screen."""
        self.dictionary_category.setHidden(not self.spell_check_box.isChecked())
        self.ai_category.setHidden(not self.ai_enabled_box.isChecked())
        self.tts_category.setHidden(not self.tts_enabled_box.isChecked())

    def _word_list_path(self) -> Path | None:
        """Where the word list lives: the configured file, or the
        project's dictionary.txt for the default (docs/PREFERENCES.md).
        None while there is nothing to write it into."""
        configured = self.dictionary_edit.text().strip()
        if configured:
            return Path(configured).expanduser()
        if self.project_root is not None:
            return Path(self.project_root) / "dictionary.txt"
        return None

    def _refresh_word_list_summary(self) -> None:
        path = self._word_list_path()
        if path is None:
            self.word_list_path_label.setText(
                "No word list yet — open a project first.")
            self.word_list_count_label.setText("")
            return
        words = 0
        try:
            words = sum(1 for line in path.read_text(
                encoding="utf-8").splitlines() if line.strip())
        except OSError:
            try:
                path.touch()
            except OSError:
                pass
        self.word_list_path_label.setText(f"File: {path}")
        self.word_list_count_label.setText(f"{words} word(s)")

    # -- the Editor page: Default ----------------------------------------

    # -- the Editor page: styles ------------------------------------------

    def _style_box_changed(self, _index: int = -1) -> None:
        """Picking the CSS item opens full CSS — then the box goes back to
        Default so it never sits on an edit as if it were a style."""
        if self.style_box.currentData() == CSS_EDIT and self.apply_css:
            self._edit_default_css()
            self.style_box.setCurrentIndex(
                self.style_box.findData("Default"))

    def _refresh_delete_box(self) -> None:
        names = self.style_names() if self.style_names else []
        current = self.style_box.currentData()
        self.style_box.clear()
        # With a provider, the list is live — a delete leaves the box
        # at once. Without one, the given styles seed it.
        ordered = names if self.style_names else list(self.styles or {})
        if "Default" in ordered:
            ordered.remove("Default")
            ordered.insert(0, "Default")
        for name in ordered:
            self.style_box.addItem(name, name)
        self.style_box.insertSeparator(self.style_box.count())
        self.style_box.addItem("Edit as CSS…", CSS_EDIT)
        index = self.style_box.findData(current)
        self.style_box.setCurrentIndex(max(0, index))

    def _edit_style(self) -> None:
        """The beginner's editor for the selected style: size, family,
        emphasis — no CSS needed."""
        name = self.style_box.currentData()
        if not name or name == CSS_EDIT or not self.styles:
            return
        signature = dict(self.styles.get(name) or {})
        popup = QDialog(self)
        popup.setWindowTitle(f"Style: {name}")
        layout = QVBoxLayout(popup)
        fields = [
            ("Font size (pt)", QDoubleSpinBox(
                minimum=6, maximum=72, singleStep=0.5,
                value=float(signature.get("size", 12.0)))),
            ("Font family", QLineEdit(str(signature.get("family", "")))),
        ]
        for label_text, widget in fields:
            row = QHBoxLayout()
            row.addWidget(QLabel(label_text))
            row.addWidget(widget, 1)
            layout.addLayout(row)
        bold = QCheckBox("Bold")
        bold.setChecked(signature.get("weight", 50) >= 75)
        italic = QCheckBox("Italic")
        italic.setChecked(bool(signature.get("italic", False)))
        layout.addWidget(bold)
        layout.addWidget(italic)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(popup.accept)
        buttons.rejected.connect(popup.reject)
        layout.addWidget(buttons)
        size_box, family_edit = fields[0][1], fields[1][1]
        if popup.exec() != QDialog.DialogCode.Accepted:
            return
        signature["size"] = round(size_box.value(), 2)
        family = family_edit.text().strip()
        if family:
            signature["family"] = family
        elif "family" in signature:
            del signature["family"]
        signature["weight"] = 75 if bold.isChecked() else 50
        signature["italic"] = italic.isChecked()
        if self.save_style:
            self.save_style(name, signature)

    def _edit_default_css(self) -> None:
        """default.css in a text popup, saved on close the same way the
        word list is. The file is the readable copy of Default; the
        editor is still the better place to shape it."""
        if self.apply_css is None:
            QMessageBox.warning(
                self, "No style file", "Open a project first.")
            return
        self._text_popup("default.css", self.default_css_text,
                         self.apply_css)

    def _delete_style(self) -> None:
        """A saved style goes back into the bin — with its name stated,
        because a style in use elsewhere makes this anything but
        automatic. The confirm names what deleting changes to the
        text: left unchecked, only the shortcut goes; ticked, the
        paragraphs wearing it come back to Default too."""
        name = self.style_box.currentData()
        if not name or name in (CSS_EDIT,) or self.delete_style is None:
            return
        usage = self.style_usage(name) if self.style_usage else (0, 0)
        dialog = QDialog(self)
        dialog.setWindowTitle("Delete style")
        layout = QVBoxLayout(dialog)
        summary = QLabel(f"Delete the style “{name}”?")
        layout.addWidget(summary)
        revert = None
        if usage and any(usage):
            documents, paragraphs = usage
            layout.addWidget(QLabel(
                f"It dresses {paragraphs} paragraph(s) in {documents} "
                "document(s)."))
            revert = QCheckBox("Also return them to Default")
            revert.setChecked(True)
            layout.addWidget(revert)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Yes
            | QDialogButtonBox.StandardButton.No)
        buttons.button(QDialogButtonBox.StandardButton.Yes).setText(
            "Delete style")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if not self.delete_style(name, bool(revert and revert.isChecked())):
            QMessageBox.warning(
                self, "Built-in style",
                "Built-in styles can't be deleted. If you want it to "
                "look different, edit the formatting itself in the "
                "editor instead.")
        self._refresh_delete_box()

    # -- tiny text popups ---------------------------------------------------

    def _text_popup(self, title: str, text: str,
                    save: Callable[[str], bool]) -> None:
        """One plain box, saved on every close: simple editing for
        things that are just text (the word list, the style file). A
        failed save keeps the popup open with the text still in it."""
        popup = QDialog(self)
        popup.setWindowTitle(title)
        layout = QVBoxLayout(popup)
        area = QPlainTextEdit(text)
        area.setPlaceholderText("One word per line…")
        layout.addWidget(area)
        error = QLabel()
        error.setWordWrap(True)
        layout.addWidget(error)
        done = QPushButton("Done")
        done.clicked.connect(popup.accept)
        layout.addWidget(done)

        original_done = popup.done

        def closing(result: int) -> None:
            if result in (QDialog.DialogCode.Accepted,
                          QDialog.DialogCode.Rejected):
                if not save(area.toPlainText()):
                    error.setText("Could not save — see the error above.")
                    return
            original_done(result)

        popup.done = closing
        popup.exec()

    def _edit_word_list(self) -> None:
        """The word list's own editor: a simple popup, one word per line,
        saved when it closes. The binder never sees the file — no third
        place for the format to drift between."""
        path = self._word_list_path()
        if path is None:
            QMessageBox.warning(
                self, "No word list yet", "Open a project first.")
            return
        try:
            text = path.read_text(encoding="utf-8") if path.exists() else ""
        except OSError:
            text = ""

        def save(plain: str) -> bool:
            lines = [line.strip() for line in plain.splitlines()
                     if line.strip()]
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("\n".join(lines)
                                + ("\n" if lines else ""), encoding="utf-8")
            except OSError:
                return False
            self._refresh_word_list_summary()
            return True

        self._text_popup("Word list", text, save)

    def _scan_entities(self) -> None:
        """Harvest names: capitalised unknowns, tick to add to list.

        Pure scan of titles/synopses/metadata/prose — no AI. The dialog
        lists candidates with counts; ticked ones append to the project
        dictionary.txt (the file wins; the session list re-reads on
        mtime, so the next squiggle refresh already knows them).
        """
        if self.project is None:
            QMessageBox.warning(
                self, "No project", "Open a project before scanning.")
            return
        from lekhika import spellcheck as _sc
        candidates = _sc.entity_candidates(self.project)
        dialog = QDialog(self)
        dialog.setWindowTitle("Names not in the dictionary")
        layout = QVBoxLayout(dialog)
        warning = QLabel(
            "Tick the names that belong in the dictionary. Words already in "
            "the dictionary are never shown. Adding a misspelling makes it "
            "count as correct from now on — don't use Add all unless every "
            "suggestion is right. Unless they're all right or all wrong, "
            "deal with the smaller number first (tick a few, save, rescan).")
        warning.setWordWrap(True)
        layout.addWidget(warning)
        listing = QListWidget()
        for word, count in candidates:
            item = QListWidgetItem(f"{word} · {count}×")
            item.setData(Qt.UserRole, word)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            listing.addItem(item)
        if not candidates:
            listing.addItem("Nothing unknown found — dictionary is up to date.")
        layout.addWidget(listing)
        row = QHBoxLayout()
        check_all = QPushButton("Add all")
        check_all.setToolTip(
            "Ticks every candidate. A misspelling added here counts as "
            "correct from now on — only use it when all suggestions are right.")
        clear_all = QPushButton("Clear")
        row.addWidget(check_all)
        row.addWidget(clear_all)
        row.addStretch()
        checked_box = QCheckBox("I have checked these words and they are the correct spellings")
        checked_box.setEnabled(bool(candidates))
        layout.addWidget(checked_box)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        row.addWidget(buttons)
        layout.addLayout(row)
        check_all.clicked.connect(lambda: [listing.item(i).setCheckState(Qt.Checked) for i in range(listing.count())])
        clear_all.clicked.connect(lambda: [listing.item(i).setCheckState(Qt.Unchecked) for i in range(listing.count())])
        save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        if not candidates:
            save_button.setEnabled(False)
        else:
            save_button.setEnabled(False)
            checked_box.toggled.connect(lambda on: save_button.setEnabled(on))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        chosen = [listing.item(i).data(Qt.UserRole)
                  for i in range(listing.count())
                  if listing.item(i).checkState() == Qt.Checked]
        if not chosen:
            return
        path = self._word_list_path()
        if path is None:
            return
        try:
            existing = path.read_text(encoding="utf-8") if path.exists() else ""
            have = {line.strip() for line in existing.splitlines() if line.strip()}
            fresh = [w for w in chosen if w not in have]
            if not fresh:
                return
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                if existing and not existing.endswith("\n"):
                    handle.write("\n")
                handle.write("\n".join(fresh) + "\n")
        except OSError:
            QMessageBox.warning(self, "Word list", "Could not write the list.")
            return
        self._refresh_word_list_summary()
