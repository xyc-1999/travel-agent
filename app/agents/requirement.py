from __future__ import annotations

import re
from datetime import date, timedelta

from app import config
from app.data_loader import (
    city_info,
    cities_data,
    normalize_city_name,
    province_candidates,
    supported_cities,
)
from app.models import (
    BudgetSpec,
    Conflict,
    RequirementAnalysis,
    RequirementInput,
    RequirementQuestion,
)


CN_DIGITS = {"零": 0, "一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _to_int(text: str) -> int | None:
    text = text.strip()
    if text.isdigit():
        return int(text)
    if text == "十":
        return 10
    if "十" in text:
        left, _, right = text.partition("十")
        tens = CN_DIGITS.get(left, 1) if left else 1
        ones = CN_DIGITS.get(right, 0) if right else 0
        return tens * 10 + ones
    if len(text) == 1 and text in CN_DIGITS:
        return CN_DIGITS[text]
    return None


def _parse_date_token(year: int, month: str, day: str) -> date | None:
    try:
        return date(year, int(month), int(day))
    except ValueError:
        return None


def _extract_dates(text: str, today: date) -> tuple[date | None, date | None]:
    # 完整日期：2026-11-20 / 2026年11月20日
    full = re.findall(r"(20\d{2})\s*[年./-]\s*(\d{1,2})\s*[月./-]\s*(\d{1,2})\s*[日号]?", text)
    if len(full) >= 2:
        start = _parse_date_token(int(full[0][0]), full[0][1], full[0][2])
        end = _parse_date_token(int(full[1][0]), full[1][1], full[1][2])
        return start, end
    if len(full) == 1:
        start = _parse_date_token(int(full[0][0]), full[0][1], full[0][2])
        return start, None

    # 同月区间：11月20日-23日 / 11月20日至11月23日
    same_month = re.search(
        r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?\s*(?:到|至|~|—|-)\s*(?:(\d{1,2})\s*月)?\s*(\d{1,2})\s*[日号]?",
        text,
    )
    if same_month:
        month = int(same_month.group(1))
        start = _parse_date_token(today.year, str(month), same_month.group(2))
        end = _parse_date_token(today.year, str(same_month.group(3) or month), same_month.group(4))
        if start and end and end < start:
            end = date(end.year + 1, end.month, end.day)
        return start, end

    # 单个日期
    single = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]", text)
    if single:
        candidate = _parse_date_token(today.year, single.group(1), single.group(2))
        if candidate and candidate < today:
            candidate = _parse_date_token(today.year + 1, single.group(1), single.group(2))
        return candidate, None

    # 2026.11.20 形式的单日期
    dot = re.search(r"(20\d{2})\s*[./]\s*(\d{1,2})\s*[./]\s*(\d{1,2})", text)
    if dot:
        return _parse_date_token(int(dot.group(1)), dot.group(2), dot.group(3)), None
    return None, None


def _extract_budget(text: str) -> tuple[float | None, str | None]:
    per_person = re.search(r"(?:人均|每人|平均每人)\s*(\d+(?:\.\d+)?)\s*(?:元|块)?", text)
    if per_person:
        return float(per_person.group(1)), "per_person"
    total = re.search(r"(?:总预算|总共|一共|预算总计|总费用)\s*(?:为|是|大概|约)?\s*(\d+(?:\.\d+)?)\s*(?:元|块)?", text)
    if total:
        return float(total.group(1)), "total"
    plain = re.search(r"预算\s*(?:为|是|大概|约)?\s*(\d+(?:\.\d+)?)\s*(?:元|块)?", text)
    if plain:
        return float(plain.group(1)), None
    return None, None


def _split_items(text: str) -> list[str]:
    parts = re.split(r"[、，,；;和及与/]", text)
    return [part.strip(" 。.！!？?") for part in parts if part.strip(" 。.！!？?")]


