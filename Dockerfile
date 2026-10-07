FROM python:3.11-slim

# libfribidi0 — нужна Pillow для полноценной вёрстки текста (Raqm): кернинг и лигатуры
# как в браузере, иначе превью в редакторе и итог разойдутся. DejaVu — последний
# запасной шрифт для редких знаков.
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-dejavu-core curl ca-certificates libfribidi0 \
    && rm -rf /var/lib/apt/lists/*

# MALLOC_ARENA_MAX=2: память после тяжёлых альбомов возвращается системе (замер: −40% RSS в простое)
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 MALLOC_ARENA_MAX=2
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

# Шрифты каталога. Если в репозитории есть папка fonts/ со всеми файлами .ttf — берём их
# (сборка не зависит от raw.githubusercontent.com); иначе скачиваем scripts/get_fonts.sh.
# «fonts*» без папки fonts ничего не копирует и не ломает сборку — скрипт копируется всегда.
COPY scripts/get_fonts.sh fonts* /tmp/fontsrc/
RUN mkdir -p fonts scripts && cp /tmp/fontsrc/get_fonts.sh scripts/ \
    && for f in /tmp/fontsrc/*.ttf; do [ -f "$f" ] && cp "$f" fonts/; done; \
    missing=$(awk '$1 == "get" {print $3}' scripts/get_fonts.sh | while read -r f; do [ -s "fonts/$f" ] || echo "$f"; done); \
    if [ -n "$missing" ]; then echo "Шрифтов нет в репозитории — скачиваю"; sh scripts/get_fonts.sh fonts; \
    else echo "Шрифты из репозитория: $(ls fonts | wc -l) файлов"; fi \
    && rm -rf /tmp/fontsrc \
    && python -c "from PIL import features; print('Raqm (кернинг как в браузере):', features.check('raqm'))"

COPY *.py webapp.html ./

# Редактор и проверка живости слушают PORT (Railway задаёт сам, по умолчанию 8080).
# Railway HEALTHCHECK из Dockerfile не читает — путь /healthz задан в railway.toml.
HEALTHCHECK --interval=60s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fs "http://127.0.0.1:${PORT:-8080}/healthz" || exit 1

CMD ["python", "bot.py"]
