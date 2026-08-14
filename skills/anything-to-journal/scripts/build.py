#!/usr/bin/env python3
"""Compile and package a prepared LaTeX manuscript without shell escape."""

from __future__ import annotations

import argparse
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
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCRIPT_DIR = Path(__file__).resolve().parent
OVERLEAF_README = SCRIPT_DIR.parent / "assets" / "generic-template" / "README_OVERLEAF.md"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tool_version(command: str) -> str:
    result = run([command, "--version"], Path.cwd(), timeout=15)
    output = result["stdout"] or result["stderr"]
    return output.splitlines()[0] if output else "unknown"


def select_compiler(requested: str) -> tuple[str, str] | None:
    candidates = {
        "latexmk": "latexmk",
        "tectonic": "tectonic",
        "xelatex": "xelatex",
        "pdflatex": "pdflatex",
    }
    if requested != "auto":
        executable = shutil.which(candidates[requested])
        return (requested, executable) if executable else None
    for name in ("tectonic", "latexmk", "xelatex", "pdflatex"):
        executable = shutil.which(candidates[name])
        if executable:
            return name, executable
    return None


def run(command: list[str], cwd: Path, timeout: int = 600) -> dict[str, Any]:
    """Run a compiler with bounded logs and a hard wall-clock timeout."""
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=(os.name == "posix"),
        )
    except OSError as exc:
        return {"argv": command, "returncode": 127, "stdout": "", "stderr": str(exc), "timed_out": False}
    limit = 12000
    tails = {"stdout": bytearray(), "stderr": bytearray()}

    def drain(stream: Any, key: str) -> None:
        try:
            while True:
                chunk = stream.read(65536)
                if not chunk:
                    return
                tails[key].extend(chunk)
                overflow = len(tails[key]) - limit
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


def compile_tex(compiler_name: str, executable: str, tex_file: Path) -> dict[str, Any]:
    cwd = tex_file.parent
    # Never let an old successful PDF make a failed/no-op compiler look good.
    for suffix in (
        ".pdf", ".aux", ".blg", ".log", ".out", ".toc", ".bcf",
        ".run.xml", ".synctex.gz", ".fls", ".fdb_latexmk",
    ):
        stale = cwd / f"{tex_file.stem}{suffix}"
        if stale.is_file() or stale.is_symlink():
            stale.unlink()
    commands: list[list[str]] = []
    if compiler_name == "latexmk":
        commands.append(
            [
                executable,
                "-norc",
                "-xelatex",
                "-interaction=nonstopmode",
                "-halt-on-error",
                "-file-line-error",
                "-latexoption=-no-shell-escape",
                tex_file.name,
            ]
        )
    elif compiler_name == "tectonic":
        commands.append(
            [
                executable,
                "--keep-intermediates",
                "--keep-logs",
                "--synctex",
                "--untrusted",
                tex_file.name,
            ]
        )
    else:
        engine = executable
        base_command = [
            engine,
            "-no-shell-escape",
            "-interaction=nonstopmode",
            "-halt-on-error",
            "-file-line-error",
            tex_file.name,
        ]
        commands.append(base_command)
        bibtex = shutil.which("bibtex")
        text = tex_file.read_text(encoding="utf-8", errors="replace")
        if bibtex and re.search(r"\\bibliography\s*\{", text):
            commands.append([bibtex, tex_file.stem])
        commands.extend([base_command, base_command])

    records: list[dict[str, Any]] = []
    success = True
    for command in commands:
        record = run(command, cwd)
        records.append(record)
        if record["returncode"] != 0:
            success = False
            break
    pdf = tex_file.with_suffix(".pdf")
    if not pdf.is_file() or pdf.read_bytes()[:5] != b"%PDF-":
        success = False
    return {
        "tex": tex_file.name,
        "pdf": pdf.name,
        "success": success,
        "commands": records,
    }


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


def validate_source_tree(manuscript_dir: Path) -> None:
    root = manuscript_dir.resolve()
    for path in manuscript_dir.rglob("*"):
        relative = path.relative_to(manuscript_dir)
        if "optional" in relative.parts:
            continue
        if path.is_symlink():
            raise ValueError(f"Manuscript source tree contains an unsafe symlink: {relative}")
        if not safe_flat_name(path.name):
            raise ValueError(f"Manuscript source has an unsafe cross-platform name: {relative}")
        if path.is_file() and path.parent != manuscript_dir:
            raise ValueError(f"Submission sources must be flat; move nested file to manuscript/: {relative}")
        if path.exists() and root not in path.resolve().parents and path.resolve() != root:
            raise ValueError(f"Manuscript source escapes its directory: {relative}")


