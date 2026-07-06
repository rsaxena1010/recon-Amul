FROM python:3.11-slim

WORKDIR /app

# Pure standard-library app: NO pip install (the build cluster's package proxy
# can't deliver wheels). Everything below ships with CPython 3.11.
# The parquet reader loads from jsdelivr CDN in the browser (see app.py). A local
# CLI build can additionally bake web/static/parquet.min.js to self-host it.
COPY render.py app.py ./

ENV DATA_DIR=/data
ENV PORT=8080

EXPOSE 8080

CMD ["python", "app.py"]
