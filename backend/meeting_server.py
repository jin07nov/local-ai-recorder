"""Standalone local meeting API/UI; no Moonshine, Gemma or third-party Python packages."""

import argparse
import http.server
import json
import mimetypes
import os
from pathlib import Path
import shutil
import signal
import sys
from urllib.parse import urlparse

from meetings import MeetingConfig, MeetingError, MeetingService, REPO


def capabilities(config):
    errors = []
    try:
        config.engine().check()
    except ValueError as error:
        errors.append(str(error))
    return {"recording_available": bool(shutil.which("arecord")),
            "transcription_available": not errors, "errors": errors,
            "model": config.model.name, "audio_device": config.device,
            "max_seconds": config.max_seconds, "chunk_seconds": config.chunk_seconds}


def make_handler(service, dist_dir=REPO / "frontend/dist"):
    dist_dir = Path(dist_dir).resolve()

    class Handler(http.server.BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(15)

        def log_message(self, format, *args):
            # Avoid recording meeting titles, transcript text or query parameters in logs.
            pass

        def send_json(self, value, status=200):
            body = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def send_file(self, path, download=False):
            with path.open("rb") as source:
                self.send_response(200)
                self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
                self.send_header("Content-Length", str(os.fstat(source.fileno()).st_size))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Cache-Control", "no-store")
                if download:
                    self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
                self.end_headers()
                shutil.copyfileobj(source, self.wfile, 128 * 1024)

        def do_GET(self):
            try:
                path = urlparse(self.path).path
                if path == "/api/meetings/status":
                    return self.send_json({**capabilities(service.config), "warnings": service.load_warnings})
                if path == "/api/meetings":
                    return self.send_json({"meetings": service.list()})
                parts = path.strip("/").split("/")
                if parts[:2] == ["api", "meetings"]:
                    if len(parts) == 3:
                        return self.send_json(service.get(parts[2]))
                    if len(parts) == 4:
                        return self.send_file(service.download(parts[2], parts[3]), download=True)
                    raise MeetingError("操作が見つかりません。", 404)
                relative = "meeting.html" if path == "/" else path.lstrip("/")
                target = (dist_dir / relative).resolve()
                if not target.is_relative_to(dist_dir) or not target.is_file():
                    raise MeetingError("画面が見つかりません。setup-meeting.sh で UI をビルドしてください。", 404)
                return self.send_file(target)
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                return
            except MeetingError as error:
                return self.send_json({"error": str(error)}, error.status)
            except FileNotFoundError:
                return self.send_json({"error": "ファイルが見つかりません。"}, 404)
            except (OSError, ValueError):
                return self.send_json({"error": "保存ファイルを読み出せませんでした。空き容量とファイルを確認してください。"}, 500)

        def do_POST(self):
            try:
                # State changes use same-origin JSON; no cross-origin CORS permission.
                origin = self.headers.get("Origin")
                if origin and origin != f"http://{self.headers.get('Host')}":
                    raise MeetingError("この操作は会議画面から行ってください。", 403)
                if self.headers.get_content_type() != "application/json":
                    raise MeetingError("JSON 形式で操作してください。", 415)
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 16384:
                    raise MeetingError("リクエストのサイズが不正です。", 413)
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise MeetingError("リクエストの形式が不正です。")
                path = urlparse(self.path).path
                if path == "/api/meetings":
                    return self.send_json(service.start(data.get("title", ""), data.get("language", "ja")), 201)
                parts = path.strip("/").split("/")
                if len(parts) != 4 or parts[:2] != ["api", "meetings"]:
                    raise MeetingError("操作が見つかりません。", 404)
                meeting_id, action = parts[2:]
                operations = {"stop": service.stop, "transcribe": service.transcribe, "cancel": service.cancel}
                if action == "delete":
                    service.delete(meeting_id)
                    return self.send_json({"deleted": meeting_id})
                if action not in operations:
                    raise MeetingError("操作が見つかりません。", 404)
                return self.send_json(operations[action](meeting_id), 202)
            except MeetingError as error:
                return self.send_json({"error": str(error)}, error.status)
            except (ValueError, TypeError) as error:
                return self.send_json({"error": str(error)}, 400)
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                return
            except OSError:
                return self.send_json({"error": "保存・処理を開始できませんでした。空き容量・権限を確認してください。"}, 500)

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.getenv("MEETING_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("MEETING_PORT", "3001")))
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--import-wav", type=Path, help="Import and transcribe a local 16 kHz mono PCM WAV, then exit")
    parser.add_argument("--language", default="ja")
    args = parser.parse_args()
    config = MeetingConfig.from_env()
    if args.check:
        state = capabilities(config)
        state["ui_available"] = (REPO / "frontend/dist/meeting.html").exists()
        state["data_dir"] = str(config.data_dir)
        print(json.dumps(state, ensure_ascii=False, indent=2))
        return 0 if state["recording_available"] and state["transcription_available"] and state["ui_available"] else 1
    service = MeetingService(config)
    try:
        if args.import_wav:
            record = service.import_wav(args.import_wav, args.import_wav.stem, args.language)
            service.transcribe(record["id"])
            service.thread.join()
            record = service.get(record["id"])
            print(json.dumps({k: v for k, v in record.items() if k != "transcript"}, ensure_ascii=False, indent=2))
            print(f"Saved: {config.data_dir / record['id']}")
            return 0 if record["status"] == "completed" else 1
        def stop_server(signum, frame):
            raise KeyboardInterrupt()
        signal.signal(signal.SIGTERM, stop_server)
        with http.server.ThreadingHTTPServer((args.host, args.port), make_handler(service)) as server:
            print(f"Meeting recorder: http://{args.host}:{args.port}", flush=True)
            print(f"Audio device: {config.device}; model: {config.model.name}", flush=True)
            print(f"Data: {config.data_dir}", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
    finally:
        service.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (MeetingError, ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
