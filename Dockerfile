FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p data/audit

ENV PYTHONUNBUFFERED=1
# Railway injects PORT at runtime; default only for local docker runs.
ENV PORT=8080

CMD ["python", "run.py"]
