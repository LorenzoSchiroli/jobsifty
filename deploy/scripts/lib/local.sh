# Local kind + Helm target. Source after lib/common.sh.
# shellcheck shell=bash

KIND_CLUSTER="${KIND_CLUSTER:-jobsifty}"
HELM_RELEASE="${HELM_RELEASE:-jobsifty}"
CHART_DIR="${REPO_ROOT}/deploy/helm/jobsifty"

_local_use_kind_context() {
  # kind writes into KUBECONFIG when it is set; unsetting it keeps local work
  # in ~/.kube/config and out of the Hetzner kubeconfig entirely.
  unset KUBECONFIG
  KUBECTL_CONTEXT="kind-${KIND_CLUSTER}"
}

_local_require_docker_daemon() {
  # The binary existing is not enough: kind, docker build and kind load all
  # need a live daemon, and each fails with its own opaque message.
  if ! docker info >/dev/null 2>&1; then
    echo "error: docker daemon is not running" >&2
    echo "hint: start Docker Desktop, then re-run" >&2
    exit 1
  fi
}

_local_ensure_cluster() {
  if kind get clusters 2>/dev/null | grep -qx "${KIND_CLUSTER}"; then
    echo "kind cluster ${KIND_CLUSTER} already exists"
    return
  fi
  echo "==> kind create cluster --name ${KIND_CLUSTER}"
  kind create cluster --name "${KIND_CLUSTER}"
}

_local_build_images() {
  echo "==> building :local images"
  docker build -f "${REPO_ROOT}/backend/Dockerfile" \
    -t jobsifty-backend:local "${REPO_ROOT}"
  docker build -f "${REPO_ROOT}/ingestion/Dockerfile" \
    -t jobsifty-ingestion:local "${REPO_ROOT}"
  # localhost:8000 matches the port-forward below and compose's CORS allow-list.
  docker build -f "${REPO_ROOT}/frontend/Dockerfile" \
    --build-arg VITE_API_URL=http://localhost:8000 \
    -t jobsifty-frontend:local "${REPO_ROOT}/frontend"

  echo "==> kind load docker-image"
  kind load docker-image \
    jobsifty-backend:local \
    jobsifty-ingestion:local \
    jobsifty-frontend:local \
    --name "${KIND_CLUSTER}"
}

_local_ensure_metrics_server() {
  if kctl get deploy metrics-server -n kube-system >/dev/null 2>&1; then
    echo "metrics-server already installed"
    return
  fi
  echo "==> installing metrics-server (required by the API HPA)"
  kctl apply -f \
    https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
  # kind-only: kind's kubelet serving cert is not signed by the cluster CA.
  # Applied once, on install, so the arg is never appended twice.
  kctl patch deployment metrics-server -n kube-system --type='json' \
    -p='[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]'
  kctl rollout status deployment metrics-server -n kube-system --timeout=120s
}

