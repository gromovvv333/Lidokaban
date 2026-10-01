"""Скрапер 2ГИС (веб-версия): список фирм по ссылкам, контакты из карточки.

Как устроено (после первого живого запуска, см. debug_2gis/):
- выдача `https://2gis.<tld>/<город>/search/<запрос>` показывает список фирм;
  телефонов в списке НЕТ, они только в карточке;
- в списке берём ссылки `/firm/<id>` (устойчивый формат URL, в отличие от
  минифицированных классов) и по тексту блока рядом — название, адрес, рейтинг;
- карточку `/firm/<id>` открываем отдельной загрузкой, нажимаем «Показать
  телефон» и читаем ссылки `tel:`; сайт/Telegram/WhatsApp — из ссылок карточки;
- дополнительно разбираем JSON-ответы страницы и ld+json, если они есть;
- пагинация по URL `.../page/N`.

Нужен российский IP (VPN), иначе 2ГИС редиректит на captcha.2gis.ru. Браузер
запускается с окном: если появилась капча, её можно решить руками, скрапер ждёт.

Диагностика: каждый запуск кладёт в `~/Documents/ЛидоКабан/debug_2gis/last_run/`
HTML списка и первой карточки, найденные ссылки и сетевой журнал (без тайлов
карты). Если поля пустые, смотри туда.
"""

import datetime
import json
import os
import random
import re
import shutil
import time
from urllib.parse import quote, unquote, urlparse

from playwright.sync_api import Page, sync_playwright

from config import DATA_DIR, MAX_DELAY_SECONDS, MAX_LEADS, MIN_DELAY_SECONDS
from enrich import _IG_RE, _IG_SKIP, _TG_RE, _WA_RE

# Домен 2ГИС по коду страны. Неизвестная страна → российский.
DOMAINS = {"RU": "ru", "KZ": "kz", "UZ": "uz", "KG": "kg", "AE": "ae"}

# Слаги городов в URL 2ГИС (2gis.kz/almaty/...). Часть слагов написана по памяти:
# если город не нашёлся, в поле «Город» можно вписать слаг прямо из адресной
# строки 2ГИС латиницей (например, almaty) — он используется как есть.
CITY_SLUGS = {
    # Казахстан
    "алматы": "almaty", "алма-ата": "almaty", "астана": "astana", "нур-султан": "astana",
    "шымкент": "shymkent", "караганда": "karaganda", "актау": "aktau", "атырау": "atyrau",
    "костанай": "kostanay", "павлодар": "pavlodar", "семей": "semey", "актобе": "aktobe",
    "тараз": "taraz", "кызылорда": "kyzylorda", "петропавловск": "petropavlovsk",
    "уральск": "uralsk", "туркестан": "turkestan", "талдыкорган": "taldykorgan",
    "кокшетау": "kokshetau", "усть-каменогорск": "ust-kamenogorsk", "темиртау": "temirtau",
    # Россия
    "москва": "moscow", "санкт-петербург": "spb", "питер": "spb", "новосибирск": "novosibirsk",
    "екатеринбург": "ekaterinburg", "казань": "kazan", "нижний новгород": "n_novgorod",
    "челябинск": "chelyabinsk", "самара": "samara", "омск": "omsk", "ростов-на-дону": "rostov-on-don",
    "уфа": "ufa", "красноярск": "krasnoyarsk", "воронеж": "voronezh", "пермь": "perm",
    "волгоград": "volgograd", "краснодар": "krasnodar", "саратов": "saratov",
    "тюмень": "tumen", "тольятти": "tolyatti", "ижевск": "izhevsk", "барнаул": "barnaul",
    "иркутск": "irkutsk", "ярославль": "yaroslavl", "владивосток": "vladivostok",
    "хабаровск": "habarovsk", "сочи": "sochi", "кемерово": "kemerovo", "томск": "tomsk",
    "оренбург": "orenburg", "рязань": "ryazan", "калининград": "kaliningrad",
    "сергиев посад": "sergiev-posad", "ивантеевка": "ivanteevka",
}

