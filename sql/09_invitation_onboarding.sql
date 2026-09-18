USE RunningTrainingDB;
GO

SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;
SET ANSI_PADDING ON;
SET ANSI_WARNINGS ON;
SET ARITHABORT ON;
SET CONCAT_NULL_YIELDS_NULL ON;
SET NUMERIC_ROUNDABORT OFF;
SET NOCOUNT ON;
SET XACT_ABORT ON;
GO

/*
    Hỗ trợ hai loại invitation:

    1. AthleteID IS NULL:
       RUNNER mới cần tự onboarding.

    2. AthleteID IS NOT NULL:
       Cấp quyền vào Athlete đã tồn tại.

    Một AppUser chỉ có tối đa một quyền RUNNER
    đang hoạt động, nhưng vẫn có thể làm
    OWNER hoặc COACH của các Athlete khác.
*/

BEGIN TRY
    BEGIN TRANSACTION;

    IF OBJECT_ID(N'dbo.AppUsers', N'U') IS NULL
    BEGIN
        THROW 51001, 'Missing table dbo.AppUsers.', 1;
    END;

    IF OBJECT_ID(N'dbo.Invitations', N'U') IS NULL
    BEGIN
        THROW 51002, 'Missing table dbo.Invitations.', 1;
    END;

    IF OBJECT_ID(
        N'dbo.AthleteUserAccess',
        N'U'
    ) IS NULL
    BEGIN
        THROW 51003,
            'Missing table dbo.AthleteUserAccess.',
            1;
    END;

    IF OBJECT_ID(N'dbo.Athletes', N'U') IS NULL
    BEGIN
        THROW 51004, 'Missing table dbo.Athletes.', 1;
    END;

    IF EXISTS
    (
        SELECT 1
        FROM sys.columns
        WHERE object_id =
              OBJECT_ID(N'dbo.Invitations')
          AND name = N'AthleteID'
          AND is_nullable = 0
    )
    BEGIN
        ALTER TABLE dbo.Invitations
        ALTER COLUMN AthleteID INT NULL;
    END;

    IF COL_LENGTH(
        N'dbo.AppUsers',
        N'OnboardingCompletedAt'
    ) IS NULL
    BEGIN
        ALTER TABLE dbo.AppUsers
        ADD OnboardingCompletedAt DATETIME2(0) NULL;
    END;

    IF OBJECT_ID(
        N'dbo.CK_Invitations_AthleteRole',
        N'C'
    ) IS NULL
    BEGIN
        ALTER TABLE dbo.Invitations
        WITH CHECK
        ADD CONSTRAINT CK_Invitations_AthleteRole
        CHECK
        (
            AthleteID IS NOT NULL
            OR AccessRole = 'RUNNER'
        );
    END;

    IF NOT EXISTS
    (
        SELECT 1
        FROM sys.indexes
        WHERE object_id =
              OBJECT_ID(N'dbo.AthleteUserAccess')
          AND name =
              N'UX_AthleteUserAccess_ActiveRunner'
    )
    BEGIN
        CREATE UNIQUE NONCLUSTERED INDEX
            UX_AthleteUserAccess_ActiveRunner
        ON dbo.AthleteUserAccess
        (
            AppUserID
        )
        WHERE
            AccessRole = 'RUNNER'
            AND IsActive = 1;
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