SET XACT_ABORT ON;
GO

BEGIN TRY
    BEGIN TRANSACTION;

    IF OBJECT_ID(N'dbo.StrengthSessions', N'U') IS NULL
    BEGIN
        THROW 50030, 'dbo.StrengthSessions does not exist.', 1;
    END;

    IF OBJECT_ID(N'dbo.StrengthExercises', N'U') IS NULL
    BEGIN
        CREATE TABLE dbo.StrengthExercises
        (
            StrengthExerciseID BIGINT IDENTITY(1, 1) NOT NULL,
            StrengthSessionID BIGINT NOT NULL,
            ExternalStrengthID VARCHAR(100) NOT NULL,
            ExerciseName NVARCHAR(200) NOT NULL,
            ExerciseCategory NVARCHAR(100) NULL,
            PlannedLoadKg DECIMAL(8, 2) NULL,
            PlannedSets SMALLINT NULL,
            PlannedRepsOrDuration NVARCHAR(100) NULL,
            PlannedVolumeKg DECIMAL(12, 2) NULL,
            PlannedRPE TINYINT NULL,
            ExerciseSide NVARCHAR(30) NULL,
            CoachingNotes NVARCHAR(1000) NULL,
            ExerciseCompleted BIT NOT NULL,
            ImportedAt DATETIME2(0) NOT NULL
                CONSTRAINT DF_StrengthExercises_ImportedAt
                DEFAULT SYSUTCDATETIME(),

            CONSTRAINT PK_StrengthExercises
                PRIMARY KEY CLUSTERED (StrengthExerciseID),

            CONSTRAINT FK_StrengthExercises_StrengthSessions
                FOREIGN KEY (StrengthSessionID)
                REFERENCES dbo.StrengthSessions (StrengthSessionID),

            CONSTRAINT UQ_StrengthExercises_Session_External
                UNIQUE (StrengthSessionID, ExternalStrengthID),

            CONSTRAINT CK_StrengthExercises_Load
                CHECK (PlannedLoadKg IS NULL OR PlannedLoadKg >= 0),

            CONSTRAINT CK_StrengthExercises_Sets
                CHECK (PlannedSets IS NULL OR PlannedSets > 0),

            CONSTRAINT CK_StrengthExercises_Volume
                CHECK (PlannedVolumeKg IS NULL OR PlannedVolumeKg >= 0),

            CONSTRAINT CK_StrengthExercises_RPE
                CHECK (PlannedRPE IS NULL OR PlannedRPE BETWEEN 1 AND 10)
        );
    END;

    IF NOT EXISTS
    (
        SELECT 1
        FROM sys.indexes
        WHERE object_id = OBJECT_ID(N'dbo.StrengthExercises')
          AND name = N'IX_StrengthExercises_Session'
    )
    BEGIN
        CREATE NONCLUSTERED INDEX IX_StrengthExercises_Session
            ON dbo.StrengthExercises
            (
                StrengthSessionID,
                StrengthExerciseID
            )
            INCLUDE
            (
                ExerciseName,
                PlannedSets,
                PlannedRepsOrDuration,
                ExerciseCompleted
            );
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
