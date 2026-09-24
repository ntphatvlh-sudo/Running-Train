from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import text
from sqlalchemy.engine import Connection


class WorkoutLoggingError(ValueError):
    """Dữ liệu nhập từ web không hợp lệ."""


@dataclass(frozen=True)
class RunActivityInput:
    athlete_id: int
    planned_workout_id: int
    activity_date: date

    distance_km: float
    duration_sec: int

    perceived_effort: int
    fatigue_score: int

    pain_flag: bool = False
    notes: str | None = None


@dataclass(frozen=True)
class WellnessInput:
    athlete_id: int
    wellness_date: date

    sleep_hours: float
    sleep_quality: int

    fatigue_score: int
    stress_score: int
    soreness_score: int

    energy_score: int
    mood_score: int
    motivation_score: int

    weight_kg: float | None = None
    resting_heart_rate: int | None = None
    hrv_ms: float | None = None

    notes: str | None = None


def normalize_optional_text(
    value: str | None,
) -> str | None:
    if value is None:
        return None

    normalized = value.strip()

    return normalized or None


def validate_score(
    value: int,
    field_name: str,
    minimum: int,
    maximum: int,
) -> int:
    normalized = int(value)

    if not minimum <= normalized <= maximum:
        raise WorkoutLoggingError(
            f"{field_name} phải từ "
            f"{minimum} đến {maximum}."
        )

    return normalized


def save_run_activity(
    connection: Connection,
    activity: RunActivityInput,
) -> dict:
    distance_m = round(
        float(activity.distance_km) * 1000
    )

    duration_sec = int(activity.duration_sec)

    if distance_m <= 0:
        raise WorkoutLoggingError(
            "Quãng đường phải lớn hơn 0."
        )

    if duration_sec <= 0:
        raise WorkoutLoggingError(
            "Thời gian phải lớn hơn 0."
        )

    row = connection.execute(
        text(
            """
            SELECT *
            FROM public.record_manual_run_activity
            (
                CAST(:athlete_id AS INTEGER),
                CAST(:planned_workout_id AS BIGINT),
                CAST(:activity_date AS DATE),
                CAST(:distance_m AS INTEGER),
                CAST(:duration_sec AS INTEGER),
                CAST(:perceived_effort AS SMALLINT),
                CAST(:fatigue_score AS SMALLINT),
                CAST(:pain_flag AS BOOLEAN),
                CAST(:notes AS VARCHAR)
            )
            """
        ),
        {
            "athlete_id":
                int(activity.athlete_id),

            "planned_workout_id":
                int(activity.planned_workout_id),

            "activity_date":
                activity.activity_date,

            "distance_m":
                distance_m,

            "duration_sec":
                duration_sec,

            "perceived_effort":
                validate_score(
                    activity.perceived_effort,
                    "RPE",
                    1,
                    10,
                ),

            "fatigue_score":
                validate_score(
                    activity.fatigue_score,
                    "Mức độ mệt",
                    1,
                    10,
                ),

            "pain_flag":
                bool(activity.pain_flag),

            "notes":
                normalize_optional_text(
                    activity.notes
                ),
        },
    ).mappings().one()

    return dict(row)


def update_support_session(
    connection: Connection,
    *,
    athlete_id: int,
    support_type: str,
    session_id: int,
    status: str,
    runner_notes: str | None = None,
) -> dict:
    normalized_type = (
        support_type.strip().upper()
    )

    normalized_status = (
        status.strip().upper()
    )

    if normalized_type not in {
        "STRENGTH",
        "MOBILITY",
    }:
        raise WorkoutLoggingError(
            "Loại buổi tập không hợp lệ."
        )

    if normalized_status not in {
        "COMPLETED",
        "SKIPPED",
    }:
        raise WorkoutLoggingError(
            "Trạng thái phải là "
            "COMPLETED hoặc SKIPPED."
        )

    row = connection.execute(
        text(
            """
            SELECT *
            FROM public.update_support_session_status
            (
                CAST(:athlete_id AS INTEGER),
                CAST(:support_type AS VARCHAR),
                CAST(:session_id AS BIGINT),
                CAST(:status AS VARCHAR),
                CAST(:runner_notes AS VARCHAR)
            )
            """
        ),
        {
            "athlete_id":
                int(athlete_id),

            "support_type":
                normalized_type,

            "session_id":
                int(session_id),

            "status":
                normalized_status,

            "runner_notes":
                normalize_optional_text(
                    runner_notes
                ),
        },
    ).mappings().one()

    return dict(row)


def save_daily_wellness(
    connection: Connection,
    wellness: WellnessInput,
) -> dict:
    scores = {
        "sleep_quality":
            validate_score(
                wellness.sleep_quality,
                "Chất lượng giấc ngủ",
                1,
                5,
            ),

        "fatigue_score":
            validate_score(
                wellness.fatigue_score,
                "Mệt mỏi",
                1,
                5,
            ),

        "stress_score":
            validate_score(
                wellness.stress_score,
                "Căng thẳng",
                1,
                5,
            ),

        "soreness_score":
            validate_score(
                wellness.soreness_score,
                "Đau nhức",
                1,
                5,
            ),

        "energy_score":
            validate_score(
                wellness.energy_score,
                "Năng lượng",
                1,
                5,
            ),

        "mood_score":
            validate_score(
                wellness.mood_score,
                "Tâm trạng",
                1,
                5,
            ),

        "motivation_score":
            validate_score(
                wellness.motivation_score,
                "Động lực",
                1,
                5,
            ),
    }

    if wellness.sleep_hours <= 0:
        raise WorkoutLoggingError(
            "Số giờ ngủ phải lớn hơn 0."
        )

    row = connection.execute(
        text(
            """
            SELECT *
            FROM public.upsert_daily_wellness
            (
                CAST(:athlete_id AS INTEGER),
                CAST(:wellness_date AS DATE),

                CAST(:sleep_hours AS NUMERIC),
                CAST(:sleep_quality AS SMALLINT),

                CAST(:weight_kg AS NUMERIC),
                CAST(:resting_heart_rate AS SMALLINT),
                CAST(:hrv_ms AS NUMERIC),

                CAST(:fatigue_score AS SMALLINT),
                CAST(:stress_score AS SMALLINT),
                CAST(:soreness_score AS SMALLINT),

                CAST(:energy_score AS SMALLINT),
                CAST(:mood_score AS SMALLINT),
                CAST(:motivation_score AS SMALLINT),

                CAST(:notes AS VARCHAR)
            )
            """
        ),
        {
            "athlete_id":
                int(wellness.athlete_id),

            "wellness_date":
                wellness.wellness_date,

            "sleep_hours":
                float(wellness.sleep_hours),

            "sleep_quality":
                scores["sleep_quality"],

            "weight_kg":
                wellness.weight_kg,

            "resting_heart_rate":
                wellness.resting_heart_rate,

            "hrv_ms":
                wellness.hrv_ms,

            "fatigue_score":
                scores["fatigue_score"],

            "stress_score":
                scores["stress_score"],

            "soreness_score":
                scores["soreness_score"],

            "energy_score":
                scores["energy_score"],

            "mood_score":
                scores["mood_score"],

            "motivation_score":
                scores["motivation_score"],

            "notes":
                normalize_optional_text(
                    wellness.notes
                ),
        },
    ).mappings().one()

    return dict(row)