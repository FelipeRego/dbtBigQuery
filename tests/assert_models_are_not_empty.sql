/*
    Fail the build if any model came out empty.

    This test exists because of a real failure, not a hypothetical one. Building
    this project into a free BigQuery sandbox with `partition_by` set on
    `event_date` produced six empty tables: the sandbox forces a 60-day
    partition expiry, every partition of a 2020-21 dataset is born expired, and
    BigQuery reports the CREATE TABLE as successful.

    dbt exited 0. All 105 tests passed. not_null passes on an empty table.
    unique passes on an empty table. accepted_values, relationships and every
    expression_is_true assertion pass on an empty table, because each one asks
    "how many rows break this rule?" and the answer is always zero when there
    are no rows.

    Row-count assertions are the only kind that cannot pass vacuously, which is
    why every project needs at least one.
*/

with row_counts as (

    select 'stg_events'             as model_name, count(*) as row_count from {{ ref('stg_events') }}
    union all
    select 'int_sessions',          count(*) from {{ ref('int_sessions') }}
    union all
    select 'fct_events',            count(*) from {{ ref('fct_events') }}
    union all
    select 'dim_users',             count(*) from {{ ref('dim_users') }}
    union all
    select 'fct_sessions',          count(*) from {{ ref('fct_sessions') }}
    union all
    select 'fct_funnel',            count(*) from {{ ref('fct_funnel') }}
    union all
    select 'feature_catalogue',     count(*) from {{ ref('feature_catalogue') }}
    union all
    select 'metricflow_time_spine', count(*) from {{ ref('metricflow_time_spine') }}

)

select *
from row_counts
where row_count = 0
