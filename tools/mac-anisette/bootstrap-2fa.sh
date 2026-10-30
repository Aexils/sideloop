#!/bin/bash
# 2FA unique contre l'anisette native du Mac. À lancer soi-même dans un
# terminal : il demande le mot de passe Apple puis le code reçu.
# Prérequis : install.sh déjà passé (il compile le binaire utilisé ici).
set -euo pipefail

APPLE_ID=${1:-levasseur.alexis@uqam.ca}
here=$(cd "$(dirname "$0")" && pwd)
bin="$HOME/Library/Application Support/sideloop-anisette/anisette-server"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

[ -x "$bin" ] || { echo "Binaire absent : lancer d'abord install.sh." >&2; exit 1; }

# grandslam avec le gsa.py PATCHÉ du repo (2FA SMS), comme dans le Dockerfile.
git clone -q --depth 1 https://github.com/JJTech0130/grandslam.git "$work/grandslam"
cp "$here/../apple_auth/gsa.py" "$work/grandslam/src/grandslam/gsa.py"
python3 -m venv "$work/venv"
"$work/venv/bin/pip" install -q "$work/grandslam" requests cryptography

# Le mot de passe du Secret du cluster = celui du CronJob (pas de saisie de
# mémoire). Sinon, demandé au clavier.
if pw=$(kubectl --context homelab -n sideloop get secret sideloop-apple \
      -o jsonpath='{.data.password}' 2>/dev/null | base64 -d) && [ -n "$pw" ]; then
  echo "Mot de passe : celui du Secret sideloop-apple."
  export SIDELOOP_APPLE_PASSWORD=$pw
fi
unset pw

"$work/venv/bin/python" "$here/bootstrap_2fa.py" "$bin" "$APPLE_ID"
