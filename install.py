#!/usr/bin/env python3
"""Install the canonical skill into an Agent Skills discovery directory."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parent
SOURCE_SKILL = PROJECT_ROOT / "skills" / "anything-to-journal"
SKILL_NAME = "anything-to-journal"


def validate_source() -> None:
    skill_md = SOURCE_SKILL / "SKILL.md"
    if not skill_md.is_file():
        raise RuntimeError(f"Canonical SKILL.md is missing: {skill_md}")
    text = skill_md.read_text(encoding="utf-8")
    if not text.startswith("---\n") or "name: anything-to-journal" not in text.split("---", 2)[1]:
        raise RuntimeError("Canonical SKILL.md has invalid frontmatter")
    # copytree dereferences symlinks by default.  Refuse them so a malicious or
    # accidental link cannot copy files from outside the open-source checkout.
    symlinks = [path for path in SOURCE_SKILL.rglob("*") if path.is_symlink()]
    if symlinks:
        relative = ", ".join(str(path.relative_to(SOURCE_SKILL)) for path in symlinks[:10])
        raise RuntimeError(f"Canonical skill contains unsupported symlink(s): {relative}")


def destination_root(args: argparse.Namespace) -> Path:
    if args.destination:
        return args.destination.expanduser().resolve()
    if args.repo:
        return args.repo.expanduser().resolve() / ".agents" / "skills"
    return Path.home() / ".agents" / "skills"


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install Anything-to-Journal as an Agent Skill")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--repo", type=Path, help="Install for one repository under .agents/skills")
    scope.add_argument("--destination", type=Path, help="Install under an explicit skills directory")
    parser.add_argument(
        "--mode",
        choices=("symlink", "copy"),
        default="symlink",
        help="Symlink for development (default) or copy for a standalone installation",
    )
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        validate_source()
    except RuntimeError as exc:
        print(f"install: error: {exc}", file=sys.stderr)
        return 2

    root = destination_root(args)
    target = root / SKILL_NAME
    root.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        current = target.resolve()
        if current == SOURCE_SKILL.resolve() and args.mode == "symlink":
            print(f"Already installed: {target} -> {current}")
            return 0
        print(f"install: error: a different symlink already exists: {target} -> {current}", file=sys.stderr)
        return 2
    if target.exists():
        print(
            f"install: error: destination already exists: {target}\n"
            "Move or remove it explicitly before installing; the installer will not overwrite it.",
            file=sys.stderr,
        )
        return 2

    if args.mode == "symlink":
        target.symlink_to(SOURCE_SKILL, target_is_directory=True)
        action = f"linked to {SOURCE_SKILL}"
    else:
        shutil.copytree(SOURCE_SKILL, target)
        action = "copied"
    print(f"Installed {SKILL_NAME}: {target} ({action})")
    print("Invoke it with: $anything-to-journal")
    print("Codex detects skill changes automatically; restart Codex only if it does not appear.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
