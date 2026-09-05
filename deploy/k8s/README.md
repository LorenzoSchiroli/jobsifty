# Jobsifty on Kubernetes (Helm)

The stack deploys as a single Helm chart: `deploy/helm/jobsifty/`. The plain
manifests that previously lived here (Phase 1) were retired after the chart
took over; see git history and
`docs/superpowers/specs/2026-07-18-k8s-phase3-helm-design.md`. Only
`loadtest-job.yaml` (a demo/ops tool, not part of the app deploy) remains as a
plain manifest.

## AWS (ECS, not Helm)

This runbook covers Helm on Kubernetes (local kind, Hetzner, and other k8s
clusters). The AWS showcase does **not** use Helm or EKS: it runs on ECS
Fargate with RDS, OpenSearch Service, and an ALB, provisioned via OpenTofu in
[`deploy/infra/aws/`](../infra/aws/). See that README for apply, bootstrap
RunTask, and DNS flip from Hetzner.

## 0. One command

`deploy/scripts/run local` does everything in sections 1–2 — creates the kind
cluster, builds and side-loads the `:local` images, installs metrics-server,
creates the secret, and runs `helm upgrade --install`. It is idempotent, so
re-running it after a code change rebuilds and rolls out.

    deploy/scripts/run local          # up (default action)
    deploy/scripts/run local down     # capture dump, then delete the kind cluster

The same command targets the clouds: `run hetzner`, `run aws`. Targets are
independent and nothing checks whether another one is live — bring the previous
one down yourself.

The rest of this section documents what `run local` does, for when you want to
drive a step by hand.

## 1. Prerequisites

### Cluster + images

    kind create cluster --name jobsifty

    docker build -f backend/Dockerfile -t jobsifty-backend:local .
    docker build -f ingestion/Dockerfile -t jobsifty-ingestion:local .
    docker build -f frontend/Dockerfile \
      --build-arg VITE_API_URL=http://localhost:8000 \
      -t jobsifty-frontend:local ./frontend
    kind load docker-image jobsifty-backend:local jobsifty-ingestion:local jobsifty-frontend:local --name jobsifty

### Secret

The chart does **not** create the secret — it references one by name
(`existingSecret`, default `jobsifty-secrets`). Create it from the repo's
`.env` before installing.

> **Warning — no quotes in `.env` values.** `kubectl create secret
> --from-env-file` stores values **verbatim**: unlike docker-compose/dotenv it
> does NOT strip surrounding quotes. `GROQ_API_KEY="gsk_..."` becomes the
> literal value `"gsk_..."` (quotes included) and Groq rejects it with
> `401 invalid_api_key` — same for every other quoted value. Write
> `GROQ_API_KEY=gsk_...` unquoted, or strip quotes before creating the secret.

Due to kubectl version constraints, `--from-env-file` cannot be combined with
`--from-literal` in a single invocation. Create the secret in two steps. Pass
**both** env files: `.env.public` carries `GROQ_MODEL_*`, `OFFER_QUERY` and
`CORS_ORIGINS`, which the chart's `envFrom` expects alongside the secrets.

    kubectl create secret generic jobsifty-secrets \
      --from-env-file=.env.public \
      --from-env-file=.env

    kubectl patch secret jobsifty-secrets \
      --type merge \
      -p '{"stringData":{"DATABASE_URL":"postgresql+asyncpg://postgres:postgres@postgres:5432/jobsifty","OPENSEARCH_URL":"http://opensearch:9200","BACKEND_URL":"http://api:8000"}}'

To pick up `.env` changes: `kubectl delete secret jobsifty-secrets`, re-run
both commands, then `kubectl rollout restart deployment api worker`.

### metrics-server (required by the API HPA)

    kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
    kubectl patch deployment metrics-server -n kube-system --type='json' \
      -p='[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]'
    kubectl rollout status deployment metrics-server -n kube-system --timeout=120s

