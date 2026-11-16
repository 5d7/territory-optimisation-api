output "api_gateway_url" {
  value = "https://${google_api_gateway_gateway.api.default_hostname}"
}

output "api_key" {
  value     = google_apikeys_key.apps_script.key_string
  sensitive = true
}

output "hmac_secret_version" {
  value = google_secret_manager_secret_version.hmac.name
}

output "worker_service_account" {
  description = "Share the target Google Sheet with this address as Editor."
  value       = google_service_account.worker.email
}

output "scheduler_paused" {
  value = !var.scheduler_enabled
}
