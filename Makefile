PYTHONPATH := codebase

dev:
	PYTHONPATH=$(PYTHONPATH) uv run python -m uvicorn src.api.main:app --port 8080 --reload

test:
	PYTHONPATH=$(PYTHONPATH) uv run python -m pytest
