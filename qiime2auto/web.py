"""无第三方 Web 依赖的本地控制台。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import uuid
from email import policy
from email.parser import BytesParser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .classifiers import CLASSIFIER_ROOT, classifier_catalog, download_classifier
from .config import AnalysisConfig
from .environment import discover_environments, figaro_install_command, install_command, install_options
from .io import generate_manifest, generate_metadata_template, inspect_manifest, is_fastq, read_sample_ids, reconcile_manifest, scan_input, validate_metadata_details
from .pipeline import PipelineOptions, run_analysis
from .preflight import build_preflight
from .runner import command_text
from .table_editor import metadata_preview, preview_manifest, save_manifest_table, save_metadata_table


WEB_ROOT = Path(__file__).resolve().parents[1] / "web"
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()
INSTALL_JOBS: dict[str, dict] = {}
INSTALL_LOCK = threading.Lock()
DOWNLOAD_JOBS: dict[str, dict] = {}
DOWNLOAD_LOCK = threading.Lock()
UPLOAD_ROOT = WEB_ROOT.parent / ".qiime2auto_uploads"


def _json_bytes(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def _preview_command(data: dict) -> str:
    input_path = data.get("input_path", "<input>")
    output_path = data.get("output_dir", "qiime2_analysis")
    parts = ["python", "qiime2_auto.py", "-i", input_path, "-o", output_path]
    if data.get("metadata"):
        parts.extend(["--metadata", str(data["metadata"])])
    for key in ("barcodes", "primer_f", "primer_r", "primer_metadata", "classifier", "sampling_depth", "phred_offset", "min_quality", "min_frequency", "trim_left_f", "trim_left_r", "trunc_len_f", "trunc_len_r", "max_ee", "trunc_q", "qiime_env"):
        if data.get(key):
            parts.extend([f"--{key.replace('_', '-')}", str(data[key])])
    for key in ("no_trim", "no_filter", "no_figaro", "skip_taxonomy", "skip_diversity", "skip_ancom", "dry_run"):
        if data.get(key):
            parts.append(f"--{key.replace('_', '-')}")
    return command_text(parts)


def _list_directories(path_value: str | None) -> dict:
    requested = Path(path_value).expanduser() if path_value else Path.cwd()
    if requested.exists() and not requested.is_dir():
        requested = requested.parent
    if not requested.exists():
        requested = requested.parent
    current = requested.resolve()
    if not current.is_dir():
        current = Path.cwd().resolve()
    directories = []
    for child in sorted(current.iterdir(), key=lambda item: item.name.lower()):
        try:
            if child.is_dir() and not child.name.startswith("."):
                directories.append({"name": child.name, "path": str(child.resolve())})
        except OSError:
            continue
    parent = current.parent if current.parent != current else None
    return {"current": str(current), "parent": str(parent) if parent else None, "directories": directories}


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
            self._send({"ok": True, "version": "1.0.0", "platform": os.name, "tools": {name: shutil.which(name) is not None for name in ("qiime", "biom", "figaro", "conda")}})
            return
        if parsed.path == "/api/environments":
            self._send({"ok": True, **discover_environments(probe=True)})
            return
        if parsed.path == "/api/install-options":
            self._send({"ok": True, **install_options()})
            return
        if parsed.path == "/api/classifiers":
            catalog = classifier_catalog()
            downloaded = next((item for item in catalog if item["downloaded"] and item["recommended"]), None) or next((item for item in catalog if item["downloaded"]), None)
            self._send({"ok": True, "root": str(CLASSIFIER_ROOT.resolve()), "catalog": catalog, "default": downloaded["path"] if downloaded else None})
            return
        if parsed.path == "/api/directories":
            values = parse_qs(parsed.query)
            self._send({"ok": True, **_list_directories(values.get("path", [""])[0])})
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
        if parsed.path.startswith("/api/install-jobs/"):
            job_id = parsed.path.rsplit("/", 1)[-1]
            with INSTALL_LOCK:
                job = INSTALL_JOBS.get(job_id)
            if not job:
                self._send({"ok": False, "error": "找不到安装任务"}, HTTPStatus.NOT_FOUND)
            else:
                self._send({"ok": True, "job": job})
            return
        if parsed.path.startswith("/api/download-jobs/"):
            job_id = parsed.path.rsplit("/", 1)[-1]
            with DOWNLOAD_LOCK:
                job = DOWNLOAD_JOBS.get(job_id)
            if not job:
                self._send({"ok": False, "error": "找不到下载任务"}, HTTPStatus.NOT_FOUND)
            else:
                self._send({"ok": True, "job": job})
            return
        self._serve_static(parsed.path)

    def do_POST(self) -> None:
        try:
            path = urlparse(self.path).path
            if path == "/api/upload":
                self._handle_upload()
                return
            data = self._body()
            if path == "/api/preflight":
                self._send({"ok": True, "preflight": build_preflight(data)})
                return
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
            if path == "/api/metadata-preview":
                self._send({"ok": True, "preview": metadata_preview(data["path"])})
                return
            if path == "/api/metadata-save":
                saved = save_metadata_table(data["path"], data.get("headers") or [], data.get("types") or [], data.get("rows") or [])
                self._send({"ok": True, "path": saved["path"], "preview": metadata_preview(saved["path"]), "validation": {key: value for key, value in saved.items() if key in {"valid", "sample_count", "columns", "types", "errors", "warnings"}}})
                return
            if path == "/api/manifest-preview":
                self._send({"ok": True, "preview": preview_manifest(data["path"], data.get("bundle_dir"))})
                return
            if path == "/api/manifest-save":
                saved = save_manifest_table(data["path"], data["data_type"], data.get("rows") or [])
                bundle_dir = data.get("bundle_dir") or str(Path(saved["path"]).parent)
                preview = preview_manifest(saved["path"], bundle_dir)
                self._send({"ok": True, "path": saved["path"], "preview": preview, "scan": scan_input(saved["path"], bundle_dir)})
                return
            if path == "/api/validate-metadata":
                result = validate_metadata_details(data["path"])
                self._send({"ok": result.valid, "validation": result.to_dict()}, HTTPStatus.OK if result.valid else HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            if path == "/api/preview":
                self._send({"ok": True, "command": _preview_command(data)})
                return
            if path == "/api/install-preview":
                command = install_command(data.get("version", ""), data.get("distribution", "amplicon"), data.get("environment_name"))
                self._send({"ok": True, "command": command, "command_text": " ".join(command)})
                return
            if path == "/api/install":
                self._start_install(data)
                return
            if path == "/api/figaro/install":
                self._start_figaro_install(data)
                return
            if path == "/api/classifiers/download":
                self._start_classifier_download(data)
                return
            if path == "/api/run":
                self._start_job(data)
                return
            self._send({"ok": False, "error": "未知 API"}, HTTPStatus.NOT_FOUND)
        except (KeyError, ValueError, OSError, json.JSONDecodeError) as exc:
            self._send({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:  # pragma: no cover - 兜底，避免浏览器收到空白响应
            self._send({"ok": False, "error": f"服务器错误: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_upload(self) -> None:
        content_type = self.headers.get("Content-Type", "")
        if not content_type.startswith("multipart/form-data"):
            raise ValueError("文件上传必须使用 multipart/form-data")
        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length) if length else b""
        message = BytesParser(policy=policy.default).parsebytes(
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8") + raw_body
        )
        fields: dict[str, str] = {}
        items: list[tuple[str, bytes]] = []
        for part in message.iter_parts() if message.is_multipart() else []:
            name = part.get_param("name", header="content-disposition")
            filename = part.get_filename()
            if filename:
                items.append((filename, part.get_payload(decode=True) or b""))
            elif name:
                payload = part.get_payload(decode=True) or b""
                fields[name] = payload.decode("utf-8", errors="replace")
        if not items:
            raise ValueError("没有选择文件")
        session_id = fields.get("session_id", "").strip()
        if session_id and (len(session_id) != 32 or any(character not in "0123456789abcdef" for character in session_id.lower())):
            raise ValueError("上传会话无效，请重新选择文件")
        session_id = session_id or uuid.uuid4().hex
        session_dir = UPLOAD_ROOT / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        saved = []
        for original_name, content in items:
            filename = Path(original_name).name
            if not filename:
                continue
            destination = session_dir / filename
            destination.write_bytes(content)
            saved.append(str(destination.resolve()))
        if not saved:
            raise ValueError("没有可保存的文件")
        selected_kind = fields.get("kind", "data")
        manifest_candidates = []
        for candidate in sorted(session_dir.iterdir()):
            if candidate.is_file() and not is_fastq(candidate):
                details = inspect_manifest(candidate, session_dir)
                if details.get("data_type"):
                    manifest_candidates.append(candidate)
        manifest_source = manifest_candidates[-1] if manifest_candidates else None
        normalized_manifest = reconcile_manifest(manifest_source, session_dir) if manifest_source and selected_kind in {"fastq", "manifest"} else None
        if selected_kind in {"classifier", "metadata"}:
            target = saved[0]
        elif normalized_manifest and Path(normalized_manifest).is_file() and Path(normalized_manifest).name.startswith(".qiime2auto"):
            target = normalized_manifest
        elif manifest_source:
            target = str(manifest_source.resolve())
        elif selected_kind == "fastq":
            target = str(session_dir.resolve())
        else:
            target = saved[0] if len(saved) == 1 else str(session_dir.resolve())
        scan = scan_input(target, session_dir)
        self._send({"ok": True, "kind": selected_kind, "session_id": session_id, "path": target, "files": saved, "scan": scan})

    def _start_install(self, data: dict) -> None:
        if os.name != "posix":
            raise ValueError("QIIME2 一键安装只支持 Linux；请在 Linux + Conda 服务器上打开本项目。")
        details = discover_environments(probe=False)
        if not details.get("conda_available"):
            raise ValueError(details.get("error") or "未找到 conda")
        command = install_command(data.get("version", ""), data.get("distribution", "amplicon"), data.get("environment_name"))
        job_id = uuid.uuid4().hex[:12]
        with INSTALL_LOCK:
            INSTALL_JOBS[job_id] = {"id": job_id, "status": "running", "command": command, "output": "正在创建 Conda 环境…"}

        def worker() -> None:
            try:
                process = subprocess.run(command, check=False, capture_output=True, text=True)
                output = "\n".join(value for value in (process.stdout, process.stderr) if value).strip()
                with INSTALL_LOCK:
                    INSTALL_JOBS[job_id].update({"status": "completed" if process.returncode == 0 else "failed", "returncode": process.returncode, "output": output[-5000:]})
            except Exception as exc:
                with INSTALL_LOCK:
                    INSTALL_JOBS[job_id].update({"status": "failed", "output": str(exc)})

        threading.Thread(target=worker, name=f"qiime2auto-install-{job_id}", daemon=True).start()
        self._send({"ok": True, "job_id": job_id}, HTTPStatus.ACCEPTED)

    def _start_figaro_install(self, data: dict) -> None:
        if os.name != "posix":
            raise ValueError("Figaro 一键安装只支持 Linux；请在 Linux + Conda 服务器上打开本页面")
        details = discover_environments(probe=False)
        if not details.get("conda_available"):
            raise ValueError(details.get("error") or "没有找到 conda")
        command = figaro_install_command(str(data.get("environment_name", "")).strip())
        job_id = uuid.uuid4().hex[:12]
        with INSTALL_LOCK:
            INSTALL_JOBS[job_id] = {"id": job_id, "kind": "figaro", "status": "running", "command": command, "output": "正在向选定的 Conda 环境安装 Figaro…"}

        def worker() -> None:
            try:
                process = subprocess.run(command, check=False, capture_output=True, text=True)
                output = "\n".join(value for value in (process.stdout, process.stderr) if value).strip()
                with INSTALL_LOCK:
                    INSTALL_JOBS[job_id].update({"status": "completed" if process.returncode == 0 else "failed", "returncode": process.returncode, "output": output[-5000:]})
            except Exception as exc:
                with INSTALL_LOCK:
                    INSTALL_JOBS[job_id].update({"status": "failed", "output": str(exc)})

        threading.Thread(target=worker, name=f"qiime2auto-figaro-{job_id}", daemon=True).start()
        self._send({"ok": True, "job_id": job_id}, HTTPStatus.ACCEPTED)

    def _start_classifier_download(self, data: dict) -> None:
        classifier_id = str(data.get("id", "")).strip()
        # Validate before creating a job, so a typo returns a useful response immediately.
        catalog = {item["id"] for item in classifier_catalog()}
        if classifier_id not in catalog:
            raise ValueError("未知的官方分类器")
        job_id = uuid.uuid4().hex[:12]
        with DOWNLOAD_LOCK:
            DOWNLOAD_JOBS[job_id] = {"id": job_id, "classifier_id": classifier_id, "status": "running", "downloaded": 0, "total": 0, "percent": 0, "path": None, "error": None}

        def progress(downloaded: int, total: int) -> None:
            with DOWNLOAD_LOCK:
                job = DOWNLOAD_JOBS[job_id]
                job.update({"downloaded": downloaded, "total": total, "percent": round(downloaded * 100 / total, 1) if total else 0})

        def worker() -> None:
            try:
                target = download_classifier(classifier_id, CLASSIFIER_ROOT, progress)
                with DOWNLOAD_LOCK:
                    DOWNLOAD_JOBS[job_id].update({"status": "completed", "path": target, "percent": 100})
            except Exception as exc:
                with DOWNLOAD_LOCK:
                    DOWNLOAD_JOBS[job_id].update({"status": "failed", "error": str(exc)})

        threading.Thread(target=worker, name=f"qiime2auto-classifier-{job_id}", daemon=True).start()
        self._send({"ok": True, "job_id": job_id}, HTTPStatus.ACCEPTED)

    def _start_job(self, data: dict) -> None:
        required = ["input_path", "output_dir", "data_type"]
        missing = [key for key in required if not data.get(key)]
        if missing:
            raise ValueError(f"缺少参数: {', '.join(missing)}")
        if os.name != "posix":
            raise ValueError("一键分析只支持 Linux；请在安装了 Conda + QIIME2 的 Linux 服务器上打开页面。")
        if not data.get("qiime_env"):
            raise ValueError("请先选择一个包含 QIIME2 的 Conda 环境。")
        environments = discover_environments(probe=True)
        selected_environment = next(
            (item for item in environments.get("environments", []) if item.get("name") == data["qiime_env"]),
            None,
        )
        if not selected_environment or not selected_environment.get("qiime_available"):
            raise ValueError("所选 Conda 环境当前没有可用的 QIIME2，请刷新环境列表后重新选择。")
        data["qiime_env_available"] = True
        input_path = Path(str(data["input_path"])).expanduser()
        metadata_path = Path(str(data.get("metadata"))).expanduser() if data.get("metadata") else None
        if not input_path.exists():
            raise ValueError(f"输入文件或目录不存在: {input_path}")
        if metadata_path and not metadata_path.is_file():
            raise ValueError(f"metadata 文件不存在: {metadata_path}")
        if not data.get("skip_taxonomy"):
            classifier_path = Path(str(data.get("classifier", ""))).expanduser()
            if not classifier_path.is_file():
                raise ValueError("物种分类已开启，但分类器文件不存在；请重新选择 .qza，或勾选跳过物种分类。")
        plan = build_preflight({**data, "platform": os.name})
        if not plan["can_run"]:
            message = "；".join(item["message"] for item in plan["blockers"])
            raise ValueError(f"启动前检查未通过：{message}")
        data["skip_diversity"] = plan["effective"]["skip_diversity"]
        data["skip_ancom"] = plan["effective"]["skip_ancom"]
        if data["skip_diversity"]:
            # A stale/invalid custom value must not prevent a run whose
            # diversity branch has already been auto-skipped.
            data["sampling_depth"] = "auto"
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
                    qiime_env=data.get("qiime_env"),
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
