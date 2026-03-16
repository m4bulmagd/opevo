FROM python:3.13-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1

COPY pyproject.toml uv.lock ./

RUN pip install uv && uv sync --frozen --no-dev

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY docker-entrypoint.sh ./

RUN chmod +x ./docker-entrypoint.sh

EXPOSE 8000
CMD ["./docker-entrypoint.sh"]
