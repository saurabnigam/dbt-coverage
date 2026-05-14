"""Human-readable metadata for rules, dimensions, and config knobs.

Drives the configuration UI tooltips so every option has a meaning attached.
"""

from __future__ import annotations

# ---- rule descriptions -----------------------------------------------------

RULE_DESCRIPTIONS: dict[str, dict[str, str]] = {
    # Quality
    "Q001": {"name": "SELECT * usage", "category": "QUALITY",
             "desc": "Flags `SELECT *` in models or CTEs. List columns explicitly."},
    "Q002": {"name": "PK column missing unique test", "category": "QUALITY",
             "desc": "A column tagged as primary key has no `unique` test in schema.yml."},
    "Q003": {"name": "High cyclomatic complexity", "category": "QUALITY",
             "desc": "Model exceeds the configured complexity threshold (UNION arms + control flow)."},
    "Q004": {"name": "Missing model description", "category": "QUALITY",
             "desc": "Model has no `description:` entry in schema.yml."},
    "Q005": {"name": "Undocumented column", "category": "QUALITY",
             "desc": "A projected column is missing from schema.yml `columns:`. Capped per model to reduce noise."},
    "Q006": {"name": "Source referenced from non-staging", "category": "QUALITY",
             "desc": "`source()` calls outside the staging layer break layering."},
    "Q007": {"name": "Hard-coded literal in business logic", "category": "QUALITY",
             "desc": "Magic numbers or strings embedded in CASE/WHERE; promote to a `var()` or seed."},
    # Performance
    "P001": {"name": "Cross join", "category": "PERFORMANCE",
             "desc": "Cartesian / unbounded CROSS JOIN. Confirm intent and add a join condition."},
    "P002": {"name": "Non-sargable predicate", "category": "PERFORMANCE",
             "desc": "Function applied to a column on the predicate side prevents index/clustering use."},
    "P003": {"name": "Implicit type cast in join key", "category": "PERFORMANCE",
             "desc": "Join columns of mismatched types — engine casts every row, defeating partition pruning."},
    "P004": {"name": "Unbounded window frame", "category": "PERFORMANCE",
             "desc": "ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING — O(N) per row."},
    "P005": {"name": "ORDER BY in subquery", "category": "PERFORMANCE",
             "desc": "ORDER BY in a derived table is wasted unless paired with LIMIT."},
    "P006": {"name": "DISTINCT to dedupe joins", "category": "PERFORMANCE",
             "desc": "DISTINCT used to clean up join fan-out; fix the join cardinality instead."},
    "P007": {"name": "Subquery in SELECT", "category": "PERFORMANCE",
             "desc": "Correlated scalar subquery — refactor to a JOIN or window function."},
    "P008": {"name": "Excessive CTE nesting", "category": "PERFORMANCE",
             "desc": "Deep CTE chains break some optimizers; configurable `max_depth` (default 8)."},
    "P009": {"name": "Repeated CASE expressions", "category": "PERFORMANCE",
             "desc": "Same CASE expression evaluated many times — extract to a helper CTE."},
    "P010": {"name": "Full table scan in incremental model", "category": "PERFORMANCE",
             "desc": "Incremental model missing `is_incremental()` filter on the source — full reload every run."},
    # Refactor
    "R001": {"name": "Near-duplicate models", "category": "REFACTOR",
             "desc": "Two models with ≥85% similar AST/text. Likely copy-paste."},
    "R002": {"name": "Dead column", "category": "REFACTOR",
             "desc": "Column projected by a model but never referenced downstream."},
    "R003": {"name": "Excessive column count", "category": "REFACTOR",
             "desc": "Model projects > N columns; consider splitting or narrowing."},
    "R004": {"name": "CTE used once", "category": "REFACTOR",
             "desc": "CTE referenced exactly once — inline it for clarity."},
    "R005": {"name": "Repeated literal", "category": "REFACTOR",
             "desc": "Same literal appears ≥ `min_occurrences` times — extract to a `var()`."},
    "R006": {"name": "Repeated expression", "category": "REFACTOR",
             "desc": "Same expression repeated; promote to a CTE column."},
    # Architecture
    "A001": {"name": "Layer violation", "category": "ARCHITECTURE",
             "desc": "Edge between layers not in `architecture.allowed_edges`."},
    "A002": {"name": "Fan-in too high", "category": "ARCHITECTURE",
             "desc": "Model is referenced by more than `threshold` downstream models."},
    "A003": {"name": "Source skipped staging", "category": "ARCHITECTURE",
             "desc": "Mart references a `source()` directly without a staging model."},
    "A004": {"name": "Cycle in DAG", "category": "ARCHITECTURE",
             "desc": "Cyclic ref() between models — dbt would reject this."},
    "A005": {"name": "Orphan model", "category": "ARCHITECTURE",
             "desc": "Model with no downstream consumers and no exposure."},
    # Testing
    "T001": {"name": "Test defined but not executed", "category": "TESTING",
             "desc": "Test in manifest with no run_results.json entry. Attach `target/run_results.json`."},
    "T002": {"name": "Model has no unit tests", "category": "TESTING",
             "desc": "Model has zero unit_tests blocks. Auto-suppressed when dbt < 1.8 or version unknown."},
    "T003": {"name": "Malformed unit test", "category": "TESTING",
             "desc": "Unit test missing `given`/`expect` block or has empty `expect.rows`."},
    # Security / Governance
    "S001": {"name": "PII in projection", "category": "SECURITY",
             "desc": "Column whose name matches a PII pattern (email, ssn, …) is projected unmasked."},
    "S002": {"name": "Hard-coded secret", "category": "SECURITY",
             "desc": "Token / password literal embedded in SQL."},
    "G001": {"name": "Missing owner / domain tag", "category": "GOVERNANCE",
             "desc": "Model lacks `meta.owner` or `meta.domain` for accountability."},
}


