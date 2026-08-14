# Journal and conference profiles

## Confirm the mode before preparation

Do not inspect source content until the user explicitly chooses a named journal/conference or generic draft-only mode. For a named venue, retain either uploaded official instructions/templates or the current official HTTPS guidance URL. Record the choice, confirmer, time, note, and `confirmation_phase: before-source-access` in `reports/format-decision.json`; `prepare_workspace.py` enforces this for folder intake. The legacy DOCX adapter, `prepare.py`, applies the same decision contract.

## Default: `generic-imrad-num`

Use a publisher-neutral interchange manuscript only after the user explicitly confirms that no target journal or conference is needed:

- standard LaTeX `article`, 12 pt, A4, 25 mm margins;
- single column, 1.5 line spacing, and line numbers;
- numeric citations with `natbib` and `unsrtnat`;
- 200--250-word abstract and 4--7 keywords;
- 6,000--8,000-word main-text target;
- IMRaD for empirical original research;
- separate contribution, funding, ethics, consent, data, conflict, and acknowledgment statements.
- figure captions below figures and table captions above tables;
- 2em first-line paragraph indentation with 0pt paragraph spacing;
- every source figure/table placed next to its callout in the main manuscript;
- no more than 19 complete manuscript-PDF pages, including references.

Call this an **interchange draft** until a target venue is selected. It represents the most common original-research structure, not a universal publisher template.

## Select the body structure

Match structure to the research design before drafting:

- experimental/observational quantitative: IMRaD;
- qualitative: Introduction, Methodology, Findings, Discussion, Reflexivity/limitations;
- engineering/design: Introduction, Related Work, System/Method, Evaluation, Results, Discussion;
- humanities/law: preserve an explicit evidence-driven argumentative structure;
- review/meta-analysis: follow the relevant current reporting guideline and provide search/selection methods;
- case report, trial, diagnostic, epidemiological, or qualitative health research: identify the applicable current EQUATOR guideline.

Never rename conceptual chapters “Methods” and “Results” merely to imitate science formatting.

## Adapt to a named journal or conference

Use only current official journal, conference, society, or publisher instructions. Record the official URL or uploaded guidance hash and verification date in `journal-profile.json`. Check:

1. article type and scope;
2. official LaTeX class/template and TeX Live version;
3. blinded versus title-page submission;
4. word, page, abstract, keyword, figure, table, and reference limits;
5. required section order and reporting guideline;
6. numbered versus author--year references and the official `.bst`/CSL;
7. artwork formats, resolution, color space, and separate-upload rules;
8. table format and supplementary-material policy;
9. data, code, ethics, consent, funding, conflict, CRediT, and AI-use statements;
10. source archive layout and whether `.bbl` is required.

Keep source labels (`src-fig-NNN`, `src-tab-NNN`) while changing visible numbering.

Set the effective page maximum to `min(19, official venue maximum)`. Scope it to the entire manuscript PDF, from title through references. A supplement and cover letter are separate files unless official rules explicitly include them. End-of-manuscript or separate-only figures/tables, a contradictory paragraph style, or an incompatible page maximum conflict with this skill's fixed contract. Before drafting, report the conflict and ask the user to choose a clearly labeled generic draft, another venue, or no conversion. Do not mark a conflicting result `target-verified` or submission-ready.

For a named target, set `format_mode` to `target`, `venue_type` to `journal` or `conference`, fill `target_venue` and `article_type`, set `status` to `target-verified`, retain an official URL or uploaded guidance hash, and record an ISO `verified_on` date no older than 366 days. `overrides` must contain evidence objects, not prose labels:

```json
{
  "requirement": "Use numbered references in citation order",
  "source": "https://official-journal.example/guide#references",
  "implemented_in": "manuscript/journal-preamble.tex and references.bib"
}
```

Record at least one such object for a target profile. A cover letter is required by this workflow for a named journal, but conference-specific transmittal files follow the official instructions.

## Optional publisher adapters

Do not bundle publisher logos, classes, or bibliography styles under this project's license. Use the versions supplied by the current TeX distribution or the publisher.

### Elsevier

For an Elsevier target that accepts `elsarticle`, change the class to `\documentclass[preprint,12pt]{elsarticle}`, move metadata into `frontmatter`, and use the bibliography style named by that journal. Prefer preprint/review layout for initial submission. Do not use `ecrc`, `3p`, `5p`, or camera-ready branding unless the editor explicitly requires it.

Official references:

- [Elsevier LaTeX instructions](https://www.elsevier.com/en-gb/researcher/author/policies-and-guidelines/latex-instructions)
- [CTAN `elsarticle` package and LPPL license](https://ctan.org/pkg/elsarticle)

### Springer Nature, Wiley, IEEE, conferences, and societies

Use the exact official class and sample for the named venue. Publisher-level templates still do not override venue-specific author instructions. Re-run compilation, citation, artwork, and source-package checks after every class change.

Official starting points:

- [Springer Nature LaTeX support](https://www.springernature.com/gp/authors/campaigns/latex-author-support)
- [Wiley LaTeX author resources](https://authors.wiley.com/author-resources/Journal-Authors/Prepare/latex-template.html)
- [IEEE Author Center templates](https://journals.ieeeauthorcenter.ieee.org/create-your-ieee-journal-article/authoring-tools-and-templates/)
- [EQUATOR reporting-guideline index](https://www.equator-network.org/)

## Profile status language

Use these exact meanings:

- `interchange-draft`: generic formatting after an explicit draft-only confirmation;
- `target-verified`: a named journal/conference's current official guide or uploaded instructions and implementation evidence are recorded; other audit items may still remain;
- `ready-for-author-review`: use only in the quality/project report after all automated, author-decision, and visual gates pass, not as a profile status;
- never use `accepted`, `guaranteed`, or `journal-approved`.
