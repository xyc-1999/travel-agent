from __future__ import annotations

from datetime import date, timedelta

from app.agents.requirement import analyze_requirement
from app.models import BudgetSpec, RequirementInput


TODAY = date(2026, 11, 4)


def test_natural_language_extracts_core_fields():
    analysis = analyze_requirement(
        RequirementInput(
            raw_text=(
                "从上海去成都玩，11月20日到11月23日，3个人，人均3000元，"
                "想去宽窄巷子，不吃辣，节奏轻松一点"
            )
        ),
        session_id="nl-1",
        today=TODAY,
    )
    draft = analysis.draft

    assert draft.origin == "上海"
    assert draft.destination == "成都"
    assert draft.start_date == date(2026, 11, 20)
    assert draft.end_date == date(2026, 11, 23)
    assert draft.days == 4
    assert draft.travelers == 3
    assert draft.budget.amount == 3000
    assert draft.budget.basis == "per_person"
    assert "宽窄巷子" in draft.must_visit
    assert "辣" in draft.avoid
    assert draft.pace == "轻松"
    assert analysis.status == "ready"


def test_unsupported_province_requires_city_choice():
    analysis = analyze_requirement(
        RequirementInput(raw_text="下个月想去四川玩 4 天，从北京出发，预算 5000"),
        session_id="province-1",
        today=TODAY,
    )
    assert analysis.status == "need_clarification"
    assert analysis.questions[0].id == "destination_city"
    assert analysis.questions[0].field == "destination"
    assert analysis.draft.destination == "四川"
    # 省级目的地不能直接当作城市生成行程
    assert analysis.draft.days == 4


def test_municipality_is_treated_as_city():
    analysis = analyze_requirement(
        RequirementInput(
            raw_text="11月20日去北京玩3天，从杭州出发，总预算4000元，2个人"
        ),
        session_id="municipality-1",
        today=TODAY,
    )
    assert analysis.draft.destination == "北京"
    assert analysis.status == "ready"


def test_missing_budget_basis_asks_one_question():
    analysis = analyze_requirement(
        RequirementInput(
            raw_text="11月20日到11月22日去成都，2个人，预算6000元"
        ),
        session_id="basis-1",
        today=TODAY,
    )
    assert analysis.draft.budget.amount == 6000
    assert any(question.id == "budget_basis" for question in analysis.questions)
    assert len(analysis.questions) <= 3


def test_date_conflict_is_clarified_not_silently_fixed():
    analysis = analyze_requirement(
        RequirementInput(
            destination="成都",
            origin="上海",
            start_date=date(2026, 11, 20),
            end_date=date(2026, 11, 21),
            days=5,
            travelers=2,
            budget=BudgetSpec(amount=5000, basis="total"),
        ),
        session_id="conflict-1",
        today=TODAY,
    )
    assert analysis.status == "need_clarification"
    assert any(conflict.code == "days_mismatch" for conflict in analysis.conflicts)
    assert any(question.id == "date_range" for question in analysis.questions)


def test_missing_origin_is_asked_not_invented():
    analysis = analyze_requirement(
        RequirementInput(
            destination="杭州",
            start_date=date(2026, 11, 20),
            end_date=date(2026, 11, 22),
            travelers=2,
            budget=BudgetSpec(amount=5000, basis="total"),
        ),
        session_id="origin-1",
        today=TODAY,
    )
    assert analysis.status == "need_clarification"
    assert analysis.draft.origin is None
    assert "origin" not in analysis.field_sources
    assert any(question.id == "origin" for question in analysis.questions)


def test_question_truncation_adds_explicit_assumptions():
    analysis = analyze_requirement(
        RequirementInput(raw_text="我想出去放松一下"),
        session_id="truncate-1",
        today=TODAY,
    )
    assert len(analysis.questions) == 3
    assert "origin" not in {question.id for question in analysis.questions}
    assert any("出发地" in item for item in analysis.assumptions)


def test_incomplete_request_asks_at_most_three_questions():
    analysis = analyze_requirement(
        RequirementInput(raw_text="我想出去放松一下"),
        session_id="empty-1",
        today=TODAY,
    )
    assert analysis.status == "need_clarification"
    assert 1 <= len(analysis.questions) <= 3
    fields = [question.field for question in analysis.questions]
    assert any("destination" in field for field in fields)


