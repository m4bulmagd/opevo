FROM python:3.13-slim

WORKDIR /app

COPY pyproject.toml uv.lock ./

RUN pip install uv && uv sync --frozen --no-dev

COPY agent ./agent

CMD ["uv", "run", "python", "-m", "agent.main"]
