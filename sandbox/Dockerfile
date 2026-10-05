FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    nodejs \
    npm \
    chromium \
    chromium-driver \
    espeak-ng \
    libespeak1 \
    fonts-dejavu \
    fonts-liberation \
    fonts-noto \
    fonts-noto-cjk \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    playwright \
    pyttsx3

COPY renderer /opt/renderer
RUN chmod +x /opt/renderer/*.py

WORKDIR /workspace

CMD ["python", "/opt/renderer/render.py", "--project", "/workspace", "--width", "1920", "--height", "1080", "--fps", "30"]
