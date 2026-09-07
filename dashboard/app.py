from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError


# Cho phép dashboard import src.database
PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.database import engine

from src.auth import (
    AthleteAccess,
    AuthorizationError,
    authorize_google_identity,
    parse_google_identity,
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
    """Đọc dữ liệu SQL Server thành DataFrame."""
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

    except Exception as error:
        st.error(
            f"Không thể tải khu vực: "
            f"{section_name}"
        )

        with st.expander(
            f"Chi tiết lỗi — {section_name}"
        ):
            st.code(
                f"{type(error).__name__}: "
                f"{error}"
            )

        return pd.DataFrame()


PREVIEW_PROCEDURES = {
    "REDUCTION": "sp_PreviewMatchingPlan",
    "PROGRESSION": "sp_PreviewControlledProgression",
}


def execute_preview_procedure(
    preview_type: str,
    plan_id: int,
    window_days: int,
) -> list[pd.DataFrame]:
    """
    Chạy stored procedure Preview và trả về
    toàn bộ các result set dưới dạng DataFrame.
    """
    procedure_name = PREVIEW_PROCEDURES.get(
        preview_type
    )

    if procedure_name is None:
        raise ValueError(
            f"Preview type không hợp lệ: "
            f"{preview_type}"
        )

    raw_connection = engine.raw_connection()
    cursor = None
    result_frames: list[pd.DataFrame] = []

    try:
        cursor = raw_connection.cursor()

        cursor.execute(
            (
                f"EXEC dbo.{procedure_name} "
                f"@PlanID = ?, "
                f"@WindowDays = ?"
            ),
            int(plan_id),
            int(window_days),
        )

        while True:
            if cursor.description is not None:
                columns = [
                    column[0]
                    for column in cursor.description
                ]

                rows = cursor.fetchall()

                frame = pd.DataFrame(
                    [
                        tuple(row)
                        for row in rows
                    ],
                    columns=columns,
                )

                result_frames.append(frame)

            if not cursor.nextset():
                break

    finally:
        if cursor is not None:
            cursor.close()

        raw_connection.close()

    return result_frames


APPLY_PROCEDURES = {
    "REDUCTION": "sp_ApplyMatchingPlan",
    "PROGRESSION": "sp_ApplyControlledProgression",
}


def execute_apply_procedure(
    adjustment_type: str,
    plan_id: int,
    window_days: int,
) -> list[pd.DataFrame]:
    """
    Apply Adaptive Plan bằng stored procedure.
    Thành công thì commit, lỗi thì rollback.
    """
    procedure_name = APPLY_PROCEDURES.get(
        adjustment_type
    )

    if procedure_name is None:
        raise ValueError(
            f"Adjustment type không hợp lệ: "
            f"{adjustment_type}"
        )

    raw_connection = engine.raw_connection()
    cursor = None
    result_frames: list[pd.DataFrame] = []

    try:
        cursor = raw_connection.cursor()

        cursor.execute(
            (
                f"EXEC dbo.{procedure_name} "
                f"@PlanID = ?, "
                f"@WindowDays = ?"
            ),
            int(plan_id),
            int(window_days),
        )

        while True:
            if cursor.description is not None:
                columns = [
                    column[0]
                    for column in cursor.description
                ]

                rows = cursor.fetchall()

                frame = pd.DataFrame(
                    [
                        tuple(row)
                        for row in rows
                    ],
                    columns=columns,
                )

                result_frames.append(frame)

            if not cursor.nextset():
                break

        raw_connection.commit()

    except Exception:
        raw_connection.rollback()
        raise

    finally:
        if cursor is not None:
            cursor.close()

        raw_connection.close()

    return result_frames


def execute_rollback_procedure(
    adjustment_run_id: int,
) -> list[pd.DataFrame]:
    """
    Rollback adjustment bằng stored procedure.
    Thành công thì commit, lỗi thì rollback.
    """
    raw_connection = engine.raw_connection()
    cursor = None
    result_frames: list[pd.DataFrame] = []

    try:
        cursor = raw_connection.cursor()

        cursor.execute(
            (
                "EXEC "
                "dbo.sp_RollbackRunPlanAdjustment "
                "@AdjustmentRunID = ?"
            ),
            int(adjustment_run_id),
        )

        while True:
            if cursor.description is not None:
                columns = [
                    column[0]
                    for column in cursor.description
                ]

                rows = cursor.fetchall()

                frame = pd.DataFrame(
                    [
                        tuple(row)
                        for row in rows
                    ],
                    columns=columns,
                )

                result_frames.append(frame)

            if not cursor.nextset():
                break

        raw_connection.commit()

    except Exception:
        raw_connection.rollback()
        raise

    finally:
        if cursor is not None:
            cursor.close()

        raw_connection.close()

    return result_frames


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
    st.error(
        "Không thể đọc dữ liệu từ SQL Server."
    )

    st.code(
        f"{type(error).__name__}: {error}"
    )

    st.info(
        "Hãy kiểm tra SQL Server, file .env "
        "và thử lại bằng test_connection.py."
    )

def require_authorized_access() -> list[AthleteAccess]:
    if not st.user.is_logged_in:
        st.title("🏃 Running Training Analytics")
        st.info(
            "Ứng dụng riêng tư. "
            "Vui lòng đăng nhập bằng tài khoản Google "
            "đã được mời."
        )
        st.button(
            "Đăng nhập bằng Google",
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
            "Google không cung cấp danh tính hợp lệ "
            "hoặc quyền vận động viên không hợp lệ."
        )
    except SQLAlchemyError:
        st.error(
            "Tài khoản Google này chưa được mời, "
            "đã bị khóa hoặc chưa có quyền truy cập."
        )

    st.button(
        "Đăng xuất",
        on_click=st.logout,
        key="authorization_logout",
    )
    st.stop()


authorized_accesses = require_authorized_access()

access_by_athlete_id = {
    access.athlete_id: access
    for access in authorized_accesses
}

if len(access_by_athlete_id) == 1:
    current_access = next(
        iter(access_by_athlete_id.values())
    )
    st.sidebar.caption(
        f"Vận động viên: {current_access.full_name}"
    )
else:
    selected_athlete_id = st.sidebar.selectbox(
        "Chọn vận động viên",
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

st.sidebar.caption(
    f"Vai trò: {current_access.access_role}"
)

st.sidebar.button(
    "Đăng xuất",
    on_click=st.logout,
    key="sidebar_logout",
)


st.title("🏃 Running Training Analytics")

st.caption(
    "Theo dõi completion, readiness, "
    "Strength, Mobility và Adaptive Plan."
)


try:
    plans = read_query(
        """
        SELECT
            PlanID,
            PlanName,
            StartDate,
            EndDate
        FROM dbo.TrainingPlans
        WHERE AthleteID = :athlete_id
        ORDER BY StartDate
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
        "Chưa có Training Plan trong SQL Server."
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


selected_plan_id = st.sidebar.selectbox(
    "Chọn Training Plan",
    options=list(plan_names.keys()),
    format_func=lambda plan_id: plan_names[plan_id],
)


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


if st.sidebar.button("Làm mới dữ liệu"):
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
    SELECT *
    FROM dbo.vw_AdaptivePlanDashboard
    WHERE PlanID = :plan_id
    """,
    {"plan_id": selected_plan_id},
)
weekly = safe_read_query(
    "Tiến độ theo tuần",
    """
    SELECT *
    FROM dbo.vw_TrainingSupportDashboard
    WHERE PlanID = :plan_id
    ORDER BY WeekStartDate
    """,
    {"plan_id": selected_plan_id},
)
readiness = safe_read_query(
    "Readiness và Fatigue",
    """
    SELECT
        PlanID,
        WeekStartDate,
        WeekEndDate,
        RunCompletionRate,
        WellnessDays,
        AverageSleepHours,
        AverageFatigueScore,
        AverageStressScore,
        AverageSorenessScore,
        AverageReadinessScore,
        ReadinessStatus,
        HasEnoughWellnessData,
        IsCompleteWeek
    FROM dbo.vw_WeeklyRunReadiness
    WHERE PlanID = :plan_id
    ORDER BY WeekStartDate
    """,
    {"plan_id": selected_plan_id},
)
schedule = safe_read_query(
    "Lịch tập hiện tại",
    """
    SELECT
        PlannedWorkoutID,
        ScheduledDate,
        WorkoutType,
        WorkoutName,
        PlannedDistanceM,
        PlannedDurationSec,
        TargetPaceSecPerKm,
        Status
    FROM dbo.PlannedWorkouts
    WHERE PlanID = :plan_id
    ORDER BY ScheduledDate
    """,
    {"plan_id": selected_plan_id},
)
history = safe_read_query(
    "Lịch sử điều chỉnh",
    """
    SELECT
        AdjustmentRunID,
        AdjustmentType,
        AdjustmentStatus,
        CompletionPercent,
        ReductionPercent,
        IncreasePercent,
        WindowStartDate,
        WindowEndDate,
        ScheduledDate,
        WorkoutType,
        PreviousWorkoutName,
        NewWorkoutName,
        PreviousPlannedDistanceM,
        NewPlannedDistanceM,
        ChangedAt
    FROM dbo.vw_PlannedWorkoutVersionHistory
    WHERE PlanID = :plan_id
    ORDER BY
        AdjustmentRunID DESC,
        ScheduledDate
    """,
    {"plan_id": selected_plan_id},
)

latest_applied_adjustment = read_query(
    """
    SELECT TOP (1)
        AdjustmentRunID,
        PlanID,
        AdjustmentType,
        Status,
        CompletionRate,
        ReductionRate,
        IncreaseRate,
        WindowStartDate,
        WindowEndDate,
        Reason,
        CreatedAt,
        AppliedAt
        FROM dbo.PlanAdjustmentRuns

    WHERE PlanID = :plan_id
      AND Status = 'APPLIED'
        ORDER BY AdjustmentRunID DESC
    """,
    {"plan_id": selected_plan_id},
)


data_freshness = safe_read_query(
    "Thời gian cập nhật dữ liệu",
    """
    SELECT MAX(ImportedAt) AS LatestImportAt
    FROM
    (
        SELECT MAX(ImportedAt) AS ImportedAt
        FROM dbo.CompletedActivities
        WHERE AthleteID = :athlete_id

        UNION ALL

        SELECT MAX(ImportedAt)
        FROM dbo.DailyWellness
        WHERE AthleteID = :athlete_id

        UNION ALL

        SELECT MAX(ImportedAt)
        FROM dbo.StrengthSessions
        WHERE AthleteID = :athlete_id

        UNION ALL

        SELECT MAX(ImportedAt)
        FROM dbo.MobilitySessions
        WHERE AthleteID = :athlete_id
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
        "SQL cập nhật gần nhất: "
        f"{latest_import_at:%Y-%m-%d %H:%M:%S}"
    )
else:
    st.sidebar.caption(
        "Chưa xác định thời gian import gần nhất."
    )




st.header("1. Adaptive Plan")


if adaptive.empty:
    st.info(
        "Plan này chưa có đủ dữ liệu để tạo "
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
        "Reduction đề xuất",
        format_percent(
            result[
                "RecommendedReductionPercent"
            ]
        ),
    )

    column3.metric(
        "Progression đề xuất",
        format_percent(
            result[
                "RecommendedIncreasePercent"
            ]
        ),
    )

    column4.metric(
        "Readiness thấp nhất",
        format_optional_number(
            result["MinimumAverageReadiness"]
        ),
    )

    adaptive_action = result["AdaptiveAction"]

    if adaptive_action == "CONTROLLED PROGRESSION":
        st.success(
            f"Đề xuất: {adaptive_action}"
        )
    elif "REDUCTION" in str(adaptive_action):
        st.warning(
            f"Đề xuất: {adaptive_action}"
        )
    else:
        st.info(
            f"Đề xuất: {adaptive_action}"
        )

    st.write(
        result["AdaptiveReason"]
    )

    race_column, fatigue_column, status_column = (
        st.columns(3)
    )

    race_column.metric(
        "Ngày Race tiếp theo",
        (
            result["NextRaceDate"].strftime(
                "%Y-%m-%d"
            )
            if pd.notna(result["NextRaceDate"])
            else "N/A"
        ),
    )

    fatigue_column.metric(
        "Fatigue cao nhất (2 tuần)",
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
        "Adaptive Plan đã được Apply thành công."
    )

    st.caption(
        f"Loại adjustment: "
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
        "Đóng thông báo Apply",
        type="secondary",
    ):
        st.session_state.pop(
            "apply_success",
            None,
        )

        st.rerun()


window_days = st.number_input(
    "Số ngày cần xem trước",
    min_value=1,
    max_value=28,
    value=7,
    step=1,
)


if adaptive.empty:
    st.info(
        "Chưa có Adaptive Plan để Preview."
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
            f"Xem trước giảm tải "
            f"{reduction_percent:.1f}%"
        )

    elif increase_percent > 0:
        preview_type = "PROGRESSION"
        preview_label = (
            f"Xem trước tăng tải "
            f"{increase_percent:.1f}%"
        )

    else:
        st.info(
            "Adaptive Plan hiện đề nghị giữ nguyên. "
            "Không có thay đổi cần Preview."
        )

    if preview_type is not None:
        st.warning(
            "Preview chỉ hiển thị đề xuất. "
            "Dữ liệu PlannedWorkouts chưa bị thay đổi."
        )

        if st.button(
            preview_label,
            type="primary",
        ):
            try:
                with st.spinner(
                    "Đang tạo Preview..."
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
            except Exception as error:
                st.error(
                    "Không thể tạo Adaptive Plan "
                    "Preview."
                )

                st.code(
                    f"{type(error).__name__}: "
                    f"{error}"
                )


preview_data = st.session_state.get(
    "adaptive_preview"
)

if (
    preview_data is not None
    and preview_data["plan_id"]
        == int(selected_plan_id)
):
    st.markdown("#### Kết quả Preview")

    st.caption(
        f"Loại: "
        f"{preview_data['preview_type']} | "
        f"Khoảng xem trước: "
        f"{preview_data['window_days']} ngày"
    )

    frames = preview_data["frames"]

    if not frames:
        st.info(
            "Stored procedure không trả về "
            "result set."
        )

    for index, frame in enumerate(
        frames,
        start=1,
    ):
        if index == 1:
            result_name = "Kết luận"
        elif index == len(frames):
            result_name = (
                "Danh sách workout đề xuất"
            )
        else:
            result_name = (
                f"Thông tin bổ sung {index - 1}"
            )

        st.markdown(
            f"**{result_name}**"
        )

        if frame.empty:
            st.info(
                "Không có workout cần thay đổi."
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
            "Preview không có workout cần thay đổi. "
            "Không thể Apply."
        )

    elif preview_is_expired:
        st.warning(
            "Preview đã quá 10 phút và hết hiệu lực. "
            "Hãy đóng Preview và tạo lại."
        )

    else:
        confirmation_phrase = (
            f"APPLY PLAN {selected_plan_id}"
        )

        st.error(
            "Apply sẽ thay đổi trực tiếp "
            "PlannedWorkouts. Phiên bản cũ sẽ "
            "được lưu trong lịch sử SQL."
        )

        confirmation_checked = st.checkbox(
            (
                "Tôi đã kiểm tra danh sách workout "
                "và đồng ý áp dụng thay đổi."
            ),
            key=(
                "apply_checked_"
                f"{preview_data['preview_token']}"
            ),
        )

        typed_confirmation = st.text_input(
            (
                "Nhập chính xác câu xác nhận: "
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
                    "Đang kiểm tra và Apply..."
                ):
                    apply_frames = (
                        execute_apply_procedure(
                            preview_data[
                                "preview_type"
                            ],
                            int(selected_plan_id),
                            int(
                                preview_data[
                                    "window_days"
                                ]
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

            except Exception as error:
                st.error(
                    "Không thể Apply Adaptive Plan. "
                    "SQL đã rollback thay đổi."
                )

                st.code(
                    f"{type(error).__name__}: "
                    f"{error}"
                )



    if st.button(
        "Đóng Preview",
        type="secondary",
    ):
        st.session_state.pop(
            "adaptive_preview",
            None,
        )

        st.rerun()



st.header("2. Tiến độ theo tuần")


if weekly.empty:
    st.info(
        "Chưa có dữ liệu completion theo tuần."
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
            "Run, Strength và Mobility "
            "completion theo tuần"
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
        "Xem dữ liệu completion theo tuần"
    ):
        st.dataframe(
            weekly,
            use_container_width=True,
            hide_index=True,
        )


st.header("3. Readiness và Fatigue")


if readiness.empty:
    st.info(
        "Chưa có dữ liệu readiness theo tuần."
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
        title="Readiness trung bình theo tuần",
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
        title="Fatigue, Stress và Soreness",
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
        "Xem dữ liệu readiness theo tuần"
    ):
        st.dataframe(
            readiness,
            use_container_width=True,
            hide_index=True,
        )


st.header("4. Lịch tập hiện tại")


schedule_scope = st.radio(
    "Phạm vi lịch",
    options=[
        "14 ngày tới",
        "Toàn bộ plan",
    ],
    horizontal=True,
)

if schedule.empty:
    st.info(
        "Plan chưa có PlannedWorkouts."
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

    if schedule_scope == "14 ngày tới":
        today = pd.Timestamp.today().normalize()
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
        use_container_width=True,
        hide_index=True,
        column_config={
            "ScheduledDate": st.column_config.DateColumn(
                "Ngày",
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
        label="Tải lịch tập CSV",
        data=schedule_csv,
        file_name=(
            f"training_plan_"
            f"{selected_plan_id}.csv"
        ),
        mime="text/csv",
    )



st.header("5. Lịch sử điều chỉnh")


if history.empty:
    st.info(
        "Plan chưa có lịch sử adjustment."
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
        label="Tải lịch sử CSV",
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
        "Adjustment đã được rollback thành công."
    )

    for frame in rollback_success["frames"]:
        if not frame.empty:
            st.dataframe(
                frame,
                use_container_width=True,
                hide_index=True,
            )

    if st.button(
        "Đóng thông báo Rollback",
        type="secondary",
    ):
        st.session_state.pop(
            "rollback_success",
            None,
        )

        st.rerun()


if latest_applied_adjustment.empty:
    st.info(
        "Plan không có adjustment APPLIED "
        "có thể rollback."
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
        "Chỉ adjustment APPLIED mới nhất "
        "được phép rollback."
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
        f"Khoảng điều chỉnh: "
        f"{latest_adjustment['WindowStartDate']} "
        f"→ "
        f"{latest_adjustment['WindowEndDate']}"
    )

    st.write(
        f"Lý do: "
        f"{latest_adjustment['Reason']}"
    )

    rollback_workouts = history[
        history["AdjustmentRunID"]
        == adjustment_run_id
    ].copy()

    st.markdown(
        "#### Các workout sẽ được khôi phục"
    )

    if rollback_workouts.empty:
        st.error(
            "Không tìm thấy lịch sử workout cho "
            "adjustment này. Không thể rollback."
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
            "Rollback sẽ thay phiên bản hiện tại "
            "bằng dữ liệu trước adjustment."
        )

        rollback_checked = st.checkbox(
            (
                "Tôi đã kiểm tra danh sách và "
                "đồng ý rollback adjustment."
            ),
            key=(
                "rollback_checked_"
                f"{adjustment_run_id}"
            ),
        )

        rollback_text = st.text_input(
            (
                "Nhập chính xác câu xác nhận: "
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
                    "Đang rollback..."
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

            except Exception as error:
                st.error(
                    "Không thể rollback. "
                    "SQL đã hủy giao dịch."
                )

                st.code(
                    f"{type(error).__name__}: "
                    f"{error}"
                )



st.caption(
    "Adaptive Plan hỗ trợ Preview, Apply và "
    "Rollback có xác nhận. Mọi thay đổi được "
    "lưu lịch sử trong SQL Server."
)