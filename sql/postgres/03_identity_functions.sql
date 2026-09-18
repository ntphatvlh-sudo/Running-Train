BEGIN;

CREATE OR REPLACE FUNCTION public.authorize_google_login
(
    p_google_subject VARCHAR(255),
    p_email_normalized VARCHAR(320),
    p_display_name VARCHAR(200)
)
RETURNS TABLE
(
    app_user_id INTEGER,
    authorization_state VARCHAR(30),
    athlete_id INTEGER,
    full_name VARCHAR(200),
    access_role VARCHAR(20)
)
LANGUAGE plpgsql
AS $function$
DECLARE
    v_google_subject VARCHAR(255);
    v_email_normalized VARCHAR(320);
    v_display_name VARCHAR(200);

    v_now TIMESTAMPTZ(0) := CURRENT_TIMESTAMP;

    v_app_user_id INTEGER;
    v_is_active BOOLEAN;
    v_onboarding_completed_at TIMESTAMPTZ(0);
    v_user_found BOOLEAN;

    v_has_onboarding_invitation BOOLEAN;
    v_has_athlete_invitation BOOLEAN;
    v_has_accepted_onboarding BOOLEAN;
    v_has_active_runner_access BOOLEAN;
BEGIN
    v_google_subject :=
        NULLIF(btrim(p_google_subject), '');

    v_email_normalized :=
        lower(NULLIF(btrim(p_email_normalized), ''));

    v_display_name :=
        NULLIF(btrim(p_display_name), '');

    IF v_google_subject IS NULL THEN
        RAISE EXCEPTION
            'Google subject is required.';
    END IF;

    IF
        v_email_normalized IS NULL
        OR v_email_normalized !~
            '^[^@\s]+@[^@\s]+\.[^@\s]+$'
    THEN
        RAISE EXCEPTION
            'A valid normalized email is required.';
    END IF;

    /*
        Tuần tự hóa mọi thao tác authorization
        cho cùng một email trong transaction hiện tại.
    */
    PERFORM pg_advisory_xact_lock(
        hashtextextended(v_email_normalized, 0)
    );

    SELECT
        user_row.app_user_id,
        user_row.is_active,
        user_row.onboarding_completed_at
    INTO
        v_app_user_id,
        v_is_active,
        v_onboarding_completed_at
    FROM public.app_users AS user_row
    WHERE user_row.google_subject = v_google_subject
    FOR UPDATE;

    v_user_found := FOUND;

    SELECT EXISTS
    (
        SELECT 1
        FROM public.invitations AS invitation
        WHERE invitation.email_normalized =
              v_email_normalized
          AND invitation.status = 'PENDING'
          AND invitation.athlete_id IS NULL
          AND
          (
              invitation.expires_at IS NULL
              OR invitation.expires_at > v_now
          )
    )
    INTO v_has_onboarding_invitation;

    SELECT EXISTS
    (
        SELECT 1
        FROM public.invitations AS invitation
        WHERE invitation.email_normalized =
              v_email_normalized
          AND invitation.status = 'PENDING'
          AND invitation.athlete_id IS NOT NULL
          AND
          (
              invitation.expires_at IS NULL
              OR invitation.expires_at > v_now
          )
    )
    INTO v_has_athlete_invitation;

    SELECT EXISTS
    (
        SELECT 1
        FROM public.invitations AS invitation
        WHERE invitation.email_normalized =
              v_email_normalized
          AND invitation.status = 'ACCEPTED'
          AND invitation.athlete_id IS NULL
    )
    INTO v_has_accepted_onboarding;

    IF
        v_has_onboarding_invitation
        AND EXISTS
        (
            SELECT 1
            FROM public.invitations AS invitation
            WHERE invitation.email_normalized =
                  v_email_normalized
              AND invitation.status = 'PENDING'
              AND invitation.athlete_id IS NOT NULL
              AND invitation.access_role = 'RUNNER'
              AND
              (
                  invitation.expires_at IS NULL
                  OR invitation.expires_at > v_now
              )
        )
    THEN
        RAISE EXCEPTION
            'Conflicting runner invitations exist for this email.';
    END IF;

    IF v_user_found THEN
        IF NOT v_is_active THEN
            RAISE EXCEPTION
                'This application account is inactive.';
        END IF;

        IF EXISTS
        (
            SELECT 1
            FROM public.app_users AS other_user
            WHERE other_user.email_normalized =
                  v_email_normalized
              AND other_user.app_user_id <>
                  v_app_user_id
        )
        THEN
            RAISE EXCEPTION
                'This email belongs to another application account.';
        END IF;

        UPDATE public.app_users AS user_row
        SET
            email_normalized = v_email_normalized,
            display_name = COALESCE(
                v_display_name,
                user_row.display_name
            ),
            last_login_at = v_now
        WHERE user_row.app_user_id = v_app_user_id;
    ELSE
        IF EXISTS
        (
            SELECT 1
            FROM public.app_users AS existing_user
            WHERE existing_user.email_normalized =
                  v_email_normalized
        )
        THEN
            RAISE EXCEPTION
                'This email is linked to another Google identity.';
        END IF;

        IF
            NOT v_has_onboarding_invitation
            AND NOT v_has_athlete_invitation
        THEN
            RAISE EXCEPTION
                'No active invitation exists for this email.';
        END IF;

        INSERT INTO public.app_users
        (
            google_subject,
            email_normalized,
            display_name,
            is_active,
            created_at,
            last_login_at
        )
        VALUES
        (
            v_google_subject,
            v_email_normalized,
            v_display_name,
            TRUE,
            v_now,
            v_now
        )
        RETURNING public.app_users.app_user_id
        INTO v_app_user_id;

        v_onboarding_completed_at := NULL;
    END IF;

    SELECT EXISTS
    (
        SELECT 1
        FROM public.athlete_user_access AS access_row
        WHERE access_row.app_user_id = v_app_user_id
          AND access_row.access_role = 'RUNNER'
          AND access_row.is_active = TRUE
    )
    INTO v_has_active_runner_access;

    IF
        v_has_onboarding_invitation
        AND v_has_active_runner_access
    THEN
        RAISE EXCEPTION
            'This account already has an active runner profile.';
    END IF;

    IF
        (
            v_has_onboarding_invitation
            OR v_has_accepted_onboarding
        )
        AND v_onboarding_completed_at IS NULL
    THEN
        IF v_has_onboarding_invitation THEN
            UPDATE public.invitations AS invitation
            SET
                status = 'ACCEPTED',
                accepted_at = v_now
            WHERE invitation.email_normalized =
                  v_email_normalized
              AND invitation.status = 'PENDING'
              AND invitation.athlete_id IS NULL
              AND
              (
                  invitation.expires_at IS NULL
                  OR invitation.expires_at > v_now
              );
        END IF;

        RETURN QUERY
        SELECT
            v_app_user_id,
            'ONBOARDING_REQUIRED'::VARCHAR(30),
            NULL::INTEGER,
            NULL::VARCHAR(200),
            'RUNNER'::VARCHAR(20);

        RETURN;
    END IF;

    UPDATE public.athlete_user_access AS access_row
    SET
        access_role =
            CASE
                WHEN access_row.is_active
                    THEN access_row.access_role
                ELSE invitation.access_role
            END,

        is_active = TRUE,

        granted_by_app_user_id =
            CASE
                WHEN access_row.is_active
                    THEN access_row.granted_by_app_user_id
                ELSE invitation.invited_by_app_user_id
            END,

        granted_at =
            CASE
                WHEN access_row.is_active
                    THEN access_row.granted_at
                ELSE v_now
            END,

        revoked_at = NULL

    FROM public.invitations AS invitation

    WHERE access_row.app_user_id = v_app_user_id
      AND access_row.athlete_id =
          invitation.athlete_id
      AND invitation.email_normalized =
          v_email_normalized
      AND invitation.status = 'PENDING'
      AND invitation.athlete_id IS NOT NULL
      AND
      (
          invitation.expires_at IS NULL
          OR invitation.expires_at > v_now
      );

    INSERT INTO public.athlete_user_access
    (
        app_user_id,
        athlete_id,
        access_role,
        is_active,
        granted_by_app_user_id,
        granted_at,
        revoked_at
    )
    SELECT
        v_app_user_id,
        invitation.athlete_id,
        invitation.access_role,
        TRUE,
        invitation.invited_by_app_user_id,
        v_now,
        NULL
    FROM public.invitations AS invitation
    WHERE invitation.email_normalized =
          v_email_normalized
      AND invitation.status = 'PENDING'
      AND invitation.athlete_id IS NOT NULL
      AND
      (
          invitation.expires_at IS NULL
          OR invitation.expires_at > v_now
      )
    ON CONFLICT ON CONSTRAINT pk_athlete_user_access
        DO NOTHING;

    UPDATE public.invitations AS invitation
    SET
        status = 'ACCEPTED',
        accepted_at = v_now
    WHERE invitation.email_normalized =
          v_email_normalized
      AND invitation.status = 'PENDING'
      AND invitation.athlete_id IS NOT NULL
      AND
      (
          invitation.expires_at IS NULL
          OR invitation.expires_at > v_now
      );

    IF NOT EXISTS
    (
        SELECT 1
        FROM public.athlete_user_access AS access_row
        WHERE access_row.app_user_id = v_app_user_id
          AND access_row.is_active = TRUE
    )
    THEN
        RAISE EXCEPTION
            'This account has no active athlete access.';
    END IF;

    RETURN QUERY
    SELECT
        v_app_user_id,
        'AUTHORIZED'::VARCHAR(30),
        access_row.athlete_id,
        athlete.full_name::VARCHAR(200),
        access_row.access_role
    FROM public.athlete_user_access AS access_row
    INNER JOIN public.athletes AS athlete
        ON athlete.athlete_id = access_row.athlete_id
    WHERE access_row.app_user_id = v_app_user_id
      AND access_row.is_active = TRUE
    ORDER BY access_row.athlete_id;
