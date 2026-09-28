terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.0"
    }
  }
}

provider "aws" {
  region = "us-east-1"
}

data "aws_iam_policy_document" "ecs_task_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

variable "allowed_origins" {
  description = "Comma-separated browser origins allowed to open interview sockets (e.g. https://fieldwork.vercel.app). Empty = any."
  type        = string
  default     = ""
}

variable "polly_voice" {
  description = "Amazon Polly generative voice for the interviewer"
  type        = string
  default     = "Matthew"
}

resource "aws_ecr_repository" "orchestrator" {
  name                 = "fieldwork-orchestrator"
  image_tag_mutability = "MUTABLE"
  force_delete         = true
  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_secretsmanager_secret" "fieldwork_apis" {
  name                    = "fieldwork/apis"
  description             = "API keys for Fieldwork (Assembly, Featherless)"
  recovery_window_in_days = 0
}

resource "aws_iam_role" "execution" {
  name               = "fieldwork-ecs-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_task_assume_role.json
}

resource "aws_iam_role_policy_attachment" "execution_managed" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "execution_secrets" {
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.fieldwork_apis.arn]
  }
}

resource "aws_iam_role_policy" "execution_secrets" {
  name   = "secrets-read"
  role   = aws_iam_role.execution.name
  policy = data.aws_iam_policy_document.execution_secrets.json
}

resource "aws_iam_role" "task" {
  name               = "fieldwork-ecs-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_task_assume_role.json
}

