#!/usr/bin/env node

import {
  cpSync,
  existsSync,
  lstatSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  renameSync,
  rmSync,
} from "node:fs";
import { homedir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const skillName = "anything-to-journal";
const packageRoot = fileURLToPath(new URL("..", import.meta.url));
const sourceSkill = join(packageRoot, "skills", skillName);
const packageJson = JSON.parse(
  readFileSync(join(packageRoot, "package.json"), "utf8"),
);

const help = `Anything-to-Journal ${packageJson.version}

Install or update the Anything-to-Journal Agent Skill.

Usage:
  npx anything-to-journal@latest install [options]
  npx anything-to-journal@latest update [options]

Commands:
  install                  Install a new copy; never overwrite an existing target
  update                   Replace a verified existing copy with this package version

Options:
  --repo <path>            Install under <path>/.agents/skills
  --destination <path>     Use an explicit skills directory
  --dry-run                Validate and show the target without changing files
  -h, --help               Show this help
  -v, --version            Show the package version

Default destination:
  $CODEX_HOME/skills, or ~/.codex/skills when CODEX_HOME is unset
`;

class CliError extends Error {}

function parseArgs(argv) {
  const options = {
    command: "install",
    destination: null,
    repo: null,
    dryRun: false,
    help: false,
    version: false,
  };
  let index = 0;

  if (argv[0] && !argv[0].startsWith("-")) {
    options.command = argv[0];
    index = 1;
  }

  while (index < argv.length) {
    const argument = argv[index];
    if (argument === "--repo" || argument === "--destination") {
      const value = argv[index + 1];
      if (!value || value.startsWith("-")) {
        throw new CliError(`${argument} requires a path`);
      }
      const key = argument === "--repo" ? "repo" : "destination";
      options[key] = value;
      index += 2;
      continue;
    }
    if (argument === "--dry-run") {
      options.dryRun = true;
      index += 1;
      continue;
    }
    if (argument === "-h" || argument === "--help") {
      options.help = true;
      index += 1;
      continue;
    }
    if (argument === "-v" || argument === "--version") {
      options.version = true;
      index += 1;
      continue;
    }
    throw new CliError(`unknown option: ${argument}`);
  }

  if (!new Set(["install", "update"]).has(options.command)) {
    throw new CliError(`unknown command: ${options.command}`);
  }
  if (options.repo && options.destination) {
    throw new CliError("--repo and --destination cannot be used together");
  }
  return options;
}

function pathExists(path) {
  try {
    lstatSync(path);
    return true;
  } catch (error) {
    if (error?.code === "ENOENT") return false;
    throw error;
  }
}

function readSkillName(skillDirectory) {
  const skillFile = join(skillDirectory, "SKILL.md");
  let contents;
  try {
    contents = readFileSync(skillFile, "utf8");
  } catch {
    throw new CliError(`missing or unreadable SKILL.md: ${skillFile}`);
  }
  const frontmatter = contents.match(/^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/);
  const name = frontmatter?.[1].match(/^name:\s*([^\r\n]+)\s*$/m)?.[1]?.trim();
  if (!name) throw new CliError(`invalid SKILL.md frontmatter: ${skillFile}`);
  return name;
}

function rejectSymlinks(directory, relative = "") {
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const absolute = join(directory, entry.name);
    const display = relative ? join(relative, entry.name) : entry.name;
    if (entry.isSymbolicLink()) {
      throw new CliError(`bundled skill contains an unsupported symlink: ${display}`);
    }
    if (entry.isDirectory()) rejectSymlinks(absolute, display);
  }
}

function validateSource() {
  if (!existsSync(sourceSkill)) {
    throw new CliError(`bundled skill is missing: ${sourceSkill}`);
  }
  if (readSkillName(sourceSkill) !== skillName) {
    throw new CliError(`bundled SKILL.md name must be ${skillName}`);
  }
  rejectSymlinks(sourceSkill);
}

