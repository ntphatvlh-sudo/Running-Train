from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class TenKPlanInput:
    race_date: date
    runs_per_week: int
    plan_level: str
    recent_10k_seconds: int
    rest_weekday: int
    has_injury: bool = False

    def validate(self) -> None:
        if self.race_date <= date.today():
            raise ValueError("Race date must be in the future.")

        if self.runs_per_week != 4:
            raise ValueError("The first 10K MVP requires exactly 4 runs per week.")

        if self.plan_level not in {"BEGINNER", "INTERMEDIATE", "ADVANCED"}:
            raise ValueError("Invalid plan level.")

        if self.recent_10k_seconds <= 0:
            raise ValueError("Recent 10K time must be positive.")

        if not 0 <= self.rest_weekday <= 6:
            raise ValueError("rest_weekday must be between 0 and 6.")

        if self.has_injury:
            raise ValueError(
                "Plan generation is blocked while an injury is reported."
            )

from datetime import timedelta


@dataclass(frozen=True)
class TrainingPlanInput:
    race_date: date
    goal_distance_m: int
    latest_pr_distance_m: int
    latest_pr_duration_sec: int
    runs_per_week: int
    plan_level: str
    rest_weekday: int
    has_injury: bool = False
    target_duration_sec: int | None = None

    def validate(self) -> None:
        if self.race_date <= date.today():
            raise ValueError("Race date must be in the future.")

        if self.latest_pr_distance_m <= 0:
            raise ValueError("Latest PR distance must be positive.")

        if self.latest_pr_duration_sec <= 0:
            raise ValueError("Latest PR duration must be positive.")

        if (
            self.target_duration_sec is not None
            and self.target_duration_sec <= 0
        ):
            raise ValueError("Target duration must be positive when provided.")

        allowed_distances = {
            "BEGINNER": {5_000, 10_000},
            "INTERMEDIATE": {10_000, 21_097},
            "ADVANCED": {21_097, 42_195},
        }
        allowed_runs_per_week = {
            "BEGINNER": {4},
            "INTERMEDIATE": {4, 5},
            "ADVANCED": {5},
        }

        if self.plan_level not in allowed_distances:
            raise ValueError("Invalid plan level.")

        if self.goal_distance_m not in allowed_distances[self.plan_level]:
            raise ValueError(
                "Goal distance is not supported for this plan level."
            )

        if self.runs_per_week not in allowed_runs_per_week[self.plan_level]:
            raise ValueError(
                "Runs per week is not supported for this plan level."
            )

        if not 0 <= self.rest_weekday <= 6:
            raise ValueError("rest_weekday must be between 0 and 6.")

        if self.has_injury:
            raise ValueError(
                "Plan generation is blocked while an injury is reported."
            )

    @property
    def latest_pr_pace_sec_per_km(self) -> int:
        return round(
            self.latest_pr_duration_sec * 1_000
            / self.latest_pr_distance_m
        )

    @property
    def estimated_goal_duration_sec(self) -> int:
        # Ước lượng tham khảo từ PR; chưa phải dự báo thành tích chính xác.
        if self.target_duration_sec is not None:
            return self.target_duration_sec
        return round(
            self.latest_pr_duration_sec
            * (self.goal_distance_m / self.latest_pr_distance_m) ** 1.06
        )

    @property
    def estimated_goal_pace_sec_per_km(self) -> int:
        return round(
            self.estimated_goal_duration_sec
            * 1_000
            / self.goal_distance_m
        )


@dataclass(frozen=True)
class PlannedWorkout:
    scheduled_date: date
    workout_type: str
    workout_name: str
    planned_distance_m: int | None
    planned_duration_sec: int | None
    target_pace_sec_per_km: int | None
    notes: str

@dataclass(frozen=True)
class PlannedMobility:
    scheduled_date: date
    routine: str

@dataclass(frozen=True)
class GeneratedTrainingPlan:
    workouts: list[PlannedWorkout]
    daily_mobility: list[PlannedMobility]

def build_daily_mobility(
    start_date: date,
    race_date: date,
    rest_weekday: int,
    activation_routine: str,
    recovery_routine: str,
) -> list[PlannedMobility]:
    if start_date > race_date:
        raise ValueError("Plan start date must not be after race date.")

    if not 0 <= rest_weekday <= 6:
        raise ValueError("rest_weekday must be between 0 and 6.")

    if not activation_routine.strip() or not recovery_routine.strip():
        raise ValueError("Mobility routine must not be empty.")

    daily_mobility = []

    for offset in range((race_date - start_date).days + 1):
        scheduled_date = start_date + timedelta(days=offset)
        is_rest_day = (
            scheduled_date.weekday() == rest_weekday
            and scheduled_date != race_date
        )
        routine = recovery_routine if is_rest_day else activation_routine

        daily_mobility.append(
            PlannedMobility(
                scheduled_date=scheduled_date,
                routine=routine,
            )
        )

    return daily_mobility

