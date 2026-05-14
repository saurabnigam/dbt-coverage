-- SCENARIO: model_no_doc (expected score = 85)
--
-- Has:
--   ✗ No description on the model (no yml entry)
--   ✔ Tests declared (not_null + unique on primary key)
--
-- Penalty breakdown: no_test=0, doc=15 (0% documented), tier1=0, tier2=0
-- Final score = 100 - 15 = 85

{{ config(materialized='view') }}

select
    product_id,
    name,
    price,
    category
from {{ ref('raw_products') }}