# ---- coverage dimension descriptions ---------------------------------------

DIMENSION_DESCRIPTIONS: dict[str, dict[str, str]] = {
    "test": {
        "name": "Model Data Test Coverage",
        "desc": "Fraction of models with at least one declared data test (any kind).",
    },
    "test_meaningful": {
        "name": "Model Meaningful Test Coverage",
        "desc": "Fraction of models with at least one PASSING logical-weight test (excludes trivial not_null/unique).",
    },
    "test_weighted_cc": {
        "name": "Complexity-Weighted Test Coverage",
        "desc": "Data test coverage weighted by cyclomatic complexity — high-complexity models contribute more to the denominator.",
    },
    "test_unit": {
        "name": "Model Unit Test Coverage",
        "desc": "Fraction of models that have at least one dbt unit_tests block (requires dbt 1.8+).",
    },
    "column_test": {
        "name": "Columnar Data Test Coverage",
        "desc": "Fraction of declared YAML columns that have at least one data test. Models with no columns declared are excluded.",
    },
    # backward-compat alias for scans stored before the rename
    "test_column": {
        "name": "Columnar Data Test Coverage",
        "desc": "Fraction of declared YAML columns that have at least one data test. Models with no columns declared are excluded.",
    },
    "column_test_meaningful": {
        "name": "Columnar Meaningful Test Coverage",
        "desc": "Fraction of declared YAML columns with at least one passing logical-weight test (excludes trivial not_null/unique).",
    },
    # backward-compat alias
    "test_column_meaningful": {
        "name": "Columnar Meaningful Test Coverage",
        "desc": "Fraction of declared YAML columns with at least one passing logical-weight test (excludes trivial not_null/unique).",
    },
    "test_unit_weighted_cc": {
        "name": "Unit CC-Weighted Coverage",
        "desc": "Unit test coverage weighted by cyclomatic complexity — complex models without unit tests have greater impact.",
    },
    "doc": {
        "name": "Model Documentation Coverage",
        "desc": "Fraction of declared model columns that have a non-empty description in schema.yml.",
    },
    "complexity": {
        "name": "Model Complexity Health",
        "desc": "Fraction of models whose cyclomatic complexity is below the warn threshold. Lower complexity = healthier.",
    },
}


# ---- column header tooltips ------------------------------------------------

