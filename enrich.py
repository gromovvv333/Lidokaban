import re
import urllib.request

from phone import is_mobile

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/131.0 Safari/537.36"
_TG_RE = re.compile(r"(?:t\.me|telegram\.me)/([A-Za-z][A-Za-z0-9_]{4,31})(?![A-Za-z0-9_])")
_TG_SKIP = {"share", "joinchat", "addstickers", "iv", "proxy", "socks", "login"}
_WA_RE = re.compile(r"(?:wa\.me/|api\.whatsapp\.com/send/?\?(?:[^\"'\s]*&)?phone=)\+?(\d{7,15})")


def _fetch(url: str, timeout: int = 8) -> str | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read(500_000).decode("utf-8", errors="ignore")
    except Exception:
        return None


_CYR_RE = re.compile(r"[а-яёА-ЯЁ]")
_RU_HINT_RE = re.compile(r'lang=["\']ru|hreflang=["\']ru|href=["\'][^"\']*/ru/?["\']')


def has_cyrillic(text: str) -> bool:
    return bool(_CYR_RE.search(text or ""))


def _site_is_russian(html: str) -> bool:
    return len(_CYR_RE.findall(html)) >= 50 or bool(_RU_HINT_RE.search(html))


def find_messengers(website_url: str) -> dict:
    """Ищет на сайте бизнеса ссылки на Telegram и WhatsApp и смотрит, есть ли
    на сайте русский язык (site_ru: True/False, None — сайт не открылся)."""
    html = _fetch(website_url) if website_url else None
    if not html:
        return {"telegram": "", "whatsapp": "", "site_ru": None}

    telegram = next(
        (m for m in _TG_RE.findall(html) if m.lower() not in _TG_SKIP), ""
    )
    wa = _WA_RE.search(html)
    return {
        "telegram": f"@{telegram}" if telegram else "",
        "whatsapp": f"+{wa.group(1)}" if wa else "",
        "site_ru": _site_is_russian(html),
    }


def russian_speaking(lead: dict, site_ru: bool | None) -> str:
    """Эвристика: кириллица в названии (адрес не берём: Google сам переводит его
    при русском интерфейсе) или русский язык на сайте → 'да'.
    Если признаков нет, но сайт проверили → 'нет'; проверить нечем → ''."""
    if has_cyrillic(lead.get("name")) or site_ru:
        return "да"
    return "" if site_ru is None else "нет"


def is_telegram_bot(username: str) -> str:
    """'да' если @username — бот (на t.me у бота кнопка Start Bot), 'нет' если
    обычный аккаунт/канал, '' если проверить не удалось."""
    if not username:
        return ""
    html = _fetch(f"https://t.me/{username.lstrip('@')}")
    if not html or "tgme_page_title" not in html:
        return ""
    return "да" if "Start Bot" in html else "нет"


def zalo_link(phone: str, country_code: str) -> str:
    """Во Вьетнаме местные сидят в Zalo, он привязан к номеру телефона."""
    if country_code != "VN" or not phone:
        return ""
    return f"https://zalo.me/{phone.lstrip('+')}"


def messenger_links(phone: str) -> dict:
    """Ссылки на чат по номеру. Проверить, есть ли аккаунт, нельзя — но по
    мобильному номеру он скорее всего есть, городской в мессенджерах не бывает."""
    if not phone or not is_mobile(phone):
        return {"is_mobile": "нет", "wa_link": "", "tg_link": ""}
    digits = phone.lstrip("+")
    return {
        "is_mobile": "да",
        "wa_link": f"https://wa.me/{digits}",
        "tg_link": f"https://t.me/+{digits}",
    }


def build_row(lead: dict, phone: str, run_id: str, country_code: str) -> dict:
    contacts = find_messengers(lead.get("website_url") or "")
    return {
        "run_id": run_id,
        "name": lead.get("name") or "",
        "phone": phone,
        "address": lead.get("address") or "",
        "has_website": lead.get("has_website") or "",
        "website": lead.get("website_url") or "",
        "rating": lead.get("rating") or "",
        "reviews_count": lead.get("reviews_count") or "",
        "telegram": contacts["telegram"],
        "has_bot": is_telegram_bot(contacts["telegram"]),
        "whatsapp": contacts["whatsapp"],
        "zalo": zalo_link(phone, country_code),
        "russian": russian_speaking(lead, contacts["site_ru"]),
        **messenger_links(phone),
    }
