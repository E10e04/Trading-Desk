# Monkora — image web : le backend (FastAPI) sert aussi l'interface. Paper trading uniquement.
FROM node:20-slim AS web
WORKDIR /w
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    BACKEND_HOST=0.0.0.0 BACKEND_PORT=8080 \
    TRADING_DATA_DIR=/data FRONTEND_DIST=/app/frontend/dist
WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install -r requirements.txt
COPY backend/app ./app
COPY --from=web /w/dist /app/frontend/dist
RUN useradd -m -u 10001 monkora && mkdir -p /data && chown -R monkora /data
USER monkora
EXPOSE 8080
VOLUME ["/data"]
CMD ["python", "-m", "app.main"]
