locals {
  name = "territory-optimisation"
  apis = toset([
    "apigateway.googleapis.com",
    "artifactregistry.googleapis.com",
    "firestore.googleapis.com",
    "iam.googleapis.com",
    "monitoring.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "servicecontrol.googleapis.com",
    "servicemanagement.googleapis.com",
    "sheets.googleapis.com",
    "cloudscheduler.googleapis.com",
  ])
}

resource "google_project_service" "required" {
  for_each           = local.apis
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_service_account" "api" {
  account_id   = "territory-api"
  display_name = "Territory API service"
}

resource "google_service_account" "worker" {
  account_id   = "territory-worker"
  display_name = "Territory optimization worker"
}

resource "google_service_account" "scheduler" {
  account_id   = "territory-scheduler"
  display_name = "Territory scheduler caller"
}

resource "google_service_account" "gateway" {
  account_id   = "territory-gateway"
  display_name = "Territory API Gateway caller"
}

resource "google_project_service_identity" "gateway" {
  provider = google-beta
  project  = var.project_id
  service  = "apigateway.googleapis.com"
}

resource "google_project_service_identity" "scheduler" {
  provider = google-beta
  project  = var.project_id
  service  = "cloudscheduler.googleapis.com"
}

resource "google_service_account_iam_member" "gateway_token_creator" {
  service_account_id = google_service_account.gateway.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_project_service_identity.gateway.email}"
}

resource "google_service_account_iam_member" "scheduler_token_creator" {
  service_account_id = google_service_account.scheduler.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_project_service_identity.scheduler.email}"
}

resource "google_artifact_registry_repository" "containers" {
  location      = var.region
  repository_id = "territory-optimisation"
  description   = "Territory Optimisation API containers"
  format        = "DOCKER"
  depends_on    = [google_project_service.required]
}

resource "google_firestore_database" "state" {
  project     = var.project_id
  name        = "(default)"
  location_id = "eur3"
  type        = "FIRESTORE_NATIVE"
  depends_on  = [google_project_service.required]
}

resource "random_password" "hmac" {
  length  = 48
  special = false
}

resource "google_secret_manager_secret" "hmac" {
  secret_id = "territory-api-hmac"
  replication {
    auto {}
  }
  depends_on = [google_project_service.required]
}

resource "google_secret_manager_secret_version" "hmac" {
  secret      = google_secret_manager_secret.hmac.id
  secret_data = random_password.hmac.result
}

resource "google_secret_manager_secret_iam_member" "api_hmac" {
  secret_id = google_secret_manager_secret.hmac.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.api.email}"
}

resource "google_project_iam_member" "api_firestore" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.api.email}"
}

resource "google_project_iam_member" "worker_firestore" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.worker.email}"
}

resource "google_cloud_run_v2_job" "worker" {
  name     = "${local.name}-worker"
  location = var.region

  template {
    task_count  = 1
    parallelism = 1
    template {
      service_account = google_service_account.worker.email
      timeout         = "2400s"
      max_retries     = 0
      containers {
        image   = var.container_image
        command = ["territory-worker"]
        resources {
          limits = { cpu = "4", memory = "8Gi" }
        }
        env {
          name  = "TERRITORY_PROJECT_ID"
          value = var.project_id
        }
        env {
          name  = "TERRITORY_REGION"
          value = var.region
        }
        env {
          name  = "TERRITORY_SPREADSHEET_ID"
          value = var.spreadsheet_id
        }
        env {
          name  = "TERRITORY_ENGINE_VERSION"
          value = var.engine_version
        }
        env {
          name  = "TERRITORY_STATE_BACKEND"
          value = "firestore"
        }
        env {
          name  = "TERRITORY_SHEETS_BACKEND"
          value = "google"
        }
        env {
          name  = "TERRITORY_JOB_BACKEND"
          value = "cloud_run"
        }
      }
    }
  }
  depends_on = [google_project_service.required]
}

resource "google_project_iam_member" "api_job_runner" {
  project = var.project_id
  role    = "roles/run.jobsExecutorWithOverrides"
  member  = "serviceAccount:${google_service_account.api.email}"
}

resource "google_firestore_field" "auth_request_ttl" {
  project    = var.project_id
  database   = google_firestore_database.state.name
  collection = "territory_auth_requests"
  field      = "expires_at"

  ttl_config {}
}

