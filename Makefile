.PHONY: help install demo serve simulate loadtest test synth clean

help:
	@echo "make install   - create venv and install (pip install -e .[dev])"
	@echo "make demo      - run the dashboard + built-in cluster simulator (http://127.0.0.1:8000)"
	@echo "make serve     - run the API/dashboard only"
	@echo "make simulate  - drive telemetry at a running server over HTTP"
	@echo "make loadtest  - benchmark detector throughput"
	@echo "make test      - run the test suite"
	@echo "make synth     - cdk synth the infrastructure"

install:
	python3 -m venv .venv
	. .venv/bin/activate && pip install --upgrade pip && pip install -e ".[dev]"

demo:
	. .venv/bin/activate && python -m gpumon demo

serve:
	. .venv/bin/activate && python -m gpumon serve

simulate:
	. .venv/bin/activate && python -m gpumon simulate

loadtest:
	. .venv/bin/activate && python -m gpumon loadtest

test:
	. .venv/bin/activate && pytest -q

synth:
	cd infrastructure && npm install && npx cdk synth

clean:
	rm -rf .venv argus.db *.db .pytest_cache infrastructure/cdk.out infrastructure/node_modules
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
