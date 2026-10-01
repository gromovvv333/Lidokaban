import datetime
import json
import os
import subprocess
import threading
import webbrowser

os.environ.setdefault(
    "PLAYWRIGHT_BROWSERS_PATH", os.path.expanduser("~/Library/Caches/ms-playwright")
)

import webview

import demo as demo_mod
import outreach
import templates as templates_mod
from config import MAX_LEADS, OUTPUT_CSV_PATH, RUNS_JSON_PATH
from enrich import build_row
from phone import normalize_phone, pick_mobile
from sources import run as scrape
from storage import (
    add_run,
    append_lead,
    get_leads_for_run,
    load_all_leads,
    load_runs,
    update_run,
)


def _pick_phone(lead: dict, country_code: str, source: str) -> str | None:
    # В 2ГИС у компании несколько телефонов — берём мобильный (для мессенджеров)
    if source == "2gis":
        return pick_mobile(lead.get("phones") or [], country_code)
    return normalize_phone(lead.get("phone"), country_code)


def _clamp_limit(value) -> int:
    """Лимит приходит из поля формы — бережёмся от пустой строки и абсурдных чисел."""
    try:
        limit = int(str(value).strip())
    except (TypeError, ValueError):
        return MAX_LEADS
    return max(1, min(limit, 500))