function destinationRoot(options) {
  if (options.destination) return resolve(options.destination);
  if (options.repo) {
    const repository = resolve(options.repo);
    if (!pathExists(repository) || !lstatSync(repository).isDirectory()) {
      throw new CliError(`repository directory does not exist: ${repository}`);
    }
    return join(repository, ".agents", "skills");
  }
  const codexHome = process.env.CODEX_HOME?.trim();
  return codexHome
    ? join(resolve(codexHome), "skills")
    : join(homedir(), ".codex", "skills");
}

function validateAction(command, target) {
  const targetExists = pathExists(target);
  if (command === "install" && targetExists) {
    throw new CliError(
      `destination already exists: ${target}\nRun the update command to replace a verified installation.`,
    );
  }
  if (command === "update" && !targetExists) {
    throw new CliError(
      `no installation found at: ${target}\nRun the install command first.`,
    );
  }
  if (command === "update") {
    let installedName;
    try {
      installedName = readSkillName(target);
    } catch (error) {
      throw new CliError(
        `refusing to replace an unverified destination: ${target}\n${error.message}`,
      );
    }
    if (installedName !== skillName) {
      throw new CliError(
        `refusing to replace ${target}: installed skill name is ${installedName}`,
      );
    }
  }
}

function shouldCopy(source) {
  const name = source.split(/[\\/]/).at(-1);
  return !(
    name === "__pycache__" ||
    name === ".DS_Store" ||
    name === ".env" ||
    name.startsWith(".env.") ||
    name === ".dev.vars" ||
    name.startsWith(".dev.vars.") ||
    name.endsWith(".pyc") ||
    name.endsWith(".pyo") ||
    name.endsWith(".pem")
  );
}

function installOrUpdate(options) {
  validateSource();
  const root = destinationRoot(options);
  const target = join(root, skillName);
  validateAction(options.command, target);

  if (options.dryRun) {
    console.log(`Would ${options.command} Anything-to-Journal ${packageJson.version}`);
    console.log(`Target: ${target}`);
    return;
  }

  mkdirSync(root, { recursive: true });
  const stagingRoot = mkdtempSync(join(root, `.${skillName}-install-`));
  const stagedSkill = join(stagingRoot, skillName);
  let backup = null;

  try {
    cpSync(sourceSkill, stagedSkill, {
      recursive: true,
      errorOnExist: true,
      filter: shouldCopy,
    });
    if (readSkillName(stagedSkill) !== skillName) {
      throw new CliError("staged skill failed validation");
    }

    if (options.command === "update") {
      backup = `${target}.backup-${process.pid}-${Date.now()}`;
      renameSync(target, backup);
    }

    try {
      renameSync(stagedSkill, target);
    } catch (error) {
      if (backup && pathExists(backup) && !pathExists(target)) {
        renameSync(backup, target);
        backup = null;
      }
      throw error;
    }

    if (backup && pathExists(backup)) {
      try {
        rmSync(backup, { recursive: true, force: true });
      } catch {
        console.warn(`Updated successfully; previous copy remains at ${backup}`);
      }
    }
  } finally {
    rmSync(stagingRoot, { recursive: true, force: true });
  }

  const verb = options.command === "install" ? "Installed" : "Updated";
  console.log(`${verb} Anything-to-Journal ${packageJson.version}`);
  console.log(`Location: ${target}`);
  console.log("Invoke it with: $anything-to-journal");
  console.log("Restart Codex only if the skill does not appear automatically.");
}

function main() {
  try {
    const options = parseArgs(process.argv.slice(2));
    if (options.help) {
      console.log(help);
      return;
    }
    if (options.version) {
      console.log(packageJson.version);
      return;
    }
    installOrUpdate(options);
  } catch (error) {
    const prefix = error instanceof CliError ? "anything-to-journal" : "unexpected error";
    console.error(`${prefix}: ${error.message}`);
    process.exitCode = error instanceof CliError ? 2 : 1;
  }
}

main();