resource "google_cloud_run_v2_service" "api" {
  name     = "${local.name}-api"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.api.email
    scaling {
      min_instance_count = 0
      max_instance_count = 2
    }
    containers {
      image = var.container_image
      resources {
        limits = { cpu = "1", memory = "1Gi" }
      }
      env {
        name  = "TERRITORY_PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "TERRITORY_REGION"
        value = var.region
      }
      env {
        name  = "TERRITORY_SPREADSHEET_ID"
        value = var.spreadsheet_id
      }
      env {
        name  = "TERRITORY_JOB_NAME"
        value = "${local.name}-worker"
      }
      env {
        name  = "TERRITORY_ENGINE_VERSION"
        value = var.engine_version
      }
      env {
        name  = "TERRITORY_STATE_BACKEND"
        value = "firestore"
      }
      env {
        name  = "TERRITORY_SHEETS_BACKEND"
        value = "google"
      }
      env {
        name  = "TERRITORY_JOB_BACKEND"
        value = "cloud_run"
      }
      env {
        name = "TERRITORY_HMAC_SECRET"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.hmac.secret_id
            version = "latest"
          }
        }
      }
    }
  }
  depends_on = [google_project_service.required, google_secret_manager_secret_iam_member.api_hmac]
}

resource "google_cloud_run_v2_service_iam_member" "scheduler_invoker" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.api.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler.email}"
}

resource "google_cloud_run_v2_service_iam_member" "gateway_invoker" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.api.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.gateway.email}"
}

resource "google_cloud_scheduler_job" "quarter_hour" {
  name      = "${local.name}-quarter-hour"
  region    = var.region
  schedule  = "*/15 * * * *"
  time_zone = "Europe/London"
  paused    = !var.scheduler_enabled

  http_target {
    uri         = "${google_cloud_run_v2_service.api.uri}/internal/scheduled-runs"
    http_method = "POST"
    oidc_token {
      service_account_email = google_service_account.scheduler.email
      audience              = google_cloud_run_v2_service.api.uri
    }
  }
  retry_config { retry_count = 1 }
  depends_on = [
    google_cloud_run_v2_service_iam_member.scheduler_invoker,
    google_service_account_iam_member.scheduler_token_creator,
  ]
}

resource "google_api_gateway_api" "api" {
  provider = google-beta
  api_id   = "${local.name}-api"
}

resource "random_id" "gateway_config" { byte_length = 4 }

resource "google_api_gateway_api_config" "api" {
  provider      = google-beta
  api           = google_api_gateway_api.api.api_id
  api_config_id = "config-${random_id.gateway_config.hex}"
  gateway_config {
    backend_config {
      google_service_account = google_service_account.gateway.email
    }
  }
  openapi_documents {
    document {
      path = "openapi.yaml"
      contents = base64encode(templatefile("${path.module}/openapi.yaml.tftpl", {
        backend_url = google_cloud_run_v2_service.api.uri
      }))
    }
  }
  lifecycle { create_before_destroy = true }
  depends_on = [
    google_project_service.required,
    google_cloud_run_v2_service_iam_member.gateway_invoker,
    google_service_account_iam_member.gateway_token_creator,
  ]
}

resource "google_api_gateway_gateway" "api" {
  provider   = google-beta
  api_config = google_api_gateway_api_config.api.id
  gateway_id = "${local.name}-gateway"
  region     = var.region
}

resource "google_apikeys_key" "apps_script" {
  name         = "territory-apps-script"
  display_name = "Territory Apps Script"
  restrictions {
    api_targets { service = google_api_gateway_api.api.managed_service }
  }
  depends_on = [google_project_service.required]
}

resource "google_logging_metric" "worker_failures" {
  name   = "territory_worker_failures"
  filter = "resource.type=\"cloud_run_job\" resource.labels.job_name=\"${google_cloud_run_v2_job.worker.name}\" severity>=ERROR"
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
  }
}

resource "google_logging_metric" "validation_failures" {
  name   = "territory_validation_failures"
  filter = "resource.type=\"cloud_run_job\" resource.labels.job_name=\"${google_cloud_run_v2_job.worker.name}\" textPayload:\"\\\"event\\\":\\\"run_failed\\\"\" textPayload:\"\\\"code\\\":\\\"validation_failed\\\"\""
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
  }
}

