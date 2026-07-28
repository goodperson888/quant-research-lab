PYTHON ?= .venv/bin/python

.PHONY: doctor init test daily weekly dev dev-api dev-web dev-worker web-build web-lint web-typecheck check-fast check-standard check-release

doctor:
	PYTHONPATH=src $(PYTHON) -m quant_lab.cli doctor

init:
	PYTHONPATH=src $(PYTHON) -m quant_lab.cli init

test:
	$(PYTHON) -m pytest

daily:
	./scripts/run_daily.sh

weekly:
	./scripts/run_weekly.sh

dev:
	./scripts/dev.sh

dev-api:
	./scripts/dev-api.sh

dev-web:
	./scripts/dev-web.sh

dev-worker:
	./scripts/dev-worker.sh

web-build:
	cd apps/web && npm run build -- --webpack

web-lint:
	cd apps/web && npm run lint

web-typecheck:
	cd apps/web && npm run typecheck

check-fast:
	./scripts/check-fast.sh

check-standard:
	./scripts/check-standard.sh

check-release:
	./scripts/check-release.sh
