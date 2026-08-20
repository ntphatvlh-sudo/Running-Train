USE RunningTrainingDB;
GO

CREATE OR ALTER PROCEDURE dbo.usp_RebuildRuleWorkoutMatches
    @AthleteID INT = 1,
    @PlanID INT = NULL,
    @MaximumDayDifference INT = 1,
    @MinimumConfidence DECIMAL(5,4) = 0.7000
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    IF @MaximumDayDifference NOT BETWEEN 0 AND 7
    BEGIN
        THROW 50001,
            'MaximumDayDifference phải từ 0 đến 7.',
            1;
    END;

    IF @MinimumConfidence NOT BETWEEN 0 AND 1
    BEGIN
        THROW 50002,
            'MinimumConfidence phải từ 0 đến 1.',
            1;
    END;

    DECLARE @Removed TABLE
    (
        PlannedWorkoutID BIGINT
    );

    DECLARE @AutoMatchResult TABLE
    (
        MatchesCreated INT
    );

    BEGIN TRY
        BEGIN TRANSACTION;

        DECLARE @RequiresRebuild BIT = 0;
        DECLARE @RuleMatchesRemoved INT = 0;

        /*
            Rebuild only when an unmatched exact-date run exists while the
            workout is occupied by a RULE match from another date. This
            repairs stale greedy matches without changing stable match IDs
            during every normal import.
        */
        IF EXISTS
        (
            SELECT 1
            FROM dbo.PlannedWorkouts AS pw
            JOIN dbo.TrainingPlans AS tp
                ON tp.PlanID = pw.PlanID
            JOIN dbo.WorkoutMatches AS occupied
                ON occupied.PlannedWorkoutID = pw.PlannedWorkoutID
               AND occupied.MatchMethod = 'RULE'
            JOIN dbo.CompletedActivities AS occupied_activity
                ON occupied_activity.ActivityID = occupied.ActivityID
            JOIN dbo.CompletedActivities AS exact_activity
                ON exact_activity.AthleteID = tp.AthleteID
               AND exact_activity.ActivityType = 'RUN'
               AND exact_activity.ActivityDate = pw.ScheduledDate
            WHERE tp.AthleteID = @AthleteID
              AND
              (
                  @PlanID IS NULL
                  OR pw.PlanID = @PlanID
              )
              AND pw.PlannedDistanceM > 0
              AND occupied_activity.ActivityDate <> pw.ScheduledDate
              AND NOT EXISTS
              (
                  SELECT 1
                  FROM dbo.WorkoutMatches AS existing
                  WHERE existing.ActivityID = exact_activity.ActivityID
              )
        )
        BEGIN
            SET @RequiresRebuild = 1;
        END;

        /*
            Remove only automatic matches in the selected scope.
            MANUAL matches are deliberate user decisions and remain intact.
        */
        IF @RequiresRebuild = 1
        BEGIN
            DELETE wm
            OUTPUT deleted.PlannedWorkoutID
                INTO @Removed (PlannedWorkoutID)
            FROM dbo.WorkoutMatches AS wm
            JOIN dbo.PlannedWorkouts AS pw
                ON pw.PlannedWorkoutID = wm.PlannedWorkoutID
            JOIN dbo.TrainingPlans AS tp
                ON tp.PlanID = pw.PlanID
            WHERE wm.MatchMethod = 'RULE'
              AND tp.AthleteID = @AthleteID
              AND
              (
                  @PlanID IS NULL
                  OR pw.PlanID = @PlanID
              );

            SET @RuleMatchesRemoved = @@ROWCOUNT;

            /*
                A removed RULE match no longer proves completion. Reset only
                those workouts; Auto Match recalculates them below.
            */
            UPDATE pw
            SET Status = 'PLANNED'
            FROM dbo.PlannedWorkouts AS pw
            JOIN @Removed AS removed
                ON removed.PlannedWorkoutID = pw.PlannedWorkoutID
            WHERE NOT EXISTS
            (
                SELECT 1
                FROM dbo.WorkoutMatches AS wm
                WHERE wm.PlannedWorkoutID = pw.PlannedWorkoutID
            );
        END;

        INSERT INTO @AutoMatchResult (MatchesCreated)
        EXEC dbo.usp_AutoMatchWorkouts
            @AthleteID = @AthleteID,
            @PlanID = @PlanID,
            @MaximumDayDifference = @MaximumDayDifference,
            @MinimumConfidence = @MinimumConfidence;

        DECLARE @RuleMatchesCreated INT =
        (
            SELECT COALESCE(MAX(MatchesCreated), 0)
            FROM @AutoMatchResult
        );

        COMMIT TRANSACTION;

        SELECT
            @RuleMatchesRemoved AS RuleMatchesRemoved,
            @RuleMatchesCreated AS RuleMatchesCreated;
    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT > 0
            ROLLBACK TRANSACTION;

        THROW;
    END CATCH;
END;
GO

/* Preview: all changes are rolled back. */
BEGIN TRANSACTION;

EXEC dbo.usp_RebuildRuleWorkoutMatches
    @AthleteID = 1,
    @PlanID = NULL,
    @MaximumDayDifference = 1,
    @MinimumConfidence = 0.7000;

SELECT
    ca.ActivityID,
    ca.ActivityDate,
    ca.DistanceM,
    ca.DurationSec,
    wm.WorkoutMatchID,
    wm.PlannedWorkoutID,
    pw.ScheduledDate,
    pw.WorkoutName,
    wm.MatchMethod,
    wm.MatchConfidence
FROM dbo.CompletedActivities AS ca
LEFT JOIN dbo.WorkoutMatches AS wm
    ON wm.ActivityID = ca.ActivityID
LEFT JOIN dbo.PlannedWorkouts AS pw
    ON pw.PlannedWorkoutID = wm.PlannedWorkoutID
WHERE ca.ActivityDate BETWEEN
      CONVERT(DATE, '20260806', 112)
      AND CONVERT(DATE, '20260810', 112)
ORDER BY ca.ActivityDate, ca.ActivityID;

ROLLBACK TRANSACTION;
GO
