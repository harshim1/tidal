FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Fail closed: a container is a deployment, so it refuses to start without TIDAL_PASSWORD
# and TIDAL_MCP_TOKEN. Set TIDAL_PUBLIC=0 only on a private network.
ENV TIDAL_PUBLIC=1 PYTHONUNBUFFERED=1
EXPOSE 8000
# Dashboard, REST API, Ring webhook and Alexa+ MCP (/mcp) on one port ($PORT on Render).
CMD ["sh", "-c", "exec uvicorn app.server:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
