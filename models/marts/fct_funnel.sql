{{
    config(
        partition_by=ga4_partition_by('session_date'),
        cluster_by=['step_name', 'device_category']
    )
}}

/*
    One row per session per funnel step — five rows per session, whether or not
    the step was reached. The dense shape is what makes step-to-step conversion
    a division of two counts rather than five separate queries stitched
    together, and it means a step with no traffic on a given day shows up as a
    zero instead of vanishing from the chart.

    Two reachability definitions are published side by side:

      is_reached             the session fired that event at any point
      is_reached_in_sequence the session fired that event AND every prior step

    They differ for two reasons. Within a session, GA4 lets a user add to cart
    straight from a category page without ever firing view_item. Across
    sessions — and this is the larger effect — the cart persists while this
    funnel does not, so a user who adds to cart on Monday and checks out on
    Tuesday produces a Tuesday session that begins checkout having reached no
    prior step.

    On this dataset the two definitions almost agree at the cart (15,188
    permissive against 14,897 strict) and diverge sharply at checkout (11,106
    against 5,868) and purchase (4,848 against 2,816). Neither is wrong. A
    funnel chart that silently mixes them is.
*/

with sessions as (

    select * from {{ ref('fct_sessions') }}

),

steps as (

    select *
    from unnest([
        struct(1 as step_number, 'session_start'  as step_name),
        struct(2,                'view_item'),
        struct(3,                'add_to_cart'),
        struct(4,                'begin_checkout'),
        struct(5,                'purchase')
    ])

),

step_events as (

    select
        session_key,
        funnel_step_number      as step_number,
        min(event_at)           as first_reached_at,
        count(*)                as step_event_count
    from {{ ref('fct_events') }}
    where funnel_step_number is not null
    group by session_key, funnel_step_number

),

expanded as (

    select
        {{ dbt_utils.generate_surrogate_key(['s.session_key', 'st.step_number']) }} as funnel_key,

        s.session_key,
        s.user_pseudo_id,
        s.session_date,
        s.session_started_at,
        s.days_since_first_session,
        s.user_first_seen_date,

        st.step_number,
        st.step_name,

        se.first_reached_at,
        coalesce(se.step_event_count, 0)            as step_event_count,
        se.first_reached_at is not null             as is_reached,

        timestamp_diff(se.first_reached_at, s.session_started_at, second) as seconds_from_session_start,

        s.device_category,
        s.operating_system,
        s.browser,
        s.country,
        s.acquisition_source,
        s.acquisition_medium,
        s.acquisition_campaign,
        s.is_engaged_session,
        s.is_cohort_eligible,
        s.session_revenue_usd

    from sessions s
    cross join steps st
    left join step_events se
           on s.session_key = se.session_key
          and st.step_number = se.step_number

),

sequenced as (

    select
        *,
        -- true only when this step and every step before it were reached
        min(if(is_reached, 1, 0)) over (
            partition by session_key
            order by step_number
            rows between unbounded preceding and current row
        ) = 1 as is_reached_in_sequence
    from expanded

)

select * from sequenced
