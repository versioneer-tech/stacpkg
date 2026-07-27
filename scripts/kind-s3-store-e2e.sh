#!/usr/bin/env bash
# Copyright 2026, Versioneer (https://versioneer.at)
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

log() {
  printf '[stacpkg-e2e] %(%Y-%m-%dT%H:%M:%S%z)T %s\n' -1 "$*"
}

cluster="${STACPKG_E2E_KIND_CLUSTER:-stacpkg-s3-store}"
namespace="stacpkg-s3-store"
s3store1_api_port="${STACPKG_E2E_S3STORE1_API_PORT:-19000}"
s3store1_console_port="${STACPKG_E2E_S3STORE1_CONSOLE_PORT:-19001}"
s3store2_api_port="${STACPKG_E2E_S3STORE2_API_PORT:-19010}"
s3store2_console_port="${STACPKG_E2E_S3STORE2_CONSOLE_PORT:-19011}"
s3store1_access_key_id="${STACPKG_E2E_S3STORE1_ACCESS_KEY_ID:-${STACPKG_E2E_S3STORE1_ROOT_USER:-minioadmin1}}"
s3store1_secret_access_key="${STACPKG_E2E_S3STORE1_SECRET_ACCESS_KEY:-${STACPKG_E2E_S3STORE1_ROOT_PASSWORD:-minioadmin123}}"
s3store2_access_key_id="${STACPKG_E2E_S3STORE2_ACCESS_KEY_ID:-${STACPKG_E2E_S3STORE2_ROOT_USER:-minioadmin2}}"
s3store2_secret_access_key="${STACPKG_E2E_S3STORE2_SECRET_ACCESS_KEY:-${STACPKG_E2E_S3STORE2_ROOT_PASSWORD:-minioadmin456}}"
registry_port="${STACPKG_E2E_REGISTRY_PORT:-15000}"
registry_user="${STACPKG_E2E_REGISTRY_USER:-stacpkg-e2e}"
registry_password="${STACPKG_E2E_REGISTRY_PASSWORD:-$(openssl rand -hex 24)}"
pytest_mark="${STACPKG_E2E_PYTEST_MARK:-e2e and not performance}"
pytest_log_level="${STACPKG_E2E_LOG_LEVEL:-INFO}"
render_docs="${STACPKG_E2E_RENDER_DOCS:-0}"
pytest_targets=("$@")
if [[ "${#pytest_targets[@]}" -eq 0 ]]; then
  pytest_targets=(tests/e2e)
fi

log "configuration: cluster=${cluster} namespace=${namespace}"
log "configuration: s3store1_api_port=${s3store1_api_port} s3store1_console_port=${s3store1_console_port}"
log "configuration: s3store2_api_port=${s3store2_api_port} s3store2_console_port=${s3store2_console_port}"
log "configuration: registry_port=${registry_port}"
log "configuration: pytest_mark=${pytest_mark} pytest_log_level=${pytest_log_level}"
log "configuration: pytest_targets=${pytest_targets[*]}"

if command -v kind >/dev/null 2>&1; then
  log "kind found: $(command -v kind)"
  if ! clusters="$(kind get clusters 2>&1)"; then
    log "failed to list kind clusters; ensure Docker is available for kind"
    printf '%s\n' "$clusters" >&2
    exit 1
  fi
  if ! grep -Fxq "$cluster" <<<"$clusters"; then
    log "creating kind cluster: ${cluster}"
    kind create cluster --name "$cluster"
  else
    log "using existing kind cluster: ${cluster}"
  fi
  log "switching kubectl context to kind-${cluster}"
  kubectl config use-context "kind-$cluster" >/dev/null
else
  log "kind not found; assuming kubectl already points at the desired cluster"
fi

log "applying S3 store namespace"
kubectl apply -f - <<YAML
apiVersion: v1
kind: Namespace
metadata:
  name: ${namespace}
YAML

log "applying S3 store credentials"
kubectl -n "$namespace" create secret generic s3-store1-root \
  --from-literal=MINIO_ROOT_USER="$s3store1_access_key_id" \
  --from-literal=MINIO_ROOT_PASSWORD="$s3store1_secret_access_key" \
  --dry-run=client \
  -o yaml \
  | kubectl apply -f -
kubectl -n "$namespace" create secret generic s3-store2-root \
  --from-literal=MINIO_ROOT_USER="$s3store2_access_key_id" \
  --from-literal=MINIO_ROOT_PASSWORD="$s3store2_secret_access_key" \
  --dry-run=client \
  -o yaml \
  | kubectl apply -f -

if ! command -v htpasswd >/dev/null 2>&1; then
  log "htpasswd is required to configure the Basic-auth OCI registry"
  exit 1
fi
registry_htpasswd="$(printf '%s\n' "$registry_password" | htpasswd -niB "$registry_user")"
log "applying OCI registry Basic-auth configuration"
kubectl -n "$namespace" create secret generic registry-basic-auth \
  --from-literal=htpasswd="$registry_htpasswd" \
  --dry-run=client \
  -o yaml \
  | kubectl apply -f -

log "applying S3 store Kubernetes manifest"
kubectl apply -f tests/setup/s3-stores.yaml
log "restarting S3 store deployments to use current credentials"
kubectl -n "$namespace" rollout restart deployment/s3store1 deployment/s3store2
log "applying local registry Kubernetes manifest"
kubectl apply -f tests/setup/registry.yaml
log "restarting registry deployment to use current Basic-auth configuration"
kubectl -n "$namespace" rollout restart deployment/registry
log "waiting for S3 store 1 deployment rollout"
kubectl -n "$namespace" rollout status deployment/s3store1 --timeout=180s
log "waiting for S3 store 2 deployment rollout"
kubectl -n "$namespace" rollout status deployment/s3store2 --timeout=180s
log "waiting for registry deployment rollout"
kubectl -n "$namespace" rollout status deployment/registry --timeout=180s
log "current e2e pods and services"
kubectl -n "$namespace" get pods,svc -o wide

