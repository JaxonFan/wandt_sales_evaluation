# Deployment — W&T Sales Scorecard (AWS, us-east-1)

Live URL: **https://wa-1689405f24e2438ba20406d4b16ed235.ecs.us-east-1.on.aws**
(demo logins — **rotate before real use**: managers `manager/demo123`, `admin/demo123`;
read-only **rep logins** `an` / `garmi` / `ting` / `vanessa` / `wendy` (all `demo123`) →
each lands on their own `/me` goal page. Metric model: see `wandt_incentive_design.md`.)

## Architecture
Public HTTPS via **Amazon ECS Express Mode** (Fargate + managed ALB/SSL + autoscaling). The container image
is built in the cloud by **CodeBuild** (no local Docker) and stored in **ECR**. App config/secrets come from
**Secrets Manager**; data lives in a dedicated **RDS Postgres** (private, SG-gated). Mirrors the Coreline stack
as a parallel `wandt-*` deployment in the same account.

```
GitHub (code) ──► S3 src.zip ──► CodeBuild ──► ECR image ──► ECS Express (Fargate)
                                                                 │  ALB + HTTPS (public)
                                                                 │  secrets: DATABASE_URL, SECRET_KEY
                                                                 ▼
                                                            RDS Postgres (wandt-db, private, SG-gated)
```

## Resources (all us-east-1, account 484907506213)
| Thing | Identifier |
|---|---|
| ECS Express service | `wandt-dashboard` (cluster `default`) — ARN `…service/default/wandt-dashboard` |
| Public endpoint | `wa-1689405f24e2438ba20406d4b16ed235.ecs.us-east-1.on.aws` |
| Container image | `484907506213.dkr.ecr.us-east-1.amazonaws.com/wandt-app:latest` |
| RDS Postgres | `wandt-db` · `wandt-db.c34o48e80ep5.us-east-1.rds.amazonaws.com:5432` · db `scorecard` · user `wandt` |
| Secrets | `wandt/DATABASE_URL`, `wandt/SECRET_KEY` |
| Build | CodeBuild project `wandt-build`; source bucket `wandt-deploy-src-484907506213` |
| IAM roles | exec `wandt-ecs-exec` · infra `wandt-ecs-infra` · build `wandt-codebuild` |
| Security groups | app/task `sg-0155d12d1edba6458` + `sg-0ce0be5b1588bec2f` (shared w/ Coreline) · rds `wandt-rds-sg` `sg-028dcc2841fd8f234` (5432 from the app SG only) |
| Sizing | 1 vCPU / 2 GB Fargate, autoscale 1–20 tasks at 60% CPU |
| Logs | CloudWatch `/ecs/wandt` |

## Redeploy after a code change
```bash
cd "<project>"
git archive --format=zip -o /tmp/wandt-src.zip HEAD      # or: zip -r /tmp/wandt-src.zip app Dockerfile requirements.txt buildspec.yml
aws s3 cp /tmp/wandt-src.zip s3://wandt-deploy-src-484907506213/src.zip --region us-east-1
aws codebuild start-build --project-name wandt-build --region us-east-1   # builds + pushes :latest
# force the service to pull the new image (primary-container.json = the primaryContainer block from the service config):
aws ecs update-express-gateway-service \
  --service-arn arn:aws:ecs:us-east-1:484907506213:service/default/wandt-dashboard \
  --primary-container file:///tmp/wandt-primary-container.json --region us-east-1
```
Note: pass CodeBuild/ECS JSON **inline or via a clean file** — heredoc-built temp files can mangle values.
Blue/green rollout takes a few minutes; the URL stays up throughout.

## Update the data
- **Routine:** log in as manager → **Import** → upload an item-level invoice XLSX. Idempotent by SOP number;
  only the 5 sales reps' lines are stored.
- **Full reseed from local:** temporarily open RDS to your IP, then run the loader:
  ```bash
  MYIP=$(curl -s https://checkip.amazonaws.com)
  aws ec2 authorize-security-group-ingress --group-id sg-028dcc2841fd8f234 --protocol tcp --port 5432 --cidr $MYIP/32 --region us-east-1
  DATABASE_URL="postgresql+psycopg2://wandt:<DB_PASS>@wandt-db.c34o48e80ep5.us-east-1.rds.amazonaws.com:5432/scorecard" \
    sales_evaluation/bin/python -m app.load_history
  aws ec2 revoke-security-group-ingress --group-id sg-028dcc2841fd8f234 --protocol tcp --port 5432 --cidr $MYIP/32 --region us-east-1
  ```
  (`<DB_PASS>` is inside the `wandt/DATABASE_URL` secret. `load_history` reads `sales_data/*.XLSX` + the roster locally.)

## Operational notes
- **Cost:** ~$30–40/month (1 always-on Fargate task + ALB + RDS t4g.micro + small data transfer).
- **Metric:** **revenue**-based, paid per 4-week period as three direct pieces — Contribution (line items ×
  $0.10), Growth (1% of revenue above a per-account bar: cost-adjusted same 4 weeks last year × size-band
  de-trend for established accounts, or the account's adaptive **glide** run-rate for newer/level-shifted ones),
  and Acquisition (a **flat $50/$100/$150 by size**, paid once at the ~quarter mark, for a self-acquired new
  account). A single-period **doubling** (≥2× its normal level) is withheld for the manager's `/jumps` review;
  **growth doesn't count if the account's quarter is down >5% YoY** (quarter-health gate); and **infrequent
  accounts (median order gap ≥ 4 weeks) are scored on a rolling annual track** (12-mo-vs-prior, paid once a
  year). Scope = the 5 sales reps (managers/inactive excluded). See `wandt_incentive_design.md`.
- **Performance:** each page recomputes the engine over ~102k item-level lines (cached per period until import/override).
- **Security TODO:** rotate the demo logins; RDS is SG-gated but `publicly-accessible` — to drop its public IP:
  `aws rds modify-db-instance --db-instance-identifier wandt-db --no-publicly-accessible --apply-immediately`.

## Tear down (delete everything / stop billing)
```bash
aws ecs delete-express-gateway-service --service-arn arn:aws:ecs:us-east-1:484907506213:service/default/wandt-dashboard --region us-east-1
aws rds delete-db-instance --db-instance-identifier wandt-db --skip-final-snapshot --delete-automated-backups --region us-east-1
aws ecr delete-repository --repository-name wandt-app --force --region us-east-1
aws secretsmanager delete-secret --secret-id wandt/DATABASE_URL --force-delete-without-recovery --region us-east-1
aws secretsmanager delete-secret --secret-id wandt/SECRET_KEY --force-delete-without-recovery --region us-east-1
aws codebuild delete-project --name wandt-build --region us-east-1
aws s3 rb s3://wandt-deploy-src-484907506213 --force
aws ec2 delete-security-group --group-id sg-028dcc2841fd8f234 --region us-east-1
# then IAM roles: wandt-ecs-exec, wandt-ecs-infra, wandt-codebuild
```
