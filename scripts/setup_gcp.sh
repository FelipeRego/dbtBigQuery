#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# One-time Google Cloud setup for this project.
#
# Creates a dedicated GCP project, enables the BigQuery API, and writes
# Application Default Credentials that dbt will use.
#
# BigQuery's free tier gives you 1 TiB of query processing per month. A full
# refresh of this project bills about 6.75 GiB across 106 query jobs, so you
# would need to run it roughly 150 times in a month before paying anything.
#
# Usage:  ./scripts/setup_gcp.sh [PROJECT_ID]
# ---------------------------------------------------------------------------
set -euo pipefail

PROJECT_ID="${1:-ga4-dbt-semantic-$(date +%y%m%d)}"
LOCATION="US"   # the GA4 public dataset lives in the US multi-region

command -v gcloud >/dev/null || { echo "gcloud not found. brew install --cask google-cloud-sdk"; exit 1; }

echo "==> Signing in to Google Cloud (a browser window will open)"
gcloud auth login --update-adc=false --brief || true

echo "==> Creating project: ${PROJECT_ID}"
if gcloud projects describe "${PROJECT_ID}" >/dev/null 2>&1; then
  echo "    project already exists, reusing it"
else
  gcloud projects create "${PROJECT_ID}" --name="GA4 dbt semantic layer"
fi

gcloud config set project "${PROJECT_ID}"

echo "==> Enabling the BigQuery API"
gcloud services enable bigquery.googleapis.com --project "${PROJECT_ID}" || {
  echo
  echo "    Could not enable the API from the CLI. Open this URL once in a browser,"
  echo "    which activates the free BigQuery sandbox for the project, then re-run:"
  echo "    https://console.cloud.google.com/bigquery?project=${PROJECT_ID}"
  exit 1
}

echo "==> Writing Application Default Credentials (a browser window will open)"
gcloud auth application-default login --project "${PROJECT_ID}"
gcloud auth application-default set-quota-project "${PROJECT_ID}" || true

echo "==> Creating the target dataset: ${PROJECT_ID}:ga4_analytics (${LOCATION})"
bq --location="${LOCATION}" mk --dataset --description "dbt models built from the GA4 obfuscated sample" \
   "${PROJECT_ID}:ga4_analytics" 2>/dev/null || echo "    dataset already exists"

echo "==> Smoke test: can we read the public GA4 dataset?"
bq query --project_id="${PROJECT_ID}" --use_legacy_sql=false --format=pretty \
  'SELECT COUNT(*) AS events FROM `bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_*` WHERE _TABLE_SUFFIX = "20210131"'

cat <<MSG

Done. Add this to your shell so dbt picks up the project:

    export DBT_BIGQUERY_PROJECT=${PROJECT_ID}

MSG
