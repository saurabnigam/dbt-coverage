-- SCENARIO: model_no_test_no_doc (expected score = 60)
--
-- Has:
--   ✗ No description (yml block has no description)
--   ✗ No tests declared
--
-- Penalty breakdown: no_test=25, doc=15, tier1=0, tier2=0
-- Final score = 100 - 25 - 15 = 60

{{ config(materialized='view') }}

select
    session_id,
    user_id,
    page_url,
    event_ts
from {{ ref('raw_events') }}
