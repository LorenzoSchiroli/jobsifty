# Shared helpers for demo dump lifecycle scripts.
# shellcheck shell=bash

: "${BASH_SOURCE[0]}"

_scripts_lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS_DIR="$(cd "${_scripts_lib_dir}/.." && pwd)"
REPO_ROOT="$(cd "${SCRIPTS_DIR}/../.." && pwd)"

DUMPS_DIR="${REPO_ROOT}/dumps"
CURRENT_DUMP="${DUMPS_DIR}/jobsifty.current.dump"
ARCHIVE_DIR="${DUMPS_DIR}/archive"
HETZNER_DIR="${REPO_ROOT}/deploy/infra/hetzner"
CLUSTER_NAME="${CLUSTER_NAME:-jobsifty}"
POSTGRES_POD="${POSTGRES_POD:-postgres-0}"
POSTGRES_DB="${POSTGRES_DB:-jobsifty}"
POSTGRES_USER="${POSTGRES_USER:-postgres}"
POD_DUMP_PATH="/tmp/jobsifty.dump"

require_cmd() {
  local cmd="$1"
  if ! command -v "${cmd}" >/dev/null 2>&1; then
    echo "error: required command not found: ${cmd}" >&2
    exit 1
  fi
}

default_kubeconfig() {
  printf '%s/%s_kubeconfig.yaml' "${HETZNER_DIR}" "${CLUSTER_NAME}"
}

kubeconfig_reachable() {
  # An explicit context (the local kind target) needs no kubeconfig file of
  # its own; it rides on whatever kubectl already resolves.
  if [[ -n "${KUBECTL_CONTEXT:-}" ]]; then
    kctl cluster-info >/dev/null 2>&1
    return
  fi
  local kc="${KUBECONFIG:-}"
  if [[ -z "${kc}" ]]; then
    kc="$(default_kubeconfig)"
  fi
  [[ -f "${kc}" ]] || return 1
  KUBECONFIG="${kc}" kubectl cluster-info >/dev/null 2>&1
}

ensure_kubeconfig() {
  # KUBECTL_CONTEXT pins the cluster directly (local kind), so the Hetzner
  # kubeconfig is neither needed nor appropriate.
  if [[ -n "${KUBECTL_CONTEXT:-}" ]]; then
    require_cmd kubectl
    if ! kctl cluster-info >/dev/null 2>&1; then
      echo "error: kubectl cannot reach context ${KUBECTL_CONTEXT}" >&2
      exit 1
    fi
    return
  fi
  if [[ -z "${KUBECONFIG:-}" ]]; then
    export KUBECONFIG
    KUBECONFIG="$(default_kubeconfig)"
  fi
  if [[ ! -f "${KUBECONFIG}" ]]; then
    echo "error: kubeconfig not found: ${KUBECONFIG}" >&2
    echo "hint: run tofu apply in ${HETZNER_DIR}, or set KUBECONFIG" >&2
    exit 1
  fi
  require_cmd kubectl
  if ! kubectl cluster-info >/dev/null 2>&1; then
    echo "error: kubectl cannot reach cluster (KUBECONFIG=${KUBECONFIG})" >&2
    exit 1
  fi
}

_pg_restore_candidates() {
  # Prefer keg-only Homebrew libpq (often newer than PATH postgresql@14).
  local c
  for c in \
    "${PG_RESTORE:-}" \
    /opt/homebrew/opt/libpq/bin/pg_restore \
    /usr/local/opt/libpq/bin/pg_restore \
    "$(command -v pg_restore 2>/dev/null || true)"; do
    [[ -n "${c}" && -x "${c}" ]] || continue
    printf '%s\n' "${c}"
  done | awk 'NF && !seen[$0]++'
}

_validate_dump_local() {
  local file="$1" candidate
  while IFS= read -r candidate; do
    if "${candidate}" -l "${file}" >/dev/null 2>&1; then
      return 0
    fi
  done < <(_pg_restore_candidates)
  return 1
}

_validate_dump_docker() {
  local file="$1"
  local abs dir base
  command -v docker >/dev/null 2>&1 || return 1
  docker info >/dev/null 2>&1 || return 1
  abs="$(cd "$(dirname "${file}")" && pwd)/$(basename "${file}")"
  dir="$(dirname "${abs}")"
  base="$(basename "${abs}")"
  # Prefer the chart/compose major; fall back to 17 for older host dumps.
  docker run --rm -v "${dir}:/dumps:ro" postgres:16 \
    pg_restore -l "/dumps/${base}" >/dev/null 2>&1 \
    || docker run --rm -v "${dir}:/dumps:ro" postgres:17 \
      pg_restore -l "/dumps/${base}" >/dev/null 2>&1
}

_validate_dump_pod() {
  local file="$1"
  kubeconfig_reachable || return 1
  local kc="${KUBECONFIG:-}"
  if [[ -z "${kc}" && -z "${KUBECTL_CONTEXT:-}" ]]; then
    kc="$(default_kubeconfig)"
  fi
  KUBECONFIG="${kc}" kctl cp "${file}" "${POSTGRES_POD}:${POD_DUMP_PATH}.validate"
  KUBECONFIG="${kc}" kctl exec "${POSTGRES_POD}" -- \
    pg_restore -l "${POD_DUMP_PATH}.validate" >/dev/null
  local rc=$?
  KUBECONFIG="${kc}" kctl exec "${POSTGRES_POD}" -- \
    rm -f "${POD_DUMP_PATH}.validate" || true
  return "${rc}"
}

