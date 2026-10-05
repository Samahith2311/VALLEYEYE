FROM python:3.13-slim AS build
WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip wheel --extra-index-url https://download.pytorch.org/whl/cpu --no-cache-dir --wheel-dir /wheels .[ml]

FROM python:3.13-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels
EXPOSE 8000
CMD ["uvicorn", "valleyeye.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
