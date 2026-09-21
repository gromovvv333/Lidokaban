#!/bin/sh
# py2app кладёт playwright/greenlet в zip, а оттуда не запустить node-драйвер
# и .so — копируем их как обычные папки. Запускать после `python setup.py py2app`.
set -e
SP=venv/lib/python3.14/site-packages
L="dist/Парсер лидов.app/Contents/Resources/lib/python3.14"
zip -qd "dist/Парсер лидов.app/Contents/Resources/lib/python314.zip" "playwright/*" "greenlet/*" || true
[ -d "$L/playwright" ] || cp -R $SP/playwright $SP/greenlet "$L"/
find "$L/playwright" "$L/greenlet" -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null; true # "$L/greenlet" -name __pycache__ -prune -exec rm -rf {} +
# свежие исходники поверх упакованных в zip (быстрее полной пересборки)
for m in config scraper phone storage enrich sources scraper_2gis; do
  zip -qd "dist/Парсер лидов.app/Contents/Resources/lib/python314.zip" "$m.pyc" 2>/dev/null || true
  cp $m.py "$L"/
done
cp app.py ui.html "dist/Парсер лидов.app/Contents/Resources/"
rm -rf "/Applications/Парсер лидов.app" && cp -R "dist/Парсер лидов.app" /Applications/
