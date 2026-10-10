# Clickable evidence in PDF Preview

Record locators while reading sources, then add them to `manuscript/evidence-locations.json` as the manuscript is drafted. The local Workspace places reference buttons beside matching PDF text and opens the source file with yellow text highlights or a recorded figure/table region. It uses bundled PDF.js and stays on localhost.

## Connect the existing evidence ledger

Keep `evidence-map.csv` and adjacent LaTeX `% EVIDENCE:CLAIM=... SRC=...` markers authoritative for claim text, source IDs, and review status. The JSON extends those same claim IDs with display locations; it does not establish that a claim is scientifically supported. Include important terms, values, and figure/table captions in the ledger when they need clickable references. Continue recording figure/table provenance in `traceability.csv` too.

```json
{
  "schema_version": "1.0",
  "claims": [
    {
      "claim_id": "claim-accuracy",
      "kind": "value",
      "preview": {"text": "Model accuracy was 0.9."},
      "sources": [
        {
          "source_id": "src-material-0001",
          "locator": {
            "page": 3,
            "text": "The model achieved an accuracy of 0.9 on the held-out set.",
            "label": "Results, Table 2"
          }
        },
        {
          "source_id": "src-material-0002",
          "locator": {"line_start": 2, "line_end": 2, "label": "accuracy.csv, data row"}
        }
      ]
    }
  ]
}
```

Use `kind`: `term`, `value`, `figure`, `table`, or `claim`. Each location source must already be in that claim's `source_ids`. All paths come from the source manifest; never add arbitrary file paths or remote URLs.

## Exact location conventions

- `preview.text` is the visible, rendered manuscript phrase, without LaTeX commands. Prefer a distinctive phrase over an isolated number such as `0.9`.
- Manuscript nodes use this phrase or figure/table caption, not stored page rectangles, because their position can move after recompilation. Region rectangles are for immutable source PDF pages or images.
- `locator.text` is an exact quotation from the source in its original language. It may differ from the English manuscript wording. Do not fabricate a quotation or copy a translated claim into this field.
- `page` and `occurrence` are one-based. A PDF `page` counts from the first physical page, independently of printed page numbers. Use `occurrence` only after inspecting repeated matches; with `page`, it selects within that page, otherwise within the document.
- `line_start` / `line_end` are one-based inclusive lines in a plain-text or CSV source. For DOCX previews they refer to the extracted text lines; use quoted text instead when stable line numbers are unavailable.
- `rect: [x, y, width, height]` marks a figure/table region with normalized coordinates from the top-left of the displayed page or image. Every component must be between 0 and 1, and the rectangle must fit inside the page. Pair a PDF rectangle with `page`. Verify region placement visually.
- `label` gives a human-readable table, figure, section, sheet/cell, or other locator. A label alone is descriptive; it is not an exact text highlight.

For PDF sources, use text and page for selectable-text evidence, or a visually verified page/rectangle for scanned documents, charts, and tables. There is no automatic OCR. Raster images support a recorded rectangle. Common UTF-8 text formats and CSV are shown with line numbers; DOCX is shown as extracted text. Other formats have an original-file download and the recorded locator, not a fabricated preview.

## Verification and older projects

After compilation, open each reference and check the manuscript phrase, source file identity, page/line, and highlight. Record the actual source location, not merely a plausible nearby match. Ambiguous matches require a page/occurrence or explicit region. Missing text, unsupported formats, missing files, and changed source hashes must remain visible limitations. Do not relabel them as verified just to remove a warning.

Existing projects without the JSON still show evidence-map references and source files. Precise highlights require recorded source locators. Update the map and locations when editing claims or replacing visuals, and recheck reference placement after pagination changes. The Workspace suppresses reference placement when the displayed PDF is from older LaTeX sources; rerun compilation and the formal audit after editing.

The complete project retains the JSON. The Overleaf upload omits local evidence metadata, like the existing private evidence ledger.
