# --- Which providers this config needs -------------------------------------
# A "provider" is a plugin that knows how to talk to a specific cloud's API.
# Here we say: we need the AWS provider, version 5.x. Terraform will download it.
terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

# --- Configure the AWS provider --------------------------------------------
# Tells the AWS plugin which region to build in. It picks up your credentials
# automatically from the `aws configure` you already did.
provider "aws" {
  region = "us-east-1"
}

# --- A "data source": read something that already exists --------------------
# This looks up your AWS account ID (we use it to make the bucket name unique,
# since S3 bucket names must be globally unique across ALL of AWS).
data "aws_caller_identity" "current" {}

# --- A "resource": something Terraform will CREATE --------------------------
# This declares one S3 bucket. On `apply`, Terraform makes it exist.
resource "aws_s3_bucket" "hello" {
  bucket = "fieldwork-tf-hello-${data.aws_caller_identity.current.account_id}"
}

# --- An "output": print a value after apply ---------------------------------
# After building, Terraform will print the bucket's name so you can see it.
output "bucket_name" {
  value = aws_s3_bucket.hello.bucket
}
