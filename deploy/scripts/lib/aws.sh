# AWS demo dump helpers. Source after lib/common.sh.
# shellcheck shell=bash

AWS_DIR="${REPO_ROOT}/deploy/infra/aws"

# Set by aws_pause_writers; restored by aws_resume_writers.
_AWS_API_DESIRED=""
_AWS_WORKER_DESIRED=""
_AWS_API_AS_MIN=""
_AWS_API_AS_MAX=""
_AWS_INGESTION_STATE=""
_AWS_WRITERS_PAUSED=0

aws_require_tools() {
  require_cmd tofu
  require_cmd aws
  require_cmd jq
}

aws_require_tfvars() {
  if [[ ! -f "${AWS_DIR}/terraform.tfvars" ]]; then
    echo "error: missing ${AWS_DIR}/terraform.tfvars" >&2
    echo "hint: cp ${AWS_DIR}/terraform.tfvars.example ${AWS_DIR}/terraform.tfvars" >&2
    exit 1
  fi
}

aws_tofu_output() {
  local name="$1"
  (
    cd "${AWS_DIR}"
    tofu output -raw "${name}"
  )
}

aws_tofu_output_json() {
  local name="$1"
  (
    cd "${AWS_DIR}"
    tofu output -json "${name}"
  )
}

aws_cluster() {
  aws_tofu_output ecs_cluster_name
}

aws_network_configuration() {
  # Shorthand form used by AWS CLI (same as deploy/infra/aws/README.md).
  local subnets sg
  subnets="$(aws_tofu_output_json private_subnet_ids | jq -r 'join(",")')"
  sg="$(aws_tofu_output ecs_tasks_security_group_id)"
  printf 'awsvpcConfiguration={subnets=[%s],securityGroups=[%s],assignPublicIp=DISABLED}' \
    "${subnets}" "${sg}"
}

aws_run_task_and_wait() {
  local task_def_arn="$1"
  local overrides_json="${2:-}"
  local cluster net task_arn exit_code last_status stop_reason

  cluster="$(aws_cluster)"
  net="$(aws_network_configuration)"

  local -a run_cmd=(
    aws ecs run-task
    --cluster "${cluster}"
    --task-definition "${task_def_arn}"
    --launch-type FARGATE
    --network-configuration "${net}"
  )
  if [[ -n "${overrides_json}" ]]; then
    run_cmd+=(--overrides "${overrides_json}")
  fi

  task_arn="$("${run_cmd[@]}" --query 'tasks[0].taskArn' --output text)"
  if [[ -z "${task_arn}" || "${task_arn}" == "None" ]]; then
    echo "error: ecs run-task did not return a task ARN" >&2
    return 1
  fi
  echo "started task ${task_arn}"

  aws ecs wait tasks-stopped --cluster "${cluster}" --tasks "${task_arn}"

  exit_code="$(aws ecs describe-tasks --cluster "${cluster}" --tasks "${task_arn}" \
    --query 'tasks[0].containers[0].exitCode' --output text)"
  last_status="$(aws ecs describe-tasks --cluster "${cluster}" --tasks "${task_arn}" \
    --query 'tasks[0].containers[0].lastStatus' --output text)"
  stop_reason="$(aws ecs describe-tasks --cluster "${cluster}" --tasks "${task_arn}" \
    --query 'tasks[0].stoppedReason' --output text)"

  if [[ "${exit_code}" != "0" ]]; then
    echo "error: task failed exit=${exit_code} status=${last_status} reason=${stop_reason}" >&2
    return 1
  fi
  echo "task finished ok"
}

aws_run_bootstrap_task() {
  echo "==> bootstrap RunTask"
  aws_run_task_and_wait "$(aws_tofu_output bootstrap_task_definition_arn)"
}

aws_run_dump_task() {
  echo "==> dump RunTask (pg_dump → S3)"
  local overrides
  overrides="$(jq -nc '{containerOverrides:[{name:"pgtools",command:["dump"]}]}')"
  aws_run_task_and_wait "$(aws_tofu_output dump_task_definition_arn)" "${overrides}"
}

aws_run_restore_task() {
  echo "==> restore RunTask (S3 → pg_restore)"
  local overrides
  overrides="$(jq -nc '{containerOverrides:[{name:"pgtools",command:["restore"]}]}')"
  aws_run_task_and_wait "$(aws_tofu_output dump_task_definition_arn)" "${overrides}"
}

aws_upload_dump() {
  local src="$1"
  local uri
  uri="$(aws_tofu_output dump_s3_uri)"
  echo "==> aws s3 cp ${src} → ${uri}"
  aws s3 cp "${src}" "${uri}"
}

