# Agent d'install sideloop — sur PVE (la seule partie hors k8s)

Tout le reste de sideloop tourne **en k8s** (anisette, signer CronJob, frontend —
métriques + logs uniformes). SEULE l'**installation** sur les iPhones vit ici,
sur pve, parce qu'elle exige un accès **L2/mDNS** au device (iOS 27 refuse le
tunnel lockdown sur une interface VPN routée — mesuré). C'est la même logique que
Tailscale : le réseau bas niveau est une capacité de l'hôte, pas du cluster.

## Flux

```
CronJob k8s → signe les IPA → /mnt/media/sideloop-signed/{*.ipa, manifest.json} (NFS)
                                        │
pve : tunneld (Wi-Fi) + install-agent ─┘ → apps install --tunnel sur chaque iPhone
```

## Bootstrap pve (one-time)

Prérequis déjà en place (session 2026-07-13) : usbmuxd, `pymobiledevice3`
(/root/.local/bin), RemotePairing des devices (records /root/.pymobiledevice3/),
`/mnt/media/sideloop-signed` exporté NFS (via l'export /mnt/media existant).

Installer l'agent + les services :
```bash
mkdir -p /opt/sideloop
cp install_agent.py /opt/sideloop/
cp sideloop-tunneld.service sideloop-install-agent.service \
   sideloop-install-agent.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now sideloop-tunneld.service      # tunnel Wi-Fi permanent
systemctl enable --now sideloop-install-agent.timer  # vérif toutes les 30 min
```

Conditions d'install (sinon l'agent attend) :
- l'iPhone doit être sur le **Wi-Fi maison** (BELL338) et **déverrouillé** ;
- après une NOUVELLE app (nouveau cert), **truster le dev** une fois sur l'iPhone :
  Réglages → Général → VPN et gestion de l'appareil.

Logs : `journalctl -u sideloop-install-agent -f` (côté pve, journald).
Tests (sans dépendance) : `python3 test_install_agent.py`.

⚠ **Ne jamais redémarrer tunneld quand des appareils répondent encore.** Un
`systemctl restart sideloop-tunneld` détruit TOUS les tunnels pour espérer en
redécouvrir un absent, et ils mettent bien plus longtemps à revenir qu'on ne
croit : mesuré le 2026-09-17, 78 s après le restart aucun des 3 appareils ne
répondait, alors que tous étaient présents. L'agent ne reconstruit donc que si
plus rien ne répond, et attend jusqu'à `TUNNEL_SETTLE_SEC` (180 s) en sortant
dès que l'ensemble joignable se stabilise. Corollaire de diagnostic : un
« device injoignable » juste après un restart de tunneld ne prouve RIEN sur
l'appareil — vérifier d'abord `ping` sur son IP LAN et sur son adresse de
tunnel (`curl -s localhost:49151/`) avant d'accuser le Wi-Fi ou l'utilisateur.
