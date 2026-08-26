FROM python:3.11-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

COPY pyproject.toml README.md* ./
COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./

RUN pip install --no-cache-dir ".[postgres]"

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "vigil.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
