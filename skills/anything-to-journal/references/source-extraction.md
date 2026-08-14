# DOCX extraction and preservation

This is the optional high-fidelity adapter for `.docx` materials inside an Anything-to-Journal workspace. Use the workspace manifest as the top-level authority and this procedure to preserve the internals of each Word file.

## Contents

1. [Authority layers](#authority-layers)
2. [DOCX preflight](#docx-preflight)
3. [Figures and objects](#figures-and-objects)
4. [Tables](#tables)
5. [Equations and notes](#equations-and-notes)
6. [Pandoc layer](#pandoc-layer)
7. [Security and privacy](#security-and-privacy)

## Authority layers

Treat the raw OOXML inventory as the preservation authority, Pandoc output as a semantic convenience, and LaTeX/PDF as generated targets. A Pandoc conversion that looks plausible does not prove that every source object survived.

Keep the source DOCX immutable. `prepare.py` records its SHA-256, inventories package parts, copies exact embedded asset bytes, writes table CSV files, and creates stable `src-*` child IDs. Never renumber those IDs after drafting begins; preserve the parent `src-material-NNNN` mapping in the workspace ledger.

After unsafe fields/content and unresolved revisions are cleared, render the source independently to `source/source-render.pdf` with macros and external-link updates disabled, inspect every page, and record its hash, renderer, complete page list, reviewer, and review time in `reports/source-render-review.json`. This human-attested render is the visual authority for floating layout, text boxes, headers/footers, comments, native drawings, crop/rotation, color/transparency effects, and AlternateContent that XML text order cannot prove.

## DOCX preflight

Run `preflight.py` directly when diagnosing a source:

```bash
python3 <skill-dir>/scripts/preflight.py source-document.docx \
  --output source-manifest.json \
  --media-dir extracted-media \
  --table-dir extracted-tables
```

Resolve every severity `error` before claiming readiness. Create a new, explicitly resolved DOCX and rerun preparation when the source contains tracked changes. Do not choose “accept” or “reject” on the author's behalf.

`preflight.py` scans document text plus footnotes, endnotes, comments, headers, and footers for revisions and potentially active Word fields. DDE/DDEAUTO, INCLUDE/IMPORT/LINK/RD/DATABASE/MACROBUTTON, macro payloads, external package relationships, and `w:altChunk` are hard blockers. Do not render or open such a document with automatic updates enabled; ask for a flattened, reviewed copy. `prepare.py` skips Pandoc on unsafe packages.

Check cover/title pages manually for title, author, degree, affiliation, supervisor, dates, and contact information. Metadata may be in text boxes that ordinary paragraph extraction reads out of order.

## Figures and objects

Distinguish a figure occurrence from an asset. The same embedded asset may appear more than once; preserve every occurrence and label, while retaining a single content hash for deduplication.

Treat preservation and placement separately. Always retain the byte-exact original asset in the project. Typeset each figure occurrence once, using either that original or one verified derivative; do not typeset both merely to demonstrate that the original was retained.

`prepare.py` creates one `src-fig-NNN.tex` and one `src-tab-NNN.tex` starter per occurrence plus an uncompiled `source-elements.tex` staging index. Insert each individual starter into `manuscript.tex` beside its first substantive callout. Never compile the aggregate index, move source visuals to `supplement.tex`, or collect them at manuscript end.

For raster/vector images:

- retain the exact extracted original file and hash;
- preserve crop, rotation, flip, caption, alt text, and placement evidence;
- use the original directly when publication-compatible;
- when labels inside a figure are not English, retain the original and create an English `translated_derivative`; keep geometry, plotted data, scales, symbols, colors, and numeric labels invariant;
- record the derivative file, tool, operation, and verification in `traceability.csv`.
- keep the main caption below the complete visual and begin with readable maximum width/height constraints rather than forced enlargement.

Native Word chart, SmartArt, grouped shape, drawing canvas, MathType, or OLE content is not an ordinary image. Use Word/LibreOffice rendering or reconstruct it from embedded chart/workbook data. Preserve the native source package and visually compare the derivative. A placeholder, `[CHART]`, or extracted XML file is not a preserved publication figure.

Each `word/embeddings/*` payload is extracted as its own `embedded-package` evidence row and must remain byte-exact with `preserved-supporting-data`; never execute it. Its visible OLE/chart representation is a different object row and still requires rendering or reconstruction. Every branch asset in `mc:AlternateContent` is retained, but the entire displayed occurrence is render-required because branch support cannot be inferred safely from OOXML alone.

External linked media is a blocker because its bytes are absent from the DOCX. Request the missing file or a DOCX with the media embedded.

## Tables

Treat generated CSV and `.tex` as starters. OOXML may contain merged cells, repeated headers, nested tables, citations, equations, images, or cell notes that CSV flattens.

The review CSV prefixes formula-leading cells with an apostrophe to prevent spreadsheet formula/DDE execution. The canonical, unescaped cell text remains in `source-manifest.json`; never treat the CSV's protective apostrophe as source content. Generated LaTeX wraps each source coordinate in an invisible `\sourcecell{...}{...}` macro. Retain every wrapper exactly once and in source row-major order while translating its second argument.

For every table:

1. preserve row/column topology and merged-cell intent;
2. translate prose and headings only;
3. preserve the multiset of numbers, signs, decimal precision, ranges, units, sample sizes, p-values, and significance markers;
4. preserve notes and citations in the correct cells;
5. use `booktabs`, `tabularx`, `longtable`, or a journal-approved structure;
6. compare the rendered table with the Word pages before setting the trace row to `verified`.
7. keep the main caption above the first table cell and place the table in the same section as its callout.

Use the starter's maximum width/height only as a first pass. For tall or wide tables, split or redesign them without changing source-cell order or numbers. Do not reduce text below readable size merely to satisfy the 19-page limit.

Do not rasterize a table merely to avoid rebuilding it unless the target venue explicitly accepts that representation and the author approves.

## Equations and notes

OOXML MathML extraction is evidence, not guaranteed LaTeX. Legacy Word `EQ` fields are inventoried separately and written as `.field.txt` evidence. Rebuild each `src-eq-NNN`, compare symbols, indices, delimiters, and equation numbers, then map it in the ledger.

Footnotes/endnotes may contain citations, definitions, limitations, copyright notes, or substantive arguments. Move content only when journal style requires it; do not silently delete it.

Treat Word comments as unresolved editorial evidence until classified. Substantive corrections must be incorporated and traced; decorative or administrative comments may use `not-research-content` only with the resolution evidence in `notes`. Images, tables, equations, or objects found in footnotes, endnotes, comments, headers, and footers receive separate auditable object IDs even when later classified as administrative.

For every verified equation or note, set `output_file` to a compiled flat `.tex` filename and put exactly one marker immediately before the reconstructed content. Use the equation's actual LaTeX label as `output_id`; use the unchanged note source ID as the note `output_id` even though it is not visibly printed:

```tex
% TRACE:SRC=src-eq-003
\begin{equation}\label{src-eq-003}
  E = mc^2
\end{equation}

% TRACE:SRC=src-endnote-002
The qualification is retained.\footnote{Translated source endnote.}
```

Each source ID must occur in exactly one `TRACE:SRC` marker inside compiled LaTeX. Pending or unresolved rows do not receive a verified marker.

## Pandoc layer

When installed, `prepare.py` invokes Pandoc with `docx+citations` and `--track-changes=all` to produce `source.md` and `source.json`. Inspect its log and compare Pandoc figure/table/citation counts against `source-manifest.json`.

Pandoc commonly recovers Zotero, legacy Mendeley, and EndNote fields, but it is not authoritative for charts, SmartArt, modern Mendeley content controls, OLE, or complex floating layouts. Use [Pandoc's current manual](https://pandoc.org/MANUAL.html) for installed-version behavior.

## Security and privacy

- Never execute macros, OLE objects, embedded packages, or document scripts.
- Perform preflight before GUI rendering; render only a cleared copy in an offline/network-restricted session with macros and field/link updates disabled.
- Reject path traversal, suspicious compression, broken relationships, and missing external media.
- Do not upload unpublished research, personal data, or confidential results to an external service without the author's authorization.
- Never place real user materials in public issues, CI fixtures, logs, or example datasets.
- Treat external DOI/metadata lookup as enrichment; record it and never overwrite source evidence silently.
