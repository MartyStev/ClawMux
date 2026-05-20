FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (Docker layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src/ ./src/
COPY alembic/ ./alembic/
COPY alembic.ini .
COPY docker/entrypoint.sh .
RUN chmod +x entrypoint.sh

# Run: migrations → uvicorn
EXPOSE 8060
ENTRYPOINT ["./entrypoint.sh"]
