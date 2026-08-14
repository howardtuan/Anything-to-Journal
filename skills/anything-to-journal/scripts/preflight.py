#!/usr/bin/env python3
"""Inventory a source DOCX without modifying it.

The script intentionally uses only the Python standard library.  It extracts
exact embedded image bytes, table cells, source notes, bibliography records,
and citation-manager field evidence into a stable manifest for later audit.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import posixpath
import re
import sys
import zipfile
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree as ET


SCHEMA_VERSION = "1.0"
NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "b": "http://schemas.openxmlformats.org/officeDocument/2006/bibliography",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "dgm": "http://schemas.openxmlformats.org/drawingml/2006/diagram",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "o": "urn:schemas-microsoft-com:office:office",
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "v": "urn:schemas-microsoft-com:vml",
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "wpg": "http://schemas.microsoft.com/office/word/2010/wordprocessingGroup",
    "wps": "http://schemas.microsoft.com/office/word/2010/wordprocessingShape",
}


def qn(prefix: str, local: str) -> str:
    return f"{{{NS[prefix]}}}{local}"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_archive_member(archive: zipfile.ZipFile, name: str) -> str:
    digest = hashlib.sha256()
    with archive.open(name) as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extract_archive_member(archive: zipfile.ZipFile, name: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with archive.open(name) as source, destination.open("wb") as target:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            target.write(chunk)


def normalized_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def element_text(element: ET.Element, include_deleted: bool = False) -> str:
    parts: list[str] = []
    for node in element.iter():
        # A table cell, note, comment, or text box may contain several Word
        # paragraphs.  Without a separator, adjacent values such as ``1`` and
        # ``2`` become the invented value ``12`` in the audit baseline.
        if node.tag == qn("w", "p"):
            parts.append(" ")
        if node.tag in {qn("w", "t"), qn("m", "t")} and node.text:
            parts.append(node.text)
        elif include_deleted and node.tag == qn("w", "delText") and node.text:
            parts.append(node.text)
        elif node.tag in {qn("w", "tab")}:
            parts.append("\t")
        elif node.tag in {qn("w", "br"), qn("w", "cr")}:
            parts.append("\n")
    return normalized_space("".join(parts))


def load_xml(archive: zipfile.ZipFile, name: str) -> ET.Element | None:
    try:
        return ET.fromstring(archive.read(name))
    except KeyError:
        return None
    except ET.ParseError as exc:
        raise ValueError(f"Invalid XML in {name}: {exc}") from exc


def validate_archive(archive: zipfile.ZipFile) -> None:
    total_uncompressed = 0
    seen_names: set[str] = set()
    for info in archive.infolist():
        normalized = info.filename.replace("\\", "/")
        if normalized in seen_names:
            raise ValueError(f"Duplicate package part in DOCX: {info.filename}")
        seen_names.add(normalized)
        parts = normalized.split("/")
        if normalized.startswith("/") or ".." in parts or any(part == "" for part in parts[:-1]):
            raise ValueError(f"Unsafe package path in DOCX: {info.filename}")
        total_uncompressed += info.file_size
        if info.file_size > 512 * 1024 * 1024:
            raise ValueError(f"DOCX part is unreasonably large: {info.filename}")
        if info.filename.lower().endswith((".xml", ".rels")) and info.file_size > 64 * 1024 * 1024:
            raise ValueError(f"DOCX XML part exceeds the 64 MiB safety limit: {info.filename}")
        if info.compress_size and info.file_size > 10 * 1024 * 1024:
            ratio = info.file_size / info.compress_size
            if ratio > 500:
                raise ValueError(f"Suspicious compression ratio in DOCX part: {info.filename}")
    if total_uncompressed > 2 * 1024 * 1024 * 1024:
        raise ValueError("DOCX expands beyond the 2 GiB safety limit")


def paragraph_style(paragraph: ET.Element) -> str:
    style = paragraph.find("./w:pPr/w:pStyle", NS)
    return style.get(qn("w", "val"), "") if style is not None else ""


def heading_level(paragraph: ET.Element) -> int | None:
    style = paragraph_style(paragraph)
    match = re.search(r"(?:heading|標題|标题)\s*([1-9])", style, re.I)
    if match:
        return int(match.group(1))
    outline = paragraph.find("./w:pPr/w:outlineLvl", NS)
    if outline is not None:
        try:
            return int(outline.get(qn("w", "val"), "0")) + 1
        except ValueError:
            return None
    return None


def is_caption(paragraph: ET.Element, text: str) -> bool:
    style = paragraph_style(paragraph).lower()
    if "caption" in style or "圖說" in style or "图说" in style:
        return True
    return bool(
        re.match(
            r"^(?:圖|图|表|figure|fig\.?|table)\s*(?:[A-Za-z]?\d+|[一二三四五六七八九十]+)",
            text,
            flags=re.I,
        )
    )


def relationship_map(root: ET.Element | None) -> dict[str, dict[str, str]]:
    relationships: dict[str, dict[str, str]] = {}
    if root is None:
        return relationships
    for rel in list(root):
        rel_id = rel.get("Id")
        if not rel_id:
            continue
        relationships[rel_id] = {
            "target": rel.get("Target", ""),
            "target_mode": rel.get("TargetMode", "Internal"),
            "type": rel.get("Type", ""),
        }
    return relationships


def resolve_doc_target(target: str) -> str:
    clean = target.replace("\\", "/")
    if clean.startswith("/"):
        return posixpath.normpath(clean).lstrip("/")
    return posixpath.normpath(posixpath.join("word", clean))


def caption_near(paragraphs: list[dict[str, Any]], index: int) -> str:
    for candidate_index in (index, index + 1, index - 1, index + 2, index - 2):
        if 0 <= candidate_index < len(paragraphs):
            candidate = paragraphs[candidate_index]
            if candidate["is_caption"] and candidate["text"]:
                return candidate["text"]
    return ""


def safe_suffix(name: str) -> str:
    suffix = Path(name).suffix.lower()
    if not suffix or not re.fullmatch(r"\.[a-z0-9]{1,8}", suffix):
        return ".bin"
    return suffix


def table_matrix(table: ET.Element) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in table.findall("./w:tr", NS):
        rows.append([element_text(cell) for cell in row.findall("./w:tc", NS)])
    return rows


NUMBER_RE = re.compile(
    r"(?<![\w.])[-+−]?\d+(?:[,.]\d+)*(?:\s*(?:%|‰|°[CF]?|[eE][-+]?\d+))?(?![\w.])"
)

ACTIVE_WORD_FIELD_COMMANDS = {
    "DATABASE",
    "DDE",
    "DDEAUTO",
    "IMPORT",
    "INCLUDE",
    "INCLUDEPICTURE",
    "INCLUDETEXT",
    "LINK",
    "MACROBUTTON",
    "RD",
}


def numeric_tokens(matrix: list[list[str]]) -> list[str]:
    tokens: list[str] = []
    for row in matrix:
        for cell in row:
            tokens.extend(match.group(0).replace(" ", "") for match in NUMBER_RE.finditer(cell))
    return tokens

REVISION_ELEMENT_NAMES = {
    "cellDel", "cellIns", "cellMerge", "customXmlDelRangeEnd",
    "customXmlDelRangeStart", "customXmlInsRangeEnd", "customXmlInsRangeStart",
    "customXmlMoveFromRangeEnd", "customXmlMoveFromRangeStart",
    "customXmlMoveToRangeEnd", "customXmlMoveToRangeStart", "del",
    "delInstrText", "delText", "ins", "moveFrom", "moveFromRangeEnd",
    "moveFromRangeStart", "moveTo", "moveToRangeEnd", "moveToRangeStart",
    "numberingChange",
}


def write_table_csv(path: Path, matrix: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerows(
            [
                [f"'{cell}" if re.match(r"^\s*[=+\-@]", cell) else cell for cell in row]
                for row in matrix
            ]
        )


def detect_bibliography(
    top_paragraphs: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int | None]:
    headings = {
        "references",
        "bibliography",
        "reference",
        "參考文獻",
        "参考文献",
        "引用文獻",
        "引用文献",
        "參考資料",
        "参考资料",
    }
    start_index: int | None = None
    start_level: int | None = None
    for index, paragraph in enumerate(top_paragraphs):
        cleaned = re.sub(r"[\s:：]+", "", paragraph["text"]).lower()
        if cleaned in {re.sub(r"[\s:：]+", "", item).lower() for item in headings}:
            start_index = index
            start_level = paragraph["heading_level"]
            break
    if start_index is None:
        return [], None

    entries: list[dict[str, Any]] = []
    for paragraph in top_paragraphs[start_index + 1 :]:
        if paragraph["heading_level"] is not None and (
            start_level is None or paragraph["heading_level"] <= start_level
        ):
            break
        text = paragraph["text"]
        if not text:
            continue
        entries.append(
            {
                "source_id": f"src-ref-{len(entries) + 1:03d}",
                "text": text,
                "paragraph_index": paragraph["index"],
                "sha256": sha256_bytes(normalized_space(text).encode("utf-8")),
            }
        )
    return entries, start_index


def citation_candidates(
    paragraphs: list[dict[str, Any]],
    excluded_paragraph_indices: set[Any] | None = None,
    start_index: int = 0,
) -> list[dict[str, Any]]:
    patterns = [
        ("numeric-bracket", re.compile(r"[\[［]\s*\d{1,4}(?:\s*[-–—,，;；]\s*\d{1,4})*\s*[\]］]")),
        ("numeric-parenthetical", re.compile(r"[（(]\s*\d{1,4}(?:\s*[-–—,，;；]\s*\d{1,4})*\s*[）)]")),
        (
            "author-year-parenthetical",
            re.compile(
                r"[（(][^()（）\n]{0,80}?(?:19|20)\d{2}[a-z]?(?:\s*[;；,，][^()（）\n]{0,80}?(?:19|20)\d{2}[a-z]?)*[）)]",
                re.I,
            ),
        ),
        (
            "author-year-narrative",
            re.compile(r"(?:[A-Z][A-Za-z'’\-]+|[\u4e00-\u9fff]{1,8})[^\n]{0,30}?[（(](?:19|20)\d{2}[a-z]?[）)]"),
        ),
    ]
    results: list[dict[str, Any]] = []
    excluded = excluded_paragraph_indices or set()
    for paragraph in paragraphs:
        if paragraph.get("index") in excluded:
            continue
        for kind, pattern in patterns:
            for match in pattern.finditer(paragraph["text"]):
                value = match.group(0)
                # A bare parenthesized year is not enough evidence of a citation.
                if kind == "author-year-parenthetical" and re.fullmatch(r"[（(]\s*(?:19|20)\d{2}[a-z]?\s*[）)]", value):
                    continue
                results.append(
                    {
                        "source_id": f"src-cite-candidate-{start_index + len(results) + 1:03d}",
                        "kind": kind,
                        "text": value,
                        "paragraph_index": paragraph["index"],
                        "story": paragraph.get("story", "document"),
                        "source_locator": paragraph.get("source_locator", f"paragraph:{paragraph['index']}"),
                        "context": paragraph["text"][:500],
                        "confidence": "candidate",
                        "sha256": sha256_bytes(
                            f"{kind}\0{value}\0{paragraph.get('source_locator', paragraph['index'])}".encode("utf-8")
                        ),
                    }
                )
    return results


def suppress_semantic_citation_displays(
    paragraphs: list[dict[str, Any]], fields: list[dict[str, Any]], story: str
) -> list[dict[str, Any]]:
    """Remove citation-field result text before heuristic candidate scanning.

    Semantic fields are authoritative inventory records.  Scanning their
    displayed result again would create a second, false plain-text candidate.
    """
    by_paragraph: dict[Any, list[str]] = {}
    for field in fields:
        if field.get("story", "document") != story:
            continue
        displayed = field.get("display_text", "")
        if displayed:
            by_paragraph.setdefault(field.get("paragraph_index"), []).append(displayed)
    cleaned: list[dict[str, Any]] = []
    for paragraph in paragraphs:
        item = dict(paragraph)
        text = item.get("text", "")
        for displayed in by_paragraph.get(item.get("index"), []):
            text = text.replace(displayed, "", 1)
        item["text"] = text
        cleaned.append(item)
    return cleaned


def superscript_citation_candidates(
    paragraphs_xml: list[ET.Element], story: str, start_index: int = 0,
    excluded_paragraph_indices: set[Any] | None = None,
) -> list[dict[str, Any]]:
    """Inventory digit-only superscript runs as reviewable citation candidates."""
    results: list[dict[str, Any]] = []
    excluded = excluded_paragraph_indices or set()
    for paragraph_index, paragraph in enumerate(paragraphs_xml, start=1):
        if paragraph_index in excluded:
            continue
        paragraph_text = element_text(paragraph)
        simple_field_runs = {
            id(run) for field in paragraph.findall(".//w:fldSimple", NS) for run in field.findall(".//w:r", NS)
        }
        semantic_sdt_runs: set[int] = set()
        for content_control in paragraph.findall(".//w:sdt", NS):
            tag = content_control.find("./w:sdtPr/w:tag", NS)
            value = tag.get(qn("w", "val"), "") if tag is not None else ""
            if re.search(r"MENDELEY_CITATION", value, re.I):
                semantic_sdt_runs.update(id(run) for run in content_control.findall(".//w:r", NS))
        field_stack: list[bool] = []
        for run in paragraph.findall(".//w:r", NS):
            field_chars = run.findall(".//w:fldChar", NS)
            for field_char in field_chars:
                field_type = field_char.get(qn("w", "fldCharType"), "")
                if field_type == "begin":
                    field_stack.append(False)
                elif field_type == "separate" and field_stack:
                    field_stack[-1] = True
            align = run.find("./w:rPr/w:vertAlign", NS)
            inside_semantic_field = bool(field_stack) or id(run) in simple_field_runs or id(run) in semantic_sdt_runs
            if (
                not inside_semantic_field
                and align is not None
                and align.get(qn("w", "val"), "").lower() == "superscript"
            ):
                value = element_text(run)
                if re.fullmatch(r"\s*\d{1,4}(?:\s*[-–—,，;；]\s*\d{1,4})*\s*", value):
                    results.append(
                        {
                            "source_id": f"src-cite-candidate-{start_index + len(results) + 1:03d}",
                            "kind": "numeric-superscript",
                            "text": normalized_space(value),
                            "paragraph_index": paragraph_index,
                            "story": story,
                            "source_locator": f"{story}:paragraph:{paragraph_index}:superscript",
                            "context": paragraph_text[:500],
                            "confidence": "candidate",
                            "sha256": sha256_bytes(
                                f"numeric-superscript\0{normalized_space(value)}\0{story}:{paragraph_index}".encode("utf-8")
                            ),
                        }
                    )
            for field_char in field_chars:
                if field_char.get(qn("w", "fldCharType"), "") == "end" and field_stack:
                    field_stack.pop()
    return results


def citation_fields(
    paragraphs_xml: list[ET.Element], start_index: int = 0, story: str = "document"
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    manager_patterns = [
        ("zotero", re.compile(r"ZOTERO_ITEM|ZOTERO_BIBL", re.I)),
        ("endnote", re.compile(r"(?:ADDIN\s+)?EN\.CITE|ENDNOTE", re.I)),
        ("mendeley", re.compile(r"MENDELEY", re.I)),
        ("csl", re.compile(r"CSL_CITATION", re.I)),
        ("word", re.compile(r"\bCITATION\s+", re.I)),
    ]

    def add_record(instruction: str, displayed: str, paragraph_index: int) -> None:
        instruction = normalized_space(instruction)
        if not instruction or re.search(r"\b(?:BIBLIOGRAPHY|ZOTERO_BIBL|EN\.REFLIST)\b", instruction, re.I):
            return
        managers = [name for name, pattern in manager_patterns if pattern.search(instruction)]
        if not managers:
            return
        manager = managers[0]
        parse_status = "recognized"
        parse_error = ""
        if manager in {"zotero", "mendeley", "csl"} and "CSL_CITATION" in instruction.upper():
            json_start = instruction.find("{")
            if json_start < 0:
                parse_status = "incomplete"
                parse_error = "CSL citation field contains no JSON object"
            else:
                try:
                    json.JSONDecoder().raw_decode(instruction[json_start:])
                    parse_status = "complete"
                except json.JSONDecodeError as exc:
                    parse_status = "incomplete"
                    parse_error = f"Invalid CSL JSON: {exc.msg}"
        elif manager == "word":
            parse_status = "complete" if re.search(r"\bCITATION\s+\S+", instruction, re.I) else "incomplete"
            if parse_status == "incomplete":
                parse_error = "Word CITATION field has no source tag"
        elif manager == "endnote":
            xml_start = instruction.find("<")
            xml_end = instruction.rfind(">")
            if xml_start < 0 or xml_end < xml_start:
                parse_status = "incomplete"
                parse_error = "EndNote field contains no embedded XML metadata"
            else:
                try:
                    ET.fromstring(instruction[xml_start : xml_end + 1])
                    parse_status = "complete"
                except ET.ParseError as exc:
                    parse_status = "incomplete"
                    parse_error = f"Invalid EndNote XML: {exc}"
        elif manager == "mendeley":
            parse_status = "incomplete"
            parse_error = "Mendeley content-control metadata requires independent web-extension/customXml recovery"
        results.append(
            {
                "source_id": f"src-cite-field-{start_index + len(results) + 1:03d}",
                "manager": manager,
                "instruction": instruction,
                "display_text": normalized_space(displayed),
                "paragraph_index": paragraph_index,
                "story": story,
                "sha256": sha256_bytes(instruction.encode("utf-8")),
                "parse_status": parse_status,
                "parse_error": parse_error,
            }
        )

    for paragraph_index, paragraph in enumerate(paragraphs_xml, start=1):
        stack: list[dict[str, Any]] = []
        for node in paragraph.iter():
            if node.tag == qn("w", "fldChar"):
                field_type = node.get(qn("w", "fldCharType"), "")
                if field_type == "begin":
                    stack.append({"instruction": [], "display": [], "separated": False})
                elif field_type == "separate" and stack:
                    stack[-1]["separated"] = True
                elif field_type == "end" and stack:
                    completed = stack.pop()
                    add_record(
                        "".join(completed["instruction"]),
                        "".join(completed["display"]),
                        paragraph_index,
                    )
                continue
            if node.tag == qn("w", "instrText") and stack:
                stack[-1]["instruction"].append(node.text or "")
            elif node.tag == qn("w", "t") and stack and stack[-1]["separated"]:
                stack[-1]["display"].append(node.text or "")

        for simple in paragraph.findall(".//w:fldSimple", NS):
            add_record(simple.get(qn("w", "instr"), ""), element_text(simple), paragraph_index)

        for content_control in paragraph.findall(".//w:sdt", NS):
            tag = content_control.find("./w:sdtPr/w:tag", NS)
            tag_value = tag.get(qn("w", "val"), "") if tag is not None else ""
            if re.search(r"MENDELEY_CITATION", tag_value, re.I):
                add_record(tag_value, element_text(content_control), paragraph_index)
    return results


def legacy_equation_fields(
    paragraphs_xml: list[ET.Element], story: str = "document", start_index: int = 0
) -> list[dict[str, Any]]:
    """Inventory legacy Word ``EQ`` fields that carry equation content."""
    results: list[dict[str, Any]] = []

    def add(instruction: str, displayed: str, paragraph_index: int) -> None:
        instruction = normalized_space(instruction)
        if not re.match(r"^EQ(?:\s|$)", instruction, re.I):
            return
        displayed = normalized_space(displayed)
        evidence = f"{instruction}\0{displayed}\0{story}:{paragraph_index}"
        results.append(
            {
                "source_id": f"src-eq-{start_index + len(results) + 1:03d}",
                "story": story,
                "kind": "legacy-eq-field",
                "paragraph_index": paragraph_index,
                "text": displayed or instruction,
                "instruction": instruction,
                "omml_xml": "",
                "sha256": sha256_bytes(evidence.encode("utf-8")),
            }
        )

    for paragraph_index, paragraph in enumerate(paragraphs_xml, start=1):
        stack: list[dict[str, Any]] = []
        for node in paragraph.iter():
            if node.tag == qn("w", "fldChar"):
                field_type = node.get(qn("w", "fldCharType"), "")
                if field_type == "begin":
                    stack.append({"instruction": [], "display": [], "separated": False})
                elif field_type == "separate" and stack:
                    stack[-1]["separated"] = True
                elif field_type == "end" and stack:
                    completed = stack.pop()
                    add(
                        "".join(completed["instruction"]),
                        "".join(completed["display"]),
                        paragraph_index,
                    )
                continue
            if node.tag == qn("w", "instrText") and stack:
                stack[-1]["instruction"].append(node.text or "")
            elif node.tag == qn("w", "t") and stack and stack[-1]["separated"]:
                stack[-1]["display"].append(node.text or "")
        for simple in paragraph.findall(".//w:fldSimple", NS):
            add(simple.get(qn("w", "instr"), ""), element_text(simple), paragraph_index)
    return results


def active_word_fields(
    paragraphs_xml: list[ET.Element], story: str = "document", start_index: int = 0
) -> list[dict[str, Any]]:
    """Find Word fields that may load, update, or invoke external content."""
    results: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()

    def add(instruction: str, paragraph_index: int) -> None:
        instruction = normalized_space(instruction)
        match = re.match(r"^([A-Za-z]+)(?:\s|$)", instruction)
        command = match.group(1).upper() if match else ""
        key = (paragraph_index, instruction)
        if command not in ACTIVE_WORD_FIELD_COMMANDS or key in seen:
            return
        seen.add(key)
        results.append(
            {
                "source_id": f"src-active-field-{start_index + len(results) + 1:03d}",
                "story": story,
                "paragraph_index": paragraph_index,
                "command": command,
                "instruction": instruction,
                "sha256": sha256_bytes(instruction.encode("utf-8")),
            }
        )

    for paragraph_index, paragraph in enumerate(paragraphs_xml, start=1):
        stack: list[list[str]] = []
        for node in paragraph.iter():
            if node.tag == qn("w", "fldChar"):
                field_type = node.get(qn("w", "fldCharType"), "")
                if field_type == "begin":
                    stack.append([])
                elif field_type == "end" and stack:
                    add("".join(stack.pop()), paragraph_index)
                continue
            if node.tag == qn("w", "instrText") and stack:
                stack[-1].append(node.text or "")
        for unfinished in stack:
            add("".join(unfinished), paragraph_index)
        for simple in paragraph.findall(".//w:fldSimple", NS):
            add(simple.get(qn("w", "instr"), ""), paragraph_index)
        # Also catch malformed instructions that were placed outside a field wrapper.
        add("".join(node.text or "" for node in paragraph.findall(".//w:instrText", NS)), paragraph_index)
    return results


def revision_records(
    root: ET.Element | None, story: str, start_index: int = 0
) -> list[dict[str, Any]]:
    """Summarize revision markup without silently choosing an accepted view."""
    if root is None:
        return []
    grouped: dict[str, list[bytes]] = {}
    word_namespace = "{" + NS["w"] + "}"
    for node in root.iter():
        if not node.tag.startswith(word_namespace):
            continue
        local_name = node.tag.rsplit("}", 1)[-1]
        if local_name not in REVISION_ELEMENT_NAMES and not local_name.endswith("PrChange"):
            continue
        grouped.setdefault(local_name, []).append(ET.tostring(node, encoding="utf-8"))
    records: list[dict[str, Any]] = []
    for local_name, payloads in sorted(grouped.items()):
        records.append(
            {
                "source_id": f"src-revision-{start_index + len(records) + 1:03d}",
                "story": story,
                "element": local_name,
                "count": len(payloads),
                "sha256": sha256_bytes(b"\0".join(payloads)),
            }
        )
    return records


def flatten_bibliography_source(source: ET.Element) -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    for child in list(source):
        local = child.tag.rsplit("}", 1)[-1]
        value = normalized_space("".join(child.itertext()))
        if value:
            flattened[local] = value
    return flattened


def note_records(root: ET.Element | None, note_tag: str, prefix: str) -> list[dict[str, Any]]:
    if root is None:
        return []
    records: list[dict[str, Any]] = []
    for note in root.findall(f"./w:{note_tag}", NS):
        raw_id = note.get(qn("w", "id"), "")
        if raw_id in {"-1", "0"}:
            continue
        text = element_text(note)
        if not text:
            continue
        records.append(
            {
                "source_id": f"src-{prefix}-{len(records) + 1:03d}",
                "word_id": raw_id,
                "text": text,
                "sha256": sha256_bytes(text.encode("utf-8")),
            }
        )
    return records


def warning(code: str, severity: str, message: str, source_id: str = "") -> dict[str, str]:
    item = {"code": code, "severity": severity, "message": message}
    if source_id:
        item["source_id"] = source_id
    return item


def inventory_docx(
    docx_path: Path,
    media_dir: Path | None = None,
    table_dir: Path | None = None,
) -> dict[str, Any]:
    docx_path = docx_path.resolve()
    if not docx_path.is_file():
        raise FileNotFoundError(f"DOCX not found: {docx_path}")
    if docx_path.suffix.lower() != ".docx":
        raise ValueError("Input must be a .docx file; convert legacy .doc first")
    if not zipfile.is_zipfile(docx_path):
        raise ValueError("Input is not a valid DOCX/ZIP package")

    warnings: list[dict[str, str]] = []
    with zipfile.ZipFile(docx_path) as archive:
        validate_archive(archive)
        names = set(archive.namelist())
        if "word/document.xml" not in names:
            raise ValueError("DOCX has no word/document.xml")
        macro_parts = sorted(name for name in names if name.lower().endswith("vbaproject.bin"))
        if macro_parts:
            warnings.append(
                warning(
                    "macro-payload-present",
                    "error",
                    "The package contains a VBA macro payload; it was not executed",
                )
            )
        embedding_parts = sorted(
            name for name in names if name.startswith("word/embeddings/") and not name.endswith("/")
        )
        if embedding_parts:
            warnings.append(
                warning(
                    "embedded-package-parts",
                    "warning",
                    f"The DOCX contains {len(embedding_parts)} embedded package(s); preserve but do not execute them",
                )
            )

        package_parts: list[dict[str, Any]] = []
        package_hashes: dict[str, str] = {}
        for info in sorted(archive.infolist(), key=lambda item: item.filename):
            if info.is_dir():
                continue
            checksum = sha256_archive_member(archive, info.filename)
            package_hashes[info.filename] = checksum
            package_parts.append(
                {
                    "package_path": info.filename,
                    "bytes": info.file_size,
                    "compressed_bytes": info.compress_size,
                    "crc32": f"{info.CRC:08x}",
                    "sha256": checksum,
                }
            )

        for rel_name in sorted(name for name in names if name.endswith(".rels")):
            rel_root = load_xml(archive, rel_name)
            if rel_root is None:
                continue
            for rel in list(rel_root):
                if rel.get("TargetMode") != "External":
                    continue
                rel_type = rel.get("Type", "")
                target = rel.get("Target", "")
                if rel_type.endswith("/hyperlink"):
                    warnings.append(
                        warning(
                            "external-hyperlink",
                            "warning",
                            f"External hyperlink is preserved but was not opened ({rel_name})",
                        )
                    )
                else:
                    warnings.append(
                        warning(
                            "external-package-relationship",
                            "error",
                            f"Blocked non-hyperlink external relationship in {rel_name} ({rel_type.rsplit('/', 1)[-1] or 'unknown'})",
                        )
                    )
        document = load_xml(archive, "word/document.xml")
        assert document is not None
        footnotes_root = load_xml(archive, "word/footnotes.xml")
        endnotes_root = load_xml(archive, "word/endnotes.xml")
        comments_root = load_xml(archive, "word/comments.xml")
        rels = relationship_map(load_xml(archive, "word/_rels/document.xml.rels"))

        all_paragraph_xml = document.findall(".//w:p", NS)
        paragraph_records: list[dict[str, Any]] = []
        for index, paragraph in enumerate(all_paragraph_xml, start=1):
            text = element_text(paragraph)
            paragraph_records.append(
                {
                    "source_id": f"src-par-{index:06d}",
                    "index": index,
                    "text": text,
                    "style": paragraph_style(paragraph),
                    "heading_level": heading_level(paragraph),
                    "is_caption": is_caption(paragraph, text),
                    "story": "document",
                    "sha256": sha256_bytes(text.encode("utf-8")),
                }
            )

        top_paragraph_xml: list[ET.Element] = []
        body = document.find("./w:body", NS)
        if body is not None:
            top_paragraph_xml = [child for child in list(body) if child.tag == qn("w", "p")]
        top_records: list[dict[str, Any]] = []
        paragraph_lookup = {id(node): index + 1 for index, node in enumerate(all_paragraph_xml)}
        for top_index, paragraph in enumerate(top_paragraph_xml):
            text = element_text(paragraph)
            top_records.append(
                {
                    "index": paragraph_lookup.get(id(paragraph), top_index + 1),
                    "top_index": top_index,
                    "text": text,
                    "style": paragraph_style(paragraph),
                    "heading_level": heading_level(paragraph),
                    "is_caption": is_caption(paragraph, text),
                }
            )

        bibliography_entries, _bibliography_start = detect_bibliography(top_records)
        bibliography_indices = {item["paragraph_index"] for item in bibliography_entries}
        fields = citation_fields(all_paragraph_xml)
        active_fields = active_word_fields(all_paragraph_xml)
        revisions = revision_records(document, "document")
        main_candidate_records = suppress_semantic_citation_displays(paragraph_records, fields, "document")
        source_citations = citation_candidates(main_candidate_records, bibliography_indices)
        source_citations.extend(
            superscript_citation_candidates(
                all_paragraph_xml,
                "document",
                len(source_citations),
                bibliography_indices,
            )
        )
        for story, root in (
            ("footnote", footnotes_root),
            ("endnote", endnotes_root),
            ("comment", comments_root),
        ):
            if root is None:
                continue
            story_paragraphs = root.findall(".//w:p", NS)
            story_fields = citation_fields(story_paragraphs, len(fields), story)
            fields.extend(story_fields)
            active_fields.extend(
                active_word_fields(story_paragraphs, story, len(active_fields))
            )
            revisions.extend(revision_records(root, story, len(revisions)))
            story_field_types = [
                node.get(qn("w", "fldCharType"), "") for node in root.findall(".//w:fldChar", NS)
            ]
            if story_field_types.count("begin") != story_field_types.count("end"):
                warnings.append(
                    warning(
                        "broken-field-structure",
                        "error",
                        f"Word field begin/end counts differ in {story} story",
                    )
                )
            story_records = [
                {
                    "index": index,
                    "text": element_text(paragraph),
                    "story": story,
                    "source_locator": f"word/{story}s.xml:paragraph:{index}",
                }
                for index, paragraph in enumerate(story_paragraphs, start=1)
            ]
            story_records = suppress_semantic_citation_displays(story_records, story_fields, story)
            source_citations.extend(citation_candidates(story_records, start_index=len(source_citations)))
            source_citations.extend(
                superscript_citation_candidates(story_paragraphs, story, len(source_citations))
            )
        field_types = [
            node.get(qn("w", "fldCharType"), "") for node in document.findall(".//w:fldChar", NS)
        ]
        if field_types.count("begin") != field_types.count("end"):
            warnings.append(
                warning(
                    "broken-field-structure",
                    "error",
                    f"Word field begin/end counts differ ({field_types.count('begin')} vs {field_types.count('end')})",
                )
            )
        for field in fields:
            if field.get("parse_status") == "incomplete":
                warnings.append(
                    warning(
                        "citation-field-incomplete",
                        "error",
                        field.get("parse_error") or "Citation field metadata is incomplete",
                        field["source_id"],
                    )
                )

        figures: list[dict[str, Any]] = []
        referenced_package_paths: set[str] = set()
        for paragraph_index, paragraph in enumerate(all_paragraph_xml, start=1):
            drawing_refs: list[tuple[str, str, dict[str, Any]]] = []
            handled_drawing_nodes: set[int] = set()
            excluded_alternate_nodes: set[int] = set()
            alternate_metadata: dict[int, list[dict[str, Any]]] = {}
            for alternate in paragraph.findall(".//mc:AlternateContent", NS):
                branches = [
                    child for child in list(alternate)
                    if child.tag in {qn("mc", "Choice"), qn("mc", "Fallback")}
                ]
                choices = [child for child in branches if child.tag == qn("mc", "Choice")]
                selected = choices[0] if choices else (branches[0] if branches else None)
                variants: list[dict[str, Any]] = []
                for child in branches:
                    relationship_ids: list[str] = []
                    for node in child.iter():
                        for attribute in ("embed", "link", "id", "dm", "lo"):
                            rel_id = node.get(qn("r", attribute), "")
                            if rel_id and rel_id not in relationship_ids:
                                relationship_ids.append(rel_id)
                    branch_assets: list[dict[str, Any]] = []
                    for rel_id in relationship_ids:
                        rel = rels.get(rel_id, {})
                        target = rel.get("target", "")
                        target_mode = rel.get("target_mode", "Internal")
                        package_path = (
                            resolve_doc_target(target)
                            if target and target_mode != "External"
                            else ""
                        )
                        branch_assets.append(
                            {
                                "relationship_id": rel_id,
                                "target": target,
                                "target_mode": target_mode,
                                "package_path": package_path,
                                "sha256": package_hashes.get(package_path, ""),
                            }
                        )
                    variants.append(
                        {
                            "branch": child.tag.rsplit("}", 1)[-1],
                            "requires": child.get("Requires", ""),
                            "selected_for_inventory": child is selected,
                            "sha256": sha256_bytes(ET.tostring(child, encoding="utf-8")),
                            "assets": branch_assets,
                        }
                    )
                for child in branches:
                    if child is selected:
                        for node in child.iter():
                            alternate_metadata[id(node)] = variants
                    else:
                        excluded_alternate_nodes.update(id(node) for node in child.iter())
            for drawing in paragraph.findall(".//w:drawing", NS):
                if id(drawing) in excluded_alternate_nodes:
                    continue
                doc_pr = drawing.find(".//wp:docPr", NS)
                base_metadata: dict[str, Any] = {
                    "alt_text": doc_pr.get("descr", "") if doc_pr is not None else "",
                    "title": (
                        doc_pr.get("title", "") or doc_pr.get("name", "")
                        if doc_pr is not None else ""
                    ),
                    "alternate_content": alternate_metadata.get(id(drawing), []),
                }
                for picture in drawing.findall(".//pic:pic", NS):
                    crop = picture.find(".//a:srcRect", NS)
                    transform = picture.find(".//a:xfrm", NS)
                    for blip in picture.findall(".//a:blip", NS):
                        effect_nodes = [
                            child for child in list(blip)
                            if child.tag.rsplit("}", 1)[-1] != "extLst"
                        ]
                        for container_name in ("effectLst", "effectDag", "scene3d", "sp3d"):
                            effect_nodes.extend(picture.findall(f".//a:{container_name}", NS))
                        effects = [
                            {
                                "tag": node.tag.rsplit("}", 1)[-1],
                                "sha256": sha256_bytes(ET.tostring(node, encoding="utf-8")),
                            }
                            for node in effect_nodes
                        ]
                        metadata = {
                            **base_metadata,
                            "crop": dict(crop.attrib) if crop is not None else {},
                            "transform": dict(transform.attrib) if transform is not None else {},
                            "effects": effects,
                        }
                        handled_drawing_nodes.add(id(blip))
                        rel_id = blip.get(qn("r", "embed")) or blip.get(qn("r", "link")) or ""
                        drawing_refs.append((rel_id, "image", metadata))
                for blip in drawing.findall(".//a:blip", NS):
                    if id(blip) in handled_drawing_nodes:
                        continue
                    crop = drawing.find(".//a:srcRect", NS)
                    transform = drawing.find(".//a:xfrm", NS)
                    metadata = {
                        **base_metadata,
                        "crop": dict(crop.attrib) if crop is not None else {},
                        "transform": dict(transform.attrib) if transform is not None else {},
                        "effects": [
                            {
                                "tag": child.tag.rsplit("}", 1)[-1],
                                "sha256": sha256_bytes(ET.tostring(child, encoding="utf-8")),
                            }
                            for child in list(blip)
                            if child.tag.rsplit("}", 1)[-1] != "extLst"
                        ],
                    }
                    handled_drawing_nodes.add(id(blip))
                    rel_id = blip.get(qn("r", "embed")) or blip.get(qn("r", "link")) or ""
                    drawing_refs.append((rel_id, "image", metadata))
                for chart in drawing.findall(".//c:chart", NS):
                    handled_drawing_nodes.add(id(chart))
                    drawing_refs.append((chart.get(qn("r", "id"), ""), "chart", base_metadata))
                for diagram in drawing.findall(".//dgm:relIds", NS):
                    handled_drawing_nodes.add(id(diagram))
                    rel_id = diagram.get(qn("r", "dm"), "") or diagram.get(qn("r", "lo"), "")
                    drawing_refs.append((rel_id, "diagram", base_metadata))
            for blip in paragraph.findall(".//a:blip", NS):
                if id(blip) not in handled_drawing_nodes and id(blip) not in excluded_alternate_nodes:
                    rel_id = blip.get(qn("r", "embed")) or blip.get(qn("r", "link")) or ""
                    drawing_refs.append(
                        (
                            rel_id,
                            "image",
                            {
                                "crop": {},
                                "transform": {},
                                "alternate_content": alternate_metadata.get(id(blip), []),
                                "effects": [
                                    {
                                        "tag": child.tag.rsplit("}", 1)[-1],
                                        "sha256": sha256_bytes(ET.tostring(child, encoding="utf-8")),
                                    }
                                    for child in list(blip)
                                    if child.tag.rsplit("}", 1)[-1] != "extLst"
                                ],
                            },
                        )
                    )
            handled_vml_nodes: set[int] = set()
            for shape in paragraph.findall(".//v:shape", NS):
                if id(shape) in excluded_alternate_nodes:
                    continue
                for image_data in shape.findall(".//v:imagedata", NS):
                    handled_vml_nodes.add(id(image_data))
                    rel_id = image_data.get(qn("r", "id"), "")
                    drawing_refs.append(
                        (
                            rel_id,
                            "vml-image",
                            {
                                "alt_text": shape.get("alt", ""),
                                "title": image_data.get(qn("o", "title"), "") or shape.get("title", ""),
                                "crop": {},
                                "transform": {},
                                "vml_crop": {
                                    key.rsplit("}", 1)[-1]: value
                                    for key, value in image_data.attrib.items()
                                    if key.rsplit("}", 1)[-1].lower()
                                    in {"croptop", "cropleft", "cropright", "cropbottom"}
                                },
                                "vml_effects": {
                                    key.rsplit("}", 1)[-1]: value
                                    for key, value in image_data.attrib.items()
                                    if key.rsplit("}", 1)[-1].lower()
                                    in {"gain", "blacklevel", "gamma", "grayscale", "bilevel", "chromakey"}
                                },
                                "vml_shape_style": shape.get("style", ""),
                                "alternate_content": alternate_metadata.get(id(shape), []),
                            },
                        )
                    )
            for image_data in paragraph.findall(".//v:imagedata", NS):
                if id(image_data) not in handled_vml_nodes and id(image_data) not in excluded_alternate_nodes:
                    drawing_refs.append(
                        (
                            image_data.get(qn("r", "id"), ""),
                            "vml-image",
                            {
                                "crop": {}, "transform": {}, "vml_crop": {}, "vml_effects": {},
                                "vml_shape_style": "",
                                "alternate_content": alternate_metadata.get(id(image_data), []),
                            },
                        )
                    )
            for chart in paragraph.findall(".//c:chart", NS):
                if id(chart) not in handled_drawing_nodes and id(chart) not in excluded_alternate_nodes:
                    drawing_refs.append(
                        (
                            chart.get(qn("r", "id"), ""),
                            "chart",
                            {"alternate_content": alternate_metadata.get(id(chart), [])},
                        )
                    )
            for diagram in paragraph.findall(".//dgm:relIds", NS):
                if id(diagram) not in handled_drawing_nodes and id(diagram) not in excluded_alternate_nodes:
                    rel_id = diagram.get(qn("r", "dm"), "") or diagram.get(qn("r", "lo"), "")
                    drawing_refs.append(
                        (rel_id, "diagram", {"alternate_content": alternate_metadata.get(id(diagram), [])})
                    )
            if not drawing_refs:
                continue

            for rel_id, kind, drawing_metadata in drawing_refs:
                alt_text = drawing_metadata.get("alt_text", "")
                title = drawing_metadata.get("title", "")
                crop_values = drawing_metadata.get("crop", {})
                transform_values = drawing_metadata.get("transform", {})
                effects = drawing_metadata.get("effects", [])
                vml_effects = drawing_metadata.get("vml_effects", {})
                alternate_content = drawing_metadata.get("alternate_content", [])
                source_id = f"src-fig-{len(figures) + 1:03d}"
                rel = rels.get(rel_id, {})
                target = rel.get("target", "")
                target_mode = rel.get("target_mode", "Internal")
                package_path = resolve_doc_target(target) if target and target_mode != "External" else ""
                extracted_file = ""
                checksum = ""
                exact_asset = False
                if package_path and package_path in names:
                    referenced_package_paths.add(package_path)
                    suffix = safe_suffix(package_path)
                    extracted_file = f"{source_id}{suffix}"
                    checksum = package_hashes[package_path]
                    zero_values = {"", "0", "0%", "0pt", "0in", "0f", "false", "off", "none"}
                    crop_applied = any(
                        str(value).strip().lower() not in zero_values for value in crop_values.values()
                    ) or any(
                        str(value).strip().lower() not in zero_values
                        for value in drawing_metadata.get("vml_crop", {}).values()
                    )
                    transform_applied = any(
                        key.rsplit("}", 1)[-1] in {"rot", "flipH", "flipV"}
                        and str(value).lower() not in {"", "0", "false", "off"}
                        for key, value in transform_values.items()
                    )
                    for declaration in drawing_metadata.get("vml_shape_style", "").split(";"):
                        key, separator, value = declaration.partition(":")
                        if (
                            separator
                            and key.strip().lower() in {"rotation", "flip"}
                            and value.strip().lower() not in zero_values
                        ):
                            transform_applied = True
                    exact_asset = kind in {"image", "vml-image"} and not (
                        crop_applied or transform_applied or effects or vml_effects or alternate_content
                    )
                    if media_dir is not None:
                        extract_archive_member(archive, package_path, media_dir / extracted_file)
                else:
                    warnings.append(
                        warning(
                            "missing-figure-asset",
                            "error",
                            f"Figure relationship {rel_id or '(none)'} does not resolve to an embedded file",
                            source_id,
                        )
                    )
                if target_mode == "External":
                    warnings.append(
                        warning(
                            "linked-figure",
                            "error",
                            f"Figure is linked externally and is not embedded: {target}",
                            source_id,
                        )
                    )
                if kind in {"chart", "diagram"}:
                    warnings.append(
                        warning(
                            f"native-{kind}-render-required",
                            "error",
                            f"Native Word {kind} data was extracted but must be rendered to a publication figure",
                            source_id,
                        )
                    )
                alternate_asset_index = 0
                for variant in alternate_content:
                    for asset_record in variant.get("assets", []):
                        alternate_asset_index += 1
                        alternate_package = asset_record.get("package_path", "")
                        alternate_file = ""
                        if alternate_package in names:
                            referenced_package_paths.add(alternate_package)
                            alternate_file = (
                                f"{source_id}-alt-{alternate_asset_index:02d}"
                                f"{safe_suffix(alternate_package)}"
                            )
                            if media_dir is not None:
                                extract_archive_member(
                                    archive, alternate_package, media_dir / alternate_file
                                )
                        asset_record["extracted_file"] = alternate_file
                figures.append(
                    {
                        "source_id": source_id,
                        "kind": kind,
                        "relationship_id": rel_id,
                        "target": target,
                        "target_mode": target_mode,
                        "package_path": package_path,
                        "extracted_file": extracted_file,
                        "sha256": checksum,
                        "exact_embedded_asset": exact_asset,
                        "paragraph_index": paragraph_index,
                        "caption": caption_near(paragraph_records, paragraph_index - 1),
                        "alt_text": alt_text,
                        "title": title,
                        "crop": crop_values,
                        "transform": transform_values,
                        "effects": effects,
                        "alternate_content": alternate_content,
                        "vml_crop": drawing_metadata.get("vml_crop", {}),
                        "vml_effects": vml_effects,
                        "vml_shape_style": drawing_metadata.get("vml_shape_style", ""),
                    }
                )

        objects: list[dict[str, Any]] = []

        def add_object(
            kind: str,
            element: ET.Element,
            paragraph_index: int,
            rel_id: str = "",
            relationships: dict[str, dict[str, str]] | None = None,
            story: str = "document",
            extract_image: bool = False,
            extract_payload: bool = False,
        ) -> dict[str, Any]:
            source_id = f"src-object-{len(objects) + 1:03d}"
            rel = (rels if relationships is None else relationships).get(rel_id, {})
            target = rel.get("target", "")
            package_path = resolve_doc_target(target) if target and rel.get("target_mode") != "External" else ""
            data = ET.tostring(element, encoding="utf-8")
            extracted_file = ""
            if package_path:
                referenced_package_paths.add(package_path)
            if extract_image and package_path in package_hashes and rel.get("type", "").endswith("/image"):
                extracted_file = f"{source_id}{safe_suffix(package_path)}"
                if media_dir is not None:
                    extract_archive_member(archive, package_path, media_dir / extracted_file)
            if extract_payload and package_path in package_hashes:
                extracted_file = f"{source_id}{safe_suffix(package_path)}"
                if media_dir is not None:
                    extract_archive_member(archive, package_path, media_dir / extracted_file)
            record = {
                "source_id": source_id,
                "kind": kind,
                "story": story,
                "paragraph_index": paragraph_index,
                "relationship_id": rel_id,
                "target": target,
                "package_path": package_path,
                "package_sha256": package_hashes.get(package_path, ""),
                "extracted_file": extracted_file,
                "text": element_text(element),
                "sha256": sha256_bytes(data),
            }
            objects.append(record)
            warnings.append(
                warning(
                    "native-object-review-required",
                    "error",
                    f"Native Word {kind} requires classification and, when research content, verified rendering",
                    source_id,
                )
            )
            return record

        for paragraph_index, paragraph in enumerate(all_paragraph_xml, start=1):
            for ole in paragraph.findall(".//o:OLEObject", NS):
                add_object("ole", ole, paragraph_index, ole.get(qn("r", "id"), ""))
            for shape in paragraph.findall(".//v:shape", NS):
                if shape.find(".//v:imagedata", NS) is None:
                    add_object("vml-shape", shape, paragraph_index)
            groups = paragraph.findall(".//wpg:wgp", NS)
            if groups:
                for group in groups:
                    add_object("drawing-group", group, paragraph_index)
                else:
                    for shape in paragraph.findall(".//wps:wsp", NS):
                        add_object("word-shape", shape, paragraph_index)
        for chunk_index, chunk in enumerate(document.findall(".//w:altChunk", NS), start=1):
            record = add_object(
                "altchunk",
                chunk,
                chunk_index,
                chunk.get(qn("r", "id"), ""),
                extract_payload=True,
            )
            warnings.append(
                warning(
                    "altchunk-content-present",
                    "error",
                    "Imported altChunk content must be flattened in Word and reviewed before conversion",
                    record["source_id"],
                )
            )

        def scan_story_objects(
            root: ET.Element | None,
            story: str,
            relationships: dict[str, dict[str, str]],
        ) -> None:
            """Inventory non-body drawings, tables, and embedded objects.

            These are not promoted to research figures automatically: every
            item receives an auditable object row and must be rendered or
            explicitly classified as non-research content.
            """
            if root is None:
                return
            for paragraph_index, paragraph in enumerate(root.findall(".//w:p", NS), start=1):
                for blip in paragraph.findall(".//a:blip", NS):
                    rel_id = blip.get(qn("r", "embed")) or blip.get(qn("r", "link")) or ""
                    add_object(f"{story}-image", blip, paragraph_index, rel_id, relationships, story, True)
                for image_data in paragraph.findall(".//v:imagedata", NS):
                    rel_id = image_data.get(qn("r", "id"), "")
                    add_object(f"{story}-vml-image", image_data, paragraph_index, rel_id, relationships, story, True)
                for chart in paragraph.findall(".//c:chart", NS):
                    add_object(
                        f"{story}-chart", chart, paragraph_index,
                        chart.get(qn("r", "id"), ""), relationships, story,
                    )
                for diagram in paragraph.findall(".//dgm:relIds", NS):
                    rel_id = diagram.get(qn("r", "dm"), "") or diagram.get(qn("r", "lo"), "")
                    add_object(f"{story}-diagram", diagram, paragraph_index, rel_id, relationships, story)
                for ole in paragraph.findall(".//o:OLEObject", NS):
                    add_object(
                        f"{story}-ole", ole, paragraph_index,
                        ole.get(qn("r", "id"), ""), relationships, story,
                    )
                for shape in paragraph.findall(".//v:shape", NS):
                    if shape.find(".//v:imagedata", NS) is None:
                        add_object(f"{story}-vml-shape", shape, paragraph_index, story=story)
                groups = paragraph.findall(".//wpg:wgp", NS)
                if groups:
                    for group in groups:
                        add_object(f"{story}-drawing-group", group, paragraph_index, story=story)
                else:
                    for shape in paragraph.findall(".//wps:wsp", NS):
                        add_object(f"{story}-word-shape", shape, paragraph_index, story=story)
            for table_index, table in enumerate(root.findall(".//w:tbl", NS), start=1):
                add_object(f"{story}-table", table, table_index, story=story)
            for chunk_index, chunk in enumerate(root.findall(".//w:altChunk", NS), start=1):
                record = add_object(
                    f"{story}-altchunk",
                    chunk,
                    chunk_index,
                    chunk.get(qn("r", "id"), ""),
                    relationships,
                    story,
                    extract_payload=True,
                )
                warnings.append(
                    warning(
                        "altchunk-content-present",
                        "error",
                        "Imported altChunk content must be flattened in Word and reviewed before conversion",
                        record["source_id"],
                    )
                )

        for story, root, rel_name in (
            ("footnote", footnotes_root, "word/_rels/footnotes.xml.rels"),
            ("endnote", endnotes_root, "word/_rels/endnotes.xml.rels"),
            ("comment", comments_root, "word/_rels/comments.xml.rels"),
        ):
            scan_story_objects(root, story, relationship_map(load_xml(archive, rel_name)))

        media_files = sorted(name for name in names if name.startswith("word/media/") and not name.endswith("/"))
        for package_path in media_files:
            if package_path not in referenced_package_paths:
                warnings.append(
                    warning(
                        "unreferenced-media",
                        "info",
                        f"Embedded media is not referenced as a body figure: {package_path}",
                    )
                )

        table_captions: dict[int, str] = {}
        if body is not None:
            body_children = list(body)
            for child_index, child in enumerate(body_children):
                if child.tag != qn("w", "tbl"):
                    continue
                for nearby_index in (child_index - 1, child_index + 1, child_index - 2, child_index + 2):
                    if 0 <= nearby_index < len(body_children) and body_children[nearby_index].tag == qn("w", "p"):
                        candidate = element_text(body_children[nearby_index])
                        if is_caption(body_children[nearby_index], candidate):
                            table_captions[id(child)] = candidate
                            break

        tables: list[dict[str, Any]] = []
        for table in document.findall(".//w:tbl", NS):
            source_id = f"src-tab-{len(tables) + 1:03d}"
            matrix = table_matrix(table)
            max_columns = max((len(row) for row in matrix), default=0)
            serialized = json.dumps(matrix, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            table_record = {
                "source_id": source_id,
                "rows": len(matrix),
                "columns": max_columns,
                "cells": matrix,
                "numeric_tokens": numeric_tokens(matrix),
                "sha256": sha256_bytes(serialized),
                "caption": table_captions.get(id(table), ""),
                "has_horizontal_merge": table.find(".//w:gridSpan", NS) is not None,
                "has_vertical_merge": table.find(".//w:vMerge", NS) is not None,
            }
            tables.append(table_record)
            if table_dir is not None:
                write_table_csv(table_dir / f"{source_id}.csv", matrix)
            if table_record["has_horizontal_merge"] or table_record["has_vertical_merge"]:
                warnings.append(
                    warning(
                        "merged-table-cells",
                        "warning",
                        "Starter CSV flattens merged cells; rebuild the LaTeX table from the DOCX rendering",
                        source_id,
                    )
                )

        equations: list[dict[str, Any]] = []
        for story, root in (
            ("document", document),
            ("footnote", footnotes_root),
            ("endnote", endnotes_root),
            ("comment", comments_root),
        ):
            if root is None:
                continue
            for element in root.findall(".//m:oMath", NS):
                data = ET.tostring(element, encoding="utf-8")
                equations.append(
                    {
                        "source_id": f"src-eq-{len(equations) + 1:03d}",
                        "story": story,
                        "text": element_text(element),
                        "omml_xml": data.decode("utf-8"),
                        "sha256": sha256_bytes(data),
                    }
                )
            equations.extend(
                legacy_equation_fields(root.findall(".//w:p", NS), story, len(equations))
            )

        footnotes = note_records(footnotes_root, "footnote", "footnote")
        endnotes = note_records(endnotes_root, "endnote", "endnote")
        comments: list[dict[str, Any]] = []
        if comments_root is not None:
            for comment in comments_root.findall("./w:comment", NS):
                text = element_text(comment)
                if not text:
                    continue
                comments.append(
                    {
                        "source_id": f"src-comment-{len(comments) + 1:03d}",
                        "word_id": comment.get(qn("w", "id"), ""),
                        "author": comment.get(qn("w", "author"), ""),
                        "date": comment.get(qn("w", "date"), ""),
                        "text": text,
                        "sha256": sha256_bytes(text.encode("utf-8")),
                    }
                )

        word_sources: list[dict[str, Any]] = []
        bibliography_xml = load_xml(archive, "word/bibliography.xml")
        if bibliography_xml is not None:
            for source in bibliography_xml.findall(".//b:Source", NS):
                fields_map = flatten_bibliography_source(source)
                word_sources.append(
                    {
                        "source_id": f"src-word-ref-{len(word_sources) + 1:03d}",
                        "fields": fields_map,
                        "sha256": sha256_bytes(
                            json.dumps(fields_map, ensure_ascii=False, sort_keys=True).encode("utf-8")
                        ),
                    }
                )

        seen_word_source_hashes = {item["sha256"] for item in word_sources}
        for custom_name in sorted(
            item for item in names if item.startswith("customXml/") and item.endswith(".xml")
        ):
            custom_root = load_xml(archive, custom_name)
            if custom_root is None:
                continue
            for source in custom_root.findall(".//b:Source", NS):
                fields_map = flatten_bibliography_source(source)
                checksum = sha256_bytes(
                    json.dumps(fields_map, ensure_ascii=False, sort_keys=True).encode("utf-8")
                )
                if checksum in seen_word_source_hashes:
                    continue
                seen_word_source_hashes.add(checksum)
                word_sources.append(
                    {
                        "source_id": f"src-word-ref-{len(word_sources) + 1:03d}",
                        "fields": fields_map,
                        "package_path": custom_name,
                        "sha256": checksum,
                    }
                )

        custom_xml_managers: list[dict[str, str]] = []
        for name in sorted(item for item in names if item.startswith("customXml/") and item.endswith(".xml")):
            with archive.open(name) as handle:
                raw_prefix = handle.read(2_000_000)
            decoded = raw_prefix.decode("utf-8", errors="ignore")
            managers: list[str] = []
            for manager in ("zotero", "mendeley", "endnote", "csl"):
                if manager in decoded.lower():
                    managers.append(manager)
            if managers:
                custom_xml_managers.append(
                    {"package_path": name, "managers": ",".join(managers), "sha256": package_hashes[name]}
                )

        comments_count = len(comments)
        header_parts = sorted(name for name in names if re.fullmatch(r"word/header\d+\.xml", name))
        footer_parts = sorted(name for name in names if re.fullmatch(r"word/footer\d+\.xml", name))
        native_chart_parts = sorted(name for name in names if name.startswith("word/charts/") and name.endswith(".xml"))
        native_diagram_parts = sorted(name for name in names if name.startswith("word/diagrams/") and name.endswith(".xml"))

        header_footer_stories: list[dict[str, Any]] = []
        for package_path in header_parts + footer_parts:
            root = load_xml(archive, package_path)
            if root is None:
                continue
            story = "header" if "/header" in package_path else "footer"
            story_name = Path(package_path).stem
            rel_name = f"word/_rels/{Path(package_path).name}.rels"
            story_rels = relationship_map(load_xml(archive, rel_name))
            story_paragraphs = root.findall(".//w:p", NS)
            story_fields = citation_fields(story_paragraphs, len(fields), story_name)
            fields.extend(story_fields)
            active_fields.extend(
                active_word_fields(story_paragraphs, story_name, len(active_fields))
            )
            revisions.extend(revision_records(root, story_name, len(revisions)))
            for field in story_fields:
                if field.get("parse_status") == "incomplete":
                    warnings.append(
                        warning(
                            "citation-field-incomplete",
                            "error",
                            field.get("parse_error") or "Citation field metadata is incomplete",
                            field["source_id"],
                        )
                    )
            story_field_types = [
                node.get(qn("w", "fldCharType"), "") for node in root.findall(".//w:fldChar", NS)
            ]
            if story_field_types.count("begin") != story_field_types.count("end"):
                warnings.append(
                    warning(
                        "broken-field-structure",
                        "error",
                        f"Word field begin/end counts differ in {package_path}",
                    )
                )
            story_candidate_records = [
                {
                    "index": index,
                    "text": element_text(paragraph),
                    "story": story_name,
                    "source_locator": f"{package_path}:paragraph:{index}",
                }
                for index, paragraph in enumerate(story_paragraphs, start=1)
            ]
            story_candidate_records = suppress_semantic_citation_displays(
                story_candidate_records, story_fields, story_name
            )
            source_citations.extend(
                citation_candidates(story_candidate_records, start_index=len(source_citations))
            )
            source_citations.extend(
                superscript_citation_candidates(story_paragraphs, story_name, len(source_citations))
            )
            for element in root.findall(".//m:oMath", NS):
                data = ET.tostring(element, encoding="utf-8")
                equations.append(
                    {
                        "source_id": f"src-eq-{len(equations) + 1:03d}",
                        "story": story_name,
                        "text": element_text(element),
                        "omml_xml": data.decode("utf-8"),
                        "sha256": sha256_bytes(data),
                    }
                )
            equations.extend(
                legacy_equation_fields(story_paragraphs, story_name, len(equations))
            )
            scan_story_objects(root, story_name, story_rels)
            header_footer_stories.append(
                {
                    "package_path": package_path,
                    "sha256": package_hashes[package_path],
                    "text": "\n".join(element_text(paragraph) for paragraph in story_paragraphs if element_text(paragraph)),
                    "paragraphs": len(story_paragraphs),
                    "tables": len(root.findall(".//w:tbl", NS)),
                    "drawings": len(root.findall(".//w:drawing", NS)) + len(root.findall(".//w:pict", NS)),
                    "relationships": [
                        {
                            "id": rel_id,
                            "type": rel.get("type", ""),
                            "target": rel.get("target", ""),
                            "target_mode": rel.get("target_mode", "Internal"),
                        }
                        for rel_id, rel in sorted(story_rels.items())
                    ],
                }
            )

        referenced_object_packages: dict[str, list[str]] = {}
        for item in objects:
            package_path = item.get("package_path", "")
            if package_path:
                referenced_object_packages.setdefault(package_path, []).append(item["source_id"])
        for package_path in embedding_parts:
            source_id = f"src-object-{len(objects) + 1:03d}"
            extracted_file = f"{source_id}{safe_suffix(package_path)}"
            if media_dir is not None:
                extract_archive_member(archive, package_path, media_dir / extracted_file)
            objects.append(
                {
                    "source_id": source_id,
                    "kind": "embedded-package",
                    "story": "package",
                    "paragraph_index": 0,
                    "relationship_id": "",
                    "target": package_path,
                    "package_path": package_path,
                    "package_sha256": package_hashes[package_path],
                    "extracted_file": extracted_file,
                    "referenced_by": referenced_object_packages.get(package_path, []),
                    "text": "Embedded supporting package; never execute it",
                    "sha256": package_hashes[package_path],
                }
            )
            warnings.append(
                warning(
                    "native-object-review-required",
                    "error",
                    "Embedded package must be classified and preserved without execution",
                    source_id,
                )
            )

        ole_count = sum(item["kind"] == "ole" or item["kind"].endswith("-ole") for item in objects)

        tracked_insertions = sum(
            item["count"] for item in revisions if item["element"] in {"ins", "moveTo"}
        )
        tracked_deletions = sum(
            item["count"] for item in revisions if item["element"] in {"del", "moveFrom"}
        )
        for revision in revisions:
            warnings.append(
                warning(
                    "tracked-changes-present",
                    "error",
                    (
                        f"DOCX contains {revision['count']} {revision['element']} revision marker(s) "
                        f"in {revision['story']}; accept or reject all changes in a reviewed source copy"
                    ),
                    revision["source_id"],
                )
            )

        for active_field in active_fields:
            warnings.append(
                warning(
                    "active-word-field",
                    "error",
                    f"Potentially active Word field {active_field['command']} must be removed or flattened before conversion",
                    active_field["source_id"],
                )
            )

        if not bibliography_entries and not word_sources:
            warnings.append(
                warning(
                    "bibliography-not-detected",
                    "error",
                    "No bibliography section or Word bibliography records were detected",
                )
            )
        if bibliography_entries and not fields and not source_citations:
            warnings.append(
                warning(
                    "citations-not-detected",
                    "error",
                    "Bibliography entries exist, but no semantic fields or citation candidates were detected; perform rendered-page citation recovery before proceeding",
                )
            )

        manifest: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "source": {
                "file_name": docx_path.name,
                "absolute_path": str(docx_path),
                "bytes": docx_path.stat().st_size,
                "sha256": sha256_file(docx_path),
            },
            "counts": {
                "paragraphs": len(all_paragraph_xml),
                "top_level_paragraphs": len(top_paragraph_xml),
                "figures": len(figures),
                "tables": len(tables),
                "equations": len(equations),
                "footnotes": len(footnotes),
                "endnotes": len(endnotes),
                "comments": comments_count,
                "bibliography_entries": len(bibliography_entries),
                "word_bibliography_sources": len(word_sources),
                "citation_fields": len(fields),
                "citation_candidates": len(source_citations),
                "active_word_fields": len(active_fields),
                "media_files": len(media_files),
                "tracked_insertions": tracked_insertions,
                "tracked_deletions": tracked_deletions,
                "revision_markers": sum(item["count"] for item in revisions),
                "ole_objects": ole_count,
                "native_objects": len(objects),
                "header_parts": len(header_parts),
                "footer_parts": len(footer_parts),
                "native_chart_parts": len(native_chart_parts),
                "native_diagram_parts": len(native_diagram_parts),
                "embedded_package_parts": len(embedding_parts),
                "macro_parts": len(macro_parts),
            },
            "package_parts": package_parts,
            "outline": [
                {
                    "paragraph_index": item["index"],
                    "level": item["heading_level"],
                    "text": item["text"],
                }
                for item in top_records
                if item["heading_level"] is not None and item["text"]
            ],
            "paragraphs": paragraph_records,
            "figures": figures,
            "tables": tables,
            "objects": objects,
            "equations": equations,
            "footnotes": footnotes,
            "endnotes": endnotes,
            "comments": comments,
            "bibliography_entries": bibliography_entries,
            "word_bibliography_sources": word_sources,
            "citation_fields": fields,
            "citation_candidates": source_citations,
            "active_word_fields": active_fields,
            "revision_markup": revisions,
            "custom_xml_reference_managers": custom_xml_managers,
            "header_footer_stories": header_footer_stories,
            "warnings": warnings,
        }
        return manifest


def summary_lines(manifest: dict[str, Any]) -> list[str]:
    counts = manifest["counts"]
    errors = sum(item["severity"] == "error" for item in manifest["warnings"])
    warnings = sum(item["severity"] == "warning" for item in manifest["warnings"])
    return [
        f"Source: {manifest['source']['file_name']}",
        f"Figures: {counts['figures']} | Tables: {counts['tables']} | Equations: {counts['equations']}",
        (
            "Bibliography entries: "
            f"{counts['bibliography_entries']} | Citation fields: {counts['citation_fields']} "
            f"| Citation candidates: {counts['citation_candidates']}"
        ),
        f"Preflight findings: {errors} error(s), {warnings} warning(s)",
    ]


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inventory and extract a source DOCX")
    parser.add_argument("docx", type=Path, help="Source .docx file")
    parser.add_argument("--output", type=Path, help="Write JSON manifest here")
    parser.add_argument("--media-dir", type=Path, help="Extract exact embedded figure assets here")
    parser.add_argument("--table-dir", type=Path, help="Write source tables as UTF-8 CSV files here")
    parser.add_argument("--strict", action="store_true", help="Exit nonzero when preflight has errors")
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        manifest = inventory_docx(args.docx, args.media_dir, args.table_dir)
    except (FileNotFoundError, ValueError, zipfile.BadZipFile) as exc:
        print(f"preflight: error: {exc}", file=sys.stderr)
        return 2

    payload = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    for line in summary_lines(manifest):
        print(line, file=sys.stderr)

    has_errors = any(item["severity"] == "error" for item in manifest["warnings"])
    return 1 if args.strict and has_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
