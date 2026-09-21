{{
    config(
        partition_by=ga4_partition_by('session_date'),
        cluster_by=['device_category', 'first_touch_medium']
    )
}}

/*
    One row per session, plus the user-level anchor that every cohort metric in
    this project depends on.

    Sessionisation
    --------------
    The grain is GA4's own `ga_session_id`, which is assigned client-side and
    rolls over after 30 minutes of inactivity *or* at midnight in the property's
    timezone. We adopt it rather than deriving our own boundaries, because it is
    the number that the GA4 UI shows and therefore the number a stakeholder will
    argue with us about. To make the cost of that choice measurable rather than
    assumed, we also count how many >N-minute gaps occur *inside* each GA4
    session (`inactivity_gaps`). A session with gaps > 0 is one that a
    server-side rule would have split.

    User first-seen
    ---------------
    `user_first_seen_at` is the user's first session *within the loaded window*,
    which is not the same thing as their first session ever. GA4 gives us the
    real answer in `user_first_touch_timestamp`, so we keep both and flag the
    disagreement as `is_left_censored`. Cohort metrics exclude censored users.
*/

with events as (

    select *
    from {{ ref('stg_events') }}
    where ga_session_id is not null

),

window_bounds as (

    select
        parse_date('%Y%m%d', '{{ var("ga4_start_date") }}') as window_start_date,
        parse_date('%Y%m%d', '{{ var("ga4_end_date") }}')   as window_end_date

),

event_gaps as (

    select
        session_key,
        timestamp_diff(
            event_at,
            lag(event_at) over (partition by session_key order by event_at),
            minute
        ) as minutes_since_previous_event
    from events

),

gap_summary as (

    select
        session_key,
        countif(minutes_since_previous_event > {{ var('session_gap_minutes') }}) as inactivity_gaps
    from event_gaps
    group by session_key

),

session_attributes as (

    -- Attributes taken from the chronologically first event of the session, so
    -- that a session that crosses a device or geo boundary reports where it
    -- started rather than an arbitrary row.
    select
        session_key,
        device_category,
        operating_system,
        browser,
        platform,
        country,
        region,
        city,
        first_touch_source,
        first_touch_medium,
        first_touch_campaign
    from events
    qualify row_number() over (partition by session_key order by event_at, event_name) = 1

),

session_agg as (

    select
        session_key,
        user_pseudo_id,
        ga_session_id,
        max(ga_session_number)                                            as ga_session_number,

        min(event_at)                                                     as session_started_at,
        max(event_at)                                                     as session_ended_at,
        date(min(event_at))                                               as session_date,
        timestamp_diff(max(event_at), min(event_at), second)              as session_duration_seconds,
        min(user_first_touch_at)                                          as user_first_touch_at,

        count(*)                                                          as events_in_session,
        countif(event_name = 'page_view')                                 as page_views,
        count(distinct page_location)                                     as distinct_pages_viewed,
        sum(coalesce(engagement_time_msec, 0)) / 1000                     as engagement_time_seconds,

        -- landing page: first event that actually carried a page_location
        array_agg(page_location ignore nulls order by event_at limit 1)[safe_offset(0)] as landing_page,

        -- GA4's own engagement flag, set client-side
        logical_or(is_session_engaged_event)                              as is_engaged_session,

        -- funnel step reached, as booleans; the long-format fact is built in marts
        countif(event_name = 'view_item')      > 0                        as has_view_item,
        countif(event_name = 'add_to_cart')    > 0                        as has_add_to_cart,
        countif(event_name = 'begin_checkout') > 0                        as has_begin_checkout,
        countif(event_name = 'purchase')       > 0                        as has_purchase,

        countif(event_name = 'purchase')                                  as purchase_events,
        count(distinct if(event_name = 'purchase', transaction_id, null)) as transactions,
        sum(if(event_name = 'purchase', coalesce(purchase_revenue_usd, 0), 0)) as session_revenue_usd

    from events
    group by session_key, user_pseudo_id, ga_session_id

),

user_first_seen as (

    select
        user_pseudo_id,
        min(session_started_at)          as user_first_seen_at,
        date(min(session_started_at))    as user_first_seen_date,
        min(user_first_touch_at)         as user_first_touch_at
    from session_agg
    group by user_pseudo_id

),

final as (

    select
        s.session_key,
        s.user_pseudo_id,
        s.ga_session_id,
        s.ga_session_number,

        s.session_started_at,
        s.session_ended_at,
        s.session_date,
        s.session_duration_seconds,

        s.events_in_session,
        s.page_views,
        s.distinct_pages_viewed,
        s.engagement_time_seconds,
        s.landing_page,

        coalesce(s.is_engaged_session, false)                             as is_engaged_session,

        -- The textbook definition, kept alongside GA4's so the README can
        -- quantify how far apart they land instead of asserting they agree.
        (
            s.session_duration_seconds >= 10
            or s.page_views >= 2
            or s.has_purchase
        )                                                                 as is_engaged_session_derived,

        coalesce(g.inactivity_gaps, 0)                                    as inactivity_gaps,
        coalesce(g.inactivity_gaps, 0) > 0                                as is_split_by_inactivity_rule,

        s.has_view_item,
        s.has_add_to_cart,
        s.has_begin_checkout,
        s.has_purchase,
        s.purchase_events,
        s.transactions,
        s.session_revenue_usd,

        a.device_category,
        a.operating_system,
        a.browser,
        a.platform,
        a.country,
        a.region,
        a.city,
        a.first_touch_source,
        a.first_touch_medium,
        a.first_touch_campaign,

        -- user anchor, repeated onto every session so cohort logic never needs
        -- a second join downstream
        u.user_first_seen_at,
        u.user_first_seen_date,
        u.user_first_touch_at,
        date_diff(s.session_date, u.user_first_seen_date, day)             as days_since_first_session,
        s.session_started_at = u.user_first_seen_at                        as is_first_session,

        -- True when GA4 says the user existed before our loaded window, which
        -- means their "first session" here is an artefact of the window.
        coalesce(date(u.user_first_touch_at) < w.window_start_date, false) as is_left_censored

    from session_agg s
    left join gap_summary        g using (session_key)
    left join session_attributes a using (session_key)
    left join user_first_seen    u using (user_pseudo_id)
    cross join window_bounds     w

)

select * from final
