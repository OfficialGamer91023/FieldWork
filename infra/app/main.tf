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

variable "allowed_ip" {
  description = "Your public IP (with /32) allowed to reach the task for smoke testing"
  type        = string
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

  ingress { # who can reach the ALB
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = [var.allowed_ip] # keep it to your IP for now; open to 0.0.0.0/0 later
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = { Name = "fieldwork-alb-sg" }
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
      "dynamodb:Query"
    ]
    resources = [
      aws_dynamodb_table.studies.arn,
      "${aws_dynamodb_table.studies.arn}/index/*"
    ]
  }
}

resource "aws_iam_role_policy" "control_plane_dynamodb" {
  name   = "dynamodb-access"
  role   = aws_iam_role.control_plane_lambda.name
  policy = data.aws_iam_policy_document.control_plane_dynamodb.json
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
      STUDIES_TABLE = aws_dynamodb_table.studies.name
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

output "execution_role_arn" { value = aws_iam_role.execution.arn }
output "task_role_arn" { value = aws_iam_role.task.arn }
output "alb_dns_name" { value = aws_lb.main.dns_name }
output "control_plane_api_url" { value = aws_apigatewayv2_stage.control_plane_default.invoke_url }