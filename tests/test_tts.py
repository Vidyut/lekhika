"""3.1: read aloud — the settings store, the selection cleanup, and
SpeechReader against Qt's mock engine (offscreen, no audio).
"""

import tempfile
import unittest
from pathlib import Path

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from lekhika import tts  # noqa: E402

_application = QApplication.instance() or QApplication([])
_application.setOrganizationName("Lekhika")
_application.setApplicationName("Lekhika")
_settings_dir = tempfile.mkdtemp(prefix="lekhika-tts-settings-")
QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, _settings_dir)


class SpeechTextTests(unittest.TestCase):
    def test_paragraph_separators_become_newlines(self):
        self.assertEqual(tts.speech_text("one two three"),
                         "one\ntwo\nthree")

    def test_plain_text_is_untouched(self):
        self.assertEqual(tts.speech_text("just words"), "just words")


class TTSSettingsTests(unittest.TestCase):
    def test_round_trip(self):
        tts.save_settings(tts.TTSSettings(voice="Anne", rate=0.35,
                                          engine="mock"))

        loaded = tts.load_settings()

        self.assertEqual(loaded.voice, "Anne")
        self.assertEqual(loaded.rate, 0.35)
        self.assertEqual(loaded.engine, "mock")

    def test_piper_and_api_settings_round_trip(self):
        saved = tts.TTSSettings(
            source="piper", piper_binary="/opt/piper/piper",
            piper_voice="/git/piper/voices/a.onnx",
            api_endpoint="https://api.example/v1/audio/speech",
            api_model="tts-1", api_key="key-1", api_voice="alloy")
        tts.save_settings(saved)

        loaded = tts.load_settings()

        self.assertEqual(loaded.source, "piper")
        self.assertEqual(loaded.piper_binary, "/opt/piper/piper")
        self.assertEqual(loaded.piper_voice, "/git/piper/voices/a.onnx")
        self.assertEqual(loaded.api_endpoint,
                         "https://api.example/v1/audio/speech")
        self.assertEqual(loaded.api_key, "key-1")

    def test_defaults_when_empty(self):
        tts.save_settings(tts.TTSSettings())

        loaded = tts.load_settings()

        self.assertEqual(loaded.voice, "")
        self.assertEqual(loaded.rate, 0.0)
        self.assertEqual(loaded.engine, tts.DEFAULT_ENGINE)
        self.assertEqual(loaded.source, "system")
        self.assertEqual(loaded.piper_binary, "")


class SpeechReaderTests(unittest.TestCase):
    """The mock engine stands in for speechd: same API, no audio."""

    def setUp(self):
        self.reader = tts.SpeechReader(engine_name="mock")

    def test_engine_connects_and_lists_voices(self):
        self.assertTrue(self.reader.ensure_engine())

        names = self.reader.voice_names()

        self.assertIn("Bob", names)
        self.assertIn("Anne", names)

    def test_speak_and_stop_are_safe(self):
        tts.save_settings(tts.TTSSettings(engine="mock"))
        self.reader.speak("Hello there.")
        self.reader.stop()

        self.assertIsNotNone(self.reader.state())

    def test_blank_text_is_not_spoken(self):
        self.reader.speak("   \n  ")

        # The engine never started; mock stays Ready or untouched.
        state = self.reader.state()
        self.assertTrue(state is None or state.name in ("Ready", "Idle"))

    def test_apply_settings_with_empty_store(self):
        tts.save_settings(tts.TTSSettings(engine="mock"))

        self.reader.apply_settings()

        self.assertIsNotNone(self.reader.state())

    def test_voice_name_reads_property_or_method(self):
        class PropertyVoice:
            name = "Pat"

        class MethodVoice:
            def name(self):
                return "Mel"

        self.assertEqual(tts.voice_name(PropertyVoice()), "Pat")
        self.assertEqual(tts.voice_name(MethodVoice()), "Mel")

    def test_system_failure_speaks_false(self):
        from unittest.mock import patch

        with patch.object(tts.QTextToSpeech, "availableEngines",
                         return_value=[]):
            self.assertFalse(tts.SpeechReader().speak("Hello error."))

    def test_missing_piper_paths_speak_false(self):
        tts.save_settings(tts.TTSSettings(
            source="piper", piper_binary="/nowhere/piper",
            piper_voice="/nowhere/v.onnx"))

        self.assertFalse(tts.SpeechReader().speak("Hello Piper."))

    def test_api_without_endpoint_speaks_false(self):
        tts.save_settings(tts.TTSSettings(source="api"))

        self.assertFalse(tts.SpeechReader().speak("Hello API."))


