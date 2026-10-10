from __future__ import annotations

import csv
import hashlib
import io
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "anything-to-journal" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import workspace_editor  # noqa: E402
from workspace_evidence import EvidenceCatalog, EvidenceError, checked_locator  # noqa: E402


PDF = b"%PDF-1.4\n% source fixture\n%%EOF\n"


class EvidenceFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="journal-evidence-")
        self.project = Path(self.temporary.name) / "journal-output"
        for name in ("manuscript", "reports", "submission", "source/materials"):
            (self.project / name).mkdir(parents=True)
        self.manifest = {"schema_version": "1.0", "source": {"kind": "workspace"}, "materials": []}
        self.catalog = EvidenceCatalog(self.project)
        self.write_manifest()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_manifest(self) -> None:
        (self.project / "source/source-manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")

    def material(self, source_id: str, name: str, content: bytes) -> Path:
        relative = f"source/materials/{source_id}{Path(name).suffix}"
        path = self.project / relative
        path.write_bytes(content)
        self.manifest["materials"].append({
            "source_id": source_id, "original_relative_path": name,
            "copied_path": relative, "sha256": hashlib.sha256(content).hexdigest(),
        })
        self.write_manifest()
        return path

    def evidence_map(self, *rows: dict) -> None:
        with (self.project / "manuscript/evidence-map.csv").open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["claim_id", "manuscript_file", "source_ids", "manuscript_claim", "status", "notes"])
            writer.writeheader()
            writer.writerows(rows)

    def locations(self, *claims: dict, schema_version: str = "1.0") -> None:
        (self.project / "manuscript/evidence-locations.json").write_text(
            json.dumps({"schema_version": schema_version, "claims": claims}), encoding="utf-8"
        )


