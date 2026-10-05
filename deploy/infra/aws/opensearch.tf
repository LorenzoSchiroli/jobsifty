data "aws_caller_identity" "current" {}

resource "aws_opensearch_domain" "main" {
  domain_name    = "${var.project}-search"
  engine_version = "OpenSearch_2.19"

  cluster_config {
    instance_type          = var.opensearch_instance_type
    instance_count         = 1
    zone_awareness_enabled = false
  }

  ebs_options {
    ebs_enabled = true
    volume_type = "gp3"
    volume_size = 20
  }

  vpc_options {
    subnet_ids         = [aws_subnet.private[0].id]
    security_group_ids = [aws_security_group.opensearch.id]
  }

  encrypt_at_rest {
    enabled = true
  }

  node_to_node_encryption {
    enabled = true
  }

  domain_endpoint_options {
    enforce_https       = true
    tls_security_policy = "Policy-Min-TLS-1-2-2019-07"
  }

  advanced_security_options {
    enabled                        = true
    internal_user_database_enabled = true

    master_user_options {
      master_user_name     = "jobsifty"
      master_user_password = random_password.opensearch.result
    }
  }

  # Fine-grained access control above is what authorizes requests: the backend
  # connects as the internal-database master user over HTTP basic auth, which is
  # not an IAM principal, so an IAM-scoped domain policy rejects it with a bare
  # 403 before FGAC ever sees the credentials. Allowing all principals here is
  # the documented pairing for FGAC, and the domain stays closed off because it
  # has no public endpoint (vpc_options) and its security group only admits the
  # ECS tasks.
  access_policies = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { AWS = "*" }
        Action    = "es:ESHttp*"
        Resource  = "arn:aws:es:${var.aws_region}:${data.aws_caller_identity.current.account_id}:domain/${var.project}-search/*"
      },
    ]
  })

  tags = {
    Name = "${var.project}-search"
  }
}
