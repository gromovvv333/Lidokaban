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

from config import MAX_LEADS, OUTPUT_CSV_PATH, RUNS_JSON_PATH
from enrich import build_row
from phone import normalize_phone, pick_mobile
from sources import run as scrape
from storage import add_run, append_lead, get_leads_for_run, load_runs, update_run


def _pick_phone(lead: dict, country_code: str, source: str) -> str | None:
    # В 2ГИС у компании несколько телефонов — берём мобильный (для мессенджеров)
    if source == "2gis":
        return pick_mobile(lead.get("phones") or [], country_code)
    return normalize_phone(lead.get("phone"), country_code)


class Api:
    def __init__(self):
        self.window = None
        self.running = False

    def start(self, city: str, category: str, country_code: str, source: str = "google", russian: bool = False) -> None:
        if self.running:
            return
        self.running = True
        threading.Thread(
            target=self._scrape_thread,
            args=(city.strip(), category.strip(), country_code.strip().upper(), source, russian),
            daemon=True,
        ).start()

    def _scrape_thread(self, city: str, category: str, country_code: str, source: str, russian: bool) -> None:
        query = f"{category}, {city}"
        run_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        collected = 0
        skipped = 0
        status = "error"

        add_run(
            RUNS_JSON_PATH,
            {
                "run_id": run_id,
                "city": city,
                "category": category,
                "country_code": country_code,
                "source": source,
                "russian": russian,
                "started_at": datetime.datetime.now().isoformat(timespec="seconds"),
                "finished_at": None,
                "collected": 0,
                "skipped": 0,
                "status": "running",
            },
        )

        self._call_js("onStatus", f"Кабан идёт по следу: {query}")

        try:
            for lead in scrape(source, query, MAX_LEADS, russian):
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
            self._call_js("onStatus", f"Кабан застрял в кустах: {e}")
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
                },
            )

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
