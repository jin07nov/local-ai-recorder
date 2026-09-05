"""Durable recording and resumable, sequential transcription jobs (stdlib only)."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import copy
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
import wave

from stt import WhisperCppSTT, TranscriptionCancelled, validate_wav

REPO = Path(__file__).resolve().parent.parent
LANGUAGES = {"ja", "en", "ar", "es", "zh", "ko"}
RATE = 16000
BYTES_PER_SECOND = RATE * 2


@dataclass
class MeetingConfig:
    data_dir: Path = REPO / ".local/meetings"
    binary: Path = REPO / ".local/whisper.cpp/build/bin/whisper-cli"
    model: Path = REPO / ".local/models/ggml-base.bin"
    device: str = "default"
    threads: int = 3
    chunk_seconds: int = 300
    timeout: int = 1800
    max_seconds: int = 7200
    reserve_bytes: int = 256 * 1024 * 1024
    vad_model: str = ""

    @classmethod
    def from_env(cls):
        value = cls()
        for field, env in (("data_dir", "MEETING_DATA_DIR"), ("binary", "WHISPER_BIN"), ("model", "WHISPER_MODEL")):
            if os.getenv(env):
                setattr(value, field, Path(os.environ[env]).expanduser().resolve())
        value.device = os.getenv("MEETING_AUDIO_DEVICE", value.device)
        value.vad_model = os.getenv("WHISPER_VAD_MODEL", "")
        for field, env, low, high in (
            ("threads", "WHISPER_THREADS", 1, 32),
            ("chunk_seconds", "MEETING_CHUNK_SECONDS", 10, 600),
            ("timeout", "WHISPER_TIMEOUT_SECONDS", 1, 14400),
            ("max_seconds", "MEETING_MAX_SECONDS", 1, 21600),
        ):
            number = int(os.getenv(env, getattr(value, field)))
            if not low <= number <= high:
                raise ValueError(f"{env} は {low}〜{high} にしてください。")
            setattr(value, field, number)
        return value

    def engine(self):
        return WhisperCppSTT(self.binary, self.model, self.threads, self.timeout, self.vad_model)


class MeetingError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def atomic_text(path, content):
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as output:
        output.write(content)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)


def atomic_json(path, value):
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def timestamp(seconds):
    seconds = int(seconds)
    return f"{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}"


class DirectoryLock:
    """Prevent a second server/CLI from recovering another process's active job."""

    def __init__(self, directory):
        self.file = (directory / ".lock").open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                self.file.seek(0)
                if not self.file.read(1):
                    self.file.write(b"0")
                    self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise MeetingError("この保存先は別の会議サーバーが使用しています。", 409)

    def close(self):
        self.file.close()


