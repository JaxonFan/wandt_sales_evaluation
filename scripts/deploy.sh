#!/usr/bin/env bash
# One-command deploy of the growth service: source -> S3 -> CodeBuild -> ECR -> ECS rollout.
# Usage:  scripts/deploy.sh          (run it in the background; it prints progress and exits when live)
set -euo pipefail
REGION=us-east-1
BUCKET=s3://wandt-deploy-src-484907506213/src.zip
SERVICE=arn:aws:ecs:us-east-1:484907506213:service/default/wandt-growth-backtest
URL=https://wa-b5cabef5d7ee4d1dadf76e5d48ee22b4.ecs.us-east-1.on.aws
CONTAINER=/tmp/wandt-backtest-container.json

SHA=$(git rev-parse --short HEAD)
echo "[1/4] packaging HEAD ($SHA)"
git archive --format=zip -o /tmp/wandt-src.zip HEAD
printf '%s' "$SHA" > /tmp/VERSION          # baked into the image; /healthz echoes it back
(cd /tmp && zip -q wandt-src.zip VERSION)
aws s3 cp /tmp/wandt-src.zip "$BUCKET" --region $REGION >/dev/null

echo "[2/4] building image"
BUILD=$(aws codebuild start-build --project-name wandt-build --region $REGION --query 'build.id' --output text)
while :; do
  ST=$(aws codebuild batch-get-builds --ids "$BUILD" --region $REGION --query 'builds[0].buildStatus' --output text)
  [ "$ST" = "IN_PROGRESS" ] || break
  sleep 15
done
[ "$ST" = "SUCCEEDED" ] || { echo "BUILD $ST — see CodeBuild $BUILD"; exit 1; }

echo "[3/4] rolling the service"
# the primaryContainer block has to be sent back as-is; /tmp gets wiped between sessions, so rebuild it
aws ecs describe-express-gateway-service --service-arn "$SERVICE" --region $REGION > /tmp/wandt-gb-service.json
SECRETS=$(aws secretsmanager list-secrets --region $REGION --query 'SecretList[?starts_with(Name, `wandt/`)].[Name,ARN]' --output json)
echo "$SECRETS" > /tmp/wandt-secrets.json
python3 -c "
import json
pc=json.load(open('/tmp/wandt-gb-service.json'))['service']['activeConfigurations'][0]['primaryContainer']
# every wandt/<NAME> secret becomes the env var NAME (DATABASE_URL, SECRET_KEY, ANTHROPIC_API_KEY, SERPAPI_KEY, ...)
pc['secrets']=[{'name': n.split('/',1)[1], 'valueFrom': arn} for n, arn in json.load(open('/tmp/wandt-secrets.json'))]
json.dump(pc, open('$CONTAINER','w'), indent=2)
print('  secrets wired:', [s['name'] for s in pc['secrets']])"
aws ecs update-express-gateway-service --service-arn "$SERVICE" --primary-container "file://$CONTAINER" \
  --region $REGION --query 'service.status.statusCode' --output text

echo "[4/4] waiting for EVERY task to report build $SHA (/healthz echoes the commit it was built from)"
# a rolling deploy serves old and new tasks side by side for several minutes; only stop when 8 probes in a
# row come back with the new sha, which means the old tasks have drained.
for i in $(seq 1 40); do
  HITS=0
  for j in $(seq 1 8); do
    case "$(curl -s --max-time 10 "$URL/healthz")" in *"$SHA"*) HITS=$((HITS+1));; esac
  done
  echo "  $(date +%H:%M:%S)  $HITS/8 tasks on $SHA"
  [ "$HITS" -eq 8 ] && { echo "LIVE on $SHA: $URL"; exit 0; }
  sleep 20
done
echo "still rolling after ~13 min — check $URL/healthz"
exit 1
