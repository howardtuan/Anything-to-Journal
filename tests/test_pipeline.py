from __future__ import annotations

import base64
import csv
import hashlib
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "anything-to-journal" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT))

import audit  # noqa: E402
import build  # noqa: E402
import install  # noqa: E402
import preflight  # noqa: E402
import prepare  # noqa: E402
import prepare_workspace  # noqa: E402


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


DOCUMENT_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
 xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
 xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">
 <w:body>
  <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>摘要</w:t></w:r></w:p>
  <w:p><w:r><w:t>研究結果如圖與表所示 [1]。</w:t></w:r></w:p>
  <w:p>
   <w:r><w:drawing><wp:inline><wp:docPr id="1" name="Figure 1" descr="sample"/>
    <a:graphic><a:graphicData><a:blip r:embed="rIdImage1"/></a:graphicData></a:graphic>
   </wp:inline></w:drawing></w:r>
  </w:p>
  <w:p><w:r><w:t>圖 1 測試圖</w:t></w:r></w:p>
  <w:p><w:r><w:t>表 1 測試表</w:t></w:r></w:p>
  <w:tbl>
   <w:tr><w:tc><w:p><w:r><w:t>組別</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>數值</w:t></w:r></w:p></w:tc></w:tr>
   <w:tr><w:tc><w:p><w:r><w:t>一</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>2.5</w:t></w:r></w:p></w:tc></w:tr>
  </w:tbl>
  <w:p>
   <w:r><w:fldChar w:fldCharType="begin"/></w:r>
   <w:r><w:instrText xml:space="preserve"> ADDIN ZOTERO_ITEM CSL_CITATION {&quot;citationItems&quot;:[{&quot;id&quot;:1}]} </w:instrText></w:r>
   <w:r><w:fldChar w:fldCharType="separate"/></w:r>
   <w:r><w:t>(Chen, 2020)</w:t></w:r>
   <w:r><w:fldChar w:fldCharType="end"/></w:r>
  </w:p>
  <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>參考文獻</w:t></w:r></w:p>
  <w:p><w:r><w:t>Chen, A. (2020). A synthetic reference. Test Journal, 1, 1–2.</w:t></w:r></w:p>
  <w:sectPr/>
 </w:body>
</w:document>
"""


RELS_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rIdImage1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/image1.png"/>
</Relationships>
"""


CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Default Extension="xml" ContentType="application/xml"/>
 <Default Extension="png" ContentType="image/png"/>
 <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
"""


def make_docx(path: Path, malicious: bool = False) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("word/document.xml", DOCUMENT_XML)
        archive.writestr("word/_rels/document.xml.rels", RELS_XML)
        archive.writestr("word/media/image1.png", PNG_1X1)
        if malicious:
            archive.writestr("../escape.txt", "unsafe")


def make_custom_docx(
    path: Path,
    document_xml: str,
    relationships_xml: str | None = None,
    parts: dict[str, bytes] | None = None,
) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("word/document.xml", document_xml)
        if relationships_xml is not None:
            archive.writestr("word/_rels/document.xml.rels", relationships_xml)
        for name, payload in (parts or {}).items():
            archive.writestr(name, payload)


class PipelineTests(unittest.TestCase):
    def test_prepare_workspace_rejects_blank_target_before_material_access(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "fresh-materials"
            output = source / "journal-output"
            with mock.patch.object(prepare_workspace, "scan_workspace") as scan, mock.patch.object(
                prepare_workspace, "copy_material"
            ) as copy:
                self.assertEqual(
                    prepare_workspace.main(
                        [
                            str(source), "--output", str(output),
                            "--target-venue", "  \t ", "--venue-type", "journal",
                            "--official-guide-url", "https://example.test/authors",
                            "--confirmed-by", "Synthetic User",
                            "--confirmation-note", "Whitespace target must be rejected.",
                        ]
                    ),
                    2,
                )
            scan.assert_not_called()
            copy.assert_not_called()
            self.assertFalse(source.exists())

    def test_prepare_workspace_inventories_arbitrary_materials(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp) / "fresh-materials"
            nested = workspace / "nested"
            nested.mkdir(parents=True)
            (workspace / "notes.txt").write_text("research notes\n", encoding="utf-8")
            (nested / "results.csv").write_bytes(b"group,value\nA,2.5\n")
            (nested / "plot.bin").write_bytes(b"\x00\x01\xffsynthetic")
            output = workspace / "journal-output"

            self.assertEqual(
                prepare_workspace.main(
                    [
                        str(workspace), "--output", str(output), "--draft-only",
                        "--confirmed-by", "Synthetic User",
                        "--confirmation-note", "Generic journal draft requested.",
                    ]
                ),
                0,
            )

            manifest = json.loads(
                (output / "source" / "source-manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["schema_version"], "1.0")
            self.assertEqual(manifest["source"]["kind"], "workspace")
            materials = manifest["materials"]
            self.assertEqual(
                [item["source_id"] for item in materials],
                ["src-material-0001", "src-material-0002", "src-material-0003"],
            )
            self.assertEqual(
                [item["original_relative_path"] for item in materials],
                ["nested/plot.bin", "nested/results.csv", "notes.txt"],
            )
            for item in materials:
                copied = output / item["stored_path"]
                original = workspace / item["original_relative_path"]
                self.assertEqual(copied.read_bytes(), original.read_bytes())
                self.assertEqual(item["bytes"], len(original.read_bytes()))
                self.assertEqual(item["sha256"], hashlib.sha256(original.read_bytes()).hexdigest())
                self.assertEqual(item["copied_path"], item["stored_path"])
                self.assertIn("extension", item)
                self.assertIn("role_hint", item)
            aggregate = prepare_workspace.workspace_aggregate(materials)
            self.assertEqual(aggregate, audit.workspace_aggregate(materials))
            self.assertEqual(manifest["source"]["sha256"], aggregate)
            self.assertEqual(manifest["source"]["aggregate_sha256"], aggregate)
            for key in prepare_workspace.EMPTY_MANIFEST_LISTS:
                self.assertEqual(manifest[key], [])

            review = json.loads(
                (output / "reports" / "source-review.json").read_text(encoding="utf-8")
            )
            self.assertEqual(review["status"], "pending")
            self.assertEqual(
                review["source_ids_reviewed"], [item["source_id"] for item in materials]
            )
            decision = json.loads(
                (output / "reports" / "format-decision.json").read_text(encoding="utf-8")
            )
            tooling = json.loads((output / "source" / "tooling.json").read_text(encoding="utf-8"))
            self.assertEqual(decision["confirmation_phase"], "before-source-access")
            self.assertLessEqual(decision["confirmed_at"], tooling["prepared_at"])
            with (output / "manuscript" / "traceability.csv").open(
                "r", encoding="utf-8-sig", newline=""
            ) as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual([row["kind"] for row in rows], ["material"] * 3)
            self.assertTrue(all(row["status"] == "pending" for row in rows))
            project = json.loads((output / "project.json").read_text(encoding="utf-8"))
            self.assertEqual(project["source_review"], "reports/source-review.json")
            self.assertTrue((output / "source" / "inventory.md").is_file())

    def test_prepare_workspace_rejects_symlinked_material_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "fresh-materials"
            workspace.mkdir()
            outside = root / "outside.txt"
            outside.write_text("secret", encoding="utf-8")
            (workspace / "unsafe.txt").symlink_to(outside)
            output = workspace / "journal-output"
            self.assertEqual(
                prepare_workspace.main(
                    [
                        str(workspace), "--output", str(output), "--draft-only",
                        "--confirmed-by", "Synthetic User",
                        "--confirmation-note", "Generic journal draft requested.",
                    ]
                ),
                2,
            )
            self.assertFalse(output.exists())
            self.assertEqual(list(workspace.iterdir()), [workspace / "unsafe.txt"])

    def test_workspace_provenance_review_and_material_structure_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp) / "fresh-materials"
            workspace.mkdir()
            (workspace / "figure.png").write_bytes(PNG_1X1)
            project = workspace / "journal-output"
            self.assertEqual(
                prepare_workspace.main(
                    [
                        str(workspace), "--output", str(project), "--draft-only",
                        "--confirmed-by", "Synthetic User",
                        "--confirmation-note", "Generic journal draft requested.",
                    ]
                ),
                0,
            )
            manifest = json.loads(
                (project / "source" / "source-manifest.json").read_text(encoding="utf-8")
            )
            clean = audit.Audit(project, require_pdf=True, strict=False)
            clean.audit_manifest_provenance(manifest)
            self.assertEqual(clean.findings, [])

            fabricated = json.loads(json.dumps(manifest))
            fabricated["materials"] = []
            fabricated["counts"]["materials"] = 0
            fabricated["source"]["material_count"] = 0
            empty_hash = audit.workspace_aggregate([])
            fabricated["source"]["sha256"] = empty_hash
            fabricated["source"]["aggregate_sha256"] = empty_hash
            empty_runner = audit.Audit(project, require_pdf=True, strict=False)
            empty_runner.validate_manifest(fabricated)
            self.assertIn(
                "empty-workspace-manifest",
                {finding["code"] for finding in empty_runner.findings},
            )

            review_path = project / "reports" / "source-review.json"
            review = json.loads(review_path.read_text(encoding="utf-8"))
            review.update(
                {
                    "status": "verified",
                    "reviewed_by": "Synthetic Reviewer",
                    "reviewed_at": "2026-08-14T00:00:00+08:00",
                }
            )
            review_path.write_text(json.dumps(review), encoding="utf-8")
            reviewed = audit.Audit(project, require_pdf=True, strict=False)
            reviewed.audit_workspace_source_review(manifest)
            self.assertEqual(reviewed.findings, [])

            manuscript = project / "manuscript" / "manuscript.tex"
            manuscript.write_text(
                r"\documentclass{article}\begin{document}"
                r"\begin{figure}\caption{Mapped}\label{fig:mapped}\end{figure}"
                r"\end{document}",
                encoding="utf-8",
            )
            item = manifest["materials"][0]
            rows = [
                {
                    "kind": "material", "source_id": item["source_id"],
                    "source_locator": item["stored_path"], "source_sha256": item["sha256"],
                    "source_summary": "figure.png", "output_id": "fig:mapped",
                    "output_file": "manuscript.tex", "output_asset": "",
                    "operation": "source-figure", "status": "verified", "notes": "",
                }
            ]
            mapped = audit.Audit(project, require_pdf=False, strict=False)
            mapped.audit_ledger(manifest, rows, compiled_files=[manuscript])
            mapped.audit_reverse_structures(manuscript.read_text(encoding="utf-8"), rows)
            self.assertNotIn(
                "unmapped-typeset-structure", {finding["code"] for finding in mapped.findings}
            )
            self.assertNotIn("invalid-trace-target", {finding["code"] for finding in mapped.findings})

            copied = project / item["stored_path"]
            copied.write_bytes(b"tampered")
            tampered = audit.Audit(project, require_pdf=True, strict=False)
            tampered.audit_manifest_provenance(manifest)
            codes = {finding["code"] for finding in tampered.findings}
            self.assertIn("workspace-material-hash-mismatch", codes)
            self.assertIn("workspace-aggregate-mismatch", codes)

    def test_active_word_field_blocks_preflight(self) -> None:
        document_xml = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
 <w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r>
 <w:r><w:instrText>DDEAUTO c:\\unsafe\\command.exe</w:instrText></w:r>
 <w:r><w:fldChar w:fldCharType="end"/></w:r></w:p><w:sectPr/>
</w:body></w:document>"""
        with tempfile.TemporaryDirectory() as temp:
            docx = Path(temp) / "active-field.docx"
            make_custom_docx(docx, document_xml)
            manifest = preflight.inventory_docx(docx)
            self.assertEqual(manifest["active_word_fields"][0]["command"], "DDEAUTO")
            self.assertIn("active-word-field", {item["code"] for item in manifest["warnings"]})

    def test_revisions_in_notes_are_blocked(self) -> None:
        document_xml = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
 <w:p><w:r><w:t>Body</w:t></w:r></w:p><w:sectPr/>
