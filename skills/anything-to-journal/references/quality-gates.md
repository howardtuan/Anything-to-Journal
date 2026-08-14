# Quality gates

Pass gates in order. A later success never cancels an earlier failure.

## 0. Format-decision integrity

- Before any preparation, the user explicitly chose a named journal/conference or generic draft-only mode.
- `reports/format-decision.json` records `confirmation_phase: before-source-access`, the aggregate source hash, confirmer, timezone-aware time, confirmation note, and the matching `journal-profile.json` mode; its time precedes `source/tooling.json` preparation time.
- A named target retains a current official HTTPS URL or uploaded official guidance/template with SHA-256.
- A venue conflict yields a generic draft, a different venue, or a stop decision before drafting; a conflicting result is never labeled venue-compliant.

## 1. Input integrity

- Every input has one stable `src-material-NNNN` ID, relative path, byte count, media type, and SHA-256; the aggregate source hash matches the complete ordered manifest.
- Every input was reviewed exactly once in `reports/source-review.json` and has one terminal `traceability.csv` row: `verified` or reasoned `not-used`.
- Immutable copies in `source/materials/` match the manifest and were never executed.
- Unreadable, encrypted, unsafe, or unsupported inputs are explicit blockers when their content is necessary.
- For DOCX inputs, package paths/relationships are safe and readable; no unresolved external media, macro payload, active field, altChunk import, executable OLE, missing package, or unresolved tracked change remains.

## 2. Evidence extraction integrity

- Claims, methods, results, limitations, citations, figures, tables, equations, and supporting files have exact material IDs and source locators.
- Every source material is either mapped to manuscript output or has a concrete `not-used` rationale.
- Source figures, tables, and equations use `source-figure`, `source-table`, or `source-equation` and reverse-map to a compiled LaTeX file and output label.
- For DOCX inputs, OOXML counts reconcile with the source render and Pandoc output; detector misses use page-complete recovery attestations and native objects have verified render/reconstruction plans.
- No missing asset, fabricated reconstruction, or silent object omission remains.

## 3. Citation integrity

- All semantic fields parse or have an explicit unresolved item.
- Citation occurrence → source record → BibTeX mappings are complete.
- Source/target citation key sets reconcile; locators and modes are preserved.
- No invented, undefined, conflicting, or uncited BibTeX record remains.
- Every BibTeX and cited key has a reverse source mapping; unannotated many-to-one collapse and source-free additions are blocked.

## 4. Figure/table integrity

- Each `src-fig-NNN` and `src-tab-NNN` label occurs exactly once in the main manuscript body.
- Every exact source asset remains unchanged in the project; each source-figure occurrence is typeset once using either the original or one verified derivative, and every derivative has provenance.
- Figure content/caption and table topology/values/units/notes match the source.
- Every figure has its caption below the complete visual; every table has its caption above the first cell.
- Every figure/table is inserted once beside a substantive callout in the same section; `source-elements.tex`, page-only `[p]`, terminal figure/table sections, and supplement placement are blocked.
- Venue/page limits never justify silent deletion or unreadably small scaling.
- Every typeset figure/table/equation environment has a verified source label; the manuscript has no source-free structure.

## 5. English and claim integrity

- No unresolved non-English prose intended for translation, placeholder, or drafting instruction remains in submission sources.
- Every result and quantitative statement maps to source evidence.
- Every quantitative prose claim has one adjacent `% EVIDENCE:CLAIM=... SRC=...` marker and one verified `manuscript/evidence-map.csv` row with the same source IDs and exact claim text.
- Numbers, units, statistical notation, equations, uncertainty, null findings, and limitations are preserved.
- Declarations, source provenance, and prior-publication status are accurate.
- The target-venue policy and any AI-use disclosure are current.

## 6. LaTeX build integrity

- Compile with shell escape disabled.
- Resolve errors, undefined citations/references, missing files/glyphs, multiply defined labels, and visible overfull boxes.
- Include `.bib`, generated `.bbl` when available, all required assets, and nonstandard class/style files in a flat source ZIP and the Overleaf ZIP.
- Never promote a PDF generated from stale sources.
- Body prose uses a 2em first-line indent, 0pt paragraph spacing, and indents the first paragraph after headings.
- The complete manuscript PDF, including references, contains 1--19 pages; an unknown count or page 20 is a hard failure.
- The build report identifies every TeX root, approved no-shell-escape command, zero return code, source/archive hash, and PDF hash; all agree with disk. Deleting a supplement or cover-letter PDF is a hard failure.

## 7. Rendered PDF integrity

Render every page of every PDF. Inspect at normal zoom and at detail zoom for:

- clipped/overflowing or microscopic tables;
- unreadable images, wrong crop/rotation, missing legends, or poor resolution;
- bad page/line breaks, isolated headings/captions, blank pages, or excess whitespace;
- missing glyphs, font substitution, broken math, `??`, or raw citation keys;
- figure/table order, numbering, captions, notes, and cross-references;
- title/author/affiliation/declaration correctness and anonymization.
- figure captions below, table captions above, contextual float placement, readable type/labels, and the 19-page maximum.

Record current PDF hashes, a positive page count equal to a fresh PDF parse, every inspected page, reviewer, and timezone-aware review time in `reports/visual-inspection.json`. A later rebuild invalidates the review automatically.

## 8. Author-decision integrity

- `reports/author-decisions.json` resolves authors/order, affiliations/contact, contributions, funding, conflicts, ethics, consent, data availability, prior publication, permissions, AI disclosure, and final approval without inference.
- `source-verified` decisions cite concrete source evidence; `not-applicable` decisions are permitted only where the schema allows and include a rationale.
- `author-confirmed` decisions record who confirmed and when; `all_authors_approved` is never source-inferred and has boolean value `true`.
- The record's aggregate source hash and complete approved source/PDF hash maps match the final build, including supplements and cover letters. A source edit or rebuild invalidates final approval.

## 9. Complete-package integrity

- Keep `submission/overleaf-upload.zip` as the one-file Overleaf handoff, with `main.tex` at ZIP root, and `submission-sources.zip` when a publisher source archive is also required.
- After the final quality report and PDF promotion/demotion, atomically rebuild `submission/submission-package.zip`.
- The complete package contains `project.json`, retained source evidence, manuscript sources/assets, reports, Overleaf/publisher source ZIPs, and final PDFs; it excludes itself, symlinks, and compiler junk.
- Reopen the ZIP and verify each manifest entry hash before returning it.

## Acceptance language

After `audit.py --require-pdf` returns zero:

- call the package **ready for author review**;
- ask authors to verify English meaning, authorship, data, citations, ethics, permissions, and target-venue compliance;
- leave submission to the human author.

If any hard gate fails, expose `DRAFT_NOT_FOR_SUBMISSION.pdf` and the exact blocker IDs. Do not produce or retain `submission.pdf`.
