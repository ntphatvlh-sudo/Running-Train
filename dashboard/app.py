from __future__ import annotations
import json
import logging

import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError



logger = logging.getLogger(__name__)

# Cho phép dashboard import src.database
PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dashboard.plan_preview import render_ten_k_preview
from src.database import engine
from dashboard.goal_management import render_goal_list

from src.auth import (
    AUTHORIZED_STATE,
    ONBOARDING_REQUIRED_STATE,
    AthleteAccess,
    AuthorizationError,
    AuthorizationResult,
    OnboardingError,
    RunnerOnboardingData,
    authorize_google_identity,
    complete_runner_onboarding,
    normalize_email,
    parse_google_identity,
)

from src.invitations import (
    EXISTING_ATHLETE_INVITATION,
    NEW_RUNNER_INVITATION,
    InvitationError,
    InvitationRequest,
    create_invitation,
)

from dashboard.training_entry import (
    render_training_entry,
)

st.set_page_config(
    page_title="Running Training Analytics",
    page_icon="🏃",
    layout="wide",
)


@st.cache_data(ttl=60)
def read_query(
    query: str,
    params: dict | None = None,
) -> pd.DataFrame:
    """Đọc dữ liệu PostgreSQL thành DataFrame."""
    with engine.connect() as connection:
        return pd.read_sql(
            text(query),
            connection,
            params=params or {},
        )

def safe_read_query(
    section_name: str,
    query: str,
    params: dict | None = None,
) -> pd.DataFrame:
    """
    Đọc một khu vực Dashboard.
    Nếu khu vực này lỗi, các khu vực khác vẫn chạy.
    """
    try:
        return read_query(
            query,
            params,
        )

    except Exception:
        logger.exception(
            "Could not load dashboard section %s",
            section_name,
        )
        st.error(
            f"Could not load {section_name}."
        )
        return pd.DataFrame()

VALID_ADJUSTMENT_TYPES = {
    "REDUCTION",
    "PROGRESSION",
}


def normalize_adjustment_type(
    adjustment_type: str,
) -> str:
    """Chuẩn hóa và kiểm tra loại điều chỉnh."""
    normalized_type = adjustment_type.strip().upper()

    if normalized_type not in VALID_ADJUSTMENT_TYPES:
        raise ValueError(
            "Adjustment should be "
            "REDUCTION or PROGRESSION."
        )

    return normalized_type


def decode_json_payload(payload) -> dict:
    """Chuyển JSONB PostgreSQL thành dictionary."""
    if isinstance(payload, str):
        return json.loads(payload)

    return dict(payload)


def execute_preview_procedure(
    preview_type: str,
    plan_id: int,
    window_days: int,
    connection: Connection | None = None,
) -> list[pd.DataFrame]:
    """
    Gọi function Preview PostgreSQL.

    Result set 1 là kết luận chung.
    Result set 2 là danh sách workout đề xuất.
    """
    normalized_type = normalize_adjustment_type(
        preview_type
    )

    statement = text(
        """
        SELECT
            result_set,
            payload
        FROM public.preview_adaptive_plan
        (
            :plan_id,
            :window_days,
            :adjustment_type
        )
        ORDER BY result_set
        """
    )

    params = {
        "plan_id": int(plan_id),
        "window_days": int(window_days),
        "adjustment_type": normalized_type,
    }

    def fetch_rows(query_connection):
        return query_connection.execute(
            statement,
            params,
        ).mappings().all()

    if connection is None:
        with engine.connect() as query_connection:
            rows = fetch_rows(query_connection)
    else:
        rows = fetch_rows(connection)

    result_frames: list[pd.DataFrame] = []

    result_set_numbers = sorted(
        {
            int(row["result_set"])
            for row in rows
        }
    )

    for result_set_number in result_set_numbers:
        records = [
            decode_json_payload(row["payload"])
            for row in rows
            if int(row["result_set"])
                == result_set_number
        ]

        result_frames.append(
            pd.DataFrame(records)
        )

    return result_frames


def preview_frames_match(
    saved_frames: list[pd.DataFrame],
    current_frames: list[pd.DataFrame],
) -> bool:
    if len(saved_frames) != len(current_frames):
        return False

    for saved_frame, current_frame in zip(
        saved_frames,
        current_frames,
    ):
        saved_records = json.loads(
            saved_frame.to_json(
                orient="records",
                date_format="iso",
            )
        )
        current_records = json.loads(
            current_frame.to_json(
                orient="records",
                date_format="iso",
            )
        )

        saved_records = sorted(
            json.dumps(record, sort_keys=True)
            for record in saved_records
        )
        current_records = sorted(
            json.dumps(record, sort_keys=True)
            for record in current_records
        )

        if saved_records != current_records:
            return False

    return True

class PreviewChangedError(Exception):
    def __init__(
        self,
        current_frames: list[pd.DataFrame],
    ) -> None:
        super().__init__("Preview data changed.")
        self.current_frames = current_frames


def execute_apply_procedure(
    adjustment_type: str,
    plan_id: int,
    window_days: int,
    expected_preview_frames: list[pd.DataFrame] | None = None,
) -> list[pd.DataFrame]:
    """
    Apply Adaptive Plan trong một transaction.

    engine.begin() tự commit khi thành công
    và tự rollback nếu phát sinh lỗi.
    """
    normalized_type = normalize_adjustment_type(
        adjustment_type
    )

    statement = text(
        """
        SELECT
            adjustment_run_id
                AS "AdjustmentRunID",
            adjustment_type
                AS "AdjustmentType",
            change_percent
                AS "ChangePercent",
            changed_workout_count
                AS "ChangedWorkoutCount"
        FROM public.apply_adaptive_plan
        (
            :plan_id,
            :window_days,
            :adjustment_type
        )
        """
    )

    with engine.begin() as connection:
        connection.execute(
            text(
                "SET TRANSACTION ISOLATION LEVEL SERIALIZABLE"
            )
        )

        if expected_preview_frames is not None:
            current_preview_frames = execute_preview_procedure(
                adjustment_type,
                plan_id,
                window_days,
                connection=connection,
            )

            if not preview_frames_match(
                expected_preview_frames,
                current_preview_frames,
            ):
                raise PreviewChangedError(
                    current_preview_frames
                )
        rows = connection.execute(
            statement,
            {
                "plan_id": int(plan_id),
                "window_days": int(window_days),
                "adjustment_type": normalized_type,
            },
        ).mappings().all()

    return [
        pd.DataFrame(rows)
    ]


def execute_rollback_procedure(
    adjustment_run_id: int,
) -> list[pd.DataFrame]:
    """
    Khôi phục adjustment trong một transaction.

    engine.begin() tự commit khi thành công
    và tự rollback nếu phát sinh lỗi.
    """
    statement = text(
        """
        SELECT
            adjustment_run_id
                AS "AdjustmentRunID",
            plan_id
                AS "PlanID",
            new_status
                AS "NewStatus",
            restored_workout_count
                AS "RestoredWorkoutCount"
        FROM public.rollback_run_plan_adjustment
        (
            :adjustment_run_id
        )
        """
    )

    with engine.begin() as connection:
        rows = connection.execute(
            statement,
            {
                "adjustment_run_id":
                    int(adjustment_run_id),
            },
        ).mappings().all()

    return [
        pd.DataFrame(rows)
    ]

