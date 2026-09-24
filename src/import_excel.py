from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import text

from database import engine


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXCEL_FILE = (
    PROJECT_ROOT / "data" / "running_data.xlsx"
)

def positive_athlete_id(value: str) -> int:
    """Chuyển athlete ID từ CLI thành số nguyên dương (tối thiểu là 1)."""
    try:
        athlete_id = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "athlete-id phải là số nguyên dương"
        ) from error

    if athlete_id < 1:
        raise argparse.ArgumentTypeError(
            "athlete-id phải lớn hơn hoặc bằng 1"
        )

    return athlete_id


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Đọc và kiểm tra các tham số được truyền từ dòng lệnh."""
    parser = argparse.ArgumentParser(
        description="Nhập dữ liệu chạy bộ từ Excel vào PostgreSQL."
    )

    parser.add_argument(
        "--athlete-id",
        type=positive_athlete_id,
        default=1,
        help="ID của vận động viên; mặc định là 1.",
    )
    parser.add_argument(
        "--excel-file",
        type=Path,
        default=DEFAULT_EXCEL_FILE,
        help="Đường dẫn đến file Excel cần nhập.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Đọc và kiểm tra Excel nhưng không ghi vào PostgreSQL.",
    )
    parser.add_argument(
        "--sync-calendar",
        action="store_true",
        help="Đồng bộ thêm Training Calendar khi nhập dữ liệu.",
    )

    return parser.parse_args(argv)


WORKOUT_TYPE_MAP = {
    "REST": "REST",
    "FULL REST": "REST",
    "STRENGTH": "STRENGTH",
    "MOBILITY": "RECOVERY",
    "ACTIVATION": "RECOVERY",
    "RECOVERY": "RECOVERY",
    "RECOVERY RUN": "RECOVERY",
    "EASY": "EASY",
    "EASY RUN": "EASY",
    "STEADY": "EASY",
    "SHAKEOUT": "EASY",
    "LONG RUN": "LONG_RUN",
    "LONG": "LONG_RUN",
    "TEMPO": "TEMPO",
    "THRESHOLD": "TEMPO",
    "HM SIMULATION": "TEMPO",
    "RACE PACE TUNE-UP": "TEMPO",
    "INTERVAL": "INTERVAL",
    "INTERVALS": "INTERVAL",
    "HM INTERVALS": "INTERVAL",
    "RACE": "RACE",
}


PRIORITY_MAP = {
    "REST": 0.25,
    "RECOVERY": 0.75,
    "EASY": 1.00,
    "STRENGTH": 1.00,
    "TEMPO": 1.25,
    "INTERVAL": 1.25,
    "LONG_RUN": 1.50,
    "RACE": 2.00,
}


def clean_text(value: Any) -> str | None:
    if pd.isna(value):
        return None

    result = str(value).strip()

    if not result or result.lower() == "nan":
        return None

    return result


def clean_float(value: Any) -> float | None:
    if pd.isna(value):
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def clean_int(value: Any) -> int | None:
    number = clean_float(value)

    if number is None:
        return None

    return int(round(number))


def clean_date(value: Any):
    if pd.isna(value):
        return None

    return pd.to_datetime(value, errors="raise").date()


def normalize_workout_type(value: Any) -> str:
    source_type = (clean_text(value) or "EASY").upper()

    return WORKOUT_TYPE_MAP.get(source_type, "EASY")


def create_external_activity_id(
    row: pd.Series,
    excel_row_number: int,
) -> str:
    run_id = clean_text(row.get("Run ID"))

    if run_id:
        return run_id

    activity_date = clean_date(row.get("Date"))
    distance_km = clean_float(row.get("Distance km"))

    if activity_date is None or distance_km is None:
        raise ValueError(
            f"Không thể tự tạo Run ID cho dòng Excel "
            f"{excel_row_number}: thiếu Date hoặc Distance km"
        )

    distance_m = int(round(distance_km * 1000))

    return (
        f"RUN-{activity_date:%Y%m%d}-"
        f"{distance_m}M"
    )


def clean_yes_no(value: Any) -> bool:
    if pd.isna(value):
        return False

    normalized = str(value).strip().upper()

    return normalized in {
        "YES",
        "Y",
        "TRUE",
        "1",
        "COMPLETED",
    }


def read_training_calendar(excel_file: Path = DEFAULT_EXCEL_FILE,) -> pd.DataFrame:
    frame = pd.read_excel(
        excel_file,
        sheet_name="Training Calendar",
        header=3,
        engine="openpyxl",
    )

    required_columns = {
        "Date",
        "Session Type",
        "Session Detail",
        "Planned km",
        "Planned min",
    }

    missing = required_columns - set(frame.columns)

    if missing:
        raise ValueError(
            "Training Calendar thiếu cột: "
            + ", ".join(sorted(missing))
        )

    frame = frame.dropna(
        subset=["Date", "Session Type"],
        how="any",
    ).copy()

    frame["Date"] = frame["Date"].apply(clean_date)

    return frame


def read_run_log(excel_file: Path = DEFAULT_EXCEL_FILE,) -> pd.DataFrame:
    frame = pd.read_excel(
        excel_file,
        sheet_name="Run Log",
        header=3,
        engine="openpyxl",
    )

    required_columns = {
        "Date",
        "Session Type",
        "Distance km",
        "Duration min",
    }

    missing = required_columns - set(frame.columns)

    if missing:
        raise ValueError(
            "Run Log thiếu cột: "
            + ", ".join(sorted(missing))
        )

    frame = frame.dropna(
        subset=[
            "Date",
            "Distance km",
            "Duration min",
        ],
        how="any",
    ).copy()

    frame["Date"] = frame["Date"].apply(clean_date)

    return frame


def read_strength_log(excel_file: Path = DEFAULT_EXCEL_FILE,) -> pd.DataFrame:
    frame = pd.read_excel(
        excel_file,
        sheet_name="Strength Log",
        header=3,
        engine="openpyxl",
    )

    required_columns = {
        "Date",
        "Exercise Completed",
        "Session ID",
    }

    missing = required_columns - set(frame.columns)

    if missing:
        raise ValueError(
            "Strength Log thiếu cột: "
            + ", ".join(sorted(missing))
        )

    frame = frame.dropna(
        subset=["Date"],
        how="any",
    ).copy()

    frame["Date"] = frame["Date"].apply(clean_date)

    frame["ExerciseCompleted"] = (
        frame["Exercise Completed"].apply(clean_yes_no)
    )

    # Không đọc Session Completion % từ công thức Excel.
    # Python tự tính lại để tránh lỗi cached formula.
    sessions = (
        frame.groupby(
            ["Date", "Session ID"],
            dropna=False,
        )
        .agg(
            PlannedExerciseCount=(
                "ExerciseCompleted",
                "size",
            ),
            CompletedExerciseCount=(
                "ExerciseCompleted",
                "sum",
            ),
        )
        .reset_index()
    )

    sessions["SessionCompletionRate"] = (
        sessions["CompletedExerciseCount"]
        / sessions["PlannedExerciseCount"]
    )

    sessions["Completed"] = (
        sessions["SessionCompletionRate"] >= 1.0
    )

    return sessions


def read_strength_exercise_log(
    excel_file: Path = DEFAULT_EXCEL_FILE,
) -> pd.DataFrame:
    """Đọc chi tiết từng động tác trong Strength Log."""
    frame = pd.read_excel(
        excel_file,
        sheet_name="Strength Log",
        header=3,
        engine="openpyxl",
    )

    required_columns = {
        "Strength ID",
        "Date",
        "Exercise",
        "Category",
        "Load kg",
        "Sets",
        "Reps / Duration",
        "Volume kg",
        "RPE 1-10",
        "Side",
        "Coaching Notes",
        "Exercise Completed",
        "Session ID",
    }

    missing = required_columns - set(frame.columns)

    if missing:
        raise ValueError(
            "Strength Log thiếu cột chi tiết: "
            + ", ".join(sorted(missing))
        )

    frame = frame.dropna(
        subset=[
            "Strength ID",
            "Date",
            "Exercise",
            "Session ID",
        ],
        how="any",
    ).copy()

    frame["Date"] = frame["Date"].apply(clean_date)
    frame["ExerciseCompletedBool"] = (
        frame["Exercise Completed"].apply(clean_yes_no)
    )

    normalized_ids = frame["Strength ID"].apply(clean_text)
    duplicate_ids = normalized_ids[
        normalized_ids.duplicated(keep=False)
    ].dropna()

    if not duplicate_ids.empty:
        duplicate_list = ", ".join(
            sorted(set(duplicate_ids.astype(str)))
        )
        raise ValueError(
            "Strength Log có Strength ID trùng: "
            f"{duplicate_list}"
        )

    return frame


def read_daily_wellness(excel_file: Path = DEFAULT_EXCEL_FILE,) -> pd.DataFrame:
    frame = pd.read_excel(
        excel_file,
        sheet_name="Daily Wellness",
        header=3,
        engine="openpyxl",
    )

    required_columns = {
        "Date",
        "Sleep h",
        "Sleep Quality 1-5",
        "Weight kg",
        "Resting HR",
        "HRV ms",
        "Fatigue 1-5",
        "Stress 1-5",
        "Soreness 1-5",
        "Energy 1-5",
        "Mood 1-5",
        "Motivation 1-5",
        "Readiness Score",
        "Readiness Band",
        "Notes",
        "Data Quality",
    }

    missing = required_columns - set(frame.columns)

    if missing:
        raise ValueError(
            "Daily Wellness thiếu cột: "
            + ", ".join(sorted(missing))
        )

    frame = frame.dropna(
        subset=["Date"],
        how="any",
    ).copy()

    frame["Date"] = frame["Date"].apply(clean_date)

    # Không nhập các ngày tương lai.
    today = pd.Timestamp.today().date()

    frame = frame[
        frame["Date"] <= today
    ].copy()

    # Không nhập các dòng chỉ có sẵn ngày
    # nhưng chưa có dữ liệu Wellness thực tế.
    wellness_columns = [
        "Sleep h",
        "Sleep Quality 1-5",
        "Weight kg",
        "Resting HR",
        "HRV ms",
        "Fatigue 1-5",
        "Stress 1-5",
        "Soreness 1-5",
        "Energy 1-5",
        "Mood 1-5",
        "Motivation 1-5",
        "Readiness Score",
    ]

    frame = frame.dropna(
        subset=wellness_columns,
        how="all",
    ).copy()

    return frame


def read_mobility_log(excel_file: Path = DEFAULT_EXCEL_FILE,) -> pd.DataFrame:
    frame = pd.read_excel(
        excel_file,
        sheet_name="Mobility Log",
        header=3,
        engine="openpyxl",
    )

    required_columns = {
        "Date",
        "Routine Plan",
        "Completed",
    }

    missing = required_columns - set(frame.columns)

    if missing:
        raise ValueError(
            "Mobility Log thiếu cột: "
            + ", ".join(sorted(missing))
        )

    frame = frame.dropna(
        subset=["Date"],
        how="any",
    ).copy()

    frame["Date"] = frame["Date"].apply(clean_date)
    frame["CompletedBool"] = frame["Completed"].apply(
        clean_yes_no
    )

    frame["Session ID"] = frame["Date"].apply(
        lambda value: f"MOB-{value:%Y%m%d}"
    )

    return frame


def ensure_athlete_exists(
    connection,
    athlete_id: int,
) -> None:
    """Dừng import nếu athlete_id không tồn tại trong PostgreSQL."""
    athlete_exists = connection.execute(
        text(
            """
            SELECT 1
            FROM public.athletes
            WHERE athlete_id = :athlete_id
            LIMIT 1
            """
        ),
        {"athlete_id": athlete_id},
    ).scalar_one_or_none()

    if athlete_exists is None:
        raise ValueError(
            f"Không tìm thấy AthleteID={athlete_id}"
        )


def load_plans(
    connection,
    athlete_id: int,
) -> list[dict]:
    """Tải các training plan thuộc vận động viên chỉ định"""
    rows = connection.execute(
        text(
            """
            SELECT
                plan_id AS "PlanID",
                plan_name AS "PlanName",
                start_date AS "StartDate",
                end_date AS "EndDate"
            FROM public.training_plans
            WHERE athlete_id = :athlete_id
            ORDER BY start_date
            """
        ),
        {"athlete_id": athlete_id},
    ).mappings().all()

    if not rows:
        raise ValueError(
            f"Không tìm thấy training plan cho "
            f"AthleteID={athlete_id}"
        )

    return [dict(row) for row in rows]


def find_plan_id(
    scheduled_date,
    plans: list[dict],
) -> int:
    matches = [
        plan
        for plan in plans
        if plan["StartDate"]
        <= scheduled_date
        <= plan["EndDate"]
    ]

    if not matches:
        raise ValueError(
            f"Không tìm thấy plan chứa ngày "
            f"{scheduled_date}"
        )

    if len(matches) > 1:
        plan_ids = [
            str(plan["PlanID"])
            for plan in matches
        ]

        raise ValueError(
            f"Ngày {scheduled_date} thuộc nhiều plan: "
            + ", ".join(plan_ids)
        )

    return int(matches[0]["PlanID"])


def preview_plan_mapping(
    calendar: pd.DataFrame,
    plans: list[dict],
):
    counts: dict[int, int] = {}

    for _, row in calendar.iterrows():
        plan_id = find_plan_id(
            row["Date"],
            plans,
        )

        counts[plan_id] = counts.get(plan_id, 0) + 1

    print("Phân bổ Training Calendar:")

    for plan in plans:
        plan_id = int(plan["PlanID"])

        print(
            f"  PlanID {plan_id}: "
            f"{plan['StartDate']} đến {plan['EndDate']} "
            f"→ {counts.get(plan_id, 0)} dòng"
        )


def sync_training_calendar(
    connection,
    frame: pd.DataFrame,
    plans: list[dict],
) -> tuple[int, int]:
    find_existing = text(
        """
        SELECT planned_workout_id
        FROM public.planned_workouts
        WHERE plan_id = :plan_id
          AND scheduled_date = :scheduled_date
          AND workout_name = :workout_name
        """
    )

    insert_statement = text(
        """
        INSERT INTO public.planned_workouts
        (
            plan_id,
            scheduled_date,
            workout_type,
            workout_name,
            planned_distance_m,
            planned_duration_sec,
            target_pace_sec_per_km,
            priority_weight,
            status,
            notes
        )
        VALUES
        (
            :plan_id,
            :scheduled_date,
            :workout_type,
            :workout_name,
            :distance_m,
            :duration_sec,
            :target_pace,
            :priority_weight,
            :status,
            :notes
        )
        """
    )

    update_statement = text(
        """
        UPDATE public.planned_workouts
        SET
            workout_type = :workout_type,
            planned_distance_m = :distance_m,
            planned_duration_sec = :duration_sec,
            target_pace_sec_per_km = :target_pace,
            priority_weight = :priority_weight,
            status = :status,
            notes = :notes
        WHERE planned_workout_id = :planned_workout_id
        """
    )

    inserted = 0
    updated = 0

    for _, row in frame.iterrows():
        scheduled_date = row["Date"]
        plan_id = find_plan_id(
            scheduled_date,
            plans,
        )

        workout_type = normalize_workout_type(
            row.get("Session Type")
        )

        workout_name = (
            clean_text(row.get("Session Detail"))
            or clean_text(row.get("Session Type"))
            or "Planned workout"
        )

        planned_km = clean_float(
            row.get("Planned km")
        )

        planned_minutes = clean_float(
            row.get("Planned min")
        )

        distance_m = (
            int(round(planned_km * 1000))
            if planned_km is not None
            else None
        )

        duration_sec = (
            int(round(planned_minutes * 60))
            if planned_minutes is not None
            else None
        )

        target_pace = None

        if distance_m and duration_sec:
            target_pace = int(
                round(
                    duration_sec
                    / (distance_m / 1000)
                )
            )

        completed = (
            clean_text(row.get("Completed"))
            or ""
        ).upper()

        status = (
            "COMPLETED"
            if completed == "YES"
            else "PLANNED"
        )

        notes = clean_text(row.get("Notes"))

        parameters = {
            "plan_id": plan_id,
            "scheduled_date": scheduled_date,
            "workout_type": workout_type,
            "workout_name": workout_name[:200],
            "distance_m": distance_m,
            "duration_sec": duration_sec,
            "target_pace": target_pace,
            "priority_weight": PRIORITY_MAP[
                workout_type
            ],
            "status": status,
            "notes": notes,
        }

        existing_id = connection.execute(
            find_existing,
            {
                "plan_id": plan_id,
                "scheduled_date": scheduled_date,
                "workout_name": workout_name[:200],
            },
        ).scalar_one_or_none()

        if existing_id is None:
            connection.execute(
                insert_statement,
                parameters,
            )
            inserted += 1
        else:
            # Workout đã tồn tại trong PostgreSQL.
            # Giữ nguyên phiên bản hiện tại, không ghi đè từ Excel.
            continue

    return inserted, updated


def sync_run_log(
    connection,
    frame: pd.DataFrame,
    athlete_id: int,
) -> tuple[int, int]:
    find_existing = text(
        """
        SELECT activity_id
        FROM public.completed_activities
        WHERE athlete_id = :athlete_id
            AND external_source = 'EXCEL'
            AND external_activity_id = :external_id
        """
    )

    insert_statement = text(
        """
        INSERT INTO public.completed_activities
        (
            athlete_id,
            external_source,
            external_activity_id,
            activity_date,
            activity_type,
            distance_m,
            duration_sec,
            moving_time_sec,
            average_pace_sec_per_km,
            average_heart_rate,
            max_heart_rate,
            elevation_gain_m,
            perceived_effort,
            notes
        )
        VALUES
        (
            :athlete_id,
            'EXCEL',
            :external_id,
            :activity_date,
            'RUN',
            :distance_m,
            :duration_sec,
            :duration_sec,
            :pace_sec_per_km,
            :average_hr,
            :max_hr,
            :elevation_m,
            :rpe,
            :notes
        )
        """
    )

    update_statement = text(
        """
        UPDATE public.completed_activities
        SET
            activity_date = :activity_date,
            activity_type = 'RUN',
            distance_m = :distance_m,
            duration_sec = :duration_sec,
            moving_time_sec = :duration_sec,
            average_pace_sec_per_km = :pace_sec_per_km,
            average_heart_rate = :average_hr,
            max_heart_rate = :max_hr,
            elevation_gain_m = :elevation_m,
            perceived_effort = :rpe,
            notes = :notes,
            imported_at = CURRENT_TIMESTAMP
        WHERE activity_id = :activity_id
            AND athlete_id = :athlete_id
        """
    )

    inserted = 0
    updated = 0
    external_ids_seen: set[str] = set()

    for excel_index, row in frame.iterrows():
        distance_km = clean_float(
            row.get("Distance km")
        )

        duration_minutes = clean_float(
            row.get("Duration min")
        )

        if (
            distance_km is None
            or duration_minutes is None
            or distance_km <= 0
            or duration_minutes <= 0
        ):
            continue

        distance_m = int(
            round(distance_km * 1000)
        )

        duration_sec = int(
            round(duration_minutes * 60)
        )

        pace_sec_per_km = int(
            round(duration_sec / distance_km)
        )

        external_id = create_external_activity_id(
            row,
            excel_index + 5,
        )

        if external_id in external_ids_seen:
            raise ValueError(
                "Run Log tạo ra Run ID trùng: "
                f"{external_id}. Nếu có hai buổi chạy cùng ngày "
                "và cùng cự ly, hãy điền Run ID riêng cho chúng."
            )

        external_ids_seen.add(external_id)

        parameters = {
            "athlete_id": athlete_id,
            "external_id": external_id,
            "activity_date": row["Date"],
            "distance_m": distance_m,
            "duration_sec": duration_sec,
            "pace_sec_per_km": pace_sec_per_km,
            "average_hr": clean_int(
                row.get("Avg HR")
            ),
            "max_hr": clean_int(
                row.get("Max HR")
            ),
            "elevation_m": clean_float(
                row.get("Elevation m")
            ),
            "rpe": clean_int(
                row.get("RPE 1-10")
            ),
            "notes": clean_text(
                row.get("Notes")
            ),
        }

        activity_id = connection.execute(
            find_existing,
            {
                "athlete_id": athlete_id,
                "external_id": external_id,
            },
        ).scalar_one_or_none()

        if activity_id is None:
            connection.execute(
                insert_statement,
                parameters,
            )

            inserted += 1
        else:
            parameters["activity_id"] = activity_id

            connection.execute(
                update_statement,
                parameters,
            )

            updated += 1

    return inserted, updated


def rebuild_rule_run_matches(
    connection,
    athlete_id: int,
) -> tuple[int, int]:
    """Xây dựng lại RULE matches cho một vận động viên."""
    result = connection.execute(
        text(
            """
            SELECT
                rule_matches_removed AS "RuleMatchesRemoved",
                rule_matches_created AS "RuleMatchesCreated"
            FROM public.rebuild_rule_workout_matches
            (
                p_athlete_id => :athlete_id,
                p_plan_id => NULL,
                p_maximum_day_difference => 1,
                p_minimum_confidence => 0.7000
            )
            """
        ),
        {"athlete_id": athlete_id},
    ).mappings().one_or_none()

    if result is None:
        return 0, 0

    return (
        int(result.get("RuleMatchesRemoved") or 0),
        int(result.get("RuleMatchesCreated") or 0),
    )


def sync_daily_wellness(
    connection,
    frame: pd.DataFrame,
    athlete_id: int,
) -> tuple[int, int]:
    """Đồng bộ Daily Wellness trong phạm vi một vận động viên."""
    find_existing = text(
        """
        SELECT daily_wellness_id
        FROM public.daily_wellness
        WHERE athlete_id = :athlete_id
            AND wellness_date = :wellness_date
        """
    )

    insert_statement = text(
        """
        INSERT INTO public.daily_wellness
        (
            athlete_id,
            wellness_date,
            sleep_hours,
            sleep_quality,
            weight_kg,
            resting_heart_rate,
            hrv_ms,
            fatigue_score,
            stress_score,
            soreness_score,
            energy_score,
            mood_score,
            motivation_score,
            readiness_score,
            readiness_band,
            data_quality,
            notes
        )
        VALUES
        (
            :athlete_id,
            :wellness_date,
            :sleep_hours,
            :sleep_quality,
            :weight_kg,
            :resting_hr,
            :hrv_ms,
            :fatigue,
            :stress,
            :soreness,
            :energy,
            :mood,
            :motivation,
            :readiness_score,
            :readiness_band,
            :data_quality,
            :notes
        )
        """
    )

    update_statement = text(
        """
        UPDATE public.daily_wellness
        SET
            sleep_hours = :sleep_hours,
            sleep_quality = :sleep_quality,
            weight_kg = :weight_kg,
            resting_heart_rate = :resting_hr,
            hrv_ms = :hrv_ms,
            fatigue_score = :fatigue,
            stress_score = :stress,
            soreness_score = :soreness,
            energy_score = :energy,
            mood_score = :mood,
            motivation_score = :motivation,
            readiness_score = :readiness_score,
            readiness_band = :readiness_band,
            data_quality = :data_quality,
            notes = :notes,
            imported_at = CURRENT_TIMESTAMP
        WHERE daily_wellness_id =
            :daily_wellness_id
            AND athlete_id = :athlete_id
        """
    )

    inserted = 0
    updated = 0

    for _, row in frame.iterrows():
        parameters = {
            "athlete_id": athlete_id,
            "wellness_date": row["Date"],

            "sleep_hours": clean_float(
                row.get("Sleep h")
            ),

            "sleep_quality": clean_int(
                row.get("Sleep Quality 1-5")
            ),

            "weight_kg": clean_float(
                row.get("Weight kg")
            ),

            "resting_hr": clean_int(
                row.get("Resting HR")
            ),

            "hrv_ms": clean_float(
                row.get("HRV ms")
            ),

            "fatigue": clean_float(
                row.get("Fatigue 1-5")
            ),

            "stress": clean_float(
                row.get("Stress 1-5")
            ),

            "soreness": clean_float(
                row.get("Soreness 1-5")
            ),

            "energy": clean_float(
                row.get("Energy 1-5")
            ),

            "mood": clean_float(
                row.get("Mood 1-5")
            ),

            "motivation": clean_float(
                row.get("Motivation 1-5")
            ),

            "readiness_score": clean_float(
                row.get("Readiness Score")
            ),

            "readiness_band": clean_text(
                row.get("Readiness Band")
            ),

            "data_quality": clean_text(
                row.get("Data Quality")
            ),

            "notes": clean_text(
                row.get("Notes")
            ),
        }

        existing_id = connection.execute(
            find_existing,
            {
                "athlete_id": athlete_id,
                "wellness_date": row["Date"],
            },
        ).scalar_one_or_none()

        if existing_id is None:
            connection.execute(
                insert_statement,
                parameters,
            )

            inserted += 1
        else:
            parameters["daily_wellness_id"] = (
                existing_id
            )

            connection.execute(
                update_statement,
                parameters,
            )

            updated += 1

    return inserted, updated


def sync_strength_log(
    connection,
    frame: pd.DataFrame,
    athlete_id: int,
) -> tuple[int, int]:
    """Đồng bộ Strength Log trong phạm vi một vận động viên."""
    find_existing = text(
        """
        SELECT strength_session_id
        FROM public.strength_sessions
        WHERE athlete_id = :athlete_id
          AND external_session_id = :external_session_id
        """
    )

    insert_statement = text(
        """
        INSERT INTO public.strength_sessions
        (
            athlete_id,
            external_session_id,
            session_date,
            planned_exercise_count,
            completed_exercise_count,
            session_completion_rate,
            completed
        )
        VALUES
        (
            :athlete_id,
            :external_session_id,
            :session_date,
            :planned_count,
            :completed_count,
            :completion_rate,
            :completed
        )
        """
    )

    update_statement = text(
        """
        UPDATE public.strength_sessions
        SET
            session_date = :session_date,
            planned_exercise_count = :planned_count,
            completed_exercise_count = :completed_count,
            session_completion_rate = :completion_rate,
            completed = :completed,
            imported_at = CURRENT_TIMESTAMP
        WHERE strength_session_id = :session_id
            AND athlete_id = :athlete_id
        """
    )

    inserted = 0
    updated = 0

    for _, row in frame.iterrows():
        parameters = {
            "athlete_id": athlete_id,
            "external_session_id": str(row["Session ID"]),
            "session_date": row["Date"],
            "planned_count": int(
                row["PlannedExerciseCount"]
            ),
            "completed_count": int(
                row["CompletedExerciseCount"]
            ),
            "completion_rate": float(
                row["SessionCompletionRate"]
            ),
            "completed": bool(row["Completed"]),
        }

        existing_id = connection.execute(
            find_existing,
            parameters,
        ).scalar_one_or_none()

        if existing_id is None:
            connection.execute(
                insert_statement,
                parameters,
            )
            inserted += 1
        else:
            parameters["session_id"] = existing_id
            connection.execute(
                update_statement,
                parameters,
            )
            updated += 1

    return inserted, updated


def sync_strength_exercise_log(
    connection,
    frame: pd.DataFrame,
    athlete_id: int,
) -> tuple[int, int]:
    """Đồng bộ từng động tác vào đúng Strength session của athlete."""
    find_session = text(
        """
        SELECT strength_session_id
        FROM public.strength_sessions
        WHERE athlete_id = :athlete_id
          AND external_session_id = :external_session_id
        """
    )

    find_existing = text(
        """
        SELECT strength_exercise_id
        FROM public.strength_exercises
        WHERE strength_session_id = :session_id
          AND external_strength_id = :external_strength_id
        """
    )

    insert_statement = text(
        """
        INSERT INTO public.strength_exercises
        (
            strength_session_id,
            external_strength_id,
            exercise_name,
            exercise_category,
            planned_load_kg,
            planned_sets,
            planned_reps_or_duration,
            planned_volume_kg,
            planned_rpe,
            exercise_side,
            coaching_notes,
            exercise_completed
        )
        VALUES
        (
            :session_id,
            :external_strength_id,
            :exercise_name,
            :exercise_category,
            :planned_load_kg,
            :planned_sets,
            :planned_reps_or_duration,
            :planned_volume_kg,
            :planned_rpe,
            :exercise_side,
            :coaching_notes,
            :exercise_completed
        )
        """
    )

    update_statement = text(
        """
        UPDATE public.strength_exercises
        SET
            exercise_name = :exercise_name,
            exercise_category = :exercise_category,
            planned_load_kg = :planned_load_kg,
            planned_sets = :planned_sets,
            planned_reps_or_duration = :planned_reps_or_duration,
            planned_volume_kg = :planned_volume_kg,
            planned_rpe = :planned_rpe,
            exercise_side = :exercise_side,
            coaching_notes = :coaching_notes,
            exercise_completed = :exercise_completed,
            imported_at = CURRENT_TIMESTAMP
        WHERE strength_exercise_id = :strength_exercise_id
          AND strength_session_id = :session_id
        """
    )

    inserted = 0
    updated = 0

    for _, row in frame.iterrows():
        external_session_id = clean_text(row.get("Session ID"))
        external_strength_id = clean_text(row.get("Strength ID"))
        exercise_name = clean_text(row.get("Exercise"))

        if not external_session_id:
            raise ValueError("Strength exercise thiếu Session ID.")

        if not external_strength_id:
            raise ValueError("Strength exercise thiếu Strength ID.")

        if not exercise_name:
            raise ValueError("Strength exercise thiếu Exercise.")

        session_parameters = {
            "athlete_id": athlete_id,
            "external_session_id": external_session_id,
        }
        session_id = connection.execute(
            find_session,
            session_parameters,
        ).scalar_one_or_none()

        if session_id is None:
            raise ValueError(
                "Không tìm thấy Strength session "
                f"{external_session_id} cho AthleteID={athlete_id}."
            )

        parameters = {
            "session_id": int(session_id),
            "external_strength_id": external_strength_id,
            "exercise_name": exercise_name,
            "exercise_category": clean_text(row.get("Category")),
            "planned_load_kg": clean_float(row.get("Load kg")),
            "planned_sets": clean_int(row.get("Sets")),
            "planned_reps_or_duration": clean_text(
                row.get("Reps / Duration")
            ),
            "planned_volume_kg": clean_float(row.get("Volume kg")),
            "planned_rpe": clean_int(row.get("RPE 1-10")),
            "exercise_side": clean_text(row.get("Side")),
            "coaching_notes": clean_text(row.get("Coaching Notes")),
            "exercise_completed": bool(
                row.get("ExerciseCompletedBool", False)
            ),
        }

        existing_id = connection.execute(
            find_existing,
            {
                "session_id": int(session_id),
                "external_strength_id": external_strength_id,
            },
        ).scalar_one_or_none()

        if existing_id is None:
            connection.execute(
                insert_statement,
                parameters,
            )
            inserted += 1
        else:
            parameters["strength_exercise_id"] = int(existing_id)
            connection.execute(
                update_statement,
                parameters,
            )
            updated += 1

    return inserted, updated


def sync_mobility_log(
    connection,
    frame: pd.DataFrame,
    athlete_id: int,
) -> tuple[int, int]:
    """Đồng bộ Mobility Log trong phạm vi một vận động viên."""
    find_existing = text(
        """
        SELECT mobility_session_id
        FROM public.mobility_sessions
        WHERE athlete_id = :athlete_id
            AND external_session_id = :external_session_id
        """
    )

    insert_statement = text(
        """
        INSERT INTO public.mobility_sessions
        (
            athlete_id,
            external_session_id,
            session_date,
            routine,
            completed
        )
        VALUES
        (
            :athlete_id,
            :external_session_id,
            :session_date,
            :routine,
            :completed
        )
        """
    )

    update_statement = text(
        """
        UPDATE public.mobility_sessions
        SET
            session_date = :session_date,
            routine = :routine,
            completed = :completed,
            imported_at = CURRENT_TIMESTAMP
        WHERE mobility_session_id = :session_id
            AND athlete_id = :athlete_id
        """
    )

    inserted = 0
    updated = 0

    for _, row in frame.iterrows():
        parameters = {
            "athlete_id": athlete_id,
            "external_session_id": str(row["Session ID"]),
            "session_date": row["Date"],
            "routine": clean_text(row.get("Routine Plan")),
            "completed": bool(row["CompletedBool"]),
        }

        existing_id = connection.execute(
            find_existing,
            parameters,
        ).scalar_one_or_none()

        if existing_id is None:
            connection.execute(
                insert_statement,
                parameters,
            )
            inserted += 1
        else:
            parameters["session_id"] = existing_id
            connection.execute(
                update_statement,
                parameters,
            )
            updated += 1

    return inserted, updated


def main() -> None:
    args = parse_args()

    if not args.excel_file.exists():
        raise FileNotFoundError(
            f"Không tìm thấy file Excel: {args.excel_file}"
        )

    if not args.sync_calendar:
        raise ValueError(
            "Importer hiện chỉ dùng để đồng bộ "
            "Training Calendar. "
            "Hãy chạy lại với cờ --sync-calendar. "
            "Run, Strength, Mobility và Daily Wellness "
            "được nhập trực tiếp trên web."
        )

    calendar = read_training_calendar(
        args.excel_file
    )

    print(
        f"Training Calendar hợp lệ: "
        f"{len(calendar)} dòng"
    )

    if args.dry_run:
        print(
            "DRY RUN hoàn tất. "
            "Training Calendar hợp lệ và "
            "không có dữ liệu nào được ghi "
            "vào PostgreSQL."
        )
        return

    with engine.begin() as connection:
        ensure_athlete_exists(
            connection,
            args.athlete_id,
        )

        plans = load_plans(
            connection,
            args.athlete_id,
        )

        preview_plan_mapping(
            calendar,
            plans,
        )

        plan_inserted, plan_updated = (
            sync_training_calendar(
                connection,
                calendar,
                plans,
            )
        )

    print("Đồng bộ Training Calendar hoàn tất.")

    print(
        f"PlannedWorkouts: "
        f"thêm {plan_inserted}, "
        f"cập nhật {plan_updated}"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("Đồng bộ thất bại.")
        print(f"{type(error).__name__}: {error}")
        raise
