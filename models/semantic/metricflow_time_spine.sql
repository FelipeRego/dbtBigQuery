{{ config(materialized='table') }}

/*
    A dense calendar, one row per day. MetricFlow joins metrics onto it so that
    a day with no sessions appears as a zero rather than vanishing from the
    series — which matters most for exactly the metrics people watch for
    trouble, because a broken tag looks like a missing point, not a drop.

    The range is deliberately wider than the loaded GA4 window and fixed rather
    than derived from the project vars. A spine that shrinks when someone
    narrows `ga4_start_date` for a cheap dev run would silently truncate every
    metric they then queried.
*/

select date_day
from unnest(
    generate_date_array(date '2019-01-01', date '2026-12-31', interval 1 day)
) as date_day
