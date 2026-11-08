#!/usr/bin/env bash
set -uo pipefail

sa=/var/run/secrets/kubernetes.io/serviceaccount
fail=0
home=$(mktemp -d /var/tmp/home.XXXXXX) || exit 1
# shellcheck disable=SC2329
cleanup() {
  rm -rf "$home"
  for bin in flux helm; do
    if [[ -e /opt/agent/cluster-tools/$bin.real ]]; then mv -f "/opt/agent/cluster-tools/$bin.real" "/opt/agent/cluster-tools/$bin"; fi
  done
  if [[ -n ${made_sa:-} ]]; then rm -rf "$sa"; fi
}
trap cleanup EXIT

expect() {
  if [[ $2 == "$3" ]]; then
    echo "ok   $1"
  else
    echo "FAIL $1: expected '$3', got '$2'"
    fail=1
  fi
}

clean_shell() { env -i HOME="$home" PATH=/opt/agent/pinned:/opt/agent/bin:/usr/local/bin:/usr/bin:/bin "$@"; }
view() { clean_shell kubectl config view --minify -o "jsonpath=$1" 2>/dev/null; }

if [[ $(id -u) != 0 ]]; then
  echo "skip cluster access checks (need root to mount a fake service account)"
  exit 0
fi
if [[ -e $sa ]]; then
  echo "skip cluster access checks ($sa exists)"
  exit 0
fi

for bin in flux helm; do
  mv "/opt/agent/cluster-tools/$bin" "/opt/agent/cluster-tools/$bin.real"
  # shellcheck disable=SC2016
  printf '#!/bin/sh\nprintf %%s "${KUBECONFIG:-unset}"\n' >"/opt/agent/cluster-tools/$bin"
  chmod 755 "/opt/agent/cluster-tools/$bin"
done

expect "kubectl: without a service account token there is no kubeconfig" "$(view '{.clusters[0].cluster.server}')" ""
expect "flux: without a service account token KUBECONFIG stays unset" "$(clean_shell flux get ks)" unset
expect "helm: without a service account token KUBECONFIG stays unset" "$(clean_shell helm list)" unset
# shellcheck disable=SC2016
expect "profile: without a service account token KUBECONFIG stays unset" "$(clean_shell bash -lc 'printf %s "${KUBECONFIG:-unset}"')" unset
expect "no kubeconfig is written without a token" "$(ls -A "$home/.kube" 2>/dev/null)" ""

made_sa=1
install -d "$sa"
printf fake-token >"$sa/token"
printf 'not a real ca\n' >"$sa/ca.crt"
printf omnigent-sandboxes >"$sa/namespace"

expect "kubectl: server is the in-cluster service" "$(view '{.clusters[0].cluster.server}')" https://kubernetes.default.svc
expect "kubectl: CA is the mounted ca.crt" "$(view '{.clusters[0].cluster.certificate-authority}')" "$sa/ca.crt"
expect "kubectl: user reads the mounted token file" "$(view '{.users[0].user.tokenFile}')" "$sa/token"
expect "kubectl: namespace comes from the mounted file" "$(view '{.contexts[0].context.namespace}')" omnigent-sandboxes
expect "flux: sees the generated kubeconfig" "$(clean_shell flux get ks)" "$home/.kube/in-cluster.yaml"
expect "helm: sees the generated kubeconfig" "$(clean_shell helm list)" "$home/.kube/in-cluster.yaml"
# shellcheck disable=SC2016
expect "profile: exports the generated kubeconfig" "$(clean_shell bash -lc 'printf %s "$KUBECONFIG"')" "$home/.kube/in-cluster.yaml"
expect "an explicit KUBECONFIG wins" "$(clean_shell KUBECONFIG=/x flux get ks)" /x

exit "$fail"
