build-sandbox:
	docker build -t ai-video-sandbox:latest ./sandbox

run-api:
	uvicorn app.main:app --reload

health:
	curl http://127.0.0.1:8000/health
