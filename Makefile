.PHONY: test lint typecheck check

test:
	pytest

lint:
	ruff check .

typecheck:
	mypy src

check: lint typecheck test
