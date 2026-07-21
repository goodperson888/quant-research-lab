PYTHON ?= .venv/bin/python

.PHONY: doctor init test daily weekly

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
