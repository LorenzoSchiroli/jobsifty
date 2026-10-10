resource "random_password" "db" {
  length  = 32
  special = false
}

resource "random_password" "opensearch" {
  length = 32
  # AWS rejects the domain unless the master password has an uppercase, a
  # lowercase, a digit and a special character. The value is sent to OpenSearch
  # as an http_auth tuple rather than inside a URL, so specials are safe; the
  # set below still excludes quotes, backslash, slash, @ and space so it stays
  # painless in shells, JSON and connection strings.
  min_upper        = 1
  min_lower        = 1
  min_numeric      = 1
  min_special      = 1
  override_special = "!#$%&*()-_=+[]{}<>:?"
}

resource "random_password" "app_secret_key" {
  length  = 48
  special = false
}

resource "aws_secretsmanager_secret" "app" {
  name                    = "${var.project}/app"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "app" {
  secret_id = aws_secretsmanager_secret.app.id
  secret_string = jsonencode({
    DATABASE_URL             = "postgresql+asyncpg://jobsifty:${random_password.db.result}@${aws_db_instance.main.address}:5432/jobsifty"
    OPENSEARCH_URL           = "https://${aws_opensearch_domain.main.endpoint}"
    OPENSEARCH_USER          = "jobsifty"
    OPENSEARCH_PASSWORD      = random_password.opensearch.result
    SECRET_KEY               = random_password.app_secret_key.result
    GROQ_API_KEY             = var.groq_api_key
    GROQ_MODEL_LARGE         = var.groq_model_large
    GROQ_MODEL_BASE          = var.groq_model_base
    CORS_ORIGINS             = "https://app.${var.domain}"
    OFFER_QUERY              = var.offer_query
    ACCESS_TOKEN_EXPIRE_DAYS = "7"
    ADZUNA_APP_ID            = var.adzuna_app_id
    ADZUNA_APP_KEY           = var.adzuna_app_key
    TAVILY_API_KEY           = var.tavily_api_key
    TINYFISH_API_KEY         = var.tinyfish_api_key
    EXA_API_KEY              = var.exa_api_key
    NVIDIA_NIM_API_KEY       = var.nvidia_nim_api_key
    GEMINI_API_KEY           = var.gemini_api_key
  })
}