INTEREST_KEYWORDS = {
    "美食": ["美食", "吃", "小吃", "火锅", "菜"],
    "博物馆": ["博物馆", "展馆", "看展"],
    "历史": ["历史", "古迹", "古城", "人文"],
    "自然": ["自然", "风景", "山水", "湿地", "公园"],
    "拍照": ["拍照", "出片", "摄影"],
    "亲子": ["亲子", "孩子", "小朋友", "带娃"],
    "夜景": ["夜景", "晚上", "夜游"],
    "古镇": ["古镇", "古城"],
    "艺术": ["艺术", "文创"],
    "徒步": ["徒步", "爬山", "登山"],
    "慢生活": ["慢生活", "悠闲", "发呆", "喝茶"],
    "购物": ["购物", "逛街", "买"],
    "夜生活": ["酒吧", "夜生活", "livehouse"],
}


def _extract_interests(text: str) -> list[str]:
    found: list[str] = []
    for interest, keywords in INTEREST_KEYWORDS.items():
        if any(keyword in text for keyword in keywords) and interest not in found:
            found.append(interest)
    return found


def _find_city_in_text(text: str) -> list[str]:
    matches: list[tuple[int, str]] = []
    for name in cities_data()["cities"]:
        index = text.find(name)
        if index >= 0:
            matches.append((index, name))
    matches.sort()
    return [name for _, name in matches]


