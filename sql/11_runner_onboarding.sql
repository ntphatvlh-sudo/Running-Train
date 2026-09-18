USE [RunningTrainingDB];
GO

SET XACT_ABORT ON;
GO

BEGIN TRY
    BEGIN TRANSACTION;

    IF OBJECT_ID(N'dbo.Athletes', N'U') IS NULL
    BEGIN
        THROW 50030,
            'dbo.Athletes does not exist.',
            1;
    END;

    IF OBJECT_ID(N'dbo.AppUsers', N'U') IS NULL
    BEGIN
        THROW 50031,
            'dbo.AppUsers does not exist.',
            1;
    END;

    IF OBJECT_ID(
        N'dbo.AthleteUserAccess',
        N'U'
    ) IS NULL
    BEGIN
        THROW 50032,
            'dbo.AthleteUserAccess does not exist.',
            1;
    END;

    IF COL_LENGTH(
        N'dbo.AppUsers',
        N'OnboardingCompletedAt'
    ) IS NULL
    BEGIN
        THROW 50033,
            'AppUsers.OnboardingCompletedAt does not exist.',
            1;
    END;

    IF OBJECT_ID(
        N'dbo.AthletePersonalRecords',
        N'U'
    ) IS NULL
    BEGIN
        CREATE TABLE dbo.AthletePersonalRecords
        (
            PersonalRecordID BIGINT
                IDENTITY(1, 1) NOT NULL,

            AthleteID INT NOT NULL,

            DistanceM INT NOT NULL,

            CompletedDurationSec INT NOT NULL,

            AchievedDate DATE NOT NULL,

            RecordSource VARCHAR(30) NOT NULL
                CONSTRAINT DF_AthletePersonalRecords_Source
                DEFAULT ('SELF_REPORTED'),

            CreatedAt DATETIME2(0) NOT NULL
                CONSTRAINT DF_AthletePersonalRecords_CreatedAt
                DEFAULT (SYSUTCDATETIME()),

            CONSTRAINT PK_AthletePersonalRecords
                PRIMARY KEY (PersonalRecordID),

            CONSTRAINT FK_AthletePersonalRecords_Athletes
                FOREIGN KEY (AthleteID)
                REFERENCES dbo.Athletes (AthleteID),

            CONSTRAINT CK_AthletePersonalRecords_Distance
                CHECK (DistanceM > 0),

            CONSTRAINT CK_AthletePersonalRecords_Duration
                CHECK (CompletedDurationSec > 0)
        );
    END;

    IF NOT EXISTS
    (
        SELECT 1
        FROM sys.indexes
        WHERE object_id =
              OBJECT_ID(N'dbo.AthletePersonalRecords')
          AND name =
              N'IX_AthletePersonalRecords_Latest'
    )
    BEGIN
        CREATE NONCLUSTERED INDEX
            IX_AthletePersonalRecords_Latest
        ON dbo.AthletePersonalRecords
        (
            AthleteID,
            AchievedDate DESC,
            PersonalRecordID DESC
        );
    END;

    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0
    BEGIN
        ROLLBACK TRANSACTION;
    END;

    THROW;
END CATCH;
GO

