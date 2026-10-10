// Maintainer tool: refresh the pinned, offline browser renderer (never run at install time).
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { cpSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const version = "6.3.289";
const integrity = "sha512-ZHjSVpDa3D6izMq8/04lvkhkATUmL9px6ChPaXc1k6nU2Mrhlg1/7F0bdUqCwUjw3NsPTfPZsMDUU6ZIcRaeQw==";
const target = fileURLToPath(new URL("../skills/anything-to-journal/assets/workspace/pdfjs/", import.meta.url));
const temporary = mkdtempSync(join(tmpdir(), "journal-pdfjs-"));
try {
  execFileSync("npm", ["pack", `pdfjs-dist@${version}`, "--pack-destination", temporary, "--json"], { stdio: "pipe" });
  const archive = join(temporary, `pdfjs-dist-${version}.tgz`);
  const actual = `sha512-${createHash("sha512").update(readFileSync(archive)).digest("base64")}`;
  if (actual !== integrity) throw new Error("PDF.js package integrity mismatch");
  execFileSync("tar", ["-xzf", archive, "-C", temporary]);
  const source = join(temporary, "package");
  mkdirSync(target, { recursive: true });
  for (const name of ["pdf", "pdf.worker"]) {
    cpSync(join(source, "legacy", "build", `${name}.min.mjs`), join(target, `${name}.mjs`));
  }
  for (const name of ["cmaps", "standard_fonts", "wasm", "iccs"]) {
    rmSync(join(target, name), { recursive: true, force: true });
    cpSync(join(source, name), join(target, name), { recursive: true });
  }
  // PDF JavaScript scripting is not used by the workspace.
  for (const name of ["quickjs-eval.js", "quickjs-eval.wasm"]) rmSync(join(target, "wasm", name), { force: true });
  cpSync(join(source, "LICENSE"), join(target, "LICENSE"));
  writeFileSync(join(target, "VERSION.json"), `${JSON.stringify({ name: "pdfjs-dist", version, integrity, source: "https://github.com/mozilla/pdf.js" }, null, 2)}\n`);
  console.log(`Vendored PDF.js ${version} with fonts, CMaps, codecs, and license notices.`);
} finally {
  rmSync(temporary, { recursive: true, force: true });
}
