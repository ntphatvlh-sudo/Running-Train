SET XACT_ABORT ON;
GO

BEGIN TRY
    BEGIN TRANSACTION;

    IF OBJECT_ID(
        N'dbo.CompletedActivities',
        N'U'
    ) IS NULL
    BEGIN
        THROW 50001,
            'dbo.CompletedActivities does not exist.',
            1;
    END;

    IF EXISTS
    (
        SELECT
            AthleteID,
            ExternalSource,
            ExternalActivityID
        FROM dbo.CompletedActivities
        WHERE ExternalActivityID IS NOT NULL
        GROUP BY
            AthleteID,
            ExternalSource,
            ExternalActivityID
        HAVING COUNT(*) > 1
    )
    BEGIN
        THROW 50002,
            'Duplicate external activity IDs exist within an athlete.',
            1;
    END;

    IF NOT EXISTS
    (
        SELECT 1
        FROM sys.indexes
        WHERE object_id =
            OBJECT_ID(N'dbo.CompletedActivities')
          AND name =
            N'UX_Activities_Athlete_Source_ExternalID'
    )
    BEGIN
        CREATE UNIQUE NONCLUSTERED INDEX
            [UX_Activities_Athlete_Source_ExternalID]
        ON [dbo].[CompletedActivities]
        (
            [AthleteID] ASC,
            [ExternalSource] ASC,
            [ExternalActivityID] ASC
        )
        WHERE [ExternalActivityID] IS NOT NULL;
    END;

    IF EXISTS
    (
        SELECT 1
        FROM sys.indexes
        WHERE object_id =
            OBJECT_ID(N'dbo.CompletedActivities')
          AND name =
            N'UX_Activities_Source_ExternalID'
    )
    BEGIN
        DROP INDEX
            [UX_Activities_Source_ExternalID]
        ON [dbo].[CompletedActivities];
    END;

    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0
    BEGIN
        ROLLBACK TRANSACTION;
    END;

    THROW;
END CATCH;
GO