</w:body></w:document>"""
        footnotes = b"""<?xml version="1.0" encoding="UTF-8"?>
<w:footnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
 <w:footnote w:id="1"><w:p><w:ins><w:r><w:t>new</w:t></w:r></w:ins>
 <w:del><w:r><w:delText>old</w:delText></w:r></w:del></w:p></w:footnote>
</w:footnotes>"""
        with tempfile.TemporaryDirectory() as temp:
            docx = Path(temp) / "revisions.docx"
            make_custom_docx(docx, document_xml, parts={"word/footnotes.xml": footnotes})
            manifest = preflight.inventory_docx(docx)
            self.assertEqual(
                {(item["story"], item["element"]) for item in manifest["revision_markup"]},
                {("footnote", "del"), ("footnote", "delText"), ("footnote", "ins")},
            )
            self.assertGreaterEqual(manifest["counts"]["revision_markers"], 3)

    def test_image_effect_requires_rendered_derivative(self) -> None:
        document_xml = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
 xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
 xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"><w:body>
 <w:p><w:r><w:drawing><wp:inline><wp:docPr id="1" name="effect"/>
 <a:graphic><a:graphicData><a:blip r:embed="rIdImage1"><a:grayscl/><a:lum bright="20000"/></a:blip>
 </a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p><w:sectPr/>
</w:body></w:document>"""
        with tempfile.TemporaryDirectory() as temp:
            docx = Path(temp) / "effect.docx"
            make_custom_docx(docx, document_xml, RELS_XML, {"word/media/image1.png": PNG_1X1})
            figure = preflight.inventory_docx(docx)["figures"][0]
            self.assertFalse(figure["exact_embedded_asset"])
            self.assertEqual({item["tag"] for item in figure["effects"]}, {"grayscl", "lum"})

    def test_alternate_content_counts_one_displayed_figure(self) -> None:
        document_xml = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
 xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
 xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
 xmlns:v="urn:schemas-microsoft-com:vml" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"><w:body>
 <w:p><mc:AlternateContent><mc:Choice Requires="wpg"><w:r><w:drawing><wp:inline>
 <wp:docPr id="1" name="choice"/><a:graphic><a:graphicData><a:blip r:embed="rIdImage1"/>
 </a:graphicData></a:graphic></wp:inline></w:drawing></w:r></mc:Choice>
 <mc:Fallback><w:r><w:pict><v:shape><v:imagedata r:id="rIdImage1"/></v:shape></w:pict></w:r></mc:Fallback>
 </mc:AlternateContent></w:p><w:sectPr/>
</w:body></w:document>"""
        with tempfile.TemporaryDirectory() as temp:
            docx = Path(temp) / "alternate.docx"
            make_custom_docx(docx, document_xml, RELS_XML, {"word/media/image1.png": PNG_1X1})
            figures = preflight.inventory_docx(docx)["figures"]
            self.assertEqual(len(figures), 1)
            self.assertEqual(len(figures[0]["alternate_content"]), 2)

    def test_altchunk_payload_is_extracted_and_blocks_conversion(self) -> None:
        document_xml = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><w:body>
 <w:altChunk r:id="rIdChunk"/><w:sectPr/>
</w:body></w:document>"""
        relationships = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rIdChunk" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/aFChunk" Target="afchunk.mht"/>
</Relationships>"""
        payload = b"HIDDEN RESEARCH 42"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            docx = root / "altchunk.docx"
            extracted = root / "extracted"
            make_custom_docx(docx, document_xml, relationships, {"word/afchunk.mht": payload})
            manifest = preflight.inventory_docx(docx, media_dir=extracted)
            item = next(item for item in manifest["objects"] if item["kind"] == "altchunk")
            self.assertEqual((extracted / item["extracted_file"]).read_bytes(), payload)
            self.assertIn("altchunk-content-present", {warning["code"] for warning in manifest["warnings"]})

    def test_legacy_eq_field_is_inventoried_and_prepared(self) -> None:
        document_xml = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
 <w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r>
 <w:r><w:instrText xml:space="preserve"> EQ \\f(1,2) </w:instrText></w:r>
 <w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>1/2</w:t></w:r>
 <w:r><w:fldChar w:fldCharType="end"/></w:r></w:p><w:sectPr/>
</w:body></w:document>"""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            docx = root / "legacy-equation.docx"
            output = root / "output"
            make_custom_docx(docx, document_xml)
            manifest = preflight.inventory_docx(docx)
            self.assertEqual(manifest["counts"]["equations"], 1)
            self.assertEqual(manifest["equations"][0]["kind"], "legacy-eq-field")
            self.assertEqual(manifest["equations"][0]["text"], "1/2")
            with mock.patch.object(prepare, "run_pandoc", return_value=({}, [])):
                self.assertEqual(
                    prepare.main(
                        [
                            str(docx), "--output", str(output), "--draft-only",
                            "--confirmed-by", "Synthetic User",
                            "--confirmation-note", "Journal-neutral draft requested.",
                        ]
                    ),
                    0,
                )
            evidence = output / "source" / "equations" / "src-eq-001.field.txt"
            self.assertIn("EQ", evidence.read_text(encoding="utf-8"))

    def test_ole_visual_and_embedded_payload_have_separate_records(self) -> None:
        document_xml = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
 xmlns:o="urn:schemas-microsoft-com:office:office"><w:body>
 <w:p><w:r><w:object><o:OLEObject r:id="rIdOle1"/></w:object></w:r></w:p><w:sectPr/>
