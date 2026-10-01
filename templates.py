"""Шаблоны первичного касания в Telegram.

Тексты лежат в `TEMPLATES_JSON_PATH` (внутри DATA_DIR), а не в коде: правки
пользователя не должны затираться пересборкой `.app`. При первом запуске файл
создаётся из DEFAULT_TEMPLATES, новые шаблоны из обновлений доливаются, уже
отредактированные не трогаются.
"""

import json
import os
import re

from config import TEMPLATES_JSON_PATH

# Продукт: телеграм-бот записи для груминг-салонов + мини-апп. Письмо идёт вместе с двумя
# демо-видео (бот и мини-апп) с данными самого салона, поэтому в тексте на них ссылка.
_BODY = """Я делаю телеграм-ботов записи для груминг-салонов и, пока смотрел ваш, собрал для салона {name} небольшое демо: два коротких видео с вашим названием, вашими услугами и ценами. Одно про бота в чате, другое про мини-приложение записи прямо в Telegram. Посмотрите, как это могло бы выглядеть у вас.

Зачем это вообще. У салонов обычно теряется часть клиентов ещё до записи: человек вспомнил про стрижку вечером или в выходной, позвонил, никто не взял, и он записался в другое место. А бот принимает записи сам, в любое время, вместе с услугой, датой и телефоном, и вам остаётся только увидеть заявку.

Плюс он сам напоминает клиентам о визите, так что «забыли и не пришли» бывает реже. А через пару месяцев мягко зовёт хозяина снова, без вашего обзвона. Меньше рутины у администратора, меньше переписок «а на когда свободно».

Можно взять только бота, мини-приложение по желанию. Если интересно, покажу, как оно живёт вживую, и скажу, сколько это будет стоить."""

DEFAULT_TEMPLATES = {
    "first_no_site": {
        "title": "Первое касание — нет сайта",
        "text": (
            "Здравствуйте! Нашёл вас на картах: {name}, {city}. "
            "Сайта у вас нет, значит, записи идут через звонки и сообщения. Как раз такой случай.\n\n"
            f"{_BODY}"
        ),
    },
    "first_many_reviews": {
        "title": "Первое касание — много отзывов",
        "text": (
            "Здравствуйте! Нашёл вас на картах: {name}, {city}. "
            "Отзывов у вас {reviews_count}, клиенты явно идут. Тем обиднее терять тех, до кого не дозвонились.\n\n"
            f"{_BODY}"
        ),
    },
    "first_default": {
        "title": "Первое касание — базовый",
        "text": (
            "Здравствуйте! Нашёл вас на картах: {name}, {city}.\n\n"
            f"{_BODY}"
        ),
    },
    "followup": {
        "title": "Второе касание (напоминание)",
        "text": (
            "Здравствуйте! Пишу ещё раз про демо-видео для {name}: возможно, сообщение затерялось. "
            "Если тема не актуальна, просто скажите, больше не потревожу."
        ),
    },
}

_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


def load_templates() -> dict:
    """Читает шаблоны, доливая недостающие из дефолтных."""
    stored = {}
    if os.path.exists(TEMPLATES_JSON_PATH):
        try:
            with open(TEMPLATES_JSON_PATH, encoding="utf-8") as f:
                stored = json.load(f)
        except (json.JSONDecodeError, OSError):
            stored = {}

    merged = {key: dict(value) for key, value in DEFAULT_TEMPLATES.items()}
    for key, value in stored.items():
        if isinstance(value, dict) and value.get("text"):
            merged.setdefault(key, {"title": key})
            merged[key] = {**merged[key], **value}

    if merged != stored:
        save_templates(merged)
    return merged


def save_templates(templates: dict) -> None:
    with open(TEMPLATES_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(templates, f, ensure_ascii=False, indent=2)


def save_template(template_id: str, text: str) -> dict:
    templates = load_templates()
    if template_id not in templates:
        return templates
    templates[template_id] = {**templates[template_id], "text": text}
    save_templates(templates)
    return templates


def _reviews_count(lead: dict) -> int:
    digits = re.sub(r"\D", "", str(lead.get("reviews_count") or ""))
    return int(digits) if digits else 0


def _has_site(lead: dict) -> bool:
    website = (lead.get("website") or "").strip()
    return website.startswith("http") or (lead.get("has_website") or "").strip().lower() == "да"


def pick_template_id(lead: dict, touch: int = 1) -> str:
    """Выбор шаблона по уже собранным колонкам лида.

    Это не скоринг и не оценка «нужна ли бизнесу автоматизация» (такое в PRD
    запрещено), а выбор формулировки зацепки по фактам из CSV.
    """
    if touch >= 2:
        return "followup"
    if not _has_site(lead):
        return "first_no_site"
    if _reviews_count(lead) >= 100:
        return "first_many_reviews"
    return "first_default"


def render(lead: dict, city: str = "", category: str = "", template_id: str | None = None, touch: int = 1,
           templates: dict | None = None) -> tuple[str, str]:
    """Возвращает (template_id, готовый текст) с подставленными полями лида."""
    templates = templates if templates is not None else load_templates()
    template_id = template_id or pick_template_id(lead, touch)
    text = (templates.get(template_id) or {}).get("text", "")

    values = {
        "name": (lead.get("name") or "").strip(),
        "city": city.strip(),
        "category": category.strip(),
        "rating": (lead.get("rating") or "").strip(),
        # В CSV лежит гугловский формат «2,196» — в русском тексте это читается как дробь
        "reviews_count": re.sub(r"\D", "", str(lead.get("reviews_count") or "")),
    }
    rendered = _PLACEHOLDER_RE.sub(lambda m: values.get(m.group(1), m.group(0)), text)
    # Город может быть неизвестен для старых запусков — убираем повисшие хвосты
    rendered = rendered.replace(" — , ", " — ").replace(", .", ".")
    return template_id, rendered.strip()