`--kubelet-insecure-tls` is a **kind-only** workaround (kind's kubelet serving
cert isn't signed by the cluster CA). Real clusters — EKS/GKE/bare metal — must
NOT use it.

## 2. Deploy

    helm install jobsifty deploy/helm/jobsifty -f deploy/helm/jobsifty/values-local.yaml

The `jobsifty-bootstrap` post-install hook runs migrations, creates the
OpenSearch index, and sets up checkpointer tables automatically. On a fresh
cluster its first attempts may crash while Postgres/OpenSearch are still
starting — that is the `backoffLimit: 6` retry budget working, not a problem.

Upgrade after chart/values changes (the bootstrap hook re-runs pre-upgrade, so
migrations apply before new code rolls out):

    helm upgrade jobsifty deploy/helm/jobsifty -f deploy/helm/jobsifty/values-local.yaml

Uninstall (PVCs — and therefore all Postgres/OpenSearch data — survive):

    helm uninstall jobsifty

## 3. Access

    kubectl port-forward svc/api 8000:8000 &
    kubectl port-forward svc/frontend 3000:80 &

Open http://localhost:3000 — matches docker-compose's ports, so the frontend's
baked-in VITE_API_URL and the backend's CORS allow-list work unchanged.

## 4. Changing config

Edit `values.yaml` / `values-local.yaml` (or use `--set`) and `helm upgrade`.
Examples:

    # pause ingestion
    helm upgrade jobsifty deploy/helm/jobsifty \
      -f deploy/helm/jobsifty/values-local.yaml --set ingestion.suspend=true

    # widen the HPA ceiling
    helm upgrade jobsifty deploy/helm/jobsifty \
      -f deploy/helm/jobsifty/values-local.yaml --set hpa.maxReplicas=6

Notable values: `existingSecret`, `hpa.minReplicas/maxReplicas/targetCPUUtilization`,
`ingestion.schedule/suspend/activeDeadlineSeconds`, per-service `image.repository/tag`
and `resources`, `postgres.storage`, `opensearch.storage`.

## 5. HPA load demo

Watch in two panes:

    kubectl get hpa api -w
    kubectl get pods -l app=api -w

Fire the in-cluster k6 load Job (ramps 0→20→50 virtual users against
`POST /jobs/search`). Base search is LLM-free — regex query parsing, then
embedding + cross-encoder reranking supply the CPU load — so no Groq quota is
consumed. The script registers a throwaway `loadtest-*` user for the JWT the
endpoint requires:

    kubectl apply -f deploy/k8s/loadtest-job.yaml

Replicas climb 1 → up to 4 past 70% CPU, then settle back to 1 a few minutes
after the load ends (~5 min scale-down stabilization). Clean up:

    kubectl delete -f deploy/k8s/loadtest-job.yaml

## Hetzner amd64 images

Prefer GitHub Actions over a laptop build. The workflow
`.github/workflows/build-push-images.yml` builds backend, ingestion, and
frontend for `linux/amd64` and pushes to GHCR.

One-time setup:

1. Set `VITE_API_URL` in `.env.public` to the public API base URL
   (e.g. `https://api.<your-domain>`). Commit that change before publishing.
2. Ensure GHCR packages will be pullable by the cluster (public packages, or a
   pull secret via `values-private.yaml`)

Publish:

1. Actions → **Build and push images** → Run workflow
2. Wait until all three build jobs are green (re-run if any leg failed before
   upgrading the cluster)
3. Each package is tagged `latest` and a 7-character git SHA; older SHA tags
   are pruned so at most two SHA tags remain per package (`latest` is kept).
   Untagged digests may linger until GHCR garbage-collects them.

Cluster pull of `latest` requires `imagePullPolicy: Always` (already set in
`values-cloud.yaml`). After a successful publish, `helm upgrade` (or a
rollout restart) picks up the new digest. To roll back, temporarily set the
image `tag` to a retained short SHA.

### Laptop fallback

Build and push from the repository root only if Actions is unavailable.
Replace `OWNER`, `TAG`, and `api.example.com`:

    docker login ghcr.io

    docker buildx build --platform linux/amd64 \
      -f backend/Dockerfile \
      -t ghcr.io/OWNER/jobsifty-backend:TAG --push .

    docker buildx build --platform linux/amd64 \
      -f ingestion/Dockerfile \
      -t ghcr.io/OWNER/jobsifty-ingestion:TAG --push .

    docker buildx build --platform linux/amd64 \
      -f frontend/Dockerfile \
      --build-arg VITE_API_URL=https://api.example.com \
      -t ghcr.io/OWNER/jobsifty-frontend:TAG --push ./frontend

The backend image includes `postgresql-client` and `rclone` for the worker's
nightly Postgres backup loop. No separate postgres-backup image is required.

The frontend API URL is embedded at build time from `.env.public`. Update that
file (and the laptop `--build-arg` if you use the fallback) and rebuild when
the public API hostname changes.

If packages under `ghcr.io/OWNER/` are private, create a pull secret and attach
it through `values-private.yaml` (or make the packages public for the
portfolio demo). Public packages need no `imagePullSecrets`.

## Hetzner application Secret

Create the application secret after OpenTofu has produced a kubeconfig. Merge
committed `.env.public` with local secrets `.env`. Values must remain unquoted
because `kubectl --from-env-file` preserves quotes.

    export KUBECONFIG=/path/to/jobsifty-kubeconfig
    # Later file wins on duplicate keys; keep secrets in `.env`.
    kubectl create secret generic jobsifty-secrets \
      --from-env-file=.env.public \
      --from-env-file=.env

Create the rclone password value once:

    rclone obscure "$BACKUP_SBOX_PASSWORD"

Patch the cluster-local URLs, public CORS origin, and Storage Box values.
`BACKUP_SBOX_PATH` is relative (no leading slash). `BACKUP_SBOX_RCLONE_PASS` is
the obscured value, not the raw password:

    kubectl patch secret jobsifty-secrets --type merge -p \
      '{"stringData":{
        "DATABASE_URL":"postgresql+asyncpg://postgres:postgres@postgres:5432/jobsifty",
        "OPENSEARCH_URL":"http://opensearch:9200",
        "BACKEND_URL":"http://api:8000",
        "CORS_ORIGINS":"https://app.example.com",
        "BACKUP_SBOX_HOST":"uXXXXX.your-storagebox.de",
        "BACKUP_SBOX_USER":"uXXXXX",
        "BACKUP_SBOX_RCLONE_PASS":"rclone-obscured-password-goes-here",
        "BACKUP_SBOX_PATH":"backups/jobsifty"
      }}'

