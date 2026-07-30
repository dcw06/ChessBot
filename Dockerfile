FROM python:3.11.15-slim-bookworm

ENV PYTHONUNBUFFERED=1
ENV TRUST_PROXY_HEADERS=1
ENV COOKIE_SECURE=1

# Install Stockfish from apt
RUN apt-get update \
    && apt-get install -y --no-install-recommends stockfish \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install inference-only dependencies. Training dependencies such as PyTorch are
# deliberately excluded from the production image.
COPY requirements-runtime.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements-runtime.lock

# Copy source + data files (model, opening book)
COPY . .

RUN useradd --create-home --uid 10001 chessbot \
    && chown -R chessbot:chessbot /app
USER chessbot

RUN REQUIRE_STOCKFISH=1 python -m scripts.smoke_test

EXPOSE 10000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:10000/health/ready', timeout=3)" || exit 1
CMD gunicorn -w 1 --worker-class gthread --threads 4 --timeout 120 \
    -b 0.0.0.0:${PORT:-10000} web_app:app
