-- SCENARIO: model_with_tier1_violations (expected score = 97)
--
-- Has:
--   ✔ Description on the model
--   ✔ Tests declared
--   ✗ Intentional SQL patterns that trigger rules:
--       - SELECT * in final projection (triggers Q001 — tier2)
--
-- Penalty breakdown: no_test=0, doc=0, tier2=3 (Q001 × 3)
-- Note: Q001 is tier-2 by default (not tier-1), so penalty = 3 per unique rule ID
-- Final score = 100 - 20 = 80
--
-- Actual rules fired depend on which rules are enabled in dbtcov.yml.
-- With default rules, run: dbtcov scan --project-dir . to see which fire.

{{ config(materialized='view') }}

-- SELECT * is a common tier1 pattern (rule R001 in many configs)
select *
from {{ ref('raw_orders') }}
