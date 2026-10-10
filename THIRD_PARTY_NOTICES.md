# Third-party notices

The local PDF Workspace bundles [PDF.js](https://github.com/mozilla/pdf.js) (`pdfjs-dist` 6.3.289, Apache-2.0) in `skills/anything-to-journal/assets/workspace/pdfjs/`, including its worker, CMaps, standard fonts, and image codecs. Its Apache license and the individual font/codec licenses are retained alongside the assets. `VERSION.json` records the upstream package integrity; maintainers can reproduce the bundle with `node scripts/vendor-pdfjs.mjs`. PDF scripting is not enabled. No publisher template or user research content is bundled.

The workflow interoperates with separately installed software, including:

- [Pandoc](https://pandoc.org/) — GPL-2.0-or-later;
- [TeX Live](https://tug.org/texlive/) — collection of packages under their respective licenses;
- [Tectonic](https://tectonic-typesetting.github.io/) — MIT;
- [`elsarticle`](https://ctan.org/pkg/elsarticle), when selected for an Elsevier target — LPPL 1.3;
- Poppler, LibreOffice, Inkscape, and reference-manager data, when the user chooses to use them — their respective licenses.

Installing or using a dependency does not relicense it under this project's MIT License. Publisher trademarks and templates remain the property of their owners. A user's source materials, extracted figures/tables, generated manuscript, and submission files retain the rights and restrictions applicable to that content.
