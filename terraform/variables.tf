variable "project_id" {
  description = "Google Cloud project ID."
  type        = string
}

variable "region" {
  description = "Cloud Run, Scheduler, and API Gateway region."
  type        = string
  default     = "europe-west2"
}

variable "spreadsheet_id" {
  description = "The single allowlisted Google Spreadsheet ID."
  type        = string
  sensitive   = true
}

variable "container_image" {
  description = "Immutable Artifact Registry image reference, preferably by digest."
  type        = string
}

variable "engine_version" {
  description = "Pinned territory engine commit SHA."
  type        = string
  default     = "91aacf817a8c94f338c1bd508079c6e83581fb6a"
}

variable "scheduler_enabled" {
  description = "Enable the every-15-minute schedule after staging validation."
  type        = bool
  default     = false
}

variable "alert_email" {
  description = "Optional email address for Cloud Monitoring alerts."
  type        = string
  default     = ""
}
