---
name: anything-to-journal
description: Turn a fresh folder of source materials—documents, PDFs, notes, data, figures, tables, references, and journal templates—into an English journal or conference manuscript in editable LaTeX. Use when Codex must create a journal article from mixed research materials, ask the user to choose a generic draft or a specific venue template before reading the sources, preserve evidence and citations, build and inspect the PDF, return a complete Overleaf-ready upload bundle, or open the finished manuscript in the local PDF Preview and LaTeX Workspace.
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
11. After the deliverables exist, start the local Manuscript Workspace when the current environment can keep a localhost process alive. Return its exact URL whether or not an in-app browser is available.

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

### 7. Open the local Manuscript Workspace

After the LaTeX, PDF, references, reports, audit evidence, and submission files exist, start the same local editing surface for both Codex Desktop and ordinary browsers:

```bash
python3 <skill-dir>/scripts/workspace_editor.py /absolute/path/fresh-folder/journal-output
```

Keep the process alive and capture the printed `http://127.0.0.1:PORT` URL. The server binds only to `127.0.0.1`; it does not upload the manuscript or use a CDN. `--port 0` is the default and chooses a free port. Use `--open-browser` only when the user wants the operating system's default browser opened as well.

The Workspace defaults to **PDF Preview** and offers a **LaTeX** tab. The editor reads and writes the real files in `journal-output/manuscript/`, especially `manuscript.tex`, and discovers the other flat `.tex` and `.bib` files. It does not make an editor-only copy. Saving uses a short debounce and a shell-escape-disabled preview compile; a successful compile atomically replaces the preview, while a failed compile keeps the last successful PDF.

When changing the manuscript in chat, edit `journal-output/manuscript/manuscript.tex` or the applicable supporting `.tex`/`.bib` file—not `submission/overleaf-upload/`. The running Workspace detects that external edit, reloads it when the browser has no unsaved content, and recompiles the PDF. If the browser has unsaved content, the editor must show the external-modification conflict and refuse a stale save instead of overwriting either version.

Any editor or chat change invalidates prior audit/final-ready state. Workspace preview compilation is intentionally separate from the formal pipeline. When the user finishes editing, rerun `build.py`, inspect the new PDF pages, update visual-inspection evidence, and rerun `audit.py --require-pdf` before calling the revised manuscript complete.

If a Codex Desktop in-app browser capability is actually available, open the exact printed localhost URL there so the user sees the Workspace at the side. Do not claim an unavailable Codex API or claim the page was opened without verifying it. If it cannot be opened automatically, keep the Workspace running, return the URL, and tell the user that the same URL works in Chrome, Safari, Edge, or a Codex Desktop browser pane that accepts localhost URLs. A headless or CLI-only environment must still complete the original manuscript, build, audit, and Overleaf handoff normally.

Use this handoff wording, adapted to the actual environment and verified actions:

```text
The manuscript is complete.

You can now make final edits:
1. [Only if verified] The manuscript Workspace is open in the Codex Desktop side browser.
   - PDF Preview
   - LaTeX editing
2. Open the same Workspace in any browser:
   http://127.0.0.1:PORT

You can edit LaTeX directly or continue asking me for changes in chat.
Both edit the same manuscript files and regenerate the PDF preview.
Run the formal build and audit again after final edits.
```

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

When the Manuscript Workspace is running, also return its exact localhost URL and state whether opening it in Codex Desktop was verified, unavailable, or not attempted. Mention that `reports/workspace-invalidation.json` records post-audit edits when it exists.

State the chosen mode, which source IDs remain unresolved, which PDFs were inspected, and the exact next human decisions. Never call a generic draft submission-ready and never claim acceptance or venue approval.
