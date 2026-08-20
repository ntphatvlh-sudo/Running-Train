USE RunningTrainingDB;
GO

/*
    Completion source priority:
    1. An explicit RULE/MANUAL row in WorkoutMatches.
    2. A one-to-one same-date fallback for activities that have no match.

    This preserves the actual activity date while allowing an explicitly
    matched activity from a nearby date to complete the planned workout.
*/
ALTER VIEW dbo.vw_RunWorkoutCompletion
AS
WITH Planned AS
(
    SELECT
        PlannedWorkoutID,
        PlanID,
        AthleteID,
        ScheduledDate,
        WorkoutType,
        WorkoutName,
        PlannedDistanceM,
        PlannedDurationSec,
        TargetPaceSecPerKm
    FROM dbo.vw_PlannedRunWorkouts
),
ExplicitMatches AS
(
    SELECT DISTINCT
        wm.PlannedWorkoutID
    FROM dbo.WorkoutMatches AS wm
    JOIN dbo.CompletedActivities AS ca
        ON ca.ActivityID = wm.ActivityID
    WHERE ca.ActivityType = 'RUN'
),
FallbackCandidates AS
(
    SELECT
        planned.PlannedWorkoutID,
        activity.ActivityID,
        ROW_NUMBER() OVER
        (
            PARTITION BY planned.PlannedWorkoutID
            ORDER BY
                ABS(
                    COALESCE(activity.DistanceM, 0)
                    - COALESCE(planned.PlannedDistanceM, 0)
                ),
                ABS(
                    COALESCE(activity.DurationSec, 0)
                    - COALESCE(planned.PlannedDurationSec, 0)
                ),
                activity.ActivityID
        ) AS WorkoutRank,
        ROW_NUMBER() OVER
        (
            PARTITION BY activity.ActivityID
            ORDER BY
                ABS(
                    COALESCE(activity.DistanceM, 0)
                    - COALESCE(planned.PlannedDistanceM, 0)
                ),
                ABS(
                    COALESCE(activity.DurationSec, 0)
                    - COALESCE(planned.PlannedDurationSec, 0)
                ),
                planned.PlannedWorkoutID
        ) AS ActivityRank
    FROM Planned AS planned
    JOIN dbo.CompletedActivities AS activity
        ON activity.AthleteID = planned.AthleteID
       AND activity.ActivityType = 'RUN'
       AND activity.ActivityDate = planned.ScheduledDate
    WHERE NOT EXISTS
    (
        SELECT 1
        FROM dbo.WorkoutMatches AS wm
        WHERE wm.PlannedWorkoutID = planned.PlannedWorkoutID
    )
      AND NOT EXISTS
    (
        SELECT 1
        FROM dbo.WorkoutMatches AS wm
        WHERE wm.ActivityID = activity.ActivityID
    )
),
FallbackMatches AS
(
    SELECT PlannedWorkoutID
    FROM FallbackCandidates
    WHERE WorkoutRank = 1
      AND ActivityRank = 1
)
SELECT
    planned.PlannedWorkoutID,
    planned.PlanID,
    planned.AthleteID,
    planned.ScheduledDate,
    planned.WorkoutType,
    planned.WorkoutName,
    planned.PlannedDistanceM,
    planned.PlannedDurationSec,
    planned.TargetPaceSecPerKm,
    CASE
        WHEN explicit.PlannedWorkoutID IS NOT NULL
          OR fallback.PlannedWorkoutID IS NOT NULL
            THEN CAST(1 AS BIT)
        ELSE CAST(0 AS BIT)
    END AS IsCompleted
FROM Planned AS planned
LEFT JOIN ExplicitMatches AS explicit
    ON explicit.PlannedWorkoutID = planned.PlannedWorkoutID
LEFT JOIN FallbackMatches AS fallback
    ON fallback.PlannedWorkoutID = planned.PlannedWorkoutID;
GO

SELECT
    PlannedWorkoutID,
    PlanID,
    ScheduledDate,
    WorkoutType,
    WorkoutName,
    PlannedDistanceM,
    IsCompleted
FROM dbo.vw_RunWorkoutCompletion
WHERE ScheduledDate = CONVERT(DATE, '20260806', 112);
GO

SELECT
    PlanID,
    DueRunWorkouts,
    CompletedRunWorkouts,
    CompletionRate
FROM dbo.vw_PlanRunCompletion;
GO
