# Host-side API image. The render sandbox is a separate image, build it with:
#   docker build -t ai-video-sandbox:latest ./sandbox
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

# Docker CLI only: the daemon is the host's, reached through the mounted socket.
COPY --from=docker:cli /usr/local/bin/docker /usr/local/bin/docker

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

EXPOSE 8000
# 0.0.0.0 inside the container; docker-compose publishes it on the host's loopback only.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