## Hetzner Helm deployment

Cloud deploys layer portable settings, then provider knobs, then a gitignored
private overlay:

1. `values.yaml` — kind-safe chart defaults
2. `values-cloud.yaml` — portable cloud/demo settings (CSI, scheduling, ingress shape, placeholder images)
3. `values-hetzner.yaml` — Hetzner-only (`storageClass: hcloud-volumes`)
4. `values-private.yaml` — real image repos, hostnames, Let's Encrypt email (repo root, gitignored)

A future provider adds `values-<provider>.yaml` instead of step 3; `values-cloud.yaml` stays shared.

Create an ignored `values-private.yaml` that overrides the safe example
image repository, image tag (`latest`, or a retained short SHA for rollback), hostname, and Let's Encrypt email:

```yaml
bootstrap:
  image: { repository: ghcr.io/OWNER/jobsifty-backend, tag: latest }
api:
  image: { repository: ghcr.io/OWNER/jobsifty-backend, tag: latest }
worker:
  image: { repository: ghcr.io/OWNER/jobsifty-backend, tag: latest }
ingestion:
  image: { repository: ghcr.io/OWNER/jobsifty-ingestion, tag: latest }
frontend:
  image: { repository: ghcr.io/OWNER/jobsifty-frontend, tag: latest }
ingress:
  frontendHost: app.example.com
  apiHost: api.example.com
certManager:
  email: ops@example.com
```

