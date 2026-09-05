"""Lifecycle/integration tests without microphones or model downloads."""
import base64
import http.client
import http.server
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from meeting_server import make_handler
from meetings import MeetingConfig, MeetingError, MeetingService, atomic_json
from stt import MoonshineSTT, Transcription, TranscriptionCancelled, WhisperCppSTT


def make_wav(path, seconds=5, rate=16000, channels=1):
    with wave.open(str(path), "wb") as output:
        output.setparams((channels, 2, rate, 0, "NONE", "not compressed"))
        output.writeframes(b"\x01\x00" * int(seconds * rate * channels))
    return path


def wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("Timed out waiting for worker state")
        time.sleep(0.02)


class FakeEngine:
    def __init__(self):
        self.calls = []
        self.fail_at = None
        self.block_at = None
        self.version = 1

    def check(self):
        pass

    def identity(self):
        return {"version": self.version}

    def transcribe(self, path, language, cancel):
        with wave.open(str(path), "rb") as source:
            frames = source.getnframes()
            data = source.readframes(frames)
        self.calls.append(data)
        if len(self.calls) == self.fail_at:
            raise RuntimeError("Test inference failure")
        if len(self.calls) == self.block_at:
            cancel.wait(5)
            if cancel.is_set():
                raise TranscriptionCancelled()
        return Transcription("会議の記録", language, "whisper.cpp", [
            {"start": 0, "end": frames / 16000, "text": "会議の記録"}])


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.config = MeetingConfig(data_dir=self.root / "meetings", chunk_seconds=2, reserve_bytes=0)
        self.engine = FakeEngine()
        self.service = MeetingService(self.config, engine=self.engine)
        self.wav = make_wav(self.root / "source.wav")

    def tearDown(self):
        self.service.close()
        self.temporary.cleanup()

    def imported(self):
        return self.service.import_wav(self.wav, "会議テスト", "ja")["id"]

    def finish(self):
        self.service.thread.join(timeout=5)
        self.assertFalse(self.service.thread.is_alive())

    def recorder(self, seconds=2, keep_open=True):
        script = f"import sys,time;sys.stdout.buffer.write(b'\\x01\\x00'*{seconds * 16000});sys.stdout.buffer.flush();time.sleep({10 if keep_open else 0})"
        self.service.recorder_command = [sys.executable, "-u", "-c", script]

    def test_chunked_transcription_preserves_all_audio_and_global_times(self):
        meeting_id = self.imported()
        self.service.transcribe(meeting_id)
        self.finish()
        record = self.service.get(meeting_id)
        self.assertEqual(record["status"], "completed")
        self.assertEqual(record["progress"], {"done": 3, "total": 3})
        self.assertEqual(len(b"".join(self.engine.calls)), 5 * 32000)
        self.assertEqual([(s["start"], s["end"]) for s in record["transcript"]["segments"]], [(0, 2), (2, 4), (4, 5)])
        markdown = self.service.download(meeting_id, "transcript.md").read_text(encoding="utf-8")
        self.assertIn("[00:00:04 – 00:00:05]", markdown)
        self.assertEqual(record["transcript"]["language"], "ja")

    def test_failed_job_resumes_completed_chunks_without_duplicates(self):
        meeting_id = self.imported()
        self.engine.fail_at = 2
        self.service.transcribe(meeting_id)
        self.finish()
        self.assertEqual(self.service.get(meeting_id)["status"], "failed")
        self.assertEqual(self.service.get(meeting_id)["progress"]["done"], 1)
        self.engine.fail_at = None
        self.service.transcribe(meeting_id)
        self.finish()
        self.assertEqual(len(self.engine.calls), 4)
        self.assertEqual(len(self.service.get(meeting_id)["transcript"]["segments"]), 3)

    def test_model_identity_change_invalidates_checkpoints(self):
        meeting_id = self.imported()
        self.service.transcribe(meeting_id)
        self.finish()
        self.engine.version = 2
        self.service.transcribe(meeting_id)
        self.finish()
        self.assertEqual(len(self.engine.calls), 6)

    def test_cancel_keeps_audio_and_confirmed_segments_and_can_resume(self):
        meeting_id = self.imported()
        self.engine.block_at = 2
        self.service.transcribe(meeting_id)
        wait_for(lambda: len(self.engine.calls) >= 2)
        self.service.cancel(meeting_id)
        self.finish()
        self.assertEqual(self.service.get(meeting_id)["status"], "interrupted")
        self.assertTrue(self.service.get(meeting_id)["has_audio"])
        self.engine.block_at = None
        self.service.transcribe(meeting_id)
        self.finish()
        self.assertEqual(self.service.get(meeting_id)["status"], "completed")
        self.assertEqual(len(self.engine.calls), 4)

    def test_capture_flushes_pcm_and_stop_creates_valid_wav(self):
        self.recorder()
        record = self.service.start("録音テスト", "ja")
        wait_for(lambda: self.service.get(record["id"])["duration_seconds"] == 2)
        self.assertEqual((self.config.data_dir / record["id"] / "audio.pcm").stat().st_size, 64000)
        self.service.stop(record["id"])
        self.finish()
        result = self.service.get(record["id"])
        self.assertEqual(result["status"], "recorded")
        with wave.open(str(self.service.download(record["id"], "audio.wav")), "rb") as audio:
            self.assertEqual(audio.getnframes(), 32000)

    def test_microphone_disconnect_preserves_captured_audio(self):
        self.recorder(keep_open=False)
        record = self.service.start("切断", "ja")
        self.finish()
        result = self.service.get(record["id"])
        self.assertEqual(result["status"], "interrupted")
        self.assertTrue(result["has_audio"])

    def test_recorder_start_failure_keeps_device_and_alsa_error(self):
        self.config.device = "plughw:CARD=Device,DEV=0"
        self.service.recorder_command = [sys.executable, "-u", "-c",
            "import sys; sys.stderr.write('arecord: audio open error: Device or resource busy'); sys.exit(1)"]
        record = self.service.start("起動失敗", "ja")
        self.finish()
        result = self.service.get(record["id"])
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["has_audio"])
        self.assertIn("Device or resource busy", result["error"])
        self.assertIn(self.config.device, result["error"])
        self.assertEqual(result["recorder_exit_code"], 1)

    def test_large_recorder_error_does_not_block_audio_or_grow_unbounded(self):
        self.service.recorder_command = [sys.executable, "-u", "-c",
            "import sys; sys.stderr.write('x' * 131072 + 'CAPTURE_FAILED'); sys.stderr.flush(); "
            "sys.stdout.buffer.write(bytes(32000)); sys.stdout.buffer.flush(); sys.exit(1)"]
        record = self.service.start("大量の録音エラー", "ja")
        self.finish()
        result = self.service.get(record["id"])
        self.assertEqual(result["status"], "interrupted")
        self.assertTrue(result["has_audio"])
        self.assertIn("CAPTURE_FAILED", result["error"])
        self.assertLess(len(result["error"]), 5000)

    def test_recording_limit_stops_and_preserves_exact_limit(self):
        self.config.max_seconds = 1
        self.recorder(seconds=2)
        record = self.service.start("時間制限", "ja")
        self.finish()
        result = self.service.get(record["id"])
        self.assertEqual(result["status"], "recorded")
        self.assertEqual(result["duration_seconds"], 1)
        self.assertIn("上限", result["notice"])

    def test_active_capture_rejects_other_job_and_delete(self):
        other = self.imported()
        self.recorder()
        record = self.service.start("使用中", "ja")
        with self.assertRaises(MeetingError):
            self.service.transcribe(other)
        with self.assertRaises(MeetingError):
            self.service.delete(record["id"])
        self.service.stop(record["id"])
        self.finish()

    def test_restart_recovers_durable_pcm_after_interruption(self):
        record = self.service._new("復旧", "ja")
        record["status"] = "recording"
        self.service._save(record)
        raw = self.config.data_dir / record["id"] / "audio.pcm"
        raw.write_bytes(b"\x01\x00" * 16000 + b"\x00")
        self.service.close()
        self.service = MeetingService(self.config, engine=self.engine)
        result = self.service.get(record["id"])
        self.assertEqual(result["status"], "interrupted")
        self.assertEqual(result["duration_seconds"], 1)
        self.assertTrue(result["has_audio"])

    def test_second_process_cannot_recover_live_data(self):
        with self.assertRaises(MeetingError):
            MeetingService(self.config, engine=self.engine)

    def test_empty_interrupted_recording_does_not_block_startup(self):
        record = self.service._new("空の録音", "ja")
        record["status"] = "recording"
        self.service._save(record)
        (self.config.data_dir / record["id"] / "audio.pcm").touch()
        self.service.close()
        self.service = MeetingService(self.config, engine=self.engine)
        result = self.service.get(record["id"])
        self.assertEqual(result["status"], "interrupted")
        self.assertFalse(result["can_transcribe"])
        self.assertIn("復旧", result["error"])

    def test_restart_preserves_transcription_checkpoints_for_recorded_pcm(self):
        self.recorder(seconds=5, keep_open=False)
        record = self.service.start("再起動と再開", "ja")
        self.finish()
        self.engine.fail_at = 2
        self.service.transcribe(record["id"])
        self.finish()
        # Simulate metadata left behind by an abrupt exit during inference.
        saved = self.service.records[record["id"]]
        saved["status"] = "transcribing"
        self.service._save(saved)
        self.service.close()
        self.engine.fail_at = None
        self.service = MeetingService(self.config, engine=self.engine)
        self.service.transcribe(record["id"])
        self.finish()
        self.assertEqual(self.service.get(record["id"])["status"], "completed")
        self.assertEqual(len(self.engine.calls), 4)

    def test_recovery_without_space_preserves_raw_for_later_retry(self):
        record = self.service._new("容量不足の復旧", "ja")
        record["status"] = "recording"
        self.service._save(record)
        (self.config.data_dir / record["id"] / "audio.pcm").write_bytes(bytes(32000))
        self.service.close()
        with patch("meetings.shutil.disk_usage", return_value=SimpleNamespace(free=1)):
            self.service = MeetingService(self.config, engine=self.engine)
        result = self.service.get(record["id"])
        self.assertTrue(result["can_transcribe"])
        self.assertFalse(result["has_audio"])
        self.service.transcribe(record["id"])
        self.finish()
        self.assertEqual(self.service.get(record["id"])["status"], "completed")

    def test_no_space_does_not_start_capture(self):
        self.recorder()
        self.config.reserve_bytes = 1024
        with patch("meetings.shutil.disk_usage", return_value=SimpleNamespace(free=1)):
            with self.assertRaises(MeetingError) as caught:
                self.service.start("容量不足", "ja")
        self.assertEqual(caught.exception.status, 507)
        self.assertIsNone(self.service.active)
        self.assertEqual(self.service.list(), [])

    def test_invalid_import_does_not_create_meeting(self):
        invalid = make_wav(self.root / "stereo.wav", channels=2)
        with self.assertRaises(ValueError):
            self.service.import_wav(invalid, "不正な形式", "ja")
        self.assertEqual(self.service.list(), [])

    def test_truncated_wav_is_rejected_before_import(self):
        invalid = make_wav(self.root / "truncated.wav")
        invalid.write_bytes(invalid.read_bytes()[:-100])
        with self.assertRaises(ValueError):
            self.service.import_wav(invalid, "途切れた音声", "ja")
        self.assertEqual(self.service.list(), [])

    def test_http_record_stop_transcribe_download_delete(self):
        self.recorder(seconds=1)
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.service))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()

        def request(method, path, body=None):
            connection = http.client.HTTPConnection(*server.server_address, timeout=5)
            encoded = json.dumps(body).encode() if body is not None else None
            connection.request(method, path, encoded, {"Content-Type": "application/json"})
            response = connection.getresponse()
            payload = response.read()
            code = response.status
            connection.close()
            return code, payload

        try:
            code, payload = request("POST", "/api/meetings", {"title": "HTTP 会議", "language": "ja"})
            self.assertEqual(code, 201)
            meeting_id = json.loads(payload)["id"]
            wait_for(lambda: self.service.get(meeting_id)["duration_seconds"] == 1)
            self.assertEqual(request("POST", f"/api/meetings/{meeting_id}/stop", {})[0], 202)
            self.finish()
            self.assertEqual(request("POST", f"/api/meetings/{meeting_id}/transcribe", {})[0], 202)
            self.finish()
            code, payload = request("GET", f"/api/meetings/{meeting_id}")
            self.assertEqual(json.loads(payload)["status"], "completed")
            code, audio = request("GET", f"/api/meetings/{meeting_id}/audio.wav")
            self.assertEqual(code, 200)
            self.assertTrue(audio.startswith(b"RIFF"))
            self.assertEqual(request("POST", f"/api/meetings/{meeting_id}/delete", {})[0], 200)
            self.assertEqual(json.loads(request("GET", "/api/meetings")[1])["meetings"], [])
        finally:
            server.shutdown()
            server.server_close()
            worker.join()


