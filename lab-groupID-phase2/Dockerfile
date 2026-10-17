# python:3.9-slim-buster from the assignment does not build anymore (Debian Buster is end-of-life)
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY wordcount/ wordcount/
COPY bench/ bench/
COPY tests/ tests/

# show print() output and log lines immediately
ENV PYTHONUNBUFFERED=1
