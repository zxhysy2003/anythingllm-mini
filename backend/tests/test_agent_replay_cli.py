import json
from pathlib import Path

from app.maintenance import export_agent_replay, replay_agent_fixture
from app.db.session import create_db_engine
from app.services.agent_replay_service import AgentReplayReport, AgentReplayService

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "agent_replay"


def test_export_cli_accepts_explicit_output_and_reports_success(
    tmp_path, monkeypatch, capsys
):
    fixture = AgentReplayService().load_fixture(
        FIXTURE_DIR / "react_calculator_success.json"
    )
    events = []

    def fake_export(invocation_id, output_path, *, overwrite):
        events.append((invocation_id, output_path, overwrite))
        return fixture

    monkeypatch.setattr(export_agent_replay, "export_fixture", fake_export)
    output_path = tmp_path / "fixture.json"

    exit_code = export_agent_replay.main(
        ["invocation-id", "--output", str(output_path)]
    )

    assert exit_code == 0
    assert events == [("invocation-id", output_path, False)]
    assert json.loads(capsys.readouterr().out) == {
        "output": str(output_path),
        "schema_version": 1,
    }


def test_export_cli_returns_operational_error(tmp_path, monkeypatch, capsys):
    def fail_export(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("cannot export")

    monkeypatch.setattr(export_agent_replay, "export_fixture", fail_export)

    exit_code = export_agent_replay.main(
        ["invocation-id", "--output", str(tmp_path / "fixture.json")]
    )

    assert exit_code == 2
    assert json.loads(capsys.readouterr().out) == {"error": "cannot export"}


def test_export_cli_rejects_missing_sqlite_database_without_creating_it(
    tmp_path, monkeypatch, capsys
):
    database_path = tmp_path / "missing.db"
    monkeypatch.setattr(
        export_agent_replay,
        "engine",
        create_db_engine(f"sqlite:///{database_path}"),
    )

    exit_code = export_agent_replay.main(
        ["invocation-id", "--output", str(tmp_path / "fixture.json")]
    )

    assert exit_code == 2
    assert database_path.exists() is False
    assert json.loads(capsys.readouterr().out) == {
        "error": f"SQLite database file does not exist: {database_path}"
    }


def test_export_cli_forwards_overwrite_flag(tmp_path, monkeypatch):
    fixture = AgentReplayService().load_fixture(
        FIXTURE_DIR / "react_calculator_success.json"
    )
    overwrites = []

    def fake_export(invocation_id, output_path, *, overwrite):
        del invocation_id, output_path
        overwrites.append(overwrite)
        return fixture

    monkeypatch.setattr(export_agent_replay, "export_fixture", fake_export)

    exit_code = export_agent_replay.main(
        ["invocation-id", "--output", str(tmp_path / "fixture.json"), "--overwrite"]
    )

    assert exit_code == 0
    assert overwrites == [True]


def test_replay_cli_uses_report_exit_codes(monkeypatch, capsys):
    failed_report = AgentReplayReport(
        agent_mode="react_text",
        passed=False,
        checks=[],
    )
    monkeypatch.setattr(
        replay_agent_fixture,
        "replay_fixture_file",
        lambda fixture_path: failed_report,
    )

    exit_code = replay_agent_fixture.main(["fixture.json"])

    assert exit_code == 1
    assert json.loads(capsys.readouterr().out)["passed"] is False


def test_replay_cli_returns_operational_error(monkeypatch, capsys):
    def fail_replay(fixture_path):
        del fixture_path
        raise RuntimeError("cannot replay")

    monkeypatch.setattr(replay_agent_fixture, "replay_fixture_file", fail_replay)

    exit_code = replay_agent_fixture.main(["fixture.json"])

    assert exit_code == 2
    assert json.loads(capsys.readouterr().out) == {"error": "cannot replay"}
