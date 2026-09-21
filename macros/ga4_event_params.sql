{#
    GA4's BigQuery export stores per-event key/value pairs in a repeated STRUCT
    called `event_params`. The value is a union: exactly one of string_value,
    int_value, float_value or double_value is populated per key.

    These macros keep the unnesting in one place so staging reads as a plain
    column list. The `limit 1` is load-bearing: GA4 does not guarantee a key
    appears at most once in a single event, and without it a duplicated key
    would raise "Scalar subquery produced more than one element" mid-run.
#}

{% macro ga4_param_string(key) -%}
    (select ep.value.string_value from unnest(event_params) ep where ep.key = '{{ key }}' limit 1)
{%- endmacro %}


{% macro ga4_param_int(key) -%}
    (select ep.value.int_value from unnest(event_params) ep where ep.key = '{{ key }}' limit 1)
{%- endmacro %}


{% macro ga4_param_float(key) -%}
    (select coalesce(ep.value.float_value, ep.value.double_value)
     from unnest(event_params) ep where ep.key = '{{ key }}' limit 1)
{%- endmacro %}
