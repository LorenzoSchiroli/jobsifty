resource "aws_scheduler_schedule" "ingestion" {
  name       = "${var.project}-ingestion"
  group_name = "default"

  flexible_time_window {
    mode = "OFF"
  }

  schedule_expression = "cron(0 */${var.ingestion_interval_hours} * * ? *)"

  target {
    arn      = aws_ecs_cluster.main.arn
    role_arn = aws_iam_role.scheduler.arn

    ecs_parameters {
      task_definition_arn = aws_ecs_task_definition.ingestion.arn
      launch_type         = "FARGATE"
      platform_version    = "LATEST"
      task_count          = 1

      network_configuration {
        subnets          = local.ecs_network_configuration.subnets
        security_groups  = local.ecs_network_configuration.security_groups
        assign_public_ip = local.ecs_network_configuration.assign_public_ip
      }
    }

    retry_policy {
      maximum_event_age_in_seconds = 86400
      maximum_retry_attempts       = 2
    }
  }
}