def _parse_text(draft: RequirementInput, text: str, today: date) -> RequirementInput:
    updates: dict[str, object] = {}
    sources: dict[str, str] = {}

    origin_match = re.search(
        r"(?:从|由)\s*([\u4e00-\u9fa5]{2,6})\s*(?:出发|去|到|飞|坐|开车|过去|前往)",
        text,
    )
    if origin_match and city_info(origin_match.group(1)):
        updates["origin"] = normalize_city_name(origin_match.group(1))
        sources["origin"] = "user_text"
    if "origin" not in updates:
        depart_match = re.search(r"([\u4e00-\u9fa5]{2,6})\s*出发", text)
        if depart_match and city_info(depart_match.group(1)):
            updates["origin"] = normalize_city_name(depart_match.group(1))
            sources["origin"] = "user_text"

    destination_match = re.search(
        r"(?:去|到|在)\s*([\u4e00-\u9fa5]{2,6}?)\s*(?:玩|旅游|旅行|度假|逛|出差|待|，|,|。|$)",
        text,
    )
    if destination_match and (city_info(destination_match.group(1)) or province_candidates(destination_match.group(1))):
        updates["destination"] = normalize_city_name(destination_match.group(1))
        sources["destination"] = "user_text"

    if "destination" not in updates:
        mentioned = _find_city_in_text(text)
        if len(mentioned) == 1:
            updates["destination"] = mentioned[0]
            sources["destination"] = "user_text"
        elif len(mentioned) >= 2 and "origin" not in updates and "origin" not in sources:
            updates["origin"] = mentioned[0]
            sources["origin"] = "user_text"
            updates["destination"] = mentioned[-1]
            sources["destination"] = "user_text"

    start, end = _extract_dates(text, today)
    if start:
        updates["start_date"] = start
        sources["start_date"] = "user_text"
    if end:
        updates["end_date"] = end
        sources["end_date"] = "user_text"

    days_match = re.search(r"([一二两三四五六七八九十]+|\d{1,2})\s*(?:天|日游)", text)
    if days_match:
        days = _to_int(days_match.group(1))
        if days:
            updates["days"] = days
            sources["days"] = "user_text"

    travelers_match = re.search(r"([一二两三四五六七八九十]+|\d{1,2})\s*个?\s*人(?!间)", text)
    if travelers_match:
        travelers = _to_int(travelers_match.group(1))
        if travelers:
            updates["travelers"] = travelers
            sources["travelers"] = "user_text"
    family_match = re.search(r"(\d{1,2})\s*大\s*(\d{1,2})\s*小", text)
    if family_match:
        updates["travelers"] = int(family_match.group(1)) + int(family_match.group(2))
        sources["travelers"] = "user_text"

    amount, basis = _extract_budget(text)
    if amount is not None:
        updates["budget"] = BudgetSpec(amount=amount, basis=basis)
        sources["budget"] = "user_text"

    must_match = re.search(r"(?:必去|一定要去|想去|要去)\s*[：:]?\s*(.+?)(?:，|,|。|；|;|$)", text)
    if must_match:
        items = [
            item
            for item in _split_items(must_match.group(1))
            if item != draft.destination and not item.endswith(("玩", "旅行", "旅游"))
        ]
        if items:
            updates["must_visit"] = items
            sources["must_visit"] = "user_text"

    avoid_match = re.search(r"(?:不去|不要|不吃|避开|禁止)\s*(.+?)(?:，|,|。|；|;|$)", text)
    if avoid_match:
        items = [
            item
            for item in _split_items(avoid_match.group(1))
            if item not in {"太赶", "太累", "太满", "太早"}
        ]
        if items:
            updates["avoid"] = items
            sources["avoid"] = "user_text"

    if "轻松" in text or "慢节奏" in text or "不累" in text or "不要太赶" in text or "悠闲" in text:
        updates["pace"] = "轻松"
        sources["pace"] = "user_text"
    elif "紧凑" in text or "特种兵" in text or "多打卡" in text:
        updates["pace"] = "紧凑"
        sources["pace"] = "user_text"

    if "经济" in text or "省钱" in text or "便宜" in text:
        updates["lodging"] = "经济型"
        sources["lodging"] = "user_text"
    elif "豪华" in text or "五星" in text or "高端" in text:
        updates["lodging"] = "豪华型"
        sources["lodging"] = "user_text"
    elif "民宿" in text:
        updates["lodging"] = "民宿"
        sources["lodging"] = "user_text"
    elif "舒适" in text:
        updates["lodging"] = "舒适型"
        sources["lodging"] = "user_text"

    if "飞机" in text or "航班" in text:
        updates["intercity_mode"] = "飞机"
        sources["intercity_mode"] = "user_text"
    elif "高铁" in text or "动车" in text or "火车" in text:
        updates["intercity_mode"] = "高铁"
        sources["intercity_mode"] = "user_text"
    elif "自驾" in text or "开车" in text:
        updates["intercity_mode"] = "自驾"
        sources["intercity_mode"] = "user_text"

    if "包车" in text:
        updates["local_transport"] = "包车"
        sources["local_transport"] = "user_text"
    elif "打车" in text or "出租" in text:
        updates["local_transport"] = "打车"
        sources["local_transport"] = "user_text"
    elif "步行" in text:
        updates["local_transport"] = "步行"
        sources["local_transport"] = "user_text"
    elif "地铁" in text or "公交" in text:
        updates["local_transport"] = "公共交通"
        sources["local_transport"] = "user_text"

    # “不去酒吧”“必去宽窄巷子”等句子不应同时被识别成兴趣标签
    interest_text = text
    for clause in (must_match, avoid_match):
        if clause:
            interest_text = interest_text.replace(clause.group(0), " ")
    interests = _extract_interests(interest_text)
    if interests:
        updates["interests"] = interests
        sources["interests"] = "user_text"

    merged = draft.model_copy(update=updates)
    return merged


def _question_destination(draft: RequirementInput) -> RequirementQuestion | None:
    name = normalize_city_name(draft.destination)
    options_allowed = list(supported_cities())
    if config.AMAP_API_KEY:
        options_allowed = list(cities_data()["cities"].keys())

    if not name:
        return RequirementQuestion(
            id="destination_city",
            field="destination",
            prompt="这次想去哪座城市？第一版一次只规划一个城市。",
            type="select",
            options=[{"value": city, "label": city} for city in options_allowed],
            hint="例如：成都、杭州、西安；普通省份需要先选择具体城市。",
        )

    # 直辖市与普通城市一样按城市处理；只有“省/自治区”才要求选择具体城市
    candidates = province_candidates(name)
    if candidates and city_info(name) is None:
        usable = [city for city in candidates if city in options_allowed]
        if not usable:
            usable = options_allowed
        message = f"“{name}”是省级目的地，第一版按单城市规划，请选择具体城市。"
        return RequirementQuestion(
            id="destination_city",
            field="destination",
            prompt=message,
            type="select",
            options=[{"value": city, "label": city} for city in usable],
            hint="直辖市按城市处理，普通省份需要选择城市。",
        )

    if name not in options_allowed:
        return RequirementQuestion(
            id="destination_city",
            field="destination",
            prompt=f"当前环境暂未覆盖“{name}”。请选择已支持的城市，或配置高德地图 Key 后再试。",
            type="select",
            options=[{"value": city, "label": city} for city in options_allowed],
            hint="演示库覆盖 6 个城市；配置 AMAP_API_KEY 后可尝试实时 POI 检索。",
        )
    return None


