# Hetzner (OpenTofu + Helm) target. Source after lib/common.sh.
# shellcheck shell=bash

hetzner_up() {
  require_cmd tofu
  require_cmd helm
  require_cmd kubectl
  require_cmd jq
  require_pg_client

  if [[ ! -f "${CURRENT_DUMP}" ]]; then
    echo "error: missing ${CURRENT_DUMP}" >&2
    echo "hint: run deploy/scripts/seed-dump first" >&2
    exit 1
  fi
  validate_dump "${CURRENT_DUMP}"

  local private_values="${REPO_ROOT}/values-private.yaml"
  if [[ ! -f "${private_values}" ]]; then
    echo "error: missing ${private_values}" >&2
    echo "hint: see deploy/k8s/README.md (Hetzner Helm deployment)" >&2
    exit 1
  fi

  # The target that is up owns DNS, so the flag is driven by the command line
  # rather than each tfvars: --no-dns is the only way to bring a stack up
  # without repointing Cloudflare at it.
  local dns_flag=false
  if [[ "${MANAGE_DNS:-1}" -eq 1 ]]; then
    dns_flag=true
  fi

  local -a tofu_args=(apply -var "manage_dns=${dns_flag}")
  if [[ "${AUTO_APPROVE:-0}" -eq 1 ]]; then
    tofu_args+=(-auto-approve)
  fi

  echo "==> tofu apply (${HETZNER_DIR})"
  (
    cd "${HETZNER_DIR}"
    tofu "${tofu_args[@]}"
  )

  ensure_kubeconfig
  echo "Using KUBECONFIG=${KUBECONFIG}"

  ensure_secret

  echo "==> helm upgrade --install"
  helm upgrade --install jobsifty "${REPO_ROOT}/deploy/helm/jobsifty" \
    -f "${REPO_ROOT}/deploy/helm/jobsifty/values.yaml" \
    -f "${REPO_ROOT}/deploy/helm/jobsifty/values-cloud.yaml" \
    -f "${REPO_ROOT}/deploy/helm/jobsifty/values-hetzner.yaml" \
    -f "${private_values}"

  echo "==> waiting for postgres-0"
  kubectl wait --for=condition=Ready "pod/${POSTGRES_POD}" --timeout=300s

  echo "==> waiting for bootstrap Job"
  # Helm post-install usually finishes the hook first; verify for re-runs / races.
  if kubectl get job jobsifty-bootstrap >/dev/null 2>&1; then
    kubectl wait --for=condition=complete job/jobsifty-bootstrap --timeout=600s
  fi

  local api_replicas worker_replicas ingestion_suspend
  api_replicas="$(kubectl get deploy api -o jsonpath='{.spec.replicas}' 2>/dev/null || echo 1)"
  worker_replicas="$(kubectl get deploy worker -o jsonpath='{.spec.replicas}' 2>/dev/null || echo 1)"
  ingestion_suspend="$(kubectl get cronjob ingestion -o jsonpath='{.spec.suspend}' 2>/dev/null || echo false)"
  [[ -n "${api_replicas}" ]] || api_replicas=1
  [[ -n "${worker_replicas}" ]] || worker_replicas=1
  [[ -n "${ingestion_suspend}" ]] || ingestion_suspend=false

  echo "==> pausing writers (api=${api_replicas}, worker=${worker_replicas}, ingestion.suspend=${ingestion_suspend})"
  kubectl scale deploy/api deploy/worker --replicas=0
  kubectl patch cronjob ingestion --type merge -p '{"spec":{"suspend":true}}'
  # Wait until pods are gone so restore is not racing live connections.
  kubectl wait --for=delete pod -l app=api --timeout=180s 2>/dev/null || true
  kubectl wait --for=delete pod -l app=worker --timeout=180s 2>/dev/null || true

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
  kubectl cp ${CURRENT_DUMP} ${POSTGRES_POD}:${POD_DUMP_PATH}
  kubectl exec ${POSTGRES_POD} -- pg_restore -U postgres -d jobsifty --clean --if-exists --no-owner ${POD_DUMP_PATH}
  # then resume:
  kubectl scale deploy/api --replicas=${api_replicas}
  kubectl scale deploy/worker --replicas=${worker_replicas}
  kubectl patch cronjob ingestion --type merge -p '{"spec":{"suspend":${ingestion_suspend}}}'
RECOVERY
    exit 1
  fi

  if ! rerun_bootstrap_job jobsifty-bootstrap; then
    echo "error: migrations after the restore failed; api/worker stay at 0 and ingestion suspended." >&2
    echo "inspect: kubectl logs job/<the job above>; then scale api/worker back up" >&2
    exit 1
  fi

  echo "==> resuming writers"
  kubectl scale deploy/api --replicas="${api_replicas}"
  kubectl scale deploy/worker --replicas="${worker_replicas}"
  kubectl patch cronjob ingestion --type merge -p "{\"spec\":{\"suspend\":${ingestion_suspend}}}"
  if [[ "${ingestion_suspend}" != "true" ]]; then
    start_initial_ingestion_job
  fi

  echo "hetzner up complete. OpenSearch will refill via worker reconcile."
}

hetzner_down() {
  require_cmd tofu
  require_pg_client
  ensure_kubeconfig

  local tmp
  tmp="$(mktemp_dump)"
  trap 'rm -f "${tmp}"' EXIT

  echo "==> dumping ${POSTGRES_POD} → temp"
  cluster_pg_dump_to "${tmp}"

  echo "==> validating and promoting"
  promote_dump "${tmp}"
  trap - EXIT

  local -a tofu_args=(destroy)
  if [[ "${AUTO_APPROVE:-0}" -eq 1 ]]; then
    tofu_args+=(-auto-approve)
  fi

  echo "==> tofu destroy (${HETZNER_DIR})"
  (
    cd "${HETZNER_DIR}"
    tofu "${tofu_args[@]}"
  )

  echo "hetzner down complete. Canonical dump: ${CURRENT_DUMP}"
}