def validate_generated_plan(
    plan: GeneratedTrainingPlan,
    start_date: date,
    race_date: date,
) -> None:
    if start_date > race_date:
        raise ValueError("Plan start date must not be after race date.")

    expected_dates = {
        start_date + timedelta(days=offset)
        for offset in range((race_date - start_date).days + 1)
    }
    mobility_dates = [
        item.scheduled_date
        for item in plan.daily_mobility
    ]

    if (
        len(mobility_dates) != len(expected_dates)
        or set(mobility_dates) != expected_dates
    ):
        raise ValueError(
            "Plan must contain exactly one mobility routine for each day."
        )

    if any(
        not item.routine.strip()
        for item in plan.daily_mobility
    ):
        raise ValueError("Mobility routine must not be empty.")


def generate_first_week(
    plan_input: TrainingPlanInput,
    start_date: date,
) -> list[PlannedWorkout]:
    plan_input.validate()

    workouts = [
        PlannedWorkout(
            scheduled_date=start_date + timedelta(days=1),
            workout_type="EASY",
            workout_name="Easy Run",
            planned_distance_m=4_000,
            planned_duration_sec=2_160,
            target_pace_sec_per_km=540,
            notes="Chạy nhẹ, có thể nói chuyện.",
        ),
        PlannedWorkout(
            scheduled_date=start_date + timedelta(days=3),
            workout_type="EASY",
            workout_name="Steady Run",
            planned_distance_m=4_000,
            planned_duration_sec=2_160,
            target_pace_sec_per_km=540,
            notes="Giữ tốc độ ổn định, không gắng sức.",
        ),
        PlannedWorkout(
            scheduled_date=start_date + timedelta(days=5),
            workout_type="LONG_RUN",
            workout_name="Long Run",
            planned_distance_m=6_000,
            planned_duration_sec=3_420,
            target_pace_sec_per_km=570,
            notes="Chạy chậm và kiểm soát sức.",
        ),
        PlannedWorkout(
            scheduled_date=start_date + timedelta(days=6),
            workout_type="RECOVERY",
            workout_name="Recovery Run",
            planned_distance_m=3_000,
            planned_duration_sec=1_800,
            target_pace_sec_per_km=600,
            notes="Chạy rất nhẹ; dừng nếu có đau bất thường.",
        ),
    ]

    return workouts

def generate_full_plan(
    plan_input: TrainingPlanInput,
) -> list[PlannedWorkout]:
    plan_input.validate()

    race_date = plan_input.race_date
    full_plan_start = (
        race_date - timedelta(weeks=17) + timedelta(days=1)
    )

    today_week_start = date.today() - timedelta(
        days=date.today().weekday()
    )

    start_date = max(full_plan_start, today_week_start)

    week_count = (
        race_date - start_date
    ).days // 7 + 1

    weekly_factors = [
        1.00, 1.08, 1.15, 0.90,
        1.10, 1.18, 1.25, 0.95,
        1.15, 1.22, 1.30, 1.00,
        1.18, 1.25, 1.30, 0.75,
        0.55,
    ]

    base_distances = [4_000, 4_000, 6_000, 3_000]
    workouts: list[PlannedWorkout] = []

    for week_number, factor in enumerate(
        weekly_factors[:week_count],
        start=1,
    ):
        week_start = start_date + timedelta(weeks=week_number - 1)

        distance_scale = (
            plan_input.goal_distance_m / 10_000
        ) ** 0.5

        distances = [
            round(distance * distance_scale * factor / 500) * 500
            for distance in base_distances
        ]

        if week_number == week_count:
            distances = [
                3_000,
                3_000,
                2_000,
                plan_input.goal_distance_m,
            ]

        day_offsets = [1, 3, 5, 6]
        workout_types = ["EASY", "EASY", "LONG_RUN", "RECOVERY"]
        workout_names = [
            "Easy Run",
            "Steady Run",
            "Long Run",
            "Recovery Run",
        ]

        for index in range(4):
            scheduled_date = week_start + timedelta(
                days=day_offsets[index]
            )

            if week_number == week_count and index == 3:
                scheduled_date = race_date

            if week_number == week_count and index == 3:
                workout_type = "RACE"
                workout_name = f"{plan_input.goal_distance_m / 1_000:g} km Race"
                notes = "Ngày thi. Bắt đầu có kiểm soát và tăng nỗ lực dần."
                pace = plan_input.estimated_goal_pace_sec_per_km
            else:
                workout_type = workout_types[index]
                workout_name = workout_names[index]
                notes = "Run comfortably and stop if unusual pain appears."
                pace_factor = 1.15 if index < 2 else 1.22
                pace = round(plan_input.latest_pr_pace_sec_per_km * pace_factor)

            workouts.append(
                PlannedWorkout(
                    scheduled_date=scheduled_date,
                    workout_type=workout_type,
                    workout_name=workout_name,
                    planned_distance_m=distances[index],
                    planned_duration_sec=round(
                        distances[index] / 1_000 * pace
                    ),
                    target_pace_sec_per_km=pace,
                    notes=notes,
                )
            )

    return [
        workout
        for workout in workouts
        if date.today() <= workout.scheduled_date <= race_date
    ]