Then deploy:

    helm lint deploy/helm/jobsifty \
      -f deploy/helm/jobsifty/values.yaml \
      -f deploy/helm/jobsifty/values-cloud.yaml \
      -f deploy/helm/jobsifty/values-hetzner.yaml \
      -f values-private.yaml

    helm upgrade --install jobsifty deploy/helm/jobsifty \
      -f deploy/helm/jobsifty/values.yaml \
      -f deploy/helm/jobsifty/values-cloud.yaml \
      -f deploy/helm/jobsifty/values-hetzner.yaml \
      -f values-private.yaml

    kubectl get pods -o wide
    kubectl get ingress,clusterissuer,certificate
    curl --fail --silent --show-error https://api.example.com/health
    curl --fail --silent --show-error --output /dev/null https://app.example.com/

Start with `certManager.issuerName: letsencrypt-staging`. After the staging
certificate works, delete the issued Certificate/Secret pair for each host,
set `issuerName` to `letsencrypt-prod`, and run `helm upgrade`. Leaving the
staging Secret in place prevents the production issuer from replacing it.

## Demo dump lifecycle

For bring-up / tear-down without losing Postgres, use the single-file dump
`dumps/jobsifty.current.dump` (shared across local and the clouds):

**Hetzner** (`deploy/infra/hetzner/README.md`):

- `deploy/scripts/seed-dump` — create the current dump
- `deploy/scripts/run hetzner` / `run hetzner down` — Helm/k8s path

**AWS** (`deploy/infra/aws/README.md`):

- `deploy/scripts/run aws` / `run aws down` — ECS/RDS via Fargate + S3

Prefer `run <target> down` over bare `tofu destroy` when the demo holds data.

## Backup and restore drill

The worker runs a backup shortly after start when `BACKUP_SBOX_*` is set, then
every `BACKUP_INTERVAL_SECONDS` (default 86400). Confirm a dump landed:

    kubectl logs -l app=worker --tail=100 | grep -i backup

To restore, download a selected `.dump` from the Storage Box, then restore into
a freshly recreated Postgres PVC only:

    kubectl scale statefulset/postgres --replicas=0
    kubectl delete pvc data-postgres-0
    kubectl scale statefulset/postgres --replicas=1
    kubectl wait --for=condition=Ready pod/postgres-0 --timeout=180s
    kubectl cp selected.dump postgres-0:/tmp/restore.dump
    kubectl exec postgres-0 -- \
      pg_restore -U postgres -d jobsifty --clean --if-exists --no-owner /tmp/restore.dump

Verify row counts and foreign keys after restoring:

    kubectl exec postgres-0 -- psql -U postgres -d jobsifty -c \
      "SELECT count(*) AS orphaned_jobs
       FROM jobs j LEFT JOIN companies c ON c.id = j.company_id
       WHERE c.id IS NULL;"

## OpenSearch recovery

OpenSearch is derived from Postgres. To test recovery, delete only its PVC:

    kubectl scale statefulset/opensearch --replicas=0
    kubectl delete pvc data-opensearch-0
    kubectl scale statefulset/opensearch --replicas=1
    kubectl wait --for=condition=Ready pod/opensearch-0 --timeout=300s
    kubectl rollout restart deployment/worker

Wait for the reconcile interval, then verify a search returns jobs. Do not
restore OpenSearch from a backup.

## Autoscaling verification

Watch the HPA, pods, nodes, and autoscaler while applying the Hetzner k6 Job:

    kubectl get hpa api -w
    kubectl get pods -o wide -w
    kubectl get nodes -w
    kubectl -n kube-system logs deployment/cluster-autoscaler -f
    kubectl apply -f deploy/k8s/loadtest-job-hetzner.yaml

Record p50/p95 latency, error rate, API CPU/memory, HPA transitions, Pending
duration, worker provisioning time, and worker scale-down time. Use those
measurements to revise API resource requests, `hpa.maxReplicas`, and the
autoscaler pool maximum. Do not treat the initial 1–4 pods and 0–2 nodes as
capacity claims.
