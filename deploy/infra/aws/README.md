# AWS ECS showcase (OpenTofu)

Managed Fargate stack for the Jobsifty demo. Hetzner stays on Helm/k8s; this path uses ECS Fargate, RDS Postgres, OpenSearch Service, and an ALB. Only one public stack should serve `jobsifty.com` at a time — flip Cloudflare DNS between Hetzner and AWS.

## 1. Prerequisites

- **OpenTofu** ≥ 1.10.1 and the **AWS CLI** configured with credentials for a **Paid AWS account** (Free Tier alone may block RDS, OpenSearch, or NAT).
- **Deployer IAM permissions.** `deployer-policy.example.json` is a least-privilege policy covering everything this stack creates. Fill in your account ID and attach it to a group (not directly to a user):

  ```bash
  cd deploy/infra/aws
  sed 's/<AWS_ACCOUNT_ID>/123456789012/g' \
    deployer-policy.example.json > deployer-policy.json   # gitignored
  ```

  `deployer-policy.json` is gitignored (same split as `terraform.tfvars.example` / `terraform.tfvars`), so your account ID never reaches the public repo. Keep the real ID out of the committed `.example` file.

  Then IAM → Policies → Create policy → JSON, and add the deploying user to a group carrying it. Replace `jobsifty` in the ARNs too if you changed `var.project`. IAM and S3 are scoped to this stack's own roles and dump bucket; the remaining services offer no useful resource-level scoping for the create/describe actions used here. Verify with `aws sts get-caller-identity`.
- **GHCR images** built for **`linux/amd64`** (GitHub Actions **Build and push images** workflow, or local `docker buildx build --platform linux/amd64`): frontend, backend, ingestion, and **`jobsifty-pgtools`** (demo dump/restore). Set `VITE_API_URL=https://api.<domain>` in `.env.public` before publishing the frontend image.
- **Cloudflare** API token with DNS edit access to the zone, plus the zone ID.
- Secrets ready for `terraform.tfvars` / `TF_VAR_*`: `cloudflare_api_token`, `groq_api_key`, and optional Adzuna and search (TinyFish/Exa/Tavily) keys.
- **GHCR pulls are anonymous by default**, matching the Helm path — public packages need no credentials. Only if the packages are private, set `ghcr_username` and `ghcr_token` (a PAT with `read:packages`); that wires `repositoryCredentials` and a Secrets Manager entry into every task definition.
- **OpenSearch service-linked role** — a VPC domain needs `AWSServiceRoleForAmazonOpenSearchService` in the account. On a fresh account the first apply fails with `ValidationException: Before you can proceed, you must enable a service-linked role`; AWS creates the role as a side effect of that failed call, so simply re-running the apply succeeds. To avoid the wasted cycle, create it up front:

  ```bash
  aws iam create-service-linked-role --aws-service-name opensearchservice.amazonaws.com
  ```

  It already exists if any OpenSearch domain has ever been created in the account. This is account-wide, not per-stack, so it is deliberately not managed by OpenTofu — `run aws down` leaves it in place.

## 2. Configure variables

From `deploy/infra/aws`:

```bash
cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars` (gitignored) with your domain, GHCR image tags, `alert_email`, `budget_limit_usd`, and Cloudflare zone ID. Put sensitive values in `terraform.tfvars` or export `TF_VAR_groq_api_key`, `TF_VAR_cloudflare_api_token`, etc.

## 3. First apply (`--no-dns`)

Use **`deploy/scripts/run aws up --no-dns`** for a trial apply while Hetzner is still serving production DNS. This creates the VPC, NAT, ALB, ACM validation records, RDS, OpenSearch, ECS services, EventBridge ingestion schedule, Secrets Manager entries, and a monthly cost budget — without repointing `app` / `api` / apex / `www`. Drop the flag once you want AWS to own the domain.

```bash
tofu init
tofu apply
```

Wait for ACM validation (Cloudflare records from `dns.tf`) and for ECS services to pass ALB health checks. Note the ALB hostname:

```bash
tofu output -raw alb_dns_name
```

**ALB health ≠ bootstrap done.** A green `/health` on the ALB only means the API container is up; migrations and OpenSearch index setup still require the one-shot bootstrap RunTask (section 4).

**Ingestion starts immediately.** The EventBridge ingestion schedule is created on this apply and begins firing every 2 hours. It posts to the internal Cloud Map address `http://api.<project>.local:8000`, so it always reaches **this** stack's API regardless of where public DNS points — it cannot write into Hetzner. Runs before the bootstrap RunTask (section 4) will fail against an unmigrated database; that is harmless and self-corrects once bootstrap has run.

## 4. Bootstrap (one-shot RunTask)

Run migrations and OpenSearch index setup before serving traffic. The bootstrap task uses the same private networking as ECS services (`assignPublicIp=DISABLED`).

From `deploy/infra/aws`:

```bash
SUBNETS=$(tofu output -json private_subnet_ids | jq -r 'join(",")')
SG=$(tofu output -raw ecs_tasks_security_group_id)

aws ecs run-task \
  --cluster "$(tofu output -raw ecs_cluster_name)" \
  --task-definition "$(tofu output -raw bootstrap_task_definition_arn)" \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[$SUBNETS],securityGroups=[$SG],assignPublicIp=DISABLED}"
```

Watch logs in CloudWatch under `/ecs/jobsifty/bootstrap` (or `/ecs/${project}/bootstrap` if you changed `var.project`). Re-run only when migrations or bootstrap logic change.

## 5. DNS flip to AWS

