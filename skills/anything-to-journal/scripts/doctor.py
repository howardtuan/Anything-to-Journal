#!/usr/bin/env python3
"""Report local dependencies for the Anything-to-Journal workflow."""

from __future__ import annotations

import argparse
import json
import os
import signal
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Iterable


def bounded_command(command: list[str], timeout: int = 15, limit: int = 4000) -> dict[str, Any]:
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


def version(executable: str) -> str:
    fallback = "unknown"
    for flag in ("--version", "-v"):
        result = bounded_command([executable, flag])
        output = result["stdout"] or result["stderr"]
        if output and fallback == "unknown":
            fallback = output.splitlines()[0]
        if result["returncode"] == 0 and output:
            return output.splitlines()[0]
    return fallback


def tool(names: list[str], required: bool, purpose: str, recommended: bool = False) -> dict[str, Any]:
    for name in names:
        path = shutil.which(name)
        if path:
            return {
                "name": name,
                "status": "ok",
                "required": required,
                "recommended": recommended,
                "path": path,
                "version": version(path),
                "purpose": purpose,
            }
    return {
        "name": " or ".join(names),
        "status": "missing",
        "required": required,
        "recommended": recommended,
        "path": None,
        "version": None,
        "purpose": purpose,
    }


def tex_packages(compiler_name: str | None) -> list[dict[str, Any]]:
    kpsewhich = shutil.which("kpsewhich")
    unicode_engine = compiler_name in {"latexmk", "tectonic", "xelatex"}
    pdftex_engine = compiler_name == "pdflatex"
    packages = [
        ("article.cls", True, False, "generic manuscript class"),
        ("geometry.sty", True, False, "page geometry"),
        ("iftex.sty", True, False, "engine-dependent font setup"),
        ("amsmath.sty", True, False, "equations"),
        ("amssymb.sty", True, False, "mathematical symbols"),
        ("booktabs.sty", True, False, "publication tables"),
        ("graphicx.sty", True, False, "figure inclusion"),
        ("longtable.sty", True, False, "multipage tables"),
        ("tabularx.sty", True, False, "width-constrained tables"),
        ("threeparttable.sty", True, False, "table notes"),
        ("array.sty", True, False, "table column definitions"),
        ("siunitx.sty", True, False, "numbers and units"),
        ("natbib.sty", True, False, "numeric citations"),
        ("unsrtnat.bst", True, False, "generic bibliography style"),
        ("microtype.sty", True, False, "typographic refinement"),
        ("setspace.sty", True, False, "line spacing"),
        ("lineno.sty", True, False, "line numbers"),
        ("caption.sty", True, False, "captions"),
        ("subcaption.sty", True, False, "subfigures"),
        ("indentfirst.sty", True, False, "indent the first paragraph after headings"),
        ("placeins.sty", True, False, "keep figures and tables in their evidence section"),
        ("adjustbox.sty", True, False, "fit figures to readable page bounds"),
        ("xurl.sty", True, False, "URL line breaking"),
        ("hyperref.sty", True, False, "cross-references and links"),
        ("fontspec.sty", unicode_engine, not unicode_engine, "XeTeX/Tectonic font setup"),
        ("fontenc.sty", pdftex_engine, not pdftex_engine, "pdfTeX font encoding"),
        ("inputenc.sty", pdftex_engine, not pdftex_engine, "pdfTeX UTF-8 input"),
        ("lmodern.sty", pdftex_engine, not pdftex_engine, "pdfTeX Latin Modern fonts"),
        ("letter.cls", False, True, "optional target-journal cover letter"),
    ]
    results: list[dict[str, Any]] = []
    for package, required, recommended, purpose in packages:
        if kpsewhich:
            check = bounded_command([kpsewhich, package])
            package_path = check["stdout"].strip() or None
            package_status = "ok" if package_path else "missing"
            availability_note = ""
        else:
            package_path = None
            package_status = "deferred"
            availability_note = "; kpsewhich unavailable, so the compiler build must verify availability"
        results.append(
            {
                "name": package,
                "status": package_status,
                "required": required,
                "recommended": recommended,
                "path": package_path,
                "purpose": f"generic-imrad-num: {purpose}{availability_note}",
            }
        )
    return results


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check Anything-to-Journal dependencies")
    parser.add_argument("--json", type=Path, help="Write the report as JSON")
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    compiler = tool(["tectonic", "latexmk", "xelatex", "pdflatex"], True, "compile the submission PDF")
    checks = [
        {
            "name": "python",
            "status": "ok" if sys.version_info >= (3, 10) else "unsupported",
            "required": True,
            "path": sys.executable,
            "version": sys.version.split()[0],
            "purpose": "DOCX inventory and audit scripts",
        },
        tool(
            ["pandoc"],
            False,
            "recommended semantic DOCX conversion with citation fields; standard-library extraction remains available",
            recommended=True,
        ),
        compiler,
        tool(["bibtex"], False, "bibliography compilation when not using Tectonic"),
        tool(["pdftoppm", "mutool"], False, "render every PDF page for visual QA"),
        tool(["pdfinfo"], False, "verify PDF page counts"),
        tool(["libreoffice", "soffice"], False, "render native Word charts, SmartArt, and OLE content"),
        tool(["inkscape", "magick", "convert"], False, "convert unsupported vector/raster artwork while retaining originals"),
    ]
    checks.extend(tex_packages(compiler["name"] if compiler["status"] == "ok" else None))
    report = {
        "ready": not any(
            item["required"] and item["status"] in {"missing", "unsupported"} for item in checks
        ),
        "checks": checks,
    }
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for item in checks:
        if item["status"] == "ok":
            marker = "OK"
        elif item["status"] == "deferred":
            marker = "DEFERRED"
        elif item["required"]:
            marker = "MISSING"
        elif item.get("recommended"):
            marker = "RECOMMEND"
        else:
            marker = "OPTIONAL"
        print(f"[{marker:10}] {item['name']}: {item['purpose']}")
    print("Ready for full pipeline." if report["ready"] else "Required dependencies are missing.")
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
