# Security policy

## Supported version

Security fixes are applied to the latest release on the default branch. The supported series is `1.x`.

## Threat model

Treat every source material, uploaded venue template, LaTeX project, document, data file, PDF, and ZIP as untrusted. The workflow rejects path traversal, unsafe names and symlinks, duplicate or oversized archive parts, macros, active Word fields, altChunk imports, external package relationships, unresolved revisions, non-flat source archives, nonportable package paths, unbounded subprocess output, stale build evidence, and source-free publication structures. Intake copies bytes and never intentionally executes source scripts, macros, OLE objects, notebooks, or embedded packages.

Word/LibreOffice and TeX/PDF parsers remain third-party attack surfaces. Perform source rendering only after preflight, with macros and link updates disabled, preferably offline or in an isolated account/container. `--no-shell-escape` is not a filesystem sandbox; Tectonic `--untrusted` is preferred, and sensitive projects should still be built in an OS-level sandbox.

## Reporting a vulnerability

Use the private security-advisory feature of the public repository once this project is hosted. Until a maintainer contact is configured, do not publish a weaponized sample or attach real user materials to an issue. Provide a minimal synthetic reproducer, affected version/commit, expected behavior, and impact. Remove unpublished research, personal data, access tokens, and third-party copyrighted content.