resource "google_logging_metric" "incomplete_coverage" {
  name   = "territory_incomplete_coverage"
  filter = "resource.type=\"cloud_run_job\" resource.labels.job_name=\"${google_cloud_run_v2_job.worker.name}\" textPayload:\"\\\"incomplete_coverage\\\":true\""
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
  }
}

resource "google_logging_metric" "infeasible_regions" {
  name   = "territory_infeasible_regions"
  filter = "resource.type=\"cloud_run_job\" resource.labels.job_name=\"${google_cloud_run_v2_job.worker.name}\" textPayload:\"\\\"infeasible\\\":true\""
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
  }
}

resource "google_logging_metric" "completed_checks" {
  name   = "territory_completed_checks"
  filter = "resource.type=\"cloud_run_job\" resource.labels.job_name=\"${google_cloud_run_v2_job.worker.name}\" textPayload:\"\\\"event\\\":\\\"run_completed\\\"\""
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
  }
}

resource "google_logging_metric" "scheduler_attempts" {
  name   = "territory_scheduler_attempts"
  filter = "resource.type=\"cloud_scheduler_job\" resource.labels.job_id=\"${google_cloud_scheduler_job.quarter_hour.name}\""
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
  }
}

resource "google_monitoring_notification_channel" "email" {
  count        = var.alert_email == "" ? 0 : 1
  display_name = "Territory API alerts"
  type         = "email"
  labels       = { email_address = var.alert_email }
}

resource "google_monitoring_alert_policy" "worker_failures" {
  display_name = "Territory worker failures"
  combiner     = "OR"
  conditions {
    display_name = "Cloud Run Job logged an error"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.worker_failures.name}\" AND resource.type=\"cloud_run_job\""
      duration        = "0s"
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      aggregations {
        alignment_period   = "900s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }
  notification_channels = [for channel in google_monitoring_notification_channel.email : channel.name]
}

resource "google_monitoring_alert_policy" "validation_failures" {
  display_name = "Territory input validation failures"
  combiner     = "OR"
  conditions {
    display_name = "A run rejected spreadsheet input"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.validation_failures.name}\" AND resource.type=\"cloud_run_job\""
      duration        = "0s"
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      aggregations {
        alignment_period   = "900s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }
  notification_channels = [for channel in google_monitoring_notification_channel.email : channel.name]
}

resource "google_monitoring_alert_policy" "coverage" {
  display_name = "Territory assignment coverage below 100 percent"
  combiner     = "OR"
  conditions {
    display_name = "A completed optimization left accounts uncovered"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.incomplete_coverage.name}\" AND resource.type=\"cloud_run_job\""
      duration        = "0s"
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      aggregations {
        alignment_period   = "900s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }
  notification_channels = [for channel in google_monitoring_notification_channel.email : channel.name]
}

resource "google_monitoring_alert_policy" "infeasible" {
  display_name = "Territory solver reported an infeasible region"
  combiner     = "OR"
  conditions {
    display_name = "Infeasible solver status"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.infeasible_regions.name}\" AND resource.type=\"cloud_run_job\""
      duration        = "0s"
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      aggregations {
        alignment_period   = "900s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }
  notification_channels = [for channel in google_monitoring_notification_channel.email : channel.name]
}

resource "google_monitoring_alert_policy" "stale_outputs" {
  display_name = "Territory outputs are stale"
  combiner     = "OR"
  enabled      = var.scheduler_enabled
  conditions {
    display_name = "No successful or skipped check for 45 minutes"
    condition_absent {
      filter   = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.completed_checks.name}\" AND resource.type=\"cloud_run_job\""
      duration = "2700s"
      aggregations {
        alignment_period   = "900s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }
  notification_channels = [for channel in google_monitoring_notification_channel.email : channel.name]
}

resource "google_monitoring_alert_policy" "scheduler_silence" {
  display_name = "Territory scheduler is silent"
  combiner     = "OR"
  enabled      = var.scheduler_enabled
  conditions {
    display_name = "No scheduler attempt for 30 minutes"
    condition_absent {
      filter   = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.scheduler_attempts.name}\" AND resource.type=\"cloud_scheduler_job\""
      duration = "1800s"
      aggregations {
        alignment_period   = "900s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }
  notification_channels = [for channel in google_monitoring_notification_channel.email : channel.name]
}
