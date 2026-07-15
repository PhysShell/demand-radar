"""CLI-level behavior -- spec section 9: non-zero exit on error, no hidden
partial output, idempotent init/ingest, clear errors for missing runs.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

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
    silently feed it untrusted evidence text."""
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
    assert "codex_unverified_for_untrusted_content" in result.stdout


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
