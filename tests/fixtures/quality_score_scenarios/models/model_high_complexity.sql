-- SCENARIO: model_high_complexity (expected score = 94)
--
-- This model has deeply nested CTEs and many branches, giving it a high
-- cyclomatic complexity (CC = 17, above the default threshold_warn = 15).
--
-- Active rule findings:
--   Q001: SELECT * in the enriched CTE → tier2 penalty = 3
--   Q003: CC=17 >= warn threshold 15 → tier2 penalty = 3
--
-- Penalty breakdown: no_test=0, doc=0, tier2=6 (Q001 + Q003 each × 3)
-- Final score = 100 - 6 = 94
--
-- complexity_coverage: NOT covered (CC 17 > threshold_warn 15)

{{ config(materialized='view') }}

with base as (
    select order_id, customer_id, amount, status, region, channel
    from {{ ref('raw_orders') }}
),
enriched as (
    select
        b.*,
        case
            when b.status = 'pending'    then 'OPEN'
            when b.status = 'shipped'   then 'IN_TRANSIT'
            when b.status = 'delivered' then 'CLOSED'
            when b.status = 'returned'  then 'REFUNDED'
            when b.status = 'cancelled' then 'VOID'
            else 'UNKNOWN'
        end as status_label,
        case
            when b.region = 'EMEA'   and b.channel = 'online' then 'EU_WEB'
            when b.region = 'EMEA'   and b.channel = 'store'  then 'EU_STORE'
            when b.region = 'APAC'   and b.channel = 'online' then 'APAC_WEB'
            when b.region = 'APAC'   and b.channel = 'store'  then 'APAC_STORE'
            when b.region = 'AMER'   and b.channel = 'online' then 'US_WEB'
            when b.region = 'AMER'   and b.channel = 'store'  then 'US_STORE'
            else 'OTHER'
        end as segment,
        case
            when b.amount < 50    then 'micro'
            when b.amount < 200   then 'small'
            when b.amount < 1000  then 'medium'
            when b.amount < 5000  then 'large'
            else 'enterprise'
        end as order_tier
    from base b
)
select *
from enriched
where status_label != 'VOID'
  and amount > 0
