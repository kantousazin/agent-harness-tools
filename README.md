# agent-harness-tools

Small, standard-library-only command line checks for a file-based multi-agent
task harness. A coordinator dispatches work to implementer agents with a task
packet (a spec file plus a status record); these subcommands check the packet
before dispatch, audit the delivered file list against the spec, assemble a
result receipt from existing artifacts, and flag likely resource-cleanup
omissions in C# code.

## Subcommands

- `lint <task-dir>` — pre-dispatch check: the spec has its required sections,
  every acceptance criterion names its evidence, and the status record carries
  a routing-decision record (a filled `jev` object, or an explicit
  `{"unavailable": true, "reason": ...}` fallback). No record = do not dispatch.
- `audit <task-dir> --files a.cs,b.cs [--files-from list.txt]` — check a
  changed-file list against the spec's Owned paths and Forbidden patterns.
- `receipt <task-dir> [--files ...] [--gate-* ...]` — assemble `result.json`
  from existing artifacts only. Unknown values stay null and are listed in
  `missing`; the tool never invents gate results, rounds, or usage.
- `leak <file.cs|dir> [...]` — warn about C# resource creation sites
  (RenderTexture / Texture2D / Material / event subscriptions / overrides)
  whose cleanup is not visible in the same file. A warning is a trigger to
  document where the cleanup lives, not an automatic failure.

Exit codes: 0 ok, 1 violations found, 2 usage / IO error.

## Requirements

Python 3, standard library only.

## License

MIT