**Hetzner DNS coexistence.** Only one stack may own the records. `deploy/scripts/run hetzner down` destroys Hetzner's `app`/`api`/apex/`www` A records, which frees the names — do that before `run aws up`. If both stacks hold them, the second apply fails on a Cloudflare conflict (a CNAME cannot coexist with an A record at the same name) rather than flapping. `manage_dns` is passed by `run` as a `-var` in both stacks; the tfvars value is ignored.

**Stop or scale down Hetzner** so it is not still serving public traffic (Helm release scaled to zero, ingress removed, or node stopped — pick one; do not run both stacks publicly).

Then either:

**A. Script-managed flip (default)** — `deploy/scripts/run aws up`. Cloudflare `app`, `api`, apex, and `www` CNAME records point at the ALB.

**B. Manual Cloudflare flip** — bring the stack up with `run aws up --no-dns`, then create DNS-only CNAME records for `app`, `api`, apex, and `www` to `$(tofu output -raw alb_dns_name)` by hand.

Allow a few minutes for DNS TTL/propagation.

## 6. Smoke test

- Open **`https://app.jobsifty.com`** (or `https://app.<your-domain>`) and confirm the frontend loads.
- Hit the API, e.g. `curl -sS https://api.jobsifty.com/health` — expect `200`.
- Optional: register/login, run a job search, confirm worker reconcile logs in CloudWatch.

## 7. Flip DNS back to Hetzner

When the demo ends, repoint Cloudflare to the Hetzner ingress IP **before** or **as** you tear down AWS:

- `deploy/scripts/run aws down` here — this removes the ALB CNAMEs along with the stack.
- `deploy/scripts/run hetzner up` there — this re-creates the A records and brings Hetzner back to serving traffic.

## 8. Demo dump lifecycle (preferred up/down)

Same Postgres dump file as Hetzner (`dumps/jobsifty.current.dump`). Scripts
use Fargate + S3 (RDS stays private). They **do not** touch Cloudflare or Hetzner.

Also required: **`pgtools_image`** in `terraform.tfvars` — GHCR
`linux/amd64` image from **Build and push images** (`jobsifty-pgtools`), or:

```bash
docker buildx build --platform linux/amd64 \
  -t ghcr.io/OWNER/jobsifty-pgtools:TAG --push \
  deploy/infra/aws/docker/pgtools
```

From the repo root:

```bash
deploy/scripts/seed-dump --from compose   # if you do not already have a current dump
deploy/scripts/run aws                    # tofu apply → bootstrap → restore dump
# … use the demo (flip DNS separately if needed) …
deploy/scripts/run aws down               # dump → promote current → tofu destroy
```

`--yes` passes `-auto-approve` to tofu. Prefer `run aws down` over bare
`tofu destroy` when the demo holds data. Spec:
`docs/superpowers/specs/2026-08-13-aws-demo-dump-lifecycle-design.md`.

## 9. Destroy checklist

Prefer `deploy/scripts/run aws down`, which captures the dump and removes the
Cloudflare records with the stack. Bare destroy from `deploy/infra/aws`:

```bash
tofu destroy
```

Confirm removal of billable resources:

| Resource | Notes |
|----------|--------|
| **NAT Gateway** | Largest idle cost if left running |
| **ALB** | Requires two public subnets (two AZs) |
| **ECS** | Cluster, services, task definitions, EventBridge schedule |
| **RDS** | `skip_final_snapshot = true` for showcase — no final snapshot |
| **OpenSearch** | Single-node domain |
| **Elastic IP** | Attached to NAT |
| **S3 dump bucket** | `force_destroy = true` (demo staging) |
| **Secrets Manager** | `recovery_window_in_days = 0` — deleted immediately on destroy |
| **CloudWatch Logs** | Retention 7 days; small residual storage possible |

Re-check the AWS console for stray ENIs, EIPs, or OpenSearch domains if destroy errors mid-run.

## 10. Cost notes

- Most resources bill **hourly** (pro-rated). **`tofu destroy` stops the meter** for NAT, ALB, Fargate, RDS, and OpenSearch; tiny charges may linger for logs or RDS snapshots if you keep them.
- Expect roughly: NAT Gateway + ALB + Fargate tasks + small RDS + OpenSearch node — dominated by NAT and OpenSearch for short demos.
- A monthly **AWS Budget** (`var.budget_limit_usd`, default `$10`) emails **`var.alert_email`** at **80%** and **100%** of **ACTUAL** spend. Confirm the subscription email from AWS Budgets after first apply.
- Use **`run aws up --no-dns`** until you intentionally cut over; leaving the stack up without traffic still incurs infrastructure cost.

## Ingestion schedule

EventBridge Scheduler runs the ingestion task definition every 2 hours (`cron(0 */2 * * ? *)`), mirroring the local Helm CronJob cadence. The task definition already passes `--hours 2` and reads `OFFER_QUERY` from Secrets Manager; no container overrides are applied at schedule time.

**Ingestion targets this stack, not public DNS.** `BACKEND_URL=http://api.<project>.local:8000` (set in `ecs.tf`) is an internal Cloud Map address backed by `service_discovery.tf`, resolving to the `api` service's own tasks inside the VPC. This mirrors the Helm path, where ingestion posts to the in-cluster Service `http://api:8000`. Both stacks therefore ingest into their own database, and running AWS pre-flip cannot pollute Hetzner.

**Overlap risk:** unlike the Helm CronJob’s `concurrencyPolicy: Forbid`, EventBridge has **no overlap guard**. A new RunTask can start every 2 hours even if the previous ingestion task is still running. Long scrapes may overlap; watch ingestion logs and task counts in ECS if runs approach the 2-hour window.
