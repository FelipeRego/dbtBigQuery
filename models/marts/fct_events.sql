{{
    config(
        partition_by=ga4_partition_by('event_date'),
        cluster_by=['event_name', 'device_category']
    )
}}

/*
    One row per event, with the session and cohort context needed to answer
    "how long after arriving did this happen?" without another join.

    `days_since_first_session` is carried down from int_sessions rather than
    recomputed, so that every metric in the semantic layer measures elapsed time
    from the same anchor. If activation, retention and feature adoption each
    computed their own day zero, they would disagree at the boundaries and
    nobody would be able to say which was right.
*/

with events as (

    select * from {{ ref('stg_events') }}

),

sessions as (

    select
        session_key,
        ga_session_number,
        is_first_session,
        is_engaged_session,
        days_since_first_session,
        user_first_seen_at,
        user_first_seen_date,
        is_left_censored
    from {{ ref('int_sessions') }}

),

features as (

    select * from {{ ref('feature_catalogue') }}

),

joined as (

    select
        e.event_key,
        e.session_key,
        e.user_pseudo_id,

        e.event_name,
        e.event_date,
        e.event_at,

        -- cohort context
        s.user_first_seen_at,
        s.user_first_seen_date,
        s.days_since_first_session,
        s.ga_session_number,
        coalesce(s.is_first_session, false)     as is_first_session,
        coalesce(s.is_engaged_session, false)   as is_engaged_session,
        coalesce(s.is_left_censored, false)     as is_left_censored,

        -- Has the 7-day observation window closed for this user? Derived here
        -- from the load window rather than joined from dim_users, so that the
        -- semantic models built on fct_events can enforce cohort eligibility
        -- without a cross-model join.
        date_diff(
            parse_date('%Y%m%d', '{{ var("ga4_end_date") }}'),
            s.user_first_seen_date,
            day
        ) >= 7                                  as has_full_d7_window,

        -- funnel position. Nulls are intentional: most events are not funnel
        -- steps, and forcing them into a step would make step counts wrong.
        case e.event_name
            when 'session_start'   then 1
            when 'view_item'       then 2
            when 'add_to_cart'     then 3
            when 'begin_checkout'  then 4
            when 'purchase'        then 5
        end                                     as funnel_step_number,

        case e.event_name
            when 'session_start'   then 'session_start'
            when 'view_item'       then 'view_item'
            when 'add_to_cart'     then 'add_to_cart'
            when 'begin_checkout'  then 'begin_checkout'
            when 'purchase'        then 'purchase'
        end                                     as funnel_step_name,

        -- feature adoption context, from the governed catalogue
        f.feature_name,
        coalesce(f.is_user_initiated, false)    as is_user_initiated_feature,

        -- dimensions
        e.device_category,
        e.operating_system,
        e.browser,
        e.platform,
        e.country,
        e.region,
        e.city,
        e.first_touch_source,
        e.first_touch_medium,
        e.first_touch_campaign,
        e.page_location,
        e.page_title,

        -- measures
        e.engagement_time_msec,
        e.transaction_id,
        coalesce(e.purchase_revenue_usd, 0)     as purchase_revenue_usd,
        coalesce(e.total_item_quantity, 0)      as total_item_quantity,
        coalesce(e.item_count, 0)               as item_count

    from events e
    left join sessions s using (session_key)
    left join features f on e.event_name = f.event_name

)

select * from joined
