FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir starlette uvicorn httpx

COPY . /app/scaffold/auth/sidecar/
# Minimal package structure so imports work
RUN mkdir -p /app/scaffold/auth && \
    touch /app/scaffold/__init__.py /app/scaffold/auth/__init__.py

ENV PORT=8000
EXPOSE 8000

CMD ["uvicorn", "scaffold.auth.sidecar.app:app", "--host", "0.0.0.0", "--port", "$PORT"]
