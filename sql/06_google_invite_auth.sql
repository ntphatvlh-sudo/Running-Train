SET XACT_ABORT ON;
GO

BEGIN TRY
    BEGIN TRANSACTION;

    IF OBJECT_ID(N'dbo.Athletes', N'U') IS NULL
    BEGIN
        THROW 50010, 'dbo.Athletes does not exist.', 1;
    END;

    IF OBJECT_ID(N'dbo.AppUsers', N'U') IS NULL
    BEGIN
        CREATE TABLE dbo.AppUsers
        (
            AppUserID INT IDENTITY(1, 1) NOT NULL,
            GoogleSubject NVARCHAR(255) NOT NULL,
            EmailNormalized NVARCHAR(320) NOT NULL,
            DisplayName NVARCHAR(200) NULL,
            IsActive BIT NOT NULL
                CONSTRAINT DF_AppUsers_IsActive DEFAULT (1),
            CreatedAt DATETIME2(0) NOT NULL
                CONSTRAINT DF_AppUsers_CreatedAt
                DEFAULT (SYSUTCDATETIME()),
            LastLoginAt DATETIME2(0) NULL,

            CONSTRAINT PK_AppUsers
                PRIMARY KEY CLUSTERED (AppUserID),

            CONSTRAINT UQ_AppUsers_GoogleSubject
                UNIQUE (GoogleSubject),

            CONSTRAINT UQ_AppUsers_EmailNormalized
                UNIQUE (EmailNormalized)
        );
    END;

        IF OBJECT_ID(N'dbo.Invitations', N'U') IS NULL
    BEGIN
        CREATE TABLE dbo.Invitations
        (
            InvitationID INT IDENTITY(1, 1) NOT NULL,
            EmailNormalized NVARCHAR(320) NOT NULL,
            AthleteID INT NOT NULL,
            AccessRole VARCHAR(20) NOT NULL,
            Status VARCHAR(20) NOT NULL
                CONSTRAINT DF_Invitations_Status
                DEFAULT ('PENDING'),
            InvitedByAppUserID INT NULL,
            CreatedAt DATETIME2(0) NOT NULL
                CONSTRAINT DF_Invitations_CreatedAt
                DEFAULT (SYSUTCDATETIME()),
            ExpiresAt DATETIME2(0) NULL,
            AcceptedAt DATETIME2(0) NULL,

            CONSTRAINT PK_Invitations
                PRIMARY KEY CLUSTERED (InvitationID),

            CONSTRAINT FK_Invitations_Athletes
                FOREIGN KEY (AthleteID)
                REFERENCES dbo.Athletes (AthleteID),

            CONSTRAINT FK_Invitations_InvitedByAppUsers
                FOREIGN KEY (InvitedByAppUserID)
                REFERENCES dbo.AppUsers (AppUserID),

            CONSTRAINT CK_Invitations_AccessRole
                CHECK (AccessRole IN ('OWNER', 'COACH', 'RUNNER')),

            CONSTRAINT CK_Invitations_Status
                CHECK (
                    Status IN (
                        'PENDING',
                        'ACCEPTED',
                        'REVOKED',
                        'EXPIRED'
                    )
                ),

            CONSTRAINT CK_Invitations_Expiry
                CHECK (
                    ExpiresAt IS NULL
                    OR ExpiresAt > CreatedAt
                ),

            CONSTRAINT CK_Invitations_AcceptedAt
                CHECK (
                    Status <> 'ACCEPTED'
                    OR AcceptedAt IS NOT NULL
                )
        );
    END;

    IF NOT EXISTS
    (
        SELECT 1
        FROM sys.indexes
        WHERE object_id = OBJECT_ID(N'dbo.Invitations')
          AND name = N'UX_Invitations_Pending_Email_Athlete'
    )
    BEGIN
        CREATE UNIQUE NONCLUSTERED INDEX
            UX_Invitations_Pending_Email_Athlete
        ON dbo.Invitations
        (
            EmailNormalized,
            AthleteID
        )
        WHERE Status = 'PENDING';
    END;

        IF OBJECT_ID(N'dbo.AthleteUserAccess', N'U') IS NULL
    BEGIN
        CREATE TABLE dbo.AthleteUserAccess
        (
            AppUserID INT NOT NULL,
            AthleteID INT NOT NULL,
            AccessRole VARCHAR(20) NOT NULL,
            IsActive BIT NOT NULL
                CONSTRAINT DF_AthleteUserAccess_IsActive
                DEFAULT (1),
            GrantedByAppUserID INT NULL,
            GrantedAt DATETIME2(0) NOT NULL
                CONSTRAINT DF_AthleteUserAccess_GrantedAt
                DEFAULT (SYSUTCDATETIME()),
            RevokedAt DATETIME2(0) NULL,

            CONSTRAINT PK_AthleteUserAccess
                PRIMARY KEY CLUSTERED
                (
                    AppUserID,
                    AthleteID
                ),

            CONSTRAINT FK_AthleteUserAccess_AppUsers
                FOREIGN KEY (AppUserID)
                REFERENCES dbo.AppUsers (AppUserID),

            CONSTRAINT FK_AthleteUserAccess_Athletes
                FOREIGN KEY (AthleteID)
                REFERENCES dbo.Athletes (AthleteID),

            CONSTRAINT FK_AthleteUserAccess_GrantedByAppUsers
                FOREIGN KEY (GrantedByAppUserID)
                REFERENCES dbo.AppUsers (AppUserID),

            CONSTRAINT CK_AthleteUserAccess_AccessRole
                CHECK (
                    AccessRole IN (
                        'OWNER',
                        'COACH',
                        'RUNNER'
                    )
                ),

            CONSTRAINT CK_AthleteUserAccess_Revocation
                CHECK (
                    (
                        IsActive = 1
                        AND RevokedAt IS NULL
                    )
                    OR
                    (
                        IsActive = 0
                        AND RevokedAt IS NOT NULL
                    )
                )
        );
    END;

    IF NOT EXISTS
    (
        SELECT 1
        FROM sys.indexes
        WHERE object_id =
            OBJECT_ID(N'dbo.AthleteUserAccess')
          AND name =
            N'IX_AthleteUserAccess_AthleteID'
    )
    BEGIN
        CREATE NONCLUSTERED INDEX
            IX_AthleteUserAccess_AthleteID
        ON dbo.AthleteUserAccess
        (
            AthleteID,
            AppUserID
        )
        INCLUDE
        (
            AccessRole,
            IsActive
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