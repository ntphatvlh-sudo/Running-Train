from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import text

from database import engine


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXCEL_FILE = PROJECT_ROOT / "data" / "running_data.xlsx"

ATHLETE_ID = 1
DRY_RUN = "--dry-run" in sys.argv
SYNC_CALENDAR = "--sync-calendar" in sys.argv


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


def read_training_calendar() -> pd.DataFrame:
    frame = pd.read_excel(
        EXCEL_FILE,
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


def read_run_log() -> pd.DataFrame:
    frame = pd.read_excel(
        EXCEL_FILE,
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


def read_strength_log() -> pd.DataFrame:
    frame = pd.read_excel(
        EXCEL_FILE,
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


def read_daily_wellness() -> pd.DataFrame:
    frame = pd.read_excel(
        EXCEL_FILE,
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


def read_mobility_log() -> pd.DataFrame:
    frame = pd.read_excel(
        EXCEL_FILE,
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



def load_plans(connection) -> list[dict]:
    rows = connection.execute(
        text(
            """
            SELECT
                PlanID,
                PlanName,
                StartDate,
                EndDate
            FROM dbo.TrainingPlans
            WHERE AthleteID = :athlete_id
            ORDER BY StartDate
            """
        ),
        {"athlete_id": ATHLETE_ID},
    ).mappings().all()

    if not rows:
        raise ValueError(
            f"Không tìm thấy training plan cho "
            f"AthleteID={ATHLETE_ID}"
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
        SELECT PlannedWorkoutID
        FROM dbo.PlannedWorkouts
        WHERE PlanID = :plan_id
          AND ScheduledDate = :scheduled_date
          AND WorkoutName = :workout_name
        """
    )

    insert_statement = text(
        """
        INSERT INTO dbo.PlannedWorkouts
        (
            PlanID,
            ScheduledDate,
            WorkoutType,
            WorkoutName,
            PlannedDistanceM,
            PlannedDurationSec,
            TargetPaceSecPerKm,
            PriorityWeight,
            Status,
            Notes
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
        UPDATE dbo.PlannedWorkouts
        SET
            WorkoutType = :workout_type,
            PlannedDistanceM = :distance_m,
            PlannedDurationSec = :duration_sec,
            TargetPaceSecPerKm = :target_pace,
            PriorityWeight = :priority_weight,
            Status = :status,
            Notes = :notes
        WHERE PlannedWorkoutID = :planned_workout_id
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
            parameters,)

            inserted += 1
        else:
        # Workout đã tồn tại trong SQL Server.
        # Giữ nguyên phiên bản hiện tại, không ghi đè từ Excel.
            continue

    return inserted, updated


def sync_run_log(
    connection,
    frame: pd.DataFrame,
) -> tuple[int, int]:
    find_existing = text(
        """
        SELECT ActivityID
        FROM dbo.CompletedActivities
        WHERE ExternalSource = 'EXCEL'
          AND ExternalActivityID = :external_id
        """
    )

    insert_statement = text(
        """
        INSERT INTO dbo.CompletedActivities
        (
            AthleteID,
            ExternalSource,
            ExternalActivityID,
            ActivityDate,
            ActivityType,
            DistanceM,
            DurationSec,
            MovingTimeSec,
            AveragePaceSecPerKm,
            AverageHeartRate,
            MaxHeartRate,
            ElevationGainM,
            PerceivedEffort,
            Notes
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
        UPDATE dbo.CompletedActivities
        SET
            AthleteID = :athlete_id,
            ActivityDate = :activity_date,
            ActivityType = 'RUN',
            DistanceM = :distance_m,
            DurationSec = :duration_sec,
            MovingTimeSec = :duration_sec,
            AveragePaceSecPerKm = :pace_sec_per_km,
            AverageHeartRate = :average_hr,
            MaxHeartRate = :max_hr,
            ElevationGainM = :elevation_m,
            PerceivedEffort = :rpe,
            Notes = :notes,
            ImportedAt = SYSUTCDATETIME()
        WHERE ActivityID = :activity_id
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
            "athlete_id": ATHLETE_ID,
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
            {"external_id": external_id},
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


def rebuild_rule_run_matches(connection) -> tuple[int, int]:
    result = connection.execute(
        text(
            """
            EXEC dbo.usp_RebuildRuleWorkoutMatches
                @AthleteID = :athlete_id,
                @PlanID = NULL,
                @MaximumDayDifference = 1,
                @MinimumConfidence = 0.7000
            """
        ),
        {"athlete_id": ATHLETE_ID},
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
) -> tuple[int, int]:
    find_existing = text(
        """
        SELECT DailyWellnessID
        FROM dbo.DailyWellness
        WHERE AthleteID = :athlete_id
          AND WellnessDate = :wellness_date
        """
    )

    insert_statement = text(
        """
        INSERT INTO dbo.DailyWellness
        (
            AthleteID,
            WellnessDate,
            SleepHours,
            SleepQuality,
            WeightKg,
            RestingHeartRate,
            HRVMs,
            FatigueScore,
            StressScore,
            SorenessScore,
            EnergyScore,
            MoodScore,
            MotivationScore,
            ReadinessScore,
            ReadinessBand,
            DataQuality,
            Notes
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
        UPDATE dbo.DailyWellness
        SET
            SleepHours = :sleep_hours,
            SleepQuality = :sleep_quality,
            WeightKg = :weight_kg,
            RestingHeartRate = :resting_hr,
            HRVMs = :hrv_ms,
            FatigueScore = :fatigue,
            StressScore = :stress,
            SorenessScore = :soreness,
            EnergyScore = :energy,
            MoodScore = :mood,
            MotivationScore = :motivation,
            ReadinessScore = :readiness_score,
            ReadinessBand = :readiness_band,
            DataQuality = :data_quality,
            Notes = :notes,
            ImportedAt = SYSUTCDATETIME()
        WHERE DailyWellnessID =
            :daily_wellness_id
        """
    )

    inserted = 0
    updated = 0

    for _, row in frame.iterrows():
        parameters = {
            "athlete_id": ATHLETE_ID,
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
                "athlete_id": ATHLETE_ID,
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
) -> tuple[int, int]:
    find_existing = text(
        """
        SELECT StrengthSessionID
        FROM dbo.StrengthSessions
        WHERE AthleteID = :athlete_id
          AND ExternalSessionID = :external_session_id
        """
    )

    insert_statement = text(
        """
        INSERT INTO dbo.StrengthSessions
        (
            AthleteID,
            ExternalSessionID,
            SessionDate,
            PlannedExerciseCount,
            CompletedExerciseCount,
            SessionCompletionRate,
            Completed
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
        UPDATE dbo.StrengthSessions
        SET
            SessionDate = :session_date,
            PlannedExerciseCount = :planned_count,
            CompletedExerciseCount = :completed_count,
            SessionCompletionRate = :completion_rate,
            Completed = :completed,
            ImportedAt = SYSUTCDATETIME()
        WHERE StrengthSessionID = :session_id
        """
    )

    inserted = 0
    updated = 0

    for _, row in frame.iterrows():
        parameters = {
            "athlete_id": ATHLETE_ID,
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


def sync_mobility_log(
    connection,
    frame: pd.DataFrame,
) -> tuple[int, int]:
    find_existing = text(
        """
        SELECT MobilitySessionID
        FROM dbo.MobilitySessions
        WHERE AthleteID = :athlete_id
          AND ExternalSessionID = :external_session_id
        """
    )

    insert_statement = text(
        """
        INSERT INTO dbo.MobilitySessions
        (
            AthleteID,
            ExternalSessionID,
            SessionDate,
            Routine,
            Completed
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
        UPDATE dbo.MobilitySessions
        SET
            SessionDate = :session_date,
            Routine = :routine,
            Completed = :completed,
            ImportedAt = SYSUTCDATETIME()
        WHERE MobilitySessionID = :session_id
        """
    )

    inserted = 0
    updated = 0

    for _, row in frame.iterrows():
        parameters = {
            "athlete_id": ATHLETE_ID,
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
    if not EXCEL_FILE.exists():
        raise FileNotFoundError(
            f"Không tìm thấy file Excel: {EXCEL_FILE}"
        )

    # Mặc định không đọc Training Calendar.
    calendar = None

    # Chỉ đọc Training Calendar khi người dùng
    # chạy script với cờ --sync-calendar.
    if SYNC_CALENDAR:
        calendar = read_training_calendar()

    # Các log này luôn được đọc khi chạy import.
    run_log = read_run_log()
    daily_wellness = read_daily_wellness()
    strength_log = read_strength_log()
    mobility_log = read_mobility_log()

    # Hiển thị số lượng dữ liệu đã đọc.
    if calendar is not None:
        print(
            f"Training Calendar hợp lệ: "
            f"{len(calendar)} dòng"
        )

    print(
        f"Run Log hợp lệ: "
        f"{len(run_log)} dòng"
    )

    print(
    f"Daily Wellness hợp lệ: "
    f"{len(daily_wellness)} ngày"
    )

    print(
        f"Strength sessions hợp lệ: "
        f"{len(strength_log)} buổi"
    )

    print(
        f"Mobility sessions hợp lệ: "
        f"{len(mobility_log)} buổi"
    )

    # DRY RUN chỉ kiểm tra Excel.
    # Không mở transaction ghi dữ liệu.
    if DRY_RUN:
        print(
            "DRY RUN hoàn tất. "
            "Không có dữ liệu nào được ghi vào SQL Server."
        )
        return

    # Giá trị mặc định của kết quả calendar.
    plan_inserted = 0
    plan_updated = 0

    # Mở transaction SQL Server.
    with engine.begin() as connection:

        # Chỉ đồng bộ calendar khi có --sync-calendar.
        if calendar is not None:
            plans = load_plans(connection)

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

        # Run Log luôn được đồng bộ.
        run_inserted, run_updated = (
            sync_run_log(
                connection,
                run_log,
            )
        )
        # Đồng bộ Daily wellness
        wellness_inserted, wellness_updated = (
            sync_daily_wellness(
                connection,
                daily_wellness,
            )
        )

        # Strength Log luôn được đồng bộ.
        strength_inserted, strength_updated = (
            sync_strength_log(
                connection,
                strength_log,
            )
        )

        # Mobility Log luôn được đồng bộ.
        mobility_inserted, mobility_updated = (
            sync_mobility_log(
                connection,
                mobility_log,
            )
        )

        # Xây dựng lại các RULE match để dữ liệu mới có thể sửa
        # những match lệch ngày đã được tạo từ lần import trước.
        # Các match MANUAL luôn được giữ nguyên.
        (
            rule_matches_removed,
            rule_matches_created,
        ) = rebuild_rule_run_matches(connection)

    print("Đồng bộ hoàn tất.")

    if calendar is not None:
        print(
            f"PlannedWorkouts: "
            f"thêm {plan_inserted}, "
            f"cập nhật {plan_updated}"
        )
    else:
        print(
            "PlannedWorkouts: không đồng bộ "
            "(không có cờ --sync-calendar)"
        )

    print(
        f"CompletedActivities: "
        f"thêm {run_inserted}, "
        f"cập nhật {run_updated}"
    )

    print(
        f"DailyWellness: "
        f"thêm {wellness_inserted}, "
        f"cập nhật {wellness_updated}"
    )

    print(
        f"StrengthSessions: "
        f"thêm {strength_inserted}, "
        f"cập nhật {strength_updated}"
    )

    print(
        f"MobilitySessions: "
        f"thêm {mobility_inserted}, "
        f"cập nhật {mobility_updated}"
    )

    print(
        f"WorkoutMatches RULE: xóa "
        f"{rule_matches_removed}, tạo lại "
        f"{rule_matches_created}"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("Đồng bộ thất bại.")
        print(f"{type(error).__name__}: {error}")
        raise
