FROM python:3.13-slim

WORKDIR /app

# 의존성 먼저 설치 (레이어 캐시)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 앱 + 데이터 (functions.json / scores.json 은 서버 전용, 클라 비공개)
COPY . .

EXPOSE 8000

# Render 등은 $PORT 를 주입한다. 없으면 8000.
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