END;
$function$;


CREATE OR REPLACE FUNCTION public.complete_runner_onboarding
(
    p_app_user_id INTEGER,
    p_full_name VARCHAR(150),
    p_date_of_birth DATE,
    p_sex VARCHAR(20),
    p_height_cm NUMERIC(5, 2),
    p_weight_kg NUMERIC(5, 2),
    p_latest_pr_distance_m INTEGER,
    p_latest_pr_completed_duration_sec INTEGER,
    p_latest_pr_achieved_date DATE
)
RETURNS TABLE
(
    app_user_id INTEGER,
    authorization_state VARCHAR(30),
    athlete_id INTEGER,
    full_name VARCHAR(150),
    access_role VARCHAR(20),
    personal_record_id BIGINT,
    latest_pr_distance_m INTEGER,
    latest_pr_completed_duration_sec INTEGER,
    latest_pr_achieved_date DATE
)
LANGUAGE plpgsql
AS $function$
DECLARE
    v_full_name VARCHAR(150);
    v_sex VARCHAR(20);

    v_today DATE :=
        (CURRENT_TIMESTAMP AT TIME ZONE
            'Asia/Ho_Chi_Minh')::DATE;

    v_now TIMESTAMPTZ(0) := CURRENT_TIMESTAMP;

    v_email_normalized VARCHAR(320);
    v_is_active BOOLEAN;
    v_onboarding_completed_at TIMESTAMPTZ(0);

    v_invitation_id INTEGER;
    v_invited_by_app_user_id INTEGER;

    v_athlete_id INTEGER;
    v_personal_record_id BIGINT;