aws_download_dump() {
  local dest="$1"
  local uri
  uri="$(aws_tofu_output dump_s3_uri)"
  echo "==> aws s3 cp ${uri} → ${dest}"
  aws s3 cp "${uri}" "${dest}"
}

_aws_api_resource_id() {
  printf 'service/%s/api' "$(aws_cluster)"
}

_aws_get_service_desired() {
  local service="$1"
  aws ecs describe-services \
    --cluster "$(aws_cluster)" \
    --services "${service}" \
    --query 'services[0].desiredCount' \
    --output text
}

_aws_wait_service_desired() {
  local service="$1"
  local want="$2"
  local cluster running i
  cluster="$(aws_cluster)"
  for i in $(seq 1 60); do
    running="$(aws ecs describe-services --cluster "${cluster}" --services "${service}" \
      --query 'services[0].runningCount' --output text)"
    if [[ "${running}" == "${want}" ]]; then
      return 0
    fi
    sleep 5
  done
  echo "error: service ${service} still runningCount=${running}, want ${want}" >&2
  return 1
}

aws_scheduler_set_state() {
  local state="$1" # ENABLED | DISABLED
  local name tmp
  name="$(aws_tofu_output ingestion_schedule_name)"
  tmp="$(mktemp)"
  aws scheduler get-schedule --name "${name}" --output json \
    | jq --arg state "${state}" '
        {
          Name: .Name,
          GroupName: (.GroupName // "default"),
          FlexibleTimeWindow: .FlexibleTimeWindow,
          ScheduleExpression: .ScheduleExpression,
          ScheduleExpressionTimezone: .ScheduleExpressionTimezone,
          Target: .Target,
          State: $state
        }
        | with_entries(select(.value != null))
      ' >"${tmp}"
  aws scheduler update-schedule --cli-input-json "file://${tmp}" >/dev/null
  rm -f "${tmp}"
}

aws_pause_writers() {
  local cluster api_res
  cluster="$(aws_cluster)"
  api_res="$(_aws_api_resource_id)"

  _AWS_API_DESIRED="$(_aws_get_service_desired api)"
  _AWS_WORKER_DESIRED="$(_aws_get_service_desired worker)"
  _AWS_INGESTION_STATE="$(aws scheduler get-schedule \
    --name "$(aws_tofu_output ingestion_schedule_name)" \
    --query 'State' --output text)"

  local as_json
  as_json="$(aws application-autoscaling describe-scalable-targets \
    --service-namespace ecs \
    --resource-ids "${api_res}" \
    --output json)"
  _AWS_API_AS_MIN="$(jq -r '.ScalableTargets[0].MinCapacity // 1' <<<"${as_json}")"
  _AWS_API_AS_MAX="$(jq -r '.ScalableTargets[0].MaxCapacity // 4' <<<"${as_json}")"

  echo "==> pausing writers (api=${_AWS_API_DESIRED}, worker=${_AWS_WORKER_DESIRED}, ingestion=${_AWS_INGESTION_STATE})"

  aws application-autoscaling register-scalable-target \
    --service-namespace ecs \
    --scalable-dimension ecs:service:DesiredCount \
    --resource-id "${api_res}" \
    --min-capacity 0 \
    --max-capacity "${_AWS_API_AS_MAX}" \
    >/dev/null

  aws ecs update-service --cluster "${cluster}" --service api --desired-count 0 >/dev/null
  aws ecs update-service --cluster "${cluster}" --service worker --desired-count 0 >/dev/null
  aws_scheduler_set_state DISABLED

  _aws_wait_service_desired api 0
  _aws_wait_service_desired worker 0
  _AWS_WRITERS_PAUSED=1
}

aws_resume_writers() {
  if [[ "${_AWS_WRITERS_PAUSED}" -ne 1 ]]; then
    return 0
  fi
  local cluster api_res
  cluster="$(aws_cluster)"
  api_res="$(_aws_api_resource_id)"

  echo "==> resuming writers (api=${_AWS_API_DESIRED}, worker=${_AWS_WORKER_DESIRED}, ingestion=${_AWS_INGESTION_STATE})"

  aws application-autoscaling register-scalable-target \
    --service-namespace ecs \
    --scalable-dimension ecs:service:DesiredCount \
    --resource-id "${api_res}" \
    --min-capacity "${_AWS_API_AS_MIN}" \
    --max-capacity "${_AWS_API_AS_MAX}" \
    >/dev/null

  aws ecs update-service --cluster "${cluster}" --service api \
    --desired-count "${_AWS_API_DESIRED}" >/dev/null
  aws ecs update-service --cluster "${cluster}" --service worker \
    --desired-count "${_AWS_WORKER_DESIRED}" >/dev/null

  if [[ "${_AWS_INGESTION_STATE}" == "ENABLED" ]]; then
    aws_scheduler_set_state ENABLED
  else
    aws_scheduler_set_state DISABLED
  fi

  _AWS_WRITERS_PAUSED=0
}

# One ingestion run now instead of at the next schedule tick, with a longer
# lookback so the gap since the dump was taken gets filled. Not waited on.
aws_start_initial_ingestion_task() {
  local overrides
  overrides="$(jq -cn --arg h "${INITIAL_INGESTION_HOURS}" '{containerOverrides: [{
    name: "ingestion",
    command: ["sh", "-c", ("uv run python -m ingestion.pipeline \"$OFFER_QUERY\" --hours " + $h)]
  }]}')"
  echo "==> starting first ingestion run (--hours ${INITIAL_INGESTION_HOURS})"
  aws ecs run-task \
    --cluster "$(aws_cluster)" \
    --task-definition "$(aws_tofu_output ingestion_task_definition_arn)" \
    --launch-type FARGATE \
    --network-configuration "$(aws_network_configuration)" \
    --overrides "${overrides}" \
    --query 'tasks[0].taskArn' --output text
}

aws_stack_reachable() {
  (
    cd "${AWS_DIR}"
    tofu output -raw ecs_cluster_name >/dev/null 2>&1
  )
}

aws_up() {
  aws_require_tools
  require_pg_client
  aws_require_tfvars

  if [[ ! -f "${CURRENT_DUMP}" ]]; then
    echo "error: missing ${CURRENT_DUMP}" >&2
    echo "hint: run deploy/scripts/seed-dump first" >&2
    exit 1
  fi
  validate_dump "${CURRENT_DUMP}"

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

  echo "==> tofu apply (${AWS_DIR})"
  (
    cd "${AWS_DIR}"
    tofu "${tofu_args[@]}"
  )

  aws_run_bootstrap_task
  aws_pause_writers

  local restore_ok=0
  if aws_upload_dump "${CURRENT_DUMP}" && aws_run_restore_task; then
    restore_ok=1
  fi

  if [[ "${restore_ok}" -ne 1 ]]; then
    cat >&2 <<'RECOVERY'
error: dump upload or pg_restore failed; leaving api/worker at 0 and ingestion disabled.
recovery:
  # fix dump / retry upload + restore, then:
  # re-run helpers or manually scale services and re-enable the EventBridge schedule
  # see deploy/infra/aws/README.md (Demo dump lifecycle)
RECOVERY
    exit 1
  fi

  # pg_restore --clean brings back the dump's own schema, which may predate
  # the deployed code: migrate again before the writers come back.
  echo "==> re-running migrations on the restored data"
  aws_run_bootstrap_task

  aws_resume_writers
  if [[ "${_AWS_INGESTION_STATE}" == "ENABLED" ]]; then
    aws_start_initial_ingestion_task
  fi

  echo "aws up complete. OpenSearch will refill via worker reconcile."
  if [[ "${MANAGE_DNS:-1}" -eq 1 ]]; then
    echo "DNS: Cloudflare app/api/apex/www now point at the ALB."
  else
    echo "DNS untouched (--no-dns). Reach it via: tofu output alb_dns_name"
  fi
}

aws_down() {
  aws_require_tools
  require_pg_client
  aws_require_tfvars

  if ! aws_stack_reachable; then
    echo "error: AWS stack outputs unavailable in ${AWS_DIR}" >&2
    echo "hint: run tofu apply / 'run aws up' first" >&2
    exit 1
  fi

  local tmp
  tmp="$(mktemp_dump)"
  trap 'rm -f "${tmp}"; aws_resume_writers 2>/dev/null || true' EXIT

  aws_pause_writers

  echo "==> dumping RDS → S3 → temp"
  aws_run_dump_task
  aws_download_dump "${tmp}"

  echo "==> validating and promoting"
  promote_dump "${tmp}"
  trap 'aws_resume_writers 2>/dev/null || true' EXIT

  local -a tofu_args=(destroy)
  if [[ "${AUTO_APPROVE:-0}" -eq 1 ]]; then
    tofu_args+=(-auto-approve)
  fi

  echo "==> tofu destroy (${AWS_DIR})"
  (
    cd "${AWS_DIR}"
    tofu "${tofu_args[@]}"
  )

  trap - EXIT
  # Stack is gone; skip resume.
  _AWS_WRITERS_PAUSED=0

  echo "aws down complete. Canonical dump: ${CURRENT_DUMP}"
  echo "Spot-check AWS console: NAT, EIP, ALB, RDS, OpenSearch, S3 dump bucket."
}
