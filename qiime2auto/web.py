"""无第三方 Web 依赖的本地控制台。"""

from __future__ import annotations

import json
import shutil
import threading
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .config import AnalysisConfig
from .io import generate_manifest, generate_metadata_template, read_sample_ids, scan_input, validate_metadata_details
from .pipeline import PipelineOptions, run_analysis
from .runner import command_text


WEB_ROOT = Path(__file__).resolve().parents[1] / "web"
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


def _json_bytes(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def _preview_command(data: dict) -> str:
    input_path = data.get("input_path", "<input>")
    output_path = data.get("output_dir", "qiime2_analysis")
    parts = ["python", "qiime2_auto.py", "-i", input_path, "-o", output_path, "--metadata", data.get("metadata", "metadata.tsv")]
    for key in ("barcodes", "primer_f", "primer_r", "classifier", "sampling_depth"):
        if data.get(key):
            parts.extend([f"--{key.replace('_', '-')}", str(data[key])])
    for key in ("no_trim", "no_filter", "no_figaro", "skip_taxonomy", "skip_diversity", "skip_ancom", "dry_run"):
        if data.get(key):
            parts.append(f"--{key.replace('_', '-')}")
    return command_text(parts)


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "QIIME2AutoDashboard/1.0"

    def log_message(self, format: str, *args) -> None:
        print(f"[web] {self.address_string()} - {format % args}")

    def _send(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = _json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8"))

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            self._send({"ok": True, "version": "1.0.0", "tools": {name: shutil.which(name) is not None for name in ("qiime", "biom", "figaro")}})
            return
        if parsed.path == "/api/scan":
            values = parse_qs(parsed.query)
            path = values.get("path", [""])[0]
            self._send({"ok": True, "scan": scan_input(path)})
            return
        if parsed.path.startswith("/api/jobs/"):
            job_id = parsed.path.rsplit("/", 1)[-1]
            with JOBS_LOCK:
                job = JOBS.get(job_id)
            if not job:
                self._send({"ok": False, "error": "找不到任务"}, HTTPStatus.NOT_FOUND)
            else:
                self._send({"ok": True, "job": job})
            return
        self._serve_static(parsed.path)

    def do_POST(self) -> None:
        try:
            data = self._body()
            path = urlparse(self.path).path
            if path == "/api/manifest":
                input_path = Path(data["input_path"])
                output_path = data.get("output_path") or str(input_path / "manifest.tsv")
                result = generate_manifest(input_path, output_path, bool(data.get("paired_end", True)))
                if not result:
                    raise ValueError("没有找到可以组成 manifest 的 FASTQ 配对")
                self._send({"ok": True, "path": result, "scan": scan_input(result)})
                return
            if path == "/api/metadata":
                source = data["source_path"]
                output_path = data.get("output_path") or str(Path(source).with_name("metadata.tsv"))
                result = generate_metadata_template(read_sample_ids(source), output_path, data.get("columns") or ["group"])
                self._send({"ok": True, "path": result, "sample_count": len(read_sample_ids(source))})
                return
            if path == "/api/validate-metadata":
                result = validate_metadata_details(data["path"])
                self._send({"ok": result.valid, "validation": result.to_dict()}, HTTPStatus.OK if result.valid else HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            if path == "/api/preview":
                self._send({"ok": True, "command": _preview_command(data)})
                return
            if path == "/api/run":
                self._start_job(data)
                return
            self._send({"ok": False, "error": "未知 API"}, HTTPStatus.NOT_FOUND)
        except (KeyError, ValueError, OSError, json.JSONDecodeError) as exc:
            self._send({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:  # pragma: no cover - 兜底，避免浏览器收到空白响应
            self._send({"ok": False, "error": f"服务器错误: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _start_job(self, data: dict) -> None:
        required = ["input_path", "output_dir", "data_type", "metadata"]
        missing = [key for key in required if not data.get(key)]
        if missing:
            raise ValueError(f"缺少参数: {', '.join(missing)}")
        job_id = uuid.uuid4().hex[:12]
        with JOBS_LOCK:
            JOBS[job_id] = {"id": job_id, "status": "running", "message": "任务已启动", "result": None}

        def worker() -> None:
            try:
                config = AnalysisConfig.from_mapping(data)
                options = PipelineOptions(
                    input_path=data["input_path"], output_dir=data["output_dir"], data_type=data["data_type"],
                    barcodes=data.get("barcodes"), primer_f=data.get("primer_f"), primer_r=data.get("primer_r"),
                    primer_metadata=data.get("primer_metadata"), no_trim=bool(data.get("no_trim")),
                    no_filter=bool(data.get("no_filter")), no_figaro=bool(data.get("no_figaro")),
                    skip_taxonomy=bool(data.get("skip_taxonomy")), skip_diversity=bool(data.get("skip_diversity")),
                    skip_ancom=bool(data.get("skip_ancom")), dry_run=bool(data.get("dry_run")),
                )
                result = run_analysis(config, options)
                with JOBS_LOCK:
                    JOBS[job_id].update({"status": "completed" if result.success else "failed", "message": result.error or "分析完成", "result": result.to_dict()})
            except Exception as exc:
                with JOBS_LOCK:
                    JOBS[job_id].update({"status": "failed", "message": str(exc)})

        threading.Thread(target=worker, name=f"qiime2auto-{job_id}", daemon=True).start()
        self._send({"ok": True, "job_id": job_id}, HTTPStatus.ACCEPTED)

    def _serve_static(self, request_path: str) -> None:
        # README 位于项目根目录，不属于普通静态资源目录；只为这两个明确文件
        # 提供映射，避免放开任意父目录访问造成路径穿越风险。
        if request_path in {"/README.html", "/guide.html"}:
            candidate = (WEB_ROOT.parent / "README.html").resolve()
            content_type = "text/html; charset=utf-8"
        elif request_path == "/README.md":
            candidate = (WEB_ROOT.parent / "README.md").resolve()
            content_type = "text/markdown; charset=utf-8"
        else:
            relative = unquote(request_path.lstrip("/")) or "index.html"
            candidate = (WEB_ROOT / relative).resolve()
            content_type = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "application/javascript; charset=utf-8"}.get(candidate.suffix, "application/octet-stream")
        if WEB_ROOT.resolve() not in candidate.parents and candidate != WEB_ROOT.resolve():
            if candidate not in {(WEB_ROOT.parent / "README.html").resolve(), (WEB_ROOT.parent / "README.md").resolve()}:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
        if not candidate.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = candidate.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    server = ThreadingHTTPServer((host, port), DashboardHandler)
    print(f"QIIME2 Auto 控制台已启动: http://{host}:{port}")
    print("按 Ctrl+C 停止。默认只监听本机，适合本地数据使用。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止控制台…")
    finally:
        server.server_close()
