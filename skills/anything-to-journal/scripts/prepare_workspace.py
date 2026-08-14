#!/usr/bin/env python3
"""Prepare an auditable journal workspace from a folder of arbitrary materials."""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import re
import shutil
import stat
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from prepare import (
    initial_author_decisions,
    normalized_csv,
    safe_flat_name,
    sha256_file,
    write_evidence_map,
    write_json,
    write_traceability,
)


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
TEMPLATE_DIR = SKILL_DIR / "assets" / "generic-template"

EMPTY_MANIFEST_LISTS = (
    "warnings",
    "paragraphs",
    "figures",
    "tables",
    "objects",
    "equations",
    "footnotes",
    "endnotes",
    "comments",
    "bibliography_entries",
    "word_bibliography_sources",
    "citation_fields",
    "citation_candidates",
    "active_word_fields",
    "revision_markup",
    "package_parts",
    "outline",
    "custom_xml_reference_managers",
    "header_footer_stories",
)

WORKSPACE_AGGREGATE_METHOD = (
    "sha256-v1(source_id\\0original_relative_path\\0copied_file"
    "\\0file_sha256\\0bytes\\n)"
)


def workspace_aggregate(materials: list[dict[str, Any]]) -> str:
    """Hash the ordered material identity, path, copied name, content hash, and size."""
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


def safe_material_extension(path: Path) -> str:
    suffix = path.suffix.lower()
    if re.fullmatch(r"\.[a-z0-9]{1,12}", suffix):
        return suffix
    return ""


def role_hint(path: Path) -> str:
    extension = path.suffix.lower()
    if extension in {".cls", ".sty", ".bst", ".bbx", ".cbx"}:
        return "venue-template"
    if extension in {".bib", ".ris", ".enl", ".nbib"}:
        return "references"
    if extension in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".eps", ".svg"}:
        return "figure"
    if extension in {".csv", ".tsv", ".xls", ".xlsx", ".ods", ".sav", ".dta", ".json"}:
        return "data"
    if extension in {".py", ".r", ".m", ".jl", ".ipynb", ".do", ".sas"}:
        return "code"
    if extension in {".pdf", ".doc", ".docx", ".odt", ".rtf", ".md", ".txt", ".tex"}:
        return "document"
    if re.search(r"(?:journal|author|instruction|template|guide)", path.stem, re.I):
        return "venue-guidance"
    return "unclassified"


def scan_workspace(root: Path) -> list[tuple[str, Path]]:
    """Inspect names and file types without opening any material content."""
    materials: list[tuple[str, Path]] = []

    def visit(directory: Path, relative_parts: tuple[str, ...]) -> None:
        try:
            entries = sorted(
                os.scandir(directory), key=lambda entry: (entry.name.casefold(), entry.name)
            )
        except OSError as exc:
            raise ValueError(f"Cannot inspect source workspace: {directory}: {exc}") from exc
        for entry in entries:
            if not safe_flat_name(entry.name):
                raise ValueError(f"Unsafe material path component: {entry.name!r}")
            relative = relative_parts + (entry.name,)
            relative_text = Path(*relative).as_posix()
            if entry.is_symlink():
                raise ValueError(f"Source workspace contains a symlink: {relative_text}")
            if entry.is_dir(follow_symlinks=False):
                visit(Path(entry.path), relative)
            elif entry.is_file(follow_symlinks=False):
                materials.append((relative_text, Path(entry.path)))
            else:
                raise ValueError(f"Source workspace contains a non-regular entry: {relative_text}")

    visit(root, ())
    if not materials:
        raise ValueError("Source workspace contains no regular material files")
    return sorted(materials, key=lambda item: (item[0].casefold(), item[0]))


def copy_material(source: Path, destination: Path) -> tuple[str, int]:
    """Copy and hash one regular file without following a last-component symlink."""
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(source, flags)
    digest = hashlib.sha256()
    size = 0
    try:
        source_stat = os.fstat(descriptor)
        if not stat.S_ISREG(source_stat.st_mode):
            raise ValueError(f"Material is no longer a regular file: {source}")
        with os.fdopen(descriptor, "rb", closefd=False) as input_handle, destination.open("xb") as output_handle:
            for chunk in iter(lambda: input_handle.read(1024 * 1024), b""):
                output_handle.write(chunk)
                digest.update(chunk)
                size += len(chunk)
            output_handle.flush()
            os.fsync(output_handle.fileno())
    finally:
        os.close(descriptor)
    return digest.hexdigest(), size


def inventory_markdown(materials: list[dict[str, Any]], aggregate_sha256: str) -> str:
    lines = [
        "# Source material inventory",
        "",
        f"Aggregate SHA-256: `{aggregate_sha256}`",
        "",
        "| Source ID | Original relative path | Copied material | Bytes | SHA-256 |",
        "|---|---|---|---:|---|",
    ]
    for item in materials:
        original = str(item["original_relative_path"]).replace("|", "\\|")
        lines.append(
            f"| `{item['source_id']}` | `{original}` | `{item['copied_path']}` | "
            f"{item['bytes']} | `{item['sha256']}` |"
        )
    lines.extend(
        [
            "",
            "Edit journal files in `manuscript/`; treat `source/materials/` as immutable evidence.",
            "",
        ]
    )
    return "\n".join(lines)


