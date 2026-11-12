from datetime import date, timedelta

from dotenv import load_dotenv
from openai import OpenAI

from src.ai_plan_schema import AITrainingPlanOutput
from src.plan_generator import PlannedWorkout, TrainingPlanInput


MODEL = "gpt-6-luna"

WEEKDAY_NAMES = (
    "Thứ Hai",
    "Thứ Ba",
    "Thứ Tư",
    "Thứ Năm",
    "Thứ Sáu",
    "Thứ Bảy",
    "Chủ Nhật",
)


def request_ai_workouts(
    plan_input: TrainingPlanInput,
    start_date: date,
) -> AITrainingPlanOutput:
    plan_input.validate()

    if start_date > plan_input.race_date:
        raise ValueError("Ngày bắt đầu không được sau ngày thi.")

    load_dotenv()

    system_prompt = """
Bạn lập kế hoạch luyện chạy bộ theo dữ liệu vận động viên được cung cấp.
Chỉ trả về dữ liệu workouts đúng schema, không tạo lịch Mobility.
Không chẩn đoán hoặc đưa ra khẳng định y tế.

Áp dụng cấu trúc theo trình độ:
- BEGINNER: ưu tiên chạy dễ, strides ngắn, long run tăng dần;
  có thể xen kẽ cross-training và strength nhẹ; tránh interval/tempo nặng.
- INTERMEDIATE: có interval hoặc hill, tempo, easy/recovery,
  strength và long run.
- ADVANCED: có interval, medium-long, tempo/progression,
  recovery kết hợp strength và quality long run.

Giữ ngày nghỉ chính. Ngày thi phải có đúng một buổi RACE vào ngày thi
và cự ly đúng mục tiêu. Không xếp buổi tập sau ngày thi.
Tăng khối lượng thận trọng, tránh tăng quá 10% giữa các tuần tải;
cứ sau 3–4 tuần tải có tuần giảm tải khoảng 20–30%, rồi taper trước race.
Dùng PR gần nhất để đặt pace hợp lý; ghi rõ buổi easy phải chạy dễ.
Hãy thể hiện ngày nghỉ hoàn toàn bằng một dòng REST.
Mỗi ngày chỉ được có tối đa một dòng workout.
Nếu strength đi kèm buổi chạy, hãy gộp nội dung strength vào tên hoặc ghi chú của buổi chạy,
không tạo thêm một dòng workout cùng ngày.
Với REST, planned_distance_m, planned_duration_sec và
target_pace_sec_per_km phải là null.
Không xếp buổi chạy hoặc cross-training vào ngày nghỉ chính.
"""

    rest_day = WEEKDAY_NAMES[plan_input.rest_weekday]
    latest_pr_pace = plan_input.latest_pr_pace_sec_per_km

    user_prompt = f"""
Hãy tạo lịch trong khoảng từ {start_date.isoformat()}
đến ngày thi {plan_input.race_date.isoformat()}, bao gồm cả ngày thi.

Trình độ: {plan_input.plan_level}
Cự ly mục tiêu: {plan_input.goal_distance_m} mét
Thời gian mục tiêu: {plan_input.target_duration_sec or 'chưa đặt'} giây
Số ngày chạy mỗi tuần: {plan_input.runs_per_week}
Ngày nghỉ chính: {rest_day}
PR gần nhất: {plan_input.latest_pr_distance_m} mét trong
{plan_input.latest_pr_duration_sec} giây
Pace tương ứng PR gần nhất: {latest_pr_pace} giây/km

Mỗi buổi phải có ngày, loại buổi, tên, ghi chú.
Nếu là buổi chạy, điền cự ly, thời lượng và pace phù hợp.
Buổi strength hoặc cross-training có thể để các trường cự ly/pace
không áp dụng ở dạng null.
Không tạo Mobility; ứng dụng sẽ gắn routine từ database.
"""

    response = OpenAI().responses.parse(
        model=MODEL,
        input=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        text_format=AITrainingPlanOutput,
        max_output_tokens=20_000,
    )

    if response.output_parsed is None:
        raise ValueError("AI không trả về kế hoạch theo schema yêu cầu.")

    return response.output_parsed


