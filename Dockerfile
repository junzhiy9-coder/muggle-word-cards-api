# Muggle Word Cards 后端 — Render 部署用
# 用法：把 backend/ 整个文件夹推到 GitHub（仓库根目录就是这些文件），
# 在 Render 里新建 Web Service 并连上这个仓库，Render 会按 render.yaml 自动部署。
FROM python:3.12-slim

# 系统依赖：Tesseract OCR 可执行文件是拍照提取必需的（只认英文，eng 语言包默认自带）
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

# Render 会注入 $PORT 环境变量；本地跑默认 8000
ENV MWC_DB_PATH=data/muggle.db
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
