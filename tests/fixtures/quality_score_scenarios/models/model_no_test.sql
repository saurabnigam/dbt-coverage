-- SCENARIO: model_no_test (expected score = 75)
--
-- Has:
--   ✔ Description on the model
--   ✔ Description on columns
--   ✗ No tests declared anywhere
--
-- Penalty breakdown: no_test=25, doc=0, tier1=0, tier2=0
-- Final score = 100 - 25 = 75

{{ config(materialized='view') }}

select
    customer_id,
    first_name,
    last_name,
    signup_date
from {{ ref('raw_customers') }}
