FROM python:3.12-slim

# =========================
# 构建代理
# =========================
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG NO_PROXY

ENV HTTP_PROXY=${HTTP_PROXY} \
    HTTPS_PROXY=${HTTPS_PROXY} \
    NO_PROXY=${NO_PROXY} \
    http_proxy=${HTTP_PROXY} \
    https_proxy=${HTTPS_PROXY} \
    no_proxy=${NO_PROXY}

WORKDIR /app

# =========================
# 安装依赖
# =========================
COPY pyproject.toml ./

RUN pip install --no-cache-dir -e .

# =========================
# 复制代码
# =========================
COPY . .

# =========================
# 运行
# =========================
EXPOSE 8000

CMD ["python", "main.py"]