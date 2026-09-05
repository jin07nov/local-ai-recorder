"""Local STT adapters. Importing this module never loads a speech model."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
import json
import math
import subprocess
import tempfile
import threading
import time
import wave


@dataclass
class Transcription:
    text: str
    language: str
    engine: str
    segments: list[dict] = field(default_factory=list)


class SpeechToText(Protocol):
    def transcribe(self, audio_path: Path, language: str) -> Transcription: ...


class MoonshineSTT:
    """Keep the translator's existing recognizer cache and inference lock."""

    def __init__(self, get_recognizer, lock):
        self.get_recognizer = get_recognizer
        self.lock = lock

    def transcribe_samples(self, samples, language: str) -> Transcription:
        with self.lock:
            result = self.get_recognizer(language).transcribe_without_streaming(samples, 16000)
        return Transcription(" ".join(line.text for line in result.lines), language, "moonshine")

    def transcribe(self, audio_path: Path, language: str) -> Transcription:
        import numpy as np
        with wave.open(str(audio_path), "rb") as source:
            validate_wav(source)
            samples = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2")
        return self.transcribe_samples(samples.astype(np.float32) / 32768.0, language)


def validate_wav(source):
    if (source.getnchannels(), source.getsampwidth(), source.getframerate(), source.getcomptype()) != (1, 2, 16000, "NONE"):
        raise ValueError("音声は 16 kHz・モノラル・16-bit PCM WAV にしてください。")
    if source.getnframes() == 0:
        raise ValueError("音声が空です。")
    position = source.tell()
    source.setpos(source.getnframes() - 1)
    complete = len(source.readframes(1)) == 2
    source.setpos(position)
    if not complete:
        raise ValueError("WAV の音声データが途中で切れています。元の録音を確認してください。")


class TranscriptionCancelled(Exception):
    pass


class WhisperCppSTT:
    def __init__(self, binary, model, threads=3, timeout=1800, vad_model=None):
        self.binary = Path(binary)
        self.model = Path(model)
        self.threads = threads
        self.timeout = timeout
        self.vad_model = Path(vad_model) if vad_model else None

    def check(self):
        if not self.binary.is_file():
            raise ValueError("whisper-cli がありません。setup-meeting.sh を実行してください。")
        if not self.model.is_file():
            raise ValueError("Whisper モデルがありません。setup-meeting.sh を実行してください。")
        if self.vad_model and not self.vad_model.is_file():
            raise ValueError("指定した VAD モデルがありません。")

    def identity(self):
        self.check()
        paths = [self.binary, self.model] + ([self.vad_model] if self.vad_model else [])
        return {"files": [(str(p.resolve()), p.stat().st_size, p.stat().st_mtime_ns) for p in paths], "threads": self.threads}

    def transcribe(self, audio_path: Path, language: str, cancel=None) -> Transcription:
        self.check()
        cancel = cancel or threading.Event()
        with wave.open(str(audio_path), "rb") as source:
            validate_wav(source)
            duration = source.getnframes() / 16000
        # Keep temporary files next to the chunk, outside publicly served files.
        with tempfile.TemporaryDirectory(prefix="whisper-", dir=audio_path.parent) as directory:
            output = Path(directory) / "result"
            command = [str(self.binary.resolve()), "-m", str(self.model.resolve()),
                       "-f", str(audio_path.resolve()), "-l", language,
                       "-t", str(self.threads), "-oj", "-of", str(output), "-np", "-ng"]
            if self.vad_model:
                command += ["--vad", "-vm", str(self.vad_model.resolve())]
            # Do not log meeting text; JSON is the only transcript output we consume.
            process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            deadline = time.monotonic() + self.timeout
            try:
                while True:
                    if cancel.is_set():
                        raise TranscriptionCancelled()
                    if time.monotonic() >= deadline:
                        raise TimeoutError("文字起こしが制限時間を超えました。保存音声から再実行できます。")
                    try:
                        code = process.wait(timeout=0.2)
                        break
                    except subprocess.TimeoutExpired:
                        pass
                if code:
                    raise RuntimeError(f"whisper.cpp が終了コード {code} で停止しました。モデル・空きメモリを確認してください。")
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
            result_file = output.with_suffix(".json")
            if not result_file.is_file():
                raise RuntimeError("whisper.cpp の JSON 出力がありません。バージョン・モデルを確認してください。")
            data = json.loads(result_file.read_text(encoding="utf-8"))
        segments = []
        for item in data["transcription"]:
            start = float(item["offsets"]["from"]) / 1000
            end = float(item["offsets"]["to"]) / 1000
            if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end < start:
                raise ValueError("whisper.cpp の時刻情報が不正です。")
            text = item["text"].strip()
            if text and start < duration:
                segments.append({"start": max(0, start), "end": min(duration, end), "text": text})
        return Transcription("\n".join(s["text"] for s in segments), language, "whisper.cpp", segments)
