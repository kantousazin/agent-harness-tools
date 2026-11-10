#!/usr/bin/env python3
"""Agent harness coordinator checks.

Implements:
  T1 receipt : assemble tasks/<id>/result.json from existing artifacts only.
               Unknown values stay null and are listed in "missing" --
               this tool never invents gate results, rounds or usage.
  T2 audit   : check a changed-file list against the spec's Owned paths
               (and Forbidden patterns). Exit 1 on any violation.
  T4 lint    : pre-dispatch check that spec.md has its required sections
               and (T3) that every acceptance criterion names its evidence
               (a line containing 検証 within the Acceptance section).
               Also enforces the mandatory Jev record (2026-11-10 user
               decision): status.json must carry a filled "jev" field, or
               an explicit {"unavailable": true, "reason": ...} fallback
               record. No jev record = LINT NG = do not dispatch.

Usage:
  harness_check.py lint     <task-dir>
  harness_check.py leak     <file.cs|dir> [...]
  harness_check.py audit    <task-dir> --files a.cs,b.cs [--files-from list.txt]
  harness_check.py receipt  <task-dir> [--files a.cs,b.cs]
                            [--gate-cmd "..."] [--gate-exit 0]
                            [--gate-passed N] [--gate-failed N]
                            [--review-rounds N] [--fix-rounds N]
                            [--out result.json]

Standard library only. Exit codes: 0 ok, 1 violations found, 2 usage/io error.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

REQUIRED_SECTIONS = [
    "Objective", "Context", "Non-goals", "Owned paths",
    "Forbidden paths", "Acceptance criteria", "Gates", "Budget",
]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def sections(md: str) -> dict[str, str]:
    """Split markdown by '## ' headings -> {heading: body}."""
    out: dict[str, str] = {}
    cur, buf = None, []
    for line in md.splitlines():
        m = re.match(r"^##\s+(.*)$", line)
        if m:
            if cur is not None:
                out[cur] = "\n".join(buf)
            cur, buf = m.group(1).strip(), []
        elif cur is not None:
            buf.append(line)
    if cur is not None:
        out[cur] = "\n".join(buf)
    return out


def find_section(secs: dict[str, str], needle: str) -> str | None:
    for k, v in secs.items():
        if needle.lower() in k.lower():
            return v
    return None


def bullet_paths(body: str) -> list[str]:
    """Extract path-like tokens from bullet lines (backticked or plain)."""
    paths = []
    for line in body.splitlines():
        if not re.match(r"^\s*[-*]\s+", line):
            continue
        ticks = re.findall(r"`([^`]+)`", line)
        cands = ticks if ticks else [re.sub(r"^\s*[-*]\s+", "", line)]
        for c in cands:
            c = c.strip().rstrip("、。")
            if "/" in c or "*" in c or re.search(r"\.\w+$", c):
                paths.append(c)
    return paths


def load_task(task_dir: Path):
    spec_p = task_dir / "spec.md"
    if not spec_p.exists():
        print(f"error: {spec_p} not found", file=sys.stderr)
        sys.exit(2)
    status = {}
    st_p = task_dir / "status.json"
    if st_p.exists():
        try:
            status = json.loads(read(st_p))
        except json.JSONDecodeError:
            print(f"warning: {st_p} is not valid JSON, ignoring", file=sys.stderr)
    return read(spec_p), sections(read(spec_p)), status


def contract_sections(task_dir: Path) -> dict[str, str]:
    """Merged sections of contract*.md (contract.md preferred).

    Trial finding (2026-11-10): most tasks keep Owned paths in contract.md,
    not spec.md (b2 was the exception), so audit must read both.
    """
    merged: dict[str, str] = {}
    files = sorted(task_dir.glob("contract*.md"),
                   key=lambda p: (p.name != "contract.md", p.name))
    for f in files:
        for k, v in sections(read(f)).items():
            merged.setdefault(k, v)
    return merged


JEV_TIERS = {"fast", "balanced", "strong", "long"}


def jev_problems(status: dict) -> list[str]:
    """Mandatory Jev record check (JEV-ROUTING.md, 2026-11-10 enforcement)."""
    if not status:
        return ["jev: status.json missing or invalid (jev record is mandatory)"]
    jev = status.get("jev")
    if not isinstance(jev, dict):
        return ["jev: no jev field in status.json (mandatory since 2026-11-10)"]
    if jev.get("unavailable") is True:
        if not str(jev.get("reason") or "").strip():
            return ["jev: unavailable fallback record needs a reason"]
        return []
    problems = []
    if jev.get("tier") not in JEV_TIERS:
        problems.append(f"jev: tier missing/invalid ({jev.get('tier')!r})")
    for key in ("tier_confidence", "high_risk", "review_need"):
        if not isinstance(jev.get(key), (int, float)):
            problems.append(f"jev: {key} missing/not numeric")
    if not str(jev.get("model") or "").strip():
        problems.append("jev: model missing")
    if not str(jev.get("at") or "").strip():
        problems.append("jev: at (timestamp) missing")
    return problems


# ---------------------------------------------------------------- lint (T4+T3)
def cmd_lint(task_dir: Path) -> int:
    _, secs, status = load_task(task_dir)
    problems = []
    for req in REQUIRED_SECTIONS:
        body = find_section(secs, req)
        if body is None:
            problems.append(f"missing section: {req}")
        elif not body.strip():
            problems.append(f"empty section: {req}")
    acc = find_section(secs, "Acceptance criteria")
    if acc:
        items = [ln for ln in acc.splitlines()
                 if re.match(r"^\s*\d+[.)]\s+", ln)]
        no_ev = [ln.strip()[:60] for ln in items if "検証" not in ln]
        if items and no_ev:
            problems.append(
                f"acceptance criteria without evidence (検証): {len(no_ev)}/{len(items)}")
            for ln in no_ev:
                problems.append(f"  - {ln}")
        elif not items:
            problems.append("acceptance criteria: no numbered items found")
    problems += jev_problems(status)
    if problems:
        print("LINT NG")
        for p in problems:
            print(p)
        return 1
    print("LINT OK")
    return 0


# ---------------------------------------------------------------- audit (T2)
def path_allowed(path: str, owned: list[str]) -> bool:
    for pat in owned:
        pat = pat.strip()
        if not pat:
            continue
        if fnmatch.fnmatch(path, pat):
            return True
        if pat.endswith("/") and path.startswith(pat):
            return True
        if "*" not in pat and path == pat:
            return True
        if "*" not in pat and path.startswith(pat.rstrip("/") + "/"):
            return True
    # a .meta companion of an allowed file is allowed
    if path.endswith(".meta"):
        return path_allowed(path[: -len(".meta")], owned)
    return False


def forbidden_hit(path: str, forbidden_body: str) -> str | None:
    globs = re.findall(r"`([^`]*\*[^`]*)`", forbidden_body)
    for g in globs:
        if fnmatch.fnmatch(Path(path).name, g) or fnmatch.fnmatch(path, g):
            return g
    for prefix in re.findall(r"`([^`]+/)`", forbidden_body):
        if path.startswith(prefix):
            return prefix
    return None


def cmd_audit(task_dir: Path, files: list[str]) -> int:
    _, secs, _ = load_task(task_dir)
    csecs = contract_sections(task_dir)
    owned = bullet_paths(find_section(secs, "Owned paths") or "")
    owned += bullet_paths(find_section(csecs, "Owned paths") or "")
    owned = list(dict.fromkeys(owned))
    forb_body = find_section(secs, "Forbidden paths") or ""
    if not owned:
        print("error: no Owned paths could be parsed from spec.md", file=sys.stderr)
        return 2
    bad = []
    for f in files:
        # Exception seen in real contracts (b2 H1): the .meta of a NEW owned
        # file is required, while changing EXISTING .meta is forbidden. From a
        # path list alone the two are indistinguishable, so a .meta whose base
        # file is itself in Owned paths is treated as the allowed companion.
        if f.endswith(".meta") and path_allowed(f, owned):
            continue
        hit = forbidden_hit(f, forb_body)
        if hit:
            bad.append(f"{f}  (forbidden: {hit})")
        elif not path_allowed(f, owned):
            bad.append(f"{f}  (outside Owned paths)")
    if bad:
        print("AUDIT NG")
        for b in bad:
            print(b)
        return 1
    print(f"AUDIT OK ({len(files)} files, {len(owned)} owned patterns)")
    return 0


# ---------------------------------------------------------------- leak (bug-reduction trial 2026-11-10)
# Warning only, never a gate: acquiring code whose cleanup lives in the
# caller is legitimate. A warning means "the implementer states where the
# cleanup happens", not an automatic reject. See
# bug-reduction-checklist-v0.1.md section D.
LEAK_RULES = [
    ("RenderTexture acquire without ReleaseTemporary/Release in file",
     r"RenderTexture\.GetTemporary|new\s+RenderTexture\s*\(",
     r"ReleaseTemporary|\.Release\s*\("),
    ("Texture2D creation without Destroy in file",
     r"new\s+Texture2D\s*\(",
     r"(?:Object\.)?Destroy\s*\("),
    ("Material creation without Destroy in file",
     r"new\s+Material\s*\(",
     r"(?:Object\.)?Destroy\s*\("),
    ("event subscribe without unsubscribe in file",
     r"[\w\.]*(?:Event|Changed|Click|Callback|Completed|Performed|Cancelled|Canceled)\w*\s*\+=",
     r"-="),
    ("Override assignment without Reset/Restore/Clear in file",
     r"\b\w*Override\w*\s*=[^=]",
     r"Reset|Restore|Clear\w*Override|Override\w*\s*=\s*(?:null|default)"),
]


def cmd_leak(paths: list[str]) -> int:
    files: list[Path] = []
    for raw in paths:
        p = Path(raw).expanduser()
        if p.is_dir():
            files += sorted(p.rglob("*.cs"))
        elif p.is_file():
            files.append(p)
        else:
            print(f"error: {p} not found", file=sys.stderr)
            return 2
    if not files:
        print("error: no .cs files found", file=sys.stderr)
        return 2
    warnings = []
    for f in files:
        text = read(f)
        for label, create_pat, cleanup_pat in LEAK_RULES:
            hits = list(re.finditer(create_pat, text))
            if hits and not re.search(cleanup_pat, text):
                lines = sorted({text.count("\n", 0, h.start()) + 1 for h in hits})
                warnings.append(
                    f"{f}:{','.join(map(str, lines))}  ({label})")
    if warnings:
        print(f"LEAK WARN ({len(warnings)} candidate(s), "
              "implementer must state where cleanup happens)")
        for w in warnings:
            print(w)
        return 1
    print(f"LEAK OK ({len(files)} files)")
    return 0


# ---------------------------------------------------------------- receipt (T1)
def cmd_receipt(task_dir: Path, args) -> int:
    _, _, status = load_task(task_dir)
    files = [f.strip() for f in args.files.split(",") if f.strip()] if args.files else []
    receipt = {
        "task_id": status.get("task_id") or status.get("task") or task_dir.name,
        "title": status.get("title"),
        "status": status.get("status") or status.get("state"),
        "issue": status.get("github_issue", status.get("issue")),
        "pr": args.pr if getattr(args, "pr", None) else status.get("pr"),
        "merge_commit": (args.merge_commit if getattr(args, "merge_commit", None)
                         else status.get("merge_commit")),
        "branch": status.get("branch"),
        "implementer": status.get("implementer"),
        "reviewer": status.get("reviewer"),
        "jev": status.get("jev"),
        "files_changed": files,
        "gate0": None,
        "rounds": {"review": args.review_rounds, "fix": args.fix_rounds},
        "usage": None,
        "sources": ["status.json", "spec.md"] + (["cli:--files"] if files else []),
        "missing": [],
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generator": "harness_check.py receipt v1",
    }
    if args.gate_cmd is not None:
        receipt["gate0"] = {
            "command": args.gate_cmd,
            "exit_code": args.gate_exit,
            "passed": args.gate_passed,
            "failed": args.gate_failed,
        }
        receipt["sources"].append("cli:--gate-*")
    for key in ("title", "issue", "pr", "merge_commit", "branch",
                "implementer", "reviewer", "jev"):
        if receipt[key] is None:
            receipt["missing"].append(key)
    if not files:
        receipt["missing"].append("files_changed")
    if receipt["gate0"] is None:
        receipt["missing"].append("gate0")
    if args.review_rounds is None:
        receipt["missing"].append("rounds.review")
    if args.fix_rounds is None:
        receipt["missing"].append("rounds.fix")
    receipt["missing"].append("usage")  # until a usage source is wired in
    out = Path(args.out) if args.out else task_dir / "result.json"
    out.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    print(f"RECEIPT WROTE {out}")
    if receipt["missing"]:
        print("missing (left null, not invented): " + ", ".join(receipt["missing"]))
    return 0


def main() -> int:
    # Report lines can contain non-ASCII markers; on consoles with a
    # legacy codepage (e.g. Windows cp1252) printing them would raise
    # UnicodeEncodeError halfway through the report. Force UTF-8 with
    # replacement so the report always completes.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("lint", "audit", "receipt"):
        p = sub.add_parser(name)
        p.add_argument("task_dir")
        if name in ("audit", "receipt"):
            p.add_argument("--files", default="")
            p.add_argument("--files-from", default="")
        if name == "receipt":
            p.add_argument("--gate-cmd")
            p.add_argument("--gate-exit", type=int)
            p.add_argument("--gate-passed", type=int)
            p.add_argument("--gate-failed", type=int)
            p.add_argument("--review-rounds", type=int)
            p.add_argument("--fix-rounds", type=int)
            p.add_argument("--pr", type=int)
            p.add_argument("--merge-commit", default="")
            p.add_argument("--out", default="")
    p_leak = sub.add_parser("leak")
    p_leak.add_argument("paths", nargs="+")
    args = ap.parse_args()
    if args.cmd == "leak":
        return cmd_leak(args.paths)
    task_dir = Path(args.task_dir).expanduser()
    if args.cmd == "lint":
        return cmd_lint(task_dir)
    files = [f.strip() for f in args.files.split(",") if f.strip()]
    if getattr(args, "files_from", ""):
        files += [ln.strip() for ln in read(Path(args.files_from)).splitlines()
                  if ln.strip()]
    if args.cmd == "audit":
        if not files:
            print("error: --files or --files-from is required", file=sys.stderr)
            return 2
        return cmd_audit(task_dir, files)
    if args.files:
        pass
    elif files:
        args.files = ",".join(files)
    return cmd_receipt(task_dir, args)


if __name__ == "__main__":
    sys.exit(main())
