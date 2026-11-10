from datetime import date, timedelta

import pytest

from src.ai_plan_schema import (
    AITrainingPlanOutput,
    PlannedWorkoutOutput,
)
from src.ai_plan_service import convert_and_validate_ai_workouts
from src.plan_generator import TrainingPlanInput


def make_case():
    today = date.today()
    days_until_monday = (7 - today.weekday()) % 7
    start_date = today + timedelta(days=days_until_monday)
    race_date = start_date + timedelta(days=6)

    plan_input = TrainingPlanInput(
        race_date=race_date,
        goal_distance_m=5_000,
        latest_pr_distance_m=10_000,
        latest_pr_duration_sec=5_100,
        runs_per_week=4,
        plan_level="BEGINNER",
        rest_weekday=0,  # Thứ Hai
        has_injury=False,
    )

    ai_output = AITrainingPlanOutput(
        workouts=[
            PlannedWorkoutOutput(
                scheduled_date=start_date.isoformat(),
                workout_type="REST",
                workout_name="Nghỉ hoàn toàn",
                planned_distance_m=None,
                planned_duration_sec=None,
                target_pace_sec_per_km=None,
                notes="Nghỉ để phục hồi.",
            ),
            PlannedWorkoutOutput(
                scheduled_date=(start_date + timedelta(days=1)).isoformat(),
                workout_type="EASY",
                workout_name="Easy Run",
                planned_distance_m=3_000,
                planned_duration_sec=1_710,
                target_pace_sec_per_km=570,
                notes="Chạy nhẹ, giữ nhịp thoải mái.",
            ),
            PlannedWorkoutOutput(
                scheduled_date=race_date.isoformat(),
                workout_type="RACE",
                workout_name="5 km Race",
                planned_distance_m=5_000,
                planned_duration_sec=1_500,
                target_pace_sec_per_km=300,
                notes="Ngày thi.",
            ),
        ]
    )

    return plan_input, start_date, ai_output


def test_convert_and_validate_accepts_valid_workouts() -> None:
    plan_input, start_date, ai_output = make_case()

    workouts = convert_and_validate_ai_workouts(
        ai_output,
        plan_input,
        start_date,
    )

    assert len(workouts) == 3
    assert workouts[-1].scheduled_date == plan_input.race_date
    assert workouts[-1].workout_type == "RACE"


def test_convert_and_validate_rejects_missing_main_rest_day() -> None:
    plan_input, start_date, ai_output = make_case()
    output_without_rest = AITrainingPlanOutput(
        workouts=ai_output.workouts[1:]
    )

    with pytest.raises(ValueError, match="Thiếu dòng REST"):
        convert_and_validate_ai_workouts(
            output_without_rest,
            plan_input,
            start_date,
        )


def test_convert_and_validate_rejects_duplicate_workout_date() -> None:
    plan_input, start_date, ai_output = make_case()
    duplicate_easy_run = ai_output.workouts[1].model_copy(
        update = {"workout_name": "Easy Run bổ sung"}
    )
    output_with_duplicate = AITrainingPlanOutput(
        workouts=[*ai_output.workouts, duplicate_easy_run]
    )

    with pytest.raises(ValueError, match="một dòng workout mỗi ngày"):
        convert_and_validate_ai_workouts(
            output_with_duplicate,
            plan_input,
            start_date,
        )


def test_convert_and_validate_rejects_wrong_race_distance() -> None:
    plan_input, start_date, ai_output = make_case()
    wrong_race = ai_output.workouts[-1].model_copy(
        update = {"planned_distance_m": 4_000}
    )

    output_with_wrong_race = AITrainingPlanOutput(
        workouts=[*ai_output.workouts[:-1], wrong_race]
    )

    with pytest.raises(ValueError, match="khớp cự ly mục tiêu"):
        convert_and_validate_ai_workouts(
            output_with_wrong_race,
            plan_input,
            start_date,
        )