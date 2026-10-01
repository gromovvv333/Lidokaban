"""Демо-видео для салона: ищем готовое в проекте бота, при необходимости собираем, кладём в папку под именем салона."""
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile

from config import DATA_DIR

BOT_DIR = os.path.expanduser("~/Documents/VIBECODE/dark_tail_bot")
BOT_OUT = os.path.join(BOT_DIR, "promo", "out")
DEMO_DIR = os.path.join(DATA_DIR, "Демо")


def _safe(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', " ", name).strip()[:80] or "salon"


def _source_videos(name: str) -> tuple[str, str] | None:
    """Готовые ролики салона (мини-апп, бот): по имени в profile.json, а не по слагу, чтобы не дублировать транслитерацию."""
    if not os.path.isdir(BOT_OUT):
        return None
    for folder in os.listdir(BOT_OUT):
        try:
            with open(os.path.join(BOT_OUT, folder, "profile.json"), encoding="utf-8") as f:
                if json.load(f).get("name") != name:
                    continue
        except (OSError, ValueError):
            continue
        app, bot = (os.path.join(BOT_OUT, folder, n) for n in ("video.mp4", "bot_video.mp4"))
        if os.path.exists(app) and os.path.exists(bot):
            return (app, bot)
    return None


def _log(text: str) -> None:
    with open(os.path.expanduser("~/leadokaban_error.log"), "a", encoding="utf-8") as f:
        f.write(f"[demo] {text}\n")


def _generate(lead: dict, city: str) -> bool:
    python = os.path.join(BOT_DIR, "venv", "bin", "python")
    if not os.path.exists(python):
        return False
    cmd = [python, "-m", "promo.make", "--name", lead["name"], "--address", lead.get("address", ""),
           "--phone", lead.get("phone", ""), "--city", city, "--site", lead.get("website", "")]
    # у .app из Launchpad в PATH нет Homebrew, а ролик кодируется через ffmpeg
    # и py2app подсовывает свои PYTHONHOME/PYTHONPATH/RESOURCEPATH: с ними venv бота не находит даже stdlib
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("PYTHON", "PY2APP", "RESOURCEPATH", "EXECUTABLEPATH", "ARGVZERO", "__PYVENV"))}
    env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + env.get("PATH", "/usr/bin:/bin")
    # Вывод пишем в файл, а не в pipe: chromium/node-драйвер playwright могут пережить скрипт
    # и держать pipe открытым, тогда run() висит после готового ролика.
    out_path = os.path.join(tempfile.gettempdir(), "leadokaban_demo.log")
    try:
        with open(out_path, "w", encoding="utf-8") as out:
            proc = subprocess.Popen(cmd, cwd=BOT_DIR, env=env, stdout=out, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, start_new_session=True)
            try:
                code = proc.wait(timeout=240)
            except subprocess.TimeoutExpired:
                code = None
            finally:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)  # добиваем оставшиеся браузеры
                except OSError:
                    pass
    except OSError as e:
        _log(f"{lead['name']}: {e}")
        return False
    with open(out_path, encoding="utf-8", errors="replace") as f:
        tail = f.read()[-800:]
    if code != 0 or "✗" in tail:
        _log(f"{lead['name']}: код {code}, {tail}")
        return False
    return True


def reveal(lead: dict, city: str = "", generate: bool = True) -> str:
    """Кладёт ролики в «Демо/<Салон>_миниапп.mp4» и «_бот.mp4», выделяет их в Finder. Возвращает: opened / generated / missing."""
    name = lead["name"]
    src = _source_videos(name)
    status = "opened"
    if not src:
        if not generate or not _generate(lead, city):
            return "missing"
        src, status = _source_videos(name), "generated"
        if not src:
            return "missing"
    os.makedirs(DEMO_DIR, exist_ok=True)
    dsts = [os.path.join(DEMO_DIR, f"{_safe(name)}_{kind}.mp4") for kind in ("миниапп", "бот")]
    for s_, d_ in zip(src, dsts):
        shutil.copyfile(s_, d_)
    subprocess.run(["open", "-R", *dsts])  # Finder выделит оба файла: их остаётся перетащить в чат
    return status
