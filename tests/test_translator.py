"""Translator language routing over the existing HTTP API, without model downloads."""
import base64
from collections import OrderedDict
import http.client
import http.server
import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))


@unittest.skipUnless(importlib.util.find_spec("numpy"), "Translator requires numpy")
class TranslatorLanguageTests(unittest.TestCase):
    def setUp(self):
        import server
        self.translator = server
        self.temporary = tempfile.TemporaryDirectory()
        self.audio_dir = Path(self.temporary.name) / "audio"
        self.audio_patch = patch.object(server, "TRANSLATOR_AUDIO_DIR", self.audio_dir)
        self.audio_patch.start()
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.ProxyHTTPRequestHandler)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join()
        self.audio_patch.stop()
        self.temporary.cleanup()

    def request(self, language, samples=(-2, -.5, 0, .5, 2)):
        audio = base64.b64encode(struct.pack("<" + "f" * len(samples), *samples)).decode()
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        connection.request("POST", "/api/stt", json.dumps({"language": language, "audio_base64": audio}),
                           {"Content-Type": "application/json"})
        response = connection.getresponse()
        code, body = response.status, response.read()
        connection.close()
        return code, body

    def test_german_uses_whisper_with_pcm16_wav_and_keeps_text_response(self):
        paths = []

        def transcribe(path, language):
            self.assertEqual(language, "de")
            with wave.open(str(path), "rb") as audio:
                self.assertEqual((audio.getnchannels(), audio.getsampwidth(), audio.getframerate()), (1, 2, 16000))
                self.assertEqual(struct.unpack("<5h", audio.readframes(5)), (-32767, -16383, 0, 16383, 32767))
            paths.append(path)
            return SimpleNamespace(text="Guten Tag. Wie geht es Ihnen?")

        engine = SimpleNamespace(check=lambda: None, transcribe=transcribe)
        config = SimpleNamespace(engine=lambda: engine)
        with patch.object(self.translator.MeetingConfig, "from_env", return_value=config), \
                patch.object(self.translator, "get_stt_recognizer") as moonshine:
            code, body = self.request("de")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body), {"text": "Guten Tag. Wie geht es Ihnen?"})
        moonshine.assert_not_called()
        self.assertTrue(paths)
        self.assertFalse(paths[0].exists())
        self.assertEqual(list(self.audio_dir.iterdir()), [])

    def test_all_existing_languages_still_use_moonshine(self):
        recognizer = SimpleNamespace(transcribe_without_streaming=lambda samples, rate:
                                     SimpleNamespace(lines=[SimpleNamespace(text="original result")]))
        with patch.object(self.translator, "get_stt_recognizer", return_value=recognizer) as moonshine, \
                patch.object(self.translator.MeetingConfig, "from_env") as whisper:
            for language in ("ar", "en", "es", "ja", "zh", "ko"):
                with self.subTest(language=language):
                    code, body = self.request(language)
                    self.assertEqual(code, 200)
                    self.assertEqual(json.loads(body), {"text": "original result"})
                    moonshine.assert_called_with(language)
            whisper.assert_not_called()

    def test_missing_whisper_is_reported_without_english_fallback(self):
        engine = SimpleNamespace(check=Mock(side_effect=ValueError("Whisper モデルがありません。")))
        with patch.object(self.translator.MeetingConfig, "from_env", return_value=SimpleNamespace(engine=lambda: engine)), \
                patch.object(self.translator, "get_stt_recognizer") as moonshine:
            code, body = self.request("de")
        self.assertEqual(code, 500)
        self.assertIn("Whisper モデル", body.decode())
        moonshine.assert_not_called()
        self.assertFalse(self.audio_dir.exists())

    def test_inference_failure_removes_temporary_audio(self):
        engine = SimpleNamespace(check=lambda: None, transcribe=Mock(side_effect=TimeoutError("inference timed out")))
        with patch.object(self.translator.MeetingConfig, "from_env", return_value=SimpleNamespace(engine=lambda: engine)):
            code, body = self.request("de")
        self.assertEqual(code, 500)
        self.assertIn(b"timed out", body)
        self.assertEqual(list(self.audio_dir.iterdir()), [])

    def test_non_finite_german_audio_never_reaches_model(self):
        with patch.object(self.translator.MeetingConfig, "from_env") as config:
            code, _ = self.request("de", samples=(float("nan"),))
            self.assertEqual(code, 500)
            config.assert_not_called()

    def test_german_cannot_enter_moonshine_english_fallback(self):
        with self.assertRaisesRegex(ValueError, "whisper.cpp"):
            self.translator.get_stt_recognizer("de")

    def test_german_tts_uses_native_german_voice_and_returns_wav(self):
        engine = SimpleNamespace(synthesize=Mock(return_value=([0.1, 0.2, 0.0], 24000)))
        constructor = Mock(return_value=engine)
        with patch.dict(sys.modules, {"moonshine_voice": SimpleNamespace(TextToSpeech=constructor)}), \
                patch.object(self.translator, "_tts_engines", OrderedDict()):
            connection = http.client.HTTPConnection(*self.server.server_address, timeout=5)
            connection.request("GET", "/api/tts?text=Guten%20Tag&lang=de")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertTrue(response.read().startswith(b"RIFF"))
            connection.close()
            constructor.assert_called_once_with("de-de")
            engine.synthesize.assert_called_once_with("Guten Tag")
            self.assertIs(self.translator.get_tts_engine("de"), engine)
            constructor.assert_called_once()


if __name__ == "__main__":
    unittest.main()
