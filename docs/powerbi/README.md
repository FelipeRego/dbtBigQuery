# Power BI report

The Power BI report is registered as a dbt exposure
(`ga4_product_analytics_dashboard` in `models/exposures.yml`), so
`dbt ls --select +exposure:ga4_product_analytics_dashboard` answers "what breaks
this report?" before a model change ships.

It is also the one consumer in this project that can compute a metric
differently from the semantic layer, because the BigQuery connector reads the
mart tables directly rather than going through MetricFlow. Two rules keep it
honest, and both are worth stating on the report page itself.

## The two rules

**1. Filter on the eligibility flags, never on dates.**

Every cohort visual must filter on the pre-computed flags rather than
recomputing the window in DAX:

| Visual | Filter |
| --- | --- |
| Activation rate | `is_cohort_eligible = TRUE` **and** `has_full_activation_window = TRUE` |
| D7 retention | `is_cohort_eligible = TRUE` **and** `has_full_d7_window = TRUE` |
| D30 retention | `is_cohort_eligible = TRUE` **and** `has_full_d30_window = TRUE` |

A DAX measure that computes `DATEDIFF(first_seen_date, TODAY(), DAY) >= 7` will
disagree with the semantic layer the moment the data stops being refreshed
daily, and it will disagree silently.

**2. The funnel uses `is_reached_in_sequence`, not `is_reached`.**

`fct_funnel` publishes both. The strict column is the one the metrics use. The
permissive column counts 11,106 sessions at begin checkout where the strict one
counts 5,868 — an 89% difference — because carts survive across sessions and a
session-scoped funnel does not. Mixing the two inside one chart is the error
this column pair exists to prevent.

## Connecting

Power BI Desktop → **Get Data** → **Google BigQuery**, then:

1. Sign in with the Google account that owns the project from `setup_gcp.sh`.
2. Navigate to your project → the `ga4_analytics_marts` dataset.
3. Load `dim_users`, `fct_sessions`, `fct_funnel`. Add `fct_events` only if you
   need event-level detail — it is 4.3m rows and Import mode will be slow.
4. Use **Import** mode for the marts. They are small (270k users, 360k sessions,
   1.8m funnel rows) and Import gives a far better authoring experience than
   DirectQuery here.

Relationships: `dim_users[user_pseudo_id]` one-to-many to both
`fct_sessions[user_pseudo_id]` and `fct_funnel[user_pseudo_id]`;
`fct_sessions[session_key]` one-to-many to `fct_funnel[session_key]`.

## Suggested page

Four visuals, matching the generated overview in `docs/img/`:

1. **KPI row** — activation rate, D7 retention, session depth, feature adoption.
2. **Cohort curves** — activation and D7 retention by `first_seen_date`, rolled
   to week. Dated by *cohort arrival*, not by when the return happened. The last
   week of the window will legitimately be blank.
3. **Funnel** — the five steps from `fct_funnel`, on `is_reached_in_sequence`.
4. **Feature adoption** — by `feature_name` from `fct_events`, filtered to
   `is_user_initiated_feature = TRUE` and `days_since_first_session <= 7`.

## Screenshot

Save a PNG of the finished report to `docs/img/powerbi-dashboard.png` and add it
to the README beneath the generated overview. That file is not in this repo yet
— it needs Power BI Desktop, which is Windows-only.
