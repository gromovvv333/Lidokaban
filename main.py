import datetime

from config import MAX_LEADS, OUTPUT_CSV_PATH, RUNS_JSON_PATH
from phone import normalize_phone
from scraper import run as scrape
from storage import add_run, append_lead, update_run


def main() -> None:
    city = input("Город/регион: ").strip()
    category = input("Категория бизнеса: ").strip()
    country_code = input("Код страны (например VN, RU): ").strip().upper()

    query = f"{category}, {city}"
    print(f"\nИщем: {query}\n")

    run_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    collected = 0
    skipped_no_phone = 0
    status = "error"

    add_run(
        RUNS_JSON_PATH,
        {
            "run_id": run_id,
            "city": city,
            "category": category,
            "country_code": country_code,
            "started_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "finished_at": None,
            "collected": 0,
            "skipped": 0,
            "status": "running",
        },
    )

    try:
        for lead in scrape(query, max_leads=MAX_LEADS):
            phone = normalize_phone(lead.get("phone"), country_code)
            if not phone:
                skipped_no_phone += 1
                continue

            append_lead(
                OUTPUT_CSV_PATH,
                {
                    "run_id": run_id,
                    "name": lead.get("name") or "",
                    "phone": phone,
                    "address": lead.get("address") or "",
                    "has_website": lead.get("has_website") or "",
                    "rating": lead.get("rating") or "",
                    "reviews_count": lead.get("reviews_count") or "",
                },
            )
            collected += 1
            print(f"[{collected}] {lead.get('name')} — {phone}")
        status = "done"
    except KeyboardInterrupt:
        print("\nОстановлено пользователем.")
        status = "done"
    except Exception as e:
        print(f"\nСбой во время парсинга: {e}")
    finally:
        update_run(
            RUNS_JSON_PATH,
            run_id,
            finished_at=datetime.datetime.now().isoformat(timespec="seconds"),
            collected=collected,
            skipped=skipped_no_phone,
            status=status,
        )

    print(f"\nГотово. Сохранено лидов: {collected} (в {OUTPUT_CSV_PATH})")
    if collected < MAX_LEADS:
        print("Похоже, релевантные результаты в этом регионе/категории закончились.")
    if skipped_no_phone:
        print(f"Пропущено без валидного телефона: {skipped_no_phone}")


if __name__ == "__main__":
    main()
