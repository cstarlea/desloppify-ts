.PHONY: \
	ci \
	ci-fast \
	lint \
	typecheck \
	arch \
	ci-contracts \
	tests \
	tests-full \
	tests-golden-node \
	sync-docs \
	package-smoke \
	install-hooks \
	install-dev \
	install-full

PIP := python -m pip
LINT_IMPORTS := $(shell python -c "import pathlib,sys; print(pathlib.Path(sys.executable).with_name('lint-imports'))")
IMPORTLINTER_CONFIG ?= .github/importlinter.ini
PYTEST_XML ?=
PYTEST_XML_FLAG := $(if $(PYTEST_XML),--junitxml=$(PYTEST_XML),)
PYTEST_TIMEOUT ?= 120
PYTEST := pytest --timeout=$(PYTEST_TIMEOUT)

sync-docs:
	mkdir -p desloppify/data/global
	find desloppify/data/global -maxdepth 1 -type f -name '*.md' -delete
	cp docs/*.md desloppify/data/global/

install-hooks:
	@hooks=$$(git rev-parse --git-path hooks 2>/dev/null || echo .git/hooks) && \
		mkdir -p "$$hooks" && \
		cp .githooks/pre-commit "$$hooks/pre-commit" && \
		chmod +x "$$hooks/pre-commit"
	@echo "Git hooks installed."

# The gates below run with whatever is installed; install once with one of these.
install-dev: install-hooks
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"

install-full: install-hooks
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[full,dev]"

lint:
	ruff check . --select E9,F63,F7,F82

typecheck:
	python -m mypy

arch:
	@if [ ! -f "$(IMPORTLINTER_CONFIG)" ]; then \
		echo "Missing $(IMPORTLINTER_CONFIG). Add import contracts before running arch gate."; \
		exit 1; \
	fi
	$(LINT_IMPORTS) --config $(IMPORTLINTER_CONFIG)

ci-contracts:
	$(PYTEST) -q desloppify/tests/ci/test_ci_contracts.py
	$(PYTEST) -q desloppify/tests/commands/test_lifecycle_transitions.py -k "assessment_then_score_when_no_review_followup"

tests:
	$(PYTEST) -q $(PYTEST_XML_FLAG)

tests-full:
	@python -c "import tree_sitter_language_pack" 2>/dev/null || \
		{ echo "tests-full needs the [full] extra: run make install-full."; exit 1; }
	$(PYTEST) -q $(PYTEST_XML_FLAG)

GOLDEN_NODE_DIR := desloppify/languages/typescript/tests/golden/node

tests-golden-node:
	npm ci --prefix $(GOLDEN_NODE_DIR) --no-audit --no-fund
	DESLOPPIFY_REQUIRE_NODE_GOLDEN=1 $(PYTEST) -q -rs desloppify/languages/typescript/tests/test_ts_golden.py \
		desloppify/languages/typescript/tests/test_ts_fixer_roundtrip.py

package-smoke:
	rm -rf dist .pkg-smoke
	python -m build
	twine check dist/*
	python -m venv .pkg-smoke
	. .pkg-smoke/bin/activate && \
		python -m pip install --upgrade pip && \
		WHEEL=$$(ls -t dist/desloppify_ts-*.whl | head -n 1) && \
		python -m pip install "$$WHEEL[full]" && \
		python -c "from importlib.resources import files; from pathlib import Path; docs=Path('docs'); bundled=files('desloppify.data.global'); names=sorted(p.name for p in docs.glob('*.md')); assert names; missing=[name for name in names if not bundled.joinpath(name).is_file()]; assert not missing, f'missing bundled docs: {missing}'; mismatched=[name for name in names if bundled.joinpath(name).read_text(encoding='utf-8') != (docs / name).read_text(encoding='utf-8')]; assert not mismatched, f'mismatched bundled docs: {mismatched}'" && \
		python -c "import importlib.metadata as m,sys; extras=set(m.metadata('desloppify-ts').get_all('Provides-Extra') or []); required={'full','treesitter','scorecard'}; missing=required-extras; print('missing extras metadata:', sorted(missing)) if missing else None; sys.exit(1 if missing else 0)" && \
		desloppify --help > /dev/null && \
		desloppify --version | head -n 1 | grep -q '^desloppify [0-9]' && \
		desloppify-ts --version > /dev/null
	rm -rf .pkg-smoke

ci-fast: lint typecheck arch ci-contracts tests

ci: ci-fast tests-full tests-golden-node package-smoke
