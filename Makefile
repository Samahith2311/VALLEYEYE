.PHONY: test lint run e2e

test:
	python -m pytest

lint:
	python -m ruff check .
	python -m mypy src/valleyeye

run:
	python -m uvicorn valleyeye.api.app:app --reload --app-dir src

e2e:
	python -m pytest -m e2e