BEGIN
    v_full_name := NULLIF(btrim(p_full_name), '');
    v_sex := NULLIF(btrim(p_sex), '');

    IF p_app_user_id IS NULL OR p_app_user_id <= 0 THEN
        RAISE EXCEPTION
            'A valid AppUserID is required.';
    END IF;

    IF v_full_name IS NULL THEN
        RAISE EXCEPTION
            'Full name is required.';
    END IF;

    IF
        p_height_cm IS NULL
        OR p_height_cm < 80
        OR p_height_cm > 250
    THEN
        RAISE EXCEPTION
            'Height must be between 80 and 250 cm.';
    END IF;

    IF
        p_weight_kg IS NULL
        OR p_weight_kg < 25
        OR p_weight_kg > 300
    THEN
        RAISE EXCEPTION
            'Weight must be between 25 and 300 kg.';
    END IF;

    IF
        p_date_of_birth IS NOT NULL
        AND p_date_of_birth > v_today
    THEN
        RAISE EXCEPTION
            'Date of birth cannot be in the future.';
    END IF;

    IF
        p_latest_pr_distance_m IS NULL
        OR p_latest_pr_distance_m <= 0
    THEN
        RAISE EXCEPTION
            'Latest PR distance must be positive.';
    END IF;

    IF
        p_latest_pr_completed_duration_sec IS NULL
        OR p_latest_pr_completed_duration_sec <= 0
    THEN
        RAISE EXCEPTION
            'Latest PR completed time must be positive.';
    END IF;

    IF
        p_latest_pr_achieved_date IS NULL
        OR p_latest_pr_achieved_date > v_today
    THEN
        RAISE EXCEPTION
            'Latest PR date is required and cannot be in the future.';
    END IF;

    IF
        p_date_of_birth IS NOT NULL
        AND p_latest_pr_achieved_date <
            p_date_of_birth
    THEN
        RAISE EXCEPTION
            'Latest PR date cannot be before date of birth.';
    END IF;

    PERFORM pg_advisory_xact_lock(
        p_app_user_id::BIGINT
    );

    SELECT
        user_row.email_normalized,
        user_row.is_active,
        user_row.onboarding_completed_at
    INTO
        v_email_normalized,
        v_is_active,
        v_onboarding_completed_at
    FROM public.app_users AS user_row
    WHERE user_row.app_user_id = p_app_user_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'App user does not exist.';
    END IF;

    IF NOT v_is_active THEN
        RAISE EXCEPTION
            'App user is not active.';
    END IF;

    IF v_onboarding_completed_at IS NOT NULL THEN
        RAISE EXCEPTION
            'Runner onboarding is already complete.';
    END IF;

    SELECT
        invitation.invitation_id,
        invitation.invited_by_app_user_id
    INTO
        v_invitation_id,
        v_invited_by_app_user_id
    FROM public.invitations AS invitation
    WHERE invitation.email_normalized =
          v_email_normalized
      AND invitation.athlete_id IS NULL
      AND invitation.access_role = 'RUNNER'
      AND invitation.status = 'ACCEPTED'
    ORDER BY
        invitation.accepted_at DESC,
        invitation.invitation_id DESC
    LIMIT 1
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'No accepted runner onboarding invitation exists.';
    END IF;

    IF EXISTS
    (
        SELECT 1
        FROM public.athlete_user_access AS access_row
        WHERE access_row.app_user_id = p_app_user_id
          AND access_row.access_role = 'RUNNER'
          AND access_row.is_active = TRUE
    )
    THEN
        RAISE EXCEPTION
            'App user already has active runner access.';
    END IF;

    INSERT INTO public.athletes
    (
        full_name,
        date_of_birth,
        sex,
        height_cm,
        weight_kg
    )
    VALUES
    (
        v_full_name,
        p_date_of_birth,
        v_sex,
        p_height_cm,
        p_weight_kg
    )
    RETURNING public.athletes.athlete_id
    INTO v_athlete_id;

    INSERT INTO public.athlete_personal_records
    (
        athlete_id,
        distance_m,
        completed_duration_sec,
        achieved_date
    )
    VALUES
    (
        v_athlete_id,
        p_latest_pr_distance_m,
        p_latest_pr_completed_duration_sec,
        p_latest_pr_achieved_date
    )
    RETURNING
        public.athlete_personal_records.personal_record_id
    INTO v_personal_record_id;

    INSERT INTO public.athlete_user_access
    (
        app_user_id,
        athlete_id,
        access_role,
        is_active,
        granted_by_app_user_id
    )
    VALUES
    (
        p_app_user_id,
        v_athlete_id,
        'RUNNER',
        TRUE,
        v_invited_by_app_user_id
    );

    UPDATE public.invitations AS invitation
    SET athlete_id = v_athlete_id
    WHERE invitation.invitation_id =
          v_invitation_id;

    UPDATE public.app_users AS user_row
    SET onboarding_completed_at = v_now
    WHERE user_row.app_user_id = p_app_user_id;

    RETURN QUERY
    SELECT
        p_app_user_id,
        'AUTHORIZED'::VARCHAR(30),
        v_athlete_id,
        v_full_name,
        'RUNNER'::VARCHAR(20),
        v_personal_record_id,
        p_latest_pr_distance_m,
        p_latest_pr_completed_duration_sec,
        p_latest_pr_achieved_date;
