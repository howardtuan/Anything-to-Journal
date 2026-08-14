#!/usr/bin/env python3
"""Audit a prepared Anything-to-Journal project and gate submission.pdf."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import signal
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import zipfile
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from preflight import inventory_docx


CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
PLACEHOLDER_RE = re.compile(
    r"\[\[.*?\]\]|\b(?:TODO|TBD|FIXME|PLACEHOLDER|CITATION\s+NEEDED|INSERT\s+HERE)\b",
    re.I | re.S,
)
NUMBER_RE = re.compile(
    r"(?<![\w.])[-+−]?\d+(?:[,.]\d+)*(?:\s*(?:%|‰|°[CF]?|[eE][-+]?\d+))?(?![\w.])"
)
CITE_RE = re.compile(
    r"\\(?:cite(?:p|t|alp|alt|author|year|yearpar|num)?|parencite|textcite|autocite|footcite|supercite)\*?\s*"
    r"(?:\[[^\]]*\]\s*){0,2}\{([^{}]+)\}"
)
NOCITE_RE = re.compile(r"\\nocite\s*\{", re.I)
BIB_ENTRY_RE = re.compile(r"(?m)^\s*@(?:[A-Za-z]+)\s*\{\s*([^,\s]+)\s*,")
AUTHOR_DECISION_IDS = {
    "manuscript_title", "authors_and_order", "affiliations", "corresponding_author",
    "author_contributions", "funding", "conflicts_of_interest", "ethics_approval",
    "informed_consent", "data_availability", "prior_publication",
    "third_party_permissions", "ai_assistance_disclosure", "all_authors_approved",
}
AUTHOR_NA_ALLOWED = {
    "funding", "ethics_approval", "informed_consent", "prior_publication",
    "ai_assistance_disclosure",
}
AUTHOR_CONFIRM_REQUIRED = {
    "manuscript_title", "authors_and_order", "affiliations", "corresponding_author",
    "conflicts_of_interest", "data_availability", "third_party_permissions",
    "all_authors_approved",
}

WORKSPACE_AGGREGATE_METHOD = (
    "sha256-v1(source_id\\0original_relative_path\\0copied_file"
    "\\0file_sha256\\0bytes\\n)"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def workspace_aggregate(materials: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for item in sorted(materials, key=lambda value: str(value.get("source_id", ""))):
        record = (
            f"{item.get('source_id', '')}\0"
            f"{item.get('original_relative_path', '')}\0"
            f"{item.get('copied_file', '')}\0"
            f"{item.get('sha256', '')}\0"
            f"{item.get('bytes', '')}\n"
        )
        digest.update(record.encode("utf-8"))
    return digest.hexdigest()


def valid_iso_datetime(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def parse_iso_datetime(value: Any) -> datetime | None:
    """Return a timezone-aware ISO datetime, or None for untrusted input."""
    if not valid_iso_datetime(value):
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def valid_build_argv(compiler: str, argv: Any, tex_name: str) -> bool:
    if not isinstance(argv, list) or not argv or not all(isinstance(item, str) for item in argv):
        return False
    executable = Path(argv[0]).name.lower().removesuffix(".exe")
    args = argv[1:]
    if executable == "bibtex":
        return compiler in {"xelatex", "pdflatex"} and args == [Path(tex_name).stem]
    if compiler == "tectonic":
        return executable == "tectonic" and args == [
            "--keep-intermediates", "--keep-logs", "--synctex", "--untrusted", tex_name,
        ]
    if compiler == "latexmk":
        return executable == "latexmk" and args == [
            "-norc", "-xelatex", "-interaction=nonstopmode", "-halt-on-error",
            "-file-line-error", "-latexoption=-no-shell-escape", tex_name,
        ]
    if compiler in {"xelatex", "pdflatex"}:
        return executable == compiler and args == [
            "-no-shell-escape", "-interaction=nonstopmode", "-halt-on-error",
            "-file-line-error", tex_name,
        ]
    return False


def safe_flat_name(name: str) -> bool:
    return bool(
        name
        and name not in {".", ".."}
        and "/" not in name
        and "\\" not in name
        and ":" not in name
        and not name.startswith(("/", "\\"))
        and not any(ord(char) < 32 or ord(char) == 127 for char in name)
        and not name.endswith((" ", "."))
    )


def safe_project_roots(project: Path, names: Iterable[str]) -> tuple[bool, str]:
    for name in names:
        path = project / name
        if path.is_symlink() or not path.is_dir() or path.resolve().parent != project:
            return False, f"Unsafe or missing project directory: {name}"
    return True, ""


def atomic_write_text(path: Path, content: str) -> None:
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
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


def atomic_copy(source: Path, destination: Path) -> None:
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{destination.name}-", dir=destination.parent)
    os.close(descriptor)
    try:
        shutil.copyfile(source, temp_name)
        os.replace(temp_name, destination)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


PACKAGE_TRANSIENT_SUFFIXES = {
    ".aux", ".blg", ".log", ".out", ".toc", ".bcf", ".run.xml",
    ".synctex.gz", ".fls", ".fdb_latexmk", ".xdv",
}


def submission_package_files(project: Path) -> list[Path]:
    """Return the complete, relevant project payload without recursive/transient files."""
    candidates: list[Path] = []
    project_file = project / "project.json"
    if project_file.is_symlink() or not project_file.is_file():
        raise ValueError("project.json is missing or unsafe")
    candidates.append(project_file)
    for root_name in ("source", "manuscript", "reports", "submission"):
        root = project / root_name
        if root.is_symlink() or not root.is_dir() or root.resolve().parent != project:
            raise ValueError(f"Unsafe or missing project directory: {root_name}")
        for path in root.rglob("*"):
            if path.is_dir() and not path.is_symlink():
                continue
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"Complete package refuses non-regular entry: {path.relative_to(project)}")
            relative = path.relative_to(project)
            if any(not safe_flat_name(part) for part in relative.parts):
                raise ValueError(f"Complete package entry has a nonportable name: {relative}")
            if project.resolve() not in path.resolve().parents:
                raise ValueError(f"Complete package entry escapes project: {relative}")
            if relative.as_posix() in {
                "submission/submission-package.zip",
                "submission/submission-package.zip.sha256",
                "submission/.build-manifest.json",
            }:
                continue
            if path.name.startswith(".submission-package-") or path.name in {".DS_Store", "Thumbs.db"}:
                continue
            if root_name == "manuscript" and (
                any(path.name.endswith(suffix) for suffix in PACKAGE_TRANSIENT_SUFFIXES)
                or (path.suffix.lower() == ".pdf" and path.stem in {"manuscript", "supplement", "cover-letter"})
            ):
                continue
            candidates.append(path)
    return sorted(set(candidates), key=lambda path: path.relative_to(project).as_posix())


def build_submission_package(project: Path) -> dict[str, Any]:
    """Atomically create and verify the nested full-project distribution archive."""
    reports = project / "reports"
    submission = project / "submission"
    manifest_path = reports / "submission-package-manifest.json"
    payload = [path for path in submission_package_files(project) if path != manifest_path]
    entries = [
        {
            "path": path.relative_to(project).as_posix(),
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
        }
        for path in payload
    ]
    package_manifest = {
        "schema_version": "1.0",
        "created_at": datetime.now().astimezone().isoformat(),
        "package": "submission/submission-package.zip",
        "note": "Entries intentionally exclude this manifest itself and the package archive to avoid circular hashes.",
        "entries": entries,
    }
    atomic_write_text(
        manifest_path,
        json.dumps(package_manifest, ensure_ascii=False, indent=2) + "\n",
    )
    files = submission_package_files(project)
    archive_path = submission / "submission-package.zip"
    descriptor, temp_name = tempfile.mkstemp(prefix=".submission-package-", suffix=".zip", dir=submission)
    os.close(descriptor)
    temp_path = Path(temp_name)
    try:
        with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for path in files:
                archive.write(path, arcname=path.relative_to(project).as_posix())
        with zipfile.ZipFile(temp_path) as archive:
            names = archive.namelist()
            expected_names = [path.relative_to(project).as_posix() for path in files]
            if names != expected_names or len(names) != len(set(names)):
                raise ValueError("Complete package entry list changed during creation")
            for entry in entries:
                with archive.open(entry["path"]) as handle:
                    digest = hashlib.sha256()
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
                if digest.hexdigest() != entry["sha256"]:
                    raise ValueError(f"Complete package verification failed: {entry['path']}")
            if "reports/submission-package-manifest.json" not in names:
                raise ValueError("Complete package omits its manifest")
        os.replace(temp_path, archive_path)
    except Exception:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        raise
    checksum = sha256_file(archive_path)
    atomic_write_text(submission / "submission-package.zip.sha256", f"{checksum}  submission-package.zip\n")
    return {
        "path": "submission/submission-package.zip",
        "sha256": checksum,
        "files": len(files),
    }


def bounded_read_command(command: list[str], timeout: int = 15, limit: int = 12000) -> dict[str, Any]:
    try:
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=(os.name == "posix"),
        )
    except OSError as exc:
        return {"returncode": 127, "stdout": "", "stderr": str(exc)}
    tails = {"stdout": bytearray(), "stderr": bytearray()}

    def drain(stream: Any, key: str) -> None:
        try:
            while True:
                chunk = stream.read(65536)
                if not chunk:
                    return
                tails[key].extend(chunk)
                if len(tails[key]) > limit:
                    del tails[key][:-limit]
        finally:
            stream.close()

    threads = [
        threading.Thread(target=drain, args=(process.stdout, "stdout"), daemon=True),
        threading.Thread(target=drain, args=(process.stderr, "stderr"), daemon=True),
    ]
    for thread in threads:
        thread.start()
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except (ProcessLookupError, OSError):
            pass
        process.wait()
        returncode = 124
    for thread in threads:
        thread.join(timeout=5)
    return {
        "returncode": returncode,
        "stdout": tails["stdout"].decode("utf-8", errors="replace"),
        "stderr": tails["stderr"].decode("utf-8", errors="replace"),
    }


def strip_tex_comments(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        output: list[str] = []
        escaped = False
        for char in line:
            if char == "%" and not escaped:
                break
            output.append(char)
            if char == "\\":
                escaped = not escaped
            else:
                escaped = False
        lines.append("".join(output))
    return "\n".join(lines)


def latex_to_words(text: str) -> list[str]:
    text = strip_tex_comments(text)
    text = re.sub(r"\\(?:cite\w*|ref|eqref|label|url|href)\*?(?:\[[^\]]*\]){0,2}\{[^{}]*\}", " ", text)
    text = re.sub(r"\\[A-Za-z@]+\*?", " ", text)
    text = re.sub(r"[{}$&_^~\\]", " ", text)
    return re.findall(r"[A-Za-z]+(?:[-'’][A-Za-z]+)*|\d+(?:\.\d+)?", text)


def normalize_number(token: str) -> str:
    value = token.replace(" ", "").replace("−", "-")
    if re.fullmatch(r"[-+]?\d{1,3}(?:,\d{3})+(?:\.\d+)?(?:%|‰)?", value):
        value = value.replace(",", "")
    elif re.fullmatch(r"[-+]?\d+,\d+(?:%|‰)?", value):
        value = value.replace(",", ".", 1)
    return value


def environment_containing_label(text: str, source_id: str) -> str | None:
    label = re.compile(r"\\label\s*\{\s*" + re.escape(source_id) + r"\s*\}")
    candidates: list[str] = []
    for environment in ("table", "table*", "longtable"):
        pattern = re.compile(
            r"\\begin\{" + re.escape(environment) + r"\}.*?\\end\{" + re.escape(environment) + r"\}",
            re.S,
        )
        candidates.extend(match.group(0) for match in pattern.finditer(text) if label.search(match.group(0)))
    return min(candidates, key=len) if candidates else None


def table_data_text(block: str) -> str:
    for environment, begin_pattern in (
        ("tabularx", r"\\begin\{tabularx\}\{[^{}]*\}\{[^{}]*\}"),
        ("tabular", r"\\begin\{tabular\}\{[^{}]*\}"),
        ("longtable", r"\\begin\{longtable\}\{[^{}]*\}"),
    ):
        match = re.search(begin_pattern + r"(.*?)\\end\{" + environment + r"\}", block, re.S)
        if match:
            block = match.group(1)
            break
    block = re.sub(r"\\caption(?:\[[^\]]*\])?\{.*?\}", "", block, flags=re.S)
    block = re.sub(r"\\label\{[^{}]+\}", "", block)
    block = re.sub(r"\\(?:cmidrule|cline)(?:\([^)]*\))?\{[\d\s\-]+\}", "", block)
    block = re.sub(r"\\(?:multicolumn|multirow)\{\s*[-+]?\d+\s*\}", r"\\multicolumn", block)
    return block.replace(r"\%", "%").replace(r"\textminus", "-")


def sourcecell_instances(block: str) -> list[tuple[str, str]]:
    r"""Read balanced ``\sourcecell{id}{content}`` wrappers in source order."""
    instances: list[tuple[str, str]] = []
    pattern = re.compile(r"\\sourcecell\s*\{([^{}]+)\}\s*\{")
    for match in pattern.finditer(block):
        depth = 1
        index = match.end()
        start = index
        while index < len(block) and depth:
            char = block[index]
            backslashes = 0
            cursor = index - 1
            while cursor >= 0 and block[cursor] == "\\":
                backslashes += 1
                cursor -= 1
            escaped = backslashes % 2 == 1
            if not escaped and char == "{":
                depth += 1
            elif not escaped and char == "}":
                depth -= 1
            index += 1
        if depth == 0:
            instances.append((match.group(1).strip(), block[start : index - 1]))
    return instances


class Audit:
    def __init__(self, project: Path, require_pdf: bool, strict: bool) -> None:
        self.project = project
        self.require_pdf = require_pdf
        self.strict = strict
        self.findings: list[dict[str, str]] = []
        self.metrics: dict[str, Any] = {}
        self.expected_pdf_hashes: dict[str, str] = {}
        self.max_manuscript_pages = 19
        self.profile: dict[str, Any] = {}

    def add(self, severity: str, code: str, message: str, source_id: str = "") -> None:
        item = {"severity": severity, "code": code, "message": message}
        if source_id:
            item["source_id"] = source_id
        self.findings.append(item)

    def error(self, code: str, message: str, source_id: str = "") -> None:
        self.add("error", code, message, source_id)

    def warn(self, code: str, message: str, source_id: str = "") -> None:
        self.add("warning", code, message, source_id)

    def load_json(self, path: Path) -> Any:
        if path.is_symlink():
            self.error("unsafe-symlink", f"Required file must not be a symlink: {path.relative_to(self.project)}")
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self.error("missing-file", f"Required file is missing: {path.relative_to(self.project)}")
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            self.error("invalid-json", f"Cannot parse {path.relative_to(self.project)}: {exc}")
        return None

    def load_manifest(self) -> dict[str, Any] | None:
        return self.load_json(self.project / "source" / "source-manifest.json")

    def validate_manifest(self, value: Any) -> dict[str, Any] | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            self.error("invalid-manifest-schema", "source-manifest.json must contain a JSON object")
            return {}
        required_lists = (
            "warnings", "paragraphs", "figures", "tables", "objects", "equations", "footnotes",
            "endnotes", "comments", "bibliography_entries", "word_bibliography_sources",
            "citation_fields", "citation_candidates", "active_word_fields", "revision_markup",
        )
        if value.get("schema_version") != "1.0":
            self.error("invalid-manifest-schema", "source-manifest.json schema_version must be 1.0")
        source = value.get("source")
        if not isinstance(source, dict) or not re.fullmatch(r"[0-9a-f]{64}", str(source.get("sha256", ""))):
            self.error("invalid-manifest-schema", "source-manifest.json has no valid source SHA-256")
            value["source"] = source if isinstance(source, dict) else {}
        id_lists = set(required_lists) - {"warnings", "paragraphs"}
        for key in required_lists:
            if not isinstance(value.get(key), list):
                self.error("invalid-manifest-schema", f"source-manifest.json key must be a list: {key}")
                value[key] = []
                continue
            normalized_items: list[dict[str, Any]] = []
            for index, item in enumerate(value[key]):
                if not isinstance(item, dict):
                    self.error("invalid-manifest-schema", f"{key}[{index}] must be an object")
                    continue
                if key in id_lists and not isinstance(item.get("source_id"), str):
                    self.error("invalid-manifest-schema", f"{key}[{index}] has no source_id")
                    continue
                if key == "warnings" and not all(
                    isinstance(item.get(field), str) for field in ("code", "severity", "message")
                ):
                    self.error("invalid-manifest-schema", f"warnings[{index}] has invalid fields")
                    continue
                normalized_items.append(item)
            value[key] = normalized_items
        if isinstance(source, dict) and source.get("kind") == "workspace":
            materials = value.get("materials")
            if not isinstance(materials, list):
                self.error("invalid-manifest-schema", "Workspace source-manifest.json materials must be a list")
                value["materials"] = []
            else:
                if not materials:
                    self.error(
                        "empty-workspace-manifest",
                        "A workspace manifest must inventory at least one material",
                    )
                normalized_materials: list[dict[str, Any]] = []
                seen_ids: set[str] = set()
                seen_files: set[str] = set()
                for index, item in enumerate(materials, start=1):
                    if not isinstance(item, dict):
                        self.error("invalid-manifest-schema", f"materials[{index - 1}] must be an object")
                        continue
                    source_id = item.get("source_id")
                    copied_file = item.get("copied_file")
                    copied_path = item.get("copied_path")
                    stored_path = item.get("stored_path")
                    original_path = item.get("original_relative_path")
                    checksum = item.get("sha256")
                    byte_count = item.get("bytes")
                    expected_id = f"src-material-{index:04d}"
                    if source_id != expected_id or source_id in seen_ids:
                        self.error(
                            "invalid-workspace-material-id",
                            f"Workspace material {index} must use stable ID {expected_id}",
                        )
                        continue
                    if (
                        not isinstance(copied_file, str)
                        or not safe_flat_name(copied_file)
                        or copied_file in seen_files
                        or copied_path != f"source/materials/{copied_file}"
                        or stored_path != f"source/materials/{copied_file}"
                    ):
                        self.error("invalid-workspace-material-path", f"Invalid copied material path for {source_id}")
                        continue
                    if (
                        not isinstance(original_path, str)
                        or not original_path
                        or Path(original_path).is_absolute()
                        or any(not safe_flat_name(part) for part in Path(original_path).parts)
                    ):
                        self.error("invalid-workspace-material-path", f"Invalid original material path for {source_id}")
                        continue
                    if not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum):
                        self.error("invalid-workspace-material-hash", f"Invalid material SHA-256 for {source_id}")
                        continue
                    if not isinstance(byte_count, int) or isinstance(byte_count, bool) or byte_count < 0:
                        self.error("invalid-workspace-material-size", f"Invalid material size for {source_id}")
                        continue
                    if not isinstance(item.get("media_type"), str) or not item.get("media_type", "").strip():
                        self.error("invalid-workspace-material-type", f"Invalid media type for {source_id}")
                        continue
                    if not isinstance(item.get("extension"), str) or not re.fullmatch(
                        r"(?:|\.[a-z0-9]{1,12})", item.get("extension", "")
                    ):
                        self.error("invalid-workspace-material-type", f"Invalid extension for {source_id}")
                        continue
                    if not isinstance(item.get("role_hint"), str) or not item.get("role_hint", "").strip():
                        self.error("invalid-workspace-material-type", f"Invalid role hint for {source_id}")
                        continue
                    seen_ids.add(source_id)
                    seen_files.add(copied_file)
                    normalized_materials.append(item)
                value["materials"] = normalized_materials
                counts = value.get("counts")
                if not isinstance(counts, dict) or counts.get("materials") != len(materials):
                    self.error(
                        "workspace-material-count-mismatch",
                        "counts.materials must match the workspace material list",
                    )
                if source.get("material_count") != len(materials):
                    self.error(
                        "workspace-material-count-mismatch",
                        "source.material_count must match the workspace material list",
                    )
        return value

    def audit_format_decision(self, manifest: dict[str, Any]) -> None:
        """Require the user's venue-or-draft choice made before project preparation."""
        profile = self.load_json(self.project / "manuscript" / "journal-profile.json")
        decision = self.load_json(self.project / "reports" / "format-decision.json")
        if not isinstance(profile, dict):
            self.error("invalid-journal-profile", "journal-profile.json must contain a JSON object")
            profile = {}
        self.profile = profile
        configured_limit = profile.get("max_manuscript_pages")
        if (
            isinstance(configured_limit, int)
            and not isinstance(configured_limit, bool)
            and 1 <= configured_limit <= 19
        ):
            self.max_manuscript_pages = configured_limit
        if not isinstance(decision, dict) or decision.get("schema_version") != "1.0":
            self.error(
                "unconfirmed-format-decision",
                "format-decision.json must be a schema 1.0 confirmation recorded before preparation",
            )
            return
        mode = decision.get("format_mode")
        if decision.get("status") != "confirmed" or mode not in {"target", "draft-only"}:
            self.error(
                "unconfirmed-format-decision",
                "The user must explicitly confirm a target venue or draft-only mode before work starts",
            )
        if decision.get("confirmation_phase") != "before-source-access":
            self.error(
                "unconfirmed-format-decision",
                "Format confirmation must record confirmation_phase=before-source-access",
            )
        if decision.get("source_sha256") != manifest.get("source", {}).get("sha256"):
            self.error("stale-format-decision", "Format confirmation does not match the source DOCX")
        if not isinstance(decision.get("confirmed_by"), str) or not decision.get("confirmed_by", "").strip():
            self.error("unconfirmed-format-decision", "Format confirmation needs confirmed_by")
        confirmed_at = parse_iso_datetime(decision.get("confirmed_at"))
        if confirmed_at is None:
            self.error("unconfirmed-format-decision", "Format confirmation needs a timezone-aware confirmed_at")
        elif confirmed_at > datetime.now(timezone.utc) + timedelta(minutes=5):
            self.error("invalid-format-chronology", "Format confirmation cannot be dated in the future")
        tooling_path = self.project / "source" / "tooling.json"
        if tooling_path.is_symlink() or not tooling_path.is_file():
            self.error(
                "invalid-format-chronology",
                "A regular source/tooling.json is required to prove when preparation began",
            )
        else:
            tooling = self.load_json(tooling_path)
            prepared_at = parse_iso_datetime(tooling.get("prepared_at")) if isinstance(tooling, dict) else None
            if prepared_at is None:
                self.error("invalid-format-chronology", "source/tooling.json needs a timezone-aware prepared_at")
            elif prepared_at > datetime.now(timezone.utc) + timedelta(minutes=5):
                self.error("invalid-format-chronology", "Source preparation cannot be dated in the future")
            elif confirmed_at is not None and confirmed_at > prepared_at:
                self.error(
                    "invalid-format-chronology",
                    "Format confirmation must precede source preparation",
                )
        if not isinstance(decision.get("confirmation_note"), str) or not decision.get("confirmation_note", "").strip():
            self.error("unconfirmed-format-decision", "Format confirmation needs the user's choice in confirmation_note")
        if profile.get("format_mode") != mode:
            self.error("format-profile-mismatch", "journal-profile.json disagrees with format-decision.json")

        guidance = decision.get("format_guidance")
        if not isinstance(guidance, list) or profile.get("format_guidance") != guidance:
            self.error("format-profile-mismatch", "Format guidance must be an identical list in the decision and profile")
            guidance = []
        valid_guidance = 0
        for index, item in enumerate(guidance, start=1):
            if not isinstance(item, dict):
                self.error("invalid-format-guidance", f"Format guidance {index} is not an object")
                continue
            if item.get("kind") == "official-url":
                if not re.fullmatch(r"https://[^\s]+", str(item.get("url", ""))):
                    self.error("invalid-format-guidance", f"Format guidance {index} has no official HTTPS URL")
                else:
                    valid_guidance += 1
            elif item.get("kind") == "uploaded-file":
                relative_value = item.get("path")
                checksum = item.get("sha256")
                relative = Path(relative_value) if isinstance(relative_value, str) else Path()
                candidate = self.project / relative
                if (
                    not relative_value
                    or relative.is_absolute()
                    or len(relative.parts) < 3
                    or relative.parts[:2] != ("source", "format-guidance")
                    or candidate.is_symlink()
                    or not candidate.is_file()
                    or self.project.resolve() not in candidate.resolve().parents
                    or not isinstance(checksum, str)
                    or not re.fullmatch(r"[0-9a-f]{64}", checksum)
                    or sha256_file(candidate) != checksum
                ):
                    self.error("invalid-format-guidance", f"Uploaded format guidance {index} is missing or changed")
                else:
                    valid_guidance += 1
            else:
                self.error("invalid-format-guidance", f"Format guidance {index} has an unsupported kind")

        if mode == "draft-only":
            if any(
                decision.get(key) not in {None, ""}
                for key in ("venue_type", "target_venue", "official_guide_url")
            ) or guidance:
                self.error("invalid-draft-format-decision", "Draft-only mode must not name target guidance")
            if profile.get("profile") != "generic-imrad-num":
                self.error("invalid-generic-profile", "Draft-only mode must use generic-imrad-num")
            if any(
                profile.get(key) not in {None, ""}
                for key in ("venue_type", "target_venue", "target_journal", "target_conference")
            ):
                self.error("format-profile-mismatch", "Draft-only profile must not name a target venue")
        elif mode == "target":
            if decision.get("venue_type") not in {"journal", "conference"}:
                self.error("unconfirmed-format-decision", "A target needs venue_type journal or conference")
            if not isinstance(decision.get("target_venue"), str) or not decision.get("target_venue", "").strip():
                self.error("unconfirmed-format-decision", "A target needs the confirmed venue name")
            if profile.get("venue_type") != decision.get("venue_type") or profile.get("target_venue") != decision.get("target_venue"):
                self.error("format-profile-mismatch", "The profile target differs from the confirmed target")
            venue_type = decision.get("venue_type")
            expected_journal = decision.get("target_venue") if venue_type == "journal" else None
            expected_conference = decision.get("target_venue") if venue_type == "conference" else None
            if profile.get("target_journal") != expected_journal or profile.get("target_conference") != expected_conference:
                self.error("format-profile-mismatch", "Journal/conference profile fields disagree with venue_type")
            if profile.get("official_guide_url") != decision.get("official_guide_url"):
                self.error("format-profile-mismatch", "Official guide URL differs from the confirmed format decision")
            if valid_guidance < 1:
                self.error("missing-format-guidance", "A target needs an uploaded guide/template or official HTTPS instructions")

        self.metrics["format_mode"] = mode

    def audit_manifest_provenance(self, manifest: dict[str, Any]) -> None:
        """Re-inventory the immutable DOCX and compare preservation evidence.

        A well-shaped JSON skeleton is not evidence.  Recomputing at the final
        gate binds all expected ledger items to the actual Word package.
        """
        recorded_source = manifest.get("source", {})
        if isinstance(recorded_source, dict) and recorded_source.get("kind") == "workspace":
            self.audit_workspace_provenance(manifest)
            return
        bundled = self.project / "source" / "original.docx"
        external_value = recorded_source.get("absolute_path", "") if isinstance(recorded_source, dict) else ""
        external = Path(external_value).expanduser() if isinstance(external_value, str) and external_value else None
        source_docx = bundled if bundled.is_file() else external
        if source_docx is None or not source_docx.is_file():
            self.error(
                "missing-source-docx",
                "Final audit needs source/original.docx or the still-accessible recorded source path",
            )
            return
        if source_docx.is_symlink():
            self.error("unsafe-source-docx", "The source DOCX used for final audit must not be a symlink")
            return
        actual_hash = sha256_file(source_docx)
        if recorded_source.get("sha256") != actual_hash:
            self.error("source-docx-hash-mismatch", "The source DOCX does not match the manifest hash")
            return
        try:
            recomputed = inventory_docx(source_docx)
        except (FileNotFoundError, ValueError, zipfile.BadZipFile) as exc:
            self.error("source-reinventory-failed", f"Could not re-inventory the source DOCX: {exc}")
            return
        canonical_keys = (
            "schema_version", "counts", "package_parts", "outline", "paragraphs", "figures",
            "tables", "objects", "equations", "footnotes", "endnotes", "comments",
            "bibliography_entries", "word_bibliography_sources", "citation_fields",
            "citation_candidates", "custom_xml_reference_managers", "header_footer_stories",
            "active_word_fields", "revision_markup", "warnings",
        )
        for key in canonical_keys:
            recorded = manifest.get(key)
            current = recomputed.get(key)
            if recorded != current:
                self.error(
                    "manifest-reinventory-mismatch",
                    f"source-manifest.json differs from a fresh DOCX inventory at key: {key}",
                )
        self.metrics["source_docx_sha256"] = actual_hash

    def audit_workspace_provenance(self, manifest: dict[str, Any]) -> None:
        recorded_source = manifest.get("source", {})
        materials = manifest.get("materials", [])
        materials_dir = self.project / "source" / "materials"
        if (
            materials_dir.is_symlink()
            or not materials_dir.is_dir()
            or materials_dir.resolve().parent != (self.project / "source").resolve()
        ):
            self.error("unsafe-workspace-materials", "source/materials must be a regular project directory")
            return

        expected_files: set[str] = set()
        verified_materials: list[dict[str, Any]] = []
        for item in materials if isinstance(materials, list) else []:
            if not isinstance(item, dict):
                continue
            source_id = str(item.get("source_id", ""))
            copied_file = item.get("copied_file")
            if not isinstance(copied_file, str) or not safe_flat_name(copied_file):
                continue
            expected_files.add(copied_file)
            material = materials_dir / copied_file
            if material.is_symlink() or not material.is_file() or material.parent != materials_dir:
                self.error("missing-workspace-material", f"Copied material is missing or unsafe: {copied_file}", source_id)
                continue
            actual_hash = sha256_file(material)
            if actual_hash != item.get("sha256"):
                self.error("workspace-material-hash-mismatch", "Copied material changed after intake", source_id)
            try:
                actual_bytes = material.stat().st_size
            except OSError:
                actual_bytes = -1
            if actual_bytes != item.get("bytes"):
                self.error("workspace-material-size-mismatch", "Copied material size changed after intake", source_id)
            verified_materials.append({**item, "sha256": actual_hash, "bytes": actual_bytes})

        actual_files: set[str] = set()
        for path in materials_dir.iterdir():
            if path.is_symlink() or not path.is_file() or not safe_flat_name(path.name):
                self.error("unsafe-workspace-material", f"Unexpected non-regular material entry: {path.name}")
                continue
            actual_files.add(path.name)
        for name in sorted(expected_files - actual_files):
            self.error("missing-workspace-material", f"Manifest material is missing: {name}")
        for name in sorted(actual_files - expected_files):
            self.error("unexpected-workspace-material", f"Uninventoried copied material is present: {name}")

        aggregate = workspace_aggregate(verified_materials)
        if recorded_source.get("aggregate_method") != WORKSPACE_AGGREGATE_METHOD:
            self.error("invalid-workspace-aggregate", "Workspace aggregate method is missing or unsupported")
        if recorded_source.get("aggregate_sha256") != aggregate or recorded_source.get("sha256") != aggregate:
            self.error("workspace-aggregate-mismatch", "Workspace aggregate SHA-256 does not match copied materials")
        if recorded_source.get("material_count") != len(materials):
            self.error("workspace-material-count-mismatch", "Workspace material count does not match the manifest")
        self.metrics["workspace_materials"] = len(materials)
        self.metrics["workspace_aggregate_sha256"] = aggregate

    def load_source_recovery(self, manifest: dict[str, Any]) -> dict[str, Any]:
        error_codes = {
            item.get("code") for item in manifest.get("warnings", [])
            if isinstance(item.get("code"), str)
            and item.get("code") in {"bibliography-not-detected", "citations-not-detected"}
        }
        result: dict[str, Any] = {
            "bibliography_entries": [],
            "citation_fields": [],
            "resolved_codes": set(),
        }
        if not error_codes:
            return result
        report = self.load_json(self.project / "reports" / "source-recovery.json")
        if not isinstance(report, dict) or report.get("schema_version") != "1.0":
            self.error("invalid-source-recovery", "source-recovery.json must use schema_version 1.0")
            return result
        if report.get("source_sha256") != manifest.get("source", {}).get("sha256"):
            self.error("stale-source-recovery", "Source recovery does not match the DOCX hash")
        render_review = self.load_json(self.project / "reports" / "source-render-review.json")
        if not isinstance(render_review, dict) or report.get("source_render_sha256") != render_review.get("render_sha256"):
            self.error("stale-source-recovery", "Source recovery does not match the reviewed source render")
        render_pages = render_review.get("page_count") if isinstance(render_review, dict) else None
        if not isinstance(render_pages, int) or isinstance(render_pages, bool) or render_pages < 1:
            self.error("invalid-source-recovery", "Source recovery requires a valid reviewed source-render page count")
            render_pages = 0

        sections = (
            ("bibliography-not-detected", "bibliography", "records", "bibliography_entries", "src-manual-ref-"),
            ("citations-not-detected", "citations", "occurrences", "citation_fields", "src-manual-cite-"),
        )
        seen_ids: set[str] = set()
        for code, section_name, list_name, output_name, prefix in sections:
            if code not in error_codes:
                continue
            section = report.get(section_name)
            if not isinstance(section, dict) or section.get("status") != "verified":
                self.error("source-recovery-pending", f"{section_name} detector miss has no verified recovery")
                continue
            outcome = section.get("outcome")
            items = section.get(list_name, [])
            if outcome not in {"recovered", "confirmed-absent"} or not isinstance(items, list):
                self.error("invalid-source-recovery", f"Invalid {section_name} recovery outcome/items")
                continue
            evidence = str(section.get("evidence", "")).strip()
            reviewer = str(section.get("reviewed_by", "")).strip()
            reviewed_at = section.get("reviewed_at")
            inspected = section.get("pages_inspected")
            if not evidence or not reviewer or not valid_iso_datetime(reviewed_at):
                self.error(
                    "invalid-source-recovery-attestation",
                    f"{section_name} recovery needs evidence, reviewed_by, and a timezone-aware reviewed_at",
                )
                continue
            if (
                not isinstance(inspected, list)
                or not all(isinstance(page, int) and not isinstance(page, bool) for page in inspected)
                or sorted(set(inspected)) != list(range(1, render_pages + 1))
            ):
                self.error(
                    "invalid-source-recovery-pages",
                    f"{section_name} recovery must attest inspection of every source-render page",
                )
                continue
            if outcome == "confirmed-absent":
                if items:
                    self.error("invalid-source-recovery", f"Confirmed-absent {section_name} needs evidence and no items")
                    continue
            elif not items:
                self.error("invalid-source-recovery", f"Recovered {section_name} must list source items")
                continue
            valid_items: list[dict[str, Any]] = []
            for index, item in enumerate(items):
                if not isinstance(item, dict):
                    self.error("invalid-source-recovery", f"{section_name} item {index} is not an object")
                    continue
                source_id = str(item.get("source_id", ""))
                locator = str(item.get("source_locator", ""))
                source_text = str(item.get("source_text", ""))
                locator_match = re.match(r"^source/source-render\.pdf:page:(\d+)(?::|$)", locator)
                page = int(locator_match.group(1)) if locator_match else 0
                expected_hash = hashlib.sha256(f"{locator}\0{source_text}".encode("utf-8")).hexdigest()
                if (
                    not source_id.startswith(prefix)
                    or source_id in seen_ids
                    or not locator
                    or not source_text
                    or page < 1
                    or page > render_pages
                    or item.get("sha256") != expected_hash
                ):
                    self.error("invalid-source-recovery-item", f"Invalid recovered item: {source_id or index}")
                    continue
                seen_ids.add(source_id)
                valid_items.append(
                    {
                        "source_id": source_id,
                        "source_locator": locator,
                        "text": source_text,
                        "display_text": source_text,
                        "sha256": expected_hash,
                    }
                )
            if outcome == "recovered" and not valid_items:
                continue
            result[output_name].extend(valid_items)
            result["resolved_codes"].add(code)
        return result

    def read_ledger(self) -> list[dict[str, str]]:
        path = self.project / "manuscript" / "traceability.csv"
        if path.is_symlink():
            self.error("unsafe-ledger-symlink", "manuscript/traceability.csv must not be a symlink")
            return []
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            for row_index, row in enumerate(rows, start=2):
                for field, value in row.items():
                    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
                        self.error(
                            "unsafe-csv-formula",
                            f"traceability.csv row {row_index} field {field} needs a leading apostrophe",
                        )
            return rows
        except FileNotFoundError:
            self.error("missing-ledger", "manuscript/traceability.csv is missing")
        except UnicodeDecodeError as exc:
            self.error("invalid-ledger", f"Cannot read traceability.csv: {exc}")
        return []

    def tex_files(self) -> list[Path]:
        manuscript = self.project / "manuscript"
        if not manuscript.is_dir():
            return []
        files: list[Path] = []
        for path in manuscript.rglob("*.tex"):
            if "optional" in path.relative_to(manuscript).parts:
                continue
            if path.is_symlink():
                self.error("unsafe-tex-symlink", f"TeX source must not be a symlink: {path.name}")
                continue
            files.append(path)
        return sorted(files)

    def compiled_tex_files(self) -> list[Path]:
        manuscript = (self.project / "manuscript").resolve()
        queue = [manuscript / "manuscript.tex"]
        supplement = manuscript / "supplement.tex"
        if supplement.is_file():
            queue.append(supplement)
        seen: set[Path] = set()
        while queue:
            path = queue.pop(0).resolve()
            if path in seen:
                continue
            if manuscript not in path.parents and path != manuscript:
                self.error("unsafe-tex-input", f"TeX input escapes manuscript directory: {path}")
                continue
            if not path.is_file():
                self.error("missing-tex-input", f"Referenced TeX file is missing: {path.name}")
                continue
            seen.add(path)
            text = strip_tex_comments(path.read_text(encoding="utf-8", errors="replace"))
            for name in re.findall(r"\\(?:input|include)\s*\{([^{}]+)\}", text):
                candidate = Path(name.strip())
                if not candidate.suffix:
                    candidate = candidate.with_suffix(".tex")
                queue.append(path.parent / candidate)
        return sorted(seen)

    def combined_tex(self, files: list[Path]) -> str:
        chunks: list[str] = []
        for path in files:
            try:
                chunks.append(f"\n% FILE {path.name}\n{path.read_text(encoding='utf-8')}")
            except UnicodeDecodeError as exc:
                self.error("invalid-tex-encoding", f"{path.name} is not UTF-8: {exc}")
        return "\n".join(chunks)

    def audit_source_preflight(
        self,
        manifest: dict[str, Any],
        rows: list[dict[str, str]],
        resolved_recovery_codes: set[str] | None = None,
    ) -> None:
        errors = [item for item in manifest.get("warnings", []) if item.get("severity") == "error"]
        self.metrics["source_preflight_errors"] = len(errors)
        resolved_render_ids = {
            row.get("source_id", "")
            for row in rows
            if row.get("status", "").strip().lower() == "verified"
            and row.get("operation", "").strip().lower() in {"rendered", "translated_derivative", "transcoded"}
        }
        resolved_citation_ids = {
            row.get("source_id", "")
            for row in rows
            if row.get("kind") == "citation"
            and row.get("status", "").strip().lower() == "verified"
            and row.get("output_id", "").strip()
        }
        resolved_object_ids = {
            row.get("source_id", "")
            for row in rows
            if row.get("kind") == "object"
            and (
                row.get("status", "").strip().lower() == "verified"
                or (
                    row.get("status", "").strip().lower() == "preserved-supporting-data"
                    and row.get("notes", "").strip()
                )
                or (
                    row.get("status", "").strip().lower() == "not-research-content"
                    and row.get("notes", "").strip()
                )
            )
        }
        for item in errors:
            if item.get("code") in (resolved_recovery_codes or set()):
                continue
            if item.get("code") in {"native-chart-render-required", "native-diagram-render-required"} and item.get("source_id") in resolved_render_ids:
                continue
            if item.get("code") == "citation-field-incomplete" and item.get("source_id") in resolved_citation_ids:
                continue
            if item.get("code") == "native-object-review-required" and item.get("source_id") in resolved_object_ids:
                continue
            self.error(
                f"source-{item.get('code', 'preflight')}",
                item.get("message", "Source preflight error"),
                item.get("source_id", ""),
            )

    def audit_ledger(
        self,
        manifest: dict[str, Any],
        rows: list[dict[str, str]],
        recovery: dict[str, Any] | None = None,
        compiled_files: list[Path] | None = None,
    ) -> None:
        by_id: dict[str, list[dict[str, str]]] = {}
        for row in rows:
            source_id = row.get("source_id", "").strip()
            if source_id:
                by_id.setdefault(source_id, []).append(row)
        expected: dict[str, str] = {}
        expected_items: dict[str, dict[str, Any]] = {}
        compiled_names = {path.name for path in (compiled_files or [])}
        groups = {
            "material": "materials",
            "figure": "figures",
            "table": "tables",
            "object": "objects",
            "equation": "equations",
            "footnote": "footnotes",
            "endnote": "endnotes",
            "comment": "comments",
            "bibliography": "bibliography_entries",
            "bibliography-metadata": "word_bibliography_sources",
            "citation": "citation_fields",
            "citation-candidate": "citation_candidates",
        }
        for kind, key in groups.items():
            for item in manifest.get(key, []):
                expected[item["source_id"]] = kind
                expected_items[item["source_id"]] = item
        for kind, key in (("bibliography", "bibliography_entries"), ("citation", "citation_fields")):
            for item in (recovery or {}).get(key, []):
                expected[item["source_id"]] = kind
                expected_items[item["source_id"]] = item

        for source_id, kind in expected.items():
            matches = by_id.get(source_id, [])
            if not matches:
                self.error("missing-trace-row", f"No traceability row for {kind}", source_id)
                continue
            if len(matches) > 1:
                self.error("duplicate-trace-row", f"Traceability has {len(matches)} rows", source_id)
            row = matches[0]
            actual_kind = row.get("kind", "").strip()
            if actual_kind != kind:
                self.error(
                    "trace-kind-mismatch",
                    f"Traceability kind is {actual_kind or '(blank)'}, expected {kind}",
                    source_id,
                )
            expected_sha = str(expected_items[source_id].get("sha256", ""))
            if row.get("source_sha256", "").strip() != expected_sha:
                self.error(
                    "trace-source-hash-mismatch",
                    "Traceability source_sha256 does not match the immutable manifest item",
                    source_id,
                )
            status = row.get("status", "").strip().lower()
            if kind == "material" and status == "not-used":
                if not row.get("notes", "").strip():
                    self.error(
                        "unexplained-unused-material",
                        "A not-used material needs a concrete exclusion rationale in notes",
                        source_id,
                    )
                continue
            if kind == "material" and status == "verified":
                operation = row.get("operation", "").strip().lower()
                if not operation or operation in {"pending", "review-required"}:
                    self.error(
                        "invalid-material-operation",
                        "A verified material needs a concrete operation",
                        source_id,
                    )
            if kind == "citation-candidate" and status == "not-a-citation":
                if not row.get("notes", "").strip():
                    self.error("unexplained-not-a-citation", "A rejected citation candidate needs evidence in notes", source_id)
                continue
            if kind in {"bibliography", "bibliography-metadata"} and status == "bibliography-only":
                if not row.get("notes", "").strip():
                    self.error("unexplained-bibliography-only", "A bibliography-only record needs evidence in notes", source_id)
                continue
            if kind == "object" and status == "not-research-content":
                if not row.get("notes", "").strip():
                    self.error("unexplained-nonresearch-object", "A non-research object needs classification evidence in notes", source_id)
                continue
            if kind == "object" and status == "preserved-supporting-data":
                if expected_items[source_id].get("kind") != "embedded-package":
                    self.error("invalid-supporting-data-status", "Only embedded-package objects may use preserved-supporting-data", source_id)
                if not row.get("notes", "").strip():
                    self.error("unexplained-supporting-data", "Preserved embedded data needs provenance/classification notes", source_id)
                output_asset = row.get("output_asset", "").strip()
                asset = self.project / "manuscript" / output_asset
                if (
                    not output_asset
                    or not safe_flat_name(output_asset)
                    or not asset.is_file()
                    or asset.is_symlink()
                ):
                    self.error(
                        "missing-supporting-data-asset",
                        "Preserved embedded data must name an existing flat, non-symlink output_asset",
                        source_id,
                    )
                elif sha256_file(asset) != expected_items[source_id].get("sha256"):
                    self.error(
                        "supporting-data-hash-mismatch",
                        "Preserved embedded data does not match the immutable DOCX package payload",
                        source_id,
                    )
                continue
            if kind == "comment" and status == "not-research-content":
                if not row.get("notes", "").strip():
                    self.error("unexplained-nonresearch-comment", "A non-research comment needs resolution evidence in notes", source_id)
                continue
            if status != "verified":
                self.error("unverified-trace-row", f"Traceability status is {status or '(blank)'}, expected verified", source_id)
            output_file = row.get("output_file", "").strip()
            if not output_file:
                self.error("missing-trace-target", "Traceability output_file is blank", source_id)
            else:
                target = self.project / "manuscript" / output_file
                if not safe_flat_name(output_file) or not target.is_file() or target.is_symlink():
                    self.error("invalid-trace-target", f"Traceability target does not exist as a flat manuscript file: {output_file}", source_id)
                elif kind == "material" and output_file not in compiled_names:
                    self.error(
                        "trace-target-not-compiled",
                        f"Workspace material output_file is not compiled: {output_file}",
                        source_id,
                    )
        for source_id in sorted(set(by_id) - set(expected)):
            self.error("unknown-trace-row", "Traceability row has no matching source inventory item", source_id)
        self.metrics["trace_rows"] = len(rows)
        self.metrics["expected_trace_rows"] = len(expected)

    def audit_visuals(
        self,
        manifest: dict[str, Any],
        tex: str,
        rows: list[dict[str, str]],
        compiled_files: list[Path],
    ) -> None:
        clean_tex = strip_tex_comments(tex)
        row_by_id = {row.get("source_id", ""): row for row in rows}
        compiled_by_name = {path.name: path for path in compiled_files}
        for figure in manifest.get("figures", []):
            source_id = figure["source_id"]
            row = row_by_id.get(source_id, {})
            if row.get("output_id", "").strip() != source_id:
                self.error("figure-output-id-mismatch", "Figure output_id must equal its source ID", source_id)
            output_file = row.get("output_file", "").strip()
            declared_path = compiled_by_name.get(output_file)
            declared_tex = ""
            if declared_path is None:
                self.error("trace-target-not-compiled", f"Figure output_file is not compiled: {output_file}", source_id)
            else:
                declared_tex = strip_tex_comments(
                    declared_path.read_text(encoding="utf-8", errors="replace")
                )
            label_count = len(re.findall(r"\\label\s*\{\s*" + re.escape(source_id) + r"\s*\}", clean_tex))
            if label_count != 1:
                self.error("figure-label-count", f"Expected one LaTeX label, found {label_count}", source_id)
            if declared_tex and not re.search(
                r"\\label\s*\{\s*" + re.escape(source_id) + r"\s*\}", declared_tex
            ):
                self.error("figure-label-wrong-file", "Figure label is absent from its declared output_file", source_id)
            filename = figure.get("extracted_file", "")
            if filename:
                asset = self.project / "manuscript" / filename
                if not asset.is_file():
                    self.error("missing-figure-file", f"Exact source asset is missing: {filename}", source_id)
                elif figure.get("sha256") and sha256_file(asset) != figure["sha256"]:
                    self.error("figure-hash-mismatch", f"Extracted source asset changed: {filename}", source_id)
            for variant in figure.get("alternate_content", []):
                if not isinstance(variant, dict):
                    self.error("invalid-alternate-content", "Figure alternate-content record is invalid", source_id)
                    continue
                for alternate in variant.get("assets", []):
                    if not isinstance(alternate, dict):
                        self.error("invalid-alternate-content", "Figure alternate asset record is invalid", source_id)
                        continue
                    alternate_file = alternate.get("extracted_file", "")
                    alternate_hash = alternate.get("sha256", "")
                    alternate_path = self.project / "manuscript" / str(alternate_file)
                    if (
                        not alternate_file
                        or not safe_flat_name(str(alternate_file))
                        or not alternate_path.is_file()
                        or alternate_path.is_symlink()
                    ):
                        self.error("missing-alternate-figure-asset", "AlternateContent branch asset is missing", source_id)
                    elif alternate_hash and sha256_file(alternate_path) != alternate_hash:
                        self.error("alternate-figure-hash-mismatch", "AlternateContent branch asset changed", source_id)
            output_asset = row.get("output_asset", "").strip()
            if not output_asset or not safe_flat_name(output_asset):
                self.error("missing-output-figure-asset", "Traceability output_asset must name one flat manuscript asset", source_id)
            else:
                target_asset = self.project / "manuscript" / output_asset
                if not target_asset.is_file():
                    self.error("missing-output-figure-asset", f"Mapped output asset is missing: {output_asset}", source_id)
                if output_asset not in clean_tex:
                    self.error("unused-output-figure-asset", f"Mapped output asset is not referenced by compiled LaTeX: {output_asset}", source_id)
                if declared_tex and output_asset not in declared_tex:
                    self.error("figure-asset-wrong-file", "Figure asset is absent from its declared output_file", source_id)
                if filename and output_asset != filename and row.get("operation", "").strip().lower() not in {
                    "translated_derivative",
                    "transcoded",
                    "rendered",
                }:
                    self.error("unexplained-figure-derivative", "A changed output asset requires a derivative operation", source_id)
            if not figure.get("exact_embedded_asset"):
                operation = row.get("operation", "").strip().lower()
                if (
                    row.get("status", "").strip().lower() != "verified"
                    or operation not in {"rendered", "translated_derivative", "transcoded"}
                    or not output_asset
                    or output_asset == filename
                    or not (self.project / "manuscript" / output_asset).is_file()
                ):
                    self.error("figure-render-unverified", "Figure requires a verified rendered derivative", source_id)

        for table in manifest.get("tables", []):
            source_id = table["source_id"]
            row = row_by_id.get(source_id, {})
            if row.get("output_id", "").strip() != source_id:
                self.error("table-output-id-mismatch", "Table output_id must equal its source ID", source_id)
            output_file = row.get("output_file", "").strip()
            declared_path = compiled_by_name.get(output_file)
            declared_tex = ""
            if declared_path is None:
                self.error("trace-target-not-compiled", f"Table output_file is not compiled: {output_file}", source_id)
            else:
                declared_tex = strip_tex_comments(
                    declared_path.read_text(encoding="utf-8", errors="replace")
                )
            label_count = len(re.findall(r"\\label\s*\{\s*" + re.escape(source_id) + r"\s*\}", clean_tex))
            if label_count != 1:
                self.error("table-label-count", f"Expected one LaTeX label, found {label_count}", source_id)
            if declared_tex and not re.search(
                r"\\label\s*\{\s*" + re.escape(source_id) + r"\s*\}", declared_tex
            ):
                self.error("table-label-wrong-file", "Table label is absent from its declared output_file", source_id)
            block = environment_containing_label(clean_tex, source_id)
            if block:
                source_numbers = Counter(normalize_number(value) for value in table.get("numeric_tokens", []))
                source_cells = table.get("cells")
                if isinstance(source_cells, list):
                    columns = int(table.get("columns", max((len(row) for row in source_cells), default=0)))
                    expected_cells: list[tuple[str, str]] = []
                    for row_index, source_row in enumerate(source_cells, start=1):
                        padded = list(source_row) + [""] * max(columns - len(source_row), 0)
                        for column_index, source_cell in enumerate(padded, start=1):
                            expected_cells.append(
                                (
                                    f"{source_id}-r{row_index:03d}-c{column_index:03d}",
                                    str(source_cell),
                                )
                            )
                    instances = sourcecell_instances(block)
                    instance_ids = [item[0] for item in instances]
                    expected_ids = [item[0] for item in expected_cells]
                    if instance_ids != expected_ids:
                        self.error(
                            "table-cell-map-mismatch",
                            "LaTeX must retain every sourcecell ID exactly once in source row-major order",
                            source_id,
                        )
                    target_by_id = {cell_id: content for cell_id, content in instances}
                    target_numbers = Counter(
                        normalize_number(match.group(0))
                        for _, content in instances
                        for match in NUMBER_RE.finditer(content)
                    )
                    for cell_id, source_cell in expected_cells:
                        target_cell = target_by_id.get(cell_id)
                        if target_cell is None:
                            continue
                        source_cell_numbers = Counter(
                            normalize_number(match.group(0))
                            for match in NUMBER_RE.finditer(source_cell)
                        )
                        target_cell_numbers = Counter(
                            normalize_number(match.group(0))
                            for match in NUMBER_RE.finditer(target_cell)
                        )
                        if source_cell_numbers != target_cell_numbers:
                            self.error(
                                "table-cell-number-mismatch",
                                f"Numeric content changed for {cell_id}",
                                source_id,
                            )
                else:
                    data_text = table_data_text(block)
                    target_numbers = Counter(
                        normalize_number(match.group(0)) for match in NUMBER_RE.finditer(data_text)
                    )
                missing = list((source_numbers - target_numbers).elements())
                extra = list((target_numbers - source_numbers).elements())
                if missing:
                    self.error(
                        "table-number-mismatch",
                        f"Source numeric tokens are missing from the LaTeX table: {missing[:20]}",
                        source_id,
                    )
                if extra:
                    self.error(
                        "table-number-added",
                        f"LaTeX table contains numeric tokens absent from source cells: {extra[:20]}",
                        source_id,
                    )
            else:
                self.error("table-environment-missing", "Could not locate the labeled table environment", source_id)

    def audit_layout_contract(
        self,
        manifest: dict[str, Any],
        rows: list[dict[str, str]],
        compiled_files: list[Path],
    ) -> None:
        """Enforce readable in-text floats, caption order, and paragraph indentation."""
        manuscript_path = self.project / "manuscript" / "manuscript.tex"
        if not manuscript_path.is_file():
            return
        main_text = strip_tex_comments(
            manuscript_path.read_text(encoding="utf-8", errors="replace")
        )
        compiled_names = {path.name for path in compiled_files}
        combined = self.combined_tex(compiled_files)
        clean_combined = strip_tex_comments(combined)
        if "source-elements.tex" in compiled_names or re.search(
            r"\\(?:input|include|InputIfFileExists)\s*\{\s*source-elements(?:\.tex)?\s*\}",
            main_text,
        ):
            self.error(
                "end-matter-float-dump",
                "source-elements.tex is a staging index and must never be compiled as an aggregate float dump",
            )
        if re.search(
            r"\\(?:listoffigures|listoftables)\b|\\section\*?\{\s*(?:Figures|Tables|Figures\s+and\s+Tables)\s*\}",
            main_text,
            re.I,
        ):
            self.error("end-matter-float-dump", "Do not add a terminal figures/tables section or list")

        indent_pattern = re.compile(
            r"\\setlength\s*\{\s*\\parindent\s*\}\s*\{\s*2em\s*\}"
        )
        spacing_pattern = re.compile(
            r"\\setlength\s*\{\s*\\parskip\s*\}\s*\{\s*0pt\s*\}"
        )
        indent_values = re.findall(
            r"\\setlength\s*\{\s*\\parindent\s*\}\s*\{\s*([^{}]+)\s*\}",
            clean_combined,
        )
        spacing_values = re.findall(
            r"\\setlength\s*\{\s*\\parskip\s*\}\s*\{\s*([^{}]+)\s*\}",
            clean_combined,
        )
        if (
            not indent_pattern.search(clean_combined)
            or not spacing_pattern.search(clean_combined)
            or any(value.strip() != "2em" for value in indent_values)
            or any(value.strip() != "0pt" for value in spacing_values)
            or any(
                re.sub(r"\s+", "", value) != "2em"
                for value in re.findall(
                    r"\\parindent\s*(?:=\s*)?([+-]?(?:\d+(?:\.\d*)?|\.\d+)\s*[A-Za-z]+)",
                    clean_combined,
                )
            )
            or any(
                re.sub(r"\s+", "", value) != "0pt"
                for value in re.findall(
                    r"\\parskip\s*(?:=\s*)?([+-]?(?:\d+(?:\.\d*)?|\.\d+)\s*[A-Za-z]+)",
                    clean_combined,
                )
            )
            or re.search(r"\\(?:addtolength|settowidth)\s*\{\s*\\par(?:indent|skip)\s*\}", clean_combined)
            or re.search(r"\\advance\s*\\par(?:indent|skip)\b", clean_combined)
        ):
            self.error(
                "paragraph-indent-policy",
                "Compiled LaTeX must set a 2em first-line indent and 0pt paragraph spacing",
            )
        if not re.search(r"\\usepackage(?:\[[^\]]*\])?\{indentfirst\}", clean_combined):
            self.error(
                "paragraph-indent-policy",
                "Use indentfirst so the first prose paragraph after each heading is also indented",
            )
        if re.search(r"\\noindent\b", clean_combined):
            self.error(
                "paragraph-indent-override",
                r"Compiled LaTeX contains \noindent, which defeats the required paragraph style",
            )

        row_by_id = {row.get("source_id", ""): row for row in rows}
        section_pattern = re.compile(r"\\section\*?\{")

        def section_number(position: int) -> int:
            return len(section_pattern.findall(main_text[:position]))

        def labelled_block(source_id: str, environments: tuple[str, ...]) -> str | None:
            label = re.compile(r"\\label\s*\{\s*" + re.escape(source_id) + r"\s*\}")
            candidates: list[str] = []
            for environment in environments:
                pattern = re.compile(
                    r"\\begin\{" + re.escape(environment) + r"\}(?:\[[^\]]*\])?.*?"
                    r"\\end\{" + re.escape(environment) + r"\}",
                    re.S,
                )
                candidates.extend(
                    match.group(0) for match in pattern.finditer(clean_combined)
                    if label.search(match.group(0))
                )
            return min(candidates, key=len) if candidates else None

        for kind, items in (("figure", manifest.get("figures", [])), ("table", manifest.get("tables", []))):
            for item in items:
                source_id = item["source_id"]
                row = row_by_id.get(source_id, {})
                if row.get("status", "").strip().lower() != "verified":
                    continue
                output_file = row.get("output_file", "").strip()
                if output_file in {"supplement.tex", "source-elements.tex"}:
                    self.error(
                        "float-not-in-text",
                        "Every source figure and table must be placed in the main manuscript body",
                        source_id,
                    )
                    continue
                if output_file == "manuscript.tex":
                    placement_matches = list(
                        re.finditer(r"\\label\s*\{\s*" + re.escape(source_id) + r"\s*\}", main_text)
                    )
                else:
                    stem = re.escape(Path(output_file).stem)
                    placement_matches = list(
                        re.finditer(
                            r"\\(?:input|include)\s*\{\s*(?:\./)?" + stem + r"(?:\.tex)?\s*\}",
                            main_text,
                        )
                    )
                callouts = list(
                    re.finditer(
                        r"\\(?:ref|autoref|cref|Cref)\s*\{\s*" + re.escape(source_id) + r"\s*\}",
                        main_text,
                    )
                )
                contextual_callout = (
                    len(placement_matches) == 1
                    and any(
                        callout.start() < placement_matches[0].start()
                        and section_number(callout.start()) == section_number(placement_matches[0].start())
                        for callout in callouts
                    )
                )
                if len(placement_matches) != 1 or not contextual_callout:
                    self.error(
                        "float-not-in-text",
                        "Place the item once after a substantive ref/autoref/cref callout in the same manuscript section",
                        source_id,
                    )
                if len(placement_matches) == 1:
                    placement_position = placement_matches[0].start()
                    end_marker = re.search(
                        r"\\(?:bibliography|printbibliography)\b|\\begin\s*\{\s*thebibliography\s*\}",
                        main_text,
                    )
                    page_breaks = list(
                        re.finditer(r"\\(?:clearpage|cleardoublepage)\b", main_text[:placement_position])
                    )
                    headings = list(
                        re.finditer(
                            r"\\(?:part|chapter|section|subsection|subsubsection)\*?\s*\{",
                            main_text[:placement_position],
                        )
                    )
                    terminal_page_break = (
                        page_breaks
                        and (not headings or page_breaks[-1].start() > headings[-1].start())
                    )
                    if (
                        (end_marker is not None and placement_position > end_marker.start())
                        or terminal_page_break
                    ):
                        self.error(
                            "end-matter-float-dump",
                            "Every verified figure and table must appear in the article body, before references and any terminal clear-page dump",
                            source_id,
                        )

                environments = ("figure", "figure*") if kind == "figure" else ("table", "table*", "longtable")
                block = labelled_block(source_id, environments)
                if block is None:
                    continue
                begin = re.match(r"\\begin\{[^{}]+\}(?:\[([^\]]*)\])?", block)
                if begin and begin.group(1):
                    float_options = set(begin.group(1).replace("!", "").strip())
                    if float_options == {"p"}:
                        self.error(
                            "float-page-only-placement",
                            "Page-only [p] placement is forbidden; use contextual [!htbp] or an official equivalent",
                            source_id,
                        )
                source_label = re.search(
                    r"\\label\s*\{\s*" + re.escape(source_id) + r"\s*\}", block
                )
                captions = list(re.finditer(r"\\caption(?:\[[^\]]*\])?\s*\{", block))
                caption = next(
                    (
                        match for match in reversed(captions)
                        if source_label is not None and match.start() < source_label.start()
                    ),
                    None,
                )
                if not caption:
                    self.error(f"{kind}-caption-position", f"{kind.title()} needs a main caption", source_id)
                    continue
                if kind == "figure":
                    visuals = list(re.finditer(
                        r"\\(?:includegraphics|includesvg|adjustimage|fbox)\b|\\begin\{tikzpicture\}",
                        block,
                    ))
                    if not visuals or caption.start() < visuals[-1].start():
                        self.error(
                            "figure-caption-position",
                            "Figure caption must appear below the complete visual",
                            source_id,
                        )
                else:
                    content_markers = [
                        match.start()
                        for pattern in (
                            r"\\begin\s*\{\s*(?:tabular\*?|tabularx)\s*\}",
                            r"\\sourcecell\s*\{",
                            r"\\(?:toprule|midrule|bottomrule|hline)\b",
                        )
                        for match in re.finditer(pattern, block)
                    ]
                    first_content = min(content_markers) if content_markers else None
                    if first_content is None or caption.start() > first_content:
                        self.error(
                            "table-caption-position",
                            "Table caption must appear above the table content",
                            source_id,
                        )

    def audit_objects(
        self,
        manifest: dict[str, Any],
        tex: str,
        rows: list[dict[str, str]],
        compiled_files: list[Path],
    ) -> None:
        clean_tex = strip_tex_comments(tex)
        row_by_id = {row.get("source_id", ""): row for row in rows}
        compiled_by_name = {path.name: path for path in compiled_files}
        rendered_operations = {"rendered", "translated_derivative", "transcoded"}
        reconstructed_operations = {"reconstructed_table", "reconstructed_equation", "translated", "manual_reconstruction"}
        for item in manifest.get("objects", []):
            source_id = item["source_id"]
            row = row_by_id.get(source_id, {})
            status = row.get("status", "").strip().lower()
            if status == "not-research-content":
                continue
            if status != "verified":
                continue
            operation = row.get("operation", "").strip().lower()
            if operation not in rendered_operations | reconstructed_operations:
                self.error("invalid-object-operation", f"Unsupported verified object operation: {operation or '(blank)'}", source_id)
            output_id = row.get("output_id", "").strip()
            output_file = row.get("output_file", "").strip()
            declared_path = compiled_by_name.get(output_file)
            declared_tex = ""
            if declared_path is None:
                self.error("trace-target-not-compiled", f"Object output_file is not compiled: {output_file}", source_id)
            else:
                declared_tex = strip_tex_comments(
                    declared_path.read_text(encoding="utf-8", errors="replace")
                )
            if not output_id:
                self.error("missing-object-output-id", "Verified object has no output_id", source_id)
            else:
                label_count = len(re.findall(r"\\label\s*\{\s*" + re.escape(output_id) + r"\s*\}", clean_tex))
                if label_count != 1:
                    self.error("object-label-count", f"Expected one output label, found {label_count}", source_id)
                if declared_tex and not re.search(
                    r"\\label\s*\{\s*" + re.escape(output_id) + r"\s*\}", declared_tex
                ):
                    self.error("object-label-wrong-file", "Object label is absent from its declared output_file", source_id)
            if operation in rendered_operations:
                output_asset = row.get("output_asset", "").strip()
                if not output_asset or not safe_flat_name(output_asset):
                    self.error("missing-object-output-asset", "Rendered object needs a flat output_asset", source_id)
                else:
                    asset = self.project / "manuscript" / output_asset
                    if not asset.is_file() or output_asset not in clean_tex:
                        self.error("unused-object-output-asset", f"Rendered asset is missing or unused: {output_asset}", source_id)
                    if declared_tex and output_asset not in declared_tex:
                        self.error("object-asset-wrong-file", "Object asset is absent from its declared output_file", source_id)

    def audit_reverse_structures(self, tex: str, rows: list[dict[str, str]]) -> None:
        """Reject typeset figures, tables, and display equations with no source mapping."""
        clean_tex = strip_tex_comments(tex)
        output_rows: dict[tuple[str, str], list[dict[str, str]]] = {}
        for row in rows:
            if row.get("status", "").strip().lower() != "verified":
                continue
            kind = row.get("kind", "").strip()
            if kind == "material":
                kind = {
                    "source-figure": "figure",
                    "source-table": "table",
                    "source-equation": "equation",
                }.get(row.get("operation", "").strip().lower(), "material")
            output_id = row.get("output_id", "").strip()
            if kind in {"figure", "table", "equation"} and output_id:
                output_rows.setdefault((kind, output_id), []).append(row)

        environment_pattern = re.compile(
            r"\\begin\{(figure\*?|table\*?|longtable|equation\*?|align\*?|alignat\*?|"
            r"flalign\*?|gather\*?|multline\*?|displaymath)\}(.*?)\\end\{\1\}",
            re.S,
        )
        for index, match in enumerate(environment_pattern.finditer(clean_tex), start=1):
            environment = match.group(1)
            block = match.group(0)
            if environment.startswith("figure"):
                kind = "figure"
            elif environment.startswith("table") or environment == "longtable":
                kind = "table"
            else:
                kind = "equation"
            labels = re.findall(r"\\label\s*\{\s*([^{}]+?)\s*\}", block)
            if not labels:
                self.error(
                    "untraced-typeset-structure",
                    f"{environment} environment {index} has no source-mapped label",
                )
                continue
            if kind in {"figure", "table"} and len(labels) != 1:
                self.error(
                    "ambiguous-typeset-structure",
                    f"{environment} environment {index} must have exactly one source label",
                )
            for label in labels:
                mapped = output_rows.get((kind, label), [])
                if len(mapped) != 1:
                    self.error(
                        "unmapped-typeset-structure",
                        f"{environment} label {label} has {len(mapped)} verified {kind} source mappings",
                    )
        if re.search(r"\\\[|\$\$", clean_tex):
            self.error(
                "untraced-display-math",
                "Use a labelled equation environment with a verified source mapping instead of \\[...\\] or $$...$$",
            )

    def audit_citations(
        self, tex: str, rows: list[dict[str, str]], compiled_files: list[Path]
    ) -> None:
        bib_path = self.project / "manuscript" / "references.bib"
        try:
            bib_text = bib_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            self.error("missing-bibliography", "manuscript/references.bib is missing")
            return
        except UnicodeDecodeError as exc:
            self.error("invalid-bibliography-encoding", f"references.bib is not UTF-8: {exc}")
            return
        bib_keys = set(BIB_ENTRY_RE.findall(bib_text))
        compiled_by_name = {path.name: path for path in compiled_files}
        cited_keys: set[str] = set()
        clean_tex = strip_tex_comments(tex)
        if NOCITE_RE.search(clean_tex):
            self.error("nocite-forbidden", r"\nocite is not allowed to conceal unmapped bibliography records")
        for group in CITE_RE.findall(clean_tex):
            cited_keys.update(key.strip() for key in group.split(",") if key.strip())
        for key in sorted(cited_keys - bib_keys):
            self.error("undefined-citation", f"Cited BibTeX key does not exist: {key}")
        for key in sorted(bib_keys - cited_keys):
            self.error("uncited-bibliography-entry", f"BibTeX entry is not cited: {key}")

        reference_mappings: dict[str, list[dict[str, str]]] = {}
        citation_mappings: dict[str, list[dict[str, str]]] = {}
        for row in rows:
            if row.get("kind") not in {"bibliography", "bibliography-metadata", "citation", "citation-candidate"}:
                continue
            if row.get("status", "").strip().lower() != "verified":
                continue
            row_kind = row.get("kind")
            output_file = row.get("output_file", "").strip()
            if row_kind in {"bibliography", "bibliography-metadata"} and output_file != "references.bib":
                self.error(
                    "reference-target-wrong-file",
                    "Verified bibliography rows must declare references.bib as output_file",
                    row.get("source_id", ""),
                )
            output_ids = [item.strip() for item in re.split(r"[,;]", row.get("output_id", "")) if item.strip()]
            if not output_ids:
                self.error("missing-bibtex-mapping", "Verified citation/reference row has no output_id", row.get("source_id", ""))
            if (
                row_kind in {"bibliography", "bibliography-metadata"}
                and len(output_ids) > 1
                and "split-source-record" not in row.get("notes", "").lower()
            ):
                self.error(
                    "ambiguous-bibtex-mapping",
                    "One source row maps to multiple BibTeX keys without split-source-record evidence",
                    row.get("source_id", ""),
                )
            for key in output_ids:
                if row_kind in {"bibliography", "bibliography-metadata"}:
                    reference_mappings.setdefault(key, []).append(row)
                else:
                    citation_mappings.setdefault(key, []).append(row)
                if key not in bib_keys:
                    self.error("invalid-bibtex-mapping", f"Mapped BibTeX key does not exist: {key}", row.get("source_id", ""))
                elif row.get("kind") in {"citation", "citation-candidate"} and key not in cited_keys:
                    self.error("mapped-citation-not-used", f"Mapped BibTeX key is not cited: {key}", row.get("source_id", ""))

            if row_kind in {"citation", "citation-candidate"}:
                source_id = row.get("source_id", "")
                declared_path = compiled_by_name.get(output_file)
                if declared_path is None:
                    self.error("trace-target-not-compiled", f"Citation output_file is not compiled: {output_file}", source_id)
                    continue
                declared_text = declared_path.read_text(encoding="utf-8", errors="replace")
                marker_pattern = re.compile(
                    r"%\s*TRACE:SRC=[^\n]*" + re.escape(source_id) + r"(?=$|[,\s])",
                    re.M,
                )
                markers = list(marker_pattern.finditer(tex))
                if len(markers) != 1:
                    self.error("citation-trace-marker-count", f"Expected one citation TRACE marker, found {len(markers)}", source_id)
                elif output_ids:
                    declared_markers = list(marker_pattern.finditer(declared_text))
                    if len(declared_markers) != 1:
                        self.error(
                            "citation-trace-wrong-file",
                            "Citation TRACE marker is absent from its declared output_file",
                            source_id,
                        )
                        continue
                    following = declared_text[
                        declared_markers[0].end() : declared_markers[0].end() + 1200
                    ]
                    next_marker = following.find("% TRACE:SRC=")
                    if next_marker >= 0:
                        following = following[:next_marker]
                    nearby_keys: set[str] = set()
                    for group in CITE_RE.findall(strip_tex_comments(following)):
                        nearby_keys.update(key.strip() for key in group.split(",") if key.strip())
                    missing_nearby = sorted(set(output_ids) - nearby_keys)
                    if missing_nearby:
                        self.error("citation-trace-not-adjacent", f"TRACE marker is not followed by mapped citation(s): {missing_nearby}", source_id)

        for key in sorted(bib_keys):
            if not reference_mappings.get(key):
                self.error("unmapped-bibliography-key", f"BibTeX entry has no verified source reference mapping: {key}")
        for key in sorted(cited_keys):
            if not citation_mappings.get(key):
                self.error("unmapped-cited-key", f"Cited key has no verified source citation mapping: {key}")
        for key, mapped_rows in sorted(reference_mappings.items()):
            primary_rows = [row for row in mapped_rows if row.get("kind") == "bibliography"]
            metadata_rows = [row for row in mapped_rows if row.get("kind") == "bibliography-metadata"]
            for same_kind_rows in (primary_rows, metadata_rows):
                if len(same_kind_rows) > 1 and not all(
                    "duplicate-source-record" in row.get("notes", "").lower()
                    for row in same_kind_rows
                ):
                    self.error(
                        "many-to-one-reference-mapping",
                        f"Multiple source records map to {key} without duplicate-source-record evidence",
                    )

        self.metrics["bib_entries"] = len(bib_keys)
        self.metrics["cited_keys"] = len(cited_keys)

    def audit_equations_and_notes(self, tex: str, rows: list[dict[str, str]], compiled_files: list[Path]) -> None:
        compiled_names = {path.name for path in compiled_files}
        compiled_by_name = {path.name: path for path in compiled_files}
        clean_tex = strip_tex_comments(tex)
        for row in rows:
            kind = row.get("kind", "")
            if kind not in {"equation", "footnote", "endnote", "comment"}:
                continue
            if row.get("status", "").strip().lower() != "verified":
                continue
            source_id = row.get("source_id", "")
            output_file = row.get("output_file", "").strip()
            if output_file not in compiled_names:
                self.error("trace-target-not-compiled", f"Mapped {kind} target is not part of compiled LaTeX: {output_file}", source_id)
            declared_path = compiled_by_name.get(output_file)
            declared_text = (
                declared_path.read_text(encoding="utf-8", errors="replace")
                if declared_path is not None else ""
            )
            output_id = row.get("output_id", "").strip()
            if not output_id:
                self.error("missing-trace-output-id", f"Verified {kind} has no output_id", source_id)
            if kind in {"footnote", "endnote", "comment"} and output_id != source_id:
                self.error("note-output-id-mismatch", f"Verified {kind} output_id must equal its source ID", source_id)
            marker_pattern = re.compile(
                r"%\s*TRACE:SRC=[^\n]*" + re.escape(source_id) + r"(?=$|[,\s])",
                re.M,
            )
            marker_count = len(marker_pattern.findall(tex))
            if marker_count != 1:
                self.error("trace-marker-count", f"Expected one {kind} TRACE marker, found {marker_count}", source_id)
            if declared_text and len(marker_pattern.findall(declared_text)) != 1:
                self.error("trace-marker-wrong-file", f"{kind} TRACE marker is absent from its declared output_file", source_id)
            if kind == "equation" and output_id:
                label_count = len(re.findall(r"\\label\s*\{\s*" + re.escape(output_id) + r"\s*\}", clean_tex))
                if label_count != 1:
                    self.error("equation-label-count", f"Expected one equation output label, found {label_count}", source_id)
                if declared_text and len(
                    re.findall(r"\\label\s*\{\s*" + re.escape(output_id) + r"\s*\}", strip_tex_comments(declared_text))
                ) != 1:
                    self.error("equation-label-wrong-file", "Equation label is absent from its declared output_file", source_id)

    def audit_language_and_placeholders(self, tex_files: list[Path]) -> None:
        targets = list(tex_files)
        bib = self.project / "manuscript" / "references.bib"
        if bib.is_file():
            targets.append(bib)
        for path in targets:
            text = path.read_text(encoding="utf-8", errors="replace")
            clean = strip_tex_comments(text) if path.suffix == ".tex" else text
            placeholder = PLACEHOLDER_RE.search(clean)
            if placeholder:
                snippet = re.sub(r"\s+", " ", placeholder.group(0))[:120]
                self.error("placeholder-present", f"{path.name} contains unresolved placeholder: {snippet}")
            match = CJK_RE.search(clean)
            if match:
                line = clean.count("\n", 0, match.start()) + 1
                self.error("cjk-text-present", f"{path.name}:{line} contains unresolved CJK text in the English submission")

    def audit_manuscript_shape(self) -> None:
        path = self.project / "manuscript" / "manuscript.tex"
        if not path.is_file():
            self.error("missing-manuscript", "manuscript/manuscript.tex is missing")
            return
        text = strip_tex_comments(path.read_text(encoding="utf-8", errors="replace"))
        abstract = re.search(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", text, re.S)
        if not abstract:
            self.error("missing-abstract", "No abstract environment was found")
        else:
            words = len(latex_to_words(abstract.group(1)))
            self.metrics["abstract_words"] = words
            if words < 150 or words > 300:
                self.warn("abstract-length", f"Abstract has {words} words; generic target is 200--250")
        main_words = len(latex_to_words(text))
        self.metrics["manuscript_words_approx"] = main_words
        if main_words < 3000 or main_words > 10000:
            self.warn("manuscript-length", f"Approximate manuscript length is {main_words} words")
        required_sections = ["Introduction", "Discussion", "Conclusion"]
        for section in required_sections:
            if not re.search(r"\\section\*?\{" + re.escape(section) + r"s?\}", text, re.I):
                self.warn("section-not-detected", f"Generic profile section not detected: {section}")

    def audit_logs(self) -> None:
        for path in sorted((self.project / "manuscript").glob("*.log")):
            text = path.read_text(encoding="utf-8", errors="replace")
            fatal_patterns = [
                r"! LaTeX Error:",
                r"LaTeX Warning: Citation .* undefined",
                r"There were undefined references",
                r"Missing character:",
                r"Emergency stop",
            ]
            for pattern in fatal_patterns:
                if re.search(pattern, text, re.I):
                    self.error("latex-log-error", f"{path.name} contains: {pattern}")
            overfull = len(re.findall(r"Overfull \\[hv]box", text))
            if overfull:
                self.warn("overfull-box", f"{path.name} reports {overfull} overfull box(es); verify rendering")

    def audit_author_decisions(self, manifest: dict[str, Any]) -> None:
        record = self.load_json(self.project / "reports" / "author-decisions.json")
        if record is None:
            return
        if not isinstance(record, dict) or record.get("schema_version") != "1.0":
            self.error("invalid-author-decisions", "author-decisions.json must use schema_version 1.0")
            return
        source_hash = manifest.get("source", {}).get("sha256", "")
        if record.get("source_sha256") != source_hash:
            self.error("stale-author-decisions", "Author decisions do not match the source DOCX hash")
        detector_errors = {
            item.get("code") for item in manifest.get("warnings", [])
            if isinstance(item.get("code"), str)
            and item.get("code") in {"bibliography-not-detected", "citations-not-detected"}
        }
        if detector_errors:
            recovery_path = self.project / "reports" / "source-recovery.json"
            if recovery_path.is_symlink() or not recovery_path.is_file():
                self.error("missing-author-recovery-approval", "Author approval needs the final source-recovery.json")
            elif record.get("approved_source_recovery_sha256") != sha256_file(recovery_path):
                self.error(
                    "stale-author-decisions",
                    "Author approval does not match the final source-recovery.json",
                )
        build_report = self.load_json(self.project / "reports" / "build-report.json")
        if not isinstance(build_report, dict):
            self.error("missing-author-approval-build", "Author approval needs the final build report")
        else:
            built_sources = build_report.get("source_sha256", {})
            built_pdfs = build_report.get("output_sha256", {})
            if not isinstance(built_sources, dict) or record.get("approved_source_sha256") != built_sources:
                self.error("stale-author-decisions", "Author approval does not match every final submission source")
            if not isinstance(built_pdfs, dict) or record.get("approved_pdf_sha256") != built_pdfs:
                self.error("stale-author-decisions", "Author approval does not match every final PDF")
        if record.get("status") != "verified":
            self.error("author-decisions-pending", "Author decisions are not marked verified")
        decisions = record.get("decisions")
        if not isinstance(decisions, list):
            self.error("invalid-author-decisions", "author-decisions.json decisions must be a list")
            return
        by_id: dict[str, list[dict[str, Any]]] = {}
        for item in decisions:
            if not isinstance(item, dict):
                self.error("invalid-author-decision", "Every author decision must be a JSON object")
                continue
            by_id.setdefault(str(item.get("id", "")), []).append(item)
        for decision_id in sorted(AUTHOR_DECISION_IDS):
            matches = by_id.get(decision_id, [])
            if len(matches) != 1:
                self.error(
                    "author-decision-count",
                    f"Expected exactly one {decision_id} decision, found {len(matches)}",
                )
                continue
            item = matches[0]
            status = item.get("status")
            canonical_allow_na = decision_id in AUTHOR_NA_ALLOWED
            if item.get("allow_not_applicable") is not canonical_allow_na:
                self.error("tampered-author-decision-policy", f"{decision_id} has a modified not-applicable policy")
            if item.get("required_for_submission") is not True:
                self.error("tampered-author-decision-policy", f"{decision_id} must remain required for submission")
            if status not in {"author-confirmed", "source-verified", "not-applicable"}:
                self.error("author-decision-pending", f"{decision_id} has unverified status: {status}")
            value = item.get("value")
            if value is None or (isinstance(value, str) and not value.strip()):
                self.error("missing-author-decision-value", f"{decision_id} has no recorded value")
            if decision_id != "all_authors_approved" and (
                not isinstance(value, (str, list, dict))
                or (isinstance(value, (list, dict)) and not value)
            ):
                self.error(
                    "invalid-author-decision-value",
                    f"{decision_id} must contain a non-empty textual or structured declaration",
                )
            evidence = str(item.get("evidence", "")).strip()
            if status == "source-verified" and not evidence:
                self.error("missing-author-decision-evidence", f"{decision_id} needs source evidence")
            if status == "not-applicable":
                if not canonical_allow_na:
                    self.error("invalid-not-applicable-decision", f"{decision_id} cannot be marked not applicable")
                if not evidence:
                    self.error("missing-author-decision-evidence", f"{decision_id} needs a not-applicable rationale")
            if status == "author-confirmed":
                if not str(item.get("confirmed_by", "")).strip() or not valid_iso_datetime(item.get("confirmed_at")):
                    self.error("missing-author-confirmation", f"{decision_id} needs confirmed_by and confirmed_at")
            if decision_id in AUTHOR_CONFIRM_REQUIRED and status != "author-confirmed":
                self.error("author-confirmation-required", f"{decision_id} must be explicitly author-confirmed")
            if decision_id == "all_authors_approved" and status != "author-confirmed":
                self.error("authors-not-approved", "all_authors_approved must be explicitly author-confirmed")
            if decision_id == "all_authors_approved" and value is not True:
                self.error("authors-not-approved", "all_authors_approved value must be the boolean true")
        for decision_id in sorted(set(by_id) - AUTHOR_DECISION_IDS):
            self.warn("unknown-author-decision", f"Unrecognized author decision: {decision_id}")

    def audit_source_render_review(self, manifest: dict[str, Any]) -> None:
        review = self.load_json(self.project / "reports" / "source-render-review.json")
        if review is None:
            return
        if not isinstance(review, dict) or review.get("schema_version") != "1.0":
            self.error("invalid-source-render-review", "source-render-review.json must use schema_version 1.0")
            return
        source_hash = manifest.get("source", {}).get("sha256", "")
        if review.get("source_sha256") != source_hash:
            self.error("stale-source-render-review", "Source render review does not match the DOCX hash")
        if review.get("rendered_from_sha256") != source_hash:
            self.error("stale-source-render-review", "Source render provenance does not match the DOCX hash")
        if not isinstance(review.get("renderer"), str) or not review.get("renderer", "").strip():
            self.error("missing-source-renderer", "Record the Word/LibreOffice renderer used for source-render.pdf")
        if not isinstance(review.get("reviewed_by"), str) or not review.get("reviewed_by", "").strip():
            self.error("missing-source-render-reviewer", "Record who inspected every source-render page")
        if not valid_iso_datetime(review.get("reviewed_at")):
            self.error("missing-source-render-review-time", "Record a timezone-aware source-render review time")
        if review.get("status") != "verified":
            self.error("source-render-review-pending", "Rendered source pages have not been verified")
        relative = review.get("render_file", "")
        if relative != "source/source-render.pdf":
            self.error("invalid-source-render-path", "render_file must be source/source-render.pdf")
            return
        render = (self.project / relative).resolve()
        project_root = self.project.resolve()
        canonical_render = self.project / "source" / "source-render.pdf"
        if canonical_render.is_symlink() or project_root not in render.parents or not render.is_file():
            self.error("missing-source-render", f"Reviewed source render is missing or unsafe: {relative}")
            return
        render_hash = sha256_file(render)
        if review.get("render_sha256") != render_hash:
            self.error("stale-source-render-review", "Reviewed source render hash does not match the file")
        pages = self.pdf_page_count(render)
        if pages is None or pages < 1:
            self.error("unverifiable-source-render", "Could not parse source-render.pdf page count")
            return
        if (
            not isinstance(review.get("page_count"), int)
            or isinstance(review.get("page_count"), bool)
            or review.get("page_count") != pages
        ):
            self.error("source-render-page-count", "Recorded source render page count is stale")
        inspected = review.get("pages_inspected", [])
        if (
            not isinstance(inspected, list)
            or not all(isinstance(page, int) and not isinstance(page, bool) for page in inspected)
            or sorted(set(inspected)) != list(range(1, pages + 1))
        ):
            self.error("source-render-review-incomplete", f"Expected source render pages 1--{pages}, got {inspected}")

    def audit_workspace_source_review(self, manifest: dict[str, Any]) -> None:
        review = self.load_json(self.project / "reports" / "source-review.json")
        if review is None:
            return
        if not isinstance(review, dict) or review.get("schema_version") != "1.0":
            self.error("invalid-source-review", "source-review.json must use schema_version 1.0")
            return
        expected_hash = manifest.get("source", {}).get("sha256", "")
        if review.get("source_sha256") != expected_hash:
            self.error("stale-source-review", "Source review does not match the workspace aggregate hash")
        if review.get("status") != "verified":
            self.error("source-review-pending", "Every source material must be reviewed before final audit")
        expected_ids = [
            str(item.get("source_id", "")) for item in manifest.get("materials", [])
            if isinstance(item, dict)
        ]
        reviewed_ids = review.get("source_ids_reviewed")
        if (
            not isinstance(reviewed_ids, list)
            or not all(isinstance(source_id, str) for source_id in reviewed_ids)
            or len(reviewed_ids) != len(set(reviewed_ids))
            or Counter(reviewed_ids) != Counter(expected_ids)
        ):
            self.error(
                "incomplete-source-review",
                "source-review.json must list every workspace source ID exactly once",
            )
        if not isinstance(review.get("reviewed_by"), str) or not review.get("reviewed_by", "").strip():
            self.error("missing-source-reviewer", "Record who reviewed the workspace materials")
        if not valid_iso_datetime(review.get("reviewed_at")):
            self.error("missing-source-review-time", "Record a timezone-aware workspace review time")

    def audit_evidence_map(
        self, manifest: dict[str, Any], tex: str, compiled_files: list[Path]
    ) -> None:
        path = self.project / "manuscript" / "evidence-map.csv"
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                columns = set(reader.fieldnames or [])
                rows = list(reader)
        except FileNotFoundError:
            self.error("missing-evidence-map", "manuscript/evidence-map.csv is missing")
            return
        except UnicodeDecodeError as exc:
            self.error("invalid-evidence-map", f"Cannot read evidence-map.csv: {exc}")
            return
        expected_columns = {
            "claim_id", "manuscript_file", "source_ids", "manuscript_claim", "status", "notes"
        }
        if columns != expected_columns:
            self.error("invalid-evidence-map", "evidence-map.csv has the wrong columns")
        source_ids: set[str] = set()
        for key in (
            "materials", "paragraphs", "figures", "tables", "objects", "equations", "footnotes", "endnotes",
            "comments", "bibliography_entries", "word_bibliography_sources",
            "citation_fields", "citation_candidates",
        ):
            source_ids.update(str(item.get("source_id", "")) for item in manifest.get(key, []))
        compiled_names = {item.name for item in compiled_files}
        row_by_id: dict[str, dict[str, str]] = {}
        for row in rows:
            claim_id = row.get("claim_id", "").strip()
            if not re.fullmatch(r"claim-[A-Za-z0-9_-]+", claim_id):
                self.error("invalid-evidence-claim-id", f"Invalid evidence claim_id: {claim_id or '(blank)'}")
                continue
            if claim_id in row_by_id:
                self.error("duplicate-evidence-claim", f"Duplicate evidence claim: {claim_id}")
            row_by_id[claim_id] = row
            if row.get("status", "").strip().lower() != "verified":
                self.error("unverified-evidence-claim", f"Evidence claim is not verified: {claim_id}")
            if row.get("manuscript_file", "").strip() not in compiled_names:
                self.error("invalid-evidence-target", f"Evidence claim target is not compiled: {claim_id}")
            mapped = [item.strip() for item in re.split(r"[,;]", row.get("source_ids", "")) if item.strip()]
            if not mapped:
                self.error("missing-evidence-source", f"Evidence claim has no source IDs: {claim_id}")
            for source_id in mapped:
                if source_id not in source_ids:
                    self.error("unknown-evidence-source", f"Evidence claim maps unknown source ID {source_id}: {claim_id}")
            if not row.get("manuscript_claim", "").strip():
                self.error("missing-evidence-text", f"Evidence claim has no manuscript_claim: {claim_id}")

        marker_pattern = re.compile(
            r"^\s*%\s*EVIDENCE:CLAIM=(claim-[A-Za-z0-9_-]+)\s+SRC=([^\n]+?)\s*$", re.M
        )
        file_texts = {
            item.name: item.read_text(encoding="utf-8", errors="replace") for item in compiled_files
        }
        marker_matches: list[tuple[str, re.Match[str]]] = []
        for file_name, file_text in file_texts.items():
            marker_matches.extend((file_name, match) for match in marker_pattern.finditer(file_text))
        marker_ids = [match.group(1) for _, match in marker_matches]
        for claim_id in sorted(set(marker_ids)):
            count = marker_ids.count(claim_id)
            if count != 1:
                self.error("evidence-marker-count", f"Expected one marker for {claim_id}, found {count}")
        for claim_id in sorted(set(row_by_id) - set(marker_ids)):
            self.error("missing-evidence-marker", f"No LaTeX EVIDENCE marker for {claim_id}")
        for claim_id in sorted(set(marker_ids) - set(row_by_id)):
            self.error("unknown-evidence-marker", f"LaTeX marker has no evidence-map row: {claim_id}")
        for marker_file, match in marker_matches:
            row = row_by_id.get(match.group(1))
            if not row:
                continue
            if row.get("manuscript_file", "").strip() != marker_file:
                self.error(
                    "evidence-marker-file-mismatch",
                    f"Marker for {match.group(1)} is in {marker_file}, not its declared manuscript_file",
                )
            marker_sources = {item.strip() for item in re.split(r"[,;]", match.group(2)) if item.strip()}
            row_sources = {item.strip() for item in re.split(r"[,;]", row.get("source_ids", "")) if item.strip()}
            if marker_sources != row_sources:
                self.error("evidence-marker-source-mismatch", f"Marker sources differ for {match.group(1)}")
            file_text = file_texts[marker_file]
            following = file_text[match.end() : match.end() + 2500]
            next_marker = following.find("% EVIDENCE:CLAIM=")
            if next_marker >= 0:
                following = following[:next_marker]
            normalized_following = re.sub(r"\s+", " ", strip_tex_comments(following)).strip()
            normalized_claim = re.sub(r"\s+", " ", row.get("manuscript_claim", "")).strip()
            if normalized_claim and normalized_claim not in normalized_following:
                self.error("evidence-marker-not-adjacent", f"Recorded text does not follow marker {match.group(1)}")

        # Fail closed on numeric prose.  Table/equation blocks are already
        # checked through traceability and are excluded from this claim map.
        excluded = re.compile(
            r"\\begin\{(?:table\*?|tabularx?|longtable|figure\*?|equation\*?|align\*?|gather\*?)\}.*?"
            r"\\end\{(?:table\*?|tabularx?|longtable|figure\*?|equation\*?|align\*?|gather\*?)\}",
            re.S,
        )
        for file_name, file_text in file_texts.items():
            prose = excluded.sub("", file_text)
            prose = re.sub(r"\\(?:cite\w*|ref|eqref|label)\*?(?:\[[^\]]*\]){0,2}\{[^{}]*\}", "", prose)
            prose = re.sub(r"\\(?:documentclass|usepackage|setlength|bibliography|bibliographystyle)\b[^\n]*", "", prose)
            pending_marker = ""
            for line_number, line in enumerate(prose.splitlines(), start=1):
                marker = marker_pattern.match(line)
                if marker:
                    pending_marker = marker.group(1)
                    continue
                content = strip_tex_comments(line).strip()
                if not content:
                    continue
                if NUMBER_RE.search(content) and not pending_marker:
                    self.error(
                        "unmapped-quantitative-claim",
                        f"Quantitative prose lacks an EVIDENCE marker at {file_name}:{line_number}",
                    )
                pending_marker = ""
        self.metrics["evidence_claims"] = len(rows)

    def audit_overleaf_bundle(self, build_report: dict[str, Any]) -> None:
        """Bind the editable Overleaf directory and ZIP to the recorded build."""
        record = build_report.get("overleaf_upload")
        if not isinstance(record, dict):
            self.error("missing-overleaf-bundle-record", "Build report does not record the Overleaf handoff bundle")
            return
        if record.get("directory") != "submission/overleaf-upload":
            self.error("invalid-overleaf-directory-record", "Overleaf directory must be submission/overleaf-upload")
        if record.get("archive") != "submission/overleaf-upload.zip":
            self.error("invalid-overleaf-archive-record", "Overleaf archive must be submission/overleaf-upload.zip")
        if record.get("main_document") != "main.tex":
            self.error("invalid-overleaf-main-document", "Overleaf main document must be main.tex")

        names = record.get("files")
        if (
            not isinstance(names, list)
            or not names
            or not all(isinstance(name, str) and safe_flat_name(name) for name in names)
            or len(names) != len(set(names))
        ):
            self.error("invalid-overleaf-file-record", "Overleaf files must be a nonempty unique flat-name list")
            names = []
        expected_names = set(names)
        for required in ("main.tex", "README_OVERLEAF.md"):
            if required not in expected_names:
                self.error("incomplete-overleaf-bundle", f"Overleaf bundle is missing required file: {required}")
        forbidden = {
            "manuscript.tex", "traceability.csv", "evidence-map.csv",
            "journal-profile.json", "submission-checklist.md", "source-elements.tex",
            "manuscript.pdf", "submission.pdf", "DRAFT_NOT_FOR_SUBMISSION.pdf",
        }
        forbidden.update(
            name for name in expected_names
            if name.endswith((".aux", ".log", ".blg", ".bcf", ".run.xml", ".synctex.gz"))
        )
        if expected_names & forbidden:
            self.error(
                "private-overleaf-entry",
                f"Overleaf bundle contains private or generated files: {sorted(expected_names & forbidden)}",
            )

        hashes = record.get("sha256")
        if not isinstance(hashes, dict) or set(hashes) != expected_names:
            self.error("invalid-overleaf-hash-record", "Overleaf hash map must exactly match the recorded files")
            hashes = {}
        valid_hashes: dict[str, str] = {}
        for name, checksum in hashes.items():
            if (
                not isinstance(name, str)
                or name not in expected_names
                or not isinstance(checksum, str)
                or not re.fullmatch(r"[0-9a-f]{64}", checksum)
            ):
                self.error("invalid-overleaf-hash-record", f"Invalid Overleaf file hash record: {name}")
                continue
            valid_hashes[name] = checksum

        directory = self.project / "submission" / "overleaf-upload"
        if directory.is_symlink() or not directory.is_dir():
            self.error("missing-overleaf-directory", "submission/overleaf-upload must be a regular directory")
        else:
            try:
                entries = list(directory.iterdir())
            except OSError as exc:
                self.error("unreadable-overleaf-directory", f"Cannot inspect Overleaf directory: {exc}")
                entries = []
            actual_names: set[str] = set()
            for path in entries:
                if path.is_symlink() or not path.is_file() or not safe_flat_name(path.name):
                    self.error("unsafe-overleaf-entry", f"Overleaf directory entry is not a safe regular file: {path.name}")
                    continue
                actual_names.add(path.name)
                checksum = sha256_file(path)
                if valid_hashes.get(path.name) != checksum:
                    self.error("overleaf-file-hash-mismatch", f"Overleaf directory file changed after build: {path.name}")
            if actual_names != expected_names:
                self.error(
                    "overleaf-directory-file-mismatch",
                    f"Overleaf directory files differ from the build record: expected {sorted(expected_names)}, found {sorted(actual_names)}",
                )
            local_main = self.project / "manuscript" / "manuscript.tex"
            overleaf_main = directory / "main.tex"
            if local_main.is_file() and overleaf_main.is_file() and sha256_file(local_main) != sha256_file(overleaf_main):
                self.error("overleaf-main-source-mismatch", "Overleaf main.tex does not match manuscript/manuscript.tex")

        archive_path = self.project / "submission" / "overleaf-upload.zip"
        recorded_archive_hash = record.get("archive_sha256")
        if not isinstance(recorded_archive_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", recorded_archive_hash):
            self.error("invalid-overleaf-archive-hash", "Build report has no valid Overleaf archive SHA-256")
        if archive_path.is_symlink() or not archive_path.is_file():
            self.error("missing-overleaf-archive", "submission/overleaf-upload.zip is missing")
            return
        if recorded_archive_hash != sha256_file(archive_path):
            self.error("overleaf-archive-hash-mismatch", "Overleaf ZIP changed after the recorded build")
        try:
            with zipfile.ZipFile(archive_path) as archive:
                infos = archive.infolist()
                archive_names = [info.filename for info in infos]
                if len(archive_names) != len(set(archive_names)):
                    self.error("duplicate-overleaf-archive-entry", "Overleaf ZIP contains duplicate names")
                if any(not safe_flat_name(name) for name in archive_names):
                    self.error("nonflat-overleaf-archive", "Overleaf ZIP entries must be flat safe filenames")
                if set(archive_names) != expected_names:
                    self.error(
                        "overleaf-archive-file-mismatch",
                        f"Overleaf ZIP files differ from the build record: {sorted(archive_names)}",
                    )
                total_uncompressed = 0
                for info in infos:
                    total_uncompressed += info.file_size
                    unix_mode = (info.external_attr >> 16) & 0xFFFF
                    file_type = stat.S_IFMT(unix_mode)
                    if info.is_dir() or stat.S_ISLNK(unix_mode) or file_type not in {0, stat.S_IFREG}:
                        self.error("unsafe-overleaf-archive-entry", f"Overleaf ZIP entry is not a regular file: {info.filename}")
                        continue
                    if info.file_size > 512 * 1024 * 1024:
                        self.error("oversized-overleaf-entry", f"Overleaf ZIP entry exceeds 512 MiB: {info.filename}")
                        continue
                    digest = hashlib.sha256()
                    with archive.open(info) as handle:
                        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                            digest.update(chunk)
                    if valid_hashes.get(info.filename) != digest.hexdigest():
                        self.error("overleaf-archive-file-hash-mismatch", f"Overleaf ZIP content changed after build: {info.filename}")
                if total_uncompressed > 2 * 1024 * 1024 * 1024:
                    self.error("oversized-overleaf-archive", "Overleaf ZIP expands beyond 2 GiB")
        except (zipfile.BadZipFile, OSError) as exc:
            self.error("invalid-overleaf-archive", f"Cannot read overleaf-upload.zip: {exc}")

    def audit_profile_and_package(self, manifest: dict[str, Any], rows: list[dict[str, str]], compiled_files: list[Path]) -> None:
        profile = self.load_json(self.project / "manuscript" / "journal-profile.json")
        if profile is None:
            return
        if not isinstance(profile, dict):
            self.error("invalid-journal-profile", "journal-profile.json must contain a JSON object")
            return
        for key in (
            "profile", "status", "article_type", "format_mode", "venue_type",
            "target_venue", "target_journal", "target_conference", "format_guidance",
            "figure_caption_position", "table_caption_position",
            "paragraph_first_line_indent", "paragraph_spacing", "float_placement",
            "max_manuscript_pages", "page_limit_scope", "overrides",
        ):
            if key not in profile:
                self.error("invalid-journal-profile", f"journal-profile.json is missing key: {key}")
        if not isinstance(profile.get("overrides"), list):
            self.error("invalid-journal-profile", "journal-profile.json overrides must be a list")
        expected_layout = {
            "figure_caption_position": "below",
            "table_caption_position": "above",
            "paragraph_first_line_indent": "2em",
            "paragraph_spacing": "0pt",
            "float_placement": "in-text",
            "page_limit_scope": "entire-manuscript-pdf-including-references",
        }
        for key, expected in expected_layout.items():
            if profile.get(key) != expected:
                self.error("invalid-layout-profile", f"{key} must be {expected!r}")
        configured_limit = profile.get("max_manuscript_pages")
        if (
            not isinstance(configured_limit, int)
            or isinstance(configured_limit, bool)
            or not 1 <= configured_limit <= 19
        ):
            self.error("invalid-page-limit", "max_manuscript_pages must be an integer from 1 through 19")
        else:
            self.max_manuscript_pages = configured_limit

        mode = profile.get("format_mode")
        target_venue = profile.get("target_venue")
        if mode == "target":
            if profile.get("status") != "target-verified":
                self.error("unverified-target-profile", "A named venue profile must have status target-verified")
            if profile.get("venue_type") not in {"journal", "conference"}:
                self.error("invalid-target-venue-type", "venue_type must be journal or conference")
            if not isinstance(target_venue, str) or not target_venue.strip():
                self.error("missing-target-venue", "A target profile needs target_venue")
            if not str(profile.get("article_type", "")).strip():
                self.error("missing-target-article-type", "A named venue profile needs an article_type")
            guide = str(profile.get("official_guide_url", ""))
            guidance = profile.get("format_guidance", [])
            uploaded_guidance = isinstance(guidance, list) and any(
                isinstance(item, dict) and item.get("kind") == "uploaded-file"
                for item in guidance
            )
            if not re.fullmatch(r"https://[^\s]+", guide) and not uploaded_guidance:
                self.error("missing-official-guide", "A named venue needs an official URL or uploaded official instructions")
            verified_on = str(profile.get("verified_on", ""))
            try:
                verified_date = date.fromisoformat(verified_on)
                age = (date.today() - verified_date).days
                if age < 0 or age > 366:
                    self.error("stale-venue-guide", "Target instructions must be verified within 366 days")
            except ValueError:
                self.error("invalid-venue-verification-date", "verified_on must be an ISO date (YYYY-MM-DD)")
            overrides = profile.get("overrides", [])
            if not overrides:
                self.error("missing-target-override-evidence", "Record at least one target-venue requirement and its implementation evidence")
            for index, item in enumerate(overrides, start=1):
                if not isinstance(item, dict) or not all(
                    str(item.get(key, "")).strip() for key in ("requirement", "source", "implemented_in")
                ):
                    self.error("invalid-target-override", f"Target override {index} needs requirement, source, and implemented_in")
        elif mode == "draft-only":
            if profile.get("profile") != "generic-imrad-num":
                self.error("invalid-generic-profile", "A venue-neutral package must use profile generic-imrad-num")
        else:
            self.error("unconfirmed-format-decision", "format_mode must be target or draft-only")

        build_report = self.load_json(self.project / "reports" / "build-report.json")
        if build_report is None:
            return
        if not isinstance(build_report, dict) or build_report.get("schema_version") != "1.0":
            self.error("invalid-build-report", "build-report.json must be an object with schema_version 1.0")
            return
        if build_report.get("success") is not True:
            self.error("build-not-successful", "The recorded LaTeX build did not succeed")
        page_limit_record = build_report.get("manuscript_page_limit")
        if (
            not isinstance(page_limit_record, dict)
            or page_limit_record.get("maximum") != self.max_manuscript_pages
            or page_limit_record.get("scope") != "entire-manuscript-pdf-including-references"
            or page_limit_record.get("passed") is not True
            or not isinstance(page_limit_record.get("actual"), int)
            or isinstance(page_limit_record.get("actual"), bool)
            or not 1 <= page_limit_record.get("actual") <= self.max_manuscript_pages
        ):
            self.error("invalid-build-page-limit", "Build report does not prove the manuscript is within the effective page limit")
        compiler = build_report.get("compiler")
        if compiler not in {"tectonic", "latexmk", "xelatex", "pdflatex"}:
            self.error("invalid-build-compiler", f"Unsupported build compiler: {compiler}")
        if not valid_iso_datetime(build_report.get("built_at")):
            self.error("invalid-build-time", "Build report needs a timezone-aware built_at timestamp")
        expected_tex = {"manuscript.tex"}
        for optional_name in ("supplement.tex", "cover-letter.tex"):
            if (self.project / "manuscript" / optional_name).is_file():
                expected_tex.add(optional_name)
        documents = build_report.get("documents")
        if not isinstance(documents, list):
            self.error("invalid-build-documents", "Build report documents must be a list")
            documents = []
        document_names = [
            item.get("tex") for item in documents
            if isinstance(item, dict) and isinstance(item.get("tex"), str)
        ]
        if len(document_names) != len(set(document_names)) or set(document_names) != expected_tex:
            self.error(
                "invalid-build-documents",
                f"Build report must contain exactly these TeX roots: {sorted(expected_tex)}",
            )
        for index, document in enumerate(documents, start=1):
            if not isinstance(document, dict):
                self.error("invalid-build-document", f"Build document {index} is not an object")
                continue
            tex_name = document.get("tex")
            expected_pdf = f"{Path(tex_name).stem}.pdf" if isinstance(tex_name, str) else ""
            if (
                tex_name not in expected_tex
                or document.get("pdf") != expected_pdf
                or document.get("success") is not True
            ):
                self.error("invalid-build-document", f"Build result is incomplete for {tex_name or index}")
            commands = document.get("commands")
            if not isinstance(commands, list) or not commands:
                self.error("invalid-build-commands", f"Build result has no command records for {tex_name or index}")
                continue
            if compiler in {"tectonic", "latexmk"} and len(commands) != 1:
                self.error("invalid-build-commands", f"Unexpected command count for {tex_name}: {len(commands)}")
            if compiler in {"xelatex", "pdflatex"} and len(commands) not in {3, 4}:
                self.error("invalid-build-commands", f"Unexpected command count for {tex_name}: {len(commands)}")
            for command_index, command in enumerate(commands, start=1):
                if not isinstance(command, dict):
                    self.error("invalid-build-command", f"Command {command_index} for {tex_name} is not an object")
                    continue
                if (
                    command.get("returncode") != 0
                    or command.get("timed_out") is not False
                    or not valid_build_argv(str(compiler), command.get("argv"), str(tex_name))
                ):
                    self.error(
                        "invalid-build-command",
                        f"Command {command_index} for {tex_name} is unsuccessful or not an approved no-shell-escape invocation",
                    )
        source_hashes = build_report.get("source_sha256", {})
        if not isinstance(source_hashes, dict) or "manuscript.tex" not in source_hashes:
            self.error("missing-build-source-hashes", "Build report does not record manuscript source hashes")
            source_hashes = {}
        valid_source_hashes: dict[str, str] = {}
        for name, checksum in source_hashes.items():
            if (
                not isinstance(name, str)
                or not safe_flat_name(name)
                or not isinstance(checksum, str)
                or not re.fullmatch(r"[0-9a-f]{64}", checksum)
            ):
                self.error("invalid-build-source-name", f"Build report contains an unsafe source name: {name}")
                continue
            valid_source_hashes[name] = checksum
            source = self.project / "manuscript" / name
            if source.is_symlink() or not source.is_file() or sha256_file(source) != checksum:
                self.error("source-changed-after-build", f"Manuscript source changed after the recorded build: {name}")
        source_hashes = valid_source_hashes
        archive_file_record = build_report.get("source_archive_files")
        if (
            not isinstance(archive_file_record, list)
            or not all(isinstance(name, str) and safe_flat_name(name) for name in archive_file_record)
            or len(archive_file_record) != len(set(archive_file_record))
            or set(archive_file_record) != set(source_hashes)
        ):
            self.error(
                "invalid-build-source-archive-record",
                "Build report source_archive_files must exactly match the hashed source inputs",
            )
        output_hashes = build_report.get("output_sha256", {})
        if not isinstance(output_hashes, dict) or "submission/manuscript.pdf" not in output_hashes:
            self.error("missing-build-output-hashes", "Build report does not record submission/manuscript.pdf")
            output_hashes = {}
        else:
            normalized_output_hashes: dict[str, str] = {}
            for name, checksum in output_hashes.items():
                if not isinstance(name, str) or not isinstance(checksum, str):
                    self.error("invalid-build-output-hash", "Build output hash map must use string names and hashes")
                    continue
                normalized_output_hashes[name] = checksum
            output_hashes = normalized_output_hashes
        required_pdf_paths = {"submission/manuscript.pdf"}
        if (self.project / "manuscript" / "supplement.tex").is_file():
            required_pdf_paths.add("submission/supplement.pdf")
        if (self.project / "manuscript" / "cover-letter.tex").is_file():
            required_pdf_paths.add("submission/cover-letter.pdf")
        recorded_pdf_paths = set(output_hashes) if isinstance(output_hashes, dict) else set()
        for relative in sorted(required_pdf_paths - recorded_pdf_paths):
            self.error("missing-build-output-hash", f"Build report omits required output: {relative}")
        for relative in sorted(recorded_pdf_paths - required_pdf_paths):
            self.error("unexpected-build-output-hash", f"Build report records an unexpected output: {relative}")
        self.expected_pdf_hashes = {
            relative: str(output_hashes.get(relative, "")) for relative in required_pdf_paths
        }
        for relative, checksum in self.expected_pdf_hashes.items():
            if not re.fullmatch(r"submission/(?:manuscript|supplement|cover-letter)\.pdf", relative):
                self.error("invalid-build-output-name", f"Unexpected build output path: {relative}")
                continue
            if not re.fullmatch(r"[0-9a-f]{64}", checksum):
                self.error("invalid-build-output-hash", f"Invalid SHA-256 for {relative}")
                continue
            output = self.project / relative
            if output.is_symlink() or not output.is_file():
                self.error("missing-build-output", f"Recorded build output is missing: {relative}")
            elif sha256_file(output) != checksum:
                self.error("stale-build-report", f"Build report hash does not match {relative}")

        self.audit_overleaf_bundle(build_report)

        archive_path = self.project / "submission" / "submission-sources.zip"
        if archive_path.is_symlink() or not archive_path.is_file():
            self.error("missing-source-archive", "submission/submission-sources.zip is missing")
            return
        try:
            with zipfile.ZipFile(archive_path) as archive:
                infos = archive.infolist()
                names = [info.filename for info in infos]
                if len(names) != len(set(names)):
                    self.error("duplicate-source-archive-entry", "submission-sources.zip contains duplicate names")
                unsafe = [name for name in names if not safe_flat_name(name)]
                if unsafe:
                    self.error("nonflat-source-archive", f"Source archive must be flat; found: {unsafe[:10]}")
                total_uncompressed = 0
                archive_hashes: dict[str, str] = {}
                for info in infos:
                    total_uncompressed += info.file_size
                    unix_mode = (info.external_attr >> 16) & 0xFFFF
                    file_type = stat.S_IFMT(unix_mode)
                    if info.is_dir() or stat.S_ISLNK(unix_mode) or file_type not in {0, stat.S_IFREG}:
                        self.error("unsafe-source-archive-entry", f"Archive entry is not a regular file: {info.filename}")
                        continue
                    if info.file_size > 512 * 1024 * 1024:
                        self.error("oversized-source-archive-entry", f"Archive entry exceeds 512 MiB: {info.filename}")
                        continue
                    if info.compress_size and info.file_size > 10 * 1024 * 1024:
                        if info.file_size / info.compress_size > 500:
                            self.error("suspicious-source-archive-ratio", f"Archive entry has suspicious compression: {info.filename}")
                            continue
                    digest = hashlib.sha256()
                    with archive.open(info) as handle:
                        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                            digest.update(chunk)
                    archive_hashes[info.filename] = digest.hexdigest()
                if total_uncompressed > 2 * 1024 * 1024 * 1024:
                    self.error("oversized-source-archive", "Source archive expands beyond 2 GiB")
                required = {
                    "manuscript.tex", "references.bib", "traceability.csv",
                    "evidence-map.csv", "journal-profile.json",
                }
                required.update(path.name for path in compiled_files)
                required.update(source_hashes)
                required.update(
                    row.get("output_asset", "").strip()
                    for row in rows
                    if row.get("status", "").strip().lower() == "verified" and row.get("output_asset", "").strip()
                )
                missing = sorted(required - set(names))
                if missing:
                    self.error("incomplete-source-archive", f"Source archive is missing: {missing}")
                unexpected = sorted(set(names) - set(source_hashes))
                if unexpected:
                    self.error("unexpected-source-archive-entry", f"Source archive contains unrecorded files: {unexpected}")
                recorded_sources = source_hashes
                for name, checksum in recorded_sources.items():
                    if name not in names:
                        continue
                    if archive_hashes.get(name) != checksum:
                        self.error("source-archive-hash-mismatch", f"Archived source does not match build input: {name}")
        except (zipfile.BadZipFile, OSError) as exc:
            self.error("invalid-source-archive", f"Cannot read submission-sources.zip: {exc}")

        if mode == "target" and profile.get("venue_type") == "journal":
            for relative in (
                "manuscript/cover-letter.tex",
                "submission/cover-letter.pdf",
            ):
                if not (self.project / relative).is_file():
                    self.error("missing-target-cover-letter", f"Target-adapted package is missing {relative}")

    def pdf_page_count(self, path: Path) -> int | None:
        pdfinfo = shutil.which("pdfinfo")
        if pdfinfo:
            result = bounded_read_command([pdfinfo, str(path)])
            match = re.search(r"^Pages:\s+(\d+)", result["stdout"], re.M)
            if match:
                return int(match.group(1))
        try:
            from pypdf import PdfReader  # type: ignore

            return len(PdfReader(str(path)).pages)
        except Exception:
            return None

    def audit_pdf_and_visual_review(self) -> Path | None:
        candidate = self.project / "submission" / "manuscript.pdf"
        if candidate.is_symlink() or not candidate.is_file():
            if self.require_pdf:
                self.error("missing-pdf", "submission/manuscript.pdf is missing; run build.py")
            return None
        review = self.load_json(self.project / "reports" / "visual-inspection.json")
        if review is None:
            return candidate
        if not isinstance(review, dict) or not isinstance(review.get("files"), list):
            self.error("invalid-visual-review", "visual-inspection.json must contain a files list")
            return candidate
        if review.get("status") != "verified":
            self.error("visual-review-pending", "The visual-inspection report is not verified")
        if not isinstance(review.get("reviewed_by"), str) or not review.get("reviewed_by", "").strip():
            self.error("missing-visual-reviewer", "Record who inspected every submission PDF page")
        if not valid_iso_datetime(review.get("reviewed_at")):
            self.error("missing-visual-review-time", "Record a timezone-aware visual review time")
        actual_relatives = {
            f"submission/{pdf.name}"
            for pdf in (self.project / "submission").glob("*.pdf")
            if pdf.name not in {"submission.pdf", "DRAFT_NOT_FOR_SUBMISSION.pdf"}
        }
        expected_relatives = set(self.expected_pdf_hashes) if self.require_pdf else actual_relatives
        if self.require_pdf:
            for relative in sorted(expected_relatives - actual_relatives):
                self.error("missing-build-output", f"Recorded PDF is missing during visual audit: {relative}")
            for relative in sorted(actual_relatives - expected_relatives):
                self.error("unrecorded-pdf-output", f"PDF is not recorded by the current build: {relative}")
        review_files = review.get("files", [])
        review_names = [str(item.get("file", "")) for item in review_files if isinstance(item, dict)]
        if len(review_names) != len(set(review_names)):
            self.error("duplicate-visual-review-entry", "visual-inspection.json contains duplicate file entries")
        if self.require_pdf:
            for relative in sorted(expected_relatives - set(review_names)):
                self.error("visual-review-missing-output", f"Visual review omits build output: {relative}")
            for relative in sorted(set(review_names) - expected_relatives):
                self.error("visual-review-stale-output", f"Visual review records an unbuilt output: {relative}")
        for relative in sorted(actual_relatives):
            pdf = self.project / relative
            if pdf.name in {"submission.pdf", "DRAFT_NOT_FOR_SUBMISSION.pdf"}:
                continue
            if pdf.is_symlink():
                self.error("unsafe-pdf-symlink", f"Submission PDF must not be a symlink: {relative}")
                continue
            try:
                header = pdf.read_bytes()[:5]
            except OSError as exc:
                self.error("unreadable-pdf", f"Cannot read {pdf.name}: {exc}")
                continue
            if header != b"%PDF-":
                self.error("invalid-pdf", f"submission/{pdf.name} does not have a PDF header")
                continue
            pdf_hash = sha256_file(pdf)
            pages_total = self.pdf_page_count(pdf)
            if pages_total is None or pages_total < 1:
                self.error("unverifiable-pdf", f"Could not parse a positive page count from {relative}")
            if pdf.name == "manuscript.pdf":
                self.metrics["pdf_sha256"] = pdf_hash
                self.metrics["pdf_pages"] = pages_total
                self.metrics["max_manuscript_pages"] = self.max_manuscript_pages
                if (
                    isinstance(pages_total, int)
                    and not isinstance(pages_total, bool)
                    and pages_total > self.max_manuscript_pages
                ):
                    self.error(
                        "manuscript-page-limit",
                        f"The complete manuscript is {pages_total} pages; the maximum is {self.max_manuscript_pages}",
                    )
            matched = next((item for item in review_files if isinstance(item, dict) and item.get("file") == relative), None)
            if not matched or matched.get("status") != "verified":
                self.error("visual-review-pending", f"Every page of {relative} has not been marked visually verified")
                continue
            if matched.get("sha256") != pdf_hash:
                self.error("visual-review-stale", f"Visual inspection hash does not match {relative}")
            recorded_page_count = matched.get("page_count")
            if (
                not isinstance(recorded_page_count, int)
                or isinstance(recorded_page_count, bool)
                or recorded_page_count < 1
                or (
                    isinstance(pages_total, int)
                    and not isinstance(pages_total, bool)
                    and recorded_page_count != pages_total
                )
            ):
                self.error(
                    "visual-review-page-count",
                    f"Recorded visual-review page count does not match {relative}",
                )
            pages = matched.get("pages_inspected", [])
            if not isinstance(pages, list) or not all(
                isinstance(page, int) and not isinstance(page, bool) for page in pages
            ):
                self.error("visual-review-incomplete", f"Invalid inspected page list for {relative}: {pages}")
            elif pages_total is not None and sorted(set(pages)) != list(range(1, pages_total + 1)):
                self.error("visual-review-incomplete", f"Expected review of pages 1--{pages_total} for {relative}, got {pages}")
            elif not pages:
                self.error("visual-review-incomplete", f"No inspected page numbers were recorded for {relative}")
        return candidate

    def run(self) -> tuple[dict[str, Any], Path | None]:
        if not self.project.is_dir():
            self.error("missing-project", f"Project directory does not exist: {self.project}")
            return self.result(), None
        manifest = self.validate_manifest(self.load_manifest())
        recovery = self.load_source_recovery(manifest) if manifest is not None else {}
        rows = self.read_ledger()
        compiled_files = self.compiled_tex_files()
        tex = self.combined_tex(compiled_files)
        if manifest is not None:
            self.audit_format_decision(manifest)
            self.audit_source_preflight(manifest, rows, recovery.get("resolved_codes", set()))
            self.audit_ledger(manifest, rows, recovery, compiled_files)
            self.audit_visuals(manifest, tex, rows, compiled_files)
            self.audit_layout_contract(manifest, rows, compiled_files)
            self.audit_objects(manifest, tex, rows, compiled_files)
            self.audit_reverse_structures(tex, rows)
        self.audit_citations(tex, rows, compiled_files)
        self.audit_equations_and_notes(tex, rows, compiled_files)
        self.audit_language_and_placeholders(compiled_files)
        self.audit_manuscript_shape()
        self.audit_logs()
        if self.require_pdf and manifest is not None:
            self.audit_manifest_provenance(manifest)
            self.audit_author_decisions(manifest)
            if manifest.get("source", {}).get("kind") == "workspace":
                self.audit_workspace_source_review(manifest)
            else:
                self.audit_source_render_review(manifest)
            self.audit_evidence_map(manifest, tex, compiled_files)
            self.audit_profile_and_package(manifest, rows, compiled_files)
        candidate = self.audit_pdf_and_visual_review()
        return self.result(), candidate

    def result(self) -> dict[str, Any]:
        errors = sum(item["severity"] == "error" for item in self.findings)
        warnings = sum(item["severity"] == "warning" for item in self.findings)
        draft_valid = errors == 0 and (not self.strict or warnings == 0)
        ready = (
            draft_valid
            and self.require_pdf
            and self.metrics.get("format_mode") == "target"
        )
        return {
            "schema_version": "1.0",
            "submission_ready": ready,
            "draft_checks_passed": draft_valid,
            "errors": errors,
            "warnings": warnings,
            "metrics": self.metrics,
            "findings": self.findings,
        }


def report_markdown(result: dict[str, Any]) -> str:
    status = "READY FOR AUTHOR REVIEW" if result["submission_ready"] else "DRAFT — NOT FOR SUBMISSION"
    lines = [
        "# Quality report",
        "",
        f"**Status: {status}**",
        "",
        f"Errors: {result['errors']}  ",
        f"Warnings: {result['warnings']}",
        "",
        "## Metrics",
        "",
    ]
    for key, value in sorted(result["metrics"].items()):
        lines.append(f"- `{key}`: {value}")
    lines.extend(["", "## Findings", ""])
    if not result["findings"]:
        lines.append("No automated findings.")
    else:
        for item in result["findings"]:
            source = f" (`{item['source_id']}`)" if item.get("source_id") else ""
            lines.append(f"- **{item['severity'].upper()} {item['code']}**{source}: {item['message']}")
    lines.extend(
        [
            "",
            "This automated report does not replace author verification, target-venue checks, or peer review.",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit an Anything-to-Journal project")
    parser.add_argument("project", type=Path, help="Prepared project directory")
    parser.add_argument("--require-pdf", action="store_true", help="Require a compiled and visually reviewed PDF")
    parser.add_argument("--strict", action="store_true", help="Treat warnings as not ready")
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    project = args.project.resolve()
    if not project.is_dir():
        print(f"audit: error: project directory does not exist: {project}", file=sys.stderr)
        return 2
    submission = project / "submission"
    submission_ok, submission_message = safe_project_roots(project, ("submission",))
    if not submission_ok:
        print(f"audit: error: {submission_message}", file=sys.stderr)
        return 2
    # Remove any prior promotion before validation so errors and unexpected
    # exceptions cannot leave a stale file that still looks submission-ready.
    for stale_name in (
        "submission.pdf", "DRAFT_NOT_FOR_SUBMISSION.pdf",
        "submission-package.zip", "submission-package.zip.sha256",
    ):
        stale = submission / stale_name
        if stale.exists() or stale.is_symlink():
            stale.unlink()
    roots_ok, roots_message = safe_project_roots(
        project, ("source", "manuscript", "reports")
    )
    if not roots_ok:
        print(f"audit: error: {roots_message}", file=sys.stderr)
        return 2
    stale_package_manifest = project / "reports" / "submission-package-manifest.json"
    if stale_package_manifest.is_file() or stale_package_manifest.is_symlink():
        stale_package_manifest.unlink()
    audit = Audit(project, args.require_pdf, args.strict)
    try:
        result, candidate = audit.run()
    except Exception as exc:  # Last-resort fail-closed boundary for malformed projects.
        audit.error("audit-internal-error", f"Audit failed safely: {type(exc).__name__}: {exc}")
        result, candidate = audit.result(), None
    reports = project / "reports"
    atomic_write_text(
        reports / "quality-report.json",
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
    )
    atomic_write_text(reports / "quality-report.md", report_markdown(result))

    project_file = project / "project.json"
    try:
        project_state = (
            json.loads(project_file.read_text(encoding="utf-8"))
            if project_file.is_file() and not project_file.is_symlink()
            else {}
        )
    except (json.JSONDecodeError, UnicodeDecodeError):
        project_state = {}
    if not isinstance(project_state, dict):
        project_state = {}
    project_state.update(
        {
            "submission_ready": result["submission_ready"],
            "quality_report": "reports/quality-report.json",
            "submission_pdf": "submission/submission.pdf" if result["submission_ready"] else None,
            "submission_package": "submission/submission-package.zip",
        }
    )
    atomic_write_text(
        project_file, json.dumps(project_state, ensure_ascii=False, indent=2) + "\n"
    )

    if candidate and submission.is_dir():
        if result["submission_ready"]:
            atomic_copy(candidate, submission / "submission.pdf")
        else:
            atomic_copy(candidate, submission / "DRAFT_NOT_FOR_SUBMISSION.pdf")

    package_info: dict[str, Any] | None = None
    try:
        package_info = build_submission_package(project)
    except Exception as exc:
        # The complete archive is part of the completion contract. Revoke any
        # promotion, publish the failure in the final report, and make one best-
        # effort pass to package the resulting draft state.
        promoted = submission / "submission.pdf"
        if promoted.is_file() or promoted.is_symlink():
            promoted.unlink()
        audit.error("submission-package-failed", f"Could not build the complete project ZIP: {exc}")
        result = audit.result()
        atomic_write_text(
            reports / "quality-report.json",
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        )
        atomic_write_text(reports / "quality-report.md", report_markdown(result))
        project_state["submission_ready"] = False
        project_state["submission_pdf"] = None
        atomic_write_text(
            project_file, json.dumps(project_state, ensure_ascii=False, indent=2) + "\n"
        )
        if candidate:
            atomic_copy(candidate, submission / "DRAFT_NOT_FOR_SUBMISSION.pdf")
        try:
            package_info = build_submission_package(project)
        except Exception:
            package_info = None

    print(report_markdown(result), end="")
    if package_info:
        print(
            f"Complete package: {package_info['path']} "
            f"({package_info['files']} files, SHA-256 {package_info['sha256']})"
        )
    valid_explicit_draft = (
        result["draft_checks_passed"]
        and result.get("metrics", {}).get("format_mode") == "draft-only"
    )
    return 0 if result["submission_ready"] or valid_explicit_draft else 1


if __name__ == "__main__":
    raise SystemExit(main())
