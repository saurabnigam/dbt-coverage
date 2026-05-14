# Contributing to dbt-coverage-lib

Thank you for your interest in contributing! This document covers everything you need to get from zero to a merged pull request.

## Table of contents

- [Code of conduct](#code-of-conduct)
- [Getting started](#getting-started)
- [Development workflow](#development-workflow)
- [Running tests](#running-tests)
- [Linting and type-checking](#linting-and-type-checking)
- [Branch naming and commit style](#branch-naming-and-commit-style)
- [Pull request checklist](#pull-request-checklist)
- [Adding a new rule](#adding-a-new-rule)
- [Release process](#release-process)
- [Getting help](#getting-help)

---

## Code of conduct

This project follows the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md). By participating you agree to abide by its terms.

---

## Getting started

### Prerequisites

- Python ≥ 3.11
- `git`
- (optional) `docker` for container-based testing

### Fork and clone

```bash
git clone https://github.com/<your-fork>/dbt-coverage-lib.git
cd dbt-coverage-lib
```

### Install in editable mode

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

This installs `dbtcov` on your `PATH` plus all development tools (`pytest`, `ruff`, `mypy`).

### Verify your setup

```bash
dbtcov --version
pytest tests/unit -q
```

---

## Development workflow

```
main          ← protected; releases only
develop       ← integration branch; PRs target this branch
feat/<name>   ← feature branches
fix/<name>    ← bug-fix branches
docs/<name>   ← documentation-only branches
```

1. Branch off `develop`:
   ```bash
   git checkout develop && git pull origin develop
   git checkout -b feat/my-feature
   ```
2. Make your changes.
3. Add or update tests (see below).
4. Update `CHANGELOG.md` under `[Unreleased]`.
5. Open a PR against `develop`.

---

## Running tests

```bash
# All tests (fast)
pytest

# Unit tests only
pytest tests/unit -q

# Integration tests (requires the sample project fixture)
pytest tests/integration -q

# With coverage report
pytest --cov=dbt_coverage --cov-report=term-missing

# Include slow tests
pytest --runslow

# Regenerate golden files after intentional output changes
UPDATE_GOLDENS=1 pytest tests/integration
```

Markers in use:

| Marker | Meaning |
|---|---|
| `slow` | Tests > 500ms — skipped by default |
| `integration` | End-to-end tests touching the sample project |
| `requires_dbt_core` | Tests needing the `[dbt-core]` optional extra |

---

## Linting and type-checking

```bash
# Lint (auto-fix safe issues)
ruff check src tests --fix

# Type-check
mypy src/dbt_coverage
```

Both must pass with zero errors before a PR can be merged. CI enforces this automatically.

---

## Branch naming and commit style

We use **Conventional Commits** (`<type>(<scope>): <subject>`):

| Type | When to use |
|---|---|
| `feat` | New feature or rule |
| `fix` | Bug fix |
| `docs` | Documentation only |
| `refactor` | Code restructure without behaviour change |
| `test` | Adding or fixing tests |
| `chore` | Build scripts, CI, dependencies |
| `perf` | Performance improvement |

Examples:
```
feat(rules): add A005 leaky-abstraction rule
fix(coverage): correct weighted-cc denominator when model has 0 CTEs
docs(contributing): add release process section
```

Breaking changes must include `BREAKING CHANGE:` in the commit body or a `!` after the type: `feat!: rename CLI flag --out to --output-dir`.

---

## Pull request checklist

Before requesting review, make sure:

- [ ] The PR is linked to an issue (`Closes #<n>`) or has a clear rationale in the description.
- [ ] New behaviour is covered by at least one unit test.
- [ ] `pytest` passes locally.
- [ ] `ruff check src tests` reports zero violations.
- [ ] `mypy src/dbt_coverage` reports zero errors.
- [ ] `CHANGELOG.md` is updated under `[Unreleased]`.
- [ ] Public-facing CLI flags or config keys are documented in `README.md` or the relevant `docs/specs/` file.
- [ ] If you added a new rule, a SPEC file exists under `docs/specs/` (even a draft is fine).

---

## Adding a new rule

1. Pick the next rule ID in the relevant pack (Q, P, R, A, T, S, G).
2. Create or update the SPEC file in `docs/specs/`.
3. Implement the rule class in `src/dbt_coverage/` following the pattern of an existing rule in the same pack.
4. Register the rule in `src/dbt_coverage/core/registry.py` (or the relevant pack `__init__`).
5. Add unit tests in `tests/unit/` and, if it touches the sample project, integration tests in `tests/integration/`.
6. Update the rule table in `README.md`.

---

## Release process

Releases are managed by maintainers:

1. Merge all intended changes into `develop`.
2. Update `CHANGELOG.md`: promote `[Unreleased]` to the new version with today's date.
3. Bump `version` in `pyproject.toml` and `src/dbt_coverage/__init__.py`.
4. Open a PR from `develop` → `main`, title `chore(release): v<version>`.
5. After merge, create a GitHub Release tagged `v<version>`.
6. The `publish.yml` GitHub Actions workflow triggers automatically and publishes to PyPI.

---

## Getting help

- **General questions**: open a [GitHub Discussion](https://github.com/dbtcov/dbt-coverage-lib/discussions) in the Q&A category.
- **Bug reports**: open a [GitHub Issue](https://github.com/dbtcov/dbt-coverage-lib/issues/new/choose) using the Bug Report template.
- **Feature requests**: open a [GitHub Issue](https://github.com/dbtcov/dbt-coverage-lib/issues/new/choose) using the Feature Request template.
