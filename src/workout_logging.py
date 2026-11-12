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


STANDARD_PR_DISTANCES_M = (5_000, 10_000, 21_097, 42_195)
RIEGEL_EXPONENT = 1.06


def estimate_standard_distance_records(
    distance_m: int,
    duration_sec: int,
) -> dict[int, int]:
    if distance_m <= 0 or duration_sec <= 0:
        raise WorkoutLoggingError(
            "Quãng đường và thời gian phải lớn hơn 0."
        )

    return {
        target_m: round(
            duration_sec * (target_m / distance_m) ** RIEGEL_EXPONENT
        )
        for target_m in STANDARD_PR_DISTANCES_M
    }


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

    result = dict(row)
    result["improved_personal_records"] = (
        update_estimated_personal_records(
            connection,
            athlete_id=int(activity.athlete_id),
            activity_date=activity.activity_date,
            distance_m=distance_m,
            duration_sec=duration_sec,
        )
    )
    return result


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

def update_estimated_personal_records(
    connection: Connection,
    *,
    athlete_id: int,
    activity_date: date,
    distance_m: int,
    duration_sec: int,
) -> list[dict]:
    improved = []

    estimates = estimate_standard_distance_records(
        distance_m,
        duration_sec,
    )

    for target_m, estimated_duration_sec in estimates.items():
        best = connection.execute(
            text(
                """
                SELECT MIN(completed_duration_sec)
                FROM public.athlete_personal_records
                WHERE athlete_id = :athlete_id
                  AND distance_m = :distance_m
                """
            ),
            {
                "athlete_id": athlete_id,
                "distance_m": target_m,
            },
        ).scalar_one()

        if best is not None and estimated_duration_sec >= int(best):
            continue

        connection.execute(
            text(
                """
                INSERT INTO public.athlete_personal_records
                (
                    athlete_id,
                    distance_m,
                    completed_duration_sec,
                    achieved_date,
                    record_source
                )
                VALUES
                (
                    :athlete_id,
                    :distance_m,
                    :duration_sec,
                    :achieved_date,
                    'ESTIMATED_ACTIVITY'
                )
                """
            ),
            {
                "athlete_id": athlete_id,
                "distance_m": target_m,
                "duration_sec": estimated_duration_sec,
                "achieved_date": activity_date,
            },
        )

        improved.append(
            {
                "distance_m": target_m,
                "duration_sec": estimated_duration_sec,
            }
        )

    return improved