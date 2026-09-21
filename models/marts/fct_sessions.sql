{{
    config(
        partition_by=ga4_partition_by('session_date'),
        cluster_by=['device_category', 'acquisition_medium']
    )
}}

/*
    One row per session, with the user's acquisition attributes attached so that
    "sessions by channel" and "users by channel" slice on the same dimension
    values. Sessions carry GA4's first-touch traffic source, which is a *user*
    attribute; joining it from dim_users rather than re-reading it from the
    event stream keeps it single-sourced and makes the semantics obvious in the
    column name.
*/

with sessions as (

    select * from {{ ref('int_sessions') }}

),

users as (

    select
        user_pseudo_id,
        acquisition_source,
        acquisition_medium,
        acquisition_campaign,
        first_device_category,
        first_country,
        is_left_censored as user_is_left_censored,
        is_cohort_eligible
    from {{ ref('dim_users') }}

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
        s.days_since_first_session,
        s.user_first_seen_date,
        coalesce(s.is_first_session, false)      as is_first_session,

        -- session dimensions
        s.device_category,
        s.operating_system,
        s.browser,
        s.platform,
        s.country,
        s.region,
        s.city,
        s.landing_page,

        -- user acquisition dimensions
        u.acquisition_source,
        u.acquisition_medium,
        u.acquisition_campaign,
        u.first_device_category,
        u.first_country,
        coalesce(u.is_cohort_eligible, false)    as is_cohort_eligible,
        coalesce(u.user_is_left_censored, false) as user_is_left_censored,

        -- engagement
        s.is_engaged_session,
        s.is_engaged_session_derived,
        s.engagement_time_seconds,
        s.inactivity_gaps,
        s.is_split_by_inactivity_rule,

        -- depth
        s.events_in_session,
        s.page_views,
        s.distinct_pages_viewed,

        -- commercial outcome
        s.has_view_item,
        s.has_add_to_cart,
        s.has_begin_checkout,
        s.has_purchase,
        s.purchase_events,
        s.transactions,
        s.session_revenue_usd

    from sessions s
    left join users u using (user_pseudo_id)

)

select * from final
