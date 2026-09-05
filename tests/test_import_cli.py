from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from import_excel import (  # noqa: E402
    DEFAULT_EXCEL_FILE,
    ensure_athlete_exists,
    parse_args,
    positive_athlete_id,
    sync_run_log,
    sync_strength_log,
)

class FakeScalarResult:
    def __init__(self, value: int | None) -> None:
        self.value = value

    def scalar_one_or_none(self) -> int | None:
        return self.value


class FakeConnection:
    def __init__(self, result: int | None) -> None:
        self.result = result
        self.statement = ""
        self.parameters: dict[str, int] = {}

    def execute(
        self,
        statement: object,
        parameters: dict[str, int],
    ) -> FakeScalarResult:
        self.statement = str(statement)
        self.parameters = parameters
        return FakeScalarResult(self.result)

class RecordingConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def execute(
        self,
        statement: object,
        parameters: dict[str, object],
    ) -> FakeScalarResult:
        sql = str(statement)
        self.calls.append((sql, parameters.copy()))

        if "SELECT ActivityID" in sql:
            return FakeScalarResult(202)

        if "SELECT StrengthSessionID" in sql:
            return FakeScalarResult(101)

        return FakeScalarResult(None)


def test_positive_athlete_id_accepts_positive_integer() -> None:
    assert positive_athlete_id("7") == 7


@pytest.mark.parametrize("value", ["0", "-1"])
def test_positive_athlete_id_rejects_non_positive_integer(
    value: str,
) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        positive_athlete_id(value)


def test_positive_athlete_id_rejects_non_integer() -> None:
    with pytest.raises(
        argparse.ArgumentTypeError,
        match="số nguyên dương",
    ):
        positive_athlete_id("abc")


def test_parse_args_uses_safe_defaults() -> None:
    args = parse_args([])

    assert args.athlete_id == 1
    assert args.excel_file == DEFAULT_EXCEL_FILE
    assert args.dry_run is False
    assert args.sync_calendar is False


def test_parse_args_accepts_custom_values() -> None:
    excel_file = Path("data/runner_2.xlsx")

    args = parse_args(
        [
            "--athlete-id",
            "2",
            "--excel-file",
            str(excel_file),
            "--dry-run",
            "--sync-calendar",
        ]
    )

    assert args.athlete_id == 2
    assert args.excel_file == excel_file
    assert args.dry_run is True
    assert args.sync_calendar is True


def test_ensure_athlete_exists_accepts_existing_athlete() -> None:
    connection = FakeConnection(result=1)

    ensure_athlete_exists(connection, athlete_id=2)

    assert connection.parameters == {"athlete_id": 2}
    assert "WHERE AthleteID = :athlete_id" in connection.statement


def test_ensure_athlete_exists_rejects_missing_athlete() -> None:
    connection = FakeConnection(result=None)

    with pytest.raises(
        ValueError,
        match="AthleteID=999",
    ):
        ensure_athlete_exists(connection, athlete_id=999)



def test_sync_strength_log_keeps_update_inside_athlete_scope() -> None:
    connection = RecordingConnection()
    frame = pd.DataFrame(
        [
            {
                "Session ID": "STR-001",
                "Date": "2026-09-01",
                "PlannedExerciseCount": 5,
                "CompletedExerciseCount": 4,
                "SessionCompletionRate": 0.8,
                "Completed": True,
            }
        ]
    )

    inserted, updated = sync_strength_log(
        connection,
        frame,
        athlete_id=2,
    )

    assert (inserted, updated) == (0, 1)

    find_sql, find_parameters = connection.calls[0]
    update_sql, update_parameters = connection.calls[1]

    assert "WHERE AthleteID = :athlete_id" in find_sql
    assert (
        "AND ExternalSessionID = :external_session_id"
        in find_sql
    )
    assert find_parameters["athlete_id"] == 2

    assert (
        "WHERE StrengthSessionID = :session_id"
        in update_sql
    )
    assert "AND AthleteID = :athlete_id" in update_sql
    assert update_parameters["athlete_id"] == 2


def test_sync_run_log_keeps_update_inside_athlete_scope() -> None:
    connection = RecordingConnection()
    frame = pd.DataFrame(
        [
            {
                "Run ID": "RUN-TEST-001",
                "Date": pd.Timestamp("2026-09-01").date(),
                "Distance km": 5.0,
                "Duration min": 30.0,
                "Avg HR": 150,
                "Max HR": 170,
                "Elevation m": 20.0,
                "RPE 1-10": 5,
                "Notes": "Test activity",
            }
        ]
    )

    inserted, updated = sync_run_log(
        connection,
        frame,
        athlete_id=2,
    )

    assert (inserted, updated) == (0, 1)

    find_sql, find_parameters = connection.calls[0]
    update_sql, update_parameters = connection.calls[1]

    assert "WHERE AthleteID = :athlete_id" in find_sql
    assert (
        "AND ExternalActivityID = :external_id"
        in find_sql
    )
    assert find_parameters == {
        "athlete_id": 2,
        "external_id": "RUN-TEST-001",
    }

    assert "WHERE ActivityID = :activity_id" in update_sql
    assert "AND AthleteID = :athlete_id" in update_sql
    assert update_parameters["athlete_id"] == 2
    assert update_parameters["activity_id"] == 202

    update_set_clause = (
        update_sql.split("SET", 1)[1]
        .split("WHERE", 1)[0]
    )
    assert "AthleteID" not in update_set_clause