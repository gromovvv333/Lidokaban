"""Очередь первичных касаний в Telegram (полуавтомат).

Автоотправки здесь нет и быть не должно: приложение готовит текст и открывает
чат, отправляет человек руками. Массовая рассылка через userbot-библиотеки
(Telethon/Pyrogram) ведёт к бану номера и потере переписки с теми, кто уже
ответил — см. docs/OUTREACH.md.

Статусы лидов лежат в отдельном `outreach.json`, а не в `leads.csv`: CSV
дописывается построчно, переписывать его на каждый клик плохо.
"""

import datetime
import json
import os

from config import FOLLOWUP_DAYS, OUTREACH_JSON_PATH

NEW = "новый"
SENT = "написал"
REPLIED = "ответил"
DECLINED = "отказ"
NO_TG = "нет_тг"
SKIPPED = "пропущен"

# Из этих статусов лид в очередь больше не возвращается
TERMINAL = {REPLIED, DECLINED, NO_TG, SKIPPED}

STATUS_LABELS = {
    NEW: "не писали",
    SENT: "написал",
    REPLIED: "ответил",
    DECLINED: "отказ",
    NO_TG: "нет Telegram",
    SKIPPED: "пропущен",
}


def lead_key(lead: dict) -> str:
    """Ключ лида: run_id + телефон, а если телефона нет — run_id + название."""
    run_id = (lead.get("run_id") or "").strip()
    ident = (lead.get("phone") or "").strip() or (lead.get("name") or "").strip()
    return f"{run_id}|{ident}"


def telegram_channel(lead: dict) -> dict | None:
    """Как писать этому лиду в Telegram.

    @username с сайта надёжнее: номер может быть не зарегистрирован в Telegram,
    тогда чат просто не откроется и лид уйдёт в статус «нет_тг».
    """
    username = (lead.get("telegram") or "").strip().lstrip("@")
    if username:
        # Бот отвечать на КП не будет, писать в него бессмысленно
        if (lead.get("has_bot") or "").strip().lower() != "да":
            return {
                "channel": "username",
                "contact": f"@{username}",
                "app_url": f"tg://resolve?domain={username}",
                "web_url": f"https://t.me/{username}",
            }

    phone = (lead.get("phone") or "").strip()
    if phone and (lead.get("is_mobile") or "").strip().lower() == "да":
        digits = phone.lstrip("+")
        return {
            "channel": "phone",
            "contact": phone,
            "app_url": f"tg://resolve?phone={digits}",
            "web_url": f"https://t.me/+{digits}",
        }
    return None


def load_state(path: str = OUTREACH_JSON_PATH) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(state: dict, path: str = OUTREACH_JSON_PATH) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _parse_dt(value: str | None) -> datetime.datetime | None:
    try:
        return datetime.datetime.fromisoformat(value) if value else None
    except ValueError:
        return None


def _days_since(value: str | None, now: datetime.datetime) -> float | None:
    parsed = _parse_dt(value)
    return None if parsed is None else (now - parsed).total_seconds() / 86400


def due_for_followup(record: dict, now: datetime.datetime, followup_days: int = FOLLOWUP_DAYS) -> bool:
    """Написали один раз, ответа нет, прошло достаточно дней."""
    if record.get("status") != SENT or int(record.get("touch") or 1) >= 2:
        return False
    days = _days_since(record.get("sent_at"), now)
    return days is not None and days >= followup_days


def build_queue(leads: list[dict], state: dict | None = None, now: datetime.datetime | None = None,
                followup_days: int = FOLLOWUP_DAYS) -> list[dict]:
    """Кого показывать в конвейере: сначала просроченные напоминания, потом новые."""
    state = load_state() if state is None else state
    now = now or datetime.datetime.now()

    followups, fresh = [], []
    for lead in leads:
        channel = telegram_channel(lead)
        if channel is None:
            continue

        key = lead_key(lead)
        record = state.get(key) or {}
        status = record.get("status") or NEW

        if status in TERMINAL:
            continue

        if status == SENT:
            if not due_for_followup(record, now, followup_days):
                continue
            item = {"key": key, "lead": lead, "touch": 2, "record": record, **channel}
            followups.append(item)
        else:
            fresh.append({"key": key, "lead": lead, "touch": 1, "record": record, **channel})

    return followups + fresh


def mark(key: str, status: str, template_id: str = "", touch: int = 1,
         name: str = "", contact: str = "", path: str = OUTREACH_JSON_PATH) -> dict:
    """Ставит статус лиду. Терминальные статусы означают «больше не показывать»."""
    if status not in STATUS_LABELS:
        raise ValueError(f"неизвестный статус: {status}")

    state = load_state(path)
    record = dict(state.get(key) or {})
    now_iso = datetime.datetime.now().isoformat(timespec="seconds")

    record["status"] = status
    record["name"] = name or record.get("name", "")
    record["contact"] = contact or record.get("contact", "")
    record["updated_at"] = now_iso

    if status == SENT:
        record["touch"] = touch
        record["sent_at"] = now_iso
        if template_id:
            record["template_id"] = template_id
    elif status == REPLIED:
        record["replied_at"] = now_iso

    state[key] = record
    save_state(state, path)
    return record


def stats(leads: list[dict], state: dict | None = None) -> dict:
    """Сводка по лидам, у которых вообще есть Telegram-канал."""
    state = load_state() if state is None else state
    counts = {status: 0 for status in STATUS_LABELS}
    reachable = 0
    for lead in leads:
        if telegram_channel(lead) is None:
            continue
        reachable += 1
        status = (state.get(lead_key(lead)) or {}).get("status") or NEW
        counts[status] = counts.get(status, 0) + 1
    return {"reachable": reachable, "counts": counts}


def _urls_from_contact(contact: str) -> dict:
    contact = (contact or "").strip()
    if contact.startswith("@"):
        username = contact.lstrip("@")
        return {
            "app_url": f"tg://resolve?domain={username}",
            "web_url": f"https://t.me/{username}",
        }
    digits = contact.lstrip("+").replace(" ", "")
    if not digits.isdigit():
        return {"app_url": "", "web_url": ""}
    return {
        "app_url": f"tg://resolve?phone={digits}",
        "web_url": f"https://t.me/+{digits}",
    }


def awaiting(state: dict | None = None, now: datetime.datetime | None = None,
             followup_days: int = FOLLOWUP_DAYS) -> list[dict]:
    """Кому написали и ждём ответа. Отсюда пользователь руками ставит «ответил»."""
    state = load_state() if state is None else state
    now = now or datetime.datetime.now()

    rows = []
    for key, record in state.items():
        if record.get("status") != SENT:
            continue
        days = _days_since(record.get("sent_at"), now)
        rows.append({
            "key": key,
            "name": record.get("name") or "",
            "contact": record.get("contact") or "",
            "sent_at": record.get("sent_at") or "",
            "touch": int(record.get("touch") or 1),
            "days": None if days is None else round(days, 1),
            "due": due_for_followup(record, now, followup_days),
            **_urls_from_contact(record.get("contact") or ""),
        })

    rows.sort(key=lambda r: r.get("sent_at") or "", reverse=True)
    return rows
