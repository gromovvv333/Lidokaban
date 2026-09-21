import csv
import json
import os

FIELDNAMES = [
    "run_id", "name", "phone", "address", "has_website", "website", "rating", "reviews_count",
    "telegram", "has_bot", "whatsapp", "zalo", "is_mobile", "wa_link", "tg_link", "russian",
]


def _migrate_header(csv_path: str) -> None:
    """Если leads.csv создан со старым набором колонок — переписываем под новый."""
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames == FIELDNAMES:
            return
        rows = list(reader)
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def append_lead(csv_path: str, lead: dict) -> None:
    """Дописывает один лид в CSV-файл, создавая заголовок при первой записи."""
    file_exists = os.path.exists(csv_path)
    if file_exists:
        _migrate_header(csv_path)

    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if not file_exists:
            writer.writeheader()
        writer.writerow(lead)


def get_leads_for_run(csv_path: str, run_id: str) -> list[dict]:
    """Возвращает лиды, собранные в рамках конкретного запуска (run_id)."""
    if not os.path.exists(csv_path):
        return []

    with open(csv_path, encoding="utf-8") as f:
        return [row for row in csv.DictReader(f) if row.get("run_id") == run_id]


def load_runs(runs_path: str) -> list[dict]:
    """Возвращает список всех прошлых запусков (метаданные), новые последние."""
    if not os.path.exists(runs_path):
        return []

    with open(runs_path, encoding="utf-8") as f:
        return json.load(f)


def _save_runs(runs_path: str, runs: list[dict]) -> None:
    with open(runs_path, "w", encoding="utf-8") as f:
        json.dump(runs, f, ensure_ascii=False, indent=2)


def add_run(runs_path: str, run: dict) -> None:
    """Добавляет запись о новом запуске в историю."""
    runs = load_runs(runs_path)
    runs.append(run)
    _save_runs(runs_path, runs)


def update_run(runs_path: str, run_id: str, **fields) -> None:
    """Обновляет запись о запуске (например, по завершении сбора)."""
    runs = load_runs(runs_path)
    for run in runs:
        if run.get("run_id") == run_id:
            run.update(fields)
            break
    _save_runs(runs_path, runs)