class PiperPlaybackTests(unittest.TestCase):
    """The subprocess route: a fake piper binary writes the WAV; the
    audio file goes to the player."""

    def setUp(self):
        tts.save_settings(tts.TTSSettings(
            source="piper", piper_binary="__BINARY__",
            piper_voice="/voice/a.onnx"))

    def _fake_binary(self, with_output: bool = True) -> str:
        directory = tempfile.mkdtemp(prefix="fakepiper-")
        binary = Path(directory) / "piper"
        binary.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "out = sys.argv[sys.argv.index('--output_file') + 1]\n"
            "sys.stdout.write('synthesized')\n"
            + ("open(out, 'wb').write(b'RIFF fake wav')\n"
               if with_output else ""), encoding="utf-8")
        binary.chmod(0o755)
        voice = Path(directory) / "a.onnx"
        voice.write_text("voice", encoding="utf-8")
        return str(binary), str(voice)

    def test_speaking_uses_the_binary_and_plays(self):
        binary, voice = self._fake_binary()
        original = tts.load_settings()
        tts.save_settings(tts.TTSSettings(
            source="piper", piper_binary=binary, piper_voice=voice))
        played = []
        reader = tts.SpeechReader()
        reader._play_audio = lambda path: played.append(path) or True

        try:
            self.assertTrue(reader.speak("Hello Piper."))
        finally:
            tts.save_settings(original)

        self.assertTrue(played)
        self.assertTrue(played[0].exists())

    def test_a_quiet_binary_fails_closed(self):
        binary, voice = self._fake_binary(with_output=False)
        original = tts.load_settings()
        tts.save_settings(tts.TTSSettings(
            source="piper", piper_binary=binary, piper_voice=voice))

        try:
            self.assertFalse(tts.SpeechReader().speak("Hello Piper."))
        finally:
            tts.save_settings(original)


class ApiTTSPlaybackTests(unittest.TestCase):
    """The HTTP route: bytes posted, audio file played."""

    def setUp(self):
        self.calls = []
        self.responses = [
            (200, "audio/mpeg", b"fake audio"),
        ]

    def _fake_urlopen(self, request, timeout=0):
        self.calls.append(request)
        status, content_type, payload = self.responses[0]

        class FakeResponse:
            def __init__(self, payload, content_type):
                self._payload = payload
                self.headers = self
                self._type = content_type

            def read(self):
                return self._payload

            def get_content_type(self):
                return self._type

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        return FakeResponse(payload, content_type)

    def test_speaking_posts_and_plays(self):
        from unittest.mock import patch
        saved = tts.TTSSettings(
            source="api", api_endpoint="https://api.example/v1/audio/speech",
            api_model="tts-1", api_key="k", api_voice="alloy")
        original = tts.load_settings()
        tts.save_settings(saved)
        played = []
        reader = tts.SpeechReader()
        reader._play_audio = lambda path: played.append(path) or True

        try:
            with patch("urllib.request.urlopen", self._fake_urlopen):
                self.assertTrue(reader.speak("Hello API."))
        finally:
            tts.save_settings(original)

        self.assertEqual(len(self.calls), 1)
        self.assertTrue(played)
        self.assertEqual(played[0].suffix, ".mp3")

    def test_an_empty_reply_fails_closed(self):
        from unittest.mock import patch
        saved = tts.TTSSettings(
            source="api", api_endpoint="https://api.example/v1/audio/speech")
        original = tts.load_settings()
        tts.save_settings(saved)

        try:
            with patch("urllib.request.urlopen", self._fake_urlopen):
                self.responses = [(200, "application/json", b"")]
                self.assertFalse(tts.SpeechReader().speak("Hello API."))
        finally:
            tts.save_settings(original)

    def test_a_key_is_never_sent_in_the_clear_or_to_a_bad_scheme(self):
        # The chat client's rule, for the same reasons: no key and no
        # spoken text over a cleartext remote connection, and only
        # http(s) is an endpoint at all.
        from unittest.mock import patch
        original = tts.load_settings()
        try:
            for endpoint in ("http://api.example/v1/audio/speech",
                             "file:///etc/passwd"):
                tts.save_settings(tts.TTSSettings(
                    source="api", api_endpoint=endpoint, api_key="secret"))
                with patch("urllib.request.urlopen", self._fake_urlopen):
                    self.assertFalse(tts.SpeechReader().speak("Hello API."))
        finally:
            tts.save_settings(original)
        self.assertEqual(self.calls, [])


class PiperInstallTests(unittest.TestCase):
    """The download-and-unpack route: the tarball is opened, but a
    member that would write outside the folder is refused."""

    def test_a_tarball_that_escapes_the_folder_is_refused(self):
        import io
        import tarfile
        from unittest.mock import patch
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            info = tarfile.TarInfo("../escape.txt")
            data = b"evil"
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        payload = buffer.getvalue()

        def fake_download(url, destination, progress=None, cancelled=None):
            Path(destination).write_bytes(payload)
            return True

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "piper"
            directory.mkdir()
            with patch.object(tts, "piper_download",
                              return_value=("piper.tgz",
                                            "https://example/piper.tgz")), \
                    patch.object(tts, "_download", side_effect=fake_download):
                result = tts.install_piper(directory)
            self.assertIsNone(result)
            self.assertFalse((Path(temporary) / "escape.txt").exists())


if __name__ == "__main__":
    unittest.main()