def _question_dates(draft: RequirementInput, today: date, conflict: Conflict | None) -> RequirementQuestion | None:
    if conflict:
        return RequirementQuestion(
            id="date_range",
            field="start_date,end_date",
            prompt=f"{conflict.message} 请确认出发与返回日期。",
            type="date_range",
            hint="格式：2026-11-20 至 2026-11-23。",
        )
    if draft.start_date and draft.end_date:
        return None
    if draft.start_date and draft.days:
        return None
    if draft.end_date and draft.days:
        return None
    return RequirementQuestion(
        id="date_range",
        field="start_date,end_date",
        prompt="请提供具体的出发与返回日期（或至少一个出发日期和天数）。",
        type="date_range",
        hint="天气适配需要日期；没有确切日期时不会用演示天气冒充预报。",
    )


def _question_budget(draft: RequirementInput) -> RequirementQuestion | None:
    if draft.budget.skip:
        return None
    if draft.budget.amount is None:
        return RequirementQuestion(
            id="budget_amount",
            field="budget.amount",
            prompt="预算大概是多少？可以按总额或人均给一个口径。",
            type="select",
            options=[
                {"value": "per_person|3000", "label": "人均 3000 元（示例）"},
                {"value": "total|8000", "label": "总预算 8000 元（示例）"},
                {"value": "custom", "label": "自定义预算（填写金额）"},
                {"value": "none", "label": "不设预算，只做估算"},
            ],
            hint="预算口径会影响住宿、餐饮与景点选择，不会把估算当成已确认需求。",
        )
    if draft.budget.basis is None:
        return RequirementQuestion(
            id="budget_basis",
            field="budget.basis",
            prompt=f"你填写的 {draft.budget.amount:.0f} 元是总预算还是人均预算？",
            type="budget_basis",
            options=[
                {"value": "total", "label": "总预算"},
                {"value": "per_person", "label": "人均预算"},
            ],
        )
    return None


def _question_origin(draft: RequirementInput) -> RequirementQuestion | None:
    if not draft.origin:
        return RequirementQuestion(
            id="origin",
            field="origin",
            prompt="从哪座城市出发？用于估算往返大交通费用。",
            type="select",
            options=[
                {"value": city, "label": city}
                for city in list(cities_data()["cities"].keys())[:24]
            ],
            hint="不填写则不把跨城交通计入预算，并会在行程中标注此假设。",
        )
    return None


