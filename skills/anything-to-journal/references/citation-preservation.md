# Citation preservation

## Preservation model

Keep three distinct sets across all source materials:

1. citation **occurrences** in text, tables, footnotes, and endnotes;
2. canonical **reference records** reconstructed from Word fields/bibliography metadata;
3. target **BibTeX keys** used by LaTeX.

Map occurrence → source record → BibTeX key in `traceability.csv`. Do not assume a displayed number such as `[12]` is a stable identity.

## Recovery order

Use the highest-confidence evidence available:

1. structured bibliography exports such as BibTeX, RIS, CSL JSON, or EndNote XML;
2. semantic document fields and embedded reference-manager metadata;
3. Word native bibliography sources in `customXml`;
4. complete bibliography paragraphs in the source materials;
5. deterministic plain numeric or author--year matching;
6. external DOI/ISBN/PMID metadata only after title/author/year identity is proven.

Do not let an LLM or search result choose among ambiguous references. Mark the occurrence unresolved.

If automated extraction reports `bibliography-not-detected` or `citations-not-detected`, inspect every page of the hash-matched `source-render.pdf` and use `reports/source-recovery.json`. Every recovered item needs a locator beginning `source/source-render.pdf:page:N`, exact source text, a stable `src-manual-*` ID, and SHA-256 of `locator + NUL + source_text`. Both recovered and confirmed-absent outcomes require evidence, reviewer, timezone-aware time, and the complete page list. Final author approval binds the recovery-report hash.

## Reference-manager fields

- Zotero: parse `ZOTERO_ITEM CSL_CITATION`/CSL JSON; preserve locators, prefixes, suffixes, and suppressed-author modes. Unlinked Zotero citations are plain text and cannot be treated as fully recovered.
- EndNote: parse `EN.CITE`/traveling-library metadata. A local EndNote record number is not a global identifier.
- Legacy Mendeley: parse CSL citation fields. New Mendeley Cite may use Word content controls and web-extension metadata; verify independently.
- Word native: map `CITATION <Tag>` fields to embedded bibliography sources.
- Plain numeric: expand ranges, check every number is within the detected bibliography sequence, and resolve duplicates.
- Plain author--year: match surname, full author list where available, year suffix, and title. Any same-author/same-year ambiguity blocks automatic mapping.
- Note styles: resolve first full citation and subsequent short-title/ibid. chains; request author review when the chain is uncertain.

## Build verified BibTeX

Create ASCII, stable, semantic keys such as `chen2024topic`, adding deterministic suffixes for collisions. Preserve source spellings in the private manifest. For non-English works, preserve the original title, add an accurate English translation where the venue requires one, and use consistent author-name romanization without inventing names.

For each record:

- verify author/editor order, title, year, venue, volume, issue, pages, DOI/URL, publisher, and access date as applicable;
- normalize a DOI only after identity matching;
- never manufacture missing metadata;
- use braces to protect acronyms, proper nouns, and chemical/formula capitalization;
- map all source record IDs that refer to the same canonical work to the same key.

## Audit rules

- Every verified bibliography/citation trace row must name its BibTeX key in `output_id`.
- Every cited key must exist in `references.bib`.
- Every BibTeX entry must be cited in the manuscript or supplement.
- Every BibTeX key has at least one verified source-reference row, and every cited key has at least one verified source-citation occurrence row.
- Two plain source-reference records cannot collapse onto one key unless all affected rows explicitly document `duplicate-source-record`; one source row cannot split into several keys without `split-source-record` evidence.
- Every original citation occurrence must map to the claim it originally supported.
- Do not use `\nocite{*}` to hide unmapped or unused records.
- Keep source bibliography entries that were never cited in the private audit ledger, label them `bibliography-only`, and do not imply that the article cites them.
- Mark a regex false positive `not-a-citation` only with concrete evidence in `notes`.
- Check citation locators and multiple-item order, not just key-set equality.

### Mark each source occurrence in LaTeX

For each verified `citation` or `citation-candidate` row, set `output_file` to the flat name of a compiled `.tex` file and set `output_id` to its comma-separated BibTeX keys without braces or LaTeX commands. Put the source occurrence in exactly one marker comment immediately before the sentence or citation command that preserves it:

```tex
% TRACE:SRC=src-cite-field-014
This association was reported previously \citep[Table~2]{chen2020topic}.
```

The mapped citation command must occur before the next `TRACE:SRC` marker and within 1,200 source characters. Preserve prefixes, suffixes, locators, suppressed-author mode, and key order in the citation command; the marker does not replace those semantics.

When one source occurrence contains multiple works, use one source ID, put all keys in that row's `output_id`, and cite the same keys together:

```text
source_id: src-cite-field-015
output_id: chen2020topic,wang2021method
```

```tex
% TRACE:SRC=src-cite-field-015
Both methods were evaluated \citep{chen2020topic,wang2021method}.
```

When journal synthesis merges multiple source occurrences into one LaTeX citation, put all occurrence IDs on one marker line. Do not stack separate marker lines before the shared citation, because each marker must lead directly to its mapping:

```tex
% TRACE:SRC=src-cite-field-016,src-cite-candidate-009
The findings are consistent \citep{lee2019result,zhou2022result}.
```

Each ledger row still lists only the key or keys present in that particular source occurrence. Rows marked `not-a-citation` or `bibliography-only` do not receive a marker.

If shortened journal prose removes a sentence that carried a citation, either preserve the supported point and citation in a concise related-work statement/supplement or report the source occurrence as unresolved. Never move a citation onto a claim it does not support.
