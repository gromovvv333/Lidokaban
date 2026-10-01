import os

MAX_LEADS = 100
MIN_DELAY_SECONDS = 5
MAX_DELAY_SECONDS = 10

# Данные лежат вне .app и вне проекта, чтобы пересборка/переустановка их не стирала
DATA_DIR = os.path.expanduser("~/Documents/ЛидоКабан")
os.makedirs(DATA_DIR, exist_ok=True)
OUTPUT_CSV_PATH = os.path.join(DATA_DIR, "leads.csv")
RUNS_JSON_PATH = os.path.join(DATA_DIR, "runs.json")
OUTREACH_JSON_PATH = os.path.join(DATA_DIR, "outreach.json")
TEMPLATES_JSON_PATH = os.path.join(DATA_DIR, "templates.json")

# Через сколько дней молчания лид возвращается в очередь на второе касание
FOLLOWUP_DAYS = 4
