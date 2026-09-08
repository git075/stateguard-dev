"""Tests for the StateGuard CLI."""

import json
import pytest
from click.testing import CliRunner
from stateguard.cli import cli
from stateguard.store import AuditStore, AuditEvent, EventType

@pytest.fixture
def runner():
    return CliRunner()

@pytest.fixture
def seeded_db(tmp_path):
    db_path = tmp_path / "audit.db"
    store = AuditStore(str(db_path))
    # Synchronously setup for test
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    from stateguard.store import _SCHEMA
    conn.executescript(_SCHEMA)
    
    # Insert some dummy data manually to avoid async issues in a sync fixture
    event1 = AuditEvent(EventType.SAGA_START, saga_id="saga-1")
    event2 = AuditEvent(EventType.COMMIT, saga_id="saga-1", step_name="step-1", state_before={"a": 1}, state_after={"a": 2})
    event3 = AuditEvent(EventType.SAGA_END, saga_id="saga-1", message="completed")
    
    from stateguard.store import _INSERT
    conn.execute(_INSERT, event1.to_row())
    conn.execute(_INSERT, event2.to_row())
    conn.execute(_INSERT, event3.to_row())
    conn.commit()
    conn.close()
    
    return str(db_path)


def test_cli_log_command(runner, seeded_db):
    result = runner.invoke(cli, ["log", "--db", seeded_db])
    assert result.exit_code == 0
    assert "saga-1" in result.output
    assert "step-1" in result.output


def test_cli_log_command_no_db(runner, tmp_path):
    bad_path = str(tmp_path / "nope.db")
    result = runner.invoke(cli, ["log", "--db", bad_path])
    assert result.exit_code == 1
    assert "No audit database found" in result.output


def test_cli_sagas_command(runner, seeded_db):
    result = runner.invoke(cli, ["sagas", "--db", seeded_db])
    assert result.exit_code == 0
    assert "saga-1" in result.output
    assert "done" in result.output
    assert "3" in result.output  # total events


def test_cli_inspect_command(runner, seeded_db):
    result = runner.invoke(cli, ["inspect", "saga-1", "--db", seeded_db])
    assert result.exit_code == 0
    assert "Saga: saga-1" in result.output
    assert "step-1" in result.output


def test_cli_inspect_command_not_found(runner, seeded_db):
    result = runner.invoke(cli, ["inspect", "unknown-saga", "--db", seeded_db])
    assert result.exit_code == 0
    assert "No events found for saga 'unknown-saga'" in result.output


def test_cli_export_command(runner, seeded_db, tmp_path):
    # Export to stdout
    result = runner.invoke(cli, ["export", "--db", seeded_db])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) == 3
    assert data[0]["event_type"] == "saga_start" # read_saga returns ASC order
    assert data[0]["saga_id"] == "saga-1"

    # Export to file
    out_file = tmp_path / "out.json"
    result = runner.invoke(cli, ["export", "--db", seeded_db, "-o", str(out_file)])
    assert result.exit_code == 0
    assert out_file.exists()
    
    file_data = json.loads(out_file.read_text())
    assert len(file_data) == 3