data "aws_iam_policy_document" "task_permissions" {
  statement {
    actions = ["dynamodb:Query"]
    resources = [
      aws_dynamodb_table.studies.arn,
      "${aws_dynamodb_table.studies.arn}/index/*"
    ]
  }
  statement {
    actions   = ["dynamodb:PutItem"]
    resources = [aws_dynamodb_table.sessions.arn]
  }
  statement {
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.transcripts.arn}/*"]
  }
  statement {
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.extraction.arn]
  }
  statement {
    # Polly has no per-voice resource ARNs; the action is the whole permission.
    actions   = ["polly:SynthesizeSpeech"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "task_permissions" {
  name   = "task-permissions"
  role   = aws_iam_role.task.name
  policy = data.aws_iam_policy_document.task_permissions.json
}

output "fieldwork_apis_secret_arn" {
  value = aws_secretsmanager_secret.fieldwork_apis.arn
}

output "repository_url" { value = aws_ecr_repository.orchestrator.repository_url }

resource "aws_cloudwatch_log_group" "orchestrator" {
  name              = "/ecs/fieldwork-orchestrator"
  retention_in_days = 14
}

resource "aws_ecs_task_definition" "orchestrator" {
  family                   = "fieldwork-orchestrator"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "256"
  memory                   = "512"

  execution_role_arn = aws_iam_role.execution.arn
  task_role_arn      = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([
    {
      name      = "orchestrator"
      image     = "${aws_ecr_repository.orchestrator.repository_url}:latest"
      essential = true

      portMappings = [
        {
          containerPort = 8000
          protocol      = "tcp"
        }
      ]

      environment = [
        {
          name  = "STUDIES_TABLE"
          value = aws_dynamodb_table.studies.name
        },
        {
          name  = "SESSIONS_TABLE"
          value = aws_dynamodb_table.sessions.name
        },
        {
          name  = "TRANSCRIPTS_BUCKET"
          value = aws_s3_bucket.transcripts.bucket
        },
        {
          name  = "EXTRACTION_QUEUE_URL"
          value = aws_sqs_queue.extraction.url
        },
        {
          name  = "ALLOWED_ORIGINS"
          value = var.allowed_origins
        },
        {
          name  = "POLLY_VOICE"
          value = var.polly_voice
        }
      ]

      secrets = [
        {
          name      = "ASSEMBLY_API"
          valueFrom = "${aws_secretsmanager_secret.fieldwork_apis.arn}:ASSEMBLY_API::"
        },
        {
          name      = "FEATHERLESS_API"
          valueFrom = "${aws_secretsmanager_secret.fieldwork_apis.arn}:FEATHERLESS_API::"
        }
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.orchestrator.name
          "awslogs-region"        = "us-east-1"
          "awslogs-stream-prefix" = "orchestrator"
        }
      }
    }
  ])
}

resource "aws_vpc" "main" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true

  tags = {
    Name = "fieldwork-vpc"
  }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "fieldwork-igw"
  }
}

resource "aws_subnet" "a" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.0.1.0/24"
  availability_zone = "us-east-1a"

  tags = {
    Name = "fieldwork-subnet-a"
  }
}

resource "aws_subnet" "b" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.0.2.0/24"
  availability_zone = "us-east-1b"

  tags = {
    Name = "fieldwork-subnet-b"
  }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = {
    Name = "fieldwork-public-rt"
  }
}

resource "aws_route_table_association" "a" {
  subnet_id      = aws_subnet.a.id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "b" {
  subnet_id      = aws_subnet.b.id
  route_table_id = aws_route_table.public.id
}

resource "aws_security_group" "task" {
  name   = "fieldwork-task-sg"
  vpc_id = aws_vpc.main.id

  ingress {
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "fieldwork-task-sg" }
}

resource "aws_ecs_cluster" "main" {
  name = "fieldwork-cluster"
}

resource "aws_ecs_service" "orchestrator" {
  name            = "fieldwork-orchestrator"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.orchestrator.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = [aws_subnet.a.id, aws_subnet.b.id]
    security_groups  = [aws_security_group.task.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.orchestrator.arn
    container_name   = "orchestrator"
    container_port   = 8000
  }

  depends_on = [aws_lb_listener.http]
}

resource "aws_security_group" "alb" {
  name   = "fieldwork-alb-sg"
  vpc_id = aws_vpc.main.id

  ingress { # only CloudFront's edge servers can reach the ALB; browsers go through CloudFront
    from_port       = 80
    to_port         = 80
    protocol        = "tcp"
    prefix_list_ids = [data.aws_ec2_managed_prefix_list.cloudfront.id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = { Name = "fieldwork-alb-sg" }
}

data "aws_ec2_managed_prefix_list" "cloudfront" {
  name = "com.amazonaws.global.cloudfront.origin-facing"
}

resource "aws_lb" "main" {
  name               = "fieldwork-alb"
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = [aws_subnet.a.id, aws_subnet.b.id]
}

resource "aws_lb_target_group" "orchestrator" {
  name        = "fieldwork-tg"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip"
  # Interviews in flight get this long to finish when a new image replaces the task.
  deregistration_delay = 60

  health_check {
    path    = "/"
    matcher = "200"
  }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.orchestrator.arn
  }
}

resource "aws_dynamodb_table" "studies" {
  name         = "fieldwork-studies"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "studyId"

  # Production-hardening (skipped for budget/learning):
  # deletion_protection_enabled = true
  # point_in_time_recovery {
  #   enabled = true
  # }

  attribute {
    name = "studyId"
    type = "S"
  }

  attribute {
    name = "founderId"
    type = "S"
  }

  attribute {
    name = "createdAt"
    type = "S"
  }

  attribute {
    name = "inviteToken"
    type = "S"
  }

  global_secondary_index {
    name            = "byFounder"
    hash_key        = "founderId"
    range_key       = "createdAt"
    projection_type = "ALL"
  }

  global_secondary_index {
    name            = "byInviteToken"
    hash_key        = "inviteToken"
    projection_type = "ALL"
  }
}

resource "aws_dynamodb_table" "sessions" {
  name         = "fieldwork-sessions"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "studyId"
  range_key    = "sessionId"

  # Production-hardening (skipped for budget/learning):
  # deletion_protection_enabled = true
  # point_in_time_recovery {
  #   enabled = true
  # }

  attribute {
    name = "studyId"
    type = "S"
  }

  attribute {
    name = "sessionId"
    type = "S"
  }
}

data "aws_caller_identity" "current" {}

resource "aws_s3_bucket" "transcripts" {
  bucket        = "fieldwork-transcripts-${data.aws_caller_identity.current.account_id}"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "transcripts" {
  bucket                  = aws_s3_bucket.transcripts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_sqs_queue" "extraction_dlq" {
  name = "fieldwork-extraction-dlq"
}

resource "aws_sqs_queue" "extraction" {
  name                       = "fieldwork-extraction"
  visibility_timeout_seconds = 180
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.extraction_dlq.arn
    maxReceiveCount     = 3
  })
}

data "archive_file" "control_plane" {
  type        = "zip"
  source_dir  = "${path.module}/../../control-plane"
  output_path = "${path.module}/control-plane.zip"
}

data "aws_iam_policy_document" "control_plane_lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "control_plane_lambda" {
  name               = "fieldwork-control-plane-lambda"
  assume_role_policy = data.aws_iam_policy_document.control_plane_lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "control_plane_lambda_basic" {
  role       = aws_iam_role.control_plane_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "control_plane_dynamodb" {
  statement {
    actions = [
      "dynamodb:PutItem",
      "dynamodb:GetItem",
      "dynamodb:Query",
      "dynamodb:UpdateItem"
    ]
    resources = [
      aws_dynamodb_table.studies.arn,
      "${aws_dynamodb_table.studies.arn}/index/*"
    ]
  }
  statement {
    # Read-only on interviews: the dashboard lists them and opens transcripts.
    actions   = ["dynamodb:Query", "dynamodb:GetItem"]
    resources = [aws_dynamodb_table.sessions.arn]
  }
  statement {
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.transcripts.arn}/transcripts/*"]
  }
}

resource "aws_iam_role_policy" "control_plane_dynamodb" {
  name   = "dynamodb-access"
  role   = aws_iam_role.control_plane_lambda.name
  policy = data.aws_iam_policy_document.control_plane_dynamodb.json
}

data "aws_iam_policy_document" "control_plane_sqs" {
  statement {
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.synthesis.arn]
  }
}

resource "aws_iam_role_policy" "control_plane_sqs" {
  name   = "synthesis-enqueue"
  role   = aws_iam_role.control_plane_lambda.name
  policy = data.aws_iam_policy_document.control_plane_sqs.json
}

resource "aws_lambda_function" "control_plane" {
  function_name    = "fieldwork-control-plane"
  role             = aws_iam_role.control_plane_lambda.arn
  handler          = "handler.handler"
  runtime          = "python3.13"
  filename         = data.archive_file.control_plane.output_path
  source_code_hash = data.archive_file.control_plane.output_base64sha256

  environment {
    variables = {
      STUDIES_TABLE             = aws_dynamodb_table.studies.name
      SESSIONS_TABLE            = aws_dynamodb_table.sessions.name
      TRANSCRIPTS_BUCKET        = aws_s3_bucket.transcripts.bucket
      SYNTHESIS_QUEUE_URL       = aws_sqs_queue.synthesis.url
      SYNTHESIS_STALE_AFTER_SEC = local.synthesis_stale_after_sec
    }
  }
}

resource "aws_apigatewayv2_api" "control_plane" {
  name          = "fieldwork-control-plane"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_stage" "control_plane_default" {
  api_id      = aws_apigatewayv2_api.control_plane.id
  name        = "$default"
  auto_deploy = true

  # No auth yet (Cognito is post-hackathon), so cap what a leaked URL can cost.
  default_route_settings {
    throttling_burst_limit = 20
    throttling_rate_limit  = 10
  }
}

resource "aws_apigatewayv2_integration" "control_plane" {
  api_id                 = aws_apigatewayv2_api.control_plane.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.control_plane.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "control_plane_post_studies" {
  api_id    = aws_apigatewayv2_api.control_plane.id
  route_key = "POST /studies"
  target    = "integrations/${aws_apigatewayv2_integration.control_plane.id}"
}

resource "aws_apigatewayv2_route" "control_plane_get_studies" {
  api_id    = aws_apigatewayv2_api.control_plane.id
  route_key = "GET /studies"
  target    = "integrations/${aws_apigatewayv2_integration.control_plane.id}"
}

resource "aws_apigatewayv2_route" "control_plane_get_study" {
  api_id    = aws_apigatewayv2_api.control_plane.id
  route_key = "GET /studies/{id}"
  target    = "integrations/${aws_apigatewayv2_integration.control_plane.id}"
}

resource "aws_apigatewayv2_route" "control_plane_list_sessions" {
  api_id    = aws_apigatewayv2_api.control_plane.id
  route_key = "GET /studies/{id}/sessions"
  target    = "integrations/${aws_apigatewayv2_integration.control_plane.id}"
}

resource "aws_apigatewayv2_route" "control_plane_get_session" {
  api_id    = aws_apigatewayv2_api.control_plane.id
  route_key = "GET /studies/{id}/sessions/{sessionId}"
  target    = "integrations/${aws_apigatewayv2_integration.control_plane.id}"
}

resource "aws_apigatewayv2_route" "control_plane_publish_study" {
  api_id    = aws_apigatewayv2_api.control_plane.id
  route_key = "PATCH /studies/{id}"
  target    = "integrations/${aws_apigatewayv2_integration.control_plane.id}"
}

resource "aws_apigatewayv2_route" "control_plane_start_synthesis" {
  api_id    = aws_apigatewayv2_api.control_plane.id
  route_key = "POST /studies/{id}/synthesize"
  target    = "integrations/${aws_apigatewayv2_integration.control_plane.id}"
}

resource "aws_apigatewayv2_route" "control_plane_get_invite" {
  api_id    = aws_apigatewayv2_api.control_plane.id
  route_key = "GET /invite/{token}"
  target    = "integrations/${aws_apigatewayv2_integration.control_plane.id}"
}

resource "aws_lambda_permission" "apigw_control_plane" {
  statement_id  = "AllowExecutionFromAPIGateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.control_plane.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.control_plane.execution_arn}/*/*"
}

