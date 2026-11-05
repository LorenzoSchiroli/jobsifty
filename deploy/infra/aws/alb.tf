resource "aws_acm_certificate" "main" {
  domain_name = var.domain
  subject_alternative_names = [
    "app.${var.domain}",
    "api.${var.domain}",
    "www.${var.domain}",
  ]
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_acm_certificate_validation" "main" {
  certificate_arn = aws_acm_certificate.main.arn

  # cloudflare_dns_record (provider v5) exposes no FQDN attribute, and dns.tf
  # stores a domain-relative name, so take the FQDN from ACM itself and depend
  # on the Cloudflare records for ordering.
  validation_record_fqdns = [
    for dvo in aws_acm_certificate.main.domain_validation_options :
    trimsuffix(dvo.resource_record_name, ".")
  ]

  depends_on = [cloudflare_dns_record.acm_validation]
}

resource "aws_lb" "main" {
  name               = "${var.project}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id

  # An advanced-search request is held to about 4 minutes (LLM calls are cut at the deadline,
  # 45 s kept for finalize); the 60 s default would cut it, 300 s leaves room above the cap.
  idle_timeout = 300

  tags = {
    Name = "${var.project}-alb"
  }
}

resource "aws_lb_target_group" "frontend" {
  name        = "${var.project}-frontend"
  port        = 80
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip"

  health_check {
    enabled             = true
    healthy_threshold   = 2
    interval            = 30
    matcher             = "200"
    path                = "/"
    port                = "traffic-port"
    protocol            = "HTTP"
    timeout             = 5
    unhealthy_threshold = 3
  }

  tags = {
    Name = "${var.project}-frontend"
  }
}

resource "aws_lb_target_group" "api" {
  name        = "${var.project}-api"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip"

  health_check {
    enabled             = true
    healthy_threshold   = 2
    interval            = 30
    matcher             = "200"
    path                = "/health"
    port                = "traffic-port"
    protocol            = "HTTP"
    timeout             = 5
    unhealthy_threshold = 3
  }

  tags = {
    Name = "${var.project}-api"
  }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"

    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.main.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = aws_acm_certificate_validation.main.certificate_arn

  default_action {
    type = "fixed-response"

    fixed_response {
      content_type = "text/plain"
      message_body = "Not found"
      status_code  = "404"
    }
  }

  depends_on = [aws_acm_certificate_validation.main]
}

resource "aws_lb_listener_rule" "apex_redirect" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 90

  action {
    type = "redirect"

    redirect {
      port        = "443"
      protocol    = "HTTPS"
      host        = "app.${var.domain}"
      status_code = "HTTP_301"
    }
  }

  condition {
    host_header {
      values = [var.domain]
    }
  }
}

resource "aws_lb_listener_rule" "www_redirect" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 95

  action {
    type = "redirect"

    redirect {
      port        = "443"
      protocol    = "HTTPS"
      host        = "app.${var.domain}"
      status_code = "HTTP_301"
    }
  }

  condition {
    host_header {
      values = ["www.${var.domain}"]
    }
  }
}

resource "aws_lb_listener_rule" "frontend" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 100

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.frontend.arn
  }

  condition {
    host_header {
      values = ["app.${var.domain}"]
    }
  }
}

resource "aws_lb_listener_rule" "api" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 110

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }

  condition {
    host_header {
      values = ["api.${var.domain}"]
    }
  }
}
