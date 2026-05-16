FROM python:3.11-slim

WORKDIR /app

# System deps minimal — pandas/numpy wheels are prebuilt
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    tzdata \
  && rm -rf /var/lib/apt/lists/*

ENV TZ=UTC \
    PYTHONUNBUFFERED=1 \
    RUN_MODE=loop \
    LOOP_INTERVAL_SEC=60

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot ./bot

# State directory (mounted as volume on Oracle)
RUN mkdir -p /app/state

CMD ["python", "-m", "bot.main", "--loop"]
