{{ config(materialized='view') }}

-- Q001: SELECT * should trigger in views off a source
select *
from {{ source('raw', 'orders') }}