def source_files(manuscript_dir: Path) -> list[Path]:
    validate_source_tree(manuscript_dir)
    ignored_pdf_stems = {"manuscript", "supplement", "cover-letter"}
    generated_suffixes = {
        ".aux", ".blg", ".log", ".out", ".toc", ".bcf", ".run.xml",
        ".synctex.gz", ".fls", ".fdb_latexmk", ".xdv",
    }
    files: list[Path] = []
    for path in manuscript_dir.iterdir():
        if not path.is_file():
            continue
        if path.name in {".DS_Store", "Thumbs.db"}:
            continue
        if any(path.name.endswith(suffix) for suffix in generated_suffixes):
            continue
        if path.suffix.lower() == ".pdf" and path.stem in ignored_pdf_stems:
            continue
        files.append(path)
    return sorted(files)


def overleaf_source_files(manuscript_sources: list[Path]) -> list[Path]:
    """Select editable/compilable files while omitting private audit scaffolding."""
    excluded = {
        "traceability.csv",
        "evidence-map.csv",
        "journal-profile.json",
        "submission-checklist.md",
        "source-elements.tex",
    }
    by_name = {path.name: path for path in manuscript_sources if path.name not in excluded}
    selected: set[Path] = {
        path for name, path in by_name.items() if path.suffix.lower() != ".tex"
    }
    queue = [
        name for name in ("manuscript.tex", "supplement.tex", "cover-letter.tex")
        if name in by_name
    ]
    seen_tex: set[str] = set()
    while queue:
        name = queue.pop(0)
        if name in seen_tex:
            continue
        path = by_name.get(name)
        if path is None or path.suffix.lower() != ".tex":
            continue
        seen_tex.add(name)
        selected.add(path)
        text = path.read_text(encoding="utf-8", errors="replace")
        text = "\n".join(re.sub(r"(?<!\\)%.*$", "", line) for line in text.splitlines())
        for value in re.findall(r"\\(?:input|include)\s*\{([^{}]+)\}", text):
            candidate = Path(value.strip())
            if candidate.is_absolute() or len(candidate.parts) != 1:
                continue
            if not candidate.suffix:
                candidate = candidate.with_suffix(".tex")
            if safe_flat_name(candidate.name) and candidate.name in by_name:
                queue.append(candidate.name)
    return sorted(selected)


def build_zip(path: Path, files: list[Path]) -> list[str]:
    archived: list[str] = []
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for file in files:
            if file.is_symlink() or not file.is_file():
                raise ValueError(f"Refusing unsafe source file: {file}")
            archive.write(file, arcname=file.name)
            archived.append(file.name)
    return archived


def build_overleaf_bundle(
    directory: Path,
    archive_path: Path,
    manuscript_sources: list[Path],
) -> tuple[list[str], dict[str, str]]:
    """Create a flat upload tree and ZIP whose root document is main.tex."""
    directory.mkdir()
    source_names = {path.name for path in manuscript_sources}
    if "main.tex" in source_names:
        raise ValueError("manuscript/main.tex conflicts with the generated Overleaf main.tex")
    for source in manuscript_sources:
        target_name = "main.tex" if source.name == "manuscript.tex" else source.name
        shutil.copy2(source, directory / target_name)
    readme_target = directory / "README_OVERLEAF.md"
    if not readme_target.is_file():
        if OVERLEAF_README.is_symlink() or not OVERLEAF_README.is_file():
            raise ValueError("README_OVERLEAF.md template is missing or unsafe")
        shutil.copy2(OVERLEAF_README, readme_target)
    bundle_files = sorted(directory.iterdir())
    names = build_zip(archive_path, bundle_files)
    hashes = {path.name: sha256_file(path) for path in bundle_files}
    return names, hashes


