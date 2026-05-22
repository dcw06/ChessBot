FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

# Install Stockfish from apt
RUN apt-get update \
    && apt-get install -y --no-install-recommends stockfish \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps — CPU-only torch to keep image small and memory low
COPY requirements.txt .
RUN pip install --no-cache-dir flask gunicorn python-chess requests numpy python-dotenv onnxruntime

# Copy source + data files (model, opening book)
COPY . .

EXPOSE 10000
CMD gunicorn -w 1 --timeout 120 -b 0.0.0.0:${PORT:-10000} web_app:app