RUN_TYPES = {
    "EASY",
    "RECOVERY",
    "LONG_RUN",
    "TEMPO",
    "INTERVAL",
    "RACE",
}


def convert_and_validate_ai_workouts(
    ai_output: AITrainingPlanOutput,
    plan_input: TrainingPlanInput,
    start_date: date,
) -> list[PlannedWorkout]:
    plan_input.validate()

    if start_date > plan_input.race_date:
        raise ValueError("Ngày bắt đầu không được sau ngày thi.")

    workouts: list[PlannedWorkout] = []
    seen_dates: set[date] = set()

    for item in ai_output.workouts:
        try:
            scheduled_date = date.fromisoformat(item.scheduled_date)
        except ValueError as error:
            raise ValueError(
                "Ngày buổi tập phải theo định dạng YYYY-MM-DD."
            ) from error

        if scheduled_date.isoformat() != item.scheduled_date:
            raise ValueError("Ngày buổi tập phải theo định dạng YYYY-MM-DD.")

        if not start_date <= scheduled_date <= plan_input.race_date:
            raise ValueError("Có buổi tập nằm ngoài thời gian kế hoạch.")

        if scheduled_date in seen_dates:
            raise ValueError("Chỉ được có một dòng workout mỗi ngày.")
        seen_dates.add(scheduled_date)

        if not item.workout_name.strip() or not item.notes.strip():
            raise ValueError("Tên buổi tập và ghi chú không được để trống.")

        workout_type = item.workout_type

        if scheduled_date == plan_input.race_date and workout_type != "RACE":
            raise ValueError("Ngày thi chỉ được chứa buổi RACE.")

        if workout_type == "RACE" and scheduled_date != plan_input.race_date:
            raise ValueError("Buổi RACE phải đúng vào ngày thi.")

        if workout_type in RUN_TYPES:
            values = (
                item.planned_distance_m,
                item.planned_duration_sec,
                item.target_pace_sec_per_km,
            )
            if any(value is None or value <= 0 for value in values):
                raise ValueError(
                    "Buổi chạy phải có cự ly, thời lượng và pace dương."
                )
        else:
            if item.target_pace_sec_per_km is not None:
                raise ValueError(
                    "Strength, cross-training và REST không có pace chạy."
                )
            numeric_values = (
                item.planned_distance_m,
                item.planned_duration_sec,
            )
            if any(value is not None and value <= 0 for value in numeric_values):
                raise ValueError("Giá trị workout không được âm hoặc bằng 0.")

        if workout_type == "REST" and any(
            value is not None
            for value in (
                item.planned_distance_m,
                item.planned_duration_sec,
                item.target_pace_sec_per_km,
            )
        ):
            raise ValueError("Các trường số của REST phải là null.")

        if (
            scheduled_date.weekday() == plan_input.rest_weekday
            and scheduled_date != plan_input.race_date
            and workout_type != "REST"
        ):
            raise ValueError("Ngày nghỉ chính không được có workout khác.")

        workouts.append(
            PlannedWorkout(
                scheduled_date=scheduled_date,
                workout_type=workout_type,
                workout_name=item.workout_name,
                planned_distance_m=item.planned_distance_m,
                planned_duration_sec=item.planned_duration_sec,
                target_pace_sec_per_km=item.target_pace_sec_per_km,
                notes=item.notes,
            )
        )

    races = [workout for workout in workouts if workout.workout_type == "RACE"]

    if len(races) != 1:
        raise ValueError("Kế hoạch phải có đúng một buổi RACE.")

    if races[0].planned_distance_m != plan_input.goal_distance_m:
        raise ValueError("Cự ly buổi RACE phải khớp cự ly mục tiêu.")

    expected_rest_dates = {
        start_date + timedelta(days=offset)
        for offset in range((plan_input.race_date - start_date).days + 1)
        if (start_date + timedelta(days=offset)).weekday()
        == plan_input.rest_weekday
        and start_date + timedelta(days=offset) != plan_input.race_date
    }
    actual_rest_dates = {
        workout.scheduled_date
        for workout in workouts
        if workout.workout_type == "REST"
    }

    if not expected_rest_dates.issubset(actual_rest_dates):
        raise ValueError("Thiếu dòng REST vào một hoặc nhiều ngày nghỉ chính.")

    return workouts