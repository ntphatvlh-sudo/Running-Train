USE [RunningTrainingDB];
GO

SET ANSI_NULLS ON;
GO

SET QUOTED_IDENTIFIER ON;
GO

CREATE OR ALTER PROCEDURE dbo.usp_CreateInvitation
    @InvitedByAppUserID INT,
    @EmailNormalized NVARCHAR(320),
    @AccessRole VARCHAR(20),
    @AthleteID INT = NULL,
    @ExpiresAt DATETIME2(0) = NULL
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    SET @EmailNormalized =
        LOWER(
            NULLIF(
                LTRIM(RTRIM(@EmailNormalized)),
                N''
            )
        );

    SET @AccessRole =
        UPPER(
            NULLIF(
                LTRIM(RTRIM(@AccessRole)),
                ''
            )
        );

    DECLARE @Now DATETIME2(0) =
        SYSUTCDATETIME();

    IF
    (
        @InvitedByAppUserID IS NULL
        OR @InvitedByAppUserID <= 0
    )
    BEGIN
        THROW 50070,
            'A valid inviting AppUserID is required.',
            1;
    END;

    IF
    (
        @EmailNormalized IS NULL
        OR @EmailNormalized NOT LIKE N'%_@_%._%'
    )
    BEGIN
        THROW 50071,
            'A valid normalized email is required.',
            1;
    END;

    IF
    (
        @AccessRole IS NULL
        OR @AccessRole NOT IN
        (
            'OWNER',
            'COACH',
            'RUNNER'
        )
    )
    BEGIN
        THROW 50072,
            'Invitation access role is invalid.',
            1;
    END;

    IF
    (
        @AthleteID IS NULL
        AND @AccessRole <> 'RUNNER'
    )
    BEGIN
        THROW 50073,
            'A new-athlete invitation must use RUNNER role.',
            1;
    END;

    IF
    (
        @ExpiresAt IS NOT NULL
        AND @ExpiresAt <= @Now
    )
    BEGIN
        THROW 50074,
            'Invitation expiry must be in the future.',
            1;
    END;


    DECLARE @InviterIsActive BIT;
    DECLARE @TargetAppUserID INT;
    DECLARE @InvitationID INT;

    BEGIN TRY
        BEGIN TRANSACTION;

        SELECT
            @InviterIsActive = IsActive
        FROM dbo.AppUsers WITH (UPDLOCK, HOLDLOCK)
        WHERE AppUserID = @InvitedByAppUserID;

        IF @InviterIsActive IS NULL
        BEGIN
            THROW 50075,
                'Inviting app user does not exist.',
                1;
        END;

        IF @InviterIsActive <> 1
        BEGIN
            THROW 50076,
                'Inviting app user is not active.',
                1;
        END;

        IF @AthleteID IS NULL
        BEGIN
            IF NOT EXISTS
            (
                SELECT 1
                FROM dbo.AthleteUserAccess
                    WITH (UPDLOCK, HOLDLOCK)
                WHERE AppUserID =
                      @InvitedByAppUserID
                  AND AccessRole = 'OWNER'
                  AND IsActive = 1
            )
            BEGIN
                THROW 50077,
                    'Only an active OWNER can invite a new runner.',
                    1;
            END;
        END;
        ELSE
        BEGIN
            IF NOT EXISTS
            (
                SELECT 1
                FROM dbo.Athletes WITH (UPDLOCK, HOLDLOCK)
                WHERE AthleteID = @AthleteID
            )
            BEGIN
                THROW 50078,
                    'Invitation athlete does not exist.',
                    1;
            END;

            IF NOT EXISTS
            (
                SELECT 1
                FROM dbo.AthleteUserAccess
                    WITH (UPDLOCK, HOLDLOCK)
                WHERE AppUserID =
                      @InvitedByAppUserID
                  AND AthleteID = @AthleteID
                  AND AccessRole = 'OWNER'
                  AND IsActive = 1
            )
            BEGIN
                THROW 50079,
                    'OWNER access is required for this athlete.',
                    1;
            END;
        END;

        DECLARE @TargetIsActive BIT;

        SELECT
            @TargetAppUserID = AppUserID,
            @TargetIsActive = IsActive
        FROM dbo.AppUsers WITH (UPDLOCK, HOLDLOCK)
        WHERE EmailNormalized = @EmailNormalized;

        IF
        (
            @TargetAppUserID IS NOT NULL
            AND @TargetIsActive <> 1
        )
        BEGIN
            THROW 50080,
                'The invited application account is inactive.',
                1;
        END;

        UPDATE dbo.Invitations
        SET Status = 'EXPIRED'
        WHERE EmailNormalized = @EmailNormalized
          AND Status = 'PENDING'
          AND ExpiresAt IS NOT NULL
          AND ExpiresAt <= @Now;

        IF EXISTS
        (
            SELECT 1
            FROM dbo.Invitations
                WITH (UPDLOCK, HOLDLOCK)
            WHERE EmailNormalized = @EmailNormalized
              AND Status = 'PENDING'
              AND
              (
                  ExpiresAt IS NULL
                  OR ExpiresAt > @Now
              )
              AND
              (
                  AthleteID = @AthleteID
                  OR
                  (
                      AthleteID IS NULL
                      AND @AthleteID IS NULL
                  )
              )
        )
        BEGIN
            THROW 50081,
                'A pending invitation already exists for this target.',
                1;
        END;

        IF
        (
            @AthleteID IS NOT NULL
            AND @TargetAppUserID IS NOT NULL
            AND EXISTS
            (
                SELECT 1
                FROM dbo.AthleteUserAccess
                    WITH (UPDLOCK, HOLDLOCK)
                WHERE AppUserID = @TargetAppUserID
                  AND AthleteID = @AthleteID
                  AND IsActive = 1
            )
        )
        BEGIN
            THROW 50082,
                'The invited user already has active access to this athlete.',
                1;
        END;

        IF
        (
            @AccessRole = 'RUNNER'
            AND EXISTS
            (
                SELECT 1
                FROM dbo.Invitations
                    WITH (UPDLOCK, HOLDLOCK)
                WHERE EmailNormalized =
                      @EmailNormalized
                  AND AccessRole = 'RUNNER'
                  AND Status = 'PENDING'
                  AND
                  (
                      ExpiresAt IS NULL
                      OR ExpiresAt > @Now
                  )
            )
        )
        BEGIN
            THROW 50083,
                'A pending RUNNER invitation already exists for this email.',
                1;
        END;

        IF
        (
            @AccessRole = 'RUNNER'
            AND @TargetAppUserID IS NOT NULL
            AND EXISTS
            (
                SELECT 1
                FROM dbo.AthleteUserAccess
                    WITH (UPDLOCK, HOLDLOCK)
                WHERE AppUserID = @TargetAppUserID
                  AND AccessRole = 'RUNNER'
                  AND IsActive = 1
            )
        )
        BEGIN
            THROW 50084,
                'The invited user already has an active RUNNER profile.',
                1;
        END;

        IF
        (
            @AthleteID IS NULL
            AND @TargetAppUserID IS NOT NULL
            AND EXISTS
            (
                SELECT 1
                FROM dbo.AppUsers
                    WITH (UPDLOCK, HOLDLOCK)
                WHERE AppUserID = @TargetAppUserID
                  AND OnboardingCompletedAt IS NOT NULL
            )
        )
        BEGIN
            THROW 50085,
                'The invited user has already completed runner onboarding.',
                1;
        END;

        INSERT INTO dbo.Invitations
        (
            EmailNormalized,
            AthleteID,
            AccessRole,
            Status,
            InvitedByAppUserID,
            ExpiresAt
        )
        VALUES
        (
            @EmailNormalized,
            @AthleteID,
            @AccessRole,
            'PENDING',
            @InvitedByAppUserID,
            @ExpiresAt
        );

        SET @InvitationID =
            CONVERT(INT, SCOPE_IDENTITY());

        COMMIT TRANSACTION;

        SELECT
            invitation.InvitationID,
            invitation.EmailNormalized,
            CASE
                WHEN invitation.AthleteID IS NULL
                    THEN 'NEW_RUNNER'
                ELSE 'EXISTING_ATHLETE'
            END AS InvitationType,
            invitation.AthleteID,
            athlete.FullName AS AthleteName,
            invitation.AccessRole,
            invitation.Status,
            invitation.InvitedByAppUserID,
            invitation.CreatedAt,
            invitation.ExpiresAt
        FROM dbo.Invitations AS invitation
        LEFT JOIN dbo.Athletes AS athlete
            ON athlete.AthleteID =
               invitation.AthleteID
        WHERE invitation.InvitationID =
              @InvitationID;
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