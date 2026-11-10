from datetime import date, timedelta

import pandas as pd
import streamlit as st
from sqlalchemy import text

from src.plan_generator import (
    GeneratedTrainingPlan,
    TrainingPlanInput,
    build_daily_mobility,
    validate_generated_plan,
)
from src.ai_plan_service import (
    convert_and_validate_ai_workouts,

    request_ai_workouts,
)


def load_latest_pr(
    read_query,
    athlete_id: int,
):
    result = read_query(
        """
        SELECT
            distance_m,
            completed_duration_sec,
            achieved_date
        FROM public.athlete_personal_records
        WHERE athlete_id = :athlete_id
        ORDER BY achieved_date DESC, personal_record_id DESC
        LIMIT 1
        """,
        {"athlete_id": athlete_id},
    )

    if result.empty:
        return None

    return result.iloc[0].to_dict()

def load_mobility_reference_routines(
    read_query,
    athlete_id: int,
) -> dict[str, str]:
    query = """
        WITH routine_usage AS (
            SELECT
                CASE
                    WHEN btrim(routine) ILIKE 'AM activation:%'
                        THEN 'ACTIVATION'
                    WHEN btrim(routine) ILIKE 'Recovery mobility:%'
                        THEN 'RECOVERY'
                END AS routine_type,
                btrim(routine) AS routine,
                COUNT(*) FILTER (
                    WHERE athlete_id = :athlete_id
                ) AS athlete_usage,
                COUNT(*) AS total_usage
            FROM public.mobility_sessions
            WHERE routine IS NOT NULL
              AND btrim(routine) <> ''
              AND (
                    btrim(routine) ILIKE 'AM activation:%'
                 OR btrim(routine) ILIKE 'Recovery mobility:%'
              )
            GROUP BY 1, 2
        ),
        ranked_routines AS (
            SELECT
                routine_type,
                routine,
                ROW_NUMBER() OVER (
                    PARTITION BY routine_type
                    ORDER BY
                        athlete_usage DESC,
                        total_usage DESC,
                        routine
                ) AS routine_rank
            FROM routine_usage
        )
        SELECT routine_type, routine
        FROM ranked_routines
        WHERE routine_rank = 1
    """

    result = read_query(
        query,
        {"athlete_id": athlete_id},
    )

    routines = {
        row["routine_type"]: row["routine"].strip()
        for row in result.to_dict(orient="records")
    }

    required_types = {"ACTIVATION", "RECOVERY"}
    missing_types = required_types - routines.keys()

    if missing_types:
        raise ValueError(
            "Database thiếu mẫu Mobility: "
            + ", ".join(sorted(missing_types))
        )

    return routines

