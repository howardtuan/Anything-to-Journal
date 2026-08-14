# Workspace contract

## Input folder

Start from a new folder containing only the materials for one manuscript. The agent may list filenames to locate the workspace, but must receive the user's draft-or-template decision before opening source content.

Useful inputs include:

- documents: PDF, DOCX, ODT, RTF, Markdown, plain text, or existing LaTeX;
- evidence: CSV, TSV, XLSX, JSON, statistical exports, code, or supplementary data;
- visuals: PNG, JPEG, TIFF, PDF, EPS, or SVG;
- references: BibTeX, RIS, EndNote exports, DOI lists, or complete reference sections;
- venue material: official author instructions, `.cls`, `.sty`, `.bst`, sample `.tex`, or an official template ZIP.

An unsupported binary is still inventory evidence. Do not execute it. Ask for an export or a safe readable copy when its content is necessary.

## Generated project

`prepare_workspace.py` creates:

```text
journal-output/
├── source/
│   ├── materials/                 # immutable safe-name copies
│   ├── source-manifest.json       # IDs, paths, types, hashes
│   ├── inventory.md               # human-readable index
│   └── tooling.json
├── manuscript/
│   ├── manuscript.tex
│   ├── references.bib
│   ├── traceability.csv
│   ├── evidence-map.csv
│   └── journal-profile.json
├── reports/
│   ├── format-decision.json
│   ├── source-review.json
│   ├── author-decisions.json
│   └── quality-report.md
├── submission/
│   ├── overleaf-upload/
│   ├── overleaf-upload.zip
│   ├── manuscript.pdf
│   └── submission-package.zip
└── project.json
```

Never edit `source/materials/`. Edit only `manuscript/`, the review records, and generated outputs.

## Stable source records

Each material has a `src-material-NNNN` identifier. Its manifest record includes the original relative path, immutable stored path, byte count, media type, extension, role hint, and SHA-256. The workspace source SHA-256 is a deterministic aggregate over the complete ordered material set.

Every material must have exactly one `traceability.csv` row:

- `verified`: the material contributed to the manuscript; name a real flat compiled `output_file` and describe the operation;
- `not-used`: the material was reviewed but not used; explain why in `notes`;
- no other terminal status is valid for a workspace material.

When a source material becomes a figure, table, or equation, use `source-figure`, `source-table`, or `source-equation` as the operation and put the corresponding LaTeX label in `output_id`.

## Complete source review

`reports/source-review.json` begins pending. Set it to verified only when:

1. its `source_sha256` equals the manifest aggregate;
2. `source_ids_reviewed` contains every material ID exactly once;
3. `reviewed_by` identifies the reviewer;
4. `reviewed_at` is timezone-aware ISO-8601;
5. notes identify unreadable exports, conflicts, or interpretation limits.

A changed file hash or incomplete ID list blocks the final audit.

## Safety

- Reject symlinks, path traversal, unsafe filenames, and an output path inside the material set.
- Never execute document macros, embedded packages, scripts, notebooks, or binaries as part of intake.
- Treat scripts and data as text/evidence unless the user separately authorizes execution.
- Do not expose confidential or unpublished materials in public logs, issues, examples, or external services.
