#!/bin/bash
# Installe le serveur anisette natif sur le Mac (LaunchAgent de l'utilisateur).
# Idempotent : relancer après une modification du .swift.
#
# ⚠ Chaque recompilation change la signature ad-hoc du binaire : si le pare-feu
# applicatif de macOS se met un jour à le bloquer, c'est la première chose à
# soupçonner (cf. mémoire « Dispo bloqué par TCC », même mécanique).
set -euo pipefail

LABEL=com.sideloop.anisette
PORT=6969
# Sources autorisées : les workers k8s (Calico SNATe les pods sur l'IP du nœud,
# et pve route vers le tailnet sans re-SNAT — mesuré le 2026-10-30).
ALLOWED=(10.10.10.21 10.10.10.22)

here=$(cd "$(dirname "$0")" && pwd)
dest="$HOME/Library/Application Support/sideloop-anisette"
plist="$HOME/Library/LaunchAgents/$LABEL.plist"
logf="$HOME/Library/Logs/sideloop-anisette.log"

ip=$(tailscale ip -4 2>/dev/null || true)
[ -n "$ip" ] || { echo "Tailscale arrêté : impossible de connaître l'IP tailnet." >&2; exit 1; }

mkdir -p "$dest" "$(dirname "$plist")"
swiftc -O "$here/anisette-server.swift" -o "$dest/anisette-server"

args=""
for a in "$dest/anisette-server" "$ip" "$PORT" "${ALLOWED[@]}"; do
  args+="    <string>$a</string>"$'\n'
done
cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
$args  </array>
  <key>RunAtLoad</key><true/>
  <!-- Relancé s'il sort (IP tailnet absente au réveil, Tailscale arrêté). -->
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardErrorPath</key><string>$logf</string>
  <key>StandardOutPath</key><string>$logf</string>
</dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$plist"
sleep 1
launchctl print "gui/$(id -u)/$LABEL" | grep -E '^\s+(state|pid) ='
tail -n 3 "$logf"
echo "→ sideloop : anisetteUrl = http://$ip:$PORT/"
