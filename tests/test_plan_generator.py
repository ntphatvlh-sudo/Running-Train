from datetime import date

import pytest
from dataclasses import replace
from datetime import date

from src.plan_generator import (
    TrainingPlanInput,
    generate_first_week,
    GeneratedTrainingPlan,
    PlannedMobility,
    validate_generated_plan,
    build_daily_mobility,
)


def make_input(has_injury: bool = False) -> TrainingPlanInput:
    return TrainingPlanInput(
        race_date=date(2027, 1, 24),
        goal_distance_m=10_000,
        latest_pr_distance_m=10_000,
        latest_pr_duration_sec=5_100,
        runs_per_week=4,
        plan_level="BEGINNER",
        rest_weekday=0,
        has_injury=has_injury,
    )


def test_generate_first_week_returns_four_runs() -> None:
    workouts = generate_first_week(
        make_input(),
        date(2026, 9, 28),
    )

    assert len(workouts) == 4
    assert sum(w.planned_distance_m or 0 for w in workouts) == 17_000
    assert workouts[0].workout_type == "EASY"
    assert workouts[2].workout_type == "LONG_RUN"


def test_injury_blocks_plan_generation() -> None:
    with pytest.raises(
        ValueError,
        match="Plan generation is blocked while an injury is reported",
    ):
        generate_first_week(
            make_input(has_injury=True),
            date(2026, 9, 28),
        )

from src.plan_generator import generate_full_plan


def test_generate_full_plan_respects_current_date() -> None:
    workouts = generate_full_plan(make_input())

    assert workouts
    assert len(workouts) <= 68
    assert all(
        workout.scheduled_date >= date.today()
        for workout in workouts
    )
    assert workouts[-1].scheduled_date == date(2027, 1, 24)
    assert sum(
        workout.workout_type == "RACE"
        for workout in workouts
    ) == 1


from datetime import timedelta


def test_short_plan_does_not_include_past_workouts() -> None:
    today = date.today()
    race_date = today + timedelta(days=20)

    workouts = generate_full_plan(
        replace(make_input(), race_date=race_date)
    )

    assert workouts
    assert all(workout.scheduled_date >= today for workout in workouts)
    assert workouts[-1].scheduled_date == race_date
    assert sum(workout.workout_type == "RACE" for workout in workouts) == 1

def test_validate_generated_plan_accepts_mobility_every_day() -> None:
    start_date = date(2026, 10, 1)
    race_date = date(2026, 10, 3)
    plan = GeneratedTrainingPlan(
        workouts=[],
        daily_mobility=[
            PlannedMobility(start_date, "Routine ngày 1"),
            PlannedMobility(date(2026, 10, 2), "Routine ngày 2"),
            PlannedMobility(race_date, "Mobility nhẹ ngày thi"),
        ],
    )

    validate_generated_plan(plan, start_date, race_date)

def test_validate_generated_plan_rejects_missing_mobility_day() -> None:
    start_date = date(2026, 10, 1)
    race_date = date(2026, 10, 3)
    plan = GeneratedTrainingPlan(
        workouts=[],
        daily_mobility=[
            PlannedMobility(start_date, "Routine ngày 1"),
            PlannedMobility(race_date, "Mobility nhẹ ngày thi"),
        ],
    )

    with pytest.raises(
        ValueError,
        match="exactly one mobility routine for each day",
    ):
        validate_generated_plan(plan, start_date, race_date)


def test_validate_generated_plan_rejects_duplicate_mobility_day() -> None:
    start_date = date(2026, 10, 1)
    race_date = date(2026, 10, 2)
    plan = GeneratedTrainingPlan(
        workouts=[],
        daily_mobility=[
            PlannedMobility(start_date, "Routine 1"),
            PlannedMobility(start_date, "Routine trùng ngày"),
        ],
    )

    with pytest.raises(
        ValueError,
        match="exactly one mobility routine for each day",
    ):
        validate_generated_plan(plan, start_date, race_date)

def test_validate_generated_plan_rejects_empty_mobility_routine() -> None:
    plan_date = date(2026, 10, 1)
    plan = GeneratedTrainingPlan(
        workouts=[],
        daily_mobility=[
            PlannedMobility(plan_date, "   "),
        ],
    )

    with pytest.raises(
        ValueError,
        match="Mobility routine must not be empty",
    ):
        validate_generated_plan(plan, plan_date, plan_date)

def test_build_daily_mobility_uses_recovery_on_rest_day() -> None:
    start_date = date(2026, 10, 12)  # Thứ Hai
    race_date = date(2026, 10, 14)

    daily_mobility = build_daily_mobility(
        start_date=start_date,
        race_date=race_date,
        rest_weekday=0,
        activation_routine="Activation mẫu",
        recovery_routine="Recovery mẫu",
    )

    assert [item.scheduled_date for item in daily_mobility] == [
        date(2026, 10, 12),
        date(2026, 10, 13),
        date(2026, 10, 14),
    ]
    assert [item.routine for item in daily_mobility] == [
        "Recovery mẫu",
        "Activation mẫu",
        "Activation mẫu",
    ]

    plan = GeneratedTrainingPlan(
        workouts=[],
        daily_mobility=daily_mobility,
    )
    validate_generated_plan(plan, start_date, race_date)