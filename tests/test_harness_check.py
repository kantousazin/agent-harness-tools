"""End-to-end tests for harness_check.py, driving the CLI as users do.

All fixtures are generic: a made-up task packet (spec.md + status.json)
and small C# snippets. Nothing here is tied to a particular project.
"""
import json
import subprocess
import sys
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "harness_check.py"

# The lint check looks for an evidence marker on each numbered acceptance
# line. The marker is the tool's own convention: the Japanese word for
# "verification", written here as an escape so this file stays ASCII-only.
EV = "検証"

VALID_SPEC = """# Example task

## Objective
Do the example thing.

## Context
Some background.

## Non-goals
Everything else.

## Owned paths
- `src/example/`
- `docs/example.md`

## Forbidden paths
- `*.secret`
- `vendor/`

## Acceptance criteria
1. The thing works EV_MARKER: run the example test
2. Nothing outside Owned paths changes EV_MARKER: audit output

## Gates
Gate zero must pass.

## Budget
One session.
""".replace("EV_MARKER", EV)

FILLED_JEV = {
    "tier": "balanced",
    "tier_confidence": 0.8,
    "high_risk": 0.2,
    "review_need": 1.5,
    "model": "example-model",
    "at": "2026-01-01T00:00:00Z",
}


def run_cli(*args):
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        capture_output=True, text=True, timeout=60,
    )


def make_task(tmp_path, spec=VALID_SPEC, status=None):
    task = tmp_path / "task"
    task.mkdir()
    (task / "spec.md").write_text(spec, encoding="utf-8")
    if status is not None:
        (task / "status.json").write_text(json.dumps(status), encoding="utf-8")
    return task


# ---------------------------------------------------------------- lint

def test_lint_ok_with_filled_jev(tmp_path):
    task = make_task(tmp_path, status={"jev": FILLED_JEV})
    r = run_cli("lint", str(task))
    assert r.returncode == 0
    assert "LINT OK" in r.stdout


def test_lint_ok_with_unavailable_jev_reason(tmp_path):
    task = make_task(tmp_path, status={"jev": {"unavailable": True,
                                               "reason": "routing service down"}})
    r = run_cli("lint", str(task))
    assert r.returncode == 0


def test_lint_ng_without_jev_record(tmp_path):
    task = make_task(tmp_path, status={})
    r = run_cli("lint", str(task))
    assert r.returncode == 1
    assert "jev" in r.stdout


def test_lint_ng_unavailable_jev_without_reason(tmp_path):
    task = make_task(tmp_path, status={"jev": {"unavailable": True}})
    r = run_cli("lint", str(task))
    assert r.returncode == 1


def test_lint_ng_missing_section(tmp_path):
    spec = VALID_SPEC.replace("## Budget", "## Notes")
    task = make_task(tmp_path, spec=spec, status={"jev": FILLED_JEV})
    r = run_cli("lint", str(task))
    assert r.returncode == 1
    assert "missing section: Budget" in r.stdout


def test_lint_ng_acceptance_without_evidence(tmp_path):
    spec = VALID_SPEC.replace(
        "2. Nothing outside Owned paths changes " + EV + ": audit output",
        "2. Nothing outside Owned paths changes")
    task = make_task(tmp_path, spec=spec, status={"jev": FILLED_JEV})
    r = run_cli("lint", str(task))
    assert r.returncode == 1
    assert "without evidence" in r.stdout


# ---------------------------------------------------------------- audit

def test_audit_ok_owned_file(tmp_path):
    task = make_task(tmp_path, status={"jev": FILLED_JEV})
    r = run_cli("audit", str(task), "--files", "src/example/a.py,docs/example.md")
    assert r.returncode == 0
    assert "AUDIT OK" in r.stdout


def test_audit_ok_meta_companion_of_owned(tmp_path):
    task = make_task(tmp_path, status={"jev": FILLED_JEV})
    r = run_cli("audit", str(task), "--files", "docs/example.md.meta")
    assert r.returncode == 0


def test_audit_ng_outside_owned(tmp_path):
    task = make_task(tmp_path, status={"jev": FILLED_JEV})
    r = run_cli("audit", str(task), "--files", "src/other/b.py")
    assert r.returncode == 1
    assert "outside Owned paths" in r.stdout


def test_audit_ng_forbidden(tmp_path):
    task = make_task(tmp_path, status={"jev": FILLED_JEV})
    r = run_cli("audit", str(task), "--files", "src/example/key.secret")
    assert r.returncode == 1
    assert "forbidden" in r.stdout


# ---------------------------------------------------------------- leak

def test_leak_warns_rendertexture_without_release(tmp_path):
    cs = tmp_path / "Sample.cs"
    cs.write_text(
        "class Sample { void F() { var rt = RenderTexture.GetTemporary(64, 64); } }",
        encoding="utf-8")
    r = run_cli("leak", str(cs))
    assert r.returncode == 1
    assert "LEAK WARN" in r.stdout


def test_leak_ok_rendertexture_with_release(tmp_path):
    cs = tmp_path / "Sample.cs"
    cs.write_text(
        "class Sample { void F() { var rt = RenderTexture.GetTemporary(64, 64);"
        " RenderTexture.ReleaseTemporary(rt); } }",
        encoding="utf-8")
    r = run_cli("leak", str(cs))
    assert r.returncode == 0
    assert "LEAK OK" in r.stdout


def test_leak_warns_event_subscribe_without_unsubscribe(tmp_path):
    cs = tmp_path / "Sample.cs"
    cs.write_text(
        "class Sample { void F() { source.Changed += OnChanged; } }",
        encoding="utf-8")
    r = run_cli("leak", str(cs))
    assert r.returncode == 1
    assert "event subscribe" in r.stdout


def test_leak_missing_path_is_usage_error(tmp_path):
    r = run_cli("leak", str(tmp_path / "nope.cs"))
    assert r.returncode == 2


# ---------------------------------------------------------------- receipt

def test_receipt_unknowns_stay_null_and_are_listed(tmp_path):
    task = make_task(tmp_path, status={"task_id": "example-1",
                                       "jev": FILLED_JEV})
    r = run_cli("receipt", str(task))
    assert r.returncode == 0
    receipt = json.loads((task / "result.json").read_text(encoding="utf-8"))
    assert receipt["task_id"] == "example-1"
    assert receipt["pr"] is None
    assert "pr" in receipt["missing"]
    assert receipt["gate0"] is None
    assert "gate0" in receipt["missing"]
    assert "usage" in receipt["missing"]


def test_receipt_gate_block_only_from_cli_flags(tmp_path):
    task = make_task(tmp_path, status={"task_id": "example-2",
                                       "jev": FILLED_JEV})
    r = run_cli("receipt", str(task), "--files", "src/example/a.py",
                "--gate-cmd", "run gate", "--gate-exit", "0",
                "--gate-passed", "10", "--gate-failed", "0")
    assert r.returncode == 0
    receipt = json.loads((task / "result.json").read_text(encoding="utf-8"))
    assert receipt["gate0"] == {"command": "run gate", "exit_code": 0,
                                "passed": 10, "failed": 0}
    assert receipt["files_changed"] == ["src/example/a.py"]
    assert "gate0" not in receipt["missing"]
