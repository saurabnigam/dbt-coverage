# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |

## Reporting a Vulnerability

**Please do not report security vulnerabilities through public GitHub issues.**

If you discover a security vulnerability in `dbt-coverage-lib`, please disclose it responsibly via a [GitHub Security Advisory](https://github.com/dbtcov/dbt-coverage-lib/security/advisories/new) (Settings → Security → Advisories → "Report a vulnerability").

Include as much of the following as possible:

- A description of the vulnerability and its potential impact
- Steps to reproduce or a minimal proof-of-concept
- Affected version(s)
- Any suggested mitigations you are aware of

**Response SLA**: We aim to acknowledge receipt within **72 hours** and to provide an initial assessment within **7 days**. Critical issues will be patched and released as soon as possible.

Once the vulnerability is confirmed and a fix is ready, we will:

1. Prepare a patched release.
2. Publish a GitHub Security Advisory with full details and CVE (if applicable).
3. Credit the reporter (unless they prefer anonymity).

## Scope

This policy covers the `dbt-coverage-lib` Python package and its CLI (`dbtcov`). It does **not** cover:

- Third-party dependencies (please report those upstream).
- Vulnerabilities in dbt itself or the dbt adapter ecosystem.
- The optional FastAPI UI (`dbt_coverage_ui`) when deployed without authentication behind a public endpoint — the UI is intended for internal/local use only.

## Security-relevant design notes

- `dbtcov` reads dbt project files and SQL locally; it does **not** connect to any database or external service.
- SARIF output files may contain model names and SQL fragments — treat them as internal artefacts.
- The `dbt_coverage_ui` FastAPI server has no built-in authentication. Do not expose it on a public network without an authenticating reverse proxy.