</w:body></w:document>"""
        relationships_xml = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rIdOle1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/oleObject" Target="embeddings/data.xlsx"/>
</Relationships>"""
        payload = b"synthetic-xlsx-package"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            docx = root / "embedded-data.docx"
            extracted = root / "extracted"
            make_custom_docx(
                docx,
                document_xml,
                relationships_xml,
                {"word/embeddings/data.xlsx": payload},
            )
            manifest = preflight.inventory_docx(docx, media_dir=extracted)
            self.assertEqual([item["kind"] for item in manifest["objects"]], ["ole", "embedded-package"])
            package = manifest["objects"][1]
            self.assertEqual(package["referenced_by"], ["src-object-001"])
            self.assertEqual((extracted / package["extracted_file"]).read_bytes(), payload)
            self.assertEqual(package["sha256"], hashlib.sha256(payload).hexdigest())

    def test_csv_starter_neutralizes_spreadsheet_formulas(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "table.csv"
            preflight.write_table_csv(path, [["=1+1", "+SUM(A1:A2)", "safe"]])
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                self.assertEqual(next(csv.reader(handle)), ["'=1+1", "'+SUM(A1:A2)", "safe"])

    def test_multparagraph_table_cell_preserves_numeric_boundaries(self) -> None:
        cell = ET.fromstring(
            '<w:tc xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:p><w:r><w:t>1</w:t></w:r></w:p><w:p><w:r><w:t>2</w:t></w:r></w:p></w:tc>'
        )
        table = ET.fromstring(
            '<w:tbl xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:tr></w:tr></w:tbl>'
        )
        table.find("./w:tr", preflight.NS).append(cell)
        matrix = preflight.table_matrix(table)
        self.assertEqual(matrix, [["1 2"]])
        self.assertEqual(preflight.numeric_tokens(matrix), ["1", "2"])

    def test_superscript_scanner_skips_field_result_but_keeps_independent_run(self) -> None:
        paragraph = ET.fromstring(
            '<w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
            '<w:r><w:instrText>ADDIN ZOTERO_ITEM CSL_CITATION {"citationItems":[]}</w:instrText></w:r>'
            '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
            '<w:r><w:rPr><w:vertAlign w:val="superscript"/></w:rPr><w:t>1</w:t></w:r>'
            '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
            '<w:r><w:rPr><w:vertAlign w:val="superscript"/></w:rPr><w:t>2</w:t></w:r>'
            '</w:p>'
        )
        self.assertEqual(len(preflight.citation_fields([paragraph])), 1)
        candidates = preflight.superscript_citation_candidates([paragraph], "document")
        self.assertEqual([item["text"] for item in candidates], ["2"])

    def test_plain_references_heading_stops_at_next_heading(self) -> None:
        paragraphs = [
            {"text": "References", "heading_level": None, "index": 1},
            {"text": "Chen (2020). Reference.", "heading_level": None, "index": 2},
            {"text": "Appendix A", "heading_level": 1, "index": 3},
            {"text": "Not a reference", "heading_level": None, "index": 4},
        ]
        entries, _ = preflight.detect_bibliography(paragraphs)
        self.assertEqual([item["text"] for item in entries], ["Chen (2020). Reference."])

    def test_table_parser_isolates_labels_and_supports_longtable(self) -> None:
        tex = r"""
\begin{table}\caption{2.5 in caption}\label{src-tab-001}\begin{tabular}{c}999\end{tabular}\end{table}
\begin{longtable}{c}\caption{Other}\label{src-tab-002}2.5\\\end{longtable}
"""
        first = audit.environment_containing_label(tex, "src-tab-001")
        second = audit.environment_containing_label(tex, "src-tab-002")
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual([m.group(0) for m in audit.NUMBER_RE.finditer(audit.table_data_text(first or ""))], ["999"])
        self.assertEqual([m.group(0) for m in audit.NUMBER_RE.finditer(audit.table_data_text(second or ""))], ["2.5"])
        wrapped = (
            r"\begin{table}\label{src-tab-003}\begin{tabular}{p{2cm}}"
            r"\sourcecell{src-tab-003-r001-c001}{\makecell{A\\B and \textbf{2}}}"
            r"\end{tabular}\end{table}"
        )
        self.assertEqual(
            audit.sourcecell_instances(wrapped),
            [("src-tab-003-r001-c001", r"\makecell{A\\B and \textbf{2}}")],
        )

    def test_generic_template_enforces_journal_float_and_paragraph_layout(self) -> None:
        template = ROOT / "skills" / "anything-to-journal" / "assets" / "generic-template"
        preamble = (template / "journal-preamble.tex").read_text(encoding="utf-8")
        compact_preamble = "".join(preamble.split())
        self.assertIn(r"\setlength{\parindent}{2em}", compact_preamble)

        profile = json.loads((template / "journal-profile.json").read_text(encoding="utf-8"))
        self.assertEqual(profile["figure_caption_position"], "below")
        self.assertEqual(profile["table_caption_position"], "above")
        self.assertEqual(profile["paragraph_first_line_indent"], "2em")
        self.assertEqual(profile["float_placement"], "in-text")
        self.assertEqual(profile["max_manuscript_pages"], 19)

        figure_tex = prepare.figure_tex(
            {
                "source_id": "src-fig-001",
                "sha256": hashlib.sha256(PNG_1X1).hexdigest(),
                "package_path": "word/media/image1.png",
                "caption": "Source caption",
                "exact_embedded_asset": True,
                "extracted_file": "src-fig-001.png",
            }
        )
        self.assertIn(r"\begin{figure}[!htbp]", figure_tex)
        self.assertLess(figure_tex.index(r"\includegraphics"), figure_tex.index(r"\caption{"))

        table_tex = prepare.table_tex(
            {
                "source_id": "src-tab-001",
                "sha256": "table-hash",
                "numeric_tokens": ["2.5"],
                "cells": [["Group", "Value"], ["A", "2.5"]],
                "columns": 2,
            }
        )
        self.assertIn(r"\begin{table}[!htbp]", table_tex)
        self.assertLess(table_tex.index(r"\caption{"), table_tex.index(r"\begin{tabularx}"))

    def test_prepare_requires_an_explicit_target_or_draft_only_choice(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            docx = root / "thesis.docx"
            output = root / "output"
            make_docx(docx)
            with self.assertRaises(SystemExit) as raised:
                prepare.main(
                    [
                        str(docx), "--output", str(output),
                        "--confirmed-by", "Synthetic User",
                        "--confirmation-note", "No format choice supplied.",
                    ]
                )
            self.assertEqual(raised.exception.code, 2)
            self.assertFalse(output.exists())

            target_output = root / "target-output"
            target_args = [
                str(docx), "--output", str(target_output),
                "--target-venue", "Synthetic Conference",
                "--venue-type", "conference",
                "--confirmed-by", "Synthetic User",
                "--confirmation-note", "Target format requested.",
            ]
            try:
                target_result = prepare.main(target_args)
            except SystemExit as exc:
                target_result = exc.code
            self.assertEqual(target_result, 2)
            self.assertFalse(target_output.exists())

    def test_prepare_rejects_blank_target_before_source_access(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "must-not-be-read.docx"
            output = root / "must-not-be-created" / "project"
            with mock.patch.object(
                prepare.Path,
                "resolve",
                side_effect=AssertionError("source/output path was resolved before target validation"),
            ) as resolve_path, mock.patch.object(prepare, "inventory_docx") as inventory:
                self.assertEqual(
                    prepare.main(
                        [
                            str(source), "--output", str(output),
                            "--target-venue", "   \t  ",
                            "--venue-type", "journal",
                            "--official-guide-url", "https://example.test/authors",
                            "--confirmed-by", "Synthetic User",
                            "--confirmation-note", "Whitespace target must be rejected.",
                        ]
                    ),
                    2,
                )
            resolve_path.assert_not_called()
            inventory.assert_not_called()
            self.assertFalse(output.parent.exists())

    def test_prepare_records_uploaded_target_format_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            docx = root / "thesis.docx"
            output = root / "target-output"
            guidance = root / "official-template.zip"
            make_docx(docx)
            guidance.write_bytes(b"synthetic official conference template")
            with mock.patch.object(prepare, "run_pandoc", return_value=({}, [])):
                self.assertEqual(
                    prepare.main(
                        [
                            str(docx), "--output", str(output),
                            "--target-venue", "Synthetic Conference",
                            "--venue-type", "conference",
                            "--guidance-file", str(guidance),
                            "--confirmed-by", "Synthetic User",
                            "--confirmation-note", "Use the uploaded official format.",
                        ]
                    ),
                    0,
                )
            profile = json.loads(
                (output / "manuscript" / "journal-profile.json").read_text(encoding="utf-8")
            )
            self.assertEqual(profile["format_mode"], "target")
            self.assertEqual(profile["venue_type"], "conference")
            self.assertEqual(profile["target_venue"], "Synthetic Conference")
            record = json.loads(
                (output / "reports" / "format-decision.json").read_text(encoding="utf-8")
            )
            self.assertEqual(record["status"], "confirmed")
            self.assertEqual(record["confirmation_phase"], "before-source-access")
            uploaded = next(item for item in record["format_guidance"] if item["kind"] == "uploaded-file")
            self.assertEqual(uploaded["path"], "source/format-guidance/official-template.zip")
            self.assertEqual(uploaded["sha256"], hashlib.sha256(guidance.read_bytes()).hexdigest())
            self.assertEqual(
                (output / uploaded["path"]).read_bytes(), guidance.read_bytes()
            )

    def test_audit_rejects_unconfirmed_format_decision(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            manuscript = project / "manuscript"
            reports = project / "reports"
            manuscript.mkdir()
            reports.mkdir()
            profile = {
                "profile": "generic-imrad-num",
                "status": "interchange-draft",
                "article_type": "research-article",
                "target_journal": None,
                "format_mode": None,
                "overrides": [],
            }
            (manuscript / "journal-profile.json").write_text(
                json.dumps(profile), encoding="utf-8"
            )
            runner = audit.Audit(project, require_pdf=True, strict=False)
            runner.audit_format_decision({"source": {"sha256": "a" * 64}})
            self.assertIn(
                "unconfirmed-format-decision",
                {item["code"] for item in runner.findings},
            )

    def test_audit_binds_format_confirmation_to_preparation_time(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            manuscript = project / "manuscript"
            reports = project / "reports"
            source = project / "source"
            manuscript.mkdir()
            reports.mkdir()
            source.mkdir()
            source_sha256 = "a" * 64
            profile = {
                "profile": "generic-imrad-num",
                "format_mode": "draft-only",
                "venue_type": None,
                "target_venue": None,
                "target_journal": None,
                "target_conference": None,
                "official_guide_url": None,
                "format_guidance": [],
                "max_manuscript_pages": 19,
            }
            (manuscript / "journal-profile.json").write_text(
                json.dumps(profile), encoding="utf-8"
            )
            (source / "tooling.json").write_text(
                json.dumps({"prepared_at": "2020-01-02T00:00:00Z"}), encoding="utf-8"
            )
            base_decision = {
                "schema_version": "1.0",
                "status": "confirmed",
                "source_sha256": source_sha256,
                "format_mode": "draft-only",
                "venue_type": None,
                "target_venue": None,
                "official_guide_url": None,
                "format_guidance": [],
                "confirmed_by": "Synthetic User",
                "confirmation_note": "A publisher-neutral draft was requested.",
            }
            cases = (
                (
                    "wrong phase",
                    {"confirmation_phase": "after-source-access", "confirmed_at": "2020-01-01T00:00:00Z"},
                    "unconfirmed-format-decision",
                ),
                (
                    "confirmation after preparation",
                    {"confirmation_phase": "before-source-access", "confirmed_at": "2020-01-03T00:00:00Z"},
                    "invalid-format-chronology",
                ),
                (
                    "implausible future confirmation",
                    {"confirmation_phase": "before-source-access", "confirmed_at": "2999-01-01T00:00:00Z"},
                    "invalid-format-chronology",
                ),
                (
                    "valid pre-access confirmation",
                    {"confirmation_phase": "before-source-access", "confirmed_at": "2020-01-02T00:00:00Z"},
                    None,
                ),
            )
            for label, changes, expected_code in cases:
                with self.subTest(case=label):
                    decision = dict(base_decision)
                    decision.update(changes)
                    (reports / "format-decision.json").write_text(
                        json.dumps(decision), encoding="utf-8"
                    )
                    runner = audit.Audit(project, require_pdf=True, strict=False)
                    runner.audit_format_decision({"source": {"sha256": source_sha256}})
                    codes = {item["code"] for item in runner.findings}
                    if expected_code:
                        self.assertIn(expected_code, codes, runner.findings)
                    else:
                        self.assertNotIn("unconfirmed-format-decision", codes, runner.findings)
                        self.assertNotIn("invalid-format-chronology", codes, runner.findings)

    def test_audit_requires_regular_valid_tooling_timestamp_record(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            manuscript = project / "manuscript"
            reports = project / "reports"
            source = project / "source"
            manuscript.mkdir()
            reports.mkdir()
            source.mkdir()
            source_sha256 = "a" * 64
            (manuscript / "journal-profile.json").write_text(
                json.dumps(
                    {
                        "profile": "generic-imrad-num",
                        "format_mode": "draft-only",
                        "venue_type": None,
                        "target_venue": None,
                        "target_journal": None,
                        "target_conference": None,
                        "official_guide_url": None,
                        "format_guidance": [],
                        "max_manuscript_pages": 19,
                    }
                ),
                encoding="utf-8",
            )
            (reports / "format-decision.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "status": "confirmed",
                        "source_sha256": source_sha256,
                        "format_mode": "draft-only",
                        "venue_type": None,
                        "target_venue": None,
                        "official_guide_url": None,
                        "format_guidance": [],
                        "confirmed_by": "Synthetic User",
                        "confirmed_at": "2020-01-01T00:00:00Z",
                        "confirmation_phase": "before-source-access",
                        "confirmation_note": "A publisher-neutral draft was requested.",
                    }
                ),
                encoding="utf-8",
            )
            tooling = source / "tooling.json"
            outside = project / "outside-tooling.json"
            outside.write_text(
                json.dumps({"prepared_at": "2020-01-02T00:00:00Z"}), encoding="utf-8"
            )
            cases = (
                ("missing", None),
                ("symlink", None),
                ("malformed", "invalid-json"),
            )
            for state, expected_code in cases:
                with self.subTest(tooling=state):
                    tooling.unlink(missing_ok=True)
                    if state == "symlink":
                        tooling.symlink_to(outside)
                    elif state == "malformed":
                        tooling.write_text("{", encoding="utf-8")
                    runner = audit.Audit(project, require_pdf=True, strict=False)
                    runner.audit_format_decision({"source": {"sha256": source_sha256}})
                    codes = {item["code"] for item in runner.findings}
                    if expected_code:
                        self.assertIn(expected_code, codes, runner.findings)
                    self.assertIn("invalid-format-chronology", codes, runner.findings)

            tooling.unlink(missing_ok=True)
            tooling.write_text(
                json.dumps({"prepared_at": "2020-01-02T00:00:00Z"}), encoding="utf-8"
            )
            valid_runner = audit.Audit(project, require_pdf=True, strict=False)
            valid_runner.audit_format_decision({"source": {"sha256": source_sha256}})
            valid_codes = {item["code"] for item in valid_runner.findings}
            self.assertFalse(
                {
                    "missing-file", "unsafe-symlink", "invalid-json",
                    "invalid-format-chronology",
                }
                & valid_codes,
                valid_runner.findings,
            )

    def test_audit_flags_caption_indent_and_end_dump_regressions(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            manuscript = project / "manuscript"
            manuscript.mkdir()
            (manuscript / "journal-preamble.tex").write_text(
                r"\setlength{\parindent}{0pt}", encoding="utf-8"
            )
            (manuscript / "source-elements.tex").write_text(
                r"\begin{figure}[p]\includegraphics{late.png}\caption{Late}\end{figure}",
                encoding="utf-8",
            )
            (manuscript / "manuscript.tex").write_text(
                r"""\documentclass{article}
\input{journal-preamble.tex}
\begin{document}
\begin{abstract}Evidence.</abstract>
\section{Introduction} Evidence.
\begin{figure}[p]\caption{Wrong side}\includegraphics{figure.png}\label{src-fig-001}\end{figure}
\begin{table}[p]\begin{tabular}{c}\sourcecell{src-tab-001-r001-c001}{1}\end{tabular}\caption{Wrong side}\label{src-tab-001}\end{table}
\section{Discussion} Evidence.
\section{Conclusions} Evidence.
\InputIfFileExists{source-elements.tex}{\input{source-elements.tex}}{}
\end{document}
""",
                encoding="utf-8",
            )
            runner = audit.Audit(project, require_pdf=False, strict=False)
            runner.audit_layout_contract(
                {
                    "figures": [{"source_id": "src-fig-001"}],
                    "tables": [{"source_id": "src-tab-001"}],
                },
                [
                    {
                        "kind": "figure", "source_id": "src-fig-001",
                        "status": "verified", "output_file": "manuscript.tex",
                    },
                    {
                        "kind": "table", "source_id": "src-tab-001",
                        "status": "verified", "output_file": "manuscript.tex",
                    },
                ],
                runner.compiled_tex_files(),
            )
            codes = {item["code"] for item in runner.findings}
            self.assertTrue(
                {
                    "figure-caption-position",
                    "table-caption-position",
                    "paragraph-indent-policy",
                    "end-matter-float-dump",
                    "float-not-in-text",
                }.issubset(codes),
                runner.findings,
            )

    def _layout_codes(
        self,
        body: str,
        manifest: dict[str, object],
        rows: list[dict[str, str]],
        preamble_suffix: str = "",
        included_files: dict[str, str] | None = None,
    ) -> tuple[set[str], list[dict[str, str]]]:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            manuscript = project / "manuscript"
            manuscript.mkdir()
            (manuscript / "journal-preamble.tex").write_text(
                r"\usepackage{indentfirst}"
                r"\setlength{\parindent}{2em}"
                r"\setlength{\parskip}{0pt}"
                + preamble_suffix,
                encoding="utf-8",
            )
            (manuscript / "manuscript.tex").write_text(
                "\\documentclass{article}\n"
                "\\input{journal-preamble.tex}\n"
                "\\begin{document}\n"
                f"{body}\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            for name, content in (included_files or {}).items():
                (manuscript / name).write_text(content, encoding="utf-8")
            runner = audit.Audit(project, require_pdf=False, strict=False)
            runner.audit_layout_contract(manifest, rows, runner.compiled_tex_files())
            return {item["code"] for item in runner.findings}, list(runner.findings)

    def test_layout_rejects_verified_float_after_terminal_boundary(self) -> None:
        figure = (
            r"\begin{figure}[!htbp]"
            r"\includegraphics{figure.png}"
            r"\caption{Evidence}\label{src-fig-001}"
            r"\end{figure}"
        )
        manifest = {"figures": [{"source_id": "src-fig-001"}], "tables": []}
        rows = [
            {
                "kind": "figure", "source_id": "src-fig-001",
                "status": "verified", "output_file": "manuscript.tex",
            }
        ]
        boundaries = {
            "bibliography command": r"\bibliography{references}",
            "thebibliography environment": (
                r"\begin{thebibliography}{9}"
                r"\bibitem{source} Synthetic reference."
                r"\end{thebibliography}"
            ),
            "terminal clearpage": r"\clearpage",
        }
        for label, boundary in boundaries.items():
            with self.subTest(boundary=label):
                codes, findings = self._layout_codes(
                    r"\section{Results}See Figure~\ref{src-fig-001}." + boundary + figure,
                    manifest,
                    rows,
                )
                self.assertTrue(
                    {"float-not-in-text", "end-matter-float-dump"} & codes,
                    findings,
                )

    def test_table_caption_after_tabular_begin_is_rejected(self) -> None:
        body = (
            r"\section{Results}See Table~\ref{src-tab-001}."
            r"\begin{table}[!htbp]"
            r"\begin{tabular}{c}"
            r"\caption{Too late}\label{src-tab-001}"
            r"\sourcecell{src-tab-001-r001-c001}{1}\\"
            r"\end{tabular}\end{table}"
        )
        codes, findings = self._layout_codes(
            body,
            {"figures": [], "tables": [{"source_id": "src-tab-001"}]},
            [
                {
                    "kind": "table", "source_id": "src-tab-001",
                    "status": "verified", "output_file": "manuscript.tex",
                }
            ],
        )
        self.assertIn("table-caption-position", codes, findings)

    def test_paragraph_indent_cannot_be_overridden_indirectly(self) -> None:
        overrides = {
            "direct assignment without equals": r"\parindent0pt",
            "relative subtraction": r"\addtolength{\parindent}{-2em}",
        }
        for label, override in overrides.items():
            with self.subTest(override=label):
                codes, findings = self._layout_codes(
                    r"\section{Introduction}Evidence.",
                    {"figures": [], "tables": []},
                    [],
                    preamble_suffix=override,
                )
                self.assertIn("paragraph-indent-policy", codes, findings)

        codes, findings = self._layout_codes(
            r"\input{section-body.tex}",
            {"figures": [], "tables": []},
            [],
            included_files={
                "section-body.tex": r"\section{Introduction}\noindent Evidence."
            },
        )
        self.assertIn("paragraph-indent-override", codes, findings)

    def test_installer_links_canonical_skill_without_duplicate_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "skills"
            self.assertEqual(install.main(["--destination", str(destination)]), 0)
            target = destination / "anything-to-journal"
            self.assertTrue(target.is_symlink())
            self.assertEqual(target.resolve(), (ROOT / "skills" / "anything-to-journal").resolve())
            self.assertEqual(install.main(["--destination", str(destination)]), 0)

    def test_installer_copy_rejects_source_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source-skill"
            source.mkdir()
            (source / "SKILL.md").write_text(
                "---\nname: anything-to-journal\ndescription: test\n---\n", encoding="utf-8"
            )
            secret = root / "secret.txt"
            secret.write_text("do not copy", encoding="utf-8")
            (source / "leak.txt").symlink_to(secret)
            with mock.patch.object(install, "SOURCE_SKILL", source):
                self.assertEqual(
                    install.main(["--destination", str(root / "installed"), "--mode", "copy"]), 2
                )
            self.assertFalse((root / "installed" / "anything-to-journal" / "leak.txt").exists())

    def test_preflight_inventories_source_elements(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            docx = root / "thesis.docx"
            make_docx(docx)
            manifest = preflight.inventory_docx(docx, root / "media", root / "tables")
            self.assertEqual(manifest["counts"]["figures"], 1)
            self.assertEqual(manifest["counts"]["tables"], 1)
            self.assertEqual(manifest["counts"]["bibliography_entries"], 1)
            self.assertEqual(manifest["counts"]["citation_fields"], 1)
            self.assertEqual(manifest["citation_fields"][0]["manager"], "zotero")
            self.assertEqual(
                [item["text"] for item in manifest["citation_candidates"]],
                ["[1]"],
            )
            self.assertEqual(manifest["tables"][0]["numeric_tokens"], ["2.5"])
            self.assertEqual((root / "media" / "src-fig-001.png").read_bytes(), PNG_1X1)
            self.assertTrue((root / "tables" / "src-tab-001.csv").is_file())

    def test_preflight_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            docx = Path(temp) / "unsafe.docx"
            make_docx(docx, malicious=True)
            with self.assertRaisesRegex(ValueError, "Unsafe package path"):
                preflight.inventory_docx(docx)

    def test_preflight_rejects_duplicate_package_parts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            docx = Path(temp) / "duplicate.docx"
            with zipfile.ZipFile(docx, "w") as archive:
                archive.writestr("[Content_Types].xml", CONTENT_TYPES)
                archive.writestr("word/document.xml", DOCUMENT_XML)
                archive.writestr("word/document.xml", DOCUMENT_XML)
            with self.assertRaisesRegex(ValueError, "Duplicate package part"):
                preflight.inventory_docx(docx)

    def test_prepare_creates_auditable_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            docx = root / "thesis.docx"
            output = root / "journal-output"
            make_docx(docx)
            self.assertEqual(
                prepare.main(
                    [
                        str(docx), "--output", str(output), "--draft-only",
                        "--confirmed-by", "Synthetic User",
                        "--confirmation-note", "Journal-neutral draft requested.",
                    ]
                ),
                0,
            )
            manifest = json.loads((output / "source" / "source-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["source"]["sha256"], hashlib.sha256(docx.read_bytes()).hexdigest())
            source_elements = (output / "manuscript" / "source-elements.tex").read_text(encoding="utf-8")
            self.assertIn(r"% \input{src-fig-001.tex}", source_elements)
            self.assertIn(r"% \input{src-tab-001.tex}", source_elements)
            figure_starter = (output / "manuscript" / "src-fig-001.tex").read_text(encoding="utf-8")
            table_starter = (output / "manuscript" / "src-tab-001.tex").read_text(encoding="utf-8")
            self.assertIn(r"\label{src-fig-001}", figure_starter)
            self.assertLess(figure_starter.index(r"\includegraphics"), figure_starter.index(r"\caption{"))
            self.assertLess(table_starter.index(r"\caption{"), table_starter.index(r"\begin{tabularx}"))
            manuscript_text = (output / "manuscript" / "manuscript.tex").read_text(encoding="utf-8")
            compiled_manuscript_text = audit.strip_tex_comments(manuscript_text)
            self.assertNotIn("source-elements.tex", compiled_manuscript_text)
            self.assertNotIn(r"\input{src-tab-001.tex}", compiled_manuscript_text)
            profile = json.loads(
                (output / "manuscript" / "journal-profile.json").read_text(encoding="utf-8")
            )
            self.assertEqual(profile["format_mode"], "draft-only")
            format_decision = json.loads(
                (output / "reports" / "format-decision.json").read_text(encoding="utf-8")
            )
            self.assertEqual(format_decision["status"], "confirmed")
            self.assertEqual(format_decision["format_mode"], "draft-only")
            self.assertEqual(format_decision["confirmation_phase"], "before-source-access")
            self.assertEqual(format_decision["confirmed_by"], "Synthetic User")
            self.assertTrue(format_decision["confirmed_at"])
            self.assertEqual(
                format_decision["confirmation_note"], "Journal-neutral draft requested."
            )
            self.assertIsNone(format_decision["venue_type"])
            self.assertIsNone(format_decision["target_venue"])
            self.assertIsNone(format_decision["official_guide_url"])
            self.assertEqual(format_decision["format_guidance"], [])
            with (output / "manuscript" / "traceability.csv").open("r", encoding="utf-8-sig") as handle:
                rows = list(csv.DictReader(handle))
            self.assertGreaterEqual(len(rows), 4)
            self.assertTrue(all(row["status"] == "pending" for row in rows))
            self.assertTrue((output / "manuscript" / "evidence-map.csv").is_file())
            self.assertTrue((output / "reports" / "author-decisions.json").is_file())
            self.assertTrue((output / "reports" / "source-render-review.json").is_file())
            self.assertTrue((output / "reports" / "source-recovery.json").is_file())

    def test_empty_manifest_cannot_bypass_submission_gates(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            for name in ("source", "manuscript", "reports", "submission"):
                (project / name).mkdir()
            (project / "source" / "source-manifest.json").write_text("{}", encoding="utf-8")
            (project / "manuscript" / "traceability.csv").write_text(
                ",".join(prepare.TRACE_FIELDS) + "\n", encoding="utf-8"
            )
            (project / "manuscript" / "manuscript.tex").write_text(
                r"\documentclass{article}\begin{document}\begin{abstract}Evidence.\end{abstract}"
                r"\section{Introduction}Evidence.\section{Discussion}Evidence."
                r"\section{Conclusions}Evidence.\end{document}", encoding="utf-8"
            )
            (project / "manuscript" / "references.bib").write_text("", encoding="utf-8")
            result, _ = audit.Audit(project, require_pdf=True, strict=False).run()
            self.assertFalse(result["submission_ready"])
            self.assertIn("invalid-manifest-schema", {item["code"] for item in result["findings"]})

    def test_well_shaped_empty_manifest_fails_docx_reinventory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            (project / "source").mkdir()
            source = project / "source" / "original.docx"
            make_docx(source)
            genuine = preflight.inventory_docx(source)
            fabricated = dict(genuine)
            for key in (
                "paragraphs", "figures", "tables", "objects", "equations", "footnotes",
                "endnotes", "comments", "bibliography_entries", "word_bibliography_sources",
                "citation_fields", "citation_candidates", "package_parts", "warnings",
            ):
                fabricated[key] = []
            runner = audit.Audit(project, require_pdf=True, strict=False)
            runner.audit_manifest_provenance(fabricated)
            self.assertIn(
                "manifest-reinventory-mismatch",
                {item["code"] for item in runner.findings},
            )

    def test_target_promotes_and_clean_draft_packages_without_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            for name in ("source", "manuscript", "reports", "submission"):
                (project / name).mkdir()
            figure_bytes = b"synthetic-figure"
            figure_hash = hashlib.sha256(figure_bytes).hexdigest()
            (project / "manuscript" / "src-fig-001.png").write_bytes(figure_bytes)
            manifest = {
                "schema_version": "1.0",
                "source": {"sha256": "a" * 64},
                "warnings": [], "paragraphs": [],
                "figures": [{
                    "source_id": "src-fig-001", "extracted_file": "src-fig-001.png",
                    "sha256": figure_hash, "exact_embedded_asset": True
                }],
                "tables": [{
                    "source_id": "src-tab-001", "numeric_tokens": ["2.5"], "sha256": "tablehash"
                }],
                "objects": [], "equations": [], "footnotes": [], "endnotes": [], "comments": [],
                "bibliography_entries": [{"source_id": "src-ref-001"}],
                "word_bibliography_sources": [],
                "citation_fields": [{"source_id": "src-cite-field-001"}],
                "citation_candidates": [],
                "active_word_fields": [],
                "revision_markup": [],
            }
            (project / "source" / "source-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            (project / "source" / "original.docx").write_bytes(b"synthetic-original-docx")
            (project / "source" / "tooling.json").write_text(
                json.dumps(
                    {
                        "prepared_at": "2020-01-02T00:00:00Z",
                        "python": "synthetic-test",
                        "pandoc": {},
                        "template_profile": "generic-imrad-num",
                    }
                ),
                encoding="utf-8",
            )
            abstract = " ".join(["evidence"] * 160)
            tex = rf"""\documentclass{{article}}
\input{{journal-preamble.tex}}
\begin{{document}}
\begin{{abstract}}{abstract}\end{{abstract}}
\section{{Introduction}} Evidence.
See Figure~\ref{{src-fig-001}} and Table~\ref{{src-tab-001}}.
% TRACE:SRC=src-cite-field-001
Evidence \citep{{chen2020}}.
\begin{{figure}}[!htbp]\includegraphics{{src-fig-001.png}}\caption{{Synthetic}}\label{{src-fig-001}}\end{{figure}}
\begin{{table}}[!htbp]\caption{{Values}}\label{{src-tab-001}}\sourcecell{{cell}}{{2.5}}\end{{table}}
\section{{Discussion}} Evidence.
\section{{Conclusions}} Evidence.
\bibliography{{references}}
\end{{document}}
"""
            (project / "manuscript" / "manuscript.tex").write_text(tex, encoding="utf-8")
            (project / "manuscript" / "journal-preamble.tex").write_text(
                r"\usepackage{indentfirst}\setlength{\parindent}{2em}\setlength{\parskip}{0pt}",
                encoding="utf-8",
            )
            (project / "manuscript" / "references.bib").write_text(
                "@article{chen2020, author={Chen, A.}, title={A Reference}, year={2020}}\n",
                encoding="utf-8",
            )
            guide_url = "https://example.test/conference/authors"
            profile = {
                "profile": "generic-imrad-num", "status": "target-verified",
                "article_type": "research-article", "target_journal": None,
                "target_conference": "Synthetic Conference",
                "target_venue": "Synthetic Conference", "venue_type": "conference",
                "format_mode": "target",
                "official_guide_url": guide_url,
                "format_guidance": [{"kind": "official-url", "url": guide_url}],
                "verified_on": audit.date.today().isoformat(),
                "overrides": [
                    {
                        "requirement": "Use the verified conference manuscript layout.",
                        "source": guide_url,
                        "implemented_in": "manuscript.tex",
                    }
                ],
                "figure_caption_position": "below", "table_caption_position": "above",
                "paragraph_first_line_indent": "2em", "paragraph_spacing": "0pt",
                "float_placement": "in-text",
                "max_manuscript_pages": 19,
                "page_limit_scope": "entire-manuscript-pdf-including-references",
            }
            (project / "manuscript" / "journal-profile.json").write_text(
                json.dumps(profile), encoding="utf-8"
            )
            format_decision = {
                "schema_version": "1.0", "status": "confirmed",
                "format_mode": "target", "source_sha256": "a" * 64,
                "venue_type": "conference", "target_venue": "Synthetic Conference",
                "official_guide_url": guide_url,
                "format_guidance": [{"kind": "official-url", "url": guide_url}],
                "confirmed_by": "Synthetic Author",
                "confirmed_at": "2020-01-01T00:00:00Z",
                "confirmation_phase": "before-source-access",
                "confirmation_note": "Use the named conference format.",
            }
            (project / "reports" / "format-decision.json").write_text(
                json.dumps(format_decision), encoding="utf-8"
            )
            fields = prepare.TRACE_FIELDS
            rows = [
                ["figure", "src-fig-001", "", figure_hash, "", "src-fig-001", "manuscript.tex", "src-fig-001.png", "exact_copy", "verified", ""],
                ["table", "src-tab-001", "", "tablehash", "", "src-tab-001", "manuscript.tex", "", "translated", "verified", ""],
                ["bibliography", "src-ref-001", "", "", "", "chen2020", "references.bib", "", "metadata_reconstruction", "verified", ""],
                ["citation", "src-cite-field-001", "", "", "", "chen2020", "manuscript.tex", "", "field_reconstruction", "verified", ""],
            ]
            with (project / "manuscript" / "traceability.csv").open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(fields)
                writer.writerows(rows)
            prepare.write_evidence_map(project / "manuscript" / "evidence-map.csv")
            decisions = prepare.initial_author_decisions(manifest)
            decisions["status"] = "verified"
            for item in decisions["decisions"]:
                item.update({
                    "value": "Confirmed for test", "status": "author-confirmed",
                    "confirmed_by": "Synthetic Author", "confirmed_at": "2026-08-02T00:00:00Z",
                })
                if item["id"] == "all_authors_approved":
                    item["value"] = True
                if item.get("required_before_drafting") is True:
                    item["value"] = "draft-only"
            source_render = project / "source" / "source-render.pdf"
            source_render.write_bytes(b"%PDF-source-render")
            source_render_hash = hashlib.sha256(source_render.read_bytes()).hexdigest()
            (project / "reports" / "source-render-review.json").write_text(
                json.dumps({
                    "schema_version": "1.0", "status": "verified", "source_sha256": "a" * 64,
                    "rendered_from_sha256": "a" * 64, "renderer": "synthetic-test-renderer",
                    "render_file": "source/source-render.pdf", "render_sha256": source_render_hash,
                    "page_count": 1, "pages_inspected": [1], "reviewed_by": "Synthetic Reviewer",
                    "reviewed_at": "2026-08-02T00:00:00Z",
                }), encoding="utf-8"
            )
            pdf = project / "submission" / "manuscript.pdf"
            pdf.write_bytes(b"%PDF-synthetic")
            pdf_hash = hashlib.sha256(pdf.read_bytes()).hexdigest()
            source_names = [
                "manuscript.tex", "journal-preamble.tex", "references.bib", "src-fig-001.png",
                "traceability.csv", "evidence-map.csv", "journal-profile.json",
            ]
            source_hashes = {
                name: hashlib.sha256((project / "manuscript" / name).read_bytes()).hexdigest()
                for name in source_names
            }
            pdf_hashes = {"submission/manuscript.pdf": pdf_hash}
            decisions["approved_source_sha256"] = source_hashes
            decisions["approved_pdf_sha256"] = pdf_hashes
            (project / "reports" / "author-decisions.json").write_text(
                json.dumps(decisions), encoding="utf-8"
            )
            with zipfile.ZipFile(project / "submission" / "submission-sources.zip", "w") as archive:
                for name in source_names:
                    archive.write(project / "manuscript" / name, arcname=name)
            overleaf_names, overleaf_hashes = build.build_overleaf_bundle(
                project / "submission" / "overleaf-upload",
                project / "submission" / "overleaf-upload.zip",
                build.overleaf_source_files(
                    [project / "manuscript" / name for name in source_names]
                ),
            )
            (project / "reports" / "build-report.json").write_text(
                json.dumps({
                    "schema_version": "1.0", "built_at": "2026-08-02T00:00:00Z",
                    "success": True, "compiler": "tectonic",
                    "documents": [{
                        "tex": "manuscript.tex", "pdf": "manuscript.pdf", "success": True,
                        "page_count": 1,
                        "commands": [{
                            "argv": ["/usr/bin/tectonic", "--keep-intermediates", "--keep-logs",
                                     "--synctex", "--untrusted", "manuscript.tex"],
                            "returncode": 0, "timed_out": False, "stdout": "", "stderr": "",
                        }],
                    }],
                    "source_archive_files": source_names,
                    "source_sha256": source_hashes,
                    "output_sha256": pdf_hashes,
                    "overleaf_upload": {
                        "directory": "submission/overleaf-upload",
                        "archive": "submission/overleaf-upload.zip",
                        "main_document": "main.tex",
                        "files": overleaf_names,
                        "sha256": overleaf_hashes,
                        "archive_sha256": hashlib.sha256(
                            (project / "submission" / "overleaf-upload.zip").read_bytes()
                        ).hexdigest(),
                    },
                    "manuscript_page_limit": {
                        "maximum": 19, "actual": 1,
                        "scope": "entire-manuscript-pdf-including-references",
                        "passed": True,
                    },
                }),
                encoding="utf-8",
            )
            visual = {
                "status": "verified", "reviewed_by": "Synthetic Reviewer",
                "reviewed_at": "2026-08-02T00:00:00Z",
                "files": [{
                    "file": "submission/manuscript.pdf", "sha256": pdf_hash,
                    "page_count": 1, "pages_inspected": [1], "status": "verified"
                }],
            }
            (project / "reports" / "visual-inspection.json").write_text(json.dumps(visual), encoding="utf-8")
            runner = audit.Audit(project, require_pdf=True, strict=False)
            runner.pdf_page_count = lambda _path: 1
            runner.audit_manifest_provenance = lambda _manifest: None
            result, _ = runner.run()
            self.assertTrue(result["submission_ready"], result["findings"])

            draft_runner = audit.Audit(project, require_pdf=False, strict=False)
            draft_runner.audit_manifest_provenance = lambda _manifest: None
            draft_runner.pdf_page_count = lambda _path: 1
            draft_result, _ = draft_runner.run()
            self.assertFalse(draft_result["submission_ready"])

            with mock.patch.object(audit.Audit, "pdf_page_count", lambda _self, _path: 1), mock.patch.object(
                audit.Audit, "audit_manifest_provenance", lambda _self, _manifest: None
            ):
                self.assertEqual(audit.main([str(project), "--require-pdf"]), 0)
            package = project / "submission" / "submission-package.zip"
            self.assertTrue(package.is_file())
            with zipfile.ZipFile(package) as archive:
                names = set(archive.namelist())
                required = {
                    "project.json",
                    "source/source-manifest.json",
                    "source/original.docx",
                    "source/source-render.pdf",
                    "source/tooling.json",
                    "manuscript/manuscript.tex",
                    "manuscript/references.bib",
                    "reports/author-decisions.json",
                    "reports/build-report.json",
                    "reports/format-decision.json",
                    "reports/quality-report.json",
                    "reports/visual-inspection.json",
                    "submission/manuscript.pdf",
                    "submission/submission-sources.zip",
                    "submission/submission.pdf",
                }
                self.assertTrue(required.issubset(names), sorted(required - names))
                self.assertNotIn("submission/submission-package.zip", names)
                self.assertTrue(all(not name.startswith(("/", "../")) for name in names))

            profile.update(
                {
                    "status": "interchange-draft",
                    "format_mode": "draft-only",
                    "venue_type": None,
                    "target_venue": None,
                    "target_journal": None,
                    "target_conference": None,
                    "official_guide_url": None,
                    "format_guidance": [],
                    "overrides": [],
                }
            )
            (project / "manuscript" / "journal-profile.json").write_text(
                json.dumps(profile), encoding="utf-8"
            )
            format_decision.update(
                {
                    "format_mode": "draft-only",
                    "venue_type": None,
                    "target_venue": None,
                    "official_guide_url": None,
                    "format_guidance": [],
                    "confirmation_note": "A publisher-neutral draft was explicitly requested.",
                }
            )
            (project / "reports" / "format-decision.json").write_text(
                json.dumps(format_decision), encoding="utf-8"
            )
            source_hashes = {
                name: hashlib.sha256((project / "manuscript" / name).read_bytes()).hexdigest()
                for name in source_names
            }
            decisions["approved_source_sha256"] = source_hashes
            (project / "reports" / "author-decisions.json").write_text(
                json.dumps(decisions), encoding="utf-8"
            )
            with zipfile.ZipFile(
                project / "submission" / "submission-sources.zip", "w"
            ) as archive:
                for name in source_names:
                    archive.write(project / "manuscript" / name, arcname=name)
            build_report = json.loads(
                (project / "reports" / "build-report.json").read_text(encoding="utf-8")
            )
            build_report["source_sha256"] = source_hashes
            build_report["source_archive_files"] = source_names
            (project / "reports" / "build-report.json").write_text(
                json.dumps(build_report), encoding="utf-8"
            )

            clean_draft_runner = audit.Audit(project, require_pdf=True, strict=False)
            clean_draft_runner.pdf_page_count = lambda _path: 1
            clean_draft_runner.audit_manifest_provenance = lambda _manifest: None
            clean_draft_result, _ = clean_draft_runner.run()
            self.assertTrue(clean_draft_result["draft_checks_passed"], clean_draft_result["findings"])
            self.assertFalse(clean_draft_result["submission_ready"])

            with mock.patch.object(
                audit.Audit, "pdf_page_count", lambda _self, _path: 1
            ), mock.patch.object(
                audit.Audit, "audit_manifest_provenance", lambda _self, _manifest: None
            ):
                self.assertEqual(audit.main([str(project), "--require-pdf"]), 0)
            self.assertTrue((project / "submission" / "DRAFT_NOT_FOR_SUBMISSION.pdf").is_file())
            self.assertFalse((project / "submission" / "submission.pdf").exists())
            draft_package = project / "submission" / "submission-package.zip"
            self.assertTrue(draft_package.is_file())
            with zipfile.ZipFile(draft_package) as archive:
                draft_names = set(archive.namelist())
            self.assertIn("submission/DRAFT_NOT_FOR_SUBMISSION.pdf", draft_names)
            self.assertNotIn("submission/submission.pdf", draft_names)
            quality = json.loads(
                (project / "reports" / "quality-report.json").read_text(encoding="utf-8")
            )
            self.assertTrue(quality["draft_checks_passed"])
            self.assertFalse(quality["submission_ready"])

    def test_build_packages_sources_and_invalidates_visual_review(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "project"
            manuscript = project / "manuscript"
            manuscript.mkdir(parents=True)
            (project / "source").mkdir()
            (project / "reports").mkdir()
            (project / "submission").mkdir()
            (manuscript / "manuscript.tex").write_text(
                r"\documentclass{article}\begin{document}Test\end{document}", encoding="utf-8"
            )
            (manuscript / "references.bib").write_text("", encoding="utf-8")
            (manuscript / "analysis.dat").write_bytes(b"supporting-data")
            for private_name in (
                "traceability.csv", "evidence-map.csv", "journal-profile.json",
                "submission-checklist.md", "source-elements.tex",
            ):
                (manuscript / private_name).write_text("private audit scaffold", encoding="utf-8")
            bin_dir = Path(temp) / "bin"
            bin_dir.mkdir()
            fake = bin_dir / "tectonic"
            fake.write_text(
                f"#!{sys.executable}\n"
                "import pathlib, sys\n"
                "if '--version' in sys.argv:\n"
                "    print('tectonic 0.0-test')\n"
                "else:\n"
                "    tex = pathlib.Path(sys.argv[-1])\n"
                "    tex.with_suffix('.pdf').write_bytes(b'%PDF-synthetic')\n",
                encoding="utf-8",
            )
            fake.chmod(0o755)
            with mock.patch.dict(os.environ, {"PATH": str(bin_dir)}, clear=False), mock.patch.object(
                build, "page_count", return_value=1
            ):
                self.assertEqual(build.main([str(project), "--compiler", "tectonic"]), 0)
            self.assertTrue((project / "submission" / "manuscript.pdf").is_file())
            self.assertTrue((project / "submission" / "submission-sources.zip").is_file())
            self.assertTrue((project / "submission" / "overleaf-upload").is_dir())
            self.assertTrue((project / "submission" / "overleaf-upload.zip").is_file())
            self.assertFalse((project / "submission" / "submission-package.zip").exists())
            with zipfile.ZipFile(project / "submission" / "submission-sources.zip") as archive:
                self.assertEqual(archive.read("analysis.dat"), b"supporting-data")
                self.assertIn("traceability.csv", archive.namelist())
            overleaf_dir = project / "submission" / "overleaf-upload"
            self.assertEqual(
                (overleaf_dir / "main.tex").read_bytes(),
                (manuscript / "manuscript.tex").read_bytes(),
            )
            self.assertTrue((overleaf_dir / "README_OVERLEAF.md").is_file())
            self.assertEqual((overleaf_dir / "analysis.dat").read_bytes(), b"supporting-data")
            self.assertFalse((overleaf_dir / "manuscript.tex").exists())
            with zipfile.ZipFile(project / "submission" / "overleaf-upload.zip") as archive:
                overleaf_names = archive.namelist()
                self.assertIn("main.tex", overleaf_names)
                self.assertIn("README_OVERLEAF.md", overleaf_names)
                self.assertIn("analysis.dat", overleaf_names)
                self.assertNotIn("manuscript.tex", overleaf_names)
                self.assertNotIn("traceability.csv", overleaf_names)
                self.assertNotIn("evidence-map.csv", overleaf_names)
                self.assertNotIn("journal-profile.json", overleaf_names)
                self.assertNotIn("submission-checklist.md", overleaf_names)
                self.assertNotIn("source-elements.tex", overleaf_names)
                self.assertTrue(all("/" not in name for name in overleaf_names))
                for name in overleaf_names:
                    self.assertEqual(archive.read(name), (overleaf_dir / name).read_bytes())
            build_report = json.loads(
                (project / "reports" / "build-report.json").read_text(encoding="utf-8")
            )
            overleaf_record = build_report["overleaf_upload"]
            self.assertEqual(overleaf_record["main_document"], "main.tex")
            self.assertEqual(set(overleaf_record["files"]), set(overleaf_names))
            self.assertEqual(
                overleaf_record["archive_sha256"],
                hashlib.sha256((project / "submission" / "overleaf-upload.zip").read_bytes()).hexdigest(),
            )
            for name, checksum in overleaf_record["sha256"].items():
                self.assertEqual(checksum, hashlib.sha256((overleaf_dir / name).read_bytes()).hexdigest())
            bundle_audit = audit.Audit(project, require_pdf=False, strict=False)
            bundle_audit.audit_overleaf_bundle(build_report)
            self.assertEqual(bundle_audit.findings, [])
            (overleaf_dir / "main.tex").write_text("tampered", encoding="utf-8")
            with zipfile.ZipFile(
                project / "submission" / "overleaf-upload.zip", "a", zipfile.ZIP_DEFLATED
            ) as archive:
                archive.writestr("wrapper/main.tex", "tampered")
            tampered_bundle = audit.Audit(project, require_pdf=False, strict=False)
            tampered_bundle.audit_overleaf_bundle(build_report)
            tamper_codes = {finding["code"] for finding in tampered_bundle.findings}
            self.assertIn("overleaf-file-hash-mismatch", tamper_codes)
            self.assertIn("overleaf-archive-hash-mismatch", tamper_codes)
            self.assertIn("nonflat-overleaf-archive", tamper_codes)
            visual = json.loads((project / "reports" / "visual-inspection.json").read_text(encoding="utf-8"))
            self.assertEqual(visual["status"], "pending")
            self.assertEqual(visual["files"][0]["pages_inspected"], [])

    def test_build_removes_stale_submission_package_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "project"
            for name in ("source", "manuscript", "reports", "submission"):
                (project / name).mkdir(parents=True, exist_ok=True)
            stale = project / "reports" / "submission-package-manifest.json"
            stale.write_text('{"stale": true}\n', encoding="utf-8")
            stale_overleaf = project / "submission" / "overleaf-upload"
            stale_overleaf.mkdir()
            (stale_overleaf / "old.tex").write_text("stale", encoding="utf-8")
            (project / "submission" / "overleaf-upload.zip").write_bytes(b"stale")
            with mock.patch.object(build, "select_compiler", return_value=None):
                self.assertEqual(build.main([str(project)]), 2)
            self.assertFalse(stale.exists())
            self.assertFalse(stale_overleaf.exists())
            self.assertFalse((project / "submission" / "overleaf-upload.zip").exists())

    def test_twenty_page_build_is_retained_as_draft_but_fails_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "project"
            manuscript = project / "manuscript"
            manuscript.mkdir(parents=True)
            (project / "source").mkdir()
            (project / "reports").mkdir()
            (project / "submission").mkdir()
            (project / "submission" / "submission-package.zip").write_bytes(b"stale-package")
            (manuscript / "manuscript.tex").write_text(
                r"\documentclass{article}\begin{document}Test\end{document}",
                encoding="utf-8",
            )
            (manuscript / "references.bib").write_text("", encoding="utf-8")
            bin_dir = Path(temp) / "bin"
            bin_dir.mkdir()
            fake = bin_dir / "tectonic"
            fake.write_text(
                f"#!{sys.executable}\n"
                "import pathlib, sys\n"
                "if '--version' in sys.argv:\n"
                "    print('tectonic 0.0-test')\n"
                "else:\n"
                "    pathlib.Path(sys.argv[-1]).with_suffix('.pdf').write_bytes(b'%PDF-synthetic')\n",
                encoding="utf-8",
            )
            fake.chmod(0o755)
            with mock.patch.dict(os.environ, {"PATH": str(bin_dir)}, clear=False), mock.patch.object(
                build, "page_count", return_value=20
            ):
                self.assertEqual(build.main([str(project), "--compiler", "tectonic"]), 1)

            self.assertTrue((project / "submission" / "manuscript.pdf").is_file())
            self.assertTrue((project / "submission" / "submission-sources.zip").is_file())
            self.assertFalse((project / "submission" / "submission-package.zip").exists())
            report = json.loads((project / "reports" / "build-report.json").read_text(encoding="utf-8"))
            self.assertFalse(report["success"])
            self.assertEqual(
                report["manuscript_page_limit"],
                {
                    "maximum": 19,
                    "actual": 20,
                    "scope": "entire-manuscript-pdf-including-references",
                    "passed": False,
                },
            )

    def test_audit_blocks_twenty_page_manuscript(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            submission = project / "submission"
            reports = project / "reports"
            submission.mkdir()
            reports.mkdir()
            pdf = submission / "manuscript.pdf"
            pdf.write_bytes(b"%PDF-synthetic")
            pdf_hash = hashlib.sha256(pdf.read_bytes()).hexdigest()
            (reports / "visual-inspection.json").write_text(
                json.dumps(
                    {
                        "status": "verified",
                        "reviewed_by": "Synthetic Reviewer",
                        "reviewed_at": "2026-08-09T00:00:00Z",
                        "files": [
                            {
                                "file": "submission/manuscript.pdf",
                                "sha256": pdf_hash,
                                "page_count": 20,
                                "pages_inspected": list(range(1, 21)),
                                "status": "verified",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            runner = audit.Audit(project, require_pdf=True, strict=False)
            runner.expected_pdf_hashes = {"submission/manuscript.pdf": pdf_hash}
            runner.pdf_page_count = lambda _path: 20
            self.assertEqual(runner.audit_pdf_and_visual_review(), pdf)
            self.assertIn(
                "manuscript-page-limit",
                {item["code"] for item in runner.findings},
            )
            self.assertFalse(runner.result()["submission_ready"])

    def test_visual_review_page_count_must_match_parsed_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            submission = project / "submission"
            reports = project / "reports"
            submission.mkdir()
            reports.mkdir()
            pdf = submission / "manuscript.pdf"
            pdf.write_bytes(b"%PDF-synthetic")
            pdf_hash = hashlib.sha256(pdf.read_bytes()).hexdigest()
            review = {
                "status": "verified",
                "reviewed_by": "Synthetic Reviewer",
                "reviewed_at": "2026-08-09T00:00:00Z",
                "files": [
                    {
                        "file": "submission/manuscript.pdf",
                        "sha256": pdf_hash,
                        "page_count": 1,
                        "pages_inspected": list(range(1, 20)),
                        "status": "verified",
                    }
                ],
            }
            review_path = reports / "visual-inspection.json"
            review_path.write_text(json.dumps(review), encoding="utf-8")

            mismatch = audit.Audit(project, require_pdf=True, strict=False)
            mismatch.expected_pdf_hashes = {"submission/manuscript.pdf": pdf_hash}
            mismatch.pdf_page_count = lambda _path: 19
            self.assertEqual(mismatch.audit_pdf_and_visual_review(), pdf)
            self.assertIn(
                "visual-review-page-count",
                {item["code"] for item in mismatch.findings},
            )

            review["files"][0]["page_count"] = 19
            review_path.write_text(json.dumps(review), encoding="utf-8")
            matching = audit.Audit(project, require_pdf=True, strict=False)
            matching.expected_pdf_hashes = {"submission/manuscript.pdf": pdf_hash}
            matching.pdf_page_count = lambda _path: 19
            self.assertEqual(matching.audit_pdf_and_visual_review(), pdf)
            self.assertNotIn(
                "visual-review-page-count",
                {item["code"] for item in matching.findings},
            )

    def test_compile_cannot_reuse_stale_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tex = root / "manuscript.tex"
            tex.write_text(r"\documentclass{article}\begin{document}New\end{document}", encoding="utf-8")
            tex.with_suffix(".pdf").write_bytes(b"%PDF-old")
            noop = root / "noop-tectonic"
            noop.write_text(f"#!{sys.executable}\nimport sys\nsys.exit(0)\n", encoding="utf-8")
            noop.chmod(0o755)
            result = build.compile_tex("tectonic", str(noop), tex)
            self.assertFalse(result["success"])
            self.assertFalse(tex.with_suffix(".pdf").exists())

    def test_source_packaging_rejects_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            manuscript = Path(temp) / "manuscript"
            manuscript.mkdir()
            outside = Path(temp) / "outside.png"
            outside.write_bytes(b"secret")
            (manuscript / "asset.png").symlink_to(outside)
            with self.assertRaisesRegex(ValueError, "symlink"):
                build.source_files(manuscript)

    def test_source_packaging_rejects_nonportable_filename(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            manuscript = Path(temp) / "manuscript"
            manuscript.mkdir()
            (manuscript / "bad:name.dat").write_bytes(b"data")
            with self.assertRaisesRegex(ValueError, "unsafe cross-platform"):
                build.source_files(manuscript)

    def test_reverse_coverage_rejects_invented_reference_and_table(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            manuscript = project / "manuscript"
            manuscript.mkdir()
            (manuscript / "references.bib").write_text(
                "@article{sourceA,title={A},year={2020}}\n"
                "@article{inventedB,title={B},year={2021}}\n",
                encoding="utf-8",
            )
            tex = (
                r"\citep{sourceA,inventedB}"
                r"\begin{table}\label{src-tab-001}1\end{table}"
                r"\begin{table}\label{tab:invented}999\end{table}"
                r"\begin{figure}\label{fig:invented}\end{figure}"
                r"\begin{equation}\label{eq:invented}x=1\end{equation}"
                r"\[y=2\]"
            )
            (manuscript / "manuscript.tex").write_text(tex, encoding="utf-8")
            rows = [
                {"kind": "bibliography", "source_id": "src-ref-001", "status": "verified", "output_id": "sourceA", "output_file": "references.bib", "notes": ""},
                {"kind": "citation", "source_id": "src-cite-field-001", "status": "verified", "output_id": "sourceA", "output_file": "manuscript.tex", "notes": ""},
                {"kind": "table", "source_id": "src-tab-001", "status": "verified", "output_id": "src-tab-001", "output_file": "manuscript.tex", "notes": ""},
            ]
            runner = audit.Audit(project, require_pdf=False, strict=False)
            runner.audit_citations(tex, rows, [manuscript / "manuscript.tex"])
            runner.audit_reverse_structures(tex, rows)
            codes = {item["code"] for item in runner.findings}
            self.assertIn("unmapped-bibliography-key", codes)
            self.assertIn("unmapped-cited-key", codes)
            self.assertIn("unmapped-typeset-structure", codes)
            self.assertGreaterEqual(
                sum(item["code"] == "unmapped-typeset-structure" for item in runner.findings),
                3,
            )
            self.assertIn("untraced-display-math", codes)

    def test_author_gate_rejects_false_approval_and_stale_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            reports = project / "reports"
            reports.mkdir()
            manifest = {
                "source": {"sha256": "a" * 64},
                "warnings": [{"code": "bibliography-not-detected"}],
            }
            recovery = reports / "source-recovery.json"
            recovery.write_text("{}", encoding="utf-8")
            (reports / "build-report.json").write_text(
                json.dumps({"source_sha256": {}, "output_sha256": {}}), encoding="utf-8"
            )
            decisions = prepare.initial_author_decisions(manifest)
            decisions["status"] = "verified"
            decisions["approved_source_sha256"] = {}
            decisions["approved_pdf_sha256"] = {}
            decisions["approved_source_recovery_sha256"] = "0" * 64
            for item in decisions["decisions"]:
                item.update(
                    {
                        "value": "Confirmed",
                        "status": "author-confirmed",
                        "confirmed_by": "Author",
                        "confirmed_at": "2026-08-02T00:00:00Z",
                    }
                )
                if item["id"] == "all_authors_approved":
                    item["value"] = False
            (reports / "author-decisions.json").write_text(
                json.dumps(decisions), encoding="utf-8"
            )

            runner = audit.Audit(project, require_pdf=True, strict=False)
            runner.audit_author_decisions(manifest)
            codes = {item["code"] for item in runner.findings}
            self.assertIn("stale-author-decisions", codes)
            self.assertIn("authors-not-approved", codes)

    def test_source_recovery_requires_page_attestation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            reports = project / "reports"
            reports.mkdir()
            render_hash = "b" * 64
            (reports / "source-render-review.json").write_text(
                json.dumps({"render_sha256": render_hash, "page_count": 2}), encoding="utf-8"
            )
            report = {
                "schema_version": "1.0", "source_sha256": "a" * 64,
                "source_render_sha256": render_hash,
                "bibliography": {
                    "status": "verified", "outcome": "confirmed-absent", "evidence": "Reviewed",
                    "reviewed_by": "Author", "reviewed_at": "2026-08-02T00:00:00Z",
                    "pages_inspected": [1], "records": [],
                },
                "citations": {"status": "not-needed", "occurrences": []},
            }
            (reports / "source-recovery.json").write_text(json.dumps(report), encoding="utf-8")
            manifest = {
                "source": {"sha256": "a" * 64},
                "warnings": [{"code": "bibliography-not-detected"}],
            }
            runner = audit.Audit(project, require_pdf=True, strict=False)
            recovery = runner.load_source_recovery(manifest)
            self.assertNotIn("bibliography-not-detected", recovery["resolved_codes"])
            self.assertIn("invalid-source-recovery-pages", {item["code"] for item in runner.findings})

    def test_malformed_build_report_fails_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            (project / "manuscript").mkdir()
            (project / "reports").mkdir()
            (project / "submission").mkdir()
            (project / "manuscript" / "journal-profile.json").write_text(
                json.dumps({
                    "profile": "generic-imrad-num", "status": "interchange-draft",
                    "article_type": "research-article", "target_journal": None,
                    "format_mode": "draft-only", "overrides": [],
                }), encoding="utf-8"
            )
            reports = (
                [],
                {
                    "schema_version": "1.0",
                    "success": True,
                    "compiler": "tectonic",
                    "source_sha256": {"manuscript.tex": "a" * 64},
                    "output_sha256": {"submission/manuscript.pdf": "b" * 64},
                },
            )
            for report in reports:
                with self.subTest(report_type=type(report).__name__):
                    (project / "reports" / "build-report.json").write_text(
                        json.dumps(report), encoding="utf-8"
                    )
                    runner = audit.Audit(project, require_pdf=True, strict=False)
                    runner.audit_profile_and_package({}, [], [])
                    codes = {item["code"] for item in runner.findings}
                    if isinstance(report, list):
                        self.assertIn("invalid-build-report", codes)
                    else:
                        self.assertIn("invalid-build-documents", codes)
                        self.assertIn("invalid-build-time", codes)

    def test_source_archive_missing_file_and_hash_mismatch_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            manuscript = project / "manuscript"
            reports = project / "reports"
            submission = project / "submission"
            for path in (manuscript, reports, submission):
                path.mkdir()
            sources = {
                "manuscript.tex": r"\documentclass{article}\begin{document}x\end{document}",
                "references.bib": "",
                "traceability.csv": ",".join(prepare.TRACE_FIELDS) + "\n",
                "evidence-map.csv": ",".join(prepare.EVIDENCE_FIELDS) + "\n",
                "journal-profile.json": json.dumps(
                    {
                        "profile": "generic-imrad-num", "status": "interchange-draft",
                        "article_type": "research-article", "target_journal": None,
                        "format_mode": "draft-only", "overrides": [],
                    }
                ),
            }
            for name, content in sources.items():
                (manuscript / name).write_text(content, encoding="utf-8")
            source_hashes = {
                name: hashlib.sha256((manuscript / name).read_bytes()).hexdigest()
                for name in sources
            }
            pdf = submission / "manuscript.pdf"
            pdf.write_bytes(b"%PDF-synthetic")
            pdf_hash = hashlib.sha256(pdf.read_bytes()).hexdigest()
            (reports / "build-report.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.0", "built_at": "2026-08-02T00:00:00Z",
                        "success": True, "compiler": "tectonic",
                        "documents": [
                            {
                                "tex": "manuscript.tex", "pdf": "manuscript.pdf", "success": True,
                                "commands": [
                                    {
                                        "argv": [
                                            "/usr/bin/tectonic", "--keep-intermediates", "--keep-logs",
                                            "--synctex", "--untrusted", "manuscript.tex",
                                        ],
                                        "returncode": 0, "timed_out": False,
                                    }
                                ],
                            }
                        ],
                        "source_archive_files": list(sources),
                        "source_sha256": source_hashes,
                        "output_sha256": {"submission/manuscript.pdf": pdf_hash},
                    }
                ),
                encoding="utf-8",
            )
            with zipfile.ZipFile(submission / "submission-sources.zip", "w") as archive:
                for name in sources:
                    if name == "references.bib":
                        continue
                    if name == "manuscript.tex":
                        archive.writestr(name, "changed after build")
                    else:
                        archive.write(manuscript / name, arcname=name)

            runner = audit.Audit(project, require_pdf=True, strict=False)
            runner.audit_profile_and_package({}, [], [manuscript / "manuscript.tex"])
            codes = {item["code"] for item in runner.findings}
            self.assertIn("incomplete-source-archive", codes)
            self.assertIn("source-archive-hash-mismatch", codes)

    def test_malformed_manifest_and_source_review_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            (project / "source").mkdir()
            (project / "reports").mkdir()
            malformed_manifest = {
                "schema_version": "1.0", "source": {"sha256": "a" * 64},
                "warnings": [{"code": [], "severity": "error", "message": "invalid"}],
                "paragraphs": [], "figures": [], "tables": [], "objects": [],
                "equations": [], "footnotes": [], "endnotes": [], "comments": [],
                "bibliography_entries": [], "word_bibliography_sources": [],
                "citation_fields": [], "citation_candidates": [], "active_word_fields": [],
                "revision_markup": [],
            }
            runner = audit.Audit(project, require_pdf=True, strict=False)
            runner.validate_manifest(malformed_manifest)
            self.assertIn("invalid-manifest-schema", {item["code"] for item in runner.findings})

            source_render = project / "source" / "source-render.pdf"
            source_render.write_bytes(b"%PDF-source")
            (project / "reports" / "source-render-review.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.0", "status": "verified",
                        "source_sha256": "a" * 64, "rendered_from_sha256": "a" * 64,
                        "renderer": "Word", "reviewed_by": "Reviewer",
                        "reviewed_at": "2026-08-02T00:00:00Z",
                        "render_file": "source/source-render.pdf",
                        "render_sha256": hashlib.sha256(source_render.read_bytes()).hexdigest(),
                        "page_count": 1, "pages_inspected": [1, {}],
                    }
                ),
                encoding="utf-8",
            )
            review_runner = audit.Audit(project, require_pdf=True, strict=False)
            review_runner.pdf_page_count = lambda _path: 1
            review_runner.audit_source_render_review({"source": {"sha256": "a" * 64}})
            self.assertIn(
                "source-render-review-incomplete",
                {item["code"] for item in review_runner.findings},
            )

    def test_audit_rejects_symlinked_report_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            outside = root / "outside"
            project.mkdir()
            outside.mkdir()
            marker = outside / "marker.txt"
            marker.write_text("unchanged", encoding="utf-8")
            for name in ("source", "manuscript", "submission"):
                (project / name).mkdir()
            (project / "reports").symlink_to(outside, target_is_directory=True)
            self.assertEqual(audit.main([str(project), "--require-pdf"]), 2)
            self.assertEqual(marker.read_text(encoding="utf-8"), "unchanged")

    def test_audit_removes_stale_promoted_pdf_when_candidate_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            for name in ("source", "manuscript", "reports", "submission"):
                (project / name).mkdir()
            promoted = project / "submission" / "submission.pdf"
            promoted.write_bytes(b"%PDF-stale")
            self.assertEqual(audit.main([str(project), "--require-pdf"]), 1)
            self.assertFalse(promoted.exists())

    def test_build_rejects_symlinked_manuscript_root_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            outside = root / "outside"
            project.mkdir()
            outside.mkdir()
            (outside / "manuscript.pdf").write_bytes(b"do-not-touch")
            (project / "manuscript").symlink_to(outside, target_is_directory=True)
            for name in ("source", "reports", "submission"):
                (project / name).mkdir()
            stale = project / "submission" / "submission.pdf"
            stale.write_bytes(b"stale")
            self.assertEqual(build.main([str(project)]), 2)
            self.assertEqual((outside / "manuscript.pdf").read_bytes(), b"do-not-touch")
            self.assertFalse(stale.exists())


if __name__ == "__main__":
    unittest.main()
