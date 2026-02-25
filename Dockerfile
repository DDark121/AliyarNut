# -----------------------------
# Base image
# -----------------------------
# syntax=docker/dockerfile:1.6
FROM python:3.11-slim

ARG REQUIREMENTS_FILE=requirements.txt

# -----------------------------
# System deps (Telethon / crypto / etc)
# -----------------------------
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# -----------------------------
# Workdir
# -----------------------------
WORKDIR /app

# -----------------------------
# Python deps
# -----------------------------
COPY requirements*.txt /app/

ENV PIP_DISABLE_PIP_VERSION_CHECK=1

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip \
    && pip install -r /app/${REQUIREMENTS_FILE}

# -----------------------------
# Project files
# -----------------------------
COPY . .

# -----------------------------
# Environment
# -----------------------------
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# -----------------------------
# Expose port (если нужен)
# -----------------------------
EXPOSE 8000

# -----------------------------
# Run Telegram FastAPI service
# -----------------------------
CMD ["uvicorn", "telegram_service.app:app", "--host", "0.0.0.0", "--port", "8000"]
