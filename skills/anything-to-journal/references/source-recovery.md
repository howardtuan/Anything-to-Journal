# Rendered-page source recovery

Use this procedure only when OOXML preflight reports `bibliography-not-detected` or `citations-not-detected`. It is a controlled fallback for a detector miss, not permission to invent or discard evidence.

## Required authority

1. Verify `source/source-render.pdf` against the immutable DOCX material and complete `reports/source-render-review.json` first.
2. Search every rendered page, including notes, appendices, tables, captions, and headers/footers.
3. In the affected `reports/source-recovery.json` section, set `status` to `verified`, choose `outcome: recovered` or `confirmed-absent`, and record concrete `evidence`, `reviewed_by`, a timezone-aware `reviewed_at`, and the complete `pages_inspected` list.
4. `confirmed-absent` must have an empty item list. It means the named reviewer verified every page and the content genuinely is absent; it does not mean extraction was inconvenient.

## Recovered item schema

Use `src-manual-ref-001` IDs for bibliography records and `src-manual-cite-001` IDs for citation occurrences. Each item contains:

```json
{
  "source_id": "src-manual-cite-001",
  "source_locator": "source/source-render.pdf:page:17:paragraph:2",
  "source_text": "(Garcia, 2020)",
  "sha256": "<sha256 of locator + one NUL byte + exact source_text>"
}
```

The page number must exist in the reviewed render. Preserve the displayed source text exactly. Compute the item hash over UTF-8 bytes as:

```python
hashlib.sha256(f"{source_locator}\0{source_text}".encode("utf-8")).hexdigest()
```

## Extend traceability

For every recovered item, append exactly one row to `manuscript/traceability.csv`:

- use `kind: bibliography` for `src-manual-ref-*` and `kind: citation` for `src-manual-cite-*`;
- copy `source_locator` and the item hash into `source_sha256`;
- map a recovered bibliography record to `references.bib` and its verified BibTeX key;
- map a recovered citation occurrence to a compiled `.tex` file and only the key(s) shown at that occurrence;
- add the usual adjacent `% TRACE:SRC=src-manual-cite-NNN` marker for a recovered citation;
- set `status: verified` only after the mapping is checked.

Rows are part of the final source ZIP and author-approved source hash map. Spreadsheet-formula-leading text must retain the protective leading apostrophe in CSV; the canonical source text remains in the JSON report.

## Bind final approval

After all recovery, mapping, build, and visual review steps are final, compute the SHA-256 of the complete `reports/source-recovery.json` file and put it in `reports/author-decisions.json` as `approved_source_recovery_sha256`. Any later recovery edit invalidates approval and requires renewed author review.