class MeetingService:
    def __init__(self, config, engine=None, recorder_command=None):
        self.config = config
        self.root = config.data_dir.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.directory_lock = DirectoryLock(self.root)
        self.engine = engine or config.engine()
        self.recorder_command = recorder_command
        self.lock = threading.RLock()
        self.records = {}
        self.active = None
        self.thread = None
        self.process = None
        self.cancel_event = threading.Event()
        self.closed = False
        self.load_warnings = []
        try:
            self._recover()
        except Exception:
            self.directory_lock.close()
            raise

    def _directory(self, meeting_id):
        if not re.fullmatch(r"[0-9a-f]{32}", meeting_id):
            raise MeetingError("会議が見つかりません。", 404)
        path = self.root / meeting_id
        if path.is_symlink() or path.resolve().parent != self.root:
            raise MeetingError("会議の保存先が不正です。", 400)
        return path

    def _save(self, record):
        atomic_json(self._directory(record["id"]) / "metadata.json", record)

    def _recover(self):
        for directory in self.root.iterdir():
            if not re.fullmatch(r"[0-9a-f]{32}", directory.name) or directory.is_symlink():
                continue
            try:
                record = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
                if record["id"] != directory.name:
                    raise ValueError("ID mismatch")
                self.records[record["id"]] = record
                if record["status"] in {"recording", "stopping", "transcribing"}:
                    record["status"] = "interrupted"
                    record["error"] = "前回の処理が中断しました。保存済み音声から再実行できます。"
                    try:
                        if not (directory / "audio.wav").exists() and (directory / "audio.pcm").is_file():
                            self._finalize_audio(record)
                    except (MeetingError, OSError, wave.Error) as error:
                        record["error"] = f"音声の復旧を完了できませんでした。{error}"
                    self._save(record)
            except (OSError, ValueError, KeyError, wave.Error):
                self.load_warnings.append(f"会議 {directory.name[:8]} の復旧に失敗しました。保存ファイルは残っています。")

    def _idle(self):
        if self.closed:
            raise MeetingError("サーバーを終了しています。", 503)
        if self.active:
            raise MeetingError("録音または文字起こしが進行中です。完了・停止を待ってください。", 409)

    def _get(self, meeting_id):
        self._directory(meeting_id)
        if meeting_id not in self.records:
            raise MeetingError("会議が見つかりません。", 404)
        return self.records[meeting_id]

    def list(self):
        with self.lock:
            return copy.deepcopy(sorted(self.records.values(), key=lambda r: r["created_at"], reverse=True))

    def get(self, meeting_id):
        with self.lock:
            record = copy.deepcopy(self._get(meeting_id))
            transcript = self._directory(meeting_id) / "transcript.json"
            record["transcript"] = json.loads(transcript.read_text(encoding="utf-8")) if transcript.exists() else None
            record["has_audio"] = (self._directory(meeting_id) / "audio.wav").is_file()
            raw = self._directory(meeting_id) / "audio.pcm"
            record["can_transcribe"] = record["has_audio"] or (raw.is_file() and raw.stat().st_size >= 2)
            return record

    def _new(self, title, language):
        if language not in LANGUAGES:
            raise MeetingError("対応していない入力言語です。")
        if not isinstance(title, str) or len(title) > 120:
            raise MeetingError("会議名は120文字以内にしてください。")
        meeting_id = uuid.uuid4().hex
        self._directory(meeting_id).mkdir()
        record = {"id": meeting_id, "title": title.strip() or "会議録音",
                  "language": language, "created_at": datetime.now().astimezone().isoformat(),
                  "status": "recorded", "duration_seconds": 0, "error": None,
                  "progress": {"done": 0, "total": 0}}
        self.records[meeting_id] = record
        self._save(record)
        return record

    def _space(self, extra=0):
        if shutil.disk_usage(self.root).free < self.config.reserve_bytes + extra:
            raise MeetingError("保存容量が不足しています。不要な会議を削除してから再実行してください。", 507)

    def start(self, title, language):
        with self.lock:
            self._idle()
            self._space()
            command = self.recorder_command
            if command is None:
                binary = shutil.which("arecord")
                if not binary:
                    raise MeetingError("arecord がありません。Pi で setup-meeting.sh を実行してください。", 503)
                command = [binary, "-q", "-D", self.config.device, "-t", "raw", "-f", "S16_LE", "-r", "16000", "-c", "1"]
            record = self._new(title, language)
            record["status"] = "recording"
            record["audio_device"] = self.config.device
            self._save(record)
            try:
                self.process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            except OSError as error:
                record.update(status="failed", error="マイク録音を開始できませんでした。arecord と音声機器を確認してください。")
                self._save(record)
                raise MeetingError(record["error"], 503) from error
            self.active = record["id"]
            self.cancel_event = threading.Event()
            self.thread = threading.Thread(target=self._capture, args=(record,), daemon=True)
            self.thread.start()
            return copy.deepcopy(record)

    def stop(self, meeting_id):
        with self.lock:
            record = self._get(meeting_id)
            if record["status"] not in {"recording", "stopping"} or self.active != meeting_id:
                raise MeetingError("この会議は録音中ではありません。", 409)
            self.cancel_event.set()
            record["status"] = "stopping"
            self._save(record)
            process = self.process
            if process and process.poll() is None:
                process.terminate()
            return copy.deepcopy(record)

    def _capture(self, record):
        directory = self._directory(record["id"])
        process = self.process
        received = 0
        error = None
        carry = b""
        try:
            with (directory / "audio.pcm").open("wb") as output:
                while True:
                    block = process.stdout.read(BYTES_PER_SECOND)
                    if not block:
                        break
                    # Reserve enough space for the final WAV as well as raw recovery audio.
                    self._space(received + len(block) * 2)
                    block = carry + block
                    carry = block[len(block) // 2 * 2:]
                    block = block[:len(block) // 2 * 2]
                    remaining = self.config.max_seconds * BYTES_PER_SECOND - received
                    block = block[:remaining]
                    output.write(block)
                    output.flush()
                    os.fsync(output.fileno())
                    received += len(block)
                    with self.lock:
                        record["duration_seconds"] = received / BYTES_PER_SECOND
                        self._save(record)
                    if received >= self.config.max_seconds * BYTES_PER_SECOND:
                        self.cancel_event.set()
                        record["notice"] = "録音時間の上限に達したため保存しました。"
                        break
            if not self.cancel_event.is_set():
                error = "マイク録音が終了しました。音声機器を確認してください。保存済み区間は再処理できます。"
        except Exception as exception:
            error = str(exception)
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            process.stdout.close()
            with self.lock:
                try:
                    self._finalize_audio(record)
                    record.update(status="interrupted" if error else "recorded", error=error)
                except Exception as exception:
                    record.update(status="failed", error=str(exception))
                try:
                    self._save(record)
                finally:
                    self.active = None
                    self.process = None

    def _finalize_audio(self, record):
        directory = self._directory(record["id"])
        raw = directory / "audio.pcm"
        count = raw.stat().st_size // 2
        if not count:
            raise MeetingError("音声を取得できませんでした。マイクと録音デバイスを確認してください。")
        self._space(count * 2)
        temporary = directory / "audio.wav.tmp"
        with raw.open("rb") as source, wave.open(str(temporary), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(RATE)
            remaining = count * 2
            while remaining:
                data = source.read(min(remaining, BYTES_PER_SECOND * 4))
                if not data:
                    raise OSError("保存音声の読み出しに失敗しました。")
                output.writeframesraw(data)
                remaining -= len(data)
        os.replace(temporary, directory / "audio.wav")
        record["duration_seconds"] = count / RATE

    def import_wav(self, path, title, language):
        path = Path(path)
        with self.lock:
            self._idle()
            with wave.open(str(path), "rb") as audio:
                validate_wav(audio)
                duration = audio.getnframes() / RATE
                if duration > self.config.max_seconds:
                    raise MeetingError("音声が録音時間の上限を超えています。")
            self._space(path.stat().st_size)
            record = self._new(title, language)
            temporary = self._directory(record["id"]) / "audio.wav.tmp"
            try:
                with path.open("rb") as source, temporary.open("wb") as target:
                    shutil.copyfileobj(source, target, BYTES_PER_SECOND * 4)
                    target.flush()
                    os.fsync(target.fileno())
                os.replace(temporary, temporary.with_suffix(""))
                record["duration_seconds"] = duration
            except Exception:
                record.update(status="failed", error="音声ファイルの取り込みに失敗しました。")
                raise
            finally:
                self._save(record)
            return copy.deepcopy(record)

    def transcribe(self, meeting_id):
        with self.lock:
            self._idle()
            record = self._get(meeting_id)
            self.engine.check()
            directory = self._directory(meeting_id)
            if not (directory / "audio.wav").exists() and (directory / "audio.pcm").exists():
                self._finalize_audio(record)
            if not (directory / "audio.wav").exists():
                raise MeetingError("文字起こしに使える保存音声がありません。", 409)
            self._space(self.config.chunk_seconds * BYTES_PER_SECOND)
            record.update(status="transcribing", error=None)
            self._save(record)
            self.active = meeting_id
            self.cancel_event = threading.Event()
            self.thread = threading.Thread(target=self._transcribe, args=(record,), daemon=True)
            self.thread.start()
            return copy.deepcopy(record)

    def cancel(self, meeting_id):
        with self.lock:
            record = self._get(meeting_id)
            if self.active != meeting_id or record["status"] != "transcribing":
                raise MeetingError("この会議は文字起こし中ではありません。", 409)
            self.cancel_event.set()
            return copy.deepcopy(record)

    def _transcribe(self, record):
        directory = self._directory(record["id"])
        audio_path = directory / "audio.wav"
        started = time.monotonic()
        try:
            identity = {"engine": self.engine.identity(), "audio": [audio_path.stat().st_size, audio_path.stat().st_mtime_ns],
                        "language": record["language"], "chunk_seconds": self.config.chunk_seconds, "format": 1}
            fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
            checkpoints = directory / "chunks" / fingerprint
            checkpoints.mkdir(parents=True, exist_ok=True)
            all_segments = []
            with wave.open(str(audio_path), "rb") as source:
                validate_wav(source)
                frames_per_chunk = self.config.chunk_seconds * RATE
                total = math.ceil(source.getnframes() / frames_per_chunk)
                with self.lock:
                    record.update(progress={"done": 0, "total": total}, model=self.config.model.name)
                    self._publish(record, all_segments, identity)
                for index in range(total):
                    if self.cancel_event.is_set():
                        raise TranscriptionCancelled()
                    checkpoint = checkpoints / f"{index:05}.json"
                    if checkpoint.exists():
                        segments = json.loads(checkpoint.read_text(encoding="utf-8"))
                    else:
                        self._space(frames_per_chunk * 2)
                        chunk = checkpoints / "input.wav"
                        source.setpos(index * frames_per_chunk)
                        with wave.open(str(chunk), "wb") as output:
                            output.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
                            output.writeframes(source.readframes(frames_per_chunk))
                        try:
                            result = self.engine.transcribe(chunk, record["language"], self.cancel_event)
                            segments = [{**item, "start": item["start"] + index * self.config.chunk_seconds,
                                         "end": item["end"] + index * self.config.chunk_seconds} for item in result.segments]
                            atomic_json(checkpoint, segments)
                        finally:
                            chunk.unlink(missing_ok=True)
                    for item in segments:
                        all_segments.append({**item, "id": len(all_segments)})
                    with self.lock:
                        record["progress"] = {"done": index + 1, "total": total}
                        self._publish(record, all_segments, identity)
            with self.lock:
                record.update(status="completed", error=None)
        except TranscriptionCancelled:
            with self.lock:
                record.update(status="interrupted", error="文字起こしを中断しました。確定済み区間から再開できます。")
        except Exception as exception:
            with self.lock:
                record.update(status="failed", error=str(exception))
        finally:
            with self.lock:
                record["processing_seconds"] = round(time.monotonic() - started, 2)
                try:
                    self._save(record)
                finally:
                    self.active = None

    def _publish(self, record, segments, identity):
        directory = self._directory(record["id"])
        atomic_json(directory / "transcript.json", {"language": record["language"], "engine": "whisper.cpp", "identity": identity, "segments": segments})
        lines = ["# 文字起こし", "", f"会議: {record['title']}", f"開始: {record['created_at']}",
                 f"言語: {record['language']} / モデル: {self.config.model.name}",
                 f"処理済み区間: {record['progress']['done']} / {record['progress']['total']}", ""]
        lines += [f"[{timestamp(s['start'])} – {timestamp(s['end'])}] {s['text']}" for s in segments]
        atomic_text(directory / "transcript.md", "\n".join(lines) + "\n")
        self._save(record)

    def download(self, meeting_id, name):
        with self.lock:
            self._get(meeting_id)
            if name not in {"audio.wav", "transcript.md", "transcript.json"}:
                raise MeetingError("ファイルが見つかりません。", 404)
            path = self._directory(meeting_id) / name
            if not path.is_file():
                raise MeetingError("ファイルはまだ作成されていません。", 404)
            return path

    def delete(self, meeting_id):
        with self.lock:
            self._get(meeting_id)
            if self.active == meeting_id:
                raise MeetingError("処理中の会議は削除できません。", 409)
            shutil.rmtree(self._directory(meeting_id))
            del self.records[meeting_id]

    def close(self):
        with self.lock:
            self.closed = True
            self.cancel_event.set()
            process = self.process
            if process and process.poll() is None:
                process.terminate()
        if self.thread:
            self.thread.join()
        self.directory_lock.close()