class EvidenceCatalogTests(EvidenceFixture):
    def test_precise_locations_merge_with_claim_status_and_original_names(self) -> None:
        self.material("src-material-0001", "results/model.csv", b"model,accuracy\nA,0.9\n")
        self.material("src-material-0002", "paper.pdf", PDF)
        self.evidence_map({"claim_id": "accuracy", "source_ids": "src-material-0001;src-material-0002", "manuscript_claim": "Model accuracy is 0.9", "status": "pending"})
        self.locations({
            "claim_id": "accuracy", "kind": "value",
            "preview": {"text": "accuracy is 0.9", "page": 2, "occurrence": 1},
            "sources": [
                {"source_id": "src-material-0001", "locator": {"text": "A,0.9", "line_start": 2, "line_end": 2}},
                {"source_id": "src-material-0002", "locator": {"page": 3, "rect": [0.1, 0.2, 0.3, 0.1], "label": "Table 2"}},
            ],
        })
        payload = self.catalog.evidence()
        claim = payload["claims"][0]
        self.assertEqual(claim["status"], "pending")
        self.assertEqual(claim["kind"], "value")
        self.assertEqual(claim["preview"]["page"], 2)
        self.assertEqual(claim["sources"][0]["name"], "results/model.csv")
        self.assertEqual(claim["sources"][0]["locator"]["text"], "A,0.9")
        self.assertTrue(all(source["available"] for source in claim["sources"]))
        self.assertEqual(payload["warnings"], [])
        self.assertEqual(self.catalog.source_preview("src-material-0001")["text"], "model,accuracy\nA,0.9\n")

    def test_older_mapping_falls_back_without_inventing_a_source_quote(self) -> None:
        self.material("source-one", "experiment.txt", b"The recorded accuracy was 0.9")
        self.evidence_map({"claim_id": "claim-one", "source_ids": "source-one", "manuscript_claim": "accuracy 0.9", "status": "verified"})
        payload = self.catalog.evidence()
        claim = payload["claims"][0]
        self.assertEqual(claim["preview"], {"text": "accuracy 0.9"})
        self.assertEqual(claim["status"], "verified")
        self.assertEqual(claim["sources"][0]["locator"], {})
        self.assertIn("exact passage", claim["sources"][0]["warning"])
        self.locations({"claim_id": "claim-one", "preview": {"text": "bad"}}, schema_version="9")
        fallback = self.catalog.evidence()
        self.assertEqual(fallback["claims"], payload["claims"])
        self.assertIn("schema_version", fallback["warnings"][0])

    def test_invalid_coordinates_are_never_returned_as_highlight_locations(self) -> None:
        self.material("source-one", "experiment.txt", b"accuracy 0.9")
        self.evidence_map({"claim_id": "one", "source_ids": "source-one", "manuscript_claim": "accuracy 0.9"})
        self.locations({
            "claim_id": "one", "kind": [], "preview": {"page": True, "rect": [0, 0, 1, 1]},
            "sources": [{"source_id": "source-one", "locator": {"page": 1, "rect": [0.9, 0, 0.2, 0.1]}}],
        })
        payload = self.catalog.evidence()
        self.assertEqual(payload["claims"][0]["preview"], {"text": "accuracy 0.9"})
        self.assertEqual(payload["claims"][0]["sources"][0]["locator"], {})
        self.assertEqual(payload["claims"][0]["kind"], "claim")
        self.assertEqual(len(payload["warnings"]), 3)
        for locator in ({"occurrence": 0}, {"page": 1.5}, {"line_start": 4, "line_end": 2}, {"page": 1, "rect": [10**400, 0, 0.1, 0.1]}):
            with self.subTest(locator=locator), self.assertRaises(ValueError):
                checked_locator(locator)
        with self.assertRaises(ValueError):
            checked_locator({"rect": [0, 0, 1, 1]}, preview=True)
        self.assertEqual(checked_locator({"rect": [0, 0, 1, 1]}), {"rect": [0, 0, 1, 1]})

    def test_locator_file_cannot_add_claims_or_sources_to_verified_ledger(self) -> None:
        self.material("source-one", "experiment.txt", b"accuracy 0.9")
        self.material("source-two", "different.txt", b"accuracy 0.2")
        self.evidence_map({"claim_id": "one", "source_ids": "source-one", "manuscript_claim": "accuracy 0.9", "status": "verified"})
        self.locations(
            {"claim_id": "one", "sources": [{"source_id": "source-two", "locator": {"text": "accuracy 0.2"}}]},
            {"claim_id": "orphan", "preview": {"text": "invented claim"}},
        )
        payload = self.catalog.evidence()
        self.assertEqual([claim["id"] for claim in payload["claims"]], ["one"])
        self.assertEqual([source["id"] for source in payload["claims"][0]["sources"]], ["source-one"])
        self.assertEqual(payload["claims"][0]["status"], "verified")
        self.assertTrue(any("absent from evidence-map" in warning for warning in payload["warnings"]))
        self.assertTrue(any("not mapped by evidence-map" in warning for warning in payload["warnings"]))

    def test_changed_sources_invalidate_availability_and_revision(self) -> None:
        path = self.material("source-one", "experiment.txt", b"accuracy 0.9")
        self.evidence_map({"claim_id": "one", "source_ids": "source-one", "manuscript_claim": "accuracy 0.9"})
        before = self.catalog.evidence()
        path.write_bytes(b"accuracy 0.2")
        after = self.catalog.evidence()
        self.assertNotEqual(before["revision"], after["revision"])
        self.assertFalse(after["claims"][0]["sources"][0]["available"])
        self.assertIn("SHA-256 mismatch", after["claims"][0]["sources"][0]["warning"])
        with self.assertRaises(EvidenceError):
            self.catalog.source_bytes("source-one")

    def test_manifest_cannot_escape_source_materials_or_follow_symlink_parents(self) -> None:
        outside = Path(self.temporary.name) / "secret.txt"
        outside.write_bytes(b"private")
        self.manifest["materials"] = [{
            "source_id": "bad", "original_relative_path": "secret.txt",
            "copied_path": "source/materials/../../../secret.txt",
            "sha256": hashlib.sha256(b"private").hexdigest(),
        }]
        self.write_manifest()
        with self.assertRaises(EvidenceError):
            self.catalog.source_bytes("bad")
        (self.project / "source/materials/linked").symlink_to(outside.parent, target_is_directory=True)
        self.manifest["materials"][0]["copied_path"] = "source/materials/linked/secret.txt"
        self.write_manifest()
        with self.assertRaises(EvidenceError):
            self.catalog.source_bytes("bad")
        with self.assertRaises(EvidenceError):
            self.catalog.source_bytes("../../secret.txt")

    def test_legacy_source_ids_use_preserved_docx_and_ignore_absolute_path(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Accuracy 0.9</w:t></w:r></w:p><w:p><w:r><w:t>Second paragraph</w:t></w:r></w:p></w:body></w:document>')
        content = buffer.getvalue()
        (self.project / "source/original.docx").write_bytes(content)
        self.manifest = {
            "source": {"file_name": "Study.docx", "absolute_path": "/private/never-open.docx", "sha256": hashlib.sha256(content).hexdigest()},
            "paragraphs": [{"source_id": "src-par-000001", "index": 1, "text": "Accuracy 0.9", "story": "document"}],
        }
        self.write_manifest()
        self.evidence_map({"claim_id": "one", "source_ids": "src-par-000001", "manuscript_claim": "Accuracy 0.9"})
        source = self.catalog.evidence()["claims"][0]["sources"][0]
        self.assertTrue(source["available"])
        self.assertEqual(source["name"], "Study.docx")
        self.assertEqual(source["locator"]["label"], "document, paragraph 1")
        preview = self.catalog.source_preview("src-par-000001")
        self.assertEqual(preview["kind"], "text")
        self.assertEqual(preview["text"], "Accuracy 0.9\nSecond paragraph")
        (self.project / "source/original.docx").unlink()
        self.assertFalse(self.catalog.evidence()["claims"][0]["sources"][0]["available"])

    def test_empty_and_malformed_optional_metadata_do_not_break_a_workspace(self) -> None:
        self.assertEqual(self.catalog.evidence()["claims"], [])
        (self.project / "manuscript/evidence-locations.json").write_text("{broken", encoding="utf-8")
        payload = self.catalog.evidence()
        self.assertEqual(payload["claims"], [])
        self.assertTrue(payload["warnings"])


class EvidenceHTTPTests(EvidenceFixture):
    def setUp(self) -> None:
        super().setUp()
        tex = self.project / "manuscript/manuscript.tex"
        tex.write_text("initial source", encoding="utf-8")
        (self.project / "submission/manuscript.pdf").write_bytes(PDF)
        (self.project / "reports/build-report.json").write_text(json.dumps({"source_sha256": {"manuscript.tex": hashlib.sha256(tex.read_bytes()).hexdigest()}}), encoding="utf-8")
        self.workspace = workspace_editor.ManuscriptWorkspace(workspace_editor.safe_project(self.project), debounce=60)
        self.server = workspace_editor.WorkspaceHTTPServer(("127.0.0.1", 0), self.workspace)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.workspace.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        super().tearDown()

    def request(self, path: str, headers: dict | None = None, method: str = "GET") -> tuple[int, bytes, dict]:
        try:
            with urllib.request.urlopen(urllib.request.Request(self.base_url + path, headers=headers or {}, method=method), timeout=5) as response:
                return response.status, response.read(), dict(response.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), dict(exc.headers)

    def test_source_endpoints_range_download_headers_and_active_content(self) -> None:
        self.material("paper", "研究/paper.pdf", PDF)
        self.material("script", 'figures/unsafe"\r\n.svg', b'<svg><script>alert(1)</script></svg>')
        status, body, headers = self.request("/api/source?id=paper", {"Range": "bytes=0-9"})
        self.assertEqual((status, body), (206, PDF[:10]))
        self.assertEqual(headers["Content-Type"], "application/pdf")
        self.assertTrue(headers["Content-Disposition"].startswith("inline;"))
        self.assertEqual(headers["Content-Range"], f"bytes 0-9/{len(PDF)}")
        status, _, headers = self.request("/api/source?id=script")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/octet-stream")
        self.assertTrue(headers["Content-Disposition"].startswith("attachment;"))
        self.assertNotIn("\r", headers["Content-Disposition"])
        self.assertNotIn("\n", headers["Content-Disposition"])
        status, body, _ = self.request("/api/source-preview?id=script")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["kind"], "text")
        self.assertEqual(self.request("/api/source?id=..%2F..%2Fsecret")[0], 404)
        self.assertEqual(self.request("/api/source?id=paper", {"Range": "bytes=10000-"})[0], 416)
        self.assertEqual(self.request("/api/source?id=paper", method="HEAD")[1], b"")

    def test_state_exposes_evidence_revision_and_stale_pdf_state(self) -> None:
        self.material("source-one", "experiment.txt", b"accuracy 0.9")
        self.evidence_map({"claim_id": "one", "source_ids": "source-one", "manuscript_claim": "accuracy 0.9"})
        _, body, _ = self.request("/api/state")
        first = json.loads(body)
        self.assertTrue(first["pdf"]["current"])
        self.assertFalse(first["pdf"]["stale"])
        self.assertEqual(first["evidence_revision"], json.loads(self.request("/api/evidence")[1])["revision"])
        self.locations({"claim_id": "one", "preview": {"text": "accuracy 0.9", "page": 1}})
        (self.project / "manuscript/manuscript.tex").write_text("edited source", encoding="utf-8")
        second = json.loads(self.request("/api/state")[1])
        self.assertNotEqual(first["evidence_revision"], second["evidence_revision"])
        self.assertFalse(second["pdf"]["current"])
        self.assertTrue(second["pdf"]["stale"])

    def test_only_local_vendored_pdfjs_assets_are_exposed(self) -> None:
        status, _, headers = self.request("/assets/pdfjs/pdf.mjs")
        self.assertEqual(status, 200)
        self.assertIn("javascript", headers["Content-Type"])
        self.assertIn("worker-src 'self'", headers["Content-Security-Policy"])
        self.assertEqual(self.request("/assets/pdfjs/../../scripts/workspace_editor.py")[0], 404)
        self.assertEqual(self.request("/assets/pdfjs/LICENSE")[0], 404)

    def test_malformed_location_values_cannot_break_state_or_evidence(self) -> None:
        self.material("source-one", "experiment.txt", b"accuracy 0.9")
        self.evidence_map({"claim_id": "one", "source_ids": "source-one", "manuscript_claim": "accuracy 0.9"})
        malformed = {
            "huge_integer": json.dumps({"schema_version": "1.0", "claims": [{"claim_id": "one", "preview": {"page": 1, "rect": [10**400, 0, 0.1, 0.1]}}]}),
            "unpaired_surrogate": json.dumps({"schema_version": "1.0", "claims": [{"claim_id": "one", "preview": {"text": "\ud800"}}]}),
            "deep_json": '{"claims":' + '[' * 1200 + '0' + ']' * 1200 + '}',
            "excessive_nesting": '{"unused":' + '[' * 100 + '0' + ']' * 100 + '}',
        }
        for case, content in malformed.items():
            with self.subTest(case=case):
                (self.project / "manuscript/evidence-locations.json").write_text(content, encoding="utf-8")
                status, body, _ = self.request("/api/state")
                self.assertEqual(status, 200)
                self.assertTrue(json.loads(body)["pdf"]["current"])
                status, body, _ = self.request("/api/evidence")
                self.assertEqual(status, 200)
                evidence = json.loads(body)
                self.assertEqual(evidence["claims"][0]["preview"], {"text": "accuracy 0.9"})
                self.assertTrue(evidence["warnings"])


if __name__ == "__main__":
    unittest.main()