def analyze_requirement(
    payload: RequirementInput,
    session_id: str,
    answers: dict[str, str] | None = None,
    today: date | None = None,
) -> RequirementAnalysis:
    today = today or date.today()
    draft = payload.model_copy(deep=True)
    field_sources: dict[str, str] = {}

    for field_name in (
        "origin",
        "destination",
        "start_date",
        "end_date",
        "days",
        "travelers",
        "interests",
        "intercity_mode",
        "local_transport",
        "lodging",
        "pace",
        "must_visit",
        "avoid",
    ):
        value = getattr(draft, field_name)
        if value not in (None, "", []):
            field_sources[field_name] = "user_form"
    if draft.budget.amount is not None:
        field_sources["budget"] = "user_form"

    if draft.raw_text:
        parsed = _parse_text(draft, draft.raw_text, today)
        # 表单里已明确填写的值优先；自然语言只补齐表单留空的字段，
        # 避免“用户改了日期，但澄清卡仍显示旧日期”的不一致。
        for field_name in (
            "origin",
            "destination",
            "start_date",
            "end_date",
            "days",
            "travelers",
            "intercity_mode",
            "local_transport",
            "lodging",
            "pace",
        ):
            if getattr(draft, field_name) in (None, "", []) and getattr(parsed, field_name) not in (None, "", []):
                setattr(draft, field_name, getattr(parsed, field_name))
                field_sources[field_name] = "user_text"
        # 兴趣、必去、避开是“可叠加”的清单：表单与自然语言取并集，不互相覆盖
        for field_name in ("interests", "must_visit", "avoid"):
            current = getattr(draft, field_name)
            parsed_value = getattr(parsed, field_name)
            if not parsed_value:
                continue
            merged = list(dict.fromkeys([*current, *parsed_value]))
            if merged != current:
                setattr(draft, field_name, merged)
                field_sources[field_name] = "user_form+user_text" if current else "user_text"
        if draft.budget.amount is None and (parsed.budget.amount is not None or parsed.budget.skip):
            draft.budget = parsed.budget
            field_sources["budget"] = "user_text"

    if answers:
        draft = apply_answers(draft, answers)

    if draft.destination:
        draft.destination = normalize_city_name(draft.destination)
    if draft.origin:
        draft.origin = normalize_city_name(draft.origin)

    assumptions: list[str] = []
    conflicts: list[Conflict] = []

    # 人数、节奏、住宿、市内交通缺省值只作为“假设”，不冒充用户确认。
    if draft.travelers is None:
        draft.travelers = 1
        field_sources["travelers"] = "default"
        assumptions.append("未提供出行人数，按 1 人估算。")
    if not draft.pace:
        draft.pace = "适中"
        field_sources["pace"] = "default"
        assumptions.append("未指定行程节奏，按“适中”排期。")
    if not draft.lodging:
        field_sources["lodging"] = "default"
        assumptions.append("未指定住宿档次，按“舒适型”估算，未视为已确认偏好。")
    if not draft.local_transport:
        draft.local_transport = "公共交通"
        field_sources["local_transport"] = "default"
        assumptions.append("未指定市内交通，按公共交通估算。")
    if not draft.interests:
        field_sources["interests"] = "default"
        assumptions.append("未提供兴趣标签，仅按地点距离与开放时间编排，不推测个人偏好。")

    # 日期与天数口径
    if draft.start_date and draft.end_date and draft.end_date < draft.start_date:
        conflicts.append(
            Conflict(
                code="date_order",
                message="返回日期早于出发日期。",
                fields=["start_date", "end_date"],
            )
        )
    elif draft.start_date and draft.start_date < today:
        conflicts.append(
            Conflict(
                code="date_in_past",
                message="出发日期早于今天。",
                fields=["start_date"],
            )
        )
    elif draft.start_date and draft.end_date and draft.days:
        derived_days = (draft.end_date - draft.start_date).days + 1
        if derived_days != draft.days:
            conflicts.append(
                Conflict(
                    code="days_mismatch",
                    message=f"填写的 {draft.days} 天与日期区间（{derived_days} 天）不一致。",
                    fields=["days", "start_date", "end_date"],
                )
            )

    # 存在日期冲突时保留用户填写的原始值，不要先自动推导再让澄清卡显示另一组日期
    if not conflicts:
        if draft.start_date and draft.end_date:
            draft.days = (draft.end_date - draft.start_date).days + 1
            field_sources["days"] = "derived"
        elif draft.start_date and draft.days:
            draft.end_date = draft.start_date + timedelta(days=draft.days - 1)
            field_sources["end_date"] = "derived"
        elif draft.end_date and draft.days:
            draft.start_date = draft.end_date - timedelta(days=draft.days - 1)
            field_sources["start_date"] = "derived"

    budget_questions = _question_budget(draft)
    if budget_questions and budget_questions.id == "budget_amount":
        field_sources.pop("budget", None)
    if draft.budget.basis is None and draft.budget.amount is not None:
        field_sources["budget"] = "user_unconfirmed"

    questions: list[RequirementQuestion] = []
    destination_question = _question_destination(draft)
    if destination_question:
        questions.append(destination_question)
        field_sources.pop("destination", None)

    date_conflict = next((item for item in conflicts if item.code in {"date_order", "date_in_past", "days_mismatch"}), None)
    date_question = _question_dates(draft, today, date_conflict)
    if date_question:
        questions.append(date_question)
        if date_conflict and date_conflict.code == "days_mismatch":
            field_sources["days"] = "user_unconfirmed"

    if draft.budget.amount is None or draft.budget.basis is None:
        if budget_questions:
            questions.append(budget_questions)

    origin_question = _question_origin(draft)
    if origin_question:
        questions.append(origin_question)

    questions = questions[:3]
    final_question_ids = {question.id for question in questions}
    if not draft.origin and "origin" not in final_question_ids:
        assumptions.append("未提供出发地，未计入跨城往返交通费用。")
    if draft.budget.amount is None:
        if draft.budget.skip:
            assumptions.append("你选择了不设预算，结果只给出估算总额与人均值，不判定是否超预算。")
        else:
            assumptions.append("未提供预算上限，预算部分仅做估算。")
        field_sources["budget"] = "default"
    elif draft.budget.basis is None and not ({"budget_basis", "budget_amount"} & final_question_ids):
        assumptions.append("预算口径未确认，结果只给出估算总额与人均值，不判定是否超预算。")

    missing_fields = [question.field for question in questions]
    status = "need_clarification" if questions else "ready"

    if draft.destination and draft.start_date and draft.end_date:
        summary = (
            f"{draft.start_date.isoformat()} 至 {draft.end_date.isoformat()}，"
            f"{draft.destination} {draft.days} 天，{draft.travelers} 人"
        )
        if draft.budget.amount:
            basis_label = "人均" if draft.budget.basis == "per_person" else "总预算"
            summary += f"，{basis_label} {draft.budget.amount:.0f} 元"
        if draft.pace:
            summary += f"，节奏{draft.pace}"
    else:
        summary = "需求信息还不完整，请先回答关键问题。"

    return RequirementAnalysis(
        session_id=session_id,
        status=status,
        questions=questions,
        draft=draft,
        conflicts=conflicts,
        assumptions=assumptions,
        field_sources=field_sources,
        missing_fields=missing_fields,
        summary=summary,
    )


