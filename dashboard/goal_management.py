import pandas as pd
from datetime import date
from sqlalchemy import text
from sqlalchemy.engine import Engine

import streamlit as st

def load_athlete_goals(
    engine: Engine,
    athlete_id: int,
) -> pd.DataFrame:
    with engine.connect() as connection:
        return pd.read_sql(
            text(
                """
                SELECT
                    goal_id,
                    goal_name,
                    goal_type,
                    target_distance_m,
                    target_duration_sec,
                    target_date,
                    priority_level,
                    status
                FROM public.goals
                WHERE athlete_id = :athlete_id
                ORDER BY target_date, goal_id
                """
            ),
            connection,
            params={"athlete_id": athlete_id},
        )


def render_goal_list(
    engine: Engine,
    athlete_id: int,
    can_manage: bool,
) -> None:
    st.subheader("Mục tiêu chạy bộ")

    goals = load_athlete_goals(engine, athlete_id)

    # ==============================[GOAL-PLAN NOTICE]========================================

    updated_goal_id = st.session_state.pop(
        f"goal_with_plan_updated_{athlete_id}",
        None,
    )

    if updated_goal_id is not None:
        st.info(
            "Goal đã được sửa và đang gắn với plan. "
            "Hãy chọn goal này trong phần Preview để tạo đề xuất plan mới. "
            "Plan hiện tại chưa bị thay đổi."
        )

    if goals.empty:
        st.info("VĐV chưa có mục tiêu nào.")
    else:
        st.dataframe(
            goals,
            hide_index=True,
            use_container_width=True,
        )

    if not can_manage:
        return

    goal_options = {0: "Thêm goal mới"}
    goal_options.update({
        int(row.goal_id): f"{row.goal_name} (ID {row.goal_id})"
        for row in goals.itertuples()
    })

    selected_goal_id = st.selectbox(
        "Chọn goal để sửa",
        options=list(goal_options),
        format_func=lambda goal_id: goal_options[goal_id],
        key=f"edit_goal_{athlete_id}",
    )

    selected_goal = (
        None
        if selected_goal_id == 0
        else goals.loc[
            goals["goal_id"] == selected_goal_id
        ].iloc[0]
    )

    goal_types = ["RACE", "DISTANCE", "PACE", "CONSISTENCY"]

    with st.form("add_goal_form"):
        goal_name = st.text_input(
            "Tên mục tiêu",
            value=(
                str(selected_goal["goal_name"])
                if selected_goal is not None
                else ""
            ),
        )

        goal_type = st.selectbox(
            "Loại mục tiêu",
            goal_types,
            index=(
                goal_types.index(selected_goal["goal_type"])
                if selected_goal is not None
                else 0
            ),
        )

        distance_km = st.number_input(
            "Cự ly (km; 0 nếu không áp dụng)",
            min_value=0.0,
            max_value=500.0,
            value=(
                float(selected_goal["target_distance_m"]) / 1000
                if selected_goal is not None
                and pd.notna(selected_goal["target_distance_m"])
                else 10.0
            ),
            step=0.1,
        )

        target_minutes = st.number_input(
            "Thời gian mục tiêu (phút; 0 nếu không áp dụng)",
            min_value=0.0,
            max_value=100_000.0,
            value=(
                float(selected_goal["target_duration_sec"]) / 60
                if selected_goal is not None
                and pd.notna(selected_goal["target_duration_sec"])
                else 0.0
            ),
            step=1.0,
        )

        target_date = st.date_input(
            "Ngày mục tiêu",
            value=(
                pd.Timestamp(selected_goal["target_date"]).date()
                if selected_goal is not None
                else date.today()
            ),
        )

        priority = st.slider(
            "Mức ưu tiên",
            1,
            5,
            value=(
                int(selected_goal["priority_level"])
                if selected_goal is not None
                else 3
            ),
        )

        submitted = st.form_submit_button(
            "Lưu thay đổi" if selected_goal is not None else "Thêm goal"
        )

    if not submitted:
        return

    if not goal_name.strip():
        st.error("Tên mục tiêu không được để trống.")
        return

    distance_m = round(distance_km * 1000) if distance_km > 0 else None
    target_duration_sec = (
        round(target_minutes * 60) if target_minutes > 0 else None
    )

    if goal_type in {"RACE", "DISTANCE"} and distance_m is None:
        st.error("Goal loại RACE hoặc DISTANCE cần có cự ly.")
        return

    values = {
        "athlete_id": athlete_id,
        "goal_name": goal_name.strip(),
        "goal_type": goal_type,
        "target_distance_m": distance_m,
        "target_duration_sec": target_duration_sec,
        "target_date": target_date,
        "priority_level": priority,
    }

    has_linked_plan = False

    try:
        with engine.begin() as connection:
            if selected_goal_id == 0:
                connection.execute(
                    text(
                        """
                        INSERT INTO public.goals
                        (
                            athlete_id,
                            goal_name,
                            goal_type,
                            target_distance_m,
                            target_duration_sec,
                            target_date,
                            priority_level,
                            status
                        )
                        VALUES
                        (
                            :athlete_id,
                            :goal_name,
                            :goal_type,
                            :target_distance_m,
                            :target_duration_sec,
                            :target_date,
                            :priority_level,
                            'ACTIVE'
                        )
                        """
                    ),
                    values,
                )
            else:
#============================ [GOAL-LINK CHECK]======================================
                has_linked_plan = connection.execute(
                    text(
                        """
                        SELECT EXISTS (
                            SELECT 1
                            FROM public.training_plans
                            WHERE goal_id = :goal_id
                              AND athlete_id = :athlete_id
                        )
                        """
                    ),
                    {
                        "goal_id": selected_goal_id,
                        "athlete_id": athlete_id,
                    },
                ).scalar_one()


                connection.execute(
                    text(
                        """
                        UPDATE public.goals
                        SET
                            goal_name = :goal_name,
                            goal_type = :goal_type,
                            target_distance_m = :target_distance_m,
                            target_duration_sec = :target_duration_sec,
                            target_date = :target_date,
                            priority_level = :priority_level
                        WHERE goal_id = :goal_id
                          AND athlete_id = :athlete_id
                        """
                    ),
                    {**values, "goal_id": selected_goal_id},
                )
    except Exception as error:
        st.error(f"Không lưu được goal: {error}")
    else:
        message = (
            "Đã thêm goal."
            if selected_goal_id == 0
            else "Đã cập nhật goal."
        )
        st.success(message)

#============================[GOAL-PLAN NOTICE]============================================
        if has_linked_plan:
            st.session_state[
                f"goal_with_plan_updated_{athlete_id}"
            ] = selected_goal_id
        st.rerun()