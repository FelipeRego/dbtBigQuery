{{
    config(
        partition_by=ga4_partition_by('event_date'),
        cluster_by=['event_name', 'user_pseudo_id']
    )
}}

/*
    One row per GA4 event, typed and renamed. No business logic lives here:
    anything that makes a judgement call about what an event *means* belongs
    downstream, so that this model stays a faithful, cheap-to-rebuild mirror
    of the export.

    Two naming decisions are deliberate:

    1. `first_touch_*` rather than `session_source`. In the GA4 export the
       `traffic_source` record describes how the *user* was first acquired,
       not how the current session started. Calling it `session_source` is the
       single most common error built on top of this dataset.
    2. `event_key` is a constructed surrogate, not a natural key. GA4 events
       carry no unique identifier; see the uniqueness test and its comment.
*/

with source as (

    select *
    from {{ source('ga4_public', 'events') }}
    where _table_suffix between '{{ var("ga4_start_date") }}' and '{{ var("ga4_end_date") }}'

),

renamed as (

    select
        -- identifiers
        user_pseudo_id,
        {{ ga4_param_int('ga_session_id') }}                       as ga_session_id,
        {{ ga4_param_int('ga_session_number') }}                   as ga_session_number,
        stream_id,

        -- event
        event_name,
        parse_date('%Y%m%d', event_date)                           as event_date,
        timestamp_micros(event_timestamp)                          as event_at,
        event_bundle_sequence_id,
        event_value_in_usd,

        -- page context
        {{ ga4_param_string('page_location') }}                    as page_location,
        {{ ga4_param_string('page_title') }}                       as page_title,
        {{ ga4_param_string('page_referrer') }}                    as page_referrer,

        -- engagement
        {{ ga4_param_int('engagement_time_msec') }}                as engagement_time_msec,
        {{ ga4_param_string('session_engaged') }} = '1'            as is_session_engaged_event,
        coalesce({{ ga4_param_int('entrances') }}, 0) = 1          as is_entrance,

        -- device and geography
        device.category                                            as device_category,
        device.operating_system                                    as operating_system,
        device.web_info.browser                                    as browser,
        device.language                                            as device_language,
        platform,
        geo.country                                                as country,
        geo.region                                                 as region,
        geo.city                                                   as city,

        -- user-scoped acquisition, NOT session-scoped (see header comment)
        traffic_source.source                                      as first_touch_source,
        traffic_source.medium                                      as first_touch_medium,
        traffic_source.name                                        as first_touch_campaign,
        timestamp_micros(user_first_touch_timestamp)               as user_first_touch_at,

        -- ecommerce
        ecommerce.transaction_id                                   as transaction_id,
        ecommerce.purchase_revenue_in_usd                          as purchase_revenue_usd,
        ecommerce.total_item_quantity                              as total_item_quantity,
        ecommerce.unique_items                                     as unique_items,
        array_length(items)                                        as item_count

    from source

),

keyed as (

    select
        {{ dbt_utils.generate_surrogate_key([
            'user_pseudo_id',
            'ga_session_id',
            'event_at',
            'event_name',
            'event_bundle_sequence_id',
            'page_location'
        ]) }} as event_key,

        concat(user_pseudo_id, '-', cast(ga_session_id as string)) as session_key,

        *
    from renamed

)

select * from keyed
