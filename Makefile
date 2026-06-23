.PHONY: install corpus demo test all

install:
	pip install -e ".[dev]"

corpus:        ## generate the seeded synthetic corpus (deterministic)
	python -m parser_assurance.scripts.generate_corpus

demo: corpus   ## run the full end-to-end demonstration
	python -m parser_assurance

test:          ## run the test suite (thesis + invariants)
	pytest

all: install corpus test demo
