locals {
  ecs_log_groups = {
    frontend  = "/ecs/${var.project}/frontend"
    api       = "/ecs/${var.project}/api"
    worker    = "/ecs/${var.project}/worker"
    ingestion = "/ecs/${var.project}/ingestion"
    bootstrap = "/ecs/${var.project}/bootstrap"
  }

  # Non-secret configuration comes straight from the repo's .env.public, the
  # same file Helm and compose read; only CORS follows this stack's domain.
  public_env = merge(
    {
      for kv in [
        for line in split("\n", file("${path.module}/../../../.env.public")) :
        regex("^([A-Z][A-Z0-9_]*)=(.*)$", trimspace(line))
        if can(regex("^[A-Z][A-Z0-9_]*=", trimspace(line)))
      ] : kv[0] => kv[1]
    },
    { CORS_ORIGINS = "https://app.${var.domain}" },
  )

  app_environment = [for name, value in local.public_env : { name = name, value = value }]

  app_secret_keys = [
    "DATABASE_URL",
    "OPENSEARCH_URL",
    "OPENSEARCH_USER",
    "OPENSEARCH_PASSWORD",
    "SECRET_KEY",
    "INGEST_TOKEN",
    "GROQ_API_KEY",
    "ADZUNA_APP_ID",
    "ADZUNA_APP_KEY",
    "TAVILY_API_KEY",
    "TINYFISH_API_KEY",
    "EXA_API_KEY",
    "NVIDIA_NIM_API_KEY",
    "GEMINI_API_KEY",
  ]

  # Ingestion only talks to the API over HTTP, so it gets no database,
  # OpenSearch or JWT secrets: just its write token and the external API keys.
  ingestion_secret_keys = [
    "INGEST_TOKEN",
    "GROQ_API_KEY",
    "ADZUNA_APP_ID",
    "ADZUNA_APP_KEY",
    "TAVILY_API_KEY",
    "TINYFISH_API_KEY",
    "EXA_API_KEY",
    "NVIDIA_NIM_API_KEY",
    "GEMINI_API_KEY",
  ]

  app_container_secrets = [
    for key in local.app_secret_keys : {
      name      = key
      valueFrom = "${aws_secretsmanager_secret.app.arn}:${key}::"
    }
  ]

  ingestion_container_secrets = [
    for key in local.ingestion_secret_keys : {
      name      = key
      valueFrom = "${aws_secretsmanager_secret.app.arn}:${key}::"
    }
  ]

  # Public GHCR packages pull anonymously, matching the Helm path (the chart
  # wires no imagePullSecrets). Set ghcr_token only if packages become private.
  ghcr_repository_credentials = var.ghcr_token != "" ? {
    repositoryCredentials = {
      credentialsParameter = aws_secretsmanager_secret.ghcr[0].arn
    }
  } : {}

  ecs_network_configuration = {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.ecs_tasks.id]
    assign_public_ip = false
  }
}

resource "aws_cloudwatch_log_group" "ecs" {
  for_each = local.ecs_log_groups

  name              = each.value
  retention_in_days = 7
}

resource "aws_ecs_cluster" "main" {
  name = var.project

  setting {
    name  = "containerInsights"
    value = "disabled"
  }
}

resource "aws_ecs_task_definition" "frontend" {
  family                   = "${var.project}-frontend"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([
    merge(local.ghcr_repository_credentials, {
      name      = "frontend"
      image     = var.frontend_image
      essential = true
      portMappings = [
        {
          containerPort = 80
          protocol      = "tcp"
        }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.ecs["frontend"].name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "ecs"
        }
      }
    })
  ])
}

# 2 vCPU for the search models (embedder + reranker); Fargate's minimum memory
# at 2 vCPU is 4 GB, so this roughly doubles the api task cost.
resource "aws_ecs_task_definition" "api" {
  family                   = "${var.project}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "2048"
  memory                   = "4096"
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([
    merge(local.ghcr_repository_credentials, {
      name      = "api"
      image     = var.backend_image
      essential = true
      command = [
        "uv", "run", "uvicorn", "backend.main:app",
        "--host", "0.0.0.0",
        "--port", "8000",
      ]
      portMappings = [
        {
          containerPort = 8000
          protocol      = "tcp"
        }
      ]
      environment = concat(local.app_environment, [
        { name = "TORCH_NUM_THREADS", value = "2" },
        { name = "OMP_NUM_THREADS", value = "2" },
      ])
      secrets = local.app_container_secrets
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.ecs["api"].name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "ecs"
        }
      }
    })
  ])
}

