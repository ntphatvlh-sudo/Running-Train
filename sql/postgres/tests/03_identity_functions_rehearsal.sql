BEGIN;

WITH created_athlete AS
(
    INSERT INTO public.athletes
    (
        full_name,
        height_cm,
        weight_kg
    )
    VALUES
    (
        'Postgres Rehearsal Owner',
        170,
        65
    )
    RETURNING athlete_id
),
created_owner AS
(
    INSERT INTO public.app_users
    (
        google_subject,
        email_normalized,
        display_name,
        onboarding_completed_at
    )
    VALUES
    (
        'postgres-rehearsal-owner-subject',
        'postgres-rehearsal-owner@example.invalid',
        'Postgres Rehearsal Owner',
        CURRENT_TIMESTAMP
    )
    RETURNING app_user_id
)
INSERT INTO public.athlete_user_access
(
    app_user_id,
    athlete_id,
    access_role,
    is_active
)
SELECT
    created_owner.app_user_id,
    created_athlete.athlete_id,
    'OWNER',
    TRUE
FROM created_owner
CROSS JOIN created_athlete;


/*
    OWNER tạo lời mời onboarding cho RUNNER mới.
    Kết quả phải là PENDING và athlete_id = NULL.
*/
SELECT *
FROM public.create_invitation
(
    (
        SELECT app_user_id
        FROM public.app_users
        WHERE email_normalized =
              'postgres-rehearsal-owner@example.invalid'
    ),
    'postgres-rehearsal-runner@example.invalid',
    'RUNNER',
    NULL::INTEGER,
    NULL::TIMESTAMPTZ
);


/*
    RUNNER đăng nhập Google lần đầu.
    Invitation chuyển từ PENDING sang ACCEPTED.
    Kết quả phải là ONBOARDING_REQUIRED.
*/
SELECT *
FROM public.authorize_google_login
(
    'postgres-rehearsal-runner-subject',
    'postgres-rehearsal-runner@example.invalid',
    'Postgres Rehearsal Runner'
);


/*
    RUNNER hoàn tất hồ sơ và Latest PR.
    Function phải tạo athlete, PR và RUNNER access.
*/
SELECT *
FROM public.complete_runner_onboarding
(
    (
        SELECT app_user_id
        FROM public.app_users
        WHERE email_normalized =
              'postgres-rehearsal-runner@example.invalid'
    ),
    'Postgres Rehearsal Runner',
    DATE '2000-01-01',
    'MALE',
    170.00,
    65.00,
    5000,
    1500,
    DATE '2026-09-01'
);


/*
    Đăng nhập lại sau onboarding.
    Kết quả phải là AUTHORIZED.
*/
SELECT *
FROM public.authorize_google_login
(
    'postgres-rehearsal-runner-subject',
    'postgres-rehearsal-runner@example.invalid',
    'Postgres Rehearsal Runner'
);


/*
    Kiểm tra trạng thái bên trong transaction.
*/
SELECT
    (
        SELECT COUNT(*)
        FROM public.app_users
    ) AS app_user_count_inside_transaction,

    (
        SELECT COUNT(*)
        FROM public.athletes
    ) AS athlete_count_inside_transaction,

    (
        SELECT COUNT(*)
        FROM public.athlete_user_access
        WHERE is_active = TRUE
    ) AS active_access_count_inside_transaction,

    (
        SELECT COUNT(*)
        FROM public.athlete_personal_records
    ) AS personal_record_count_inside_transaction,

    (
        SELECT COUNT(*)
        FROM public.invitations
        WHERE status = 'ACCEPTED'
    ) AS accepted_invitation_count_inside_transaction;


/*
    Không lưu bất kỳ dữ liệu rehearsal nào.
*/
ROLLBACK;


/*
    Xác nhận rollback đã dọn sạch toàn bộ dữ liệu thử.
*/
SELECT
    (
        SELECT COUNT(*)
        FROM public.app_users
    ) AS app_user_count_after_rollback,

    (
        SELECT COUNT(*)
        FROM public.athletes
    ) AS athlete_count_after_rollback,

    (
        SELECT COUNT(*)
        FROM public.athlete_user_access
    ) AS access_count_after_rollback,

    (
        SELECT COUNT(*)
        FROM public.athlete_personal_records
    ) AS personal_record_count_after_rollback,

    (
        SELECT COUNT(*)
        FROM public.invitations
    ) AS invitation_count_after_rollback;