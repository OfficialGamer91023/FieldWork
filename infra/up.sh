#!/usr/bin/env bash
# Bring the whole Fieldwork stack up from nothing (or update it in place).
#   1. ECR repo + secret slot   2. secret value from ./.env   3. orchestrator image   4. everything else
# Needs: AWS CLI logged in as rafay-admin, Docker running, Terraform. Run from the repo root.
# Tear down with:  (cd infra/app && terraform destroy)
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD
TF="terraform -chdir=infra/app"
REGION=us-east-1

echo "==> 1/4 ECR repository + secret slot"
$TF init -input=false >/dev/null
$TF apply -input=false -auto-approve \
  -target=aws_ecr_repository.orchestrator \
  -target=aws_secretsmanager_secret.fieldwork_apis

echo "==> 2/4 API keys: ./.env -> Secrets Manager (value never printed)"
SECRET_ARN=$($TF output -raw fieldwork_apis_secret_arn)
TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT
python3 - "$TMP" <<'PY'
import json, sys
env = {}
for line in open(".env"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
json.dump({"ASSEMBLY_API": env["ASSEMBLY_API"], "FEATHERLESS_API": env["FEATHERLESS_API"]}, open(sys.argv[1], "w"))
PY
aws secretsmanager put-secret-value --region $REGION --secret-id "$SECRET_ARN" \
  --secret-string "file://$TMP" --query VersionId --output text >/dev/null
rm -f "$TMP"

echo "==> 3/4 Orchestrator image (linux/arm64 to match the Fargate task)"
REPO=$($TF output -raw repository_url)
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin "${REPO%%/*}" >/dev/null
docker build --platform linux/arm64 -t "$REPO:latest" "$ROOT/server"
docker push "$REPO:latest"

echo "==> 4/4 Everything else (VPC, ALB, CloudFront, ECS, Lambdas, queues, tables, API)"
$TF apply -input=false -auto-approve

# If the service already existed, make it pick up the image we just pushed.
aws ecs update-service --region $REGION --cluster fieldwork-cluster --service fieldwork-orchestrator \
  --force-new-deployment --query 'service.serviceName' --output text >/dev/null

echo
echo "Done. Set these on Vercel (Project -> Settings -> Environment Variables), then redeploy:"
echo "  CONTROL_PLANE_API_URL          = $($TF output -raw control_plane_api_url)"
echo "  NEXT_PUBLIC_ORCHESTRATOR_WS_URL = $($TF output -raw orchestrator_ws_url)"
echo "  NEXT_PUBLIC_APP_URL            = https://<your-vercel-domain>"
echo "CloudFront takes ~5 minutes to go live the first time. Health check:"
echo "  curl https://$($TF output -raw orchestrator_ws_url | sed 's#wss://##')/"
