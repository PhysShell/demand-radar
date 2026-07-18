"""CLI-level behavior -- spec section 9: non-zero exit on error, no hidden
partial output, idempotent init/ingest, clear errors for missing runs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from demand_radar.agents import o7_invoke
from demand_radar.cli import app

runner = CliRunner()
REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "fixtures" / "mixed-demand-signals.jsonl"


def test_init_creates_database(tmp_path: Path) -> None:
    db = tmp_path / "d.db"
    result = runner.invoke(app, ["init", "--db", str(db)])
    assert result.exit_code == 0
    assert db.is_file()


def test_init_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "d.db"
    first = runner.invoke(app, ["init", "--db", str(db)])
    second = runner.invoke(app, ["init", "--db", str(db)])
    assert first.exit_code == 0
    assert second.exit_code == 0


def test_ingest_reports_errors_and_exits_nonzero(tmp_path: Path) -> None:
    db = tmp_path / "d.db"
    runner.invoke(app, ["init", "--db", str(db)])
    result = runner.invoke(
        app, ["ingest", "--product", "own-audit", "--input", str(FIXTURE), "--db", str(db)]
    )
    assert result.exit_code == 1
    assert "errors=2" in result.stdout


def test_ingest_against_uninitialized_db_fails_clearly(tmp_path: Path) -> None:
    db = tmp_path / "never-created.db"
    result = runner.invoke(
        app, ["ingest", "--product", "own-audit", "--input", str(FIXTURE), "--db", str(db)]
    )
    assert result.exit_code == 1
    assert "error" in result.stdout.lower()


def test_ingest_unknown_product_fails_clearly(tmp_path: Path) -> None:
    db = tmp_path / "d.db"
    runner.invoke(app, ["init", "--db", str(db)])
    result = runner.invoke(
        app, ["ingest", "--product", "not-a-real-product", "--input", str(FIXTURE), "--db", str(db)]
    )
    assert result.exit_code == 1


def test_report_missing_run_fails_clearly(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["report", "--run", "no-such-run", "--runs-dir", str(tmp_path / "runs")]
    )
    assert result.exit_code == 1
    assert "not found" in result.stdout.lower()


def test_verify_missing_run_fails_clearly(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["verify", "--run", "no-such-run", "--runs-dir", str(tmp_path / "runs")]
    )
    assert result.exit_code == 1


def test_inspect_evidence_missing_id_fails_clearly(tmp_path: Path) -> None:
    db = tmp_path / "d.db"
    runner.invoke(app, ["init", "--db", str(db)])
    result = runner.invoke(app, ["inspect", "evidence", "ev_doesnotexist", "--db", str(db)])
    assert result.exit_code == 1


def test_run_refuses_same_provider_for_analyst_and_critic(tmp_path: Path) -> None:
    db = tmp_path / "d.db"
    runner.invoke(app, ["init", "--db", str(db)])
    runner.invoke(
        app, ["ingest", "--product", "own-audit", "--input", str(FIXTURE), "--db", str(db)]
    )
    result = runner.invoke(
        app,
        [
            "run",
            "--product",
            "own-audit",
            "--analyst",
            "claude",
            "--critic",
            "claude",
            "--db",
            str(db),
            "--runs-dir",
            str(tmp_path / "runs"),
        ],
    )
    assert result.exit_code == 2
    assert "single_provider_unreviewed" in result.stdout


def test_run_refuses_codex_for_untrusted_zone_2(tmp_path: Path) -> None:
    """Codex's closed-world guarantee is unverified against a live install
    (docs/trust-boundaries.md) -- --analyst/--critic must refuse it, not
    silently feed it untrusted evidence text. The refusal is keyed off
    O7InvokeRunner(engine="codex").verified_profiles() lacking
    read-only-data, not a hardcoded engine-name check."""
    db = tmp_path / "d.db"
    runner.invoke(app, ["init", "--db", str(db)])
    runner.invoke(
        app, ["ingest", "--product", "own-audit", "--input", str(FIXTURE), "--db", str(db)]
    )
    result = runner.invoke(
        app,
        [
            "run",
            "--product",
            "own-audit",
            "--analyst",
            "claude",
            "--critic",
            "codex",
            "--db",
            str(db),
            "--runs-dir",
            str(tmp_path / "runs"),
        ],
    )
    assert result.exit_code == 2
    assert "unverified_capability_profile" in result.stdout
    assert "critic engine 'codex'" in result.stdout


def test_run_with_claude_analyst_clears_the_capability_profile_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """claude's verified_profiles() includes read-only-data, so this run
    must NOT be refused by _refuse_unverified_profiles -- unlike the codex
    case above, it proceeds into the real pipeline and only then fails, for
    an unrelated reason. `subprocess.run` is monkeypatched to always raise
    FileNotFoundError (matching this offline test environment, where `o7`
    genuinely is not on PATH, but pinned explicitly rather than relying on
    that fact so the test can't start hitting a real `o7`/claude install if
    one is ever present where this suite runs -- the hard constraint here is
    "no real engine calls in tests", not "no o7 binary in this sandbox").
    Asserting on the resulting BLOCKED_NOT_INSTALLED downstream failure (not
    an early exit) is what proves the capability-profile gate was actually
    cleared, not skipped by accident."""

    def fake_run(argv: list[str], **kwargs: Any) -> None:
        raise FileNotFoundError("o7 not found (test double)")

    monkeypatch.setattr(o7_invoke.subprocess, "run", fake_run)

    db = tmp_path / "d.db"
    runner.invoke(app, ["init", "--db", str(db)])
    runner.invoke(
        app, ["ingest", "--product", "own-audit", "--input", str(FIXTURE), "--db", str(db)]
    )
    result = runner.invoke(
        app,
        [
            "run",
            "--product",
            "own-audit",
            "--analyst",
            "claude",
            "--critic",
            "human",
            "--db",
            str(db),
            "--runs-dir",
            str(tmp_path / "runs"),
        ],
    )
    assert "unverified_capability_profile" not in result.stdout
    assert "run_id=" in result.stdout
    assert "BLOCKED_NOT_INSTALLED" in result.stdout


def test_run_with_fake_runner_completes_and_writes_artifacts(tmp_path: Path) -> None:
    db = tmp_path / "d.db"
    runs_dir = tmp_path / "runs"
    runner.invoke(app, ["init", "--db", str(db)])
    runner.invoke(
        app, ["ingest", "--product", "own-audit", "--input", str(FIXTURE), "--db", str(db)]
    )
    result = runner.invoke(
        app,
        [
            "run",
            "--product",
            "own-audit",
            "--analyst",
            "fake",
            "--critic",
            "fake",
            "--db",
            str(db),
            "--runs-dir",
            str(runs_dir),
        ],
    )
    assert "run_id=" in result.stdout
    assert (runs_dir / "own-audit").is_dir()
    run_dirs = list((runs_dir / "own-audit").iterdir())
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]
    assert (run_dir / "task.yaml").is_file()
    assert (run_dir / "state.json").is_file()
    assert (run_dir / "evidence" / "accepted.jsonl").is_file()
    assert (run_dir / "evidence" / "duplicates.jsonl").is_file()
    assert (run_dir / "outputs" / "report.md").is_file()
    assert (run_dir / "verification.json").is_file()