COLUMN_TOOLTIPS: dict[str, str] = {
    "model": "Model name and file path. Click a row to open the full detail panel.",
    "score": (
        "Quality score 0–100. Starts at 100 and deducts points for column test gaps "
        "(up to -25), meaningful column gaps (up to -10), unit CC-weighted gaps (up to -10), "
        "doc coverage gaps (up to -15), Tier-1 rule findings (-10 each, capped -40), "
        "Tier-2 findings (-3 each, capped -20), unexecuted tests (-5 each, capped -15), "
        "parse failure (-10) or uncertain parse (-5), and skipped checks (-1 each, capped -5). "
        "All weights are configurable under 'Score Weights' in the config editor."
    ),
    "findings": (
        "Number of rule violations on this model: Tier-1 (blocking, e.g. Q001/P001) "
        "and Tier-2 (warnings). Click the row to see the full finding list."
    ),
    "tests": "Total data tests declared in schema.yml (not_null, unique, relationships, singular SQL, etc.).",
    "unit": (
        "Unit tests defined with the dbt unit_tests block (dbt 1.8+). "
        "Unit tests mock upstream refs and assert on output rows — "
        "stricter than data tests."
    ),
    "parse": (
        "SQL parse status: ✓ full AST parsed, ~ uncertain (Jinja unresolved, "
        "AST-level rules skipped), ✗ parse failed."
    ),
}


# ---- top-level config knobs ------------------------------------------------

CONFIG_FIELDS: dict[str, dict[str, str]] = {
    "render.mode": {
        "name": "Render mode",
        "desc": "AUTO picks COMPILED when target/compiled is present and covers ≥ compiled_min_coverage of models, else MOCK.",
        "options": "AUTO | COMPILED | MOCK | PARTIAL",
    },
    "render.compiled_min_coverage": {
        "name": "Minimum compiled coverage",
        "desc": "Hit-ratio (0–1) of compiled SQL files required before AUTO selects COMPILED.",
    },
    "dialect": {
        "name": "SQL dialect",
        "desc": "sqlglot dialect used to parse models. Common: snowflake, bigquery, postgres, redshift, databricks.",
    },
    "confidence_threshold": {
        "name": "Confidence threshold",
        "desc": "Findings below this confidence are dropped. 0.7 = report only fairly-certain matches.",
    },
    "complexity.threshold_warn": {
        "name": "Complexity warn",
        "desc": "Above this CC value the model contributes a Q003 MAJOR finding.",
    },
    "complexity.threshold_block": {
        "name": "Complexity block",
        "desc": "Above this CC value Q003 escalates to CRITICAL.",
    },
    # ---- scoring weights ---------------------------------------------------
    "scoring.no_test_penalty": {
        "name": "No-test penalty",
        "desc": "Points deducted when a model has zero declared tests. Default: 25.",
    },
    "scoring.doc_penalty_max": {
        "name": "Doc penalty (max)",
        "desc": "Maximum points deducted for missing documentation (scales with % undocumented). Default: 15.",
    },
    "scoring.tier1_per_finding": {
        "name": "Tier-1 penalty per finding",
        "desc": "Points deducted for each TIER_1_ENFORCED finding on the model. Default: 10.",
    },
    "scoring.tier1_cap": {
        "name": "Tier-1 penalty cap",
        "desc": "Maximum total penalty from Tier-1 findings. Default: 40.",
    },
    "scoring.tier2_per_finding": {
        "name": "Tier-2 penalty per finding",
        "desc": "Points deducted for each TIER_2_WARN finding on the model. Default: 3.",
    },
    "scoring.tier2_cap": {
        "name": "Tier-2 penalty cap",
        "desc": "Maximum total penalty from Tier-2 findings. Default: 20.",
    },
    "scoring.unexec_per_test": {
        "name": "Unexecuted test penalty",
        "desc": "Points deducted per test that was declared but not executed. Default: 5.",
    },
    "scoring.unexec_cap": {
        "name": "Unexecuted test cap",
        "desc": "Maximum total penalty from unexecuted tests. Default: 15.",
    },
    "scoring.parse_fail_penalty": {
        "name": "Parse-fail penalty",
        "desc": "Points deducted when SQL parsing fails entirely. Default: 10.",
    },
    "scoring.parse_uncertain_penalty": {
        "name": "Parse-uncertain penalty",
        "desc": "Points deducted when parse is uncertain (Jinja unresolved). Default: 5.",
    },
    "scoring.skip_cap": {
        "name": "Skip-check cap",
        "desc": "Maximum total penalty from skipped rule checks. Default: 5.",
    },
}


def rule_meta(rule_id: str) -> dict[str, str]:
    return RULE_DESCRIPTIONS.get(
        rule_id,
        {"name": rule_id, "category": "UNKNOWN", "desc": "No description available."},
    )
