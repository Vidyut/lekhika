"""Read prose aloud (3.1).

Three ways out, picked by where they run:

- **System speech** — the system's own engine (Qt's QTextToSpeech:
  speech-dispatcher and espeak-ng are installed here). The default, and
  the only source that works with nothing installed.
- **Piper** — a small, free, offline neural speech program the user
  downloads (a few dozen megabytes, or lighter), pointed at here with
  its binary and one voice model. Considerably lighter than the big
  models, and it never sends text out of the machine. The download
  lives with Piper's releases; the settings just remember where the
  binary and voice went.
- **External API** — a TTS service over HTTP (OpenAI-compatible
  request shape: JSON in, audio bytes out) the writer has configured
  themselves, for trying free tiers at low volume, or for providers
  with voices they already pay for. The key lives in settings, never
  in the project.

A session reader owns one recorder of that choice: speak plain text
for the system path, synthesize to a temporary file and play it for
Piper or the API. Nothing here uploads a manuscript; the API route
sends the words it speaks, and only those.
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, Signal
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtTextToSpeech import QTextToSpeech

DEFAULT_ENGINE = "speechd"
SOURCES = ("system", "piper", "api")


@dataclass(frozen=True)
class TTSSettings:
    voice: str = ""
    rate: float = 0.0     # QTextToSpeech rates run -1.0 .. 1.0
    engine: str = DEFAULT_ENGINE
    source: str = "system"     # system | piper | api
    piper_binary: str = ""
    piper_voice: str = ""
    api_endpoint: str = ""
    api_model: str = ""
    api_key: str = ""
    api_voice: str = ""


def load_settings() -> TTSSettings:
    settings = QSettings()
    try:
        rate = float(settings.value("tts/rate", 0.0))
    except (TypeError, ValueError):
        rate = 0.0
    source = str(settings.value("tts/source", "system"))
    return TTSSettings(
        voice=str(settings.value("tts/voice", "")),
        rate=rate,
        engine=(str(settings.value("tts/engine", DEFAULT_ENGINE))
                or DEFAULT_ENGINE),
        source=source if source in SOURCES else "system",
        piper_binary=str(settings.value("tts/piperBinary", "")),
        piper_voice=str(settings.value("tts/piperVoice", "")),
        api_endpoint=str(settings.value("tts/apiEndpoint", "")),
        api_model=str(settings.value("tts/apiModel", "")),
        api_key=str(settings.value("tts/apiKey", "")),
        api_voice=str(settings.value("tts/apiVoice", "")),
    )


def save_settings(settings: TTSSettings) -> None:
    store = QSettings()
    store.setValue("tts/voice", settings.voice)
    store.setValue("tts/rate", settings.rate)
    store.setValue("tts/engine", settings.engine or DEFAULT_ENGINE)
    store.setValue("tts/source", settings.source)
    store.setValue("tts/piperBinary", settings.piper_binary)
    store.setValue("tts/piperVoice", settings.piper_voice)
    store.setValue("tts/apiEndpoint", settings.api_endpoint)
    store.setValue("tts/apiModel", settings.api_model)
    store.setValue("tts/apiKey", settings.api_key)
    store.setValue("tts/apiVoice", settings.api_voice)


def available_system_engines() -> list[str]:
    """What this installation can speak with as it stands. The engine
    list is the machine's own — this probes nothing about another
    system: detection has to happen where the app runs, like every
    fallback it decides between from that answer."""
    return list(QTextToSpeech.availableEngines())


# ---------------------------------------------------------------------------
# Piper: the one-click install (Linux, x86_64/aarch64).
#
# The release binary with its license is a single tarball; the voice is
# one .onnx file and its .onnx.json sidecar from the piper-voices
# repository. Everything downloads to the user's own config folder, and
# nothing writes outside it.
# ---------------------------------------------------------------------------

_PIPER_RELEASES: tuple[tuple[str, str], ...] = (
    ("aarch64", "piper_linux_aarch64.tar.gz",
      "https://github.com/rhasspy/piper/releases/download/"
      "2023.11.14-2/piper_linux_aarch64.tar.gz"),
    ("x86_64", "piper_linux_x86_64.tar.gz",
      "https://github.com/rhasspy/piper/releases/download/"
      "2023.11.14-2/piper_linux_x86_64.tar.gz"),
)
_PIPER_VOICE_BASE = ("https://huggingface.co/rhasspy/piper-voices/resolve/"
                     "main/en/en_US/lessac/low/en_US-lessac-low.onnx")
PIPER_VOICE_NAME = "en_US-lessac-low"


def piper_download() -> tuple[str, str] | None:
    """(tarball name, URL) for this machine, or None.

    Piper's official releases are Linux builds only — the universal
    route is elsewhere in this file (system speech, an API, or the
    ``piper-tts`` package on other platforms)."""
    if sys.platform != "linux":
        return None
    machine = platform.machine().lower()
    mapping = ("arm64" if machine == "arm64" else machine)
    for family, name, url in _PIPER_RELEASES:
        if family == mapping or family in machine:
            return (name, url)
    return None


def _download(url: str, dest: Path, progress=None, cancelled=None) -> bool:
    """Stream one file; False on error or cancel."""
    try:
        request = urllib.request.Request(
            url, headers={"User-Agent": "Lekhak"})
        with urllib.request.urlopen(request, timeout=120) as response:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            dest.parent.mkdir(parents=True, exist_ok=True)
            with dest.open("wb") as handle:
                while True:
                    if cancelled and cancelled():
                        return False
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
    except OSError:
        return False
    return True


def _download_voice(directory: Path, progress=None, cancelled=None) -> Path | None:
    """One voice model and its sidecar, downloaded and verified."""
    voice_dir = Path(directory) / "voices"
    voice = voice_dir / f"{PIPER_VOICE_NAME}.onnx"
    if not _download(_PIPER_VOICE_BASE, voice, progress, cancelled):
        return None
    sidecar = voice.with_suffix(voice.suffix + ".json")
    if not _download(_PIPER_VOICE_BASE + ".json", sidecar,
                     progress, cancelled):
        return None
    return voice


def install_piper(directory: Path, progress=None, cancelled=None) -> tuple[Path, Path] | None:
    """Download+unpack Piper and one voice into ``directory``.

    ``progress(done, total)`` and ``cancelled()`` are optional hooks for
    a progress dialog. Returns (binary, voice model) on success.
    """
    directory = Path(directory)
    choice = piper_download()
    if choice is None:
        return None
    name, url = choice
    tarball = directory / name
    if not _download(url, tarball, progress, cancelled):
        return None
    import tarfile
    try:
        with tarfile.open(tarball) as archive:
            archive.extractall(directory)
    except (tarfile.TarError, OSError):
        tarball.unlink(missing_ok=True)
        return None
    members = [m for m in directory.iterdir() if m.is_file()]
    binary = next((m for m in members if m.name == "piper"), None)
    if binary is None:
        tarball.unlink(missing_ok=True)
        return None
    binary.chmod(binary.stat().st_mode | 0o111)
    voice = _download_voice(directory, progress, cancelled)
    if voice is None:
        return None
    try:
        subprocess.run([str(binary), "--help"], check=True,
                       capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    tarball.unlink(missing_ok=True)
    return (binary, voice)


def pip_install_piper(progress=None, cancelled=None) -> Path | None:
    """`pip install piper-tts` — the Piper package works on Windows,
    macOS and Linux alike, so this is the universal install. Returns
    the `piper` command afterwards or None."""
    import sys
    environment = dict(__import__("os").environ)
    if cancelled and cancelled():
        return None
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "piper-tts"],
            capture_output=True, text=True, timeout=300, env=environment)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    found = shutil.which("piper")
    return Path(found) if found else None


def install_piper_with_voice(directory: Path, progress=None,
                             cancelled=None, pip: bool = False) -> tuple[Path, Path] | None:
    """Binary plus voice as one step (second halfway: binary first)."""
    if pip:
        binary = pip_install_piper(progress, cancelled)
        if binary is None:
            return None
        voice = _download_voice(directory, progress, cancelled)
        return (binary, voice) if voice is not None else None
    return install_piper(directory, progress, cancelled)


def find_piper_on_path() -> Path | None:
    """An already-installed Piper, wherever it lives — for a binary a
    user got through pip, a system package manager, or a build of their
    own."""
    found = shutil.which("piper")
    return Path(found) if found else None


def speech_text(selected: str) -> str:
    """Selection text fit for speaking.

    QTextCursor.selectedText() joins paragraphs with Unicode
    separators the engines read as glued words; a newline becomes a
    pause instead.
    """
    return selected.replace("\u2029", "\n").replace("\u2028", "\n")


def voice_name(voice) -> str:
    """QVoice.name is a method in some PySide6 builds and a plain
    property in others; ask whichever this one is."""
    name = voice.name
    return str(name() if callable(name) else name)


class SpeechReader(QObject):
    """Owns the speech engine (or an audio player) for one window.

    Constructed lazily on the first read-aloud: opening a
    speech-dispatcher connection costs real time and most sessions
    never read aloud. ``state_changed`` forwards the engine state so
    the UI can tell speaking from idle.

    The three sources share ``speak(text)``: the system's engine
    speaks directly, while Piper and the API are synthesized to a
    temporary audio file and played. A failed route returns False
    rather than silently saying nothing.
    """
    state_changed = Signal(object)

    def __init__(self, parent=None, engine_name: str | None = None) -> None:
        super().__init__(parent)
        self._engine_name = engine_name
        self._engine: QTextToSpeech | None = None
        self._voices: list = []
        self._player: QMediaPlayer | None = None
        self._audio_file: Path | None = None

    def ensure_engine(self) -> bool:
        """Connect the engine; False when this system has none."""
        if self._engine is not None:
            return True
        engines = QTextToSpeech.availableEngines()
        wanted = self._engine_name or load_settings().engine
        name = wanted if wanted in engines else (
            engines[0] if engines else None)
        if name is None:
            return False
        self._engine = QTextToSpeech(name)
        self._engine.stateChanged.connect(self._forward_state)
        self._voices = list(self._engine.availableVoices())
        return True

    def _forward_state(self, state) -> None:
        # A direct signal-to-signal connect is refused across the
        # C++/PyObject boundary; re-emit by hand.
        self.state_changed.emit(state)

    def voice_names(self) -> list[str]:
        if not self.ensure_engine():
            return []
        return [voice_name(voice) for voice in self._voices]

    def apply_settings(self) -> None:
        """Put the saved voice and rate on the engine."""
        if not self.ensure_engine() or self._engine is None:
            return
        saved = load_settings()
        for voice in self._voices:
            if voice_name(voice) == saved.voice:
                self._engine.setVoice(voice)
                break
        self._engine.setRate(saved.rate)

    def speak(self, text: str) -> bool:
        if not text.strip():
            return False
        settings = load_settings()
        if settings.source == "piper":
            return self._speak_piper(settings, text)
        if settings.source == "api":
            return self._speak_api(settings, text)
        if not self.ensure_engine():
            return False
        self.apply_settings()
        if self._engine is None:
            return False
        self._engine.say(text)
        return True

    def pause(self) -> None:
        if self._player is not None:
            self._player.pause()
            return
        if self._engine is not None:
            self._engine.pause()

    def resume(self) -> None:
        if self._player is not None:
            self._player.play()
            return
        if self._engine is not None:
            self._engine.resume()

    def stop(self) -> None:
        if self._player is not None:
            self._player.stop()
        if self._engine is not None:
            self._engine.stop()

    def state(self):
        if self._player is not None:
            return self._player.playbackState()
        return self._engine.state() if self._engine is not None else None

    def _play_audio(self, path: Path) -> bool:
        """Play a synthesized audio file; the previous utterance's file
        leaves once the new one is on the air."""
        previous = self._audio_file
        if previous is not None:
            previous.unlink(missing_ok=True)
        self._audio_file = path
        if self._player is None:
            self._player = QMediaPlayer(self)
        from PySide6.QtCore import QUrl
        self._player.setSource(QUrl.fromLocalFile(str(path)))
        self._player.play()
        return True

    def _speak_piper(self, settings: TTSSettings, text: str) -> bool:
        binary = Path(settings.piper_binary).expanduser() if settings.piper_binary else None
        voice = Path(settings.piper_voice).expanduser() if settings.piper_voice else None
        if binary is None or not binary.is_file() or voice is None or not voice.is_file():
            return False
        fd, temporary = tempfile.mkstemp(suffix=".wav", prefix="lekhak-")
        import os
        os.close(fd)
        try:
            result = subprocess.run(
                [str(binary), "--model", str(voice),
                 "--output_file", temporary],
                input=text, capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.TimeoutExpired):
            Path(temporary).unlink(missing_ok=True)
            return False
        if result.returncode != 0 or not Path(temporary).stat().st_size:
            Path(temporary).unlink(missing_ok=True)
            return False
        return self._play_audio(Path(temporary))

    def _speak_api(self, settings: TTSSettings, text: str) -> bool:
        if not settings.api_endpoint:
            return False
        payload = json.dumps({
            "model": settings.api_model or "tts-1",
            "voice": settings.api_voice or "alloy",
            "input": text,
        }).encode("utf-8")
        request = urllib.request.Request(
            settings.api_endpoint, data=payload, method="POST",
            headers={"Content-Type": "application/json"})
        if settings.api_key:
            request.add_header("Authorization",
                               f"Bearer {settings.api_key}")
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                audio = response.read()
                content_type = response.headers.get_content_type()
        except OSError:
            return False
        if not audio:
            return False
        suffix = { "audio/wav": ".wav", "audio/x-wav": ".wav",
                   "audio/mpeg": ".mp3", "audio/ogg": ".ogg",
                   "audio/mp4": ".m4a" }.get(content_type, ".mp3")
        fd, temporary = tempfile.mkstemp(suffix=suffix, prefix="lekhak-")
        import os
        with os.fdopen(fd, "wb") as handle:
            handle.write(audio)
        return self._play_audio(Path(temporary))
