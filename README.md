<p align="center">
  <img src="assets/logo.svg" width="760" alt="Anything to Journal — auditable source materials to editable journal manuscript">
</p>

<p align="center"><strong>Anything in. Journal out.</strong></p>

<p align="center">Turn anything in one research folder into an evidence-traceable journal manuscript, editable LaTeX project, inspected PDF, and one-file Overleaf upload.</p>

<p align="center"><a href="https://anything-to-journal-website.howardtuan.workers.dev/">Website</a> · <a href="README.zh-TW.md">繁體中文</a></p>

# Anything to Journal

Anything to Journal is an open-source Agent Skill for building a journal or conference manuscript from whatever research material you have. Put the materials for one paper in a fresh folder, invoke the skill, choose a generic draft or a specific venue, and let the agent reconcile the complete source set into an auditable manuscript project.

The workflow accepts mixed inputs: PDFs, Word files, notes, Markdown, LaTeX, spreadsheets, datasets, figures, tables, reference exports, supplementary files, code, and official publisher templates. Every input receives a stable source ID and SHA-256 record. Used evidence maps back to the manuscript; unused material receives an explicit reason.

## What you get

- a journal or conference manuscript in editable LaTeX;
- a compiled PDF that has been checked page by page;
- a source manifest, material review record, traceability ledger, and quantitative evidence map;
- preserved figures, tables, equations, citations, and supporting files;
- author-decision and quality-gate reports;
- `submission/overleaf-upload.zip`, ready for **New Project → Upload Project** in Overleaf;
- a complete audit package for reproducibility and handoff.

The skill never invents data, references, authors, ethics approvals, permissions, or venue requirements, and it never submits a manuscript on the author's behalf.

## The workflow

```text
fresh material folder
        │
        ├─ choose: generic draft or specific venue template
        │
        ├─ inventory and review every source
        ├─ map evidence, citations, figures, tables, and equations
        ├─ write the journal manuscript
        ├─ confirm author-only decisions
        ├─ compile, inspect every PDF page, and audit
        │
        └─ journal-output/submission/overleaf-upload.zip
```

The format choice is deliberately made before source content is opened. A named journal or conference requires its current official instructions or template. A generic run uses the bundled publisher-neutral interchange profile and remains clearly labeled as a draft.

## Quick start

### 1. Create one fresh folder

```text
my-paper-materials/
├── study-notes.md
├── methods.docx
├── results.xlsx
├── analysis.csv
├── figure-01.png
├── references.bib
└── official-template.zip       # optional
```

Keep all material for one manuscript together. Do not place unrelated projects or the generated `journal-output/` from an earlier run in this folder.

### 2. Install the skill

#### Recommended — install with npx

Node.js 18 or newer is required. Install the latest published release with:

```bash
npx anything-to-journal@latest install
```

The default destination is `$CODEX_HOME/skills/anything-to-journal`, or `~/.codex/skills/anything-to-journal` when `CODEX_HOME` is unset. Restart Codex only if the skill does not appear automatically.

To update an existing installation later, run:

```bash
npx anything-to-journal@latest update
```

`install` never overwrites an existing target. `update` first verifies that the destination is an Anything-to-Journal skill, stages the new release, and then replaces the old copy atomically.

For a repository-local installation under `.agents/skills`, use:

```bash
npx anything-to-journal@latest install --repo /absolute/path/to/repository
```

Use `--destination /absolute/path/to/skills` for another explicit skills directory. Run `npx anything-to-journal@latest --help` for all options.

#### Ask your agent to install it

Copy this repository URL and send it to your agent:

```text
Please install this Agent Skill for me: https://github.com/howardtuan/Anything-to-Journal
```

#### Contributor install from a clone

Clone this repository, then run:

```bash
python3 install.py
```

Development installs use one canonical source through a symlink:

```text
~/.agents/skills/anything-to-journal -> <this-repository>/skills/anything-to-journal
```

Use `python3 install.py --mode copy` for a standalone copy, or `--repo /path/to/repository` for a repository-local installation. The installer refuses to overwrite an existing destination.

### 3. Ask the agent

Open the fresh material folder, invoke the skill, and add your request after the skill name. For example:

```text
/skill Anything-to-Journal Turn everything in this folder into a journal manuscript.
```

Before reading the materials, the agent asks you to choose:

- **Generic draft** — a publisher-neutral editable manuscript; or
- **Specific venue** — a named journal or conference using current official instructions or an official template you provide.

After that answer, the agent reads and classifies every file, drafts from the evidence, asks for human-only declarations and approvals, builds the PDFs, and returns the output folder.

## Direct workspace preparation

Agents normally run this for you. After the format decision, a generic intake is:

```bash
python3 skills/anything-to-journal/scripts/prepare_workspace.py \
  /absolute/path/my-paper-materials \
  --output /absolute/path/my-paper-materials/journal-output \
  --draft-only \
  --confirmed-by "REQUESTING USER" \
  --confirmation-note "User explicitly requested a generic journal draft."
```

A target-venue intake records the official evidence:

```bash
python3 skills/anything-to-journal/scripts/prepare_workspace.py \
  /absolute/path/my-paper-materials \
  --output /absolute/path/my-paper-materials/journal-output \
  --target-venue "VENUE NAME" \
  --venue-type journal \
  --official-guide-url "https://official.example/author-guide" \
  --guidance-file /absolute/path/my-paper-materials/official-template.zip \
  --confirmed-by "REQUESTING USER" \
  --confirmation-note "User chose the venue before source access."
```

Use `--venue-type conference` where appropriate. Inputs are copied with safe filenames and are never executed during intake.

## Output anatomy

```text
journal-output/
├── source/
│   ├── materials/                    # immutable copies
│   ├── source-manifest.json          # IDs, paths, sizes, media types, hashes
│   └── inventory.md                  # readable source index
├── manuscript/
│   ├── manuscript.tex                # main editable source
│   ├── references.bib
│   ├── traceability.csv
│   └── evidence-map.csv
├── reports/
│   ├── format-decision.json
│   ├── source-review.json
│   ├── author-decisions.json
│   ├── visual-inspection.json
│   └── quality-report.md
├── submission/
│   ├── overleaf-upload/              # expanded editable project
│   ├── overleaf-upload.zip           # upload this one file
│   ├── manuscript.pdf                # or DRAFT_NOT_FOR_SUBMISSION.pdf
│   └── submission-package.zip
└── project.json
```

The source ledger uses stable `src-material-NNNN` IDs. Every material ends as `verified` with a real output mapping or `not-used` with a concrete reason. Quantitative manuscript claims carry adjacent evidence markers matching `evidence-map.csv`.

## Edit in Overleaf

Upload exactly:

```text
journal-output/submission/overleaf-upload.zip
```

In Overleaf:

1. choose **New Project → Upload Project**;
2. select `overleaf-upload.zip`;
3. confirm `main.tex` is the main document;
4. choose the compiler named in `README_OVERLEAF.md`;
5. select **Recompile**.

The ZIP has no enclosing directory: `main.tex` is at its root. It includes the bibliography, required `.tex`/`.cls`/`.sty`/`.bst` files, and referenced assets, while excluding private evidence, reports, compiler junk, and the final PDF. You can edit `main.tex`, `references.bib`, supporting `.tex` files, and figure assets directly.

See Overleaf's official [Upload a project](https://docs.overleaf.com/managing-projects-and-files/uploading-a-project) instructions for the current interface and limits.

## Build and audit

After the manuscript and human decisions are complete:

```bash
python3 skills/anything-to-journal/scripts/build.py /absolute/path/journal-output
python3 skills/anything-to-journal/scripts/audit.py /absolute/path/journal-output --require-pdf
```

The audit checks format confirmation, complete material review, source hashes, citation and evidence mappings, figures/tables/equations, author decisions, LaTeX logs, PDF hashes, page count, visual inspection records, and archive contents. A hard failure exposes `DRAFT_NOT_FOR_SUBMISSION.pdf` and the blocker IDs instead of claiming readiness.

For `.docx` inputs, the bundled high-fidelity adapter also inventories OOXML, revisions, fields, references, media, native objects, tables, equations, notes, comments, and relationships:

```bash
python3 skills/anything-to-journal/scripts/preflight.py source-document.docx --strict
```

## Fixed quality rules

Unless a confirmed official template imposes a stricter compatible rule, the workflow requires:

- figure captions below figures and table captions above tables;
- figures and tables beside their first substantive callout;
- 2em first-line indentation and 0pt paragraph spacing;
- at most 19 complete manuscript pages, including references;
- no unresolved placeholders, citations, references, or unsupported quantitative claims;
- page-by-page inspection after the final build;
- final submission and accountability to remain with the human authors.

## Requirements

- Node.js 18 or newer for the npx installer;
- Python 3.10 or newer;
- a TeX engine: Tectonic is preferred, with XeLaTeX or LuaLaTeX supported;
- optional Pandoc for rich DOCX semantic conversion;
- optional LibreOffice/Word, Poppler, and image tools for high-fidelity rendering and inspection.

Check the local environment with:

```bash
python3 skills/anything-to-journal/scripts/doctor.py
```

## Development

Run the npx installer tests, synthetic skill tests, and skill validator:

```bash
npm test
python3 -m unittest discover -s tests -v
python3 /path/to/skill-creator/scripts/quick_validate.py skills/anything-to-journal
```

Inspect the exact npm package contents before releasing:

```bash
npm pack --dry-run
```

Maintainers must publish a new semantic version before `@latest` can deliver an update:

```bash
npm version patch
npm publish
```

The `prepublishOnly` check runs both the npx installer tests and the synthetic skill suite. Never reuse an already published npm version.

Fixtures must be synthetic. Never commit a user's unpublished sources, private data, copyrighted publisher template, or confidential results.

## License and citation

Original code and documentation are available under the [MIT License](LICENSE). User materials, generated manuscripts, publisher templates, fonts, and third-party tools retain their own licenses; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Citation metadata is provided in [CITATION.cff](CITATION.cff).
