from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlannedWorkoutOutput(StrictOutputModel):
    scheduled_date: str = Field(
        description="Ngày tập theo định dạng YYYY-MM-DD."
    )
    workout_type: Literal[
        "EASY",
        "RECOVERY",
        "LONG_RUN",
        "TEMPO",
        "INTERVAL",
        "RACE",
        "STRENGTH",
        "REST",
        "CROSS_TRAINING",
    ]
    workout_name: str
    planned_distance_m: int | None
    planned_duration_sec: int | None
    target_pace_sec_per_km: int | None
    notes: str


class AITrainingPlanOutput(StrictOutputModel):
    workouts: list[PlannedWorkoutOutput]