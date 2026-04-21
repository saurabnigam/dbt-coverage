# SPEC-06 — SQL Parser + Error Recovery

**Status:** draft
**Depends on:** SPEC-01, SPEC-05
**Blocks:** SPEC-07, SPEC-18

---

## 1. Purpose

Take a `ParsedNode` with `rendered_sql` already populated (by SPEC-05) and populate its `ast` field using **sqlglot**. Handle parse failures gracefully — never crash the scan.

---

## 2. Non-goals

- No SQL execution.
- No dialect auto-detection beyond the fallback ladder.
- No AST manipulation (that's SPEC-18 / rule implementations).
- No lineage resolution (SPEC-15/18).

---

## 3. Module layout

```
src/dbt_coverage/parsers/
  sql_parser.py                  # parse() entrypoint
  sqlglot_dialects.py           # dialect validation helpers
```

---

## 4. API Surface

### 4.1 `sql_parser.py`

```python
import sqlglot
from sqlglot.expressions import Expression
from dbt_coverage.core import ParsedNode

class SqlParser:
    def __init__(self, dialect: str):
        """dialect: sqlglot-valid name from SPEC-02 dialect resolution."""

    def parse(self, node: ParsedNode) -> ParsedNode:
        """
        Mutates (or returns a copy of) `node` with:
          - ast: Expression | None
          - parse_success: bool
          - parse_error: str | None   (set iff parse_success=False)
        Never raises.
        """

    def parse_all(self, nodes: list[ParsedNode]) -> list[ParsedNode]:
        """Convenience batch wrapper. Sequential — sqlglot is CPU-bound but fast."""
```

### 4.2 Error recovery ladder

On `sqlglot.errors.ParseError` from the first attempt:

1. **Attempt 1** — `sqlglot.parse_one(rendered_sql, read=dialect)`.
2. **Attempt 2** (fallback) — `sqlglot.parse_one(rendered_sql, read=None)` (dialect-free).
3. **Attempt 3** (sanitize) — strip known problem tokens:
   - Unresolved `__MACRO_*__` identifiers at statement positions → replace with `SELECT 1` placeholder.
   - Trailing incomplete CTEs (`WITH x AS (` with no close) → truncate to last complete statement.
   - Retry with original dialect.
4. **Give up** — `ast=None`, `parse_success=False`, `parse_error = <attempt_1 error message>` (not the sanitized retry, so the user sees the real problem).

Every failure chain is reported once per node — no retry loops.

### 4.3 `sqlglot_dialects.py`

```python
_VALID_DIALECTS = {
    "snowflake", "bigquery", "postgres", "redshift",
    "databricks", "spark", "duckdb", "trino", "athena", "mysql", "tsql", "oracle",
}

def validate_dialect(dialect: str) -> str:
    """Raise ConfigError if not in _VALID_DIALECTS. Case-insensitive, normalized to lower."""
```

---

## 5. Edge cases

| Case | Expected behavior |
|---|---|
| Clean SQL parses in attempt 1 | `ast` populated, `parse_success=True` |
| Dialect-specific syntax (e.g. Snowflake `QUALIFY`) with wrong dialect | Attempt 1 fails, attempt 2 may succeed; set `parse_success=True`, log warning |
| Totally invalid SQL (`SELECT FROM WHERE`) | All 3 attempts fail; `parse_success=False`, error recorded |
| Empty `rendered_sql` | `parse_success=False`, `parse_error="empty input"` |
| `rendered_sql` is only comments | sqlglot may parse as empty program; `parse_success=True`, `ast` is an empty `Expression` node |
| Multiple statements (`SELECT ...; SELECT ...;`) | `parse_one` returns first; add warning "multiple statements detected, only first parsed" |
| Node already has `ast` set (re-parse) | Overwrite |
| `render_uncertain=True` upstream | Still attempt to parse; uncertainty flag is independent of parse outcome |
| sqlglot raises non-ParseError (e.g. `RecursionError` on pathological input) | Catch broad `Exception`, set `parse_success=False` with error message |
| `dialect` string not in sqlglot's dialect registry | Caught at `SqlParser.__init__` via `validate_dialect` → `ConfigError` |

---

## 6. Test plan (`tests/unit/parsers/`)

### 6.1 `test_sql_parser.py`
- `SELECT * FROM __REF_orders__` (snowflake) → parses, `ast` is `exp.Select`, one table ref.
- `SELECT x FROM t QUALIFY row_number() OVER () = 1` with `dialect="postgres"` → attempt 1 fails, attempt 2 succeeds, `parse_success=True`.
- `SELECT FROM WHERE` → all attempts fail; `parse_success=False`, error message contains "parse" (case-insensitive).
- `WITH x AS (SELECT 1` (unclosed) → attempt 3 sanitizer truncates, parses as empty or `SELECT 1`; `parse_success=True` with a warning — acceptable (we don't want rules to run on half-parsed nodes, but we also don't want to fail the whole scan).
  - **Decision:** sanitizer attempt returns with `parse_success=False` if the truncated version loses >50% of source — test enforces this.
- `rendered_sql=""` → `parse_success=False`.
- Node with `ast` pre-populated → overwritten on re-parse.
- Pathological nested subquery input causes RecursionError → caught; `parse_success=False`.

### 6.2 `test_sqlglot_dialects.py`
- Each key in `_VALID_DIALECTS` validates.
- `"mssql"` is not in our set (users should pass `"tsql"`); raises `ConfigError` with hint message.
- Case insensitivity: `"Snowflake"` normalizes to `"snowflake"`.

### 6.3 Integration (`tests/integration/test_parser_pipeline.py`)
- Full pipeline: `IndexedFile` → `JinjaRenderer.render()` → `SqlParser.parse()` → verify `ast is not None` and `refs` extractable from AST match those captured during Jinja render.

**Coverage target:** 95%+.

---

## 7. Acceptance criteria

- [ ] 100% of `examples/basic_project/models/*.sql` parse with `parse_success=True` after the 3-attempt ladder
- [ ] At least one fixture with known-bad SQL parses with `parse_success=False` and doesn't crash
- [ ] `ruff`, `mypy --strict` clean
- [ ] `pytest tests/unit/parsers/test_sql_parser.py` ≥95% coverage
- [ ] `sqlglot` is the only new runtime dep introduced
- [ ] No call to `sqlglot.parse_one` outside this module — enforced by lint rule: `grep -r "sqlglot.parse" src/dbt_coverage/` returns only `parsers/sql_parser.py`

---

## 8. Open questions

- Should we retain intermediate parse errors (from attempts 2 and 3) for debugging? **Proposal:** no, retain only attempt 1 error — attempts 2-3 are best-effort recovery, their errors are noise.
- Do we need to support SQL with multiple statements? **Proposal:** phase-1 no, warn if detected; phase-2 could split and parse each. Singular tests may have multi-statement files but they're out of AST-rule scope in v1.