def render_ten_k_preview(
    read_query,
    athlete_id: int
) -> None:
    st.header("Preview kế hoạch")

    latest_pr = load_latest_pr(
        read_query,
        athlete_id,
    )

    if latest_pr is None:
        st.warning("Chưa có PR trong hồ sơ runner. Cần có PR trước khi tạo Preview.")
        return

    st.caption(
        f"PR gần nhất trong hồ sơ: {latest_pr['distance_m'] / 1000:g} km, "
        f"{latest_pr['completed_duration_sec'] // 60} phút, "
        f"ngày {latest_pr['achieved_date']}."
    )

    st.sidebar.header("Thông tin kế hoạch")


    race_date = st.sidebar.date_input(
        "Ngày thi",
        value=date(2027, 1, 24),
    )

    plan_level = st.sidebar.selectbox(
        "Trình độ",
        ["BEGINNER", "INTERMEDIATE", "ADVANCED"],
    )

    allowed_goal_distance = {
        "BEGINNER": [5_000, 10_000],
        "INTERMEDIATE": [10_000, 21_097],
        "ADVANCED": [21_097, 42_195],
    }

    goal_distance_m = st.sidebar.selectbox(
        "Cự ly mục tiêu",
        options=allowed_goal_distance[plan_level],
        format_func=lambda distance: {
            5_000: "5K",
            10_000: "10K",
            21_097: "Half Marathon",
            42_195: "Marathon",
        }[distance],
    )

    allowed_runs_per_week = {
        "BEGINNER": [4],
        "INTERMEDIATE": [4,5],
        "ADVANCED": [5],
    }

    has_injury = st.sidebar.checkbox(
        "Đang có chấn thương",
        value=False,
    )

    runs_per_week = st.sidebar.selectbox(
        "Số buổi chạy/tuần",
        options=allowed_runs_per_week[plan_level]
    )

    weekday_names = [
        'Thứ Hai',
        'Thứ Ba',
        'Thứ Tư',
        'Thứ Năm',
        'Thứ Sáu',
        'Thứ Bảy',
        'Chủ Nhật',
    ]

    rest_weekday = st.sidebar.selectbox(
        "Ngày nghỉ chính",
        options=range(7),
        format_func=lambda weekday: weekday_names[weekday],
    )

    plan_input = TrainingPlanInput(
        race_date=race_date,
        goal_distance_m=goal_distance_m,
        latest_pr_distance_m=int(latest_pr["distance_m"]),
        latest_pr_duration_sec=int(latest_pr["completed_duration_sec"]),
        runs_per_week=runs_per_week,
        plan_level=plan_level,
        rest_weekday=rest_weekday,
        has_injury=has_injury,
    )

    full_plan_start = (
        plan_input.race_date
        - timedelta(weeks=17)
        + timedelta(days=1)
    )

    today_week_start = date.today() - timedelta(
        days=date.today().weekday()
    )

    available_start = max(full_plan_start, today_week_start)
    available_weeks = (
        plan_input.race_date - available_start
    ).days // 7 + 1

    if available_weeks < 17:
        st.warning(
            f"Chỉ còn {available_weeks} tuần đến ngày thi. "
            "Kế hoạch đã được rút ngắn; thời gian này ít hơn "
            "mức khuyến nghị 17 tuần."
        )

    if st.button("Tạo Preview kế hoạch"):
        for key in (
            "plan_preview_workouts",
            "plan_preview_mobility",
            "plan_preview_athlete_id",
            "plan_preview_input",
        ):
            st.session_state.pop(key, None)

        try:
            mobility_templates = load_mobility_reference_routines(
                read_query,
                athlete_id,
            )
            daily_mobility = build_daily_mobility(
                start_date=available_start,
                race_date=plan_input.race_date,
                rest_weekday=plan_input.rest_weekday,
                activation_routine=mobility_templates["ACTIVATION"],
                recovery_routine=mobility_templates["RECOVERY"],
            )

            ai_output = request_ai_workouts(
                plan_input,
                available_start,
            )
            workouts = convert_and_validate_ai_workouts(
                ai_output,
                plan_input,
                available_start,
            )

            generated_plan = GeneratedTrainingPlan(
                workouts=workouts,
                daily_mobility=daily_mobility,
            )
            validate_generated_plan(
                generated_plan,
                available_start,
                plan_input.race_date,
            )
        except Exception as error:
            st.error(f"Không tạo được Preview: {error}")
        else:
            st.session_state["plan_preview_workouts"] = workouts
            st.session_state["plan_preview_mobility"] = daily_mobility
            st.session_state["plan_preview_athlete_id"] = athlete_id
            st.session_state["plan_preview_input"] = plan_input

    if (
        "plan_preview_workouts" not in st.session_state
        or "plan_preview_mobility" not in st.session_state
        or st.session_state.get("plan_preview_athlete_id") != athlete_id
        or st.session_state.get("plan_preview_input") != plan_input
    ):
        st.info("Nhấn “Tạo Preview kế hoạch” để tạo lịch.")
        return

    workouts = st.session_state["plan_preview_workouts"]

    rows = [
        {
            "Ngày": workout.scheduled_date,
            "Loại": workout.workout_type,
            "Tên buổi tập": workout.workout_name,
            "Quãng đường km": (
                None
                if workout.planned_distance_m is None
                else workout.planned_distance_m/1000
            ),
            "Thời lượng phút": (
                None
                if workout.planned_duration_sec is None
                else round(workout.planned_duration_sec/60)
            ),
            "Pace giây/km": workout.target_pace_sec_per_km,
            "Ghi chú": workout.notes,
        }
        for workout in workouts
    ]

    preview = pd.DataFrame(rows)

    training_session_count = sum(
        workout.workout_type != "REST"
        for workout in workouts
    )
    st.metric("Số buổi tập", training_session_count)
    st.metric(
        "Tổng quãng đường km",
        round(preview["Quãng đường km"].sum(), 1),
    )

    st.dataframe(
        preview,
        hide_index=True,
    )


    daily_mobility = st.session_state["plan_preview_mobility"]
    mobility_preview = pd.DataFrame(
        [
            {
                "Ngày": item.scheduled_date,
                "Bài Mobility": item.routine,
            }
            for item in daily_mobility
        ]
    )

    st.subheader("Mobility hằng ngày")
    st.dataframe(mobility_preview, hide_index=True)

    st.divider()
    st.subheader("Xác nhận bản Preview")

    confirmed = st.checkbox(
        "Tôi đã xem và xác nhận bản Preview này",
    )

    if confirmed:
        st.success(
            "Preview đã được xác nhận trong phiên hiện tại. "
            "Chưa ghi dữ liệu vào database."
        )
    else:
        st.info(
            "Hãy kiểm tra lịch trước khi xác nhận."
        )
