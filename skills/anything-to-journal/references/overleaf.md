# Overleaf handoff

## The one-file upload

Give the user `submission/overleaf-upload.zip`. Do not ask them to choose individual LaTeX files from the audit package.

The ZIP must open directly to:

```text
main.tex
references.bib
journal-preamble.tex
README_OVERLEAF.md
<required .tex/.cls/.sty/.bst files>
<referenced figures and data>
```

Do not place these files inside an enclosing top-level folder. Keep `main.tex` at ZIP root. Include only files required to compile or edit the manuscript; do not include private source evidence, reports, compiler junk, or the final PDF.

## User instructions

1. Sign in to Overleaf.
2. Choose **New Project → Upload Project**.
3. Select `overleaf-upload.zip`.
4. Open the uploaded project and confirm `main.tex` is the main document.
5. Use the compiler and any TeX Live requirement stated in `README_OVERLEAF.md`.
6. Select **Recompile**.

The author can then edit:

- `main.tex` for title, abstract, sections, declarations, figure/table placement, and wording;
- `references.bib` for verified reference metadata;
- supporting `.tex` files for sections, figures, or tables;
- image files to replace a verified derivative without changing its referenced filename;
- journal class/style files only when the target venue provides replacements.

After edits, recompile and re-check citations, cross-references, figures, tables, declarations, and page count. An Overleaf edit invalidates the local audit hashes; describe it as an author-edited derivative unless the updated project is downloaded and audited again.

## Current Overleaf constraints

Overleaf documents the project-creation flow at <https://docs.overleaf.com/managing-projects-and-files/uploading-a-project>. Its current guidance says a project ZIP should contain LaTeX-processable text and image files, use the correct compiler and TeX Live version, and keep the main document out of a top-level folder. If Overleaf changes this behavior, follow the current official documentation.