resource "aws_ecs_task_definition" "worker" {
  family                   = "${var.project}-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([
    merge(local.ghcr_repository_credentials, {
      name        = "worker"
      image       = var.backend_image
      essential   = true
      command     = ["uv", "run", "python", "-m", "backend.worker"]
      environment = local.app_environment
      secrets     = local.app_container_secrets
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.ecs["worker"].name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "ecs"
        }
      }
    })
  ])
}

locals {
  # Fargate has no deadline of its own: `timeout` kills a hung run 10 minutes
  # before the next one (the pipeline stops enriching 20 minutes before it).
  ingestion_command = join(" ", [
    "timeout ${var.ingestion_interval_hours * 3600 - 600}",
    "uv run python -m ingestion.pipeline ${var.offer_scope}",
    "--interval-hours ${var.ingestion_interval_hours}",
  ])
}

resource "aws_ecs_task_definition" "ingestion" {
  family                   = "${var.project}-ingestion"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "1024"
  memory                   = "2048"
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([
    merge(local.ghcr_repository_credentials, {
      name      = "ingestion"
      image     = var.ingestion_image
      essential = true
      command   = ["sh", "-c", local.ingestion_command]
      # Internal Cloud Map address, not the public hostname: ingestion must
      # always reach THIS stack's API, whichever cloud currently owns DNS.
      environment = concat(local.app_environment, [
        {
          name  = "BACKEND_URL"
          value = "http://api.${var.project}.local:8000"
        }
      ])
      secrets = local.ingestion_container_secrets
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.ecs["ingestion"].name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "ecs"
        }
      }
    })
  ])
}

resource "aws_ecs_task_definition" "bootstrap" {
  family                   = "${var.project}-bootstrap"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([
    merge(local.ghcr_repository_credentials, {
      name      = "bootstrap"
      image     = var.backend_image
      essential = true
      command = [
        "sh", "-c",
        "uv run alembic upgrade head && uv run python -m backend.bootstrap",
      ]
      environment = local.app_environment
      secrets     = local.app_container_secrets
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.ecs["bootstrap"].name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "ecs"
        }
      }
    })
  ])
}

resource "aws_ecs_service" "frontend" {
  name            = "frontend"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.frontend.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = local.ecs_network_configuration.subnets
    security_groups  = local.ecs_network_configuration.security_groups
    assign_public_ip = local.ecs_network_configuration.assign_public_ip
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.frontend.arn
    container_name   = "frontend"
    container_port   = 80
  }

  depends_on = [aws_lb_listener.https]
}

resource "aws_ecs_service" "api" {
  name            = "api"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = local.ecs_network_configuration.subnets
    security_groups  = local.ecs_network_configuration.security_groups
    assign_public_ip = local.ecs_network_configuration.assign_public_ip
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = 8000
  }

  service_registries {
    registry_arn = aws_service_discovery_service.api.arn
  }

  depends_on = [aws_lb_listener.https]
}

resource "aws_ecs_service" "worker" {
  name            = "worker"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  deployment_maximum_percent         = 100
  deployment_minimum_healthy_percent = 0

  network_configuration {
    subnets          = local.ecs_network_configuration.subnets
    security_groups  = local.ecs_network_configuration.security_groups
    assign_public_ip = local.ecs_network_configuration.assign_public_ip
  }

}

resource "aws_appautoscaling_target" "api" {
  max_capacity       = 4
  min_capacity       = 1
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.api.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"
}

resource "aws_appautoscaling_policy" "api_cpu" {
  name               = "${var.project}-api-cpu"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.api.resource_id
  scalable_dimension = aws_appautoscaling_target.api.scalable_dimension
  service_namespace  = aws_appautoscaling_target.api.service_namespace

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }

    target_value       = 70.0
    scale_in_cooldown  = 300
    scale_out_cooldown = 60
  }
}
