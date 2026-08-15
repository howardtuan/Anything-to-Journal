import assert from "node:assert/strict";
import {
  existsSync,
  lstatSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { execFileSync, spawnSync } from "node:child_process";
import test from "node:test";

const repository = new URL("..", import.meta.url);
const cli = new URL("../bin/anything-to-journal.mjs", import.meta.url);

function run(args, environment = {}) {
  return execFileSync(process.execPath, [cli.pathname, ...args], {
    cwd: repository,
    encoding: "utf8",
    env: { ...process.env, ...environment },
  });
}

function runFailure(args, environment = {}) {
  return spawnSync(process.execPath, [cli.pathname, ...args], {
    cwd: repository,
    encoding: "utf8",
    env: { ...process.env, ...environment },
  });
}

function temporaryDirectory() {
  return mkdtempSync(join(tmpdir(), "anything-to-journal-installer-"));
}

test("package exposes one npx-compatible binary", () => {
  const packageJson = JSON.parse(
    readFileSync(new URL("../package.json", import.meta.url), "utf8"),
  );
  assert.equal(packageJson.name, "anything-to-journal");
  assert.equal(packageJson.bin["anything-to-journal"], "bin/anything-to-journal.mjs");
  assert.equal(packageJson.private, undefined);
  assert.equal(packageJson.publishConfig.access, "public");
  assert.ok(packageJson.files.includes("!skills/anything-to-journal/**/*.pyc"));
});

test("installs a standalone skill without overwriting it", () => {
  const root = temporaryDirectory();
  try {
    const skills = join(root, "skills");
    const output = run(["install", "--destination", skills]);
    const target = join(skills, "anything-to-journal");
    assert.match(output, /Installed Anything-to-Journal 1[.]0[.]0/);
    assert.match(readFileSync(join(target, "SKILL.md"), "utf8"), /^name: anything-to-journal$/m);

    const repeated = runFailure(["install", "--destination", skills]);
    assert.equal(repeated.status, 2);
    assert.match(repeated.stderr, /destination already exists/);
    assert.match(repeated.stderr, /update command/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("updates only a verified installation and removes stale files", () => {
  const root = temporaryDirectory();
  try {
    const skills = join(root, "skills");
    run(["install", "--destination", skills]);
    const target = join(skills, "anything-to-journal");
    writeFileSync(join(target, "stale-file.txt"), "remove during update\n");

    const output = run(["update", "--destination", skills]);
    assert.match(output, /Updated Anything-to-Journal 1[.]0[.]0/);
    assert.equal(existsSync(join(target, "stale-file.txt")), false);
    assert.match(readFileSync(join(target, "SKILL.md"), "utf8"), /^name: anything-to-journal$/m);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("refuses to update an unrelated destination", () => {
  const root = temporaryDirectory();
  try {
    const target = join(root, "skills", "anything-to-journal");
    mkdirSync(target, { recursive: true });
    writeFileSync(join(target, "SKILL.md"), "---\nname: unrelated-skill\ndescription: test\n---\n");

    const result = runFailure(["update", "--destination", join(root, "skills")]);
    assert.equal(result.status, 2);
    assert.match(result.stderr, /refusing to replace/);
    assert.match(readFileSync(join(target, "SKILL.md"), "utf8"), /unrelated-skill/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("updates a verified legacy symlink without changing its source checkout", () => {
  const root = temporaryDirectory();
  try {
    const legacySource = join(root, "legacy-source");
    mkdirSync(legacySource);
    writeFileSync(
      join(legacySource, "SKILL.md"),
      "---\nname: anything-to-journal\ndescription: legacy test\n---\n",
    );
    writeFileSync(join(legacySource, "legacy-only.txt"), "keep in checkout\n");

    const skills = join(root, "skills");
    mkdirSync(skills);
    const target = join(skills, "anything-to-journal");
    symlinkSync(legacySource, target, "dir");

    run(["update", "--destination", skills]);
    assert.equal(lstatSync(target).isSymbolicLink(), false);
    assert.equal(existsSync(join(target, "legacy-only.txt")), false);
    assert.equal(existsSync(join(legacySource, "legacy-only.txt")), true);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("supports Codex, repository, and dry-run destinations", () => {
  const root = temporaryDirectory();
  try {
    const codexHome = join(root, "custom-codex-home");
    const output = run(["install"], { CODEX_HOME: codexHome });
    assert.match(output, new RegExp(join(codexHome, "skills").replaceAll("\\", "\\\\")));
    assert.equal(existsSync(join(codexHome, "skills", "anything-to-journal", "SKILL.md")), true);

    const project = join(root, "project");
    mkdirSync(project);
    const repoOutput = run(["install", "--repo", project]);
    assert.match(repoOutput, /[.]agents/);
    assert.equal(existsSync(join(project, ".agents", "skills", "anything-to-journal", "SKILL.md")), true);

    const dryTarget = join(root, "dry-run-skills");
    const dryOutput = run(["install", "--destination", dryTarget, "--dry-run"]);
    assert.match(dryOutput, /Would install/);
    assert.equal(existsSync(dryTarget), false);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("reports help and package version", () => {
  assert.match(run(["--help"]), /npx anything-to-journal@latest update/);
  assert.equal(run(["--version"]).trim(), "1.0.0");
});