local_up() {
  require_cmd docker
  require_cmd kind
  require_cmd kubectl
  require_cmd helm
  require_pg_client

  _local_require_docker_daemon
  _local_use_kind_context
  _local_ensure_cluster
  _local_build_images
  _local_ensure_metrics_server
  ensure_secret

  echo "==> helm upgrade --install"
  helm --kube-context "${KUBECTL_CONTEXT}" upgrade --install "${HELM_RELEASE}" "${CHART_DIR}" \
    -f "${CHART_DIR}/values.yaml" \
    -f "${CHART_DIR}/values-local.yaml"

  echo "==> waiting for ${POSTGRES_POD}"
  kctl wait --for=condition=Ready "pod/${POSTGRES_POD}" --timeout=300s

  echo "==> waiting for bootstrap Job"
  if kctl get job "${HELM_RELEASE}-bootstrap" >/dev/null 2>&1; then
    kctl wait --for=condition=complete "job/${HELM_RELEASE}-bootstrap" --timeout=600s
  fi

  # Same canonical dump the cloud targets restore, so local matches the demo.
  local api_replicas worker_replicas ingestion_suspend
  api_replicas="$(kctl get deploy api -o jsonpath='{.spec.replicas}' 2>/dev/null || echo 1)"
  worker_replicas="$(kctl get deploy worker -o jsonpath='{.spec.replicas}' 2>/dev/null || echo 1)"
  ingestion_suspend="$(kctl get cronjob ingestion -o jsonpath='{.spec.suspend}' 2>/dev/null || echo false)"
  [[ -n "${api_replicas}" ]] || api_replicas=1
  [[ -n "${worker_replicas}" ]] || worker_replicas=1
  [[ -n "${ingestion_suspend}" ]] || ingestion_suspend=false

  echo "==> pausing writers (api=${api_replicas}, worker=${worker_replicas}, ingestion.suspend=${ingestion_suspend})"
  kctl scale deploy/api deploy/worker --replicas=0
  kctl patch cronjob ingestion --type merge -p '{"spec":{"suspend":true}}'
  kctl wait --for=delete pod -l app=api --timeout=180s 2>/dev/null || true
  kctl wait --for=delete pod -l app=worker --timeout=180s 2>/dev/null || true

  local restore_ok=0
  echo "==> restoring ${CURRENT_DUMP} into ${POSTGRES_POD}"
  if cluster_pg_restore_from "${CURRENT_DUMP}"; then
    restore_ok=1
  fi

  if [[ "${restore_ok}" -ne 1 ]]; then
    cat >&2 <<RECOVERY
error: pg_restore failed; leaving api/worker scaled to 0 and ingestion suspended.
recovery:
  # fix dump / retry:
  kubectl --context ${KUBECTL_CONTEXT} cp ${CURRENT_DUMP} ${POSTGRES_POD}:${POD_DUMP_PATH}
  kubectl --context ${KUBECTL_CONTEXT} exec ${POSTGRES_POD} -- pg_restore -U postgres -d ${POSTGRES_DB} --clean --if-exists --no-owner ${POD_DUMP_PATH}
  # then resume:
  kubectl --context ${KUBECTL_CONTEXT} scale deploy/api --replicas=${api_replicas}
  kubectl --context ${KUBECTL_CONTEXT} scale deploy/worker --replicas=${worker_replicas}
  kubectl --context ${KUBECTL_CONTEXT} patch cronjob ingestion --type merge -p '{"spec":{"suspend":${ingestion_suspend}}}'
RECOVERY
    exit 1
  fi

  echo "==> resuming writers"
  kctl scale deploy/api --replicas="${api_replicas}"
  kctl scale deploy/worker --replicas="${worker_replicas}"
  kctl patch cronjob ingestion --type merge -p "{\"spec\":{\"suspend\":${ingestion_suspend}}}"

  cat <<HINT

local up complete. Port-forward to reach the stack:

  kubectl --context ${KUBECTL_CONTEXT} port-forward svc/api 8000:8000 &
  kubectl --context ${KUBECTL_CONTEXT} port-forward svc/frontend 3000:80 &

then open http://localhost:3000
HINT
}

local_down() {
  require_cmd kubectl
  require_cmd kind
  require_pg_client

  _local_use_kind_context
  if ! kctl cluster-info >/dev/null 2>&1; then
    echo "kind cluster ${KIND_CLUSTER} is not running; nothing to tear down"
    return
  fi

  # Symmetry with the cloud targets: the local DB is promoted to the canonical
  # dump before teardown, so whatever ran here is what the next deploy restores.
  # A release already uninstalled leaves no postgres pod; skip rather than abort.
  if kctl get pod "${POSTGRES_POD}" >/dev/null 2>&1; then
    local tmp
    tmp="$(mktemp_dump)"
    trap 'rm -f "${tmp}"' EXIT

    echo "==> dumping ${POSTGRES_POD} -> temp"
    cluster_pg_dump_to "${tmp}"

    echo "==> validating and promoting"
    promote_dump "${tmp}"
    trap - EXIT
  else
    echo "no ${POSTGRES_POD} pod; skipping capture (canonical dump left unchanged)"
  fi

  # local_up creates the cluster, so down removes it: the kind analogue of the
  # cloud targets' tofu destroy. Deleting the cluster takes the release and the
  # PVCs with it, so a separate helm uninstall would only add time.
  echo "==> kind delete cluster --name ${KIND_CLUSTER}"
  kind delete cluster --name "${KIND_CLUSTER}"

  cat <<HINT

local down complete. Canonical dump: ${CURRENT_DUMP}
The cluster, its PVCs and the :local images' node copies are gone.
Rebuild with: deploy/scripts/run local
HINT
}
