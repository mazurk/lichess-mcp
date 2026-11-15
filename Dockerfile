FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends stockfish \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py engine.py analysis.py puzzles.json ./
COPY web/ ./web/

# état persisté des parties (monté sur le volume nommé games_data en compose)
RUN mkdir -p /app/data

EXPOSE 8000

CMD ["python", "server.py"]
