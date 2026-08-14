#!/usr/bin/env python3
"""Prepare an auditable LaTeX manuscript workspace from a source DOCX."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import signal
import shutil
import subprocess
import sys
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from preflight import inventory_docx, summary_lines


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
TEMPLATE_DIR = SKILL_DIR / "assets" / "generic-template"


LATEX_ESCAPES = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def latex_escape(value: str) -> str:
    return "".join(LATEX_ESCAPES.get(char, char) for char in value)


def cjk_present(text: str) -> bool:
    return bool(re.search(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]", text))


def safe_flat_name(name: str) -> bool:
    return bool(
        name
        and name not in {".", ".."}
        and "/" not in name
        and "\\" not in name
        and ":" not in name
        and not any(ord(char) < 32 or ord(char) == 127 for char in name)
        and not name.endswith((" ", "."))
    )


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def initial_author_decisions(manifest: dict[str, Any]) -> dict[str, Any]:
    """Create a fail-closed intake for assertions source materials cannot prove."""
    prompts = [
        ("manuscript_title", "Confirm the final English manuscript title.", False),
        ("authors_and_order", "Confirm every author and the author order.", False),
        ("affiliations", "Confirm each author's current submission affiliation.", False),
        ("corresponding_author", "Confirm corresponding author name and email.", False),
        ("author_contributions", "Confirm the author-contribution statement (CRediT when applicable).", False),
        ("funding", "Confirm funding sources and grant identifiers, or confirm none.", True),
        ("conflicts_of_interest", "Confirm the competing-interests declaration.", False),
        ("ethics_approval", "Confirm ethics approval identifiers or that approval was not applicable.", True),
        ("informed_consent", "Confirm participant consent requirements and status.", True),
        ("data_availability", "Confirm the data/code availability statement and any repository links.", False),
        ("prior_publication", "Confirm source provenance, preprint, and prior-publication disclosure.", True),
        ("third_party_permissions", "Confirm permissions for every reused third-party item.", False),
        ("ai_assistance_disclosure", "Confirm disclosure required by the selected venue for AI assistance.", True),
        ("all_authors_approved", "Record explicit approval of the final files from every listed author.", False),
    ]
    return {
        "schema_version": "1.0",
        "status": "pending",
        "source_sha256": manifest["source"]["sha256"],
        "approved_source_sha256": {},
        "approved_pdf_sha256": {},
        "approved_source_recovery_sha256": None,
        "instructions": (
            "Do not infer declarations. Set status to author-confirmed, source-verified, or not-applicable; "
            "record evidence. all_authors_approved must be author-confirmed."
        ),
        "decisions": [
            {
                "id": decision_id,
                "prompt": prompt,
                "required_for_submission": True,
                "allow_not_applicable": allow_na,
                "value": None,
                "status": "pending",
                "evidence": "",
                "confirmed_by": "",
                "confirmed_at": None,
            }
            for decision_id, prompt, allow_na in prompts
        ],
    }


def initial_source_render_review(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "status": "pending",
        "source_sha256": manifest["source"]["sha256"],
        "render_file": "source/source-render.pdf",
        "rendered_from_sha256": manifest["source"]["sha256"],
        "renderer": None,
        "render_sha256": None,
        "page_count": None,
        "pages_inspected": [],
        "reviewed_by": "",
        "reviewed_at": None,
        "notes": "Render the DOCX and inspect every page against the manifest before verification.",
    }


def initial_source_recovery(manifest: dict[str, Any]) -> dict[str, Any]:
    warning_codes = {item.get("code") for item in manifest.get("warnings", [])}
    return {
        "schema_version": "1.0",
        "source_sha256": manifest["source"]["sha256"],
        "source_render_sha256": None,
        "instructions": (
            "Use only after page-by-page source-render review. For a detector miss, record recovered items "
            "with stable src-manual-* IDs and sha256(locator + NUL + source_text), or document confirmed-absent."
        ),
        "bibliography": {
            "status": "pending" if "bibliography-not-detected" in warning_codes else "not-needed",
            "outcome": None,
            "evidence": "",
            "reviewed_by": "",
            "reviewed_at": None,
            "pages_inspected": [],
            "records": [],
        },
        "citations": {
            "status": "pending" if "citations-not-detected" in warning_codes else "not-needed",
            "outcome": None,
            "evidence": "",
            "reviewed_by": "",
            "reviewed_at": None,
            "pages_inspected": [],
            "occurrences": [],
        },
    }


EVIDENCE_FIELDS = [
    "claim_id",
    "manuscript_file",
    "source_ids",
    "manuscript_claim",
    "status",
    "notes",
]


def write_evidence_map(path: Path) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        csv.DictWriter(handle, fieldnames=EVIDENCE_FIELDS).writeheader()


def source_text(manifest: dict[str, Any]) -> str:
    lines: list[str] = []
    for paragraph in manifest["paragraphs"]:
        text = paragraph["text"]
        if not text:
            continue
        level = paragraph["heading_level"]
        if level is not None:
            lines.append(f"{'#' * min(level, 6)} {text}")
        else:
            lines.append(text)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def outline_markdown(manifest: dict[str, Any]) -> str:
    lines = ["# Source outline", ""]
    if not manifest["outline"]:
        lines.append("No heading styles were detected. Reconstruct the outline from `source.txt`.")
    for item in manifest["outline"]:
        indent = "  " * max((item["level"] or 1) - 1, 0)
        lines.append(f"{indent}- `{item['paragraph_index']}` {item['text']}")
    lines.append("")
    return "\n".join(lines)


def references_text(manifest: dict[str, Any]) -> str:
    lines = ["SOURCE BIBLIOGRAPHY PARAGRAPHS", "=" * 34, ""]
    for item in manifest["bibliography_entries"]:
        lines.append(f"[{item['source_id']}] {item['text']}")
    lines.extend(["", "WORD BIBLIOGRAPHY METADATA", "=" * 27, ""])
    for item in manifest["word_bibliography_sources"]:
        lines.append(f"[{item['source_id']}] {json.dumps(item['fields'], ensure_ascii=False, sort_keys=True)}")
    lines.extend(["", "SEMANTIC CITATION FIELDS", "=" * 24, ""])
    for item in manifest["citation_fields"]:
        lines.append(
            f"[{item['source_id']}] manager={item['manager']} display={item['display_text']}\n"
            f"  instruction={item['instruction']}"
        )
    lines.extend(["", "PLAIN-TEXT CITATION CANDIDATES", "=" * 30, ""])
    for item in manifest["citation_candidates"]:
        lines.append(f"[{item['source_id']}] {item['text']} :: {item['context']}")
    return "\n".join(lines).rstrip() + "\n"


def findings_markdown(manifest: dict[str, Any], pandoc_findings: list[dict[str, str]]) -> str:
    findings = list(manifest["warnings"]) + pandoc_findings
    lines = ["# Preflight findings", ""]
    for summary in summary_lines(manifest):
        lines.append(f"- {summary}")
    lines.append("")
    if not findings:
        lines.append("No automated preflight findings.")
    else:
        for item in findings:
            source = f" (`{item['source_id']}`)" if item.get("source_id") else ""
            lines.append(f"- **{item['severity'].upper()} {item['code']}**{source}: {item['message']}")
    lines.extend(
        [
            "",
            "Errors block submission-ready status. Warnings require documented review; they are not permission to omit source material.",
            "",
        ]
    )
    return "\n".join(lines)


def table_tex(table: dict[str, Any]) -> str:
    source_id = table["source_id"]
    matrix: list[list[str]] = table["cells"]
    columns = max(table["columns"], 1)
    spec = "Y" * columns
    lines = [
        f"% source-table-sha256: {table['sha256']}",
        f"% source-numeric-tokens: {json.dumps(table['numeric_tokens'], ensure_ascii=False)}",
        r"\begin{table}[!htbp]",
        r"\centering",
        r"\small",
        f"\\caption{{[[ENGLISH CAPTION REQUIRED FOR {source_id}]]}}",
        f"\\label{{{source_id}}}",
        r"\begingroup",
        r"\setlength{\tabcolsep}{4pt}",
        r"\begin{adjustbox}{max width=\linewidth,max totalheight=0.42\textheight,center}",
        f"\\begin{{tabularx}}{{\\linewidth}}{{{spec}}}",
        r"\toprule",
    ]
    for row_index, row in enumerate(matrix):
        padded = row + [""] * (columns - len(row))
        wrapped = [
            f"\\sourcecell{{{source_id}-r{row_index + 1:03d}-c{column_index + 1:03d}}}"
            f"{{{latex_escape(cell)}}}"
            for column_index, cell in enumerate(padded)
        ]
        lines.append(" & ".join(wrapped) + r" \\")
        if row_index == 0:
            lines.append(r"\midrule")
    lines.extend(
        [
            r"\bottomrule",
            r"\end{tabularx}",
            r"\end{adjustbox}",
            r"\endgroup",
            f"% [[TRANSLATE NOTES, REBUILD MERGED CELLS, AND VERIFY ALL VALUES FOR {source_id}]]",
            r"\end{table}",
            "",
        ]
    )
    return "\n".join(lines)


def figure_tex(figure: dict[str, Any]) -> str:
    source_id = figure["source_id"]
    caption = figure["caption"] or "Source caption was not detected"
    lines = [
        f"% source-figure-sha256: {figure['sha256']}",
        f"% source-package-path: {figure['package_path']}",
        r"\begin{figure}[!htbp]",
        r"\centering",
    ]
    if figure["exact_embedded_asset"] and figure["extracted_file"]:
        lines.append(
            f"\\includegraphics[max width=0.95\\linewidth,max height=0.55\\textheight,"
            f"keepaspectratio]{{{latex_escape(figure['extracted_file'])}}}"
        )
    else:
        lines.append(
            f"\\fbox{{\\parbox[c][45mm][c]{{0.9\\linewidth}}{{\\centering "
            f"[[RENDER OR RECONSTRUCT {source_id} FROM THE SOURCE DOCX]]}}}}"
        )
    lines.extend(
        [
            f"\\caption{{[[ENGLISH CAPTION REQUIRED FOR {source_id}; SOURCE: {latex_escape(caption)}]]}}",
            f"\\label{{{source_id}}}",
            r"\end{figure}",
            "",
        ]
    )
    return "\n".join(lines)


def source_elements_tex(manifest: dict[str, Any]) -> str:
    lines = [
        "% STAGING INDEX ONLY. Never compile or input this aggregate file.",
        "% Insert each individual input beside its first substantive in-text callout.",
        "",
    ]
    for figure in manifest["figures"]:
        lines.append(f"% \\input{{{figure['source_id']}.tex}}")
    for table in manifest["tables"]:
        lines.append(f"% \\input{{{table['source_id']}.tex}}")
    lines.append("")
    return "\n".join(lines)


TRACE_FIELDS = [
    "kind",
    "source_id",
    "source_locator",
    "source_sha256",
    "source_summary",
    "output_id",
    "output_file",
    "output_asset",
    "operation",
    "status",
    "notes",
]


def trace_rows(manifest: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []

    def add(
        kind: str,
        item: dict[str, Any],
        locator: str,
        summary: str,
        output_id: str = "",
        output_file: str = "",
        output_asset: str = "",
        operation: str = "",
    ) -> None:
        rows.append(
            {
                "kind": kind,
                "source_id": item["source_id"],
                "source_locator": locator,
                "source_sha256": item.get("sha256", ""),
                "source_summary": normalized_csv(summary),
                "output_id": output_id,
                "output_file": output_file,
                "output_asset": output_asset,
                "operation": operation,
                "status": "pending",
                "notes": "",
            }
        )

    for item in manifest["figures"]:
        add(
            "figure",
            item,
            item["package_path"] or item["target"],
            item["caption"] or item["title"] or item["kind"],
            item["source_id"],
            f"{item['source_id']}.tex",
            item["extracted_file"],
            "exact_copy" if item["exact_embedded_asset"] else "render_required",
        )
    for item in manifest["tables"]:
        add(
            "table",
            item,
            f"rows={item['rows']};columns={item['columns']}",
            item["caption"] or f"{item['rows']} x {item['columns']} source table",
            item["source_id"],
            f"{item['source_id']}.tex",
            "",
            "starter_reconstruction",
        )
    for item in manifest.get("objects", []):
        add(
            "object",
            item,
            item.get("package_path") or f"{item.get('story', 'document')}:paragraph:{item['paragraph_index']}",
            item.get("text") or item["kind"],
            output_asset=item.get("extracted_file", ""),
            operation="review_required",
        )
    for item in manifest["equations"]:
        story = item.get("story", "document")
        story_paths = {
            "document": "word/document.xml",
            "footnote": "word/footnotes.xml",
            "endnote": "word/endnotes.xml",
            "comment": "word/comments.xml",
        }
        locator = story_paths.get(story, f"word/{story}.xml")
        add("equation", item, locator, item["text"], operation="manual_reconstruction")
    for item in manifest["footnotes"]:
        add("footnote", item, f"word/footnotes.xml#{item['word_id']}", item["text"], operation="translated")
    for item in manifest["endnotes"]:
        add("endnote", item, f"word/endnotes.xml#{item['word_id']}", item["text"], operation="translated")
    for item in manifest.get("comments", []):
        add("comment", item, f"word/comments.xml#{item['word_id']}", item["text"], operation="resolved_or_translated")
    for item in manifest["bibliography_entries"]:
        add("bibliography", item, f"paragraph:{item['paragraph_index']}", item["text"], operation="metadata_reconstruction")
    for item in manifest["word_bibliography_sources"]:
        add("bibliography-metadata", item, "word/customXml bibliography", json.dumps(item["fields"], ensure_ascii=False), operation="metadata_copy")
    for item in manifest["citation_fields"]:
        add(
            "citation",
            item,
            f"{item.get('story', 'document')}:paragraph:{item['paragraph_index']}",
            item["display_text"],
            operation="field_reconstruction",
        )
    for item in manifest["citation_candidates"]:
        add(
            "citation-candidate",
            item,
            item.get("source_locator", f"paragraph:{item['paragraph_index']}"),
            item["text"],
            operation="plain_text_recovery",
        )
    return rows


def normalized_csv(text: str) -> str:
    value = re.sub(r"\s+", " ", text).strip()[:1000]
    return "'" + value if value.startswith(("=", "+", "-", "@")) else value


def write_traceability(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=TRACE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def bounded_command(
    command: list[str], timeout: int = 300, max_output_bytes: int = 12000
) -> dict[str, Any]:
    """Run a converter without allowing unbounded output or wall-clock time."""
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=(os.name == "posix"),
        )
    except OSError as exc:
        return {"argv": command, "returncode": 127, "stdout": "", "stderr": str(exc), "timed_out": False}
    tails = {"stdout": bytearray(), "stderr": bytearray()}

    def drain(stream: Any, key: str) -> None:
        try:
            while True:
                chunk = stream.read(65536)
                if not chunk:
                    return
                tails[key].extend(chunk)
                overflow = len(tails[key]) - max_output_bytes
                if overflow > 0:
                    del tails[key][:overflow]
        finally:
            stream.close()

    threads = [
        threading.Thread(target=drain, args=(process.stdout, "stdout"), daemon=True),
        threading.Thread(target=drain, args=(process.stderr, "stderr"), daemon=True),
    ]
    for thread in threads:
        thread.start()
    timed_out = False
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except (ProcessLookupError, OSError):
            pass
        returncode = process.wait()
    for thread in threads:
        thread.join(timeout=5)
    stderr = tails["stderr"].decode("utf-8", errors="replace")
    if timed_out:
        stderr = f"Process timed out after {timeout} seconds.\n{stderr}"
        returncode = 124
    return {
        "argv": command,
        "returncode": returncode,
        "stdout": tails["stdout"].decode("utf-8", errors="replace"),
        "stderr": stderr,
        "timed_out": timed_out,
    }


def pandoc_version(pandoc: str) -> str:
    result = bounded_command([pandoc, "--version"], timeout=15, max_output_bytes=4000)
    return result["stdout"].splitlines()[0] if result["stdout"] else "unknown"


def run_pandoc(docx: Path, source_dir: Path) -> tuple[dict[str, Any], list[dict[str, str]]]:
    pandoc = shutil.which("pandoc")
    if not pandoc:
        return (
            {"available": False, "commands": []},
            [
                {
                    "code": "pandoc-unavailable",
                    "severity": "warning",
                    "message": "Pandoc was not found; use source.txt/manifest or install Pandoc before semantic conversion",
                }
            ],
        )

    media_dir = source_dir / "pandoc-media"
    log_file = source_dir / "pandoc-log.json"
    commands = [
        [
            pandoc,
            str(docx),
            "--from=docx+citations",
            "--to=gfm",
            "--wrap=none",
            "--track-changes=all",
            f"--extract-media={media_dir}",
            f"--log={log_file}",
            "--output",
            str(source_dir / "source.md"),
        ],
        [
            pandoc,
            str(docx),
            "--from=docx+citations",
            "--to=json",
            "--track-changes=all",
            "--output",
            str(source_dir / "source.json"),
        ],
    ]
    records: list[dict[str, Any]] = []
    findings: list[dict[str, str]] = []
    for command in commands:
        result = bounded_command(command)
        records.append(result)
        if result["returncode"] != 0:
            findings.append(
                {
                    "code": "pandoc-conversion-failed",
                    "severity": "error",
                    "message": result["stderr"].strip() or "Pandoc conversion failed",
                }
            )
    return {"available": True, "version": pandoc_version(pandoc), "commands": records}, findings


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare an Anything-to-Journal project from one DOCX")
    parser.add_argument("docx", type=Path, help="Source .docx material")
    parser.add_argument("--output", type=Path, required=True, help="New output directory")
    parser.add_argument("--no-copy-source", action="store_true", help="Do not copy the original DOCX into source/")
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument(
        "--draft-only",
        action="store_true",
        help="The user explicitly chose a publisher-neutral draft with no target venue",
    )
    choice.add_argument("--target-venue", help="Confirmed target journal or conference name")
    parser.add_argument("--venue-type", choices=("journal", "conference"))
    parser.add_argument("--official-guide-url", help="Current official HTTPS author-guidance URL")
    parser.add_argument(
        "--guidance-file",
        action="append",
        type=Path,
        default=[],
        help="Uploaded official instructions/template; repeat for multiple files",
    )
    parser.add_argument("--confirmed-by", required=True, help="Person who made the format choice")
    parser.add_argument(
        "--confirmation-note",
        required=True,
        help="Exact or faithful record of the user's target-format or draft-only confirmation",
    )
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.confirmed_by.strip() or not args.confirmation_note.strip():
        print("prepare: error: format confirmation identity and note must not be blank", file=sys.stderr)
        return 2
    target_venue = args.target_venue.strip() if isinstance(args.target_venue, str) else None
    if args.draft_only:
        if args.venue_type or args.official_guide_url or args.guidance_file:
            print("prepare: error: draft-only cannot include target-venue guidance", file=sys.stderr)
            return 2
    else:
        if not target_venue:
            print("prepare: error: --target-venue must not be blank", file=sys.stderr)
            return 2
        if not args.venue_type:
            print("prepare: error: --venue-type is required with --target-venue", file=sys.stderr)
            return 2
        if not args.official_guide_url and not args.guidance_file:
            print(
                "prepare: error: a target venue needs --official-guide-url or --guidance-file",
                file=sys.stderr,
            )
            return 2
    if args.official_guide_url and not re.fullmatch(r"https://[^\s]+", args.official_guide_url):
        print("prepare: error: --official-guide-url must be HTTPS", file=sys.stderr)
        return 2
    guidance_files = [path.expanduser().resolve() for path in args.guidance_file]
    if len({path.name.casefold() for path in guidance_files}) != len(guidance_files):
        print("prepare: error: guidance files must have unique filenames", file=sys.stderr)
        return 2
    for path in guidance_files:
        if path.is_symlink() or not path.is_file() or not safe_flat_name(path.name):
            print(f"prepare: error: unsafe or missing guidance file: {path}", file=sys.stderr)
            return 2
    # Capture the confirmed choice before resolving, opening, or inventorying
    # the user's DOCX. The completed project records this pre-access timestamp.
    format_confirmed_at = datetime.now(timezone.utc).isoformat()
    docx = args.docx.resolve()
    output = args.output.resolve()
    if output.exists():
        print(f"prepare: error: output already exists: {output}", file=sys.stderr)
        return 2
    if not TEMPLATE_DIR.is_dir():
        print(f"prepare: error: template is missing: {TEMPLATE_DIR}", file=sys.stderr)
        return 2

    output.parent.mkdir(parents=True, exist_ok=True)
    temp_root = Path(tempfile.mkdtemp(prefix=f".{output.name}-prepare-", dir=output.parent))
    try:
        source_dir = temp_root / "source"
        manuscript_dir = temp_root / "manuscript"
        reports_dir = temp_root / "reports"
        submission_dir = temp_root / "submission"
        source_dir.mkdir()
        manuscript_dir.mkdir()
        reports_dir.mkdir()
        submission_dir.mkdir()
        shutil.copytree(TEMPLATE_DIR, manuscript_dir, dirs_exist_ok=True)

        manifest = inventory_docx(
            docx,
            media_dir=manuscript_dir,
            table_dir=source_dir / "tables",
        )
        write_json(source_dir / "source-manifest.json", manifest)
        (source_dir / "source.txt").write_text(source_text(manifest), encoding="utf-8")
        (source_dir / "source-outline.md").write_text(outline_markdown(manifest), encoding="utf-8")
        (source_dir / "source-references.txt").write_text(references_text(manifest), encoding="utf-8")
        if manifest["equations"]:
            equations_dir = source_dir / "equations"
            equations_dir.mkdir()
            for equation in manifest["equations"]:
                if equation.get("omml_xml"):
                    (equations_dir / f"{equation['source_id']}.omml.xml").write_text(
                        equation["omml_xml"] + "\n", encoding="utf-8"
                    )
                else:
                    field_evidence = (
                        f"instruction: {equation.get('instruction', '')}\n"
                        f"display: {equation.get('text', '')}\n"
                    )
                    (equations_dir / f"{equation['source_id']}.field.txt").write_text(
                        field_evidence, encoding="utf-8"
                    )
        if not args.no_copy_source:
            shutil.copy2(docx, source_dir / "original.docx")

        guidance_records: list[dict[str, str]] = []
        if args.official_guide_url:
            guidance_records.append({"kind": "official-url", "url": args.official_guide_url})
        if guidance_files:
            guidance_dir = source_dir / "format-guidance"
            guidance_dir.mkdir()
            for guidance in guidance_files:
                destination = guidance_dir / guidance.name
                shutil.copy2(guidance, destination)
                guidance_records.append(
                    {
                        "kind": "uploaded-file",
                        "path": f"source/format-guidance/{guidance.name}",
                        "sha256": sha256_file(destination),
                    }
                )

        profile_path = manuscript_dir / "journal-profile.json"
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        profile.update(
            {
                "format_mode": "draft-only" if args.draft_only else "target",
                "venue_type": None if args.draft_only else args.venue_type,
                "target_venue": None if args.draft_only else target_venue,
                "target_journal": target_venue if args.venue_type == "journal" else None,
                "target_conference": target_venue if args.venue_type == "conference" else None,
                "official_guide_url": args.official_guide_url,
                "format_guidance": guidance_records,
                "status": "interchange-draft" if args.draft_only else "target-intake",
            }
        )
        write_json(profile_path, profile)
        write_json(
            reports_dir / "format-decision.json",
            {
                "schema_version": "1.0",
                "status": "confirmed",
                "confirmation_phase": "before-source-access",
                "source_sha256": manifest["source"]["sha256"],
                "format_mode": profile["format_mode"],
                "venue_type": profile["venue_type"],
                "target_venue": profile["target_venue"],
                "official_guide_url": args.official_guide_url,
                "format_guidance": guidance_records,
                "confirmed_by": args.confirmed_by.strip(),
                "confirmed_at": format_confirmed_at,
                "confirmation_note": args.confirmation_note.strip(),
            },
        )

        for figure in manifest["figures"]:
            (manuscript_dir / f"{figure['source_id']}.tex").write_text(
                figure_tex(figure), encoding="utf-8"
            )
        for table in manifest["tables"]:
            (manuscript_dir / f"{table['source_id']}.tex").write_text(table_tex(table), encoding="utf-8")
        (manuscript_dir / "source-elements.tex").write_text(source_elements_tex(manifest), encoding="utf-8")
        write_traceability(manuscript_dir / "traceability.csv", trace_rows(manifest))
        write_evidence_map(manuscript_dir / "evidence-map.csv")
        write_json(reports_dir / "author-decisions.json", initial_author_decisions(manifest))
        write_json(reports_dir / "source-render-review.json", initial_source_render_review(manifest))
        write_json(reports_dir / "source-recovery.json", initial_source_recovery(manifest))

        unsafe_codes = {
            "macro-payload-present",
            "embedded-package-parts",
            "external-package-relationship",
            "linked-figure",
            "active-word-field",
            "altchunk-content-present",
        }
        unsafe_findings = [
            item
            for item in manifest["warnings"]
            if item["code"] in unsafe_codes
            and (item["severity"] == "error" or item["code"] == "embedded-package-parts")
        ]
        if unsafe_findings:
            tooling = {"available": bool(shutil.which("pandoc")), "commands": [], "skipped": "unsafe DOCX preflight"}
            pandoc_findings = [
                {
                    "code": "pandoc-skipped-for-safety",
                    "severity": "error",
                    "message": "Pandoc conversion was skipped because the DOCX contains unsafe external or executable package content",
                }
            ]
        else:
            tooling, pandoc_findings = run_pandoc(docx, source_dir)
        write_json(
            source_dir / "tooling.json",
            {
                "prepared_at": datetime.now(timezone.utc).isoformat(),
                "python": sys.version.split()[0],
                "pandoc": tooling,
                "template_profile": "generic-imrad-num",
            },
        )
        (reports_dir / "preflight-findings.md").write_text(
            findings_markdown(manifest, pandoc_findings), encoding="utf-8"
        )
        write_json(
            temp_root / "project.json",
            {
                "schema_version": "1.0",
                "source_manifest": "source/source-manifest.json",
                "traceability": "manuscript/traceability.csv",
                "profile": "manuscript/journal-profile.json",
                "author_decisions": "reports/author-decisions.json",
                "source_render_review": "reports/source-render-review.json",
                "source_recovery": "reports/source-recovery.json",
                "format_decision": "reports/format-decision.json",
                "evidence_map": "manuscript/evidence-map.csv",
                "submission_package": "submission/submission-package.zip",
                "submission_ready": False,
            },
        )

        os.rename(temp_root, output)
    except Exception as exc:  # Preserve a clean target on any failure.
        shutil.rmtree(temp_root, ignore_errors=True)
        print(f"prepare: error: {exc}", file=sys.stderr)
        return 2

    print(f"Prepared project: {output}")
    print(f"Manifest: {output / 'source' / 'source-manifest.json'}")
    print(f"Draft LaTeX: {output / 'manuscript' / 'manuscript.tex'}")
    print(f"Format mode: {'draft-only' if args.draft_only else args.venue_type + ' target'}")
    error_count = sum(item["severity"] == "error" for item in manifest["warnings"])
    if error_count:
        print(f"Preflight blockers: {error_count}; inspect reports/preflight-findings.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