class Api:
    def __init__(self):
        self.window = None
        self.running = False

    def start(self, city: str, category: str, country_code: str, source: str = "google",
              russian: bool = False, max_leads: int = MAX_LEADS, type_filter: str = "") -> None:
        if self.running:
            return
        self.running = True
        threading.Thread(
            target=self._scrape_thread,
            args=(city.strip(), category.strip(), country_code.strip().upper(), source, russian,
                  _clamp_limit(max_leads), (type_filter or "").strip()),
            daemon=True,
        ).start()

    def _scrape_thread(self, city: str, category: str, country_code: str, source: str,
                       russian: bool, max_leads: int = MAX_LEADS, type_filter: str = "") -> None:
        query = f"{category}, {city}"
        run_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        collected = 0
        skipped = 0
        status = "error"
        error_text = ""

        add_run(
            RUNS_JSON_PATH,
            {
                "run_id": run_id,
                "city": city,
                "category": category,
                "country_code": country_code,
                "source": source,
                "russian": russian,
                "max_leads": max_leads,
                "type_filter": type_filter,
                "started_at": datetime.datetime.now().isoformat(timespec="seconds"),
                "finished_at": None,
                "collected": 0,
                "skipped": 0,
                "status": "running",
            },
        )

        self._call_js("onStatus", f"Кабан идёт по следу: {query}")

        try:
            for lead in scrape(source, query, max_leads, russian, country_code, type_filter):
                phone = _pick_phone(lead, country_code, source)
                if not phone:
                    skipped += 1
                    continue

                row = build_row(lead, phone, run_id, country_code)
                append_lead(OUTPUT_CSV_PATH, row)
                collected += 1
                self._call_js("onLead", row)
            status = "done"
        except Exception as e:
            import traceback
            with open(os.path.expanduser("~/leadokaban_error.log"), "a", encoding="utf-8") as f:
                f.write(f"{datetime.datetime.now()} {query}\n{traceback.format_exc()}\n")
            error_text = str(e) or e.__class__.__name__
            self._call_js("onStatus", f"Кабан застрял в кустах: {error_text}")
        finally:
            self.running = False
            update_run(
                RUNS_JSON_PATH,
                run_id,
                finished_at=datetime.datetime.now().isoformat(timespec="seconds"),
                collected=collected,
                skipped=skipped,
                status=status,
            )
            self._call_js(
                "onDone",
                {
                    "collected": collected,
                    "skipped": skipped,
                    "path": os.path.abspath(OUTPUT_CSV_PATH),
                    "note": (f"Остановился из-за ошибки: {error_text}" if error_text
                             else self._source_note(source)),
                    "error": bool(error_text),
                },
            )

    @staticmethod
    def _source_note(source: str) -> str:
        """Пояснение, почему сбор закончился и сколько лидов вообще было в выдаче."""
        try:
            if source == "2gis":
                import scraper_2gis as module
            else:
                import scraper as module
            st = module.LAST_STATS
            if not st:
                return ""
            parts = []
            if st.get("stop"):
                parts.append(f"Остановился: {st['stop']}.")
            if source == "2gis":
                if st.get("claimed") is not None:
                    parts.append(f"2ГИС заявил {st['claimed']} мест, просмотрено {st.get('listed', 0)} "
                                 f"на {st.get('pages', 0)} стр., отсеяно фильтром {st.get('filtered', 0)}.")
                    if st["claimed"] > st.get("listed", 0) and "закончилась" in (st.get("stop") or ""):
                        parts.append("Не все заявленные места удалось открыть: возможно, не сработал "
                                     "переход на следующую страницу.")
                else:
                    parts.append(f"Просмотрено {st.get('listed', 0)} на {st.get('pages', 0)} стр., "
                                 f"отсеяно фильтром {st.get('filtered', 0)}.")
            return " ".join(parts)
        except Exception:
            return ""

    def open_url(self, url: str) -> None:
        # Только http(s): клик в окне pywebview иначе навигирует само окно
        if url.startswith(("http://", "https://")):
            webbrowser.open(url)

    def open_app_link(self, app_url: str, fallback_url: str) -> None:
        """Открывает чат сразу в приложении (tg:// / whatsapp://). Если приложение
        не установлено — `open` вернёт ошибку, тогда открываем https-ссылку в браузере."""
        if not app_url.startswith(("tg://", "whatsapp://")):
            return
        result = subprocess.run(["open", app_url], capture_output=True)
        if result.returncode != 0:
            self.open_url(fallback_url)

    def get_history(self) -> list[dict]:
        return list(reversed(load_runs(RUNS_JSON_PATH)))

    def get_run_leads(self, run_id: str) -> list[dict]:
        return get_leads_for_run(OUTPUT_CSV_PATH, run_id)

    # --- Полуавтомат рассылки (см. docs/OUTREACH.md) -----------------------
    # Автоотправки здесь нет: готовим текст и открываем чат, отправляет человек.

    def _city_by_run(self) -> dict:
        return {
            run.get("run_id"): run.get("city") or ""
            for run in load_runs(RUNS_JSON_PATH)
        }

    def _leads_for(self, run_id: str) -> list[dict]:
        run_id = (run_id or "").strip()
        if run_id:
            return get_leads_for_run(OUTPUT_CSV_PATH, run_id)
        return load_all_leads(OUTPUT_CSV_PATH)

    def get_outreach_queue(self, run_id: str = "") -> dict:
        """Очередь конвейера: сначала просроченные напоминания, потом новые."""
        leads = self._leads_for(run_id)
        cities = self._city_by_run()
        tpls = templates_mod.load_templates()

        items = []
        for item in outreach.build_queue(leads):
            lead = item["lead"]
            city = cities.get(lead.get("run_id"), "")
            template_id, text = templates_mod.render(
                lead, city=city, touch=item["touch"], templates=tpls
            )
            items.append({
                "key": item["key"],
                "name": lead.get("name") or "",
                "city": city,
                "rating": lead.get("rating") or "",
                "reviews_count": lead.get("reviews_count") or "",
                "website": lead.get("website") or "",
                "instagram": lead.get("instagram") or "",
                "address": lead.get("address") or "",
                "lead_phone": lead.get("phone") or "",
                "russian": lead.get("russian") or "",
                "channel": item["channel"],
                "contact": item["contact"],
                "app_url": item["app_url"],
                "web_url": item["web_url"],
                "touch": item["touch"],
                "template_id": template_id,
                "message": text,
            })

        return {"items": items, "stats": outreach.stats(leads)}

    def get_awaiting(self) -> list[dict]:
        return outreach.awaiting()

    def mark_outreach(self, key: str, status: str, template_id: str = "", touch: int = 1,
                      name: str = "", contact: str = "") -> bool:
        try:
            outreach.mark(key, status, template_id, int(touch or 1), name, contact)
        except ValueError:
            return False
        return True

    def copy_to_clipboard(self, text: str) -> bool:
        """Текст в буфер через pbcopy: схема tg:// подставить его в чат не умеет."""
        try:
            # У .app, запущенного из Finder, нет LANG, и pbcopy читает UTF-8 как MacRoman
            # (в буфер улетает «–Ч–і—А–∞–≤...»). Локаль задаём явно.
            env = {**os.environ, "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8"}
            subprocess.run(["pbcopy"], input=(text or "").encode("utf-8"), check=True, env=env)
        except (OSError, subprocess.CalledProcessError):
            return False
        return True

    def send_step(self, app_url: str, web_url: str, text: str, lead: dict | None = None, city: str = "") -> bool:
        """Один клик пользователя: текст в буфер + открытый чат + папка с демо-видео салона (если оно уже собрано)."""
        copied = self.copy_to_clipboard(text)
        self.open_app_link(app_url, web_url)
        if lead:
            demo_mod.reveal(lead, city, generate=False)
        return copied

    def open_demo(self, lead: dict, city: str = "") -> str:
        """Кнопка «Демо»: открывает папку с видео, а если ролика нет, сначала собирает его (около минуты)."""
        return demo_mod.reveal(lead, city, generate=True)

    def get_templates(self) -> dict:
        return templates_mod.load_templates()

    def save_template(self, template_id: str, text: str) -> dict:
        return templates_mod.save_template(template_id, text)

    def _call_js(self, func_name: str, arg) -> None:
        if self.window is None:
            return
        self.window.evaluate_js(f"{func_name}({json.dumps(arg)})")


def main() -> None:
    print("Создаём окно...")
    api = Api()
    ui_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui.html")
    window = webview.create_window(
        "🐗 ЛидоКабан",
        ui_path,
        js_api=api,
        width=900,
        height=650,
    )
    api.window = window
    print("Окно создано, запускаем event loop...")
    webview.start(debug=True)
    print("Event loop завершён (окно закрыто).")


if __name__ == "__main__":
    main()
