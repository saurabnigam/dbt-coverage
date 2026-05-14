-- SCENARIO: model_trivial_tests_only (expected score = 100)
--
-- Has:
--   ✔ Description on the model
--   ✔ Tests declared: only not_null and unique (both classified as TRIVIAL)
--
-- test_coverage (YAML-based): covered=1  → no_test_penalty = 0
-- test_meaningful_coverage:   covered=0  → but this dimension only affects
--                                          separate coverage metric, NOT the
--                                          quality score directly.
--
-- Penalty breakdown: no_test=0 (YAML has tests), doc=0, tier1=0, tier2=0
-- Final score = 100
--
-- NOTE: Run `dbtcov scan --coverage` to see test_meaningful=0% separately.

{{ config(materialized='view') }}

select
    inventory_id,
    product_id,
    warehouse_id,
    qty_on_hand
from {{ ref('raw_inventory') }}
