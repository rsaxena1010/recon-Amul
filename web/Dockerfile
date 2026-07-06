FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY dashboard_core.py app.py ./

ENV DATA_DIR=/data \
    PORT=8080
RUN mkdir -p /data

EXPOSE 8080

# 2 workers, generous timeout for large-file parsing
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "2", "--timeout", "120", "app:app"]
