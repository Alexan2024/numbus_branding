FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-dejavu-core curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Шрифты (лицензия OFL) скачиваются при сборке с зафиксированного коммита
# Google Fonts — сборка не сломается, если у них что-то переименуют.
ARG GF=https://raw.githubusercontent.com/google/fonts/23e54b51ddffbc7713c583748e3bd86f62b1fa4a/ofl
RUN mkdir -p fonts && cd fonts \
    && curl -fsSL "$GF/nunito/Nunito%5Bwght%5D.ttf" -o Nunito.ttf \
    && curl -fsSL "$GF/inter/Inter%5Bopsz,wght%5D.ttf" -o Inter.ttf \
    && curl -fsSL "$GF/manrope/Manrope%5Bwght%5D.ttf" -o Manrope.ttf \
    && curl -fsSL "$GF/montserrat/Montserrat%5Bwght%5D.ttf" -o Montserrat.ttf \
    && curl -fsSL "$GF/unbounded/Unbounded%5Bwght%5D.ttf" -o Unbounded.ttf \
    && curl -fsSL "$GF/playfairdisplay/PlayfairDisplay%5Bwght%5D.ttf" -o PlayfairDisplay.ttf

COPY *.py ./

CMD ["python", "bot.py"]