tmpdir="$(mktemp -d)"
pids=()

cleanup() {
  log "cleaning up e2e runner resources"
  for log_file in "$tmpdir"/*.port-forward.log; do
    if [[ -f "$log_file" ]]; then
      log "port-forward log ${log_file}:"
      sed 's/^/[stacpkg-e2e]   /' "$log_file" || true
    fi
  done
  for pid in "${pids[@]}"; do
    log "stopping port-forward process pid=${pid}"
    kill "$pid" >/dev/null 2>&1 || true
  done
  rm -rf "$tmpdir"
}
trap cleanup EXIT

log "starting S3 store 1 port-forward: service=s3store1 api=${s3store1_api_port} console=${s3store1_console_port}"
kubectl -n "$namespace" port-forward \
  "svc/s3store1" "${s3store1_api_port}:9000" "${s3store1_console_port}:9001" \
  >"$tmpdir/s3store1.port-forward.log" 2>&1 &
pids+=("$!")

log "starting S3 store 2 port-forward: service=s3store2 api=${s3store2_api_port} console=${s3store2_console_port}"
kubectl -n "$namespace" port-forward \
  "svc/s3store2" "${s3store2_api_port}:9000" "${s3store2_console_port}:9001" \
  >"$tmpdir/s3store2.port-forward.log" 2>&1 &
pids+=("$!")

log "starting registry port-forward: service=registry registry=${registry_port}"
kubectl -n "$namespace" port-forward \
  "svc/registry" "${registry_port}:5000" \
  >"$tmpdir/registry.port-forward.log" 2>&1 &
pids+=("$!")

for url in \
  "http://127.0.0.1:${s3store1_api_port}/minio/health/ready" \
  "http://127.0.0.1:${s3store2_api_port}/minio/health/ready"
do
  log "waiting for S3 store health endpoint: ${url}"
  for _ in {1..40}; do
    if curl -fsS "$url" >/dev/null 2>&1; then
      log "S3 store health endpoint is ready: ${url}"
      break
    fi
    sleep 1
  done
  log "verifying S3 store health endpoint: ${url}"
  curl -fsS "$url" >/dev/null
done

registry_url="http://127.0.0.1:${registry_port}/v2/"
log "waiting for registry endpoint: ${registry_url}"
for _ in {1..40}; do
  registry_status="$(curl -sS -o /dev/null -w '%{http_code}' "$registry_url" || true)"
  if [[ "$registry_status" == "401" ]]; then
    log "registry endpoint is ready: ${registry_url}"
    break
  fi
  sleep 1
done
log "verifying registry endpoint: ${registry_url}"
registry_status="$(curl -sS -o /dev/null -w '%{http_code}' "$registry_url" || true)"
if [[ "$registry_status" != "401" ]]; then
  log "expected Basic-auth registry challenge, got HTTP ${registry_status}"
  exit 1
fi

export STACPKG_E2E_S3STORE1_ENDPOINT="http://127.0.0.1:${s3store1_api_port}"
export STACPKG_E2E_S3STORE2_ENDPOINT="http://127.0.0.1:${s3store2_api_port}"
export STACPKG_S3_ACCESS_KEY_ID_STACPKG_E2E_S3STORE1="${s3store1_access_key_id}"
export STACPKG_S3_SECRET_ACCESS_KEY_STACPKG_E2E_S3STORE1="${s3store1_secret_access_key}"
export STACPKG_S3_ACCESS_KEY_ID_STACPKG_E2E_S3STORE2="${s3store2_access_key_id}"
export STACPKG_S3_SECRET_ACCESS_KEY_STACPKG_E2E_S3STORE2="${s3store2_secret_access_key}"
export AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID:-${s3store1_access_key_id}}"
export AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY:-${s3store1_secret_access_key}}"
export STACPKG_E2E_REGISTRY="127.0.0.1:${registry_port}"
export STACPKG_TEST_S3STORE_ENDPOINT="${STACPKG_TEST_S3STORE_ENDPOINT:-${STACPKG_E2E_S3STORE1_ENDPOINT}}"
export ORAS_USER="$registry_user"
export ORAS_PASS="$registry_password"

cat <<EOF
E2E endpoints:
  S3 store 1 API:     ${STACPKG_E2E_S3STORE1_ENDPOINT}
  S3 store 1 console: http://127.0.0.1:${s3store1_console_port}
  S3 store 2 API:     ${STACPKG_E2E_S3STORE2_ENDPOINT}
  S3 store 2 console: http://127.0.0.1:${s3store2_console_port}
  OCI registry:       http://${STACPKG_E2E_REGISTRY} (Basic auth)
EOF

log "running pytest e2e suite"
uv run --group integration pytest \
  "${pytest_targets[@]}" \
  -m "$pytest_mark" \
  -o log_cli=true \
  -o "log_cli_level=${pytest_log_level}"

if [[ "$render_docs" == "1" ]]; then
  log "generating shell-sourced use case docs"
  uv run python scripts/generate_usecase_tests.py --no-tests
  log "rendering docs with generated use case pages"
  uv run --group docs --group integration mkdocs build --strict
fi

if [[ "${STACPKG_E2E_KEEP_FORWARD:-0}" == "1" ]]; then
  log "keeping S3 store port-forwards open. Press Ctrl-C to stop them."
  wait
fi
