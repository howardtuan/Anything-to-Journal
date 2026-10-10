"""Read-only, manifest-bound provenance for the local manuscript workspace.

Evidence locations are author-supplied navigation hints, never verification of a
claim. Source paths always come from the inventory, not from those hints or URLs.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import mimetypes
import re
import threading
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote
from xml.etree import ElementTree


MAX_METADATA_BYTES = 12 * 1024 * 1024
MAX_TEXT_BYTES = 4 * 1024 * 1024
KINDS = {"term", "value", "figure", "table", "claim"}
TEXT_SUFFIXES = {
    ".txt", ".md", ".csv", ".tsv", ".json", ".jsonl", ".yaml", ".yml",
    ".tex", ".bib", ".log", ".rst", ".xml", ".html", ".htm", ".svg",
    ".py", ".r", ".js", ".css", ".sql",
}
LEGACY_LISTS = (
    "paragraphs", "figures", "tables", "objects", "equations", "footnotes",
    "endnotes", "comments", "bibliography_entries", "word_bibliography_sources",
    "citation_fields", "citation_candidates",
)


class EvidenceError(Exception):
    """An unavailable or unsafe evidence source."""


def safe_path(project: Path, relative: str) -> Path:
    """Reject traversal and symlinks at every component, including parent dirs."""
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise EvidenceError("Source path is unsafe.")
    parts = PurePosixPath(relative).parts
    if relative.startswith("/") or any(part in {".", ".."} for part in relative.split("/")):
        raise EvidenceError("Source path is unsafe.")
    if not parts or any(ord(char) < 32 or ord(char) == 127 for char in relative):
        raise EvidenceError("Source path is unsafe.")
    target = project
    for part in parts:
        target = target / part
        if target.is_symlink():
            raise EvidenceError("Symbolic links are not allowed in source paths.")
    if project.resolve() not in target.resolve().parents or not target.is_file():
        raise EvidenceError("The preserved source file is missing or unsafe.")
    return target


def checked_locator(value: Any, *, preview: bool = False) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("locator must be an object")
    result: dict[str, Any] = {}
    for name in ("page", "occurrence", "line_start", "line_end"):
        if name not in value:
            continue
        number = value[name]
        if type(number) is not int or number < 1:
            raise ValueError(f"{name} must be a positive integer")
        result[name] = number
    for name in ("text", "label"):
        if name in value:
            if not isinstance(value[name], str) or len(value[name]) > 100_000:
                raise ValueError(f"{name} must be a string of at most 100000 characters")
            result[name] = value[name]
    if "line_end" in result and "line_start" not in result:
        raise ValueError("line_end requires line_start")
    if result.get("line_end", result.get("line_start", 1)) < result.get("line_start", 1):
        raise ValueError("line_end must not precede line_start")
    if "rect" in value:
        rect = value["rect"]
        if (
            not isinstance(rect, list) or len(rect) != 4
            # Compare bounds before converting integers to floats in isfinite:
            # JSON can contain integers larger than the platform float range.
            or any(type(n) not in (int, float) or n < 0 or n > 1 or not math.isfinite(n) for n in rect)
            or rect[2] <= 0 or rect[3] <= 0
            or rect[0] + rect[2] > 1.000001 or rect[1] + rect[3] > 1.000001
        ):
            raise ValueError("rect must be normalized [x, y, width, height] within the page")
        if preview and "page" not in result:
            raise ValueError("rect requires a page")
        result["rect"] = rect
    if preview:
        result.pop("line_start", None)
        result.pop("line_end", None)
        result.pop("label", None)
    return result


def source_media(name: str, prefix: bytes) -> tuple[str, str]:
    """Inline only recognized PDF/raster bytes. Everything else downloads."""
    suffix = Path(name).suffix.lower()
    if suffix == ".pdf" and prefix.startswith(b"%PDF-"):
        return "pdf", "application/pdf"
    signatures = {
        ".png": (prefix.startswith(b"\x89PNG\r\n\x1a\n"), "image/png"),
        ".jpg": (prefix.startswith(b"\xff\xd8\xff"), "image/jpeg"),
        ".jpeg": (prefix.startswith(b"\xff\xd8\xff"), "image/jpeg"),
        ".gif": (prefix.startswith((b"GIF87a", b"GIF89a")), "image/gif"),
        ".webp": (prefix.startswith(b"RIFF") and prefix[8:12] == b"WEBP", "image/webp"),
    }
    if suffix in signatures and signatures[suffix][0]:
        return "image", signatures[suffix][1]
    if suffix in {".html", ".htm", ".svg", ".xhtml"}:
        return "file", "application/octet-stream"
    return "file", mimetypes.guess_type(name)[0] or "application/octet-stream"


def content_disposition(name: str, inline: bool) -> str:
    basename = name.replace("\\", "/").rsplit("/", 1)[-1]
    basename = "".join(char for char in basename if ord(char) >= 32 and ord(char) != 127)
    fallback = re.sub(r"[^a-zA-Z0-9._ -]", "_", basename)[:180] or "source"
    return f"{'inline' if inline else 'attachment'}; filename=\"{fallback}\"; filename*=UTF-8''{quote(basename, safe='')}"


class EvidenceCatalog:
    def __init__(self, project: Path) -> None:
        self.project = project
        self._hashes: dict[str, tuple[tuple[int, ...], str]] = {}
        self._lock = threading.RLock()

    def _metadata(self, relative: str, warnings: list[str], *, csv_file: bool = False) -> Any:
        path = self.project / relative
        if not path.exists() and not path.is_symlink():
            return [] if csv_file else {}
        try:
            path = safe_path(self.project, relative)
            if path.stat().st_size > MAX_METADATA_BYTES:
                raise ValueError("metadata exceeds the size limit")
            text = path.read_text(encoding="utf-8-sig")
            value = list(csv.DictReader(io.StringIO(text))) if csv_file else json.loads(text)
            if not csv_file and not isinstance(value, dict):
                raise ValueError("expected a JSON object")
            if not csv_file:
                # Escaped lone surrogates are accepted by Python's JSON parser
                # but cannot be returned as UTF-8. Validate all strings, even
                # in unknown fields, without recursing into untrusted trees.
                pending = [(value, 0)]
                while pending:
                    item, depth = pending.pop()
                    if depth > 64:
                        raise ValueError("metadata nesting exceeds 64 levels")
                    if isinstance(item, str):
                        item.encode("utf-8")
                    elif isinstance(item, dict):
                        pending.extend((key, depth + 1) for key in item)
                        pending.extend((child, depth + 1) for child in item.values())
                    elif isinstance(item, list):
                        pending.extend((child, depth + 1) for child in item)
            return value
        except (OSError, UnicodeError, ValueError, RecursionError, csv.Error, EvidenceError) as exc:
            warnings.append(f"{relative}: {exc}")
            return [] if csv_file else {}

    def _inventory(self, manifest: dict[str, Any], warnings: list[str]) -> dict[str, dict[str, Any]]:
        records: dict[str, dict[str, Any]] = {}

        def add(record: dict[str, Any]) -> None:
            source_id = record["id"]
            if not isinstance(source_id, str) or not source_id:
                return
            if source_id in records:
                records[source_id]["invalid"] = "Source ID is duplicated in the manifest."
                warnings.append(f"{source_id}: duplicate manifest source ID")
            else:
                records[source_id] = record

        materials = manifest.get("materials", [])
        if not isinstance(materials, list):
            materials = []
        for item in materials:
            if not isinstance(item, dict):
                continue
            relative = item.get("copied_path") or item.get("stored_path") or ""
            if not relative and isinstance(item.get("copied_file"), str):
                relative = "source/materials/" + item["copied_file"]
            add({
                "id": item.get("source_id"),
                "name": str(item.get("original_relative_path") or item.get("copied_file") or "Source file"),
                "path": relative, "sha256": item.get("sha256"), "locator": {},
                "invalid": "Source material path must be inside source/materials/."
                if not isinstance(relative, str) or not relative.startswith("source/materials/") else "",
            })
        original = manifest.get("source", {})
        if not isinstance(original, dict) or original.get("kind") == "workspace":
            return records
        # Legacy prepare.py preserves original.docx. Never follow absolute_path.
        for key in LEGACY_LISTS:
            items = manifest.get(key, [])
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                locator: dict[str, Any] = {}
                index = item.get("index") or item.get("paragraph_index")
                if type(index) is int and index > 0:
                    locator["label"] = f"{item.get('story', 'document')}, paragraph {index}"
                if isinstance(item.get("text"), str) and item["text"]:
                    locator["text"] = item["text"]
                if key == "tables":
                    locator["label"] = str(item.get("caption") or item.get("source_id") or "Source table")
                add({
                    "id": item.get("source_id"),
                    "name": str(original.get("file_name") or "original.docx"),
                    "path": "source/original.docx", "sha256": original.get("sha256"),
                    "locator": locator, "legacy": True,
                })
        return records

    def _checked_source(self, record: dict[str, Any]) -> Path:
        if record.get("invalid"):
            raise EvidenceError(record["invalid"])
        checksum = record.get("sha256")
        if not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", checksum):
            raise EvidenceError("The manifest has no valid SHA-256 for this source.")
        path = safe_path(self.project, record["path"])
        stat = path.stat()
        signature = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        with self._lock:
            cached = self._hashes.get(str(path))
            if cached and cached[0] == signature:
                actual = cached[1]
            else:
                digest = hashlib.sha256()
                with path.open("rb") as handle:
                    for block in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(block)
                actual = digest.hexdigest()
                if len(self._hashes) > 512:
                    self._hashes.clear()
                self._hashes[str(path)] = (signature, actual)
        if actual != checksum.lower():
            raise EvidenceError("Source SHA-256 mismatch: the preserved file changed; navigation is disabled.")
        return path

    def _load(self) -> tuple[dict[str, Any], list[dict[str, str]], list[dict[str, str]], dict[str, Any], list[str]]:
        warnings: list[str] = []
        manifest = self._metadata("source/source-manifest.json", warnings)
        evidence = self._metadata("manuscript/evidence-map.csv", warnings, csv_file=True)
        traces = self._metadata("manuscript/traceability.csv", warnings, csv_file=True)
        locations = self._metadata("manuscript/evidence-locations.json", warnings)
        return manifest, evidence, traces, locations, warnings

    def evidence(self) -> dict[str, Any]:
        manifest, rows, traces, locations, warnings = self._load()
        inventory = self._inventory(manifest, warnings)
        trace_by_id = {row.get("source_id", ""): row for row in traces}
        hints: dict[str, dict[str, Any]] = {}
        if locations:
            if locations.get("schema_version") != "1.0" or not isinstance(locations.get("claims"), list):
                warnings.append("evidence-locations.json: expected schema_version '1.0' and a claims array; using evidence-map.csv.")
            else:
                for item in locations["claims"]:
                    if not isinstance(item, dict) or not isinstance(item.get("claim_id"), str) or not item["claim_id"].strip():
                        warnings.append("evidence-locations.json: skipped a claim without a valid claim_id.")
                        continue
                    claim_id = item["claim_id"]
                    if claim_id in hints:
                        warnings.append(f"{claim_id}: duplicate location claim was ignored.")
                        continue
                    hints[claim_id] = item
        seen: set[str] = set()
        claims: list[dict[str, Any]] = []
        ledger_ids = {row.get("claim_id") for row in rows}
        for claim_id in hints:
            if claim_id not in ledger_ids:
                warnings.append(f"{claim_id}: location claim is absent from evidence-map.csv and was ignored.")
        for row in rows:
            claim_id = (row.get("claim_id") or "").strip()
            if not claim_id or claim_id in seen:
                if claim_id:
                    warnings.append(f"{claim_id}: duplicate evidence-map claim was ignored.")
                continue
            seen.add(claim_id)
            hint = hints.get(claim_id, {})
            preview = {"text": row.get("manuscript_claim", "") or ""}
            if "preview" in hint:
                try:
                    preview.update(checked_locator(hint["preview"], preview=True))
                except ValueError as exc:
                    warnings.append(f"{claim_id}: invalid preview location ({exc}); using claim text.")
            source_ids = [item.strip() for item in re.split(r"[,;]", row.get("source_ids", "") or "") if item.strip()]
            source_hints: dict[str, dict[str, Any]] = {}
            hinted_sources = hint.get("sources", [])
            if not isinstance(hinted_sources, list):
                warnings.append(f"{claim_id}: sources must be an array.")
                hinted_sources = []
            for item in hinted_sources:
                if not isinstance(item, dict) or not isinstance(item.get("source_id"), str) or not item["source_id"]:
                    warnings.append(f"{claim_id}: invalid source location was ignored.")
                    continue
                source_id = item["source_id"]
                if source_id not in source_ids:
                    warnings.append(f"{claim_id}/{source_id}: location source is not mapped by evidence-map.csv and was ignored.")
                    continue
                if source_id in source_hints:
                    warnings.append(f"{claim_id}: duplicate location for {source_id} was ignored.")
                    continue
                try:
                    source_hints[source_id] = checked_locator(item.get("locator", {}))
                except ValueError as exc:
                    warnings.append(f"{claim_id}/{source_id}: invalid source location ({exc}).")
                    source_hints[source_id] = {}
            sources = []
            for source_id in dict.fromkeys(source_ids):
                record = inventory.get(source_id)
                locator = dict(record.get("locator", {})) if record else {}
                if source_id in source_hints:
                    locator = source_hints[source_id]
                elif not locator and trace_by_id.get(source_id, {}).get("source_locator"):
                    locator["label"] = trace_by_id[source_id]["source_locator"]
                source: dict[str, Any] = {
                    "id": source_id, "name": record["name"] if record else source_id,
                    "locator": locator, "available": False,
                    "url": "/api/source?id=" + quote(source_id, safe=""),
                    "preview_url": "/api/source-preview?id=" + quote(source_id, safe=""),
                }
                try:
                    if not record:
                        raise EvidenceError("Source ID is not present in the manifest.")
                    self._checked_source(record)
                    source["available"] = True
                except (EvidenceError, OSError) as exc:
                    source["warning"] = str(exc)
                    warnings.append(f"{claim_id}/{source_id}: {exc}")
                if not any(key in locator for key in ("text", "rect", "page", "line_start")):
                    source.setdefault("warning", "This mapping identifies the source file; an exact passage has not been recorded.")
                sources.append(source)
            kind = hint.get("kind", "claim")
            if not isinstance(kind, str) or kind not in KINDS:
                warnings.append(f"{claim_id}: invalid kind; using claim.")
                kind = "claim"
            if not hint and len(source_ids) == 1 and trace_by_id.get(source_ids[0], {}).get("kind") in KINDS:
                kind = trace_by_id[source_ids[0]]["kind"]
            claims.append({
                "id": claim_id, "kind": kind,
                "text": row.get("manuscript_claim") or preview.get("text") or claim_id,
                "status": row.get("status") or "pending",
                "preview": preview, "sources": sources,
            })
        result = {"claims": claims, "warnings": list(dict.fromkeys(warnings))}
        result["revision"] = hashlib.sha256(json.dumps(result, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        return result

    def source_bytes(self, source_id: str) -> tuple[dict[str, Any], bytes, str, str]:
        warnings: list[str] = []
        manifest = self._metadata("source/source-manifest.json", warnings)
        record = self._inventory(manifest, warnings).get(source_id)
        if not record:
            raise EvidenceError("Source ID is not present in the manifest.")
        path = self._checked_source(record)
        content = path.read_bytes()
        # Check the actual response bytes, including a change between stat/open.
        if hashlib.sha256(content).hexdigest() != record["sha256"].lower():
            raise EvidenceError("Source SHA-256 mismatch: the preserved file changed; navigation is disabled.")
        kind, content_type = source_media(path.name, content[:16])
        return record, content, kind, content_type

    def source_preview(self, source_id: str) -> dict[str, Any]:
        record, content, kind, _ = self.source_bytes(source_id)
        result: dict[str, Any] = {
            "id": source_id, "name": record["name"], "kind": kind,
            "url": "/api/source?id=" + quote(source_id, safe=""),
        }
        suffix = Path(record["path"]).suffix.lower()
        if suffix == ".docx":
            try:
                with zipfile.ZipFile(io.BytesIO(content)) as archive:
                    info = archive.getinfo("word/document.xml")
                    if info.file_size > MAX_TEXT_BYTES:
                        raise ValueError("DOCX text is too large for inline preview")
                    xml = archive.read(info)
                    if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
                        raise ValueError("DOCX XML declarations are unsafe")
                    root = ElementTree.fromstring(xml)
                    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
                    paragraphs = [
                        "".join(node.text or "" for node in paragraph.findall(".//w:t", namespace))
                        for paragraph in root.findall(".//w:p", namespace)
                    ]
                    result.update(kind="text", text="\n".join(paragraphs))
                    result["warning"] = "DOCX text extraction preserves paragraph order; original pagination and embedded figures are available in the downloaded file."
            except (KeyError, ValueError, RuntimeError, OSError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
                result["warning"] = f"Inline DOCX extraction is unavailable: {exc}"
        elif suffix in TEXT_SUFFIXES:
            if len(content) > MAX_TEXT_BYTES:
                result["warning"] = "This text file exceeds the inline preview limit; download the preserved original."
            else:
                try:
                    result.update(kind="text", text=content.decode("utf-8-sig"))
                except UnicodeDecodeError:
                    result["warning"] = "This file is not UTF-8 text; download the preserved original."
        elif kind == "file":
            result["warning"] = "Inline preview is unavailable for this format; download the preserved original."
        return result
