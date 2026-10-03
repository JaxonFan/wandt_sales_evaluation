#!/usr/bin/env bash
# One-time: store the assistant's API keys in Secrets Manager and let the ECS exec role read them.
# Run it yourself (it touches IAM + secrets):   ! scripts/setup_agent_secrets.sh
# It prompts for the two keys (SerpAPI is optional — leave blank to skip web search).
set -euo pipefail
REGION=us-east-1; ROLE=wandt-ecs-exec
read -rsp "ANTHROPIC_API_KEY: " ANTHROPIC; echo
read -rsp "SERPAPI_KEY (optional): " SERP; echo
put() { local name=$1 val=$2
  if aws secretsmanager describe-secret --secret-id "$name" --region $REGION >/dev/null 2>&1; then
    aws secretsmanager put-secret-value --secret-id "$name" --secret-string "$val" --region $REGION >/dev/null && echo "updated $name"
  else
    aws secretsmanager create-secret --name "$name" --secret-string "$val" --region $REGION >/dev/null && echo "created $name"
  fi; }
[ -n "$ANTHROPIC" ] && put wandt/ANTHROPIC_API_KEY "$ANTHROPIC"
[ -n "$SERP" ] && put wandt/SERPAPI_KEY "$SERP"
# the exec role's inline policy lists secret ARNs explicitly — rewrite it with every wandt/* secret
ARNS=$(aws secretsmanager list-secrets --region $REGION --query 'SecretList[?starts_with(Name, `wandt/`)].ARN' --output json)
python3 - "$ARNS" <<'PY' > /tmp/wandt-read-secrets.json
import json,sys; arns=json.loads(sys.argv[1])
print(json.dumps({"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["secretsmanager:GetSecretValue"],"Resource":arns}]}))
PY
aws iam put-role-policy --role-name $ROLE --policy-name read-secrets --policy-document file:///tmp/wandt-read-secrets.json --region $REGION
echo "exec role can now read: $(echo "$ARNS" | tr -d '\n ')"
echo "Next: scripts/deploy.sh (it wires every wandt/* secret into the container automatically)."
