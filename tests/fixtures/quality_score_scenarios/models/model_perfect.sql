-- SCENARIO: model_perfect (expected score = 100)
--
-- Has:
--   ✔ Description on the model itself
--   ✔ Description on every column
--   ✔ A logical test (singular) → test_meaningful covered
--   ✔ No violations of any kind
--
-- Penalty breakdown: no_test=0, doc=0, tier1=0, tier2=0, unexec=0, parse=0, skips=0
-- Final score = 100

{{ config(materialized='view') }}

select
    order_id,
    customer_id,
    amount,
    status,
    ordered_at
from {{ ref('raw_orders') }}
where amount > 0
