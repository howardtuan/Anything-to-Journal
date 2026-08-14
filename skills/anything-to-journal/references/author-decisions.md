# Author decisions and final approval

`reports/author-decisions.json` is a submission gate, not a suggestion form. Source materials can contain evidence about funding or ethics, but they cannot prove the current author list, current affiliations, venue-specific disclosure duties, permissions, or every author's approval of the final PDF.

## Status values

- `source-verified`: the value appears explicitly in an immutable source material; put the source ID, page, cell, line, or exact manifest locator in `evidence`.
- `author-confirmed`: a human author supplied or confirmed the value; fill `confirmed_by` and a timezone-aware ISO-8601 `confirmed_at` timestamp.
- `not-applicable`: use only when `allow_not_applicable` is true and record the author's rationale in `evidence`.
- `pending`: unresolved and therefore not submission-ready.

Never convert silence into “none,” “not applicable,” or “no conflicts.” Never infer coauthors from acknowledgments, supervisors from a cover page, or contribution roles from author order.

## Required sequence

1. Prepare the project and preserve the initial pending record.
2. Fill source-supported values with `source-verified`; ask the corresponding author only for the remaining decisions.
3. Build and visually inspect the manuscript PDF.
4. Have every listed author review the final English text, figures, tables, citations, declarations, and author order.
5. Set `all_authors_approved` to `author-confirmed`, set its `value` to the JSON boolean `true` (not the string `"yes"`), and record who collected the approvals and when.
6. Copy the final build report's complete `source_sha256` and `output_sha256` maps into top-level `approved_source_sha256` and `approved_pdf_sha256`. This includes manuscript, supplement, cover letter, references, assets, and supporting TeX files when present.
7. If `source-recovery.json` resolved a detector miss, copy its final SHA-256 into top-level `approved_source_recovery_sha256`. The recovery report itself must name its reviewer, review time, all source-render pages, evidence, and recovered page locators.
8. Set the top-level `status` to `verified` and rerun `audit.py --require-pdf`.

A rebuild or manuscript edit changes a hash and invalidates approval. Re-review, update the hashes, and rerun the audit. This skill never performs the external submission.
