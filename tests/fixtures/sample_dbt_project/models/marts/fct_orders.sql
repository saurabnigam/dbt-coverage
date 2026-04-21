{{ config(materialized='table') }}

select
    order_id,
    customer_id,
    order_date,
    amount
from {{ ref('stg_orders') }}