END;
$function$;


CREATE OR REPLACE FUNCTION public.create_invitation
(
    p_invited_by_app_user_id INTEGER,
    p_email_normalized VARCHAR(320),
    p_access_role VARCHAR(20),
    p_athlete_id INTEGER,
    p_expires_at TIMESTAMPTZ(0)
)
RETURNS TABLE
(
    invitation_id INTEGER,
    email_normalized VARCHAR(320),
    invitation_type VARCHAR(30),
    athlete_id INTEGER,
    athlete_name VARCHAR(150),
    access_role VARCHAR(20),
    status VARCHAR(20),
    invited_by_app_user_id INTEGER,
    created_at TIMESTAMPTZ(0),
    expires_at TIMESTAMPTZ(0)
)
LANGUAGE plpgsql
AS $function$
DECLARE
    v_email_normalized VARCHAR(320);
    v_access_role VARCHAR(20);
    v_now TIMESTAMPTZ(0) := CURRENT_TIMESTAMP;

    v_inviter_is_active BOOLEAN;

    v_target_app_user_id INTEGER;
    v_target_is_active BOOLEAN;

    v_invitation_id INTEGER;
BEGIN
    v_email_normalized :=
        lower(NULLIF(btrim(p_email_normalized), ''));

    v_access_role :=
        upper(NULLIF(btrim(p_access_role), ''));

    IF
        p_invited_by_app_user_id IS NULL
        OR p_invited_by_app_user_id <= 0
    THEN
        RAISE EXCEPTION
            'A valid inviting AppUserID is required.';
    END IF;

    IF
        v_email_normalized IS NULL
        OR v_email_normalized !~
            '^[^@\s]+@[^@\s]+\.[^@\s]+$'
    THEN
        RAISE EXCEPTION
            'A valid normalized email is required.';
    END IF;

    IF
        v_access_role IS NULL
        OR v_access_role NOT IN (
            'OWNER',
            'COACH',
            'RUNNER'
        )
    THEN
        RAISE EXCEPTION
            'Invitation access role is invalid.';
    END IF;

    IF
        p_athlete_id IS NULL
        AND v_access_role <> 'RUNNER'
    THEN
        RAISE EXCEPTION
            'A new-athlete invitation must use RUNNER role.';
    END IF;

    IF
        p_expires_at IS NOT NULL
        AND p_expires_at <= v_now
    THEN
        RAISE EXCEPTION
            'Invitation expiry must be in the future.';
    END IF;

    PERFORM pg_advisory_xact_lock(
        hashtextextended(v_email_normalized, 0)
    );

    SELECT user_row.is_active
    INTO v_inviter_is_active
    FROM public.app_users AS user_row
    WHERE user_row.app_user_id =
          p_invited_by_app_user_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'Inviting app user does not exist.';
    END IF;

    IF NOT v_inviter_is_active THEN
        RAISE EXCEPTION
            'Inviting app user is not active.';
    END IF;

    IF p_athlete_id IS NULL THEN
        IF NOT EXISTS
        (
            SELECT 1
            FROM public.athlete_user_access AS access_row
            WHERE access_row.app_user_id =
                  p_invited_by_app_user_id
              AND access_row.access_role = 'OWNER'
              AND access_row.is_active = TRUE
        )
        THEN
            RAISE EXCEPTION
                'Only an active OWNER can invite a new runner.';
        END IF;
    ELSE
        IF NOT EXISTS
        (
            SELECT 1
            FROM public.athletes AS athlete
            WHERE athlete.athlete_id = p_athlete_id
        )
        THEN
            RAISE EXCEPTION
                'Invitation athlete does not exist.';
        END IF;

        IF NOT EXISTS
        (
            SELECT 1
            FROM public.athlete_user_access AS access_row
            WHERE access_row.app_user_id =
                  p_invited_by_app_user_id
              AND access_row.athlete_id = p_athlete_id
              AND access_row.access_role = 'OWNER'
              AND access_row.is_active = TRUE
        )
        THEN
            RAISE EXCEPTION
                'OWNER access is required for this athlete.';
        END IF;
    END IF;

    SELECT
        target_user.app_user_id,
        target_user.is_active
    INTO
        v_target_app_user_id,
        v_target_is_active
    FROM public.app_users AS target_user
    WHERE target_user.email_normalized =
          v_email_normalized
    FOR UPDATE;

    IF
        FOUND
        AND NOT v_target_is_active
    THEN
        RAISE EXCEPTION
            'The invited application account is inactive.';
    END IF;

    UPDATE public.invitations AS invitation
    SET status = 'EXPIRED'
    WHERE invitation.email_normalized =
          v_email_normalized
      AND invitation.status = 'PENDING'
      AND invitation.expires_at IS NOT NULL
      AND invitation.expires_at <= v_now;

    IF EXISTS
    (
        SELECT 1
        FROM public.invitations AS invitation
        WHERE invitation.email_normalized =
              v_email_normalized
          AND invitation.status = 'PENDING'
          AND
          (
              invitation.expires_at IS NULL
              OR invitation.expires_at > v_now
          )
          AND invitation.athlete_id
              IS NOT DISTINCT FROM p_athlete_id
    )
    THEN
        RAISE EXCEPTION
            'A pending invitation already exists for this target.';
    END IF;

    IF
        p_athlete_id IS NOT NULL
        AND v_target_app_user_id IS NOT NULL
        AND EXISTS
        (
            SELECT 1
            FROM public.athlete_user_access AS access_row
            WHERE access_row.app_user_id =
                  v_target_app_user_id
              AND access_row.athlete_id = p_athlete_id
              AND access_row.is_active = TRUE
        )
    THEN
        RAISE EXCEPTION
            'The invited user already has active access to this athlete.';
    END IF;

    IF
        v_access_role = 'RUNNER'
        AND EXISTS
        (
            SELECT 1
            FROM public.invitations AS invitation
            WHERE invitation.email_normalized =
                  v_email_normalized
              AND invitation.access_role = 'RUNNER'
              AND invitation.status = 'PENDING'
              AND
              (
                  invitation.expires_at IS NULL
                  OR invitation.expires_at > v_now
              )
        )
    THEN
        RAISE EXCEPTION
            'A pending RUNNER invitation already exists for this email.';
    END IF;

    IF
        v_access_role = 'RUNNER'
        AND v_target_app_user_id IS NOT NULL
        AND EXISTS
        (
            SELECT 1
            FROM public.athlete_user_access AS access_row
            WHERE access_row.app_user_id =
                  v_target_app_user_id
              AND access_row.access_role = 'RUNNER'
              AND access_row.is_active = TRUE
        )
    THEN
        RAISE EXCEPTION
            'The invited user already has an active RUNNER profile.';
    END IF;

    IF
        p_athlete_id IS NULL
        AND v_target_app_user_id IS NOT NULL
        AND EXISTS
        (
            SELECT 1
            FROM public.app_users AS target_user
            WHERE target_user.app_user_id =
                  v_target_app_user_id
              AND target_user.onboarding_completed_at
                  IS NOT NULL
        )
    THEN
        RAISE EXCEPTION
            'The invited user has already completed runner onboarding.';
    END IF;

    INSERT INTO public.invitations
    (
        email_normalized,
        athlete_id,
        access_role,
        status,
        invited_by_app_user_id,
        expires_at
    )
    VALUES
    (
        v_email_normalized,
        p_athlete_id,
        v_access_role,
        'PENDING',
        p_invited_by_app_user_id,
        p_expires_at
    )
    RETURNING public.invitations.invitation_id
    INTO v_invitation_id;

    RETURN QUERY
    SELECT
        invitation.invitation_id,
        invitation.email_normalized,

        CASE
            WHEN invitation.athlete_id IS NULL
                THEN 'NEW_RUNNER'
            ELSE 'EXISTING_ATHLETE'
        END::VARCHAR(30),

        invitation.athlete_id,
        athlete.full_name,
        invitation.access_role,
        invitation.status,
        invitation.invited_by_app_user_id,
        invitation.created_at,
        invitation.expires_at

    FROM public.invitations AS invitation

    LEFT JOIN public.athletes AS athlete
        ON athlete.athlete_id =
           invitation.athlete_id

    WHERE invitation.invitation_id =
          v_invitation_id;
END;
$function$;

COMMIT;