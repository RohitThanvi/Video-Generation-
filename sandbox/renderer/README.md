# Sandbox renderer

This image contains the deterministic rendering environment.

`render.py` opens `/workspace/source/index.html` with Chromium, records the browser, and uses FFmpeg to produce `renders/final/final.mp4`.

`tts.py` generates local narration with pyttsx3.

The container is intentionally network-isolated by the host runner, so generated HTML should use local assets rather than remote CDNs.
