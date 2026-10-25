FROM node:20-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim
WORKDIR /app
# git: 반복별 diff를 작업 저장소에서 읽기 위해 사용
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY --from=frontend-build /app/frontend/dist ./frontend/dist
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