class AdapterTests(unittest.TestCase):
    def test_moonshine_keeps_pcm_contract_and_text_joining(self):
        samples = [0.1, 0.2]
        calls = []
        class Recognizer:
            def transcribe_without_streaming(self, audio, rate):
                calls.append((audio, rate))
                return SimpleNamespace(lines=[SimpleNamespace(text="first"), SimpleNamespace(text="second")])
        adapter = MoonshineSTT(lambda language: Recognizer(), threading.RLock())
        result = adapter.transcribe_samples(samples, "ja")
        self.assertEqual(result.text, "first second")
        self.assertIs(calls[0][0], samples)
        self.assertEqual(calls[0][1], 16000)
        self.assertEqual(result.segments, [])

    def test_whisper_cli_arguments_and_millisecond_json_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wav = make_wav(root / "audio.wav", seconds=2)
            model = root / "model.bin"
            model.write_bytes(b"test fixture")
            adapter = WhisperCppSTT(Path(sys.executable), model)
            calls = []
            def process(command, **kwargs):
                calls.append(command)
                destination = Path(command[command.index("-of") + 1] + ".json")
                atomic_json(destination, {"transcription": [{"offsets": {"from": 250, "to": 1500}, "text": " 日本語 "}]})
                return SimpleNamespace(wait=lambda timeout: 0, poll=lambda: 0)
            with patch("stt.subprocess.Popen", side_effect=process):
                result = adapter.transcribe(wav, "ja")
            self.assertEqual(result.segments, [{"start": .25, "end": 1.5, "text": "日本語"}])
            self.assertEqual(calls[0][calls[0].index("-l") + 1], "ja")
            self.assertNotIn("-tr", calls[0])

    def test_whisper_timeout_terminates_child(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wav = make_wav(root / "audio.wav", seconds=1)
            model = root / "model.bin"
            model.write_bytes(b"test fixture")
            adapter = WhisperCppSTT(Path(sys.executable), model, timeout=-1)
            class Process:
                stopped = False
                def poll(self): return 0 if self.stopped else None
                def terminate(self): self.stopped = True
                def wait(self, timeout): return 0
            child = Process()
            with patch("stt.subprocess.Popen", return_value=child):
                with self.assertRaises(TimeoutError):
                    adapter.transcribe(wav, "ja")
            self.assertTrue(child.stopped)

    @unittest.skipUnless(importlib.util.find_spec("numpy"), "Original translator requires numpy")
    def test_original_stt_http_response_remains_compatible(self):
        import server as translator_server
        recognizer = SimpleNamespace(transcribe_without_streaming=lambda samples, rate: SimpleNamespace(
            lines=[SimpleNamespace(text="hello"), SimpleNamespace(text="world")]))
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), translator_server.ProxyHTTPRequestHandler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with patch.object(translator_server, "get_stt_recognizer", return_value=recognizer):
                connection = http.client.HTTPConnection(*server.server_address, timeout=5)
                audio = base64.b64encode(struct.pack("<ff", 0.1, 0.2)).decode()
                connection.request("POST", "/api/stt", json.dumps({"audio_base64": audio, "language": "en"}), {"Content-Type": "application/json"})
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(json.loads(response.read()), {"text": "hello world"})
                connection.close()
        finally:
            server.shutdown()
            server.server_close()
            worker.join()


if __name__ == "__main__":
    unittest.main()
