#!/usr/bin/env python3
"""Serve a local-only PDF and LaTeX workspace for an Anything-to-Journal project."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import webbrowser
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, urlparse

from build import compile_tex, select_compiler


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
STATIC_DIR = SKILL_DIR / "assets" / "workspace"
EDITABLE_SUFFIXES = {".tex", ".bib"}
MAX_SOURCE_BYTES = 10 * 1024 * 1024
MAX_REQUEST_BYTES = 12 * 1024 * 1024
GENERATED_SUFFIXES = (
    ".aux", ".blg", ".log", ".out", ".toc", ".bcf", ".run.xml",
    ".synctex.gz", ".fls", ".fdb_latexmk", ".xdv",
)
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/assets/workspace.css": ("workspace.css", "text/css; charset=utf-8"),
    "/assets/workspace.js": ("workspace.js", "text/javascript; charset=utf-8"),
}


class WorkspaceError(Exception):
    """An error safe to return to the local workspace client."""


class ConflictError(WorkspaceError):
    """The on-disk file changed after the editor loaded it."""

    def __init__(self, message: str, current: dict[str, Any]) -> None:
        super().__init__(message)
        self.current = current


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_bytes(path: Path, content: bytes) -> None:
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def atomic_write_text(path: Path, content: str) -> None:
    atomic_write_bytes(path, content.encode("utf-8"))


def read_json_object(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def valid_pdf(path: Path) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    try:
        with path.open("rb") as handle:
            return handle.read(5) == b"%PDF-"
    except OSError:
        return False


def safe_project(raw_project: Path) -> Path:
    expanded = raw_project.expanduser()
    if expanded.is_symlink() or not expanded.is_dir():
        raise WorkspaceError(f"unsafe or missing journal-output directory: {expanded}")
    project = expanded.resolve()
    for name in ("manuscript", "reports", "submission"):
        root = project / name
        if root.is_symlink() or not root.is_dir() or root.resolve().parent != project:
            raise WorkspaceError(f"unsafe or missing project directory: {name}")
    main_file = project / "manuscript" / "manuscript.tex"
    if main_file.is_symlink() or not main_file.is_file():
        raise WorkspaceError("manuscript/manuscript.tex is missing or unsafe")
    if not STATIC_DIR.is_dir() or STATIC_DIR.is_symlink():
        raise WorkspaceError(f"workspace web assets are missing or unsafe: {STATIC_DIR}")
    return project


class ManuscriptWorkspace:
    """Own source synchronization, preview compilation, and readiness invalidation."""

    def __init__(self, project: Path, compiler: str = "auto", debounce: float = 0.65) -> None:
        self.project = project
        self.manuscript_dir = project / "manuscript"
        self.reports_dir = project / "reports"
        self.submission_dir = project / "submission"
        self.cache_dir = project / ".manuscript-workspace"
        if self.cache_dir.is_symlink() or (self.cache_dir.exists() and not self.cache_dir.is_dir()):
            raise WorkspaceError("unsafe .manuscript-workspace cache path")
        self.cache_dir.mkdir(mode=0o700, exist_ok=True)
        self.preview_pdf = self.cache_dir / "preview.pdf"
        self.state_file = self.cache_dir / "state.json"
        self.compiler = compiler
        self.debounce = debounce
        self.lock = threading.RLock()
        self.compile_timer: threading.Timer | None = None
        self.compiling = False
        self.compile_requested = False
        self.compile_status = "saved"
        self.compile_error = ""
        self.compiled_at: str | None = None
        self.pdf_version = sha256_file(self.preview_pdf) if valid_pdf(self.preview_pdf) else ""
        self.external_revision = 0
        self.last_changed_paths: list[str] = []
        self.invalidated = False
        self.closed = False
        self._snapshot = self._scan_sources()
        self._source_fingerprint = self._fingerprint(self._snapshot)
        cached_state = read_json_object(self.state_file)
        cached_fingerprint = cached_state.get("source_fingerprint")
        self.preview_source_fingerprint = (
            str(cached_fingerprint) if self.pdf_version and cached_fingerprint else ""
        )

        if not self.pdf_version:
            self._seed_preview()
        if self.pdf_version and not self.preview_source_fingerprint and self._formal_build_matches_sources():
            self.preview_source_fingerprint = self._source_fingerprint
        if self.pdf_version and self.preview_source_fingerprint == self._source_fingerprint:
            self._persist_state()
        else:
            if self.preview_source_fingerprint and self.preview_source_fingerprint != self._source_fingerprint:
                self._invalidate_formal_state("LaTeX sources changed outside the workspace")
            elif self._has_formal_build() and not self._formal_build_matches_sources():
                self._invalidate_formal_state("LaTeX sources no longer match the last formal build")
            self.schedule_compile(delay=0.05)

    def close(self) -> None:
        with self.lock:
            self.closed = True
            if self.compile_timer:
                self.compile_timer.cancel()

    def _source_path(self, relative: str, must_exist: bool = True) -> Path:
        if (
            not isinstance(relative, str)
            or not relative
            or relative != Path(relative).name
            or "/" in relative
            or "\\" in relative
            or Path(relative).suffix.lower() not in EDITABLE_SUFFIXES
            or any(ord(char) < 32 or ord(char) == 127 for char in relative)
        ):
            raise WorkspaceError("only flat .tex and .bib files inside manuscript/ are editable")
        path = self.manuscript_dir / relative
        if path.is_symlink():
            raise WorkspaceError("symbolic links are not allowed in the editor")
        if must_exist and not path.is_file():
            raise WorkspaceError(f"editable file does not exist: {relative}")
        if path.resolve(strict=False).parent != self.manuscript_dir.resolve():
            raise WorkspaceError("file path escapes manuscript/")
        return path

    def _file_record(self, path: Path) -> dict[str, Any]:
        stat = path.stat()
        return {
            "path": path.name,
            "sha256": sha256_file(path),
            "bytes": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "kind": path.suffix.lower().removeprefix("."),
        }

    def _scan_sources(self) -> dict[str, dict[str, Any]]:
        records: dict[str, dict[str, Any]] = {}
        for path in sorted(self.manuscript_dir.iterdir(), key=lambda value: value.name.casefold()):
            if path.suffix.lower() not in EDITABLE_SUFFIXES:
                continue
            if path.is_symlink():
                raise WorkspaceError(f"editable source is a symbolic link: {path.name}")
            if not path.is_file() or path.resolve().parent != self.manuscript_dir.resolve():
                raise WorkspaceError(f"editable source is unsafe: {path.name}")
            records[path.name] = self._file_record(path)
        if "manuscript.tex" not in records:
            raise WorkspaceError("manuscript/manuscript.tex is missing")
        return records

    @staticmethod
    def _fingerprint(snapshot: dict[str, dict[str, Any]]) -> str:
        digest = hashlib.sha256()
        for name in sorted(snapshot):
            digest.update(f"{name}\0{snapshot[name]['sha256']}\n".encode("utf-8"))
        return digest.hexdigest()

    def _seed_preview(self) -> None:
        candidates = (
            self.submission_dir / "manuscript.pdf",
            self.submission_dir / "submission.pdf",
            self.submission_dir / "DRAFT_NOT_FOR_SUBMISSION.pdf",
            self.manuscript_dir / "manuscript.pdf",
        )
        for candidate in candidates:
            if not valid_pdf(candidate):
                continue
            atomic_write_bytes(self.preview_pdf, candidate.read_bytes())
            self.pdf_version = sha256_file(self.preview_pdf)
            self.compiled_at = datetime.fromtimestamp(
                candidate.stat().st_mtime, tz=timezone.utc
            ).isoformat()
            return

    def _has_formal_build(self) -> bool:
        report = self.reports_dir / "build-report.json"
        return report.is_file() and not report.is_symlink()

    def _formal_build_matches_sources(self) -> bool:
        report = read_json_object(self.reports_dir / "build-report.json")
        hashes = report.get("source_sha256")
        if not isinstance(hashes, dict):
            return False
        return all(hashes.get(name) == record["sha256"] for name, record in self._snapshot.items())

    def _persist_state(self) -> None:
        payload = {
            "schema_version": "1.0",
            "source_fingerprint": self.preview_source_fingerprint or None,
            "pdf_sha256": self.pdf_version or None,
            "compiled_at": self.compiled_at,
            "compile_status": self.compile_status,
        }
        atomic_write_text(self.state_file, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    def _invalidate_formal_state(self, reason: str) -> None:
        """Revoke stale final/audit status without running the formal build pipeline."""
        now = datetime.now(timezone.utc).isoformat()
        changed = list(self.last_changed_paths)
        project_file = self.project / "project.json"
        project_state = read_json_object(project_file)
        project_state["submission_ready"] = False
        project_state["submission_pdf"] = None
        project_state["workspace_invalidated_at"] = now
        project_state["workspace_invalidation_reason"] = reason
        atomic_write_text(project_file, json.dumps(project_state, ensure_ascii=False, indent=2) + "\n")

        quality_path = self.reports_dir / "quality-report.json"
        if quality_path.is_file() and not quality_path.is_symlink():
            quality = read_json_object(quality_path)
            quality["submission_ready"] = False
            quality["draft_checks_passed"] = False
            quality["invalidated_at"] = now
            quality["invalidation_reason"] = reason
            atomic_write_text(quality_path, json.dumps(quality, ensure_ascii=False, indent=2) + "\n")
        quality_markdown = self.reports_dir / "quality-report.md"
        if quality_markdown.is_file() and not quality_markdown.is_symlink():
            old_text = quality_markdown.read_text(encoding="utf-8", errors="replace")
            marker = "<!-- manuscript-workspace-invalidated -->"
            if marker not in old_text:
                notice = (
                    f"{marker}\n> **INVALIDATED:** LaTeX sources changed at {now}. "
                    "Run `build.py` and `audit.py` again before relying on this report.\n\n"
                )
                atomic_write_text(quality_markdown, notice + old_text)

        visual_path = self.reports_dir / "visual-inspection.json"
        if visual_path.is_file() and not visual_path.is_symlink():
            visual = read_json_object(visual_path)
            visual["status"] = "pending"
            visual["reviewed_by"] = ""
            visual["reviewed_at"] = None
            visual["instructions"] = "LaTeX changed; rebuild and inspect every PDF page again."
            files = visual.get("files")
            if isinstance(files, list):
                for item in files:
                    if isinstance(item, dict):
                        item["status"] = "pending"
                        item["pages_inspected"] = []
            atomic_write_text(visual_path, json.dumps(visual, ensure_ascii=False, indent=2) + "\n")

        author_path = self.reports_dir / "author-decisions.json"
        if author_path.is_file() and not author_path.is_symlink():
            author = read_json_object(author_path)
            if author.get("status") == "verified":
                author["status"] = "pending"
                author["workspace_invalidated_at"] = now
                author["workspace_invalidation_reason"] = "Final source hashes changed; reconfirm approval."
                atomic_write_text(author_path, json.dumps(author, ensure_ascii=False, indent=2) + "\n")

        for name in (
            "submission.pdf", "DRAFT_NOT_FOR_SUBMISSION.pdf",
            "submission-package.zip", "submission-package.zip.sha256",
        ):
            stale = self.submission_dir / name
            if stale.is_file() or stale.is_symlink():
                stale.unlink()
        package_manifest = self.reports_dir / "submission-package-manifest.json"
        if package_manifest.is_file() or package_manifest.is_symlink():
            package_manifest.unlink()

        invalidation = {
            "schema_version": "1.0",
            "invalidated_at": now,
            "reason": reason,
            "changed_files": changed,
            "formal_recovery": [
                "python scripts/build.py <journal-output>",
                "python scripts/audit.py <journal-output> --require-pdf",
            ],
        }
        atomic_write_text(
            self.reports_dir / "workspace-invalidation.json",
            json.dumps(invalidation, ensure_ascii=False, indent=2) + "\n",
        )
        self.invalidated = True

    def refresh_external_changes(self) -> list[str]:
        with self.lock:
            current = self._scan_sources()
            names = sorted(set(self._snapshot) | set(current))
            changed = [
                name for name in names
                if self._snapshot.get(name, {}).get("sha256") != current.get(name, {}).get("sha256")
            ]
            if not changed:
                return []
            self._snapshot = current
            self._source_fingerprint = self._fingerprint(current)
            self.external_revision += 1
            self.last_changed_paths = changed
            self._invalidate_formal_state("LaTeX sources were modified outside the web editor")
            self.schedule_compile()
            return changed

    def list_files(self) -> list[dict[str, Any]]:
        self.refresh_external_changes()
        with self.lock:
            return [dict(self._snapshot[name]) for name in sorted(self._snapshot)]

    def read_file(self, relative: str) -> dict[str, Any]:
        self.refresh_external_changes()
        path = self._source_path(relative)
        if path.stat().st_size > MAX_SOURCE_BYTES:
            raise WorkspaceError(f"file is too large for the browser editor: {relative}")
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise WorkspaceError(f"file is not valid UTF-8: {relative}") from exc
        with self.lock:
            record = dict(self._snapshot[relative])
        record["content"] = content
        return record

    def save_file(self, relative: str, content: str, expected_sha256: str) -> dict[str, Any]:
        if not isinstance(content, str):
            raise WorkspaceError("content must be a UTF-8 string")
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_SOURCE_BYTES:
            raise WorkspaceError("file is too large for the browser editor")
        path = self._source_path(relative)
        with self.lock:
            current = self._file_record(path)
            if not isinstance(expected_sha256, str) or expected_sha256 != current["sha256"]:
                raise ConflictError(
                    "The file was modified outside the editor. Your unsaved text was not written.",
                    current,
                )
            changed = sha256_bytes(encoded) != current["sha256"]
            if changed:
                atomic_write_bytes(path, encoded)
            record = self._file_record(path)
            self._snapshot = self._scan_sources()
            self._source_fingerprint = self._fingerprint(self._snapshot)
            if changed:
                self.last_changed_paths = [relative]
                self._invalidate_formal_state("LaTeX sources were saved from the web editor")
            self.schedule_compile()
            return dict(record)

    def schedule_compile(self, delay: float | None = None) -> None:
        with self.lock:
            if self.closed:
                return
            self.compile_requested = True
            if self.compiling:
                return
            self.compile_status = "compiling"
            self.compile_error = ""
            if self.compile_timer:
                self.compile_timer.cancel()
            self.compile_timer = threading.Timer(
                self.debounce if delay is None else delay,
                self._compile_worker,
            )
            self.compile_timer.daemon = True
            self.compile_timer.start()

    def _copy_compile_tree(self, destination: Path) -> None:
        manuscript_root = self.manuscript_dir.resolve()
        for source in self.manuscript_dir.rglob("*"):
            relative = source.relative_to(self.manuscript_dir)
            if source.is_symlink():
                raise WorkspaceError(f"preview compile refuses symbolic link: {relative}")
            if source.is_dir():
                (destination / relative).mkdir(exist_ok=True)
                continue
            if not source.is_file() or manuscript_root not in source.resolve().parents:
                raise WorkspaceError(f"preview compile refuses unsafe file: {relative}")
            if any(source.name.endswith(suffix) for suffix in GENERATED_SUFFIXES):
                continue
            if source.suffix.lower() == ".pdf" and source.stem in {
                "manuscript", "supplement", "cover-letter"
            }:
                continue
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    def _run_preview_compile(self) -> tuple[bool, str, bytes | None]:
        selected = select_compiler(self.compiler)
        if not selected:
            return False, "No LaTeX compiler found. Install Tectonic or TeX Live.", None
        compiler_name, executable = selected
        temp_dir = Path(tempfile.mkdtemp(prefix=".preview-build-", dir=self.cache_dir))
        try:
            self._copy_compile_tree(temp_dir)
            result = compile_tex(compiler_name, executable, temp_dir / "manuscript.tex")
            pdf_path = temp_dir / "manuscript.pdf"
            if result.get("success") and valid_pdf(pdf_path):
                return True, "", pdf_path.read_bytes()
            details: list[str] = []
            for command in result.get("commands", []):
                if not isinstance(command, dict):
                    continue
                output = str(command.get("stderr") or command.get("stdout") or "").strip()
                if output:
                    details.append(output[-4000:])
            message = "\n\n".join(details).strip() or "LaTeX compilation failed without a diagnostic."
            return False, message, None
        except Exception as exc:
            return False, f"Preview compilation failed safely: {type(exc).__name__}: {exc}", None
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def _compile_worker(self) -> None:
        with self.lock:
            if self.closed or self.compiling:
                return
            self.compile_timer = None
            self.compiling = True
            self.compile_requested = False
            self.compile_status = "compiling"
            self.compile_error = ""
            compiled_source_fingerprint = self._source_fingerprint
        success, error, pdf_content = self._run_preview_compile()
        with self.lock:
            if success and pdf_content:
                atomic_write_bytes(self.preview_pdf, pdf_content)
                self.pdf_version = sha256_file(self.preview_pdf)
                self.preview_source_fingerprint = compiled_source_fingerprint
                self.compiled_at = datetime.now(timezone.utc).isoformat()
                self.compile_status = "saved"
                self.compile_error = ""
            else:
                self.compile_status = "compile_failed"
                self.compile_error = error[-8000:]
            self.compiling = False
            self._persist_state()
            rerun = self.compile_requested
        if rerun:
            self.schedule_compile()

    def recompile(self) -> None:
        self.refresh_external_changes()
        self.schedule_compile(delay=0.0)

    def state(self) -> dict[str, Any]:
        files = self.list_files()
        with self.lock:
            return {
                "files": files,
                "main_file": "manuscript.tex",
                "compile_status": self.compile_status,
                "compile_error": self.compile_error,
                "compiled_at": self.compiled_at,
                "pdf": {
                    "available": bool(self.pdf_version and valid_pdf(self.preview_pdf)),
                    "version": self.pdf_version,
                    "url": "/api/pdf" if self.pdf_version else None,
                },
                "external_revision": self.external_revision,
                "last_changed_paths": list(self.last_changed_paths),
                "formal_outputs_invalidated": self.invalidated,
            }


class WorkspaceHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address: tuple[str, int], workspace: ManuscriptWorkspace) -> None:
        self.workspace = workspace
        super().__init__(address, WorkspaceRequestHandler)


class WorkspaceRequestHandler(BaseHTTPRequestHandler):
    server: WorkspaceHTTPServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        if (
            len(args) >= 2
            and str(args[0]).startswith(("GET ", "HEAD "))
            and str(args[1]) in {"200", "206"}
        ):
            return
        sys.stderr.write(f"workspace: {self.address_string()} - {format % args}\n")

    def _host_allowed(self) -> bool:
        host = self.headers.get("Host", "")
        hostname = host.rsplit(":", 1)[0].strip("[]").lower()
        return hostname in {"127.0.0.1", "localhost"}

    def _origin_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        parsed = urlparse(origin)
        try:
            port = parsed.port
        except ValueError:
            return False
        return (
            parsed.scheme == "http"
            and parsed.hostname in {"127.0.0.1", "localhost"}
            and port == self.server.server_port
        )

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self'; connect-src 'self'; frame-src 'self'; object-src 'self'; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'self'",
        )

    def _send_bytes(
        self,
        status: int,
        content: bytes,
        content_type: str,
        extra_headers: dict[str, str] | None = None,
        head_only: bool = False,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self._security_headers()
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if not head_only:
            self.wfile.write(content)

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        content = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        self._send_bytes(status, content, "application/json; charset=utf-8")

    def _error(self, status: int, message: str, **extra: Any) -> None:
        self._send_json(status, {"error": message, **extra})

    def _request_json(self) -> dict[str, Any]:
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise WorkspaceError("Content-Type must be application/json")
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise WorkspaceError("invalid Content-Length") from exc
        if size <= 0 or size > MAX_REQUEST_BYTES:
            raise WorkspaceError("request body is empty or too large")
        try:
            value = json.loads(self.rfile.read(size).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WorkspaceError("request body must be valid UTF-8 JSON") from exc
        if not isinstance(value, dict):
            raise WorkspaceError("request JSON must be an object")
        return value

    def _mutation_allowed(self) -> bool:
        return self._origin_allowed() and self.headers.get("X-Workspace-Request") == "1"

    def _serve_static(self, path: str, head_only: bool = False) -> bool:
        record = STATIC_FILES.get(path)
        if not record:
            return False
        filename, content_type = record
        target = STATIC_DIR / filename
        if target.is_symlink() or not target.is_file() or target.resolve().parent != STATIC_DIR.resolve():
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "workspace asset is missing or unsafe")
            return True
        self._send_bytes(HTTPStatus.OK, target.read_bytes(), content_type, head_only=head_only)
        return True

    def _serve_pdf(self, head_only: bool = False) -> None:
        path = self.server.workspace.preview_pdf
        if not valid_pdf(path):
            self._error(HTTPStatus.NOT_FOUND, "No successful PDF preview is available yet.")
            return
        size = path.stat().st_size
        start, end = 0, size - 1
        status = HTTPStatus.OK
        range_header = self.headers.get("Range")
        if range_header:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
            if not match or (not match.group(1) and not match.group(2)):
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self._security_headers()
                self.end_headers()
                return
            if match.group(1):
                start = int(match.group(1))
                end = int(match.group(2)) if match.group(2) else size - 1
            else:
                suffix = int(match.group(2))
                start = max(0, size - suffix)
            end = min(end, size - 1)
            if start > end or start >= size:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self._security_headers()
                self.end_headers()
                return
            status = HTTPStatus.PARTIAL_CONTENT
        length = end - start + 1
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Disposition": 'inline; filename="manuscript.pdf"',
        }
        if status == HTTPStatus.PARTIAL_CONTENT:
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        self.send_response(status)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Length", str(length))
        self._security_headers()
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        if not head_only:
            with path.open("rb") as handle:
                handle.seek(start)
                remaining = length
                while remaining:
                    chunk = handle.read(min(65536, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)

    def do_HEAD(self) -> None:
        if not self._host_allowed():
            self._error(HTTPStatus.FORBIDDEN, "localhost Host header required")
            return
        parsed = urlparse(self.path)
        if self._serve_static(parsed.path, head_only=True):
            return
        if parsed.path == "/api/pdf":
            self._serve_pdf(head_only=True)
            return
        self._error(HTTPStatus.NOT_FOUND, "not found")

    def do_GET(self) -> None:
        if not self._host_allowed():
            self._error(HTTPStatus.FORBIDDEN, "localhost Host header required")
            return
        parsed = urlparse(self.path)
        if self._serve_static(parsed.path):
            return
        try:
            if parsed.path == "/api/state":
                self._send_json(HTTPStatus.OK, self.server.workspace.state())
                return
            if parsed.path == "/api/file":
                values = parse_qs(parsed.query, strict_parsing=True)
                relative = values.get("path", [""])[0]
                self._send_json(HTTPStatus.OK, self.server.workspace.read_file(relative))
                return
            if parsed.path == "/api/pdf":
                self._serve_pdf()
                return
        except ValueError:
            self._error(HTTPStatus.BAD_REQUEST, "invalid query string")
            return
        except WorkspaceError as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        self._error(HTTPStatus.NOT_FOUND, "not found")

    def do_PUT(self) -> None:
        if not self._host_allowed() or not self._mutation_allowed():
            self._error(HTTPStatus.FORBIDDEN, "local workspace request headers required")
            return
        parsed = urlparse(self.path)
        if parsed.path != "/api/file":
            self._error(HTTPStatus.NOT_FOUND, "not found")
            return
        try:
            values = parse_qs(parsed.query, strict_parsing=True)
            relative = values.get("path", [""])[0]
            payload = self._request_json()
            record = self.server.workspace.save_file(
                relative,
                payload.get("content"),
                payload.get("expected_sha256"),
            )
            self._send_json(
                HTTPStatus.OK,
                {"saved": True, "file": record, "compile_scheduled": True},
            )
        except ConflictError as exc:
            self._error(HTTPStatus.CONFLICT, str(exc), current=exc.current)
        except ValueError:
            self._error(HTTPStatus.BAD_REQUEST, "invalid query string")
        except WorkspaceError as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))

    def do_POST(self) -> None:
        if not self._host_allowed() or not self._mutation_allowed():
            self._error(HTTPStatus.FORBIDDEN, "local workspace request headers required")
            return
        parsed = urlparse(self.path)
        if parsed.path != "/api/recompile":
            self._error(HTTPStatus.NOT_FOUND, "not found")
            return
        try:
            self.server.workspace.recompile()
            self._send_json(HTTPStatus.ACCEPTED, {"compile_scheduled": True})
        except WorkspaceError as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Open a local PDF Preview | LaTeX workspace for journal-output"
    )
    parser.add_argument("project", type=Path, help="Prepared journal-output directory")
    parser.add_argument("--port", type=int, default=0, help="Local TCP port; 0 chooses a free port")
    parser.add_argument(
        "--compiler",
        choices=("auto", "latexmk", "tectonic", "xelatex", "pdflatex"),
        default="auto",
    )
    parser.add_argument(
        "--open-browser",
        action="store_true",
        help="Also ask the operating system to open the same localhost URL",
    )
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    if not 0 <= args.port <= 65535:
        print("workspace-editor: error: --port must be between 0 and 65535", file=sys.stderr)
        return 2
    try:
        project = safe_project(args.project)
        workspace = ManuscriptWorkspace(project, compiler=args.compiler)
        server = WorkspaceHTTPServer(("127.0.0.1", args.port), workspace)
    except (OSError, WorkspaceError) as exc:
        print(f"workspace-editor: error: {exc}", file=sys.stderr)
        return 2
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"Manuscript Workspace: {url}", flush=True)
    print(f"Editing: {project / 'manuscript' / 'manuscript.tex'}", flush=True)
    print("Press Ctrl+C to stop the local workspace.", flush=True)
    if args.open_browser:
        threading.Timer(0.2, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        workspace.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