def apply_answers(draft: RequirementInput, answers: dict[str, str]) -> RequirementInput:
    updates: dict[str, object] = {}
    for question_id, raw_value in answers.items():
        value = str(raw_value).strip()
        if not value:
            continue
        if question_id == "destination_city":
            updates["destination"] = normalize_city_name(value)
        elif question_id == "origin":
            updates["origin"] = normalize_city_name(value)
        elif question_id == "date_range":
            parts = re.split(r"[|~至到—]", value)
            if len(parts) >= 2:
                try:
                    updates["start_date"] = date.fromisoformat(parts[0].strip())
                    updates["end_date"] = date.fromisoformat(parts[1].strip())
                    updates["days"] = (
                        updates["end_date"] - updates["start_date"]  # type: ignore[operator]
                    ).days + 1
                except ValueError:
                    pass
            elif len(parts) == 1:
                try:
                    updates["start_date"] = date.fromisoformat(parts[0].strip())
                except ValueError:
                    pass
        elif question_id == "budget_basis":
            updates["budget"] = BudgetSpec(
                amount=draft.budget.amount,
                basis="per_person" if value == "per_person" else "total",
            )
        elif question_id == "budget_amount":
            if value == "none":
                updates["budget"] = BudgetSpec(amount=None, basis=None, skip=True)
            elif "|" in value:
                basis, amount = value.split("|", 1)
                try:
                    updates["budget"] = BudgetSpec(amount=float(amount), basis=basis)  # type: ignore[arg-type]
                except ValueError:
                    pass
            else:
                try:
                    updates["budget"] = BudgetSpec(amount=float(value), basis=draft.budget.basis)
                except ValueError:
                    pass
        elif question_id == "travelers":
            try:
                updates["travelers"] = max(1, int(float(value)))
            except ValueError:
                pass
    return draft.model_copy(update=updates)
