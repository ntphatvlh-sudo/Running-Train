USE RunningTrainingDB;
GO

SELECT
    OBJECT_ID(
        'dbo.vw_AdaptivePlanDashboard', 'V'
    ) AS AdaptiveDashboardID,

    OBJECT_ID(
        'dbo.vw_TrainingSupportDashboard', 'V'
    ) AS TrainingSupportDashboardID,

    OBJECT_ID(
        'dbo.vw_WeeklyRunReadiness', 'V'
    ) AS WeeklyReadinessID,

    OBJECT_ID(
        'dbo.vw_PlannedWorkoutVersionHistory', 'V'
    ) AS VersionHistoryID,

    OBJECT_ID(
        'dbo.sp_PreviewMatchingPlan', 'P'
    ) AS PreviewReductionID,

    OBJECT_ID(
        'dbo.sp_PreviewControlledProgression', 'P'
    ) AS PreviewProgressionID,

    OBJECT_ID(
        'dbo.sp_ApplyMatchingPlan', 'P'
    ) AS ApplyReductionID,

    OBJECT_ID(
        'dbo.sp_ApplyControlledProgression', 'P'
    ) AS ApplyProgressionID,

    OBJECT_ID(
        'dbo.sp_RollbackRunPlanAdjustment', 'P'
    ) AS RollbackID;
GO

SELECT
    TABLE_NAME,
    TABLE_TYPE
FROM INFORMATION_SCHEMA.TABLES
WHERE TABLE_SCHEMA = 'dbo'
ORDER BY TABLE_TYPE, TABLE_NAME;
GO