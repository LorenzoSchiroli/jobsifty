output "alb_dns_name" {
  description = "DNS name of the internet-facing application load balancer."
  value       = aws_lb.main.dns_name
}

output "ecs_cluster_name" {
  description = "ECS cluster name for RunTask and service operations."
  value       = aws_ecs_cluster.main.name
}

output "bootstrap_task_definition_arn" {
  description = "Bootstrap task definition ARN (migrations + OpenSearch setup)."
  value       = aws_ecs_task_definition.bootstrap.arn
}

output "ingestion_task_definition_arn" {
  description = "Ingestion task definition ARN (also scheduled every 2 hours)."
  value       = aws_ecs_task_definition.ingestion.arn
}

output "private_subnet_ids" {
  description = "Private subnet IDs for Fargate awsvpc network configuration."
  value       = aws_subnet.private[*].id
}

output "ecs_tasks_security_group_id" {
  description = "Security group ID attached to ECS Fargate tasks."
  value       = aws_security_group.ecs_tasks.id
}

output "rds_address" {
  description = "RDS Postgres hostname for debugging or one-off connections."
  value       = aws_db_instance.main.address
}

output "opensearch_endpoint" {
  description = "OpenSearch domain VPC endpoint (HTTPS)."
  value       = aws_opensearch_domain.main.endpoint
}

output "manage_dns" {
  description = "Whether Cloudflare app/api/apex/www records point at the ALB."
  value       = var.manage_dns
}

output "dump_bucket_name" {
  description = "S3 bucket for demo dump staging (force_destroy)."
  value       = aws_s3_bucket.dump.id
}

output "dump_s3_uri" {
  description = "s3:// URI of the staging dump object."
  value       = local.dump_s3_uri
}

output "dump_task_definition_arn" {
  description = "Fargate task definition ARN for pg_dump / pg_restore via S3."
  value       = aws_ecs_task_definition.dump.arn
}

output "ingestion_schedule_name" {
  description = "EventBridge Scheduler name for ingestion (pause/resume in demo scripts)."
  value       = aws_scheduler_schedule.ingestion.name
}

output "ingestion_command" {
  description = "The ingestion container's shell command (the first run after `up` appends a longer --hours)."
  value       = local.ingestion_command
}
