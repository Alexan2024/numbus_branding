FROM python:3.11-slim

# libfribidi0 — нужна Pillow для полноценной вёрстки текста (Raqm): кернинг и лигатуры
# как в браузере, иначе превью в редакторе и итог разойдутся. DejaVu — последний
# запасной шрифт для редких знаков.
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-dejavu-core curl ca-certificates libfribidi0 \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY scripts/get_fonts.sh scripts/
RUN sh scripts/get_fonts.sh fonts \
    && python -c "from PIL import features; print('Raqm (кернинг как в браузере):', features.check('raqm'))"

COPY *.py webapp.html ./

# Редактор и проверка живости слушают PORT (Railway задаёт сам, по умолчанию 8080)
HEALTHCHECK --interval=60s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fs "http://127.0.0.1:${PORT:-8080}/healthz" || exit 1

CMD ["python", "bot.py"]