def format_percent(value) -> str:
    if pd.isna(value):
        return "N/A"

    return f"{float(value):.1f}%"


def format_optional_number(
    value,
    digits: int = 1,
) -> str:
    if pd.isna(value):
        return "N/A"

    return f"{float(value):.{digits}f}"


def show_database_error(error: Exception) -> None:
    logger.error(
        "Database operation failed: %s",
        type(error).__name__,
        exc_info=(
            type(error),
            error,
            error.__traceback__,
        ),
    )

    st.error(
        "Could not connect to PostgreSQL or run the query."
    )

    st.info(
        "Please check the database connection and try again."
    )

def show_owner_invitation_manager(
    app_user_id: int,
    owner_accesses: list[AthleteAccess],
) -> None:
    st.header("Invitation Manage")

    invitation_type = st.radio(
        "Invitation Type",
        options=(
            NEW_RUNNER_INVITATION,
            EXISTING_ATHLETE_INVITATION,
        ),
        format_func=lambda value: {
            NEW_RUNNER_INVITATION: (
                "New Runner — Create new profile"
            ),
            EXISTING_ATHLETE_INVITATION: (
                "Access another athlete profile"
            ),
        }[value],
        horizontal=True,
        key="owner_invitation_type",
    )

    owner_access_by_athlete_id = {
        access.athlete_id: access
        for access in owner_accesses
    }

    with st.form("owner_invitation_form"):
        email = st.text_input(
            "Recipient's Gmail *",
            max_chars=320,
        )

        if invitation_type == NEW_RUNNER_INVITATION:
            athlete_id = None
            access_role = "RUNNER"

            st.caption(
                "Recipients will create their profile"
                "upon their first login."
            )
        else:
            athlete_id = st.selectbox(
                "Athlete *",
                options=list(
                    owner_access_by_athlete_id.keys()
                ),
                format_func=lambda selected_id: (
                    owner_access_by_athlete_id[
                        selected_id
                    ].full_name
                ),
            )

            access_role = st.selectbox(
                "Role *",
                options=(
                    "RUNNER",
                    "COACH",
                    "OWNER",
                ),
            )

        expiry_option = st.selectbox(
            "Invitation duration",
            options=(
                "Unlimited",
                "7 days",
                "30 days",
            ),
        )

        submitted = st.form_submit_button(
            "Create invitation",
            type="primary",
        )

    if not submitted:
        return

    if not email.strip():
        st.error("Please fill Recipient's email.")
        return

    try:
        email = normalize_email(email)
    except ValueError:
        st.error("The recipient's Gmail address is not in the correct format.")
        return

    expires_at = None
    now_utc = datetime.now(timezone.utc).replace(
        tzinfo=None
    )

    if expiry_option == "7 days":
        expires_at = now_utc + timedelta(days=7)
    elif expiry_option == "30 days":
        expires_at = now_utc + timedelta(days=30)

    request = InvitationRequest(
        invited_by_app_user_id=app_user_id,
        email_normalized=email,
        access_role=access_role,
        athlete_id=athlete_id,
        expires_at=expires_at,
    )

    try:
        with engine.begin() as connection:
            invitation = create_invitation(
                connection,
                request,
            )
    except InvitationError:
        st.error(
            "Unable to verify the result of the invitation creation."
        )
        return
    except SQLAlchemyError:
        st.error(
            "Unable to create invitation. The Gmail account may "
            "have been invited, already granted access, "
            "or deemed ineligible for access."
        )
        return

    invitation_target = (
        invitation.athlete_name
        if invitation.athlete_name is not None
        else "New runner"
    )

    st.success(
        f"Invitation is sent #{invitation.invitation_id} "
        f"to {invitation.email_normalized} — "
        f"{invitation.access_role} / "
        f"{invitation_target}."
    )


def show_runner_onboarding(app_user_id: int) -> None:
    st.title("🏃 Establish Athlete's training profile")

    st.info(
        "Please fill your newest physical condition and "
        "performance to have the suitable training plan."
    )

    with st.form("runner_onboarding_form"):
        full_name = st.text_input(
            "Full name *",
            max_chars=150,
        )

        profile_col_1, profile_col_2 = st.columns(2)

        with profile_col_1:
            height_cm = st.number_input(
                "Height (cm) *",
                min_value=80.0,
                max_value=250.0,
                value=None,
                step=0.1,
            )

            date_of_birth = st.date_input(
                "Date of birth (Optional)",
                value=None,
                min_value=date(1900, 1, 1),
                max_value=date.today(),
            )

        with profile_col_2:
            weight_kg = st.number_input(
                "Weight (kg) *",
                min_value=25.0,
                max_value=300.0,
                value=None,
                step=0.1,
            )

            sex = st.selectbox(
                "Sex (Optional)",
                options=(
                    None,
                    "MALE",
                    "FEMALE",
                    "OTHER",
                    "PREFER_NOT_TO_SAY",
                ),
                format_func=lambda value: {
                    None: "Prefer not to provide",
                    "MALE": "Male",
                    "FEMALE": "Female",
                    "OTHER": "Other",
                    "PREFER_NOT_TO_SAY": "Prefer not to say",
                }[value],
            )

        st.subheader("Latest PR")

        pr_distance_km = st.number_input(
            "Distance (km) *",
            min_value=0.01,
            value=None,
            step=0.1,
        )

        time_col_1, time_col_2, time_col_3 = st.columns(3)

        with time_col_1:
            pr_hours = st.number_input(
                "hour *",
                min_value=0,
                value=0,
                step=1,
            )

        with time_col_2:
            pr_minutes = st.number_input(
                "Minutes *",
                min_value=0,
                max_value=59,
                value=0,
                step=1,
            )

        with time_col_3:
            pr_seconds = st.number_input(
                "Seconds *",
                min_value=0,
                max_value=59,
                value=0,
                step=1,
            )

        pr_achieved_date = st.date_input(
            "Date of PR *",
            value=date.today(),
            max_value=date.today(),
        )

        submitted = st.form_submit_button(
            "Onboarding completed",
            type="primary",
        )

    if not submitted:
        return

    normalized_full_name = full_name.strip()

    if not normalized_full_name:
        st.error("Full name is required.")
        return

    if height_cm is None:
        st.error("Height is required.")
        return

    if weight_kg is None:
        st.error("Weight is required.")
        return

    if pr_distance_km is None:
        st.error("Latest PR distance is required.")
        return

    completed_duration_sec = (
        int(pr_hours) * 3600
        + int(pr_minutes) * 60
        + int(pr_seconds)
    )

    if completed_duration_sec <= 0:
        st.error(
            "Finish time of Latest PR "
            "must be greater than 0."
        )
        return

    onboarding = RunnerOnboardingData(
        full_name=normalized_full_name,
        date_of_birth=date_of_birth,
        sex=sex,
        height_cm=float(height_cm),
        weight_kg=float(weight_kg),
        latest_pr_distance_m=int(
            round(float(pr_distance_km) * 1000)
        ),
        latest_pr_completed_duration_sec=(
            completed_duration_sec
        ),
        latest_pr_achieved_date=pr_achieved_date,
    )

    try:
        with engine.begin() as connection:
            complete_runner_onboarding(
                connection,
                app_user_id=app_user_id,
                onboarding=onboarding,
            )
    except OnboardingError:
        st.error(
            "Unable to complete onboarding because of  "
            "invalid authorization result."
        )
        return
    except SQLAlchemyError:
        st.error(
            "Can not save profile into PostgreSQL. "
            "PLease try again."
        )
        return

    st.success("Athlete profile completed.")
    st.rerun()


