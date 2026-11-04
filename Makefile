.PHONY: install corpus demo test check all

install:
	pip install -e ".[dev]"

corpus:        ## generate the seeded synthetic corpus (deterministic)
	python -m parser_assurance.scripts.generate_corpus

demo: corpus   ## run the full end-to-end demonstration
	python -m parser_assurance

test:          ## run the test suite (thesis + invariants)
	pytest

check:         ## run the oracle-free monitor on the example stream (exits 1: it contains drift)
	-python -m parser_assurance.cli examples/claude_drift.jsonl

all: install corpus test demo
