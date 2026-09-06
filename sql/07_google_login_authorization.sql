SET ANSI_NULLS ON;
GO

SET QUOTED_IDENTIFIER ON;
GO

CREATE OR ALTER PROCEDURE dbo.usp_AuthorizeGoogleLogin
    @GoogleSubject NVARCHAR(255),
    @EmailNormalized NVARCHAR(320),
    @DisplayName NVARCHAR(200) = NULL
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    SET @GoogleSubject =
        NULLIF(LTRIM(RTRIM(@GoogleSubject)), N'');

    SET @EmailNormalized =
        LOWER(
            NULLIF(
                LTRIM(RTRIM(@EmailNormalized)),
                N''
            )
        );

    SET @DisplayName =
        NULLIF(LTRIM(RTRIM(@DisplayName)), N'');

    IF @GoogleSubject IS NULL
    BEGIN
        THROW 50020,
            'Google subject is required.',
            1;
    END;

    IF
    (
        @EmailNormalized IS NULL
        OR @EmailNormalized NOT LIKE N'%_@_%._%'
    )
    BEGIN
        THROW 50021,
            'A valid normalized email is required.',
            1;
    END;

    DECLARE @Now DATETIME2(0) = SYSUTCDATETIME();
    DECLARE @AppUserID INT;
    DECLARE @IsActive BIT;

    BEGIN TRY
        BEGIN TRANSACTION;

        SELECT
            @AppUserID = AppUserID,
            @IsActive = IsActive
        FROM dbo.AppUsers WITH (UPDLOCK, HOLDLOCK)
        WHERE GoogleSubject = @GoogleSubject;

        IF @AppUserID IS NOT NULL
        BEGIN
            IF @IsActive = 0
            BEGIN
                THROW 50022,
                    'This application account is inactive.',
                    1;
            END;

            IF EXISTS
            (
                SELECT 1
                FROM dbo.AppUsers WITH (UPDLOCK, HOLDLOCK)
                WHERE EmailNormalized = @EmailNormalized
                  AND AppUserID <> @AppUserID
            )
            BEGIN
                THROW 50023,
                    'This email belongs to another application account.',
                    1;
            END;

            UPDATE dbo.AppUsers
            SET
                EmailNormalized = @EmailNormalized,
                DisplayName =
                    COALESCE(@DisplayName, DisplayName),
                LastLoginAt = @Now
            WHERE AppUserID = @AppUserID;
        END;
        ELSE
        BEGIN
            IF EXISTS
            (
                SELECT 1
                FROM dbo.AppUsers WITH (UPDLOCK, HOLDLOCK)
                WHERE EmailNormalized = @EmailNormalized
            )
            BEGIN
                THROW 50024,
                    'This email is linked to another Google identity.',
                    1;
            END;

            IF NOT EXISTS
            (
                SELECT 1
                FROM dbo.Invitations WITH (UPDLOCK, HOLDLOCK)
                WHERE EmailNormalized = @EmailNormalized
                  AND Status = 'PENDING'
                  AND
                  (
                      ExpiresAt IS NULL
                      OR ExpiresAt > @Now
                  )
            )
            BEGIN
                THROW 50025,
                    'No active invitation exists for this email.',
                    1;
            END;

            INSERT INTO dbo.AppUsers
            (
                GoogleSubject,
                EmailNormalized,
                DisplayName,
                IsActive,
                CreatedAt,
                LastLoginAt
            )
            VALUES
            (
                @GoogleSubject,
                @EmailNormalized,
                @DisplayName,
                1,
                @Now,
                @Now
            );

            SET @AppUserID =
                CONVERT(INT, SCOPE_IDENTITY());
        END;

        UPDATE access_row
        SET
            AccessRole =
                CASE
                    WHEN access_row.IsActive = 1
                        THEN access_row.AccessRole
                    ELSE invitation.AccessRole
                END,
            IsActive = 1,
            GrantedByAppUserID =
                CASE
                    WHEN access_row.IsActive = 1
                        THEN access_row.GrantedByAppUserID
                    ELSE invitation.InvitedByAppUserID
                END,
            GrantedAt =
                CASE
                    WHEN access_row.IsActive = 1
                        THEN access_row.GrantedAt
                    ELSE @Now
                END,
            RevokedAt = NULL
        FROM dbo.AthleteUserAccess AS access_row
        INNER JOIN dbo.Invitations AS invitation
            WITH (UPDLOCK, HOLDLOCK)
            ON invitation.AthleteID =
                access_row.AthleteID
        WHERE access_row.AppUserID = @AppUserID
          AND invitation.EmailNormalized =
              @EmailNormalized
          AND invitation.Status = 'PENDING'
          AND
          (
              invitation.ExpiresAt IS NULL
              OR invitation.ExpiresAt > @Now
          );

        INSERT INTO dbo.AthleteUserAccess
        (
            AppUserID,
            AthleteID,
            AccessRole,
            IsActive,
            GrantedByAppUserID,
            GrantedAt,
            RevokedAt
        )
        SELECT
            @AppUserID,
            invitation.AthleteID,
            invitation.AccessRole,
            1,
            invitation.InvitedByAppUserID,
            @Now,
            NULL
        FROM dbo.Invitations AS invitation
            WITH (UPDLOCK, HOLDLOCK)
        WHERE invitation.EmailNormalized =
              @EmailNormalized
          AND invitation.Status = 'PENDING'
          AND
          (
              invitation.ExpiresAt IS NULL
              OR invitation.ExpiresAt > @Now
          )
          AND NOT EXISTS
          (
              SELECT 1
              FROM dbo.AthleteUserAccess AS existing_access
                  WITH (UPDLOCK, HOLDLOCK)
              WHERE existing_access.AppUserID = @AppUserID
                AND existing_access.AthleteID =
                    invitation.AthleteID
          );

        UPDATE dbo.Invitations
        SET
            Status = 'ACCEPTED',
            AcceptedAt = @Now
        WHERE EmailNormalized = @EmailNormalized
          AND Status = 'PENDING'
          AND
          (
              ExpiresAt IS NULL
              OR ExpiresAt > @Now
          );

        IF NOT EXISTS
        (
            SELECT 1
            FROM dbo.AthleteUserAccess
            WHERE AppUserID = @AppUserID
              AND IsActive = 1
        )
        BEGIN
            THROW 50026,
                'This account has no active athlete access.',
                1;
        END;

        COMMIT TRANSACTION;

        SELECT
            @AppUserID AS AppUserID,
            access_row.AthleteID,
            athlete.FullName,
            access_row.AccessRole
        FROM dbo.AthleteUserAccess AS access_row
        INNER JOIN dbo.Athletes AS athlete
            ON athlete.AthleteID = access_row.AthleteID
        WHERE access_row.AppUserID = @AppUserID
          AND access_row.IsActive = 1
        ORDER BY access_row.AthleteID;
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