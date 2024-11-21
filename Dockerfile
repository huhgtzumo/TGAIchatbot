FROM python:3.9-slim

WORKDIR /app

# 只保留必要的系統依賴
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN mkdir -p logs && chmod 777 logs
RUN mkdir -p temp && chmod 777 temp

COPY . .

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

ENV TZ=Asia/Taipei
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# 使用非 root 用戶運行
RUN useradd -m botuser
USER botuser

CMD ["python", "src/main.py"]