---
layout: default
title: agent-harness-tools
---

# agent-harness-tools

Small, standard-library-only command line checks for a file-based
multi-agent task harness. A coordinator dispatches work to implementer
agents with a task packet (a spec file plus a status record); these
subcommands check the packet before dispatch, audit the delivered file
list against the spec, assemble a result receipt from existing
artifacts, and flag likely resource-cleanup omissions in C# code.

## Usage

```
harness_check.py lint     <task-dir>
harness_check.py audit    <task-dir> --files a.cs,b.cs [--files-from list.txt]
harness_check.py receipt  <task-dir> [--files a.cs,b.cs] [--gate-* ...]
harness_check.py leak     <file.cs|dir> [...]
```

- **lint** — pre-dispatch check: the spec has its required sections,
  every acceptance criterion names its evidence, and the status record
  carries a routing-decision record (a filled `jev` object, or an
  explicit `{"unavailable": true, "reason": ...}` fallback).
  No record = do not dispatch.
- **audit** — check a changed-file list against the spec's Owned paths
  and Forbidden patterns. Exit 1 on any violation.
- **receipt** — assemble `result.json` from existing artifacts only.
  Unknown values stay null and are listed in `missing`; the tool never
  invents gate results, rounds, or usage.
- **leak** — warn about C# resource creation sites (RenderTexture /
  Texture2D / Material / event subscriptions / overrides) whose cleanup
  is not visible in the same file. A warning is a trigger to document
  where the cleanup lives, not an automatic failure.

Exit codes: 0 ok, 1 violations found, 2 usage / IO error.

## Development

- Tests: `python -m pytest` (fixtures are generic example packets).
- Codespaces / dev containers: open the repository in a codespace and
  the environment is ready (Python plus pytest, see `.devcontainer/`).
- CI runs the test suite on Python 3.10–3.13 and lints the workflows
  with actionlint; a weekly scheduled run repeats both as a health
  check.

## License

MIT. Source: the repository root.
