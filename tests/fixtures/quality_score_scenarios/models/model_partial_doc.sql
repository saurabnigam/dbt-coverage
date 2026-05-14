-- SCENARIO: model_partial_doc (expected score = 92)
--
-- Has:
--   ✔ Description on the model itself
--   ✗ Only 1 of 4 columns is documented (25% doc coverage)
--   ✔ A logical test declared
--
-- doc_ratio = 1/4 = 0.25  (model description contributes; exact ratio depends
--                           on what compute_doc_coverage counts as denominator)
--
-- NOTE: The doc penalty formula in orchestrator.py uses the model-level
--       doc_per_node tuple from compute_doc_coverage, which is (1,1) when the
--       model has a description (doc_coverage is per-model, not per-column).
--       So the score remains 100 here — column descriptions don't affect the
--       quality score directly.
--
-- Penalty breakdown: no_test=0, doc=0 (model has description), tier1=0, tier2=0
-- Final score = 100
--
-- To get a partial doc penalty in the quality score, the model itself must lack
-- a description. Column-level documentation does not influence the doc dimension
-- of the quality score.

{{ config(materialized='view') }}

select
    payment_id,
    order_id,
    amount,
    payment_method
from {{ ref('raw_payments') }}
