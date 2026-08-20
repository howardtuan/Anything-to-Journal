from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import MethodType


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "anything-to-journal" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import workspace_editor  # noqa: E402


PDF_ONE = b"%PDF-1.4\n% synthetic previous preview\n%%EOF\n"
PDF_TWO = b"%PDF-1.4\n% synthetic updated preview\n%%EOF\n"


class WorkspaceEditorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="anything-to-journal-workspace-")
        self.project = Path(self.temporary.name) / "journal-output"
        self.manuscript = self.project / "manuscript"
        self.reports = self.project / "reports"
        self.submission = self.project / "submission"
        self.manuscript.mkdir(parents=True)
        self.reports.mkdir()
        self.submission.mkdir()
        (self.manuscript / "manuscript.tex").write_text(
            "\\documentclass{article}\n\\begin{document}\nInitial text.\n\\end{document}\n",
            encoding="utf-8",
        )
        (self.manuscript / "section.tex").write_text("Supporting section.\n", encoding="utf-8")
        (self.manuscript / "references.bib").write_text(
            "@article{test,\n  title = {Synthetic}\n}\n", encoding="utf-8"
        )
        (self.submission / "manuscript.pdf").write_bytes(PDF_ONE)
        (self.submission / "submission.pdf").write_bytes(PDF_ONE)
        (self.submission / "submission-package.zip").write_bytes(b"stale package")
        (self.project / "project.json").write_text(
            json.dumps({"schema_version": "1.0", "submission_ready": True}) + "\n",
            encoding="utf-8",
        )
        (self.reports / "quality-report.json").write_text(
            json.dumps({"submission_ready": True, "draft_checks_passed": True}) + "\n",
            encoding="utf-8",
        )
        (self.reports / "quality-report.md").write_text("# Quality report\n\nPASS\n", encoding="utf-8")
        (self.reports / "visual-inspection.json").write_text(
            json.dumps(
                {
                    "status": "verified",
                    "reviewed_by": "Tester",
                    "reviewed_at": "2026-01-01T00:00:00+00:00",
                    "files": [{"file": "submission/manuscript.pdf", "status": "verified", "pages_inspected": [1]}],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (self.reports / "author-decisions.json").write_text(
            json.dumps({"status": "verified", "decisions": []}) + "\n", encoding="utf-8"
        )
        (self.reports / "build-report.json").write_text(
            json.dumps(
                {
                    "success": True,
                    "source_sha256": {
                        path.name: workspace_editor.sha256_file(path)
                        for path in self.manuscript.iterdir()
                        if path.is_file()
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        self.workspace = workspace_editor.ManuscriptWorkspace(
            workspace_editor.safe_project(self.project), debounce=0.01
        )
        self.server = workspace_editor.WorkspaceHTTPServer(("127.0.0.1", 0), self.workspace)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.workspace.close()
        self.thread.join(timeout=2)
        self.temporary.cleanup()

    def request(
        self,
        path: str,
        method: str = "GET",
        payload: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, bytes, dict[str, str]]:
        body = None
        request_headers = dict(headers or {})
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        if method != "GET":
            request_headers["X-Workspace-Request"] = "1"
        request = urllib.request.Request(
            self.base_url + path,
            data=body,
            method=method,
            headers=request_headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=4) as response:
                return response.status, response.read(), dict(response.headers)
        except urllib.error.HTTPError as error:
            return error.code, error.read(), dict(error.headers)

    def state(self) -> dict[str, object]:
        status, content, _ = self.request("/api/state")
        self.assertEqual(status, 200)
        return json.loads(content)

    def wait_for_status(self, expected: str, timeout: float = 3) -> dict[str, object]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self.state()
            if state["compile_status"] == expected:
                return state
            time.sleep(0.03)
        self.fail(f"workspace did not reach compile status {expected}")

    def install_fake_compile(self, success: bool, pdf: bytes = PDF_TWO) -> None:
        def fake_compile(instance: workspace_editor.ManuscriptWorkspace):
            if success:
                return True, "", pdf
            return False, "Synthetic LaTeX error on line 3", None

        self.workspace._run_preview_compile = MethodType(fake_compile, self.workspace)

    def test_pdf_preview_is_default_scrollable_tab_and_assets_are_local(self) -> None:
        status, content, headers = self.request("/")
        self.assertEqual(status, 200)
        html = content.decode("utf-8")
        self.assertIn('id="pdfTab" class="tab active"', html)
        self.assertIn('aria-selected="true"', html)
        self.assertIn('id="pdfFrame"', html)
        self.assertIn('id="latexTab"', html)
        self.assertIn('id="sourceEditor"', html)
        self.assertNotIn("https://", html)
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])

        status, content, headers = self.request("/api/pdf", headers={"Range": "bytes=0-9"})
        self.assertEqual(status, 206)
        self.assertEqual(content, PDF_ONE[:10])
        self.assertEqual(headers["Accept-Ranges"], "bytes")

        css = (ROOT / "skills" / "anything-to-journal" / "assets" / "workspace" / "workspace.css").read_text()
        self.assertIn("#pdfFrame { width: 100%; height: 100%", css)

    def test_editor_supports_multiple_sources_syntax_search_undo_and_save_shortcut(self) -> None:
        state = self.state()
        self.assertEqual(
            [record["path"] for record in state["files"]],
            ["manuscript.tex", "references.bib", "section.tex"],
        )
        script = (
            ROOT / "skills" / "anything-to-journal" / "assets" / "workspace" / "workspace.js"
        ).read_text(encoding="utf-8")
        self.assertIn("highlightLatexLine", script)
        self.assertIn("highlightBibLine", script)
        self.assertIn('event.key.toLowerCase() === "s"', script)
        self.assertIn('event.key.toLowerCase() === "f"', script)
        self.assertIn("performUndo", script)
        self.assertIn("performRedo", script)
        self.assertIn("lineNumbers", script)

    def test_save_writes_actual_file_compiles_preview_and_invalidates_final_state(self) -> None:
        self.install_fake_compile(True)
        status, content, _ = self.request("/api/file?path=manuscript.tex")
        self.assertEqual(status, 200)
        record = json.loads(content)
        updated = record["content"].replace("Initial text.", "Edited in the browser.")
        status, content, _ = self.request(
            "/api/file?path=manuscript.tex",
            method="PUT",
            payload={"content": updated, "expected_sha256": record["sha256"]},
        )
        self.assertEqual(status, 200)
        self.assertIn("Edited in the browser.", (self.manuscript / "manuscript.tex").read_text())

        state = self.wait_for_status("saved")
        self.assertEqual(state["pdf"]["version"], workspace_editor.sha256_bytes(PDF_TWO))
        self.assertEqual(self.workspace.preview_pdf.read_bytes(), PDF_TWO)
        project_state = json.loads((self.project / "project.json").read_text())
        quality = json.loads((self.reports / "quality-report.json").read_text())
        visual = json.loads((self.reports / "visual-inspection.json").read_text())
        author = json.loads((self.reports / "author-decisions.json").read_text())
        self.assertFalse(project_state["submission_ready"])
        self.assertFalse(quality["submission_ready"])
        self.assertEqual(visual["status"], "pending")
        self.assertEqual(author["status"], "pending")
        self.assertFalse((self.submission / "submission.pdf").exists())
        self.assertFalse((self.submission / "submission-package.zip").exists())
        self.assertTrue((self.reports / "workspace-invalidation.json").is_file())

    def test_external_change_syncs_and_stale_editor_save_is_rejected(self) -> None:
        self.install_fake_compile(True)
        status, content, _ = self.request("/api/file?path=manuscript.tex")
        self.assertEqual(status, 200)
        loaded = json.loads(content)
        external = loaded["content"].replace("Initial text.", "Changed by Codex.")
        (self.manuscript / "manuscript.tex").write_text(external, encoding="utf-8")

        state = self.state()
        current = next(record for record in state["files"] if record["path"] == "manuscript.tex")
        self.assertNotEqual(current["sha256"], loaded["sha256"])
        self.assertIn("manuscript.tex", state["last_changed_paths"])

        status, content, _ = self.request(
            "/api/file?path=manuscript.tex",
            method="PUT",
            payload={"content": loaded["content"] + "% stale\n", "expected_sha256": loaded["sha256"]},
        )
        self.assertEqual(status, 409)
        self.assertIn("modified outside", json.loads(content)["error"])
        self.assertEqual((self.manuscript / "manuscript.tex").read_text(), external)
        self.wait_for_status("saved")

    def test_compile_failure_preserves_last_successful_pdf(self) -> None:
        self.install_fake_compile(False)
        previous_hash = workspace_editor.sha256_file(self.workspace.preview_pdf)
        status, _, _ = self.request("/api/recompile", method="POST")
        self.assertEqual(status, 202)
        state = self.wait_for_status("compile_failed")
        self.assertEqual(state["pdf"]["version"], previous_hash)
        self.assertIn("Synthetic LaTeX error", state["compile_error"])
        self.assertEqual(self.workspace.preview_pdf.read_bytes(), PDF_ONE)

    def test_path_traversal_and_cross_origin_mutations_are_rejected(self) -> None:
        status, content, _ = self.request("/api/file?path=..%2Fproject.json")
        self.assertEqual(status, 400)
        self.assertIn("inside manuscript", json.loads(content)["error"])

        loaded = json.loads(self.request("/api/file?path=manuscript.tex")[1])
        status, _, _ = self.request(
            "/api/file?path=manuscript.tex",
            method="PUT",
            payload={"content": loaded["content"], "expected_sha256": loaded["sha256"]},
            headers={"Origin": "http://example.com"},
        )
        self.assertEqual(status, 403)

        outside = Path(self.temporary.name) / "outside.tex"
        outside.write_text("private outside content\n", encoding="utf-8")
        (self.manuscript / "section.tex").unlink()
        (self.manuscript / "section.tex").symlink_to(outside)
        status, content, _ = self.request("/api/state")
        self.assertEqual(status, 400)
        self.assertIn("symbolic link", json.loads(content)["error"])


if __name__ == "__main__":
    unittest.main()