def test_form_dates_win_over_stale_raw_text_dates():
    analysis = analyze_requirement(
        RequirementInput(
            raw_text="11月20日到11月22日去成都玩3天，从上海出发，2个人，人均3000元，节奏轻松",
            destination="成都",
            start_date=date(2026, 12, 1),
            end_date=date(2026, 12, 4),
            days=4,
        ),
        session_id="form-wins-1",
        today=TODAY,
    )

    assert analysis.draft.start_date == date(2026, 12, 1)
    assert analysis.draft.end_date == date(2026, 12, 4)
    assert analysis.draft.days == 4
    assert analysis.conflicts == []
    assert "2026-12-01" in analysis.summary
    # 表单留空的字段才由自然语言补齐
    assert analysis.draft.origin == "上海"
    assert analysis.draft.travelers == 2
    assert analysis.draft.pace == "轻松"
    assert analysis.field_sources["start_date"] == "user_form"
    assert analysis.field_sources["travelers"] == "user_text"


def test_list_fields_merge_form_and_text_without_overwriting():
    analysis = analyze_requirement(
        RequirementInput(
            raw_text="11月20日到11月22日去成都，喜欢历史和博物馆，必去宽窄巷子，不去酒吧",
            destination="成都",
            interests=["美食"],
            must_visit=["宽窄巷子"],
            avoid=["辣"],
            start_date=date(2026, 11, 20),
            end_date=date(2026, 11, 22),
        ),
        session_id="list-merge-1",
        today=TODAY,
    )

    assert set(analysis.draft.interests) == {"美食", "历史", "博物馆"}
    assert analysis.draft.must_visit == ["宽窄巷子"]
    assert analysis.draft.avoid == ["辣", "酒吧"]
    assert analysis.field_sources["interests"] == "user_form+user_text"


def test_form_budget_amount_is_not_silently_typed_by_text_basis():
    analysis = analyze_requirement(
        RequirementInput(
            raw_text="人均3000元，11月20日到11月22日去成都",
            destination="成都",
            budget=BudgetSpec(amount=6000),
        ),
        session_id="budget-basis-1",
        today=TODAY,
    )

    # 表单填了金额、但口径未选时，不能被文本里的“人均”悄悄改写口径
    assert analysis.draft.budget.amount == 6000
    assert analysis.draft.budget.basis is None
    assert any(question.id == "budget_basis" for question in analysis.questions)


def test_date_conflict_keeps_original_values_for_clarification_card():
    analysis = analyze_requirement(
        RequirementInput(
            destination="成都",
            origin="上海",
            start_date=date(2026, 12, 1),
            end_date=date(2026, 12, 4),
            days=3,
        ),
        session_id="conflict-keep-1",
        today=TODAY,
    )

    # 澄清卡预填的日期必须与冲突提示一致，不能被自动推导成 12-03
    assert analysis.draft.start_date == date(2026, 12, 1)
    assert analysis.draft.end_date == date(2026, 12, 4)
    assert analysis.draft.days == 3
    assert any("4 天" in conflict.message for conflict in analysis.conflicts)
    assert "2026-12-04" in analysis.summary


def test_yaoqu_marks_must_visit():
    analysis = analyze_requirement(
        RequirementInput(
            raw_text="2026-12-01至2026-12-05从北京去成都，1人，总预算3000，要去杜甫草堂，节奏紧凑",
        ),
        session_id="yaoqu-1",
        today=TODAY,
    )

    assert analysis.draft.must_visit == ["杜甫草堂"]
    assert analysis.field_sources["must_visit"] == "user_text"


def test_yaoqu_city_is_not_treated_as_must_visit():
    analysis = analyze_requirement(
        RequirementInput(raw_text="2026-12-01至2026-12-03想去成都玩"),
        session_id="yaoqu-city-1",
        today=TODAY,
    )

    assert analysis.draft.must_visit == []


def test_travelers_without_ge_suffix_are_parsed():
    analysis = analyze_requirement(
        RequirementInput(
            raw_text="2026-12-01至2026-12-05从北京去西安，4人，总预算3000，想看历史古迹，必去兵马俑，节奏紧凑",
        ),
        session_id="travelers-ren-1",
        today=TODAY,
    )

    assert analysis.draft.travelers == 4
    assert analysis.field_sources["travelers"] == "user_text"
    assert "按 1 人估算" not in analysis.assumptions
    assert "4 人" in analysis.summary


def test_clarify_answers_complete_the_request():
    from app.agents.requirement import _apply_answers

    payload = RequirementInput(raw_text="想去成都玩")
    first = analyze_requirement(payload, session_id="clarify-1", today=TODAY)
    assert first.status == "need_clarification"

    answers = {
        "date_range": f"{TODAY + timedelta(days=20)}|{TODAY + timedelta(days=22)}",
        "budget_amount": "total|6000",
        "origin": "上海",
    }
    updated_payload = _apply_answers(first.draft, answers)
    second = analyze_requirement(updated_payload, session_id="clarify-1", answers=answers, today=TODAY)

    assert second.status == "ready"
    assert second.draft.destination == "成都"
    assert second.draft.days == 3
    assert second.draft.budget.basis == "total"
