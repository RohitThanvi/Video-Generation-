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
    libespeak-ng1 \
    fonts-inter \
    fonts-roboto \
    fonts-dejavu \
    fonts-liberation \
    fonts-noto \
    fonts-noto-cjk \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    playwright \
    pyttsx3

# JavaScript libraries that generated videos can use offline. render.py serves them to the
# page under /vendor/<name>/ and maps `import ... from 'three' | 'gsap' | 'katex' | 'mathkit'`.
# (Chromium comes from apt; frames are screenshots piped into the system ffmpeg, so Playwright's
# own browser/ffmpeg downloads are not needed.)
ENV VIDEO_LIBS_DIR=/opt/lib/node_modules
RUN mkdir -p /opt/lib && cd /opt/lib \
    && npm install --omit=dev --no-audit --no-fund --no-package-lock \
       three@0.186.1 gsap@3.15.0 d3@7.9.0 katex@0.19.0 \
    && npm cache clean --force

COPY renderer /opt/renderer
RUN chmod +x /opt/renderer/*.py

WORKDIR /workspace

CMD ["python", "/opt/renderer/render.py", "--project", "/workspace", "--width", "1920", "--height", "1080", "--fps", "30"]
