terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
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

output "execution_role_arn" { value = aws_iam_role.execution.arn }
output "task_role_arn" { value = aws_iam_role.task.arn }