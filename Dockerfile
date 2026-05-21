FROM python:3.12-slim

WORKDIR /app

# 安装依赖
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e .

# 复制代码
COPY . .

# 暴露端口
EXPOSE 8000

# 启动
CMD ["python", "main.py"]
