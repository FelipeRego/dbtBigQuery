{#
    Partitioning, made conditional — and off by default.

    Why
    ---
    BigQuery's free sandbox (a project with no billing account attached) forces
    a 60-day expiration on every partition and does not let you raise it. The
    GA4 obfuscated sample covers 2020-11-01 to 2021-01-31. Partition a model on
    `event_date` in a sandbox and every partition you write is already older
    than 60 days, so BigQuery accepts the CREATE TABLE, reports success, scans
    the full source — and leaves you with an empty table.

    That failure is silent in the worst possible way: `dbt build` exits 0, and
    every not_null, unique, accepted_values and relationships test passes,
    because a test that finds no failing rows passes whether the table is clean
    or empty. This repo learned that the hard way; `tests/assert_models_are_not_empty.sql`
    exists so it cannot happen twice.

    So partitioning ships OFF, and the project works for anyone who clones it
    into a free sandbox. Clustering stays on either way: it needs no billing
    account and does most of the pruning work at this data volume.

    Turning it on
    -------------
    Attach a billing account to the project (the free tier still gives you 1 TiB
    of query processing a month, and a full build of this project bills about
    6.75 GiB), then either set the var permanently in dbt_project.yml or run:

        dbt build --vars 'enable_partitioning: true'
#}

{% macro ga4_partition_by(field) %}
    {% if var('enable_partitioning', false) %}
        {{ return({'field': field, 'data_type': 'date'}) }}
    {% else %}
        {{ return(none) }}
    {% endif %}
{% endmacro %}