def require_authorization() -> AuthorizationResult:
    if not st.user.is_logged_in:
        st.title("🏃 Running Training Analytics")
        st.info(
            "Private application. "
            "Please login by invited Google account."
        )
        st.button(
            "Sign in with Google",
            on_click=st.login,
            type="primary",
        )
        st.stop()

    try:
        identity = parse_google_identity(
            st.user.to_dict()
        )

        with engine.begin() as connection:
            return authorize_google_identity(
                connection,
                identity,
            )
    except (ValueError, AuthorizationError):
        st.error(
            "Google does not provide a valid identity "
            "or invalid athlete eligibility."
        )
    except SQLAlchemyError:
        st.error(
            "Google account is uninvited, "
            "blocked or no access yet."
        )

    st.button(
        "Sign out",
        on_click=st.logout,
        key="authorization_logout",
    )
    st.stop()


authorization = require_authorization()

if (
    authorization.authorization_state
    == ONBOARDING_REQUIRED_STATE
):

    show_runner_onboarding(
        app_user_id=authorization.app_user_id
    )

    st.button(
        "Sign out",
        on_click=st.logout,
        key="onboarding_logout",
    )

    st.stop()

if authorization.authorization_state != AUTHORIZED_STATE:
    st.error("Invalid authorization state.")
    st.stop()

authorized_accesses = list(authorization.accesses)

access_by_athlete_id = {
    access.athlete_id: access
    for access in authorized_accesses
}

if len(access_by_athlete_id) == 1:
    current_access = next(
        iter(access_by_athlete_id.values())
    )
    st.sidebar.caption(
        f"Athlete: {current_access.full_name}"
    )
else:
    selected_athlete_id = st.sidebar.selectbox(
        "Choose athlete",
        options=list(access_by_athlete_id.keys()),
        format_func=lambda athlete_id: (
            access_by_athlete_id[
                athlete_id
            ].full_name
        ),
    )
    current_access = access_by_athlete_id[
        selected_athlete_id
    ]

current_athlete_id = current_access.athlete_id

with engine.begin() as connection:
    connection.execute(
        text(
            """
            UPDATE public.training_plans
            SET status = 'COMPLETED'
            WHERE athlete_id = :athlete_id
              AND status = 'ACTIVE'
              AND end_date < CURRENT_DATE
            """
        ),
        {"athlete_id": int(current_athlete_id)},
    )

render_goal_list(
    engine=engine,
    athlete_id=int(current_athlete_id),
    can_manage=current_access.access_role in {"RUNNER", "OWNER", "COACH"}
)

st.divider()
render_ten_k_preview(
    read_query=read_query,
    athlete_id=int(current_athlete_id),
    engine=engine,
)

st.sidebar.caption(
    f"Role: {current_access.access_role}"
)

st.sidebar.button(
    "Sign out",
    on_click=st.logout,
    key="sidebar_logout",
)


st.title("🏃 Running Training Analytics")

st.caption(
    "Track completion, readiness, strength, mobility, "
    "and adaptive plan recommendations."
)

owner_accesses = [
    access
    for access in authorization.accesses
    if access.access_role == "OWNER"
]

if owner_accesses:
    with st.expander(
        "✉️ Invitation management",
        expanded=False,
    ):
        show_owner_invitation_manager(
            app_user_id=authorization.app_user_id,
            owner_accesses=owner_accesses,
        )

try:
    plans = read_query(
        """
        SELECT
            plan_id AS "PlanID",
            plan_name AS "PlanName",
            start_date AS "StartDate",
            end_date AS "EndDate",
            status AS "Status"
        FROM public.training_plans
        WHERE athlete_id = :athlete_id
        ORDER BY start_date
        """,
        {
            "athlete_id": current_athlete_id,
        },
    )
except Exception as error:
    show_database_error(error)
    st.stop()


if plans.empty:
    st.warning(
        "No Training Plan exists in PostgreSQL."
    )
    st.stop()


plan_names = {
    int(row.PlanID): (
        f"{row.PlanName} "
        f"({row.StartDate:%Y-%m-%d} → "
        f"{row.EndDate:%Y-%m-%d})"
    )
    for row in plans.itertuples()
}


saved_plan_id = None
saved_plan_id_raw = st.query_params.get("plan_id")

if saved_plan_id_raw is not None:
    try:
        candidate_plan_id = int(saved_plan_id_raw)
    except (TypeError, ValueError):
        candidate_plan_id = None

    if candidate_plan_id in plan_names:
        saved_plan_id = candidate_plan_id

default_plan_index = (
    list(plan_names.keys()).index(saved_plan_id)
    if saved_plan_id is not None
    else 0
)

selected_plan_id = st.sidebar.selectbox(
    "Choose Training Plan",
    options=list(plan_names.keys()),
    index=default_plan_index,
    key="selected_training_plan",
    format_func=lambda plan_id: plan_names[plan_id],
)

selected_plan_status = plans.loc[
    plans["PlanID"] == selected_plan_id,
    "Status",
].iloc[0]