def page_count(path: Path) -> int | None:
    pdfinfo = shutil.which("pdfinfo")
    if pdfinfo:
        result = run([pdfinfo, str(path)], path.parent, timeout=15)
        match = re.search(r"^Pages:\s+(\d+)", result["stdout"], re.M)
        if match:
            return int(match.group(1))
    try:
        from pypdf import PdfReader  # type: ignore

        return len(PdfReader(str(path)).pages)
    except Exception:
        return None


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# Build report",
        "",
        f"Compiler: `{report.get('compiler', 'unavailable')}`  ",
        f"Success: `{str(report['success']).lower()}`",
        "",
    ]
    if report.get("message"):
        lines.append(report["message"])
        lines.append("")
    for item in report.get("documents", []):
        state = "PASS" if item["success"] else "FAIL"
        lines.append(f"- **{state}** `{item['tex']}` → `{item['pdf']}`")
        for command in item["commands"]:
            if command["returncode"]:
                detail = command["stderr"] or command["stdout"]
                lines.append(f"  - exit {command['returncode']}: `{detail[-500:].strip()}`")
    lines.extend(
        [
            "",
            "A successful build is still a draft until `audit.py --require-pdf` passes after visual inspection.",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compile and package an Anything-to-Journal project")
    parser.add_argument("project", type=Path, help="Prepared project directory")
    parser.add_argument(
        "--compiler",
        choices=("auto", "latexmk", "tectonic", "xelatex", "pdflatex"),
        default="auto",
    )
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    project = args.project.resolve()
    manuscript_dir = project / "manuscript"
    reports_dir = project / "reports"
    submission_dir = project / "submission"
    if not project.is_dir():
        print(f"build: error: not a prepared project: {project}", file=sys.stderr)
        return 2
    submission_ok, submission_message = safe_project_roots(project, ("submission",))
    if not submission_ok:
        print(f"build: error: {submission_message}", file=sys.stderr)
        return 2
    for stale_name in (
        "submission.pdf", "DRAFT_NOT_FOR_SUBMISSION.pdf", "manuscript.pdf",
        "supplement.pdf", "cover-letter.pdf", "submission-sources.zip",
        "overleaf-upload.zip", "submission-package.zip", "submission-package.zip.sha256",
    ):
        stale = submission_dir / stale_name
        if stale.is_file() or stale.is_symlink():
            stale.unlink()
    stale_overleaf_dir = submission_dir / "overleaf-upload"
    if stale_overleaf_dir.is_symlink() or stale_overleaf_dir.is_file():
        stale_overleaf_dir.unlink()
    elif stale_overleaf_dir.is_dir():
        shutil.rmtree(stale_overleaf_dir)
    roots_ok, roots_message = safe_project_roots(
        project, ("source", "manuscript", "reports")
    )
    if not roots_ok:
        print(f"build: error: {roots_message}", file=sys.stderr)
        return 2
    stale_package_manifest = reports_dir / "submission-package-manifest.json"
    if stale_package_manifest.is_file() or stale_package_manifest.is_symlink():
        stale_package_manifest.unlink()
    atomic_write_text(
        reports_dir / "visual-inspection.json",
        json.dumps(
            {
                "status": "pending",
                "files": [],
                "reviewed_by": "",
                "reviewed_at": None,
                "instructions": "A new build invalidates all earlier visual inspection evidence.",
            },
            indent=2,
        )
        + "\n",
    )
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
    project_state["submission_ready"] = False
    project_state["submission_pdf"] = None
    atomic_write_text(
        project_file, json.dumps(project_state, ensure_ascii=False, indent=2) + "\n"
    )

    selected = select_compiler(args.compiler)
    if not selected:
        report = {
            "schema_version": "1.0",
            "success": False,
            "message": "No LaTeX compiler found. Install TeX Live with latexmk, or install Tectonic.",
            "documents": [],
        }
        atomic_write_text(reports_dir / "build-report.json", json.dumps(report, indent=2) + "\n")
        atomic_write_text(reports_dir / "build-report.md", markdown_report(report))
        print(markdown_report(report), end="")
        return 2

    compiler_name, executable = selected
    documents: list[Path] = []
    for name in ("manuscript.tex", "supplement.tex", "cover-letter.tex"):
        path = manuscript_dir / name
        if path.is_file():
            documents.append(path)
    if not documents or documents[0].name != "manuscript.tex":
        print("build: error: manuscript/manuscript.tex is missing", file=sys.stderr)
        return 2

    try:
        validate_source_tree(manuscript_dir)
    except ValueError as exc:
        report = {
            "schema_version": "1.0",
            "success": False,
            "compiler": compiler_name,
            "message": str(exc),
            "documents": [],
        }
        atomic_write_text(
            reports_dir / "build-report.json", json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        )
        atomic_write_text(reports_dir / "build-report.md", markdown_report(report))
        print(markdown_report(report), end="")
        return 2

    results = [compile_tex(compiler_name, executable, path) for path in documents]
    success = all(item["success"] for item in results)
    max_manuscript_pages = 19
    profile_path = manuscript_dir / "journal-profile.json"
    if profile_path.is_file() and not profile_path.is_symlink():
        try:
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
            configured_limit = profile.get("max_manuscript_pages") if isinstance(profile, dict) else None
            if (
                isinstance(configured_limit, int)
                and not isinstance(configured_limit, bool)
                and 1 <= configured_limit <= 19
            ):
                max_manuscript_pages = configured_limit
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
    report: dict[str, Any] = {
        "schema_version": "1.0",
        "built_at": datetime.now(timezone.utc).isoformat(),
        "success": success,
        "compiler": compiler_name,
        "compiler_path": executable,
        "compiler_version": tool_version(executable),
        "documents": results,
    }

    generated: list[str] = []
    if success:
        stage = Path(tempfile.mkdtemp(prefix=".submission-build-", dir=project))
        try:
            output_names = {
                "manuscript": "manuscript.pdf",
                "supplement": "supplement.pdf",
                "cover-letter": "cover-letter.pdf",
            }
            for result in results:
                source_pdf = manuscript_dir / result["pdf"]
                target_name = output_names[source_pdf.stem]
                shutil.copy2(source_pdf, stage / target_name)
                generated.append(target_name)
            zip_name = "submission-sources.zip"
            archive_sources = source_files(manuscript_dir)
            archived = build_zip(stage / zip_name, archive_sources)
            generated.append(zip_name)
            report["source_archive_files"] = archived
            report["source_sha256"] = {path.name: sha256_file(path) for path in archive_sources}

            overleaf_dir_name = "overleaf-upload"
            overleaf_zip_name = "overleaf-upload.zip"
            overleaf_files, overleaf_hashes = build_overleaf_bundle(
                stage / overleaf_dir_name,
                stage / overleaf_zip_name,
                overleaf_source_files(archive_sources),
            )
            generated.append(overleaf_zip_name)
            report["overleaf_upload"] = {
                "directory": f"submission/{overleaf_dir_name}",
                "archive": f"submission/{overleaf_zip_name}",
                "main_document": "main.tex",
                "files": overleaf_files,
                "sha256": overleaf_hashes,
                "archive_sha256": sha256_file(stage / overleaf_zip_name),
            }

            old_manifest = submission_dir / ".build-manifest.json"
            if old_manifest.is_file() and not old_manifest.is_symlink():
                try:
                    old_value = json.loads(old_manifest.read_text(encoding="utf-8"))
                    old_generated = old_value.get("generated", []) if isinstance(old_value, dict) else []
                except (json.JSONDecodeError, UnicodeDecodeError):
                    old_generated = []
                for name in old_generated if isinstance(old_generated, list) else []:
                    if not isinstance(name, str) or not safe_flat_name(Path(name).name):
                        continue
                    target = submission_dir / Path(name).name
                    if target.is_file() or target.is_symlink():
                        target.unlink()
            for stale_name in ("submission.pdf", "DRAFT_NOT_FOR_SUBMISSION.pdf"):
                stale = submission_dir / stale_name
                if stale.is_file() or stale.is_symlink():
                    stale.unlink()
            for path in stage.iterdir():
                os.replace(path, submission_dir / path.name)
            manifest_payload = {"generated": generated, "compiler": compiler_name}
            atomic_write_text(
                submission_dir / ".build-manifest.json",
                json.dumps(manifest_payload, indent=2) + "\n",
            )
        finally:
            shutil.rmtree(stage, ignore_errors=True)

        visual_files: list[dict[str, Any]] = []
        for name in ("manuscript.pdf", "supplement.pdf", "cover-letter.pdf"):
            path = submission_dir / name
            if path.is_file():
                visual_files.append(
                    {
                        "file": f"submission/{name}",
                        "sha256": sha256_file(path),
                        "page_count": page_count(path),
                        "pages_inspected": [],
                        "status": "pending",
                        "notes": "",
                    }
                )
        report["output_sha256"] = {
            item["file"]: item["sha256"] for item in visual_files
        }
        manuscript_record = next(
            (item for item in visual_files if item["file"] == "submission/manuscript.pdf"),
            None,
        )
        manuscript_pages = manuscript_record.get("page_count") if manuscript_record else None
        page_limit_ok = (
            isinstance(manuscript_pages, int)
            and not isinstance(manuscript_pages, bool)
            and 1 <= manuscript_pages <= max_manuscript_pages
        )
        report["manuscript_page_limit"] = {
            "maximum": max_manuscript_pages,
            "actual": manuscript_pages,
            "scope": "entire-manuscript-pdf-including-references",
            "passed": page_limit_ok,
        }
        if not page_limit_ok:
            report["success"] = False
            limit_message = (
                f"Manuscript PDF must be 1--{max_manuscript_pages} pages; "
                f"detected {manuscript_pages if manuscript_pages is not None else 'unknown'}."
            )
            report["message"] = (
                f"{report.get('message', '')}\n{limit_message}".strip()
            )
        visual_review = {
            "status": "pending",
            "files": visual_files,
            "reviewed_by": "",
            "reviewed_at": None,
            "instructions": "Render every PDF page, inspect it, then record every page number and set each status plus the top-level status to verified.",
        }
        atomic_write_text(
            reports_dir / "visual-inspection.json",
            json.dumps(visual_review, ensure_ascii=False, indent=2) + "\n",
        )

    atomic_write_text(
        reports_dir / "build-report.json",
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
    )
    atomic_write_text(reports_dir / "build-report.md", markdown_report(report))
    print(markdown_report(report), end="")
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
