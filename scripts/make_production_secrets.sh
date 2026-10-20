#!/usr/bin/env bash
# Create ansible/group_vars/production_secrets.yml, Vault-encrypted, holding a random database
# password for the production profile. Refuses to overwrite an existing file. The plaintext
# only ever exists in a 0600 temporary file that is removed on any exit.
#
#   scripts/make_production_secrets.sh        # asks for a NEW vault password (twice)
#   ANSIBLE_VAULT_PASSWORD_FILE=~/.vault_pass scripts/make_production_secrets.sh   # non-interactive
#
# Then run the production profile with:  ansible-playbook -i inventory.ini site.yml -e profile=production --ask-vault-pass
set -euo pipefail
cd "$(dirname "$0")/../ansible"
target="group_vars/production_secrets.yml"
[ -e "$target" ] && { echo "$target already exists; not overwriting it" >&2; exit 1; }
command -v ansible-vault >/dev/null || { echo "ansible-vault not found (activate the venv / install ansible)" >&2; exit 1; }
command -v openssl >/dev/null || { echo "openssl not found" >&2; exit 1; }
umask 077
tmp="$(mktemp "${target}.XXXXXX")"
trap 'rm -f "$tmp"' EXIT
printf 'vault_db_password: "%s"\n' "$(openssl rand -base64 24 | tr -d '/+=\n')" > "$tmp"
ansible-vault encrypt "$tmp" >/dev/null
mv "$tmp" "$target"
trap - EXIT
echo "created $target (encrypted). The database password is inside it; view with: ansible-vault view $target"
