# SPEC-30 — Security & Governance pack (S001, S002, G001, G003)

## 1. Scope

Security and governance rules. Introduces `Category.SECURITY` alongside the existing `Category.GOVERNANCE`.

## 2. Enum additions

`Category.SECURITY = "SECURITY"` added to [src/dbt_coverage/core/enums.py](../../src/dbt_coverage/core/enums.py). `FindingType.VULNERABILITY` already exists.

## 3. Rules

### S001 PII column unmasked

- **Category**: `SECURITY`, **Tier**: `TIER_1_ENFORCED`, **Severity**: `CRITICAL`, **FindingType**: `VULNERABILITY`.
- Inspects projection columns for patterns matching a configurable PII list.
- **Default patterns**: `ssn`, `social_security`, `tax_id`, `credit_card`, `cc_number`, `aadhar`, `passport`, `iban`, `dob`, `date_of_birth`, `email_address`.
- Fires unless the column has `meta.pii: true` in schema.yml **and** the expression routes through a macro whose name matches `mask_.*` / `hash_.*` / `redact_.*`.
- Param: `rules.S001.params.patterns`, `rules.S001.params.mask_macro_prefixes`.

### S002 hardcoded secret

- **Tier**: `TIER_1_ENFORCED`, **Severity**: `BLOCKER`, **FindingType**: `VULNERABILITY`.
- Regex scan of SQL string literals for:
  - AWS access keys: `AKIA[0-9A-Z]{16}`
  - GitHub tokens: `gh[pousr]_[A-Za-z0-9]{36}`
  - Stripe live keys: `sk_live_[A-Za-z0-9]{24,}`
  - JWT: `eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\..+`
  - Generic base64-ish high-entropy strings of length ≥ 40 (opt-in via param).

### G001 missing owner

- **Category**: `GOVERNANCE`, **Tier**: `TIER_2_WARN`, **Severity**: `MINOR`.
- Model lacks `meta.owner` *and* `meta.team` in its schema.yml entry.

### G003 waiver expired

- **Category**: `GOVERNANCE`, **Tier**: `TIER_1_ENFORCED`, **Severity**: `MAJOR`.
- **Not a scannable rule** — emitted by the `WaiverResolver` from [SPEC-31](SPEC-31-waivers-and-baseline.md) when a `dbtcov.yml` override entry's `expires` date has passed.
- Points at the dbtcov.yml override entry's location.
- Message: *"Waiver for {rules} on {target} expired on {expires}; please re-review."*

## 4. Tests

`tests/unit/analyzers/packs/security/test_s001.py`, `test_s002.py`, `test_g001.py`. `G003` is covered by the waiver tests in SPEC-31.