_TRANSLIT = dict(zip(
    "абвгдежзийклмнопрстуфхцчшщъыьэюя",
    ["a", "b", "v", "g", "d", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s",
     "t", "u", "f", "h", "c", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"],
))

_AT_RE = re.compile(r"@([A-Za-z][A-Za-z0-9_]{4,31})")

MAX_PAGES = 50

# Итоги последнего запуска: app.py показывает их пользователю, чтобы было видно,
# почему лидов столько (сколько 2ГИС показал, сколько отсеял фильтр).
LAST_STATS: dict = {}
_CLAIMED_RE = re.compile(r"Места\s*(\d+)")
DEBUG_DIR = os.path.join(DATA_DIR, "debug_2gis")


def _human_delay() -> None:
    time.sleep(random.uniform(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS))


def _translit(text: str, sep: str) -> str:
    out = []
    for ch in text:
        if ch in " -_":
            out.append(sep)
        else:
            out.append(_TRANSLIT.get(ch, ch))
    return re.sub(rf"{re.escape(sep)}+", sep, "".join(out)).strip(sep) if sep else "".join(out)


def _city_slug_candidates(city: str) -> list[str]:
    """Варианты слага города. Слаг 2ГИС для многословных городов обычно с
    подчёркиванием (sergiev_posad, n_novgorod), но наверняка не знаем: неверный
    слаг 2ГИС молча заменяет другим городом, поэтому run() проверяет результат."""
    c = (city or "").strip().lower().replace("ё", "е")
    if not c:
        return ["moscow"]
    if re.fullmatch(r"[a-z0-9_\-]+", c):
        return [c]  # уже слаг
    if c in CITY_SLUGS:
        return [CITY_SLUGS[c]]
    candidates = [_translit(c, "_"), _translit(c, ""), _translit(c, "-")]
    return list(dict.fromkeys(x for x in candidates if x))


_CATALOG_RE = re.compile(r'"name":"([^"]+)","country_code":"(\w+)","alias":"([\w\-]+)"')


def _norm_city(name: str) -> str:
    return re.sub(r"[\s\-]+", " ", (name or "").lower().replace("ё", "е")).strip()


def _catalog_alias(html: str, city: str, tld: str) -> tuple[str, bool] | None:
    """В каждой странице 2ГИС зашит каталог всех городов (название, страна, адресное
    слово, ~1200 штук). Слаг из него точный, в отличие от угадывания.

    Возвращает (alias, exact) — exact=False, если совпало только название, а
    страна другая (бывают города-омонимы в разных странах 2ГИС): такой слаг
    не гарантирован и нуждается в дополнительной проверке, как угаданный."""
    wanted = _norm_city(city)
    if not wanted or not html:
        return None
    matches = [(country, alias) for name, country, alias in _CATALOG_RE.findall(html)
               if _norm_city(name) == wanted]
    for country, alias in matches:
        if country == tld:
            return alias, True  # город из нужной страны
    return (matches[0][1], False) if matches else None


def _split_query(query: str) -> tuple[str, str]:
    """app.py/main.py собирают запрос как «категория, город»."""
    category, _, city = query.rpartition(",")
    if not category:
        return query.strip(), ""
    return category.strip(), city.strip()


# --- разбор JSON ------------------------------------------------------------


def _looks_like_firm(d: dict) -> bool:
    if not d.get("id") or not d.get("name"):
        return False
    kind = d.get("type")
    if kind is not None:
        return kind == "branch"  # здания, улицы, районы в выдаче нас не интересуют
    return "contact_groups" in d or "address_name" in d


def _find_firms(node, out: list) -> None:
    if isinstance(node, dict):
        if _looks_like_firm(node):
            out.append(node)
            return
        for value in node.values():
            _find_firms(value, out)
    elif isinstance(node, list):
        for value in node:
            _find_firms(value, out)


def _contacts(item: dict) -> list[dict]:
    result = []
    for group in item.get("contact_groups") or []:
        if isinstance(group, dict):
            result.extend(c for c in (group.get("contacts") or []) if isinstance(c, dict))
    return result


def _phone_of(contact: dict) -> str:
    for key in ("value", "print_text", "text"):
        value = contact.get(key)
        if isinstance(value, str) and len(re.sub(r"\D", "", value)) >= 6:
            return value.replace("tel:", "").strip()
    url = contact.get("url")
    if isinstance(url, str) and url.startswith("tel:"):
        return url[4:].strip()
    return ""


def _website_of(contact: dict) -> str:
    value = (contact.get("value") or "").strip() if isinstance(contact.get("value"), str) else ""
    if value.startswith("http"):
        return value
    text = (contact.get("text") or contact.get("print_text") or "").strip()
    if text and "." in text and " " not in text:
        return text if text.startswith("http") else f"http://{text}"
    return value if "." in value and " " not in value else ""


def _messenger_of(contact: dict, kind: str) -> str:
    blob = unquote(" ".join(
        str(contact.get(k) or "") for k in ("value", "text", "print_text", "url")
    ))
    if kind == "telegram":
        m = _TG_RE.search(blob) or _AT_RE.search(blob)
        return f"@{m.group(1)}" if m else ""
    m = _WA_RE.search(blob)
    if m:
        return f"+{m.group(1)}"
    digits = re.sub(r"\D", "", str(contact.get("value") or contact.get("text") or ""))
    return f"+{digits}" if len(digits) >= 7 else ""


def _to_lead(item: dict) -> dict:
    phones, website, telegram, whatsapp = [], "", "", ""
    for c in _contacts(item):
        kind = str(c.get("type") or "").lower()
        if kind == "phone":
            phone = _phone_of(c)
            if phone and phone not in phones:
                phones.append(phone)
        elif kind == "website" and not website:
            website = _website_of(c)
        elif kind == "telegram" and not telegram:
            telegram = _messenger_of(c, "telegram")
        elif kind == "whatsapp" and not whatsapp:
            whatsapp = _messenger_of(c, "whatsapp")

    reviews = item.get("reviews") if isinstance(item.get("reviews"), dict) else {}
    rating = reviews.get("general_rating") or reviews.get("org_rating") or item.get("rating")
    count = reviews.get("general_review_count") or reviews.get("org_review_count") or ""

    return {
        "id": str(item.get("id")),
        "name": str(item.get("name") or "").strip(),
        "phones": phones,
        "address": str(item.get("address_name") or item.get("full_address_name") or "").strip(),
        "has_website": "да" if website else "нет",
        "website_url": website,
        "rating": "" if rating in (None, "") else str(rating).replace(",", "."),
        "reviews_count": re.sub(r"\D", "", str(count)),
        "telegram": telegram,
        "whatsapp": whatsapp,
    }


# --- работа со страницей ------------------------------------------------------

_NOISE = ("tile", "/vt?", "/ald", "metafiles", "disk.2gis", "mapgl.2gis", "clickstream",
          "bss-ext", "/_/log", "fonts")
_SOCIAL = ("vk.com", "instagram.com", "facebook.com", "ok.ru", "youtube.com", "t.me",
           "wa.me", "whatsapp.com", "telegram.me", "twitter.com", "tiktok.com")
_DOMAIN_RE = re.compile(r"^(?:https?://)?[\w\-]+(?:\.[\w\-]+)+(?:/\S*)?$")
_FIRM_ID_RE = re.compile(r"/firm/(\d+)")
_REVEAL_RE = re.compile(r"Показать\s+(?:телефон|номер)", re.I)
_RATING_RE = re.compile(r"(\d(?:[.,]\d)?)\s*\n\s*(\d+)\s+оцен")

LIST_JS = """() => {
  const idOf = (a) => { const m = (a.href || '').match(/\\/firm\\/(\\d+)/); return m ? m[1] : null; };
  const anchors = [...document.querySelectorAll('a[href*="/firm/"]')];
  const byId = new Map();
  for (const a of anchors) {
    const id = idOf(a);
    if (!id || byId.has(id)) continue;
    let block = a, el = a, depth = 0;
    while (el.parentElement && depth < 6) {
      const p = el.parentElement;
      const ids = new Set([...p.querySelectorAll('a[href*="/firm/"]')].map(idOf));
      if (ids.size > 1) break;
      block = p; el = p; depth++;
    }
    byId.set(id, {id, href: a.href, name: (a.innerText || '').trim(), text: block.innerText || ''});
  }
  return [...byId.values()];
}"""

SCROLL_JS = """() => {
  const a = document.querySelector('a[href*="/firm/"]');
  if (!a) return 0;
  let el = a.parentElement;
  while (el && el !== document.body) {
    const s = getComputedStyle(el);
    if (/(auto|scroll)/.test(s.overflowY) && el.scrollHeight > el.clientHeight + 20) {
      el.scrollTop = el.scrollHeight; return 1;
    }
    el = el.parentElement;
  }
  window.scrollTo(0, document.body.scrollHeight);
  return 2;
}"""

ANCHORS_JS = """() => [...document.querySelectorAll('a')].map(
  a => ({href: a.href || '', text: (a.innerText || '').trim()}))"""


def _digits_key(phone: str) -> str:
    return re.sub(r"\D", "", phone)[-10:]


class _Session:
    def __init__(self, context, page: Page):
        self.page = page
        self.pending: list = []  # JSON-ответы, ещё не разобранные
        self.net_log: list[str] = []
        self.samples: list[str] = []
        self.debug_saved = 0
        context.on("response", self._on_response)

    def _on_response(self, response) -> None:
        try:
            url = response.url
            if "2gis" not in url or any(k in url for k in _NOISE):
                return
            ctype = response.headers.get("content-type", "")
            self.net_log.append(f"{response.status} {response.request.resource_type} {ctype[:28]} {url[:260]}")
            if "json" in ctype:
                self.pending.append(response)
        except Exception:
            pass

    def drain_firms(self) -> list[dict]:
        firms: list[dict] = []
        pending, self.pending = self.pending, []
        for response in pending:
            try:
                body = response.json()
            except Exception:
                continue
            if len(self.samples) < 8:
                self.samples.append(json.dumps(body, ensure_ascii=False)[:150_000])
            _find_firms(body, firms)
        return firms

    def goto(self, url: str) -> None:
        self.pending.clear()
        self.page.goto(url, timeout=60_000, wait_until="domcontentloaded")
        _wait_captcha(self.page)

    def save_debug(self, folder: str, files: dict) -> str:
        path = os.path.join(DEBUG_DIR, folder)
        try:
            os.makedirs(path, exist_ok=True)
            for name, content in files.items():
                mode = "wb" if isinstance(content, bytes) else "w"
                kwargs = {} if mode == "wb" else {"encoding": "utf-8"}
                with open(os.path.join(path, name), mode, **kwargs) as f:
                    f.write(content)
        except Exception:
            pass
        return path

    def snapshot(self, folder: str, reason: str, extra: dict | None = None) -> str:
        files: dict = {"info.txt": f"reason: {reason}\nurl: {self.page.url}\n\nNETWORK:\n"
                                    + "\n".join(self.net_log[-300:])}
        try:
            files["page.html"] = self.page.content()[:3_000_000]
        except Exception:
            pass
        try:
            files["page.png"] = self.page.screenshot()
        except Exception:
            pass
        try:
            files["body.txt"] = self.page.locator("body").inner_text(timeout=3000)[:8000]
        except Exception:
            pass
        for i, raw in enumerate(self.samples):
            files[f"response_{i}.json"] = raw
        files.update(extra or {})
        return self.save_debug(folder, files)


def _wait_captcha(page: Page, timeout_s: int = 180) -> None:
    if "captcha" not in page.url:
        return
    print("2ГИС показал капчу: решите её в окне браузера, скрапер подождёт.")
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        page.wait_for_timeout(1500)
        if "captcha" not in page.url:
            return
    raise RuntimeError(
        "2ГИС требует капчу и она не решена. Проверьте, что VPN с российским IP включён "
        "для всей системы, и решите капчу в открывшемся окне."
    )


# --- список --------------------------------------------------------------------


def _firm_count(page: Page) -> int:
    try:
        return len(page.evaluate(LIST_JS))
    except Exception:
        return 0


def _load_list(session: _Session, url: str) -> list[dict]:
    page = session.page
    session.goto(url)
    try:
        page.locator('a[href*="/firm/"]').first.wait_for(timeout=25_000)
    except Exception:
        return []
    page.wait_for_timeout(1500)

    # часть выдачи подгружается прокруткой списка
    for _ in range(6):
        before = _firm_count(page)
        try:
            page.evaluate(SCROLL_JS)
        except Exception:
            break
        page.wait_for_timeout(1500)
        if _firm_count(page) <= before:
            break
    try:
        return page.evaluate(LIST_JS)
    except Exception:
        return []


def _parse_block(info: dict) -> dict:
    """Название, адрес, рейтинг из текста карточки в списке."""
    text = (info.get("text") or "").replace("​", "")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    name = (info.get("name") or "").splitlines()[0].strip() if info.get("name") else ""
    if not name and lines:
        name = lines[0]

    address = ""
    for ln in lines:
        if ln == name or "," not in ln:
            continue
        address = re.sub(r"\s*\d+\s+филиал\S*$", "", ln).strip()
        break

    rating, count = "", ""
    m = _RATING_RE.search(text)
    if m:
        rating, count = m.group(1).replace(",", "."), m.group(2)

    # «Шапка» блока: всё до адреса (название, тип заведения: «Ветеринарный центр»,
    # «Салон для собак», «Груминг-студия»). По ней работает фильтр по типу.
    header = []
    for ln in lines:
        if "," in ln:
            break
        header.append(ln)
    return {"name": name, "address": address, "rating": rating, "reviews_count": count,
            "header": " ".join(header + [name]).lower()}


def _parse_filter(type_filter: str) -> list[str]:
    return [w.strip().lower() for w in re.split(r"[,;]", type_filter or "") if w.strip()]


# --- карточка ------------------------------------------------------------------


def _click_reveal(page: Page) -> None:
    """Нажимает «Показать телефон» (у фирм их бывает несколько)."""
    for _ in range(4):
        try:
            button = page.get_by_text(_REVEAL_RE).first
            if button.count() == 0:
                return
            button.click(timeout=2500)
            page.wait_for_timeout(500)
        except Exception:
            return


def _ld_json(page: Page) -> dict:
    """Структурированные данные страницы, если 2ГИС их отдаёт."""
    out = {"phones": [], "website": "", "rating": "", "count": "", "address": ""}
    try:
        raws = page.eval_on_selector_all(
            'script[type="application/ld+json"]', "els => els.map(e => e.textContent)"
        )
    except Exception:
        return out

    def walk(node):
        if isinstance(node, list):
            for x in node:
                walk(x)
            return
        if not isinstance(node, dict):
            return
        tel = node.get("telephone")
        for t in ([tel] if isinstance(tel, str) else tel or []):
            if isinstance(t, str) and t.strip():
                out["phones"].append(t.strip())
        url = node.get("url")
        if isinstance(url, str) and url.startswith("http") and "2gis" not in url and not out["website"]:
            out["website"] = url
        agg = node.get("aggregateRating")
        if isinstance(agg, dict):
            out["rating"] = out["rating"] or str(agg.get("ratingValue") or "")
            out["count"] = out["count"] or str(agg.get("ratingCount") or agg.get("reviewCount") or "")
        addr = node.get("address")
        if isinstance(addr, dict):
            out["address"] = out["address"] or str(addr.get("streetAddress") or "")
        elif isinstance(addr, str):
            out["address"] = out["address"] or addr
        for v in node.values():
            if isinstance(v, (dict, list)):
                walk(v)

    for raw in raws:
        try:
            walk(json.loads(raw))
        except Exception:
            continue
    return out


def _classify_anchors(anchors: list[dict]) -> dict:
    phones, website, telegram, whatsapp, instagram = [], "", "", "", ""
    for a in anchors:
        href = unquote(a.get("href") or "")
        text = (a.get("text") or "").strip()
        low = href.lower()

        if low.startswith("tel:"):
            phone = href[4:].strip()
            if phone and _digits_key(phone) not in {_digits_key(p) for p in phones}:
                phones.append(phone)
            continue

        m = _TG_RE.search(href)
        if m and not telegram:
            telegram = f"@{m.group(1)}"
            continue
        m = _WA_RE.search(href)
        if m and not whatsapp:
            whatsapp = f"+{m.group(1)}"
            continue
        m = _IG_RE.search(href)
        if m and not instagram and m.group(1).lower() not in _IG_SKIP:
            instagram = f"https://instagram.com/{m.group(1)}"
            continue

        if website or not text or len(text) > 60 or not _DOMAIN_RE.match(text):
            continue
        host = re.sub(r"^https?://", "", text).split("/")[0].lower()
        if "2gis" in low.split("?")[0].split("//")[-1].split("/")[0] and "link.2gis" not in low:
            continue
        if "2gis" in host or any(host.endswith(s) for s in _SOCIAL):
            continue
        website = text if text.startswith("http") else f"http://{text}"
    return {"phones": phones, "website": website, "telegram": telegram, "whatsapp": whatsapp, "instagram": instagram}


def _read_card(session: _Session, base: str, firm_id: str, listed: dict, save_debug: bool) -> dict:
    page = session.page
    session.goto(f"{base}/firm/{firm_id}")
    try:
        page.locator("h1").first.wait_for(timeout=15_000)
    except Exception:
        pass
    page.wait_for_timeout(1500)

    json_leads = [
        _to_lead(i) for i in session.drain_firms()
        if str(i.get("id")).split("_")[0] == firm_id
    ]
    _click_reveal(page)
    page.wait_for_timeout(500)

    try:
        anchors = page.evaluate(ANCHORS_JS)
    except Exception:
        anchors = []
    dom = _classify_anchors(anchors)
    ld = _ld_json(page)
    jl = json_leads[0] if json_leads else {}

    try:
        h1 = page.locator("h1").first.inner_text(timeout=2000).strip()
    except Exception:
        h1 = ""

    phones: list[str] = []
    seen_keys: set[str] = set()
    for phone in (jl.get("phones") or []) + ld["phones"] + dom["phones"]:
        key = _digits_key(phone)
        if key and key not in seen_keys:
            seen_keys.add(key)
            phones.append(phone)

    website = jl.get("website_url") or ld["website"] or dom["website"]
    lead = {
        "name": jl.get("name") or h1 or listed["name"],
        "phones": phones,
        "address": jl.get("address") or ld["address"] or listed["address"],
        "has_website": "да" if website else "нет",
        "website_url": website,
        "rating": jl.get("rating") or ld["rating"] or listed["rating"],
        "reviews_count": jl.get("reviews_count") or re.sub(r"\D", "", ld["count"]) or listed["reviews_count"],
        "telegram": jl.get("telegram") or dom["telegram"],
        "whatsapp": jl.get("whatsapp") or dom["whatsapp"],
        "instagram": dom["instagram"],
    }

    if save_debug and (session.debug_saved == 0 or (not phones and session.debug_saved < 2)):
        session.debug_saved += 1
        session.snapshot(
            "last_run", f"первая карточка {firm_id}, телефонов найдено: {len(phones)}",
            {"card_anchors.json": json.dumps(anchors, ensure_ascii=False, indent=1),
             "card_result.json": json.dumps(lead, ensure_ascii=False, indent=1)},
        )
    return lead


# --- публичный генератор -------------------------------------------------------


def run(query: str, max_leads: int = MAX_LEADS, russian: bool = False, country_code: str = "",
        type_filter: str = ""):
    """Генератор с тем же контрактом, что у scraper.run: по одному отдаёт dict
    (name, phones, address, has_website, website_url, rating, reviews_count,
    плюс telegram/whatsapp, если нашлись в карточке). `russian` не используется:
    2ГИС и так русскоязычный.

    type_filter: слова через запятую. Если задан, берём только фирмы, у которых в
    названии или типе («Салон для собак», «Груминг-студия») есть одно из слов.
    2ГИС ищет по всей карточке, поэтому в выдачу попадают ветклиники и зоомагазины
    с услугой «груминг»."""
    category, city = _split_query(query)
    cc = (country_code or "").upper()
    tld = DOMAINS.get(cc, "ru")
    tld_known = cc in DOMAINS  # иначе tld — угаданный дефолт, а не код страны пользователя
    keywords = _parse_filter(type_filter)
    headless = os.environ.get("LEADOKABAN_HEADLESS") == "1"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(locale="ru-RU")
        page = context.new_page()
        session = _Session(context, page)

        seen: set[str] = set()
        collected = 0
        log: list[str] = []
        LAST_STATS.clear()
        LAST_STATS.update({"claimed": None, "listed": 0, "filtered": 0, "pages": 0, "collected": 0,
                           "stop": ""})
        shutil.rmtree(os.path.join(DEBUG_DIR, "last_run"), ignore_errors=True)

        try:
            base, first_listed = _resolve_city(session, tld, city, category, tld_known)
            search_url = f"{base}/search/{quote(category)}"

            try:
                body_text = page.locator("body").inner_text(timeout=3000)
                m = _CLAIMED_RE.search(body_text)
                LAST_STATS["claimed"] = int(m.group(1)) if m else None
            except Exception:
                pass
            session.snapshot("list_p1", "первая страница выдачи")
            log.append(f"фильтр: {keywords or 'нет'}; 2ГИС заявил мест: {LAST_STATS['claimed']}")

            for page_no in range(1, MAX_PAGES + 1):
                if collected >= max_leads:
                    log.append(f"стоп: достигнут лимит {max_leads}")
                    LAST_STATS["stop"] = f"набран заданный лимит ({max_leads} лидов)"
                    break

                if page_no == 1:
                    listed = first_listed
                    page_url = session.page.url
                else:
                    page_url = f"{search_url}/page/{page_no}"
                    listed = _load_list(session, page_url)
                new = [info for info in listed if info["id"] not in seen]
                LAST_STATS["pages"] = page_no
                log.append(f"стр.{page_no}: {page_url} → итоговый адрес {session.page.url}; "
                           f"ссылок на фирмы {len(listed)}, новых {len(new)}")

                if not new:
                    if page_no == 1:
                        where = session.snapshot("last_run", "в списке нет ссылок на фирмы")
                        raise RuntimeError(
                            f"2ГИС не показал фирмы по адресу {search_url}. Проверьте категорию и VPN. "
                            f"Диагностика: {where}"
                        )
                    session.snapshot(f"list_end_p{page_no}", f"страница {page_no} пустая или без новых фирм")
                    log.append(f"стоп: на странице {page_no} нет новых фирм")
                    LAST_STATS["stop"] = (
                        f"выдача 2ГИС закончилась: страница {page_no} не принесла новых фирм"
                    )
                    break  # страницы кончились

                for info in new:
                    if collected >= max_leads:
                        break
                    seen.add(info["id"])
                    LAST_STATS["listed"] += 1

                    parsed = _parse_block(info)
                    if keywords and not any(k in parsed["header"] for k in keywords):
                        LAST_STATS["filtered"] += 1
                        log.append(f"  отсеян фильтром: {parsed['header'][:90]}")
                        continue  # не тот тип заведения, карточку даже не открываем

                    _human_delay()
                    lead = _read_card(session, base, info["id"], parsed, save_debug=True)
                    yield lead
                    collected += 1
                    LAST_STATS["collected"] = collected
                    log.append(f"  взят: {parsed['header'][:90]}")

                _human_delay()
            else:
                LAST_STATS["stop"] = LAST_STATS["stop"] or f"просмотрено максимум {MAX_PAGES} страниц выдачи"
        finally:
            if not LAST_STATS.get("stop"):
                if collected >= max_leads:
                    LAST_STATS["stop"] = f"набран заданный лимит ({max_leads} лидов)"
                else:
                    LAST_STATS["stop"] = "работа прервана (окно закрыто или ошибка)"
            log.append(f"итого: {LAST_STATS}")
            session.save_debug("last_run", {"run_log.txt": "\n".join(log)})
            browser.close()


def _resolve_city(session: _Session, tld: str, city: str, category: str,
                  tld_known: bool = True) -> tuple[str, list[dict]]:
    """Находит рабочий слаг города. Неизвестный слаг 2ГИС не отвергает, а молча
    показывает другой город (у нас по IP это Москва). Слаг из каталога 2ГИС
    считаем подтверждённым по URL, только если он совпал по правильной стране;
    угаданный (CITY_SLUGS/транслитерация, либо каталожный матч не по той стране) —
    дополнительно сверяем с адресами фирм, чтобы не попасть на другой реальный
    город с совпавшим слагом.

    tld_known=False — country_code не из DOMAINS, tld — угаданный дефолт "ru",
    а не код страны пользователя: совпадение по такой «стране» в каталоге ничего
    не подтверждает."""
    page = session.page
    is_latin = bool(re.fullmatch(r"[a-z0-9_\-]+", (city or "").strip().lower()))
    candidates = _city_slug_candidates(city)
    confirmed: set[str] = set()  # слаги, подтверждённые каталогом 2ГИС (не угаданные)

    # Точный слаг из каталога городов 2ГИС (он зашит в любой странице сайта)
    if not is_latin:
        try:
            session.goto(f"https://2gis.{tld}/")
            page.wait_for_timeout(1500)
            found = _catalog_alias(page.content(), city, tld)
        except RuntimeError:
            raise  # капча не решена
        except Exception:
            found = None
        if found:
            alias, exact = found
            if exact and tld_known:
                confirmed.add(alias)
            candidates = [alias] + [c for c in candidates if c != alias]

    # Для угаданных слагов (CITY_SLUGS/транслитерация, без подтверждения каталогом)
    # нужна доп. проверка: угадывание может случайно совпасть со слагом ДРУГОГО
    # реального города. Каталожный слаг этой проверке не подвергаем — его
    # ложно отвергала сверка с адресами (там обычно только улица/дом, без города).
    stem = re.sub(r"[^а-яa-z]", "", (city or "").lower().replace("ё", "е"))[:6]
    tried: list[str] = []
    queue = list(candidates)

    while queue:
        slug = queue.pop(0)
        base = f"https://2gis.{tld}/{slug}"
        listed = _load_list(session, f"{base}/search/{quote(category)}")
        path = urlparse(session.page.url).path
        tried.append(f"{slug} → {path.split('/')[1] if path.count('/') > 0 else path}")

        if f"/{slug}/" not in path + "/":
            # 2ГИС перекинул в другой город, но на его странице есть каталог городов
            if not is_latin:
                found = _catalog_alias(page.content(), city, tld)
                if found:
                    alias, exact = found
                    if alias not in queue and not any(t.startswith(alias + " ") for t in tried):
                        if exact and tld_known:
                            confirmed.add(alias)
                        queue.insert(0, alias)
            continue

        if slug not in confirmed and stem and not is_latin and len(listed) >= 3:
            blob = " ".join((i.get("text") or "") for i in listed).lower().replace("ё", "е")
            if stem not in blob:
                continue

        return base, listed

    where = session.snapshot("last_run", f"город не найден, пробовали: {', '.join(tried)}")
    raise RuntimeError(
        f"2ГИС не знает город «{city}» под адресами: {', '.join(tried)}. Откройте свой город в "
        f"2ГИС, скопируйте слово из адресной строки (например sergiev-posad в "
        f"2gis.ru/sergiev-posad/...) и впишите его латиницей в поле «Город». Диагностика: {where}"
    )