data "archive_file" "extractor" {
  type        = "zip"
  source_dir  = "${path.module}/../../extract"
  output_path = "${path.module}/extractor.zip"
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "extractor_lambda" {
  name               = "fieldwork-extractor-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "extractor_lambda_basic" {
  role       = aws_iam_role.extractor_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "extractor_permissions" {
  statement {
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes"
    ]
    resources = [aws_sqs_queue.extraction.arn]
  }
  statement {
    actions = [
      "dynamodb:GetItem",
      "dynamodb:UpdateItem"
    ]
    resources = [aws_dynamodb_table.sessions.arn]
  }
  # Read-only: the study's goal + seed questions steer what gets extracted.
  statement {
    actions   = ["dynamodb:GetItem"]
    resources = [aws_dynamodb_table.studies.arn]
  }
  statement {
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.transcripts.arn}/*"]
  }
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.fieldwork_apis.arn]
  }
}

resource "aws_iam_role_policy" "extractor_permissions" {
  name   = "extractor-permissions"
  role   = aws_iam_role.extractor_lambda.name
  policy = data.aws_iam_policy_document.extractor_permissions.json
}

variable "extraction_model" {
  description = "Featherless model for per-interview extraction."
  type        = string
  # Async like synthesis, and it reads the full transcript: a weak read here
  # can't be recovered downstream, so it gets the big model too.
  default = "deepseek-ai/DeepSeek-V4.1-Flash"
}

resource "aws_lambda_function" "extractor" {
  function_name    = "fieldwork-extractor"
  role             = aws_iam_role.extractor_lambda.arn
  handler          = "handler.handler"
  runtime          = "python3.13"
  filename         = data.archive_file.extractor.output_path
  source_code_hash = data.archive_file.extractor.output_base64sha256
  timeout          = 30

  environment {
    variables = {
      STUDIES_TABLE      = aws_dynamodb_table.studies.name
      SESSIONS_TABLE     = aws_dynamodb_table.sessions.name
      TRANSCRIPTS_BUCKET = aws_s3_bucket.transcripts.bucket
      EXTRACTION_MODEL   = var.extraction_model
      # Pass only the secret's ARN; the handler fetches the value at runtime
      # via GetSecretValue so the plaintext key never lands in tfstate.
      FIELDWORK_SECRET_ARN = aws_secretsmanager_secret.fieldwork_apis.arn
    }
  }
}

resource "aws_lambda_event_source_mapping" "extractor_sqs" {
  event_source_arn = aws_sqs_queue.extraction.arn
  function_name    = aws_lambda_function.extractor.arn
  batch_size       = 1
  # Featherless budget is 100 concurrency units shared by all three planes.
  # 5 extractors x 4 units (DeepSeek) = 20 max, so a burst of finished
  # interviews can never starve live interviews (7B, 1 unit per reply).
  scaling_config {
    maximum_concurrency = 5
  }
}

variable "synthesis_model" {
  description = "Featherless model for synthesis. Set to a bogus name to test the failure path."
  type        = string
  # Synthesis is one off-the-hot-path call per study, so it can afford a far
  # bigger model than the real-time interview loop (which stays on the 7B).
  default = "deepseek-ai/DeepSeek-V4.1-Flash"
}

locals {
  synthesis_max_receives = 3
  synthesizer_timeout    = 60
  # AWS guidance: visibility >= 6x the function timeout, so a slow attempt (plus
  # Lambda's own internal retries) never overlaps a redelivery of the same message.
  synthesis_visibility_sec = 6 * local.synthesizer_timeout
  # The worker refreshes synthesisStartedAt on every SQS receive, and a failed
  # attempt is redelivered at most one visibility timeout later. So a live run's
  # timestamp is never older than visibility + one run; past that, it's dead.
  synthesis_stale_after_sec = local.synthesis_visibility_sec + 2 * local.synthesizer_timeout
}

data "archive_file" "synthesizer" {
  type        = "zip"
  source_dir  = "${path.module}/../../synthesize"
  output_path = "${path.module}/synthesizer.zip"
  excludes    = ["test_handler.py", "seed.py", "invoke_test.py", "__pycache__"]
}

resource "aws_iam_role" "synthesizer_lambda" {
  name               = "fieldwork-synthesizer-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "synthesizer_lambda_basic" {
  role       = aws_iam_role.synthesizer_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "synthesizer_permissions" {
  statement {
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes"
    ]
    resources = [aws_sqs_queue.synthesis.arn]
  }
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.fieldwork_apis.arn]
  }
  statement {
    actions   = ["dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.studies.arn]
  }
  statement {
    actions   = ["dynamodb:Query"]
    resources = [aws_dynamodb_table.sessions.arn]
  }
}

resource "aws_iam_role_policy" "synthesizer_permissions" {
  name   = "synthesizer-permissions"
  role   = aws_iam_role.synthesizer_lambda.name
  policy = data.aws_iam_policy_document.synthesizer_permissions.json
}

resource "aws_lambda_function" "synthesizer" {
  function_name    = "fieldwork-synthesizer"
  role             = aws_iam_role.synthesizer_lambda.arn
  handler          = "handler.handler"
  runtime          = "python3.13"
  filename         = data.archive_file.synthesizer.output_path
  source_code_hash = data.archive_file.synthesizer.output_base64sha256
  timeout          = local.synthesizer_timeout
  # 128 MB used 96 MB on 3 tiny sessions; more memory also buys proportionally more CPU.
  memory_size = 256

  environment {
    variables = {
      STUDIES_TABLE        = aws_dynamodb_table.studies.name
      SESSIONS_TABLE       = aws_dynamodb_table.sessions.name
      FIELDWORK_SECRET_ARN = aws_secretsmanager_secret.fieldwork_apis.arn
      MAX_RETRIES          = local.synthesis_max_receives
      SYNTHESIS_MODEL      = var.synthesis_model
    }
  }
}

resource "aws_cloudwatch_log_group" "synthesizer" {
  name              = "/aws/lambda/fieldwork-synthesizer"
  retention_in_days = 14
}

resource "aws_sqs_queue" "synthesis_dlq" {
  name = "fieldwork-synthesis-dlq"
  # Max retention (14 days) so a dead message is still there when someone looks.
  message_retention_seconds = 1209600
}

resource "aws_sqs_queue" "synthesis" {
  name                       = "fieldwork-synthesis"
  visibility_timeout_seconds = local.synthesis_visibility_sec
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.synthesis_dlq.arn
    # Same value the handler reads as MAX_RETRIES, so "final attempt → FAILED"
    # lines up exactly with "next failure → DLQ".
    maxReceiveCount = local.synthesis_max_receives
  })
}

resource "aws_lambda_event_source_mapping" "synthesizer_sqs" {
  event_source_arn = aws_sqs_queue.synthesis.arn
  function_name    = aws_lambda_function.synthesizer.arn
  batch_size       = 1
  # 2 x 4 units = 8 max (2 is the lowest cap SQS mappings accept).
  scaling_config {
    maximum_concurrency = 2
  }
  # Creating the mapping validates that the role can already read the queue;
  # without this, Terraform may create it before the SQS permissions exist.
  depends_on = [aws_iam_role_policy.synthesizer_permissions]
}

output "execution_role_arn" { value = aws_iam_role.execution.arn }
output "task_role_arn" { value = aws_iam_role.task.arn }
output "alb_dns_name" { value = aws_lb.main.dns_name }
output "control_plane_api_url" { value = aws_apigatewayv2_stage.control_plane_default.invoke_url }
# ---- Public front door for the real-time plane -------------------------------------------
# Browsers load the dashboard over HTTPS (Vercel), so the interview socket must be wss://.
# CloudFront gives us TLS on *.cloudfront.net without owning a domain, and passes WebSocket
# upgrades straight through to the ALB. Nothing is cached.
data "aws_cloudfront_cache_policy" "disabled" {
  name = "Managed-CachingDisabled"
}

data "aws_cloudfront_origin_request_policy" "all_viewer" {
  name = "Managed-AllViewer" # forwards Sec-WebSocket-* headers, Origin and the ?token= query
}

resource "aws_cloudfront_distribution" "orchestrator" {
  enabled         = true
  comment         = "Fieldwork orchestrator (interview WebSocket)"
  price_class     = "PriceClass_100"
  is_ipv6_enabled = true

  origin {
    origin_id   = "alb"
    domain_name = aws_lb.main.dns_name
    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "http-only" # TLS ends at CloudFront; the ALB only accepts CloudFront
      origin_ssl_protocols   = ["TLSv1.2"]
      origin_read_timeout    = 60
    }
  }

  default_cache_behavior {
    target_origin_id         = "alb"
    viewer_protocol_policy   = "https-only"
    allowed_methods          = ["GET", "HEAD", "OPTIONS"]
    cached_methods           = ["GET", "HEAD"]
    cache_policy_id          = data.aws_cloudfront_cache_policy.disabled.id
    origin_request_policy_id = data.aws_cloudfront_origin_request_policy.all_viewer.id
    compress                 = false
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}

output "orchestrator_ws_url" {
  value = "wss://${aws_cloudfront_distribution.orchestrator.domain_name}"
}
