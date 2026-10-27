#!/usr/bin/env bash
# Create the postgres-credentials Secret if it does not exist yet, with a
# freshly generated random password. Nothing secret is ever committed:
# the password exists only inside the cluster.
#
# Idempotent: if the Secret already exists it is left untouched, so an
# existing PostgreSQL volume keeps working (its password was fixed at first
# initialisation). A full `minikube delete` destroys the Secret and the PVC
# together, so a new random password on the next run is consistent.
#
# Production does NOT use this script: the Ansible `secrets` role creates the
# same Secret from an Ansible Vault value.
set -euo pipefail
KC="${KC:-kubectl}"
NS="${NS:-ml-serving}"
DB_USER="${DB_USER:-feedback}"
DB_NAME="${DB_NAME:-feedback}"

"$KC" get namespace "$NS" >/dev/null 2>&1 || "$KC" create namespace "$NS"

if "$KC" -n "$NS" get secret postgres-credentials >/dev/null 2>&1; then
  echo "postgres-credentials already present -- left unchanged"
  exit 0
fi

PW="$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 32)"
# The Secret goes to kubectl on stdin (printf is a shell builtin): the password is never an argument of
# a command, so it never shows in the process list (it did with --from-literal).
printf 'apiVersion: v1\nkind: Secret\nmetadata:\n  name: postgres-credentials\n  namespace: "%s"\ntype: Opaque\nstringData:\n  POSTGRES_USER: "%s"\n  POSTGRES_PASSWORD: "%s"\n  POSTGRES_DB: "%s"\n' \
  "$NS" "$DB_USER" "$PW" "$DB_NAME" | "$KC" create -f - >/dev/null
unset PW
echo "postgres-credentials created with a generated password"
