{{
    config(
        partition_by=ga4_partition_by('first_seen_date'),
        cluster_by=['acquisition_medium', 'first_device_category']
    )
}}

/*
    One row per user_pseudo_id. This model carries the cohort flags that the
    activation and retention metrics aggregate, and — just as importantly — the
    eligibility flags that say when a user is allowed to count.

    Why eligibility flags exist
    ---------------------------
    The loaded window ends on a fixed date. A user whose first session was three
    days before that date has had three days to activate, not seven. Including
    them in an activation rate drags it down for reasons that have nothing to do
    with the product, and the effect is strongest in the most recent cohorts,
    which is exactly where people look for a trend. `has_full_activation_window`
    and `has_full_d7_window` let the metrics exclude users whose observation
    window is still open. Every metric that uses them says so in its description.

    `is_left_censored` is the mirror image: GA4's own
    user_first_touch_timestamp shows the user existed before the window opened,
    so their "first session" here is an artefact of where we cut the data.
*/

with sessions as (

    select * from {{ ref('int_sessions') }}

),

events as (

    select * from {{ ref('stg_events') }}

),

window_bounds as (

    select
        parse_date('%Y%m%d', '{{ var("ga4_start_date") }}') as window_start_date,
        parse_date('%Y%m%d', '{{ var("ga4_end_date") }}')   as window_end_date

),

first_session_attributes as (

    -- Acquisition is taken from the user's first observed session. For a
    -- left-censored user this is a mid-life session, which is why those users
    -- are excluded from cohort metrics rather than silently mis-attributed.
    select
        user_pseudo_id,
        first_touch_source      as acquisition_source,
        first_touch_medium      as acquisition_medium,
        first_touch_campaign    as acquisition_campaign,
        device_category         as first_device_category,
        operating_system        as first_operating_system,
        country                 as first_country,
        landing_page            as first_landing_page
    from sessions
    qualify row_number() over (partition by user_pseudo_id order by session_started_at, session_key) = 1

),

session_rollup as (

    select
        user_pseudo_id,
        min(user_first_seen_at)                                   as first_seen_at,
        min(user_first_seen_date)                                 as first_seen_date,
        min(user_first_touch_at)                                  as first_touch_at,
        max(session_started_at)                                   as last_seen_at,
        date(max(session_started_at))                             as last_seen_date,
        max(is_left_censored)                                     as is_left_censored,

        count(*)                                                  as sessions,
        countif(is_engaged_session)                               as engaged_sessions,
        count(distinct session_date)                              as active_days,
        sum(events_in_session)                                    as events,
        sum(page_views)                                           as page_views,
        sum(session_duration_seconds)                             as total_session_seconds,

        countif(has_view_item)                                    as sessions_with_view_item,
        countif(has_add_to_cart)                                  as sessions_with_add_to_cart,
        countif(has_begin_checkout)                               as sessions_with_begin_checkout,
        countif(has_purchase)                                     as sessions_with_purchase,
        sum(transactions)                                         as transactions,
        sum(session_revenue_usd)                                  as lifetime_revenue_usd,

        -- Retention: did the user come back on a later day? Day 0 is excluded
        -- deliberately — returning within the acquisition day is not retention.
        countif(days_since_first_session = 1)              > 0     as is_retained_d1,
        countif(days_since_first_session between 1 and 7)  > 0     as is_retained_d7,
        countif(days_since_first_session between 1 and 30) > 0     as is_retained_d30

    from sessions
    group by user_pseudo_id

),

first_purchase as (

    select
        user_pseudo_id,
        min(event_at) as first_purchase_at
    from events
    where event_name = 'purchase'
    group by user_pseudo_id

),

feature_first_use as (

    select
        e.user_pseudo_id,
        count(distinct f.feature_name) as features_adopted
    from events e
    join {{ ref('feature_catalogue') }} f
      on e.event_name = f.event_name
     and f.is_user_initiated
    group by e.user_pseudo_id

),

final as (

    select
        r.user_pseudo_id,

        r.first_seen_at,
        r.first_seen_date,
        r.first_touch_at,
        r.last_seen_at,
        r.last_seen_date,
        date_diff(r.last_seen_date, r.first_seen_date, day)          as lifespan_days,

        a.acquisition_source,
        a.acquisition_medium,
        a.acquisition_campaign,
        a.first_device_category,
        a.first_operating_system,
        a.first_country,
        a.first_landing_page,

        r.sessions,
        r.engaged_sessions,
        r.active_days,
        r.events,
        r.page_views,
        r.total_session_seconds,

        r.sessions_with_view_item,
        r.sessions_with_add_to_cart,
        r.sessions_with_begin_checkout,
        r.sessions_with_purchase,
        r.transactions,
        r.lifetime_revenue_usd,
        coalesce(fa.features_adopted, 0)                             as features_adopted,

        p.first_purchase_at,
        date_diff(date(p.first_purchase_at), r.first_seen_date, day) as days_to_first_purchase,

        -- ACTIVATION. See the README: first purchase within N days of the first
        -- session, where N is the `activation_window_days` project variable.
        coalesce(
            date_diff(date(p.first_purchase_at), r.first_seen_date, day)
                between 0 and {{ var('activation_window_days') }},
            false
        )                                                            as is_activated,

        r.is_retained_d1,
        r.is_retained_d7,
        r.is_retained_d30,

        -- Eligibility: has the observation window for this user actually closed?
        date_diff(w.window_end_date, r.first_seen_date, day)
            >= {{ var('activation_window_days') }}                   as has_full_activation_window,
        date_diff(w.window_end_date, r.first_seen_date, day) >= 1     as has_full_d1_window,
        date_diff(w.window_end_date, r.first_seen_date, day) >= 7     as has_full_d7_window,
        date_diff(w.window_end_date, r.first_seen_date, day) >= 30    as has_full_d30_window,

        r.is_left_censored,

        -- The population every cohort metric should be measured on: users we
        -- genuinely saw arrive, inside a window we can fully observe.
        not r.is_left_censored                                       as is_cohort_eligible

    from session_rollup r
    left join first_session_attributes a using (user_pseudo_id)
    left join first_purchase           p using (user_pseudo_id)
    left join feature_first_use       fa using (user_pseudo_id)
    cross join window_bounds           w

)

select * from final
