FROM python:3.11-alpine

WORKDIR /app

# 纯标准库，无需 pip install
COPY netsub_client.py netsub_server.py ./

# 产物目录（可挂载出来）
RUN mkdir -p /app/out
VOLUME ["/app/out"]

EXPOSE 8787

# 健康检查
HEALTHCHECK --interval=60s --timeout=10s --start-period=90s --retries=3 \
  CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8787/health',timeout=5).read().strip()==b'ok' else 1)"

CMD ["python3", "netsub_server.py", "--interval", "240", "--port", "8787", "--out", "/app/out"]