validate_dump() {
  local file="$1"
  if [[ ! -s "${file}" ]]; then
    echo "error: dump missing or empty: ${file}" >&2
    exit 1
  fi
  if _validate_dump_local "${file}"; then
    return 0
  fi
  if _validate_dump_docker "${file}"; then
    echo "validated dump via docker postgres image (host pg_restore too old or missing)"
    return 0
  fi
  if _validate_dump_pod "${file}"; then
    echo "validated dump via ${POSTGRES_POD} pg_restore"
    return 0
  fi
  cat >&2 <<EOF
error: could not validate dump with pg_restore -l: ${file}
tried: host pg_restore, docker postgres:16/17, and cluster ${POSTGRES_POD}
hint: upgrade client tools (dump format may need PostgreSQL 16+), e.g.
  brew install libpq && brew link --force libpq
or start Docker Desktop, or ensure KUBECONFIG points at a live cluster.
EOF
  exit 1
}

require_pg_client() {
  # Validation can use docker/pod fallbacks when no usable host client exists.
  if [[ -n "$(_pg_restore_candidates | head -n1)" ]]; then
    return 0
  fi
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    return 0
  fi
  if kubeconfig_reachable; then
    return 0
  fi
  echo "error: need host pg_restore (libpq 16+), a running Docker daemon, or a reachable cluster" >&2
  exit 1
}

promote_dump() {
  local src="$1"
  validate_dump "${src}"
  mkdir -p "${ARCHIVE_DIR}"
  if [[ -e "${CURRENT_DUMP}" ]]; then
    local stamp
    stamp="$(date -u +%Y-%m-%dT%H%M%SZ)"
    local archived="${ARCHIVE_DIR}/${stamp}.dump"
    mv "${CURRENT_DUMP}" "${archived}"
    echo "archived previous current → ${archived}"
  fi
  mv "${src}" "${CURRENT_DUMP}"
  echo "promoted → ${CURRENT_DUMP}"
}

cluster_pg_dump_to() {
  local dest="$1"
  ensure_kubeconfig
  kctl exec "${POSTGRES_POD}" -- \
    pg_dump -U "${POSTGRES_USER}" -Fc "${POSTGRES_DB}" -f "${POD_DUMP_PATH}"
  kctl cp "${POSTGRES_POD}:${POD_DUMP_PATH}" "${dest}"
  kctl exec "${POSTGRES_POD}" -- rm -f "${POD_DUMP_PATH}" || true
}

cluster_pg_restore_from() {
  local src="$1"
  local rc=0
  ensure_kubeconfig
  validate_dump "${src}"
  kctl cp "${src}" "${POSTGRES_POD}:${POD_DUMP_PATH}"
  # pg_restore often exits 1 on non-fatal warnings with --clean; treat >1 as hard fail.
  set +e
  kctl exec "${POSTGRES_POD}" -- \
    pg_restore -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" \
      --clean --if-exists --no-owner "${POD_DUMP_PATH}"
  rc=$?
  set -e
  kctl exec "${POSTGRES_POD}" -- rm -f "${POD_DUMP_PATH}" || true
  if [[ "${rc}" -gt 1 ]]; then
    echo "error: pg_restore failed with exit ${rc}" >&2
    return "${rc}"
  fi
  if ! kctl exec "${POSTGRES_POD}" -- \
    psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -v ON_ERROR_STOP=1 -tAc \
      "SELECT 1 FROM jobs LIMIT 1" >/dev/null; then
    echo "error: restore finished but jobs table is not queryable" >&2
    return 1
  fi
  if [[ "${rc}" -eq 1 ]]; then
    echo "warning: pg_restore reported warnings (exit 1); continuing after jobs check" >&2
  fi
}

mktemp_dump() {
  mktemp "${TMPDIR:-/tmp}/jobsifty-dump.XXXXXX.dump"
}

# Empty means "whatever KUBECONFIG points at" (Hetzner). The local target sets
# it to the kind context so a stray KUBECONFIG cannot aim local work at a cloud
# cluster.
KUBECTL_CONTEXT="${KUBECTL_CONTEXT:-}"

kctl() {
  if [[ -n "${KUBECTL_CONTEXT:-}" ]]; then
    kubectl --context "${KUBECTL_CONTEXT}" "$@"
  else
    kubectl "$@"
  fi
}

# Both .env.public and .env, in that order: the chart's envFrom expects
# GROQ_MODEL_*, OFFER_QUERY and CORS_ORIGINS (public) alongside the secrets.
ensure_secret() {
  if kctl get secret jobsifty-secrets >/dev/null 2>&1; then
    echo "secret jobsifty-secrets already exists (leaving unchanged)"
    return
  fi
  local env_public="${REPO_ROOT}/.env.public"
  local env_secret="${REPO_ROOT}/.env"
  if [[ ! -f "${env_public}" || ! -f "${env_secret}" ]]; then
    echo "error: jobsifty-secrets missing and .env.public/.env not both present" >&2
    echo "hint: create the secret as documented in deploy/k8s/README.md" >&2
    exit 1
  fi
  echo "==> creating jobsifty-secrets from .env.public + .env"
  kctl create secret generic jobsifty-secrets \
    --from-env-file="${env_public}" \
    --from-env-file="${env_secret}"
  kctl patch secret jobsifty-secrets --type merge -p \
    '{"stringData":{"DATABASE_URL":"postgresql+asyncpg://postgres:postgres@postgres:5432/jobsifty","OPENSEARCH_URL":"http://opensearch:9200","BACKEND_URL":"http://api:8000"}}'
}

