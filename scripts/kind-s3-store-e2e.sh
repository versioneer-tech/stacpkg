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
s3store2_api_port="${STACPKG_E2E_S3STORE2_API_PORT:-19010}"
s3store1_access_key_id="${STACPKG_E2E_S3STORE1_ACCESS_KEY_ID:-minioadmin1}"
s3store1_secret_access_key="${STACPKG_E2E_S3STORE1_SECRET_ACCESS_KEY:-minioadmin123}"
s3store2_access_key_id="${STACPKG_E2E_S3STORE2_ACCESS_KEY_ID:-minioadmin2}"
s3store2_secret_access_key="${STACPKG_E2E_S3STORE2_SECRET_ACCESS_KEY:-minioadmin456}"
registry_port="${STACPKG_E2E_REGISTRY_PORT:-15000}"
registry_user="${STACPKG_E2E_REGISTRY_USER:-stacpkg-e2e}"
registry_password="${STACPKG_E2E_REGISTRY_PASSWORD:-$(openssl rand -hex 24)}"
pytest_mark="${STACPKG_E2E_PYTEST_MARK:-e2e and not performance}"
pytest_targets=("$@")
if [[ "${#pytest_targets[@]}" -eq 0 ]]; then
  pytest_targets=(tests/e2e)
fi

if [[ "$cluster" != "stacpkg-s3-store" ]]; then
  log "only the dedicated stacpkg-s3-store cluster is supported"
  exit 1
fi
for tool in kind kubectl htpasswd; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    log "required tool is missing: $tool"
    exit 1
  fi
done
if ! clusters="$(kind get clusters 2>&1)"; then
  log "cannot list Kind clusters; check Docker"
  exit 1
fi
if ! grep -Fxq "$cluster" <<<"$clusters"; then
  log "cluster ${cluster} is unavailable; inspect its Docker nodes before creation"
  log "cluster creation requires approval; CI creates it in the workflow"
  exit 1
fi

tmpdir="$(mktemp -d /tmp/xyz-stacpkg-e2e.XXXXXX)"
pids=()
kubectl_cmd=(kubectl --kubeconfig "$tmpdir/kubeconfig" --context "kind-$cluster")

cleanup() {
  result=$?
  if [[ "$result" -ne 0 ]]; then
    "${kubectl_cmd[@]}" -n "$namespace" get pods,svc -o wide || true
    "${kubectl_cmd[@]}" -n "$namespace" get events --sort-by=.lastTimestamp || true
    for deployment in s3store1 s3store2 registry; do
      "${kubectl_cmd[@]}" -n "$namespace" logs "deployment/$deployment" --all-containers --tail=100 || true
    done
  fi
  for pid in "${pids[@]}"; do
    kill "$pid" >/dev/null 2>&1 || true
    wait "$pid" 2>/dev/null || true
  done
  if [[ "$result" -ne 0 ]]; then
    for log_file in "$tmpdir"/*.port-forward.log; do
      if [[ -f "$log_file" ]]; then
        cat "$log_file"
      fi
    done
  fi
  rm -rf "$tmpdir"
}

# Keep the user's current context and kubeconfig unchanged.
umask 077
trap cleanup EXIT
kind get kubeconfig --name "$cluster" >"$tmpdir/kubeconfig"
"${kubectl_cmd[@]}" cluster-info --request-timeout=15s >/dev/null
log "using kind-${cluster}, namespace ${namespace}"
"${kubectl_cmd[@]}" -n "$namespace" get pods,svc --ignore-not-found

log "preparing test services"
"${kubectl_cmd[@]}" apply -f - <<YAML
apiVersion: v1
kind: Namespace
metadata:
  name: ${namespace}
YAML

"${kubectl_cmd[@]}" -n "$namespace" create secret generic s3-store1-root \
  --from-literal=accesskey="$s3store1_access_key_id" \
  --from-literal=secretkey="$s3store1_secret_access_key" \
  --dry-run=client \
  -o yaml \
  | "${kubectl_cmd[@]}" apply -f -
"${kubectl_cmd[@]}" -n "$namespace" create secret generic s3-store2-root \
  --from-literal=accesskey="$s3store2_access_key_id" \
  --from-literal=secretkey="$s3store2_secret_access_key" \
  --dry-run=client \
  -o yaml \
  | "${kubectl_cmd[@]}" apply -f -

registry_htpasswd="$(printf '%s\n' "$registry_password" | htpasswd -niB "$registry_user")"
"${kubectl_cmd[@]}" -n "$namespace" create secret generic registry-basic-auth \
  --from-literal=htpasswd="$registry_htpasswd" \
  --dry-run=client \
  -o yaml \
  | "${kubectl_cmd[@]}" apply -f -

"${kubectl_cmd[@]}" apply -f tests/setup/s3-stores.yaml -f tests/setup/registry.yaml
# Restart pods so changes to test credentials take effect.
"${kubectl_cmd[@]}" -n "$namespace" rollout restart deployment/s3store1 deployment/s3store2 deployment/registry
for deployment in s3store1 s3store2 registry; do
  "${kubectl_cmd[@]}" -n "$namespace" rollout status "deployment/$deployment" --timeout=180s
done
"${kubectl_cmd[@]}" -n "$namespace" get pods,svc -o wide

forward() {
  "${kubectl_cmd[@]}" -n "$namespace" port-forward "svc/$1" "$2:$3" \
    >"$tmpdir/$1.port-forward.log" 2>&1 &
  pids+=("$!")
}

forward s3store1 "$s3store1_api_port" 9000
forward s3store2 "$s3store2_api_port" 9000
forward registry "$registry_port" 5000

for url in \
  "http://127.0.0.1:${s3store1_api_port}/minio/health/ready" \
  "http://127.0.0.1:${s3store2_api_port}/minio/health/ready"
do
  log "waiting for S3 store health endpoint: ${url}"
  for _ in {1..40}; do
    if curl -fsS "$url" >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done
  curl -fsS "$url" >/dev/null
done

registry_url="http://127.0.0.1:${registry_port}/v2/"
log "waiting for registry endpoint: ${registry_url}"
for _ in {1..40}; do
  registry_status="$(curl -sS -o /dev/null -w '%{http_code}' "$registry_url" || true)"
  if [[ "$registry_status" == "401" ]]; then
    break
  fi
  sleep 1
done
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
export AWS_ACCESS_KEY_ID="${s3store1_access_key_id}"
export AWS_SECRET_ACCESS_KEY="${s3store1_secret_access_key}"
export STACPKG_E2E_REGISTRY="127.0.0.1:${registry_port}"
export ORAS_USER="$registry_user"
export ORAS_PASS="$registry_password"

cat <<EOF
E2E endpoints:
  S3 store 1 API:     ${STACPKG_E2E_S3STORE1_ENDPOINT}
  S3 store 2 API:     ${STACPKG_E2E_S3STORE2_ENDPOINT}
  OCI registry:       http://${STACPKG_E2E_REGISTRY} (Basic auth)
EOF

log "running ${pytest_targets[*]} (${pytest_mark})"
uv run pytest "${pytest_targets[@]}" -m "$pytest_mark"

if [[ "${STACPKG_E2E_KEEP_FORWARD:-0}" == "1" ]]; then
  log "keeping S3 store port-forwards open. Press Ctrl-C to stop them."
  wait
fi
