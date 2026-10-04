#!/usr/bin/env bash
# One-time: store the assistant's API keys in Secrets Manager and let the ECS exec role read them.
# Run it yourself (it touches IAM + secrets):   ! scripts/setup_agent_secrets.sh
# It prompts for the keys; leave any blank to skip. Gemini is the default backbone.
set -euo pipefail
REGION=us-east-1; ROLE=wandt-ecs-exec
read -rsp "GEMINI_API_KEY (the assistant's backbone): " GEMINI; echo
read -rsp "ANTHROPIC_API_KEY (optional, only if ASSISTANT_BACKBONE=claude): " ANTHROPIC; echo
read -rsp "SERPAPI_KEY (optional, web search): " SERP; echo
put() { local name=$1 val=$2
  if aws secretsmanager describe-secret --secret-id "$name" --region $REGION >/dev/null 2>&1; then
    aws secretsmanager put-secret-value --secret-id "$name" --secret-string "$val" --region $REGION >/dev/null && echo "updated $name"
  else
    aws secretsmanager create-secret --name "$name" --secret-string "$val" --region $REGION >/dev/null && echo "created $name"
  fi; }
[ -n "$GEMINI" ] && put wandt/GEMINI_API_KEY "$GEMINI"
[ -n "$ANTHROPIC" ] && put wandt/ANTHROPIC_API_KEY "$ANTHROPIC"
[ -n "$SERP" ] && put wandt/SERPAPI_KEY "$SERP"
# the exec role's inline policy lists secret ARNs explicitly — rewrite it with every wandt/* secret.
# list-secrets is eventually consistent: wait until a just-created secret shows up before writing the policy.
EXPECT=$(aws secretsmanager list-secrets --region $REGION --query 'length(SecretList[?starts_with(Name, `wandt/`)])' --output text)
for i in 1 2 3 4 5 6; do
  ARNS=$(aws secretsmanager list-secrets --region $REGION --query 'SecretList[?starts_with(Name, `wandt/`)].ARN' --output json)
  if { [ -z "$GEMINI" ] || echo "$ARNS" | grep -q GEMINI_API_KEY; } && { [ -z "$ANTHROPIC" ] || echo "$ARNS" | grep -q ANTHROPIC_API_KEY; } && { [ -z "$SERP" ] || echo "$ARNS" | grep -q SERPAPI_KEY; }; then break; fi
  echo "  waiting for the new secret to be listed…"; sleep 5
done
python3 - "$ARNS" <<'PY' > /tmp/wandt-read-secrets.json
import json,sys; arns=json.loads(sys.argv[1])
print(json.dumps({"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["secretsmanager:GetSecretValue"],"Resource":arns}]}))
PY
aws iam put-role-policy --role-name $ROLE --policy-name read-secrets --policy-document file:///tmp/wandt-read-secrets.json --region $REGION
echo "exec role can now read: $(echo "$ARNS" | tr -d '\n ')"
echo "Next: scripts/deploy.sh (it wires every wandt/* secret into the container automatically)."