CREATE OR ALTER PROCEDURE dbo.usp_CompleteRunnerOnboarding
    @AppUserID INT,
    @FullName NVARCHAR(150),
    @DateOfBirth DATE = NULL,
    @Sex VARCHAR(20) = NULL,
    @HeightCm DECIMAL(5, 2),
    @WeightKg DECIMAL(5, 2),
    @LatestPRDistanceM INT,
    @LatestPRCompletedDurationSec INT,
    @LatestPRAchievedDate DATE
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    SET @FullName =
        NULLIF(LTRIM(RTRIM(@FullName)), N'');

    SET @Sex =
        NULLIF(LTRIM(RTRIM(@Sex)), '');

    DECLARE @Today DATE =
        CONVERT(
            DATE,
            SYSUTCDATETIME()
                AT TIME ZONE 'UTC'
                AT TIME ZONE 'SE Asia Standard Time'
        );

    IF @AppUserID IS NULL OR @AppUserID <= 0
    BEGIN
        THROW 50034,
            'A valid AppUserID is required.',
            1;
    END;

    IF @FullName IS NULL
    BEGIN
        THROW 50035,
            'Full name is required.',
            1;
    END;

    IF
    (
        @HeightCm IS NULL
        OR @HeightCm < 80
        OR @HeightCm > 250
    )
    BEGIN
        THROW 50036,
            'Height must be between 80 and 250 cm.',
            1;
    END;

    IF
    (
        @WeightKg IS NULL
        OR @WeightKg < 25
        OR @WeightKg > 300
    )
    BEGIN
        THROW 50037,
            'Weight must be between 25 and 300 kg.',
            1;
    END;

    IF
    (
        @DateOfBirth IS NOT NULL
        AND @DateOfBirth >
            @Today
    )
    BEGIN
        THROW 50038,
            'Date of birth cannot be in the future.',
            1;
    END;

    IF
    (
        @LatestPRDistanceM IS NULL
        OR @LatestPRDistanceM <= 0
    )
    BEGIN
        THROW 50039,
            'Latest PR distance must be positive.',
            1;
    END;

    IF
    (
        @LatestPRCompletedDurationSec IS NULL
        OR @LatestPRCompletedDurationSec <= 0
    )
    BEGIN
        THROW 50040,
            'Latest PR completed time must be positive.',
            1;
    END;

    IF
    (
        @LatestPRAchievedDate IS NULL
        OR @LatestPRAchievedDate >
            @Today
    )
    BEGIN
        THROW 50041,
            'Latest PR date is required and cannot be in the future.',
            1;
    END;

    IF
    (
        @DateOfBirth IS NOT NULL
        AND @LatestPRAchievedDate < @DateOfBirth
    )
    BEGIN
        THROW 50042,
            'Latest PR date cannot be before date of birth.',
            1;
    END;

        DECLARE @Now DATETIME2(0) =
        SYSUTCDATETIME();

    DECLARE @EmailNormalized NVARCHAR(320);
    DECLARE @IsActive BIT;
    DECLARE @OnboardingCompletedAt DATETIME2(0);
    DECLARE @InvitationID INT;
    DECLARE @InvitedByAppUserID INT;
    DECLARE @AthleteID INT;
    DECLARE @PersonalRecordID BIGINT;

    BEGIN TRY
        BEGIN TRANSACTION;

        SELECT
            @EmailNormalized = EmailNormalized,
            @IsActive = IsActive,
            @OnboardingCompletedAt =
                OnboardingCompletedAt
        FROM dbo.AppUsers WITH (UPDLOCK, HOLDLOCK)
        WHERE AppUserID = @AppUserID;

        IF @EmailNormalized IS NULL
        BEGIN
            THROW 50043,
                'App user does not exist.',
                1;
        END;

        IF @IsActive <> 1
        BEGIN
            THROW 50044,
                'App user is not active.',
                1;
        END;

        IF @OnboardingCompletedAt IS NOT NULL
        BEGIN
            THROW 50045,
                'Runner onboarding is already complete.',
                1;
        END;

        SELECT TOP (1)
            @InvitationID = InvitationID,
            @InvitedByAppUserID =
                InvitedByAppUserID
        FROM dbo.Invitations WITH (UPDLOCK, HOLDLOCK)
        WHERE EmailNormalized = @EmailNormalized
          AND AthleteID IS NULL
          AND AccessRole = 'RUNNER'
          AND Status = 'ACCEPTED'
        ORDER BY
            AcceptedAt DESC,
            InvitationID DESC;

        IF @InvitationID IS NULL
        BEGIN
            THROW 50046,
                'No accepted runner onboarding invitation exists.',
                1;
        END;

        IF EXISTS
        (
            SELECT 1
            FROM dbo.AthleteUserAccess
                 WITH (UPDLOCK, HOLDLOCK)
            WHERE AppUserID = @AppUserID
              AND AccessRole = 'RUNNER'
              AND IsActive = 1
        )
        BEGIN
            THROW 50047,
                'App user already has active runner access.',
                1;
        END;

        INSERT INTO dbo.Athletes
        (
            FullName,
            DateOfBirth,
            Sex,
            HeightCm,
            WeightKg
        )
        VALUES
        (
            @FullName,
            @DateOfBirth,
            @Sex,
            @HeightCm,
            @WeightKg
        );

        SET @AthleteID =
            CONVERT(INT, SCOPE_IDENTITY());

        INSERT INTO dbo.AthletePersonalRecords
        (
            AthleteID,
            DistanceM,
            CompletedDurationSec,
            AchievedDate
        )
        VALUES
        (
            @AthleteID,
            @LatestPRDistanceM,
            @LatestPRCompletedDurationSec,
            @LatestPRAchievedDate
        );

        SET @PersonalRecordID =
            CONVERT(BIGINT, SCOPE_IDENTITY());

        INSERT INTO dbo.AthleteUserAccess
        (
            AppUserID,
            AthleteID,
            AccessRole,
            IsActive,
            GrantedByAppUserID
        )
        VALUES
        (
            @AppUserID,
            @AthleteID,
            'RUNNER',
            1,
            @InvitedByAppUserID
        );

        UPDATE dbo.Invitations
        SET AthleteID = @AthleteID
        WHERE InvitationID = @InvitationID;

        UPDATE dbo.AppUsers
        SET OnboardingCompletedAt = @Now
        WHERE AppUserID = @AppUserID;

        COMMIT TRANSACTION;

        SELECT
            @AppUserID AS AppUserID,
            CAST('AUTHORIZED' AS VARCHAR(30))
                AS AuthorizationState,
            @AthleteID AS AthleteID,
            @FullName AS FullName,
            CAST('RUNNER' AS VARCHAR(20))
                AS AccessRole,
            @PersonalRecordID AS PersonalRecordID,
            @LatestPRDistanceM AS LatestPRDistanceM,
            @LatestPRCompletedDurationSec
                AS LatestPRCompletedDurationSec,
            @LatestPRAchievedDate
                AS LatestPRAchievedDate;
    END TRY
    BEGIN CATCH
        IF XACT_STATE() <> 0
        BEGIN
            ROLLBACK TRANSACTION;
        END;

        THROW;
    END CATCH;
END;
GO