---
name: anything-to-journal
description: Turn a fresh folder of source materials—documents, PDFs, notes, data, figures, tables, references, and journal templates—into an English journal or conference manuscript in editable LaTeX. Use when Codex must create a journal article from mixed research materials, ask the user to choose a generic draft or a specific venue template before reading the sources, preserve evidence and citations, build and inspect the PDF, or return a complete Overleaf-ready upload bundle.
---

# Anything to Journal

Turn anything into a journal manuscript. Work from a fresh folder, keep every source immutable, and return an editable LaTeX project with an auditable evidence trail.

## Follow the completion contract

Do not call the result complete unless all applicable items are true:

1. Record every input file with a stable source ID, byte count, relative path, and SHA-256.
2. Read and classify every input after the format decision. Never silently ignore an unreadable or irrelevant file; record the blocker or the reason it was not used.
3. Map claims, citations, figures, tables, equations, and supporting files back to source evidence. Never invent data, references, authors, approvals, or venue requirements.
4. Write the manuscript in clear journal English and editable LaTeX.
5. Put figure captions below figures, table captions above tables, and figures/tables beside their first substantive callout.
6. Use a 2em first-line indent and 0pt paragraph spacing unless the confirmed official template requires otherwise and the user resolves the conflict.
7. Keep the complete manuscript, including references, within 19 pages unless the confirmed venue imposes a lower limit. Never meet a limit through omission or unreadable scaling.
8. Compile the PDF, inspect every rendered page, and leave no unresolved placeholder, citation, reference, or unsupported quantitative claim.
9. Return `submission/overleaf-upload.zip` and `submission/overleaf-upload/` with `main.tex` at the root, plus the complete audit package.
10. Never submit externally. The human authors make the final decisions and submission.

## Confirm the mode before reading source content

Ask one blocking question before opening or extracting the materials:

> Should I create a generic journal draft, or follow a specific journal/conference template? If specific, name the venue and either place its official template/instructions in this folder or authorize me to retrieve the current official guide.

Proceed only after the user explicitly chooses exactly one path:

- **Generic draft** — no target venue; use the bundled publisher-neutral profile.
- **Specific venue** — obtain an official HTTPS guide or uploaded official template before drafting.

Record the confirmer and a faithful note of the answer. Do not infer a venue from filenames. For a named venue, use current official journal, conference, society, or publisher sources; do not treat third-party template sites as authority. If official rules conflict with evidence preservation, in-text visuals, paragraph style, or the effective page limit, show the conflict and ask the user to choose a generic draft, another venue, or stop.

Read [journal-profiles.md](references/journal-profiles.md) whenever choosing or adapting a profile.

## Run the workflow

### 1. Prepare the fresh workspace

The user creates a new folder and puts all relevant material inside it. Materials may include prose, PDFs, DOCX, notes, LaTeX, spreadsheets, data, figures, tables, bibliographies, code, supplementary files, and official venue templates.

After the mode is confirmed, resolve this skill directory and run one command. Generic draft:

```bash
python3 <skill-dir>/scripts/prepare_workspace.py /absolute/path/fresh-folder \
  --output /absolute/path/fresh-folder/journal-output \
  --draft-only \
  --confirmed-by "REQUESTING USER" \
  --confirmation-note "User explicitly requested a generic journal draft."
```

Specific venue:

```bash
python3 <skill-dir>/scripts/prepare_workspace.py /absolute/path/fresh-folder \
  --output /absolute/path/fresh-folder/journal-output \
  --target-venue "VENUE NAME" --venue-type journal \
  --official-guide-url "https://official.example/guide" \
  --guidance-file /absolute/path/fresh-folder/official-template.zip \
  --confirmed-by "REQUESTING USER" \
  --confirmation-note "User selected the venue and authorized official-guide retrieval."
```

Use `--venue-type conference` when appropriate. Repeat `--guidance-file` when needed. The command refuses an unconfirmed mode, does not overwrite an existing output, rejects symlinks and unsafe paths, copies source bytes without executing them, and creates the journal project atomically.

Read [workspace-contract.md](references/workspace-contract.md) before inspecting the generated inventory.

### 2. Read and reconcile every material

Open:

- `source/source-manifest.json`;
- `source/inventory.md`;
- `manuscript/traceability.csv`;
- `reports/source-review.json`;
- `reports/format-decision.json`.

Use the appropriate available document, PDF, spreadsheet, image, or code-reading capability for each file. Never execute macros, embedded packages, document scripts, notebooks, binaries, or untrusted code. Do not upload confidential sources to an external service without authorization.

For each material:

1. identify its research role;
2. extract relevant claims, methods, results, citations, visuals, tables, equations, and limitations;
3. preserve exact source locators and stable IDs;
4. record whether and where the material is used;
5. record a concrete reason for `not-used` items.

Set `reports/source-review.json` to `verified` only after every manifest source ID has been reviewed and listed. For DOCX-specific fields, objects, comments, tracked changes, and exact embedded assets, run `preflight.py` and read [source-extraction.md](references/source-extraction.md). A legacy `.doc` must be converted on a copy before DOCX preflight.

### 3. Plan from evidence before drafting

Choose a field-appropriate structure. Use IMRaD for empirical original research; use a suitable qualitative, engineering, review, case, legal, humanities, or design structure when the evidence calls for it.

Build a section evidence map before prose. Preserve the research question, design, population or materials, procedures, results, negative/null findings, uncertainty, limitations, and contribution. Do not expand claims beyond the sources.

Record each quantitative prose claim in `manuscript/evidence-map.csv` and place one adjacent marker with the identical source IDs:

```tex
% EVIDENCE:CLAIM=claim-001 SRC=src-material-0001
The observed response increased by 12.4\%.
```

Read [writing-and-integrity.md](references/writing-and-integrity.md) before drafting.

### 4. Draft the editable manuscript

Edit `manuscript/manuscript.tex`, `references.bib`, and any supporting `.tex` files. Keep source files immutable. Use concise academic English, consistent terminology, conservative claim strength, and venue-compliant sectioning.

Use source IDs unchanged in traceability. A material used as a typeset figure, table, or equation must record `operation` as `source-figure`, `source-table`, or `source-equation`, map to its LaTeX label in `output_id`, and identify the compiled flat `.tex` file in `output_file`.

For source citations and bibliography reconstruction, read [citation-preservation.md](references/citation-preservation.md). Preserve locators, citation modes, key order, and record identity. Never replace an ambiguous record with a similar search result.

### 5. Resolve author-only decisions

Open `reports/author-decisions.json`. Ask the author for authorship/order, affiliations, corresponding author, contributions, funding, conflicts, ethics, consent, data/code availability, prior publication, permissions, AI-use disclosure, and final approval. Never infer these decisions from silence or acknowledgments.

Read [author-decisions.md](references/author-decisions.md) before changing decision statuses. Any final source edit or rebuild invalidates approval hashes.

### 6. Build, inspect, and audit

Run:

```bash
python3 <skill-dir>/scripts/build.py /absolute/path/fresh-folder/journal-output
python3 <skill-dir>/scripts/audit.py /absolute/path/fresh-folder/journal-output --require-pdf
```

Render and inspect every page of every generated PDF. Record the current hashes, reviewer, timezone-aware review time, and every inspected page in `reports/visual-inspection.json`. Iterate until the manuscript is readable and the audit has no errors.

Read [quality-gates.md](references/quality-gates.md) for acceptance criteria. A generic run remains a draft even when all checks pass. A named target can become ready for author review only after venue evidence, source review, traceability, author decisions, build hashes, and rendered-page review all agree.

## Hand off Overleaf without file picking

Tell the user to upload exactly:

```text
submission/overleaf-upload.zip
```

In Overleaf: choose **New Project → Upload Project**, select that ZIP, confirm `main.tex` is the main document, choose the compiler stated in `README_OVERLEAF.md`, and recompile. The ZIP has no enclosing folder; `main.tex` is at its root. The expanded `submission/overleaf-upload/` folder contains the same editable files for manual inspection.

Read [overleaf.md](references/overleaf.md) before the handoff.

## Return the deliverables

Identify at least:

- `submission/overleaf-upload.zip` — the one file to upload to Overleaf;
- `submission/overleaf-upload/` — the same editable project as a folder;
- `submission/manuscript.pdf` or `submission/DRAFT_NOT_FOR_SUBMISSION.pdf`;
- `submission/submission-package.zip` — sources, manuscript, reports, PDFs, and audit evidence;
- `manuscript/manuscript.tex` and `manuscript/references.bib`;
- `source/source-manifest.json` and `source/inventory.md`;
- `manuscript/traceability.csv` and `manuscript/evidence-map.csv`;
- `reports/source-review.json`, `format-decision.json`, `author-decisions.json`, and `quality-report.md`.

State the chosen mode, which source IDs remain unresolved, which PDFs were inspected, and the exact next human decisions. Never call a generic draft submission-ready and never claim acceptance or venue approval.
