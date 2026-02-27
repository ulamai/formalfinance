FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md /app/
COPY formalfinance /app/formalfinance

RUN pip install --no-cache-dir .

EXPOSE 8080

CMD ["formalfinance", "serve", "--host", "0.0.0.0", "--port", "8080", "--db-path", "/data/runs.sqlite3"]