def initial_source_review(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "status": "pending",
        "source_sha256": manifest["source"]["sha256"],
        "source_ids_reviewed": [item["source_id"] for item in manifest["materials"]],
        "reviewed_by": "",
        "reviewed_at": None,
        "notes": "Inspect every copied material before setting status to verified.",
    }


def material_trace_rows(materials: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "kind": "material",
            "source_id": item["source_id"],
            "source_locator": item["copied_path"],
            "source_sha256": item["sha256"],
            "source_summary": normalized_csv(str(item["original_relative_path"])),
            "output_id": "",
            "output_file": "",
            "output_asset": "",
            "operation": "review-required",
            "status": "pending",
            "notes": "",
        }
        for item in materials
    ]


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare a journal project from a fresh folder of arbitrary materials"
    )
    parser.add_argument("workspace", type=Path, help="Folder containing all source materials")
    parser.add_argument("--output", type=Path, required=True, help="New output directory")
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument(
        "--draft-only",
        action="store_true",
        help="The user explicitly chose a publisher-neutral draft with no target venue",
    )
    choice.add_argument(
        "--target-venue",
        "--target",
        dest="target_venue",
        help="Confirmed target journal or conference name",
    )
    parser.add_argument("--venue-type", "--venue", dest="venue_type", choices=("journal", "conference"))
    parser.add_argument("--official-guide-url", help="Current official HTTPS author-guidance URL")
    parser.add_argument(
        "--guidance-file",
        "--guidance",
        dest="guidance_file",
        action="append",
        type=Path,
        default=[],
        help="Uploaded official instructions/template; repeat for multiple files",
    )
    parser.add_argument("--confirmed-by", required=True, help="Person who made the format choice")
    parser.add_argument(
        "--confirmation-note",
        "--note",
        dest="confirmation_note",
        required=True,
        help="Faithful record of the user's target-format or draft-only confirmation",
    )
    return parser.parse_args(argv)