if selected_plan_status == "DRAFT":
    st.sidebar.caption("Plan này hiện đang ở trạng thái DRAFT.")

    if st.sidebar.button(
        "Kích hoạt plan nháp",
        key=f"activate_draft_plan_{selected_plan_id}",
    ):
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        UPDATE public.training_plans
                        SET status = 'COMPLETED'
                        WHERE athlete_id = :athlete_id
                          AND status = 'ACTIVE'
                          AND end_date < CURRENT_DATE
                        """
                    ),
                    {"athlete_id": int(current_athlete_id)},
                )

                has_overlap = connection.execute(
                    text(
                        """
                        SELECT EXISTS (
                            SELECT 1
                            FROM public.training_plans AS active
                            JOIN public.training_plans AS draft
                              ON draft.plan_id = :plan_id
                            WHERE active.athlete_id = :athlete_id
                              AND active.status = 'ACTIVE'
                              AND active.plan_id <> draft.plan_id
                              AND daterange(
                                    active.start_date,
                                    active.end_date,
                                    '[]'
                                  )
                                  &&
                                  daterange(
                                    draft.start_date,
                                    draft.end_date,
                                    '[]'
                                  )
                        )
                        """
                    ),
                    {
                        "athlete_id": int(current_athlete_id),
                        "plan_id": int(selected_plan_id),
                    },
                ).scalar_one()

                if has_overlap:
                    raise ValueError(
                        "Plan dates overlap another ACTIVE plan."
                    )

                result = connection.execute(
                    text(
                        """
                        UPDATE public.training_plans
                        SET status = 'ACTIVE'
                        WHERE plan_id = :plan_id
                          AND athlete_id = :athlete_id
                          AND status = 'DRAFT'
                          AND start_date <= CURRENT_DATE
                          AND end_date >= CURRENT_DATE
                        """
                    ),
                    {
                        "plan_id": int(selected_plan_id),
                        "athlete_id": int(current_athlete_id),
                    },
                )

                if result.rowcount != 1:
                    raise ValueError(
                        "Plan must be DRAFT and include today's date."
                    )

            st.success("Đã kích hoạt plan.")
            st.cache_data.clear()
            st.rerun()

        except (SQLAlchemyError, ValueError) as error:
            st.error(f"Không kích hoạt được plan: {error}")

st.query_params["plan_id"] = str(selected_plan_id)


previous_plan_id = st.session_state.get(
    "preview_plan_id"
)

if (
    previous_plan_id is not None
    and previous_plan_id != selected_plan_id
):
    st.session_state.pop(
        "adaptive_preview",
        None,
    )

st.session_state["preview_plan_id"] = (
    selected_plan_id
)


if st.sidebar.button("Refresh data"):
    st.cache_data.clear()

    st.session_state.pop(
        "adaptive_preview",
        None,
    )

    st.session_state.pop(
        "apply_success",
        None,
    )

    st.session_state.pop(
        "rollback_success",
        None,
    )

    st.rerun()



adaptive = safe_read_query(
    "Adaptive Plan",
    """
    SELECT
        plan_id AS "PlanID",
        plan_name AS "PlanName",
        start_date AS "StartDate",
        end_date AS "EndDate",
        due_run_workouts AS "DueRunWorkouts",
        completed_run_workouts AS "CompletedRunWorkouts",
        run_completion_percent AS "RunCompletionPercent",
        support_status AS "SupportStatus",
        recommended_reduction_percent
            AS "RecommendedReductionPercent",
        recommended_increase_percent
            AS "RecommendedIncreasePercent",
        complete_week_count AS "CompleteWeekCount",
        minimum_two_week_run_completion_percent
            AS "MinimumTwoWeekRunCompletionPercent",
        maximum_average_fatigue
            AS "MaximumAverageFatigue",
        maximum_average_stress
            AS "MaximumAverageStress",
        maximum_average_soreness
            AS "MaximumAverageSoreness",
        minimum_average_readiness
            AS "MinimumAverageReadiness",
        next_race_date AS "NextRaceDate",
        days_until_race AS "DaysUntilRace",
        progression_decision AS "ProgressionDecision",
        adaptive_action AS "AdaptiveAction",
        adaptive_reason AS "AdaptiveReason"
    FROM public.vw_adaptive_plan_dashboard
    WHERE plan_id = :plan_id
    """,
    {"plan_id": selected_plan_id},
)
weekly = safe_read_query(
    "Weekly progress",
    """
    SELECT
        plan_id AS "PlanID",
        plan_name AS "PlanName",
        week_start_date AS "WeekStartDate",
        week_end_date AS "WeekEndDate",
        due_run_workouts AS "DueRunWorkouts",
        completed_run_workouts AS "CompletedRunWorkouts",
        run_completion_percent AS "RunCompletionPercent",
        due_strength_sessions AS "DueStrengthSessions",
        completed_strength_sessions
            AS "CompletedStrengthSessions",
        strength_completion_percent
            AS "StrengthCompletionPercent",
        strength_exercise_completion_percent
            AS "StrengthExerciseCompletionPercent",
        due_mobility_sessions AS "DueMobilitySessions",
        completed_mobility_sessions
            AS "CompletedMobilitySessions",
        mobility_completion_percent
            AS "MobilityCompletionPercent",
        strength_status AS "StrengthStatus",
        mobility_status AS "MobilityStatus",
        support_status AS "SupportStatus",
        run_status AS "RunStatus",
        is_complete_week AS "IsCompleteWeek"
    FROM public.vw_training_support_dashboard
    WHERE plan_id = :plan_id
    ORDER BY week_start_date
    """,
    {"plan_id": selected_plan_id},
)
readiness = safe_read_query(
    "Readiness and Fatigue",
    """
    SELECT
        plan_id AS "PlanID",
        week_start_date AS "WeekStartDate",
        week_end_date AS "WeekEndDate",
        run_completion_rate AS "RunCompletionRate",
        wellness_days AS "WellnessDays",
        average_sleep_hours AS "AverageSleepHours",
        average_fatigue_score AS "AverageFatigueScore",
        average_stress_score AS "AverageStressScore",
        average_soreness_score AS "AverageSorenessScore",
        average_readiness_score
            AS "AverageReadinessScore",
        readiness_status AS "ReadinessStatus",
        has_enough_wellness_data
            AS "HasEnoughWellnessData",
        is_complete_week AS "IsCompleteWeek"
    FROM public.vw_weekly_run_readiness
    WHERE plan_id = :plan_id
    ORDER BY week_start_date
    """,
    {"plan_id": selected_plan_id},
)
schedule = safe_read_query(
    "Current plan",
    """
    SELECT
        planned_workout_id AS "PlannedWorkoutID",
        scheduled_date AS "ScheduledDate",
        workout_type AS "WorkoutType",
        workout_name AS "WorkoutName",
        planned_distance_m AS "PlannedDistanceM",
        planned_duration_sec AS "PlannedDurationSec",
        target_pace_sec_per_km AS "TargetPaceSecPerKm",
        target_heart_rate_min AS "TargetHeartRateMin",
        target_heart_rate_max AS "TargetHeartRateMax",
        notes AS "Notes",
        status AS "Status"
    FROM public.planned_workouts
    WHERE plan_id = :plan_id
    ORDER BY scheduled_date
    """,
    {"plan_id": selected_plan_id},
)
strength_details = safe_read_query(
    "Detailed Strength",
    """
    SELECT
        strength.session_date AS "SessionDate",
        exercise.strength_exercise_id
            AS "StrengthExerciseID",
        exercise.exercise_name AS "ExerciseName",
        exercise.exercise_category AS "ExerciseCategory",
        exercise.planned_load_kg AS "PlannedLoadKg",
        exercise.planned_sets AS "PlannedSets",
        exercise.planned_reps_or_duration
            AS "PlannedRepsOrDuration",
        exercise.planned_rpe AS "PlannedRPE",
        exercise.exercise_side AS "ExerciseSide",
        exercise.coaching_notes AS "CoachingNotes",
        exercise.exercise_completed AS "ExerciseCompleted"
    FROM public.strength_exercises AS exercise
    JOIN public.strength_sessions AS strength
      ON strength.strength_session_id =
            exercise.strength_session_id
    JOIN public.training_plans AS training_plan
      ON training_plan.plan_id = :plan_id
     AND training_plan.athlete_id = strength.athlete_id
     AND strength.session_date BETWEEN
            training_plan.start_date
            AND training_plan.end_date
    WHERE strength.athlete_id = :athlete_id
    ORDER BY
        strength.session_date,
        exercise.strength_exercise_id
    """,
    {
        "plan_id": selected_plan_id,
        "athlete_id": current_athlete_id,
    },
)
mobility_details = safe_read_query(
    "Detailed Mobility",
    """
    SELECT
        mobility.session_date AS "SessionDate",
        mobility.routine AS "Routine",
        mobility.completed AS "Completed"
    FROM public.mobility_sessions AS mobility
    JOIN public.training_plans AS training_plan
      ON training_plan.plan_id = :plan_id
     AND training_plan.athlete_id = :athlete_id
    WHERE mobility.athlete_id = :athlete_id
      AND (
          mobility.plan_id = :plan_id
          OR (
              mobility.plan_id IS NULL
              AND mobility.session_date BETWEEN
                  training_plan.start_date
                  AND training_plan.end_date
          )
      )
    ORDER BY mobility.session_date
    """,
    {
        "plan_id": selected_plan_id,
        "athlete_id": current_athlete_id,
    },
)
try:
    history = read_query(
        """
        SELECT
            adjustment_run_id AS "AdjustmentRunID",
            adjustment_type AS "AdjustmentType",
            adjustment_status AS "AdjustmentStatus",
            completion_percent AS "CompletionPercent",
            reduction_percent AS "ReductionPercent",
            increase_percent AS "IncreasePercent",
            window_start_date AS "WindowStartDate",
            window_end_date AS "WindowEndDate",
            scheduled_date AS "ScheduledDate",
            workout_type AS "WorkoutType",
            previous_workout_name AS "PreviousWorkoutName",
            new_workout_name AS "NewWorkoutName",
            previous_planned_distance_m
                AS "PreviousPlannedDistanceM",
            new_planned_distance_m
                AS "NewPlannedDistanceM",
            changed_at AS "ChangedAt"
        FROM public.vw_planned_workout_version_history
        WHERE plan_id = :plan_id
        ORDER BY
            adjustment_run_id DESC,
            scheduled_date
        """,
        {"plan_id": selected_plan_id},
    )
except SQLAlchemyError as error:
    show_database_error(error)
    st.stop()

try:
    latest_applied_adjustment = read_query(
        """
        SELECT
            adjustment_run_id AS "AdjustmentRunID",
            plan_id AS "PlanID",
            adjustment_type AS "AdjustmentType",
            status AS "Status",
            completion_rate AS "CompletionRate",
            reduction_rate AS "ReductionRate",
            increase_rate AS "IncreaseRate",
            window_start_date AS "WindowStartDate",
            window_end_date AS "WindowEndDate",
            reason AS "Reason",
            created_at AS "CreatedAt",
            applied_at AS "AppliedAt"
        FROM public.plan_adjustment_runs
        WHERE plan_id = :plan_id
            AND status = 'APPLIED'
        ORDER BY adjustment_run_id DESC
        LIMIT 1
        """,
        {"plan_id": selected_plan_id},
    )
except SQLAlchemyError as error:
    show_database_error(error)
    st.stop()

data_freshness = safe_read_query(
    "Data update time",
    """
    SELECT
        MAX(imported_at) AS "LatestImportAt"
    FROM
    (
        SELECT MAX(imported_at) AS imported_at
        FROM public.completed_activities
        WHERE athlete_id = :athlete_id

        UNION ALL

        SELECT MAX(imported_at)
        FROM public.daily_wellness
        WHERE athlete_id = :athlete_id

        UNION ALL

        SELECT MAX(imported_at)
        FROM public.strength_sessions
        WHERE athlete_id = :athlete_id

        UNION ALL

        SELECT MAX(imported_at)
        FROM public.mobility_sessions
        WHERE athlete_id = :athlete_id
    ) AS imports
    """,
    {
        "athlete_id": current_athlete_id,
    },
)



if (
    not data_freshness.empty
    and pd.notna(
        data_freshness.iloc[0][
            "LatestImportAt"
        ]
    )
):
    latest_import_at = data_freshness.iloc[0][
        "LatestImportAt"
    ]

    st.sidebar.caption(
        "Latest PostgreSQL update: "
        f"{latest_import_at:%Y-%m-%d %H:%M:%S}"
    )
else:
    st.sidebar.caption(
        "Most recent import time not yet determined."
    )





st.header("1. Current Plan")


schedule_scope = st.segmented_control(
    "Range",
    options=[
        "Next 14 days",
        "Full plan",
    ],
    default="Next 14 days",
)

if schedule.empty:
    st.info(
        "The plan does not yet have any PlannedWorkouts."
    )
else:
    display_schedule = schedule.copy()

    display_schedule["ScheduledDate"] = (
        pd.to_datetime(
            display_schedule[
                "ScheduledDate"
            ]
        )
    )

    if schedule_scope == "Next 14 days":
        try:
            with engine.connect() as connection:
                database_today = connection.execute(
                    text("SELECT CURRENT_DATE")
                ).scalar_one()
        except SQLAlchemyError as error:
            show_database_error(error)
            st.stop()

        today = pd.Timestamp(database_today).normalize()
        end_date = today + pd.Timedelta(
            days=14
        )

        display_schedule = display_schedule[
            (
                display_schedule[
                    "ScheduledDate"
                ] >= today
            )
            & (
                display_schedule[
                    "ScheduledDate"
                ] <= end_date
            )
        ].copy()

    schedule_for_details = display_schedule.copy()


    display_schedule["PlannedKm"] = (
        display_schedule["PlannedDistanceM"]
        / 1000
    )

    display_schedule["PlannedMinutes"] = (
        display_schedule["PlannedDurationSec"]
        / 60
    )

    display_schedule["TargetPace"] = (
        display_schedule[
            "TargetPaceSecPerKm"
        ].apply(
            lambda value: (
                f"{int(value) // 60}:"
                f"{int(value) % 60:02d}/km"
                if pd.notna(value)
                else ""
            )
        )
    )

    display_schedule = display_schedule[
        [
            "ScheduledDate",
            "WorkoutType",
            "WorkoutName",
            "PlannedKm",
            "PlannedMinutes",
            "TargetPace",
            "Status",
        ]
    ]

    st.dataframe(
        display_schedule,
        width="stretch",
        hide_index=True,
        column_config={
            "ScheduledDate": st.column_config.DateColumn(
                "Date",
                format="YYYY-MM-DD",
            ),
            "PlannedKm": st.column_config.NumberColumn(
                "Planned km",
                format="%.1f",
            ),
            "PlannedMinutes":
                st.column_config.NumberColumn(
                    "Planned min",
                    format="%.0f",
                ),
        },
    )


    schedule_csv = display_schedule.to_csv(
        index=False
    ).encode("utf-8-sig")

    st.download_button(
        label="Download plan as CSV",
        data=schedule_csv,
        file_name=(
            f"training_plan_"
            f"{selected_plan_id}.csv"
        ),
        mime="text/csv",
    )

    st.subheader("Detail instruction")

    if not strength_details.empty:
        strength_details = strength_details.copy()
        strength_details["SessionDate"] = pd.to_datetime(
            strength_details["SessionDate"]
        ).dt.normalize()

    if not mobility_details.empty:
        mobility_details = mobility_details.copy()
        mobility_details["SessionDate"] = pd.to_datetime(
            mobility_details["SessionDate"]
        ).dt.normalize()

    detailed_workout_count = 0

    for workout in schedule_for_details.itertuples():
        scheduled_date = pd.Timestamp(
            workout.ScheduledDate
        ).normalize()
        workout_strength = pd.DataFrame()
        workout_mobility = pd.DataFrame()

        if not strength_details.empty:
            workout_strength = strength_details[
                strength_details["SessionDate"] == scheduled_date
            ].copy()

        if not mobility_details.empty:
            workout_mobility = mobility_details[
                mobility_details["SessionDate"] == scheduled_date
            ].copy()

        workout_notes = (
            str(workout.Notes).strip()
            if pd.notna(workout.Notes)
            else ""
        )

        if (
            workout_strength.empty
            and workout_mobility.empty
            and not workout_notes
        ):
            continue

        detailed_workout_count += 1
        expander_label = (
            f"{scheduled_date:%Y-%m-%d} · "
            f"{workout.WorkoutName}"
        )

        with st.expander(expander_label):
            if workout_notes:
                st.markdown("**Training note**")
                st.text(workout_notes)

            if not workout_strength.empty:
                st.markdown("**Strength**")
                strength_display = workout_strength[
                    [
                        "ExerciseName",
                        "ExerciseCategory",
                        "PlannedLoadKg",
                        "PlannedSets",
                        "PlannedRepsOrDuration",
                        "PlannedRPE",
                        "ExerciseSide",
                        "CoachingNotes",
                        "ExerciseCompleted",
                    ]
                ].rename(
                    columns={
                        "ExerciseName": "Excercise",
                        "ExerciseCategory": "Category",
                        "PlannedLoadKg": "Load (Kg)",
                        "PlannedSets": "Sets",
                        "PlannedRepsOrDuration": "Reps / Duration",
                        "PlannedRPE": "RPE",
                        "ExerciseSide": "Side",
                        "CoachingNotes": "Instructions",
                        "ExerciseCompleted": "Completed",
                    }
                )

                st.dataframe(
                    strength_display,
                    width="stretch",
                    hide_index=True,
                    column_config={
                        "Load (Kg)": st.column_config.NumberColumn(
                            format="%.1f"
                        ),
                        "Completed": (
                            st.column_config.CheckboxColumn()
                        ),
                    },
                )

            if not workout_mobility.empty:
                st.markdown("**Mobility / activation**")

                for mobility in workout_mobility.itertuples():
                    if pd.notna(mobility.Routine):
                        routine_sections = str(
                            mobility.Routine
                        ).split("|")

                        for section in routine_sections:
                            instructions = [
                                instruction.strip()
                                for instruction in section.split(";")
                                if instruction.strip()
                            ]

                            for instruction in instructions:
                                st.text(f"• {instruction}")

    if detailed_workout_count == 0:
        st.info(
            "No Strength, Mobility instruction"
            "or Notes for chosen region."
        )



render_training_entry(
    engine=engine,
    athlete_id=int(current_athlete_id),
    plan_id=int(selected_plan_id),
    clear_dashboard_cache=read_query.clear,
)

st.divider()


st.header("2. Adaptive Plan")


if adaptive.empty:
    st.info(
        "This plan does not have enough data to be created"
        "Adaptive Plan recommendation."
    )
else:
    result = adaptive.iloc[0]

    column1, column2, column3, column4 = (
        st.columns(4)
    )

    column1.metric(
        "Run completion",
        format_percent(
            result["RunCompletionPercent"]
        ),
    )

    column2.metric(
        "Proposed Reduction",
        format_percent(
            result[
                "RecommendedReductionPercent"
            ]
        ),
    )

    column3.metric(
        "Proposed Progression",
        format_percent(
            result[
                "RecommendedIncreasePercent"
            ]
        ),
    )

    column4.metric(
        "Lowest Readiness",
        format_optional_number(
            result["MinimumAverageReadiness"]
        ),
    )

    adaptive_action = result["AdaptiveAction"]

    if adaptive_action == "CONTROLLED PROGRESSION":
        st.success(
            f"Propose: {adaptive_action}"
        )
    elif "REDUCTION" in str(adaptive_action):
        st.warning(
            f"Propose: {adaptive_action}"
        )
    else:
        st.info(
            f"Propose: {adaptive_action}"
        )

    st.write(
        result["AdaptiveReason"]
    )

    race_column, fatigue_column, status_column = (
        st.columns(3)
    )

    race_column.metric(
        "The next race",
        (
            result["NextRaceDate"].strftime(
                "%Y-%m-%d"
            )
            if pd.notna(result["NextRaceDate"])
            else "N/A"
        ),
    )

    fatigue_column.metric(
        "Highest fatigue (2 weeks)",
        format_optional_number(
            result["MaximumAverageFatigue"]
        ),
    )

    status_column.metric(
        "Progression status",
        (
            result["ProgressionDecision"]
            if pd.notna(
                result["ProgressionDecision"]
            )
            else "N/A"
        ),
    )


st.subheader("Preview Adaptive Plan")


apply_success = st.session_state.get(
    "apply_success"
)

if (
    apply_success is not None
    and apply_success["plan_id"]
        == int(selected_plan_id)
):
    st.success(
        "Adaptive Plan is applied successfully."
    )

    st.caption(
        f"Adjustment type: "
        f"{apply_success['adjustment_type']}"
    )

    for frame in apply_success["frames"]:
        if not frame.empty:
            st.dataframe(
                frame,
                use_container_width=True,
                hide_index=True,
            )

    if st.button(
        "Close Apply Notification",
        type="secondary",
    ):
        st.session_state.pop(
            "apply_success",
            None,
        )

        st.rerun()


window_days = st.number_input(
    "Preview window (days)",
    min_value=1,
    max_value=28,
    value=7,
    step=1,
)


if adaptive.empty:
    st.info(
        "There is no Adaptive Plan for Preview."
    )

else:
    preview_result = adaptive.iloc[0]

    reduction_percent = (
        0.0
        if pd.isna(
            preview_result[
                "RecommendedReductionPercent"
            ]
        )
        else float(
            preview_result[
                "RecommendedReductionPercent"
            ]
        )
    )

    increase_percent = (
        0.0
        if pd.isna(
            preview_result[
                "RecommendedIncreasePercent"
            ]
        )
        else float(
            preview_result[
                "RecommendedIncreasePercent"
            ]
        )
    )

    preview_type = None
    preview_label = None

    if reduction_percent > 0:
        preview_type = "REDUCTION"
        preview_label = (
            f"Reduction preview "
            f"{reduction_percent:.1f}%"
        )

    elif increase_percent > 0:
        preview_type = "PROGRESSION"
        preview_label = (
            f"Progression preview "
            f"{increase_percent:.1f}%"
        )

    else:
        st.info(
            "Adaptive Plan is proposed to remain unchanged."
            "No Adjustment to Preview."
        )

    if preview_type is not None:
        st.warning(
            "Preview only show proposions. "
            "PlannedWorkouts have not been changed."
        )

        if st.button(
            preview_label,
            type="primary",
        ):
            try:
                with st.spinner(
                    "Preview Creating..."
                ):
                    frames = (
                        execute_preview_procedure(
                            preview_type,
                            int(selected_plan_id),
                            int(window_days),
                        )
                    )

                st.session_state[
                    "adaptive_preview"
                ] = {
                    "plan_id": int(
                        selected_plan_id
                    ),
                    "preview_type": preview_type,
                    "window_days": int(
                        window_days
                    ),
                    "frames": frames,

                    # Preview chỉ được Apply trong 10 phút.
                    "created_at": time.time(),

                    # Token riêng cho các widget xác nhận.
                    "preview_token": time.time_ns(),
                }
            except Exception:
                st.error(
                    "Unable to create Adaptive Plan "
                    "Preview."
                )
                logger.exception(
                    "Adaptive Plan Preview failed"
                )


preview_data = st.session_state.get(
    "adaptive_preview"
)

if (
    preview_data is not None
    and preview_data["plan_id"]
        == int(selected_plan_id)
):
    st.markdown("#### Preview Result")

    st.caption(
        f"Type: "
        f"{preview_data['preview_type']} | "
        f"Range: "
        f"{preview_data['window_days']} days"
    )

    frames = preview_data["frames"]

    if not frames:
        st.info(
            "Stored procedure does not return "
            "result set."
        )

    for index, frame in enumerate(
        frames,
        start=1,
    ):
        if index == 1:
            result_name = "Conclusion"
        elif index == len(frames):
            result_name = (
                "List of proposed workout plans"
            )
        else:
            result_name = (
                f"Additional information {index - 1}"
            )

        st.markdown(
            f"**{result_name}**"
        )

        if frame.empty:
            st.info(
                "No workout to change."
            )
        else:
            st.dataframe(
                frame,
                use_container_width=True,
                hide_index=True,
            )


    workout_frames = [
        frame
        for frame in frames
        if (
            "PlannedWorkoutID"
            in frame.columns
            and not frame.empty
        )
    ]

    has_proposed_workouts = (
        len(workout_frames) > 0
    )

    preview_age_seconds = (
        time.time()
        - preview_data["created_at"]
    )

    preview_is_expired = (
        preview_age_seconds > 600
    )

    st.divider()
    st.markdown("#### Apply Adaptive Plan")

    if not has_proposed_workouts:
        st.info(
            "Preview has no workout to change "
            "Unable to Apply."
        )

    elif preview_is_expired:
        st.warning(
            "Preview is older than 10 mins and expired. "
            "Please close Preview and recreate."
        )

    else:
        confirmation_phrase = (
            f"APPLY PLAN {selected_plan_id}"
        )

        st.error(
            "Apply will change directly "
            "PlannedWorkouts. Last version will be "
            "saved in SQL history."
        )

        confirmation_checked = st.checkbox(
            (
                "I have checked list of workouts "
                "and agree to apply changes."
            ),
            key=(
                "apply_checked_"
                f"{preview_data['preview_token']}"
            ),
        )

        typed_confirmation = st.text_input(
            (
                "Fill confirmation correctly: "
                f"{confirmation_phrase}"
            ),
            key=(
                "apply_text_"
                f"{preview_data['preview_token']}"
            ),
        )

        confirmation_is_valid = (
            confirmation_checked
            and typed_confirmation.strip()
                == confirmation_phrase
        )

        if st.button(
            "Apply Adaptive Plan",
            type="primary",
            disabled=not confirmation_is_valid,
            key=(
                "apply_button_"
                f"{preview_data['preview_token']}"
            ),
        ):
            try:
                with st.spinner(
                    "Checking and Applying..."
                ):
                    apply_frames = (
                        execute_apply_procedure(
                            preview_data["preview_type"],
                            int(selected_plan_id),
                            int(preview_data["window_days"]),
                            expected_preview_frames=(
                                preview_data["frames"]
                            ),
                        )
                    )

                st.session_state[
                    "apply_success"
                ] = {
                    "plan_id": int(
                        selected_plan_id
                    ),
                    "adjustment_type":
                        preview_data[
                            "preview_type"
                        ],
                    "frames": apply_frames,
                }

                # Preview đã được sử dụng.
                st.session_state.pop(
                    "adaptive_preview",
                    None,
                )

                # Buộc các query đọc lại dữ liệu SQL.
                st.cache_data.clear()

                st.rerun()

            except PreviewChangedError as error:
                st.session_state[
                    "adaptive_preview"
                ] = {
                    "plan_id": int(selected_plan_id),
                    "preview_type": preview_data[
                        "preview_type"
                    ],
                    "window_days": int(
                        preview_data["window_days"]
                    ),
                    "frames": error.current_frames,
                    "created_at": time.time(),
                    "preview_token": time.time_ns(),
                }

                st.warning(
                    "The plan changed after Preview. "
                    "Review the refreshed Preview before applying."
                )
                st.rerun()

            except Exception:
                st.error(
                    "Unable to apply Adaptive Plan. "
                    "Please review the server log."
                )
                logger.exception(
                    "Adaptive Plan Apply failed"
                )



    if st.button(
        "Close Preview",
        type="secondary",
    ):
        st.session_state.pop(
            "adaptive_preview",
            None,
        )

        st.rerun()



st.header("3. Progression by Week")


if weekly.empty:
    st.info(
        "No completion data by week."
    )
else:
    weekly_chart = weekly[
        [
            "WeekStartDate",
            "RunCompletionPercent",
            "StrengthCompletionPercent",
            "MobilityCompletionPercent",
        ]
    ].copy()

    weekly_chart = weekly_chart.melt(
        id_vars=["WeekStartDate"],
        var_name="Metric",
        value_name="CompletionPercent",
    )

    figure = px.line(
        weekly_chart,
        x="WeekStartDate",
        y="CompletionPercent",
        color="Metric",
        markers=True,
        title=(
            "Run, Strength, and Mobility completion by week"
        ),
    )

    figure.add_hline(
        y=75,
        line_dash="dash",
        line_color="red",
        annotation_text="75% threshold",
    )

    figure.update_yaxes(
        range=[0, 105],
        title="Completion %",
    )

    figure.update_xaxes(
        title="Week start"
    )

    st.plotly_chart(
        figure,
        use_container_width=True,
    )

    with st.expander(
        "View completion data by week"
    ):
        st.dataframe(
            weekly,
            use_container_width=True,
            hide_index=True,
        )


st.header("4. Readiness and Fatigue")


if readiness.empty:
    st.info(
        "No readiness data by week."
    )
else:
    readiness_column1, readiness_column2 = (
        st.columns(2)
    )

    readiness_figure = px.line(
        readiness,
        x="WeekStartDate",
        y="AverageReadinessScore",
        markers=True,
        title="Average Readiness by week",
    )

    readiness_figure.add_hline(
        y=80,
        line_dash="dash",
        line_color="green",
        annotation_text="Readiness 80",
    )

    readiness_figure.update_yaxes(
        range=[0, 100]
    )

    readiness_column1.plotly_chart(
        readiness_figure,
        use_container_width=True,
    )

    fatigue_chart = readiness[
        [
            "WeekStartDate",
            "AverageFatigueScore",
            "AverageStressScore",
            "AverageSorenessScore",
        ]
    ].copy()

    fatigue_chart = fatigue_chart.melt(
        id_vars=["WeekStartDate"],
        var_name="Metric",
        value_name="Score",
    )

    fatigue_figure = px.line(
        fatigue_chart,
        x="WeekStartDate",
        y="Score",
        color="Metric",
        markers=True,
        title="Fatigue, Stress and Soreness",
    )

    fatigue_figure.add_hline(
        y=2,
        line_dash="dash",
        line_color="green",
        annotation_text="Progression limit",
    )

    fatigue_figure.update_yaxes(
        range=[0, 5]
    )

    readiness_column2.plotly_chart(
        fatigue_figure,
        use_container_width=True,
    )

    with st.expander(
        "View readiness by week"
    ):
        st.dataframe(
            readiness,
            use_container_width=True,
            hide_index=True,
        )




st.header("5. Adjustment history")


if history.empty:
    st.info(
        "No Plan adjustment History."
    )
else:
    st.dataframe(
        history,
        use_container_width=True,
        hide_index=True,
    )
    history_csv = history.to_csv(
        index=False
    ).encode("utf-8-sig")

    st.download_button(
        label="Download history as CSV",
        data=history_csv,
        file_name=(
            f"adjustment_history_"
            f"plan_{selected_plan_id}.csv"
        ),
        mime="text/csv",
    )

st.header("6. Rollback adjustment")


rollback_success = st.session_state.get(
    "rollback_success"
)

if (
    rollback_success is not None
    and rollback_success["plan_id"]
        == int(selected_plan_id)
):
    st.success(
        "Adjustment have been rolled back successfully."
    )

    for frame in rollback_success["frames"]:
        if not frame.empty:
            st.dataframe(
                frame,
                use_container_width=True,
                hide_index=True,
            )

    if st.button(
        "Close Rollback notification",
        type="secondary",
    ):
        st.session_state.pop(
            "rollback_success",
            None,
        )

        st.rerun()


if latest_applied_adjustment.empty:
    st.info(
        "No APPLIED adjustment to rollback"
    )

else:
    latest_adjustment = (
        latest_applied_adjustment.iloc[0]
    )

    adjustment_run_id = int(
        latest_adjustment["AdjustmentRunID"]
    )

    adjustment_type = (
        latest_adjustment["AdjustmentType"]
    )

    st.warning(
        "Only the latest APPLIED adjustment can be rolled back."
    )

    rollback_column1, rollback_column2, (
        rollback_column3
    ) = st.columns(3)

    rollback_column1.metric(
        "AdjustmentRunID",
        adjustment_run_id,
    )

    rollback_column2.metric(
        "Adjustment type",
        adjustment_type,
    )

    rollback_column3.metric(
        "Applied at",
        (
            latest_adjustment[
                "AppliedAt"
            ].strftime("%Y-%m-%d %H:%M:%S")

            if pd.notna(
                latest_adjustment["AppliedAt"]
            )
            else "N/A"
        ),
    )

    st.write(
        f"Adjustment range: "
        f"{latest_adjustment['WindowStartDate']} "
        f"→ "
        f"{latest_adjustment['WindowEndDate']}"
    )

    st.write(
        f"Reason: "
        f"{latest_adjustment['Reason']}"
    )

    rollback_workouts = history[
        history["AdjustmentRunID"]
        == adjustment_run_id
    ].copy()

    st.markdown(
        "#### Workouts will be restored"
    )

    if rollback_workouts.empty:
        st.error(
            "No workout history found for this adjustment "
            "Unable to rollback."
        )

    else:
        st.dataframe(
            rollback_workouts[
                [
                    "ScheduledDate",
                    "WorkoutType",
                    "PreviousWorkoutName",
                    "NewWorkoutName",
                    "PreviousPlannedDistanceM",
                    "NewPlannedDistanceM",
                ]
            ],
            use_container_width=True,
            hide_index=True,
        )

        rollback_phrase = (
            f"ROLLBACK {adjustment_run_id}"
        )

        st.error(
            "Rollback will restore the version before adjustment."
        )

        rollback_checked = st.checkbox(
            (
                "I checked the list and "
                "agree to rollback adjustment."
            ),
            key=(
                "rollback_checked_"
                f"{adjustment_run_id}"
            ),
        )

        rollback_text = st.text_input(
            (
                "Fill confirmation correctly: "
                f"{rollback_phrase}"
            ),
            key=(
                "rollback_text_"
                f"{adjustment_run_id}"
            ),
        )

        rollback_is_confirmed = (
            rollback_checked
            and rollback_text.strip()
                == rollback_phrase
        )

        if st.button(
            "Rollback adjustment",
            type="primary",
            disabled=not rollback_is_confirmed,
            key=(
                "rollback_button_"
                f"{adjustment_run_id}"
            ),
        ):
            try:
                with st.spinner(
                    "Rolling back..."
                ):
                    rollback_frames = (
                        execute_rollback_procedure(
                            adjustment_run_id
                        )
                    )

                st.session_state[
                    "rollback_success"
                ] = {
                    "plan_id": int(
                        selected_plan_id
                    ),
                    "adjustment_run_id":
                        adjustment_run_id,
                    "frames": rollback_frames,
                }

                st.session_state.pop(
                    "adaptive_preview",
                    None,
                )

                st.session_state.pop(
                    "apply_success",
                    None,
                )

                st.cache_data.clear()
                st.rerun()

            except Exception:
                st.error(
                    "Unable to roll back adjustment. "
                    "Please review the server log."
                )
                logger.exception(
                    "Adaptive Plan Rollback failed"
                )



st.caption(
    "Adaptive Plan supports Preview, Apply and "
    "Rollback with confirmation."
    "Changes are saved in PostgreSQL."
)
