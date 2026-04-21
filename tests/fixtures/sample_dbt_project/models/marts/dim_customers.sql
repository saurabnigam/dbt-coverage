{{ config(materialized='table') }}

-- P001: intentional cross join without ON / WHERE filter
select
    c.customer_id,
    c.first_name,
    c.last_name,
    c.email,
    c.created_at,
    o.order_id
from {{ ref('stg_customers') }} c
cross join {{ ref('stg_orders') }} o