def validate_confirmation(args: argparse.Namespace) -> tuple[str | None, str | None]:
    if not args.confirmed_by.strip() or not args.confirmation_note.strip():
        return None, "format confirmation identity and note must not be blank"
    target_venue = args.target_venue.strip() if isinstance(args.target_venue, str) else None
    if args.draft_only:
        if args.venue_type or args.official_guide_url or args.guidance_file:
            return None, "draft-only cannot include target-venue guidance"
    else:
        if not target_venue:
            return None, "--target-venue must not be blank"
        if not args.venue_type:
            return None, "--venue-type is required with --target-venue"
        if not args.official_guide_url and not args.guidance_file:
            return None, "a target venue needs --official-guide-url or --guidance-file"
    if args.official_guide_url and not re.fullmatch(r"https://[^\s]+", args.official_guide_url):
        return None, "--official-guide-url must be HTTPS"
    return target_venue, None


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)

    # This validation and timestamp intentionally happen before resolving,
    # scanning, opening, or hashing any user material.
    target_venue, confirmation_error = validate_confirmation(args)
    if confirmation_error:
        print(f"prepare-workspace: error: {confirmation_error}", file=sys.stderr)
        return 2
    format_confirmed_at = datetime.now(timezone.utc).isoformat()

    raw_workspace = args.workspace.expanduser()
    raw_output = args.output.expanduser()
    if raw_workspace.is_symlink() or not raw_workspace.is_dir():
        print(f"prepare-workspace: error: unsafe or missing source workspace: {raw_workspace}", file=sys.stderr)
        return 2
    workspace = raw_workspace.resolve()
    if raw_output.exists() or raw_output.is_symlink():
        print(f"prepare-workspace: error: output already exists: {raw_output}", file=sys.stderr)
        return 2
    output = raw_output.resolve(strict=False)
    if not safe_flat_name(output.name):
        print(f"prepare-workspace: error: unsafe output directory name: {output.name!r}", file=sys.stderr)
        return 2
    if not TEMPLATE_DIR.is_dir() or TEMPLATE_DIR.is_symlink():
        print(f"prepare-workspace: error: template is missing or unsafe: {TEMPLATE_DIR}", file=sys.stderr)
        return 2

    guidance_files = [path.expanduser() for path in args.guidance_file]
    if len({path.name.casefold() for path in guidance_files}) != len(guidance_files):
        print("prepare-workspace: error: guidance files must have unique filenames", file=sys.stderr)
        return 2
    for path in guidance_files:
        if path.is_symlink() or not path.is_file() or not safe_flat_name(path.name):
            print(f"prepare-workspace: error: unsafe or missing guidance file: {path}", file=sys.stderr)
            return 2

    try:
        discovered = scan_workspace(workspace)
    except ValueError as exc:
        print(f"prepare-workspace: error: {exc}", file=sys.stderr)
        return 2

    output.parent.mkdir(parents=True, exist_ok=True)
    temp_root = Path(tempfile.mkdtemp(prefix=f".{output.name}-prepare-", dir=output.parent))
    try:
        source_dir = temp_root / "source"
        materials_dir = source_dir / "materials"
        manuscript_dir = temp_root / "manuscript"
        reports_dir = temp_root / "reports"
        submission_dir = temp_root / "submission"
        materials_dir.mkdir(parents=True)
        manuscript_dir.mkdir()
        reports_dir.mkdir()
        submission_dir.mkdir()
        shutil.copytree(TEMPLATE_DIR, manuscript_dir, dirs_exist_ok=True)

        materials: list[dict[str, Any]] = []
        for index, (relative, original) in enumerate(discovered, start=1):
            source_id = f"src-material-{index:04d}"
            extension = safe_material_extension(original)
            copied_file = f"{source_id}{extension}"
            destination = materials_dir / copied_file
            checksum, byte_count = copy_material(original, destination)
            if sha256_file(destination) != checksum:
                raise ValueError(f"Copied material hash verification failed: {relative}")
            media_type = mimetypes.guess_type(original.name)[0] or "application/octet-stream"
            materials.append(
                {
                    "source_id": source_id,
                    "original_relative_path": relative,
                    "copied_file": copied_file,
                    "copied_path": f"source/materials/{copied_file}",
                    "stored_path": f"source/materials/{copied_file}",
                    "sha256": checksum,
                    "bytes": byte_count,
                    "media_type": media_type,
                    "extension": extension,
                    "role_hint": role_hint(original),
                }
            )

        aggregate_sha256 = workspace_aggregate(materials)
        manifest: dict[str, Any] = {
            "schema_version": "1.0",
            "source": {
                "kind": "workspace",
                "sha256": aggregate_sha256,
                "aggregate_sha256": aggregate_sha256,
                "aggregate_method": WORKSPACE_AGGREGATE_METHOD,
                "material_count": len(materials),
            },
            "counts": {"materials": len(materials)},
            "materials": materials,
        }
        manifest.update({key: [] for key in EMPTY_MANIFEST_LISTS})
        write_json(source_dir / "source-manifest.json", manifest)
        (source_dir / "inventory.md").write_text(
            inventory_markdown(materials, aggregate_sha256), encoding="utf-8"
        )

        guidance_records: list[dict[str, str]] = []
        if args.official_guide_url:
            guidance_records.append({"kind": "official-url", "url": args.official_guide_url})
        if guidance_files:
            guidance_dir = source_dir / "format-guidance"
            guidance_dir.mkdir()
            for raw_guidance in guidance_files:
                guidance = raw_guidance.resolve()
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
                "source_sha256": aggregate_sha256,
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

        write_traceability(manuscript_dir / "traceability.csv", material_trace_rows(materials))
        write_evidence_map(manuscript_dir / "evidence-map.csv")
        write_json(reports_dir / "author-decisions.json", initial_author_decisions(manifest))
        write_json(reports_dir / "source-review.json", initial_source_review(manifest))
        prepared_at = datetime.now(timezone.utc).isoformat()
        write_json(
            source_dir / "tooling.json",
            {
                "prepared_at": prepared_at,
                "python": sys.version.split()[0],
                "source_kind": "workspace",
                "material_count": len(materials),
                "aggregate_method": WORKSPACE_AGGREGATE_METHOD,
                "template_profile": "generic-imrad-num",
            },
        )
        (reports_dir / "preflight-findings.md").write_text(
            "# Source intake findings\n\n"
            f"Imported {len(materials)} regular material file(s). "
            "Review every item in `source/inventory.md` and complete `source-review.json`.\n",
            encoding="utf-8",
        )
        write_json(
            temp_root / "project.json",
            {
                "schema_version": "1.0",
                "source_kind": "workspace",
                "source_manifest": "source/source-manifest.json",
                "source_review": "reports/source-review.json",
                "traceability": "manuscript/traceability.csv",
                "profile": "manuscript/journal-profile.json",
                "author_decisions": "reports/author-decisions.json",
                "format_decision": "reports/format-decision.json",
                "evidence_map": "manuscript/evidence-map.csv",
                "overleaf_upload": "submission/overleaf-upload.zip",
                "submission_package": "submission/submission-package.zip",
                "submission_ready": False,
            },
        )
        os.rename(temp_root, output)
    except Exception as exc:
        shutil.rmtree(temp_root, ignore_errors=True)
        print(f"prepare-workspace: error: {exc}", file=sys.stderr)
        return 2

    print(f"Prepared project: {output}")
    print(f"Material inventory: {output / 'source' / 'inventory.md'}")
    print(f"Draft LaTeX: {output / 'manuscript' / 'manuscript.tex'}")
    print(f"Format mode: {'draft-only' if args.draft_only else args.venue_type + ' target'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
