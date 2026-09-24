from datetime import date

import pytest

from src.workout_logging import (
    RunActivityInput,
    WellnessInput,
    WorkoutLoggingError,
    save_daily_wellness,
    save_run_activity,
    update_support_session,
)


class FakeResult:
    def __init__(self, row: dict):
        self.row = row

    def mappings(self):
        return self

    def one(self):
        return self.row


class RecordingConnection:
    def __init__(self, row: dict):
        self.row = row
        self.statement = None
        self.params = None

    def execute(self, statement, params):
        self.statement = str(statement)
        self.params = params

        return FakeResult(self.row)


def test_save_run_activity_converts_km_and_notes():
    connection = RecordingConnection(
        {
            "created_activity_id": 7,
            "updated_planned_workout_id": 10,
            "updated_workout_status": "COMPLETED",
            "calculated_average_pace_sec_per_km": 360,
            "calculated_completion_rate": 1,
        }
    )

    result = save_run_activity(
        connection,
        RunActivityInput(
            athlete_id=8,
            planned_workout_id=10,
            activity_date=date(2026, 9, 23),
            distance_km=5.25,
            duration_sec=1890,
            perceived_effort=6,
            fatigue_score=4,
            pain_flag=False,
            notes="  Easy run  ",
        ),
    )

    assert result["created_activity_id"] == 7
    assert connection.params["distance_m"] == 5250
    assert connection.params["duration_sec"] == 1890
    assert connection.params["notes"] == "Easy run"

    assert (
        "record_manual_run_activity"
        in connection.statement
    )


@pytest.mark.parametrize(
    ("distance_km", "duration_sec"),
    [
        (0, 1800),
        (-1, 1800),
        (5, 0),
        (5, -1),
    ],
)
def test_save_run_activity_rejects_invalid_values(
    distance_km,
    duration_sec,
):
    connection = RecordingConnection({})

    with pytest.raises(WorkoutLoggingError):
        save_run_activity(
            connection,
            RunActivityInput(
                athlete_id=8,
                planned_workout_id=10,
                activity_date=date(2026, 9, 23),
                distance_km=distance_km,
                duration_sec=duration_sec,
                perceived_effort=5,
                fatigue_score=5,
            ),
        )


def test_save_run_activity_rejects_invalid_score():
    connection = RecordingConnection({})

    with pytest.raises(
        WorkoutLoggingError,
        match="RPE",
    ):
        save_run_activity(
            connection,
            RunActivityInput(
                athlete_id=8,
                planned_workout_id=10,
                activity_date=date(2026, 9, 23),
                distance_km=5,
                duration_sec=1800,
                perceived_effort=11,
                fatigue_score=5,
            ),
        )


def test_update_support_session_normalizes_values():
    connection = RecordingConnection(
        {
            "updated_support_type": "MOBILITY",
            "updated_session_id": 12,
            "updated_status": "SKIPPED",
        }
    )

    result = update_support_session(
        connection,
        athlete_id=8,
        support_type=" mobility ",
        session_id=12,
        status=" skipped ",
        runner_notes="  Sore calf  ",
    )

    assert result["updated_status"] == "SKIPPED"
    assert connection.params["support_type"] == "MOBILITY"
    assert connection.params["status"] == "SKIPPED"
    assert connection.params["runner_notes"] == "Sore calf"

    assert (
        "update_support_session_status"
        in connection.statement
    )


def test_update_support_session_rejects_type():
    connection = RecordingConnection({})

    with pytest.raises(
        WorkoutLoggingError,
        match="Loại buổi tập",
    ):
        update_support_session(
            connection,
            athlete_id=8,
            support_type="REST",
            session_id=12,
            status="COMPLETED",
        )


def test_save_daily_wellness_passes_scores():
    connection = RecordingConnection(
        {
            "saved_daily_wellness_id": 20,
            "calculated_readiness_score": 80,
            "calculated_readiness_band": "READY",
        }
    )

    result = save_daily_wellness(
        connection,
        WellnessInput(
            athlete_id=8,
            wellness_date=date(2026, 9, 23),
            sleep_hours=7.5,
            sleep_quality=4,
            fatigue_score=2,
            stress_score=3,
            soreness_score=2,
            energy_score=4,
            mood_score=5,
            motivation_score=4,
            weight_kg=60.5,
            resting_heart_rate=52,
            hrv_ms=48,
            notes="  Feeling good  ",
        ),
    )

    assert result["calculated_readiness_score"] == 80
    assert connection.params["sleep_hours"] == 7.5
    assert connection.params["fatigue_score"] == 2
    assert connection.params["notes"] == "Feeling good"

    assert (
        "upsert_daily_wellness"
        in connection.statement
    )


@pytest.mark.parametrize(
    (
        "field_name",
        "invalid_value",
        "expected_message",
    ),
    [
        (
            "sleep_quality",
            0,
            "Chất lượng giấc ngủ",
        ),
        (
            "fatigue_score",
            6,
            "Mệt mỏi",
        ),
        (
            "stress_score",
            0,
            "Căng thẳng",
        ),
        (
            "soreness_score",
            6,
            "Đau nhức",
        ),
        (
            "energy_score",
            0,
            "Năng lượng",
        ),
        (
            "mood_score",
            6,
            "Tâm trạng",
        ),
        (
            "motivation_score",
            0,
            "Động lực",
        ),
    ],
)
def test_save_daily_wellness_rejects_invalid_scores(
    field_name,
    invalid_value,
    expected_message,
):
    values = {
        "sleep_quality": 3,
        "fatigue_score": 3,
        "stress_score": 3,
        "soreness_score": 3,
        "energy_score": 3,
        "mood_score": 3,
        "motivation_score": 3,
    }

    values[field_name] = invalid_value

    connection = RecordingConnection({})

    with pytest.raises(
        WorkoutLoggingError,
        match=expected_message,
    ):
        save_daily_wellness(
            connection,
            WellnessInput(
                athlete_id=8,
                wellness_date=date(2026, 9, 23),
                sleep_hours=7,
                **values,
            ),
        )