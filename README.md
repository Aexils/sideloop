# sideloop

Re-signature **automatique** d'apps iOS (`.ipa`) avec un **Apple ID gratuit**,
pilotée depuis Kubernetes. Un « Sideloadly headless » : le certificat gratuit
expire tous les 7 jours, sideloop re-signe et réinstalle avant, tout seul.

> Prouvé de bout en bout le 2026-07-13 : Spotify + YouTube OLED re-signés et
> installés **sans fil sur iOS 27** avec une chaîne d'auth Apple **maison en
> Python** (aucun binaire tiers pour la signature).

## Architecture (split assumé)

Tout tourne **en k8s** sauf l'install, qui exige un accès réseau L2/mDNS au
device (iOS 27 refuse le tunnel sur une interface VPN routée — mesuré). C'est la
même logique que Tailscale : le réseau bas niveau est une capacité de l'hôte.

```
┌───────────────── k8s (namespace sideloop) — métriques + logs uniformes ──────────┐
│  anisette (pod, machine trustée)   ·   frontend/api (upload IPA)                  │
│  CronJob signer (tous les ~5 j) :                                                 │
│    login GrandSlam (2FA-free) → portail dev (device/cert/App ID/profil) → zsign   │
│    → dépose les IPA signées + manifest.json sur /mnt/media/sideloop-signed (NFS)  │
└───────────────────────────────────────────┬──────────────────────────────────────┘
                                            │ NFS (DD 1To)
┌───────────────────────────────────────────▼──────────── pve (hôte, hors k8s) ─────┐
│  tunneld + install-agent : lit le manifest → apps install --tunnel sur les iPhones │
│  (quand ils sont sur le Wi-Fi maison)                                              │
└────────────────────────────────────────────────────────────────────────────────────┘
```

**Signer** parle aux serveurs Apple (marche de partout). **Installer** parle à
l'iPhone (exige le Wi-Fi maison). Voir la mémoire projet du homelab pour le détail
des murs franchis (2FA SMS, anisette trustée, RemoteXPC iOS 27…).

## Layout

```
src/sideloop/          code Python
  signing.py           LE pipeline de signature (auth Apple + portail dev + zsign)
  refresh.py           entrypoint CronJob : signe chaque app → manifest
  api.py               frontend : upload d'IPA, apps gérées
  config/models/storage
charts/sideloop/       chart Helm (anisette · CronJob · frontend · PVC NFS · route)
tools/apple_auth/      briques d'auth validées (grandslam patché, devauth, dev_portal)
tools/pve-install-agent/  l'agent d'install + units systemd (partie pve)
Dockerfile             image : zsign (build) + grandslam patché + notre code
```

## Bootstrap (one-time, hors GitOps)

- **anisette trustée** : après 1er déploiement, faire le **2FA une fois** contre le
  pod anisette (flux SMS via grandslam) → son adi.pb persiste = 0 re-2FA ensuite.
- **secret** : mot de passe Apple scellé via kubeseal (`sideloop-apple`) dans le repo
  d'infra homelab.
- **pve** : `tools/pve-install-agent/README.md` (usbmuxd, RemotePairing, units).

Le déploiement/mise à jour se fait en **GitOps via ArgoCD** (repo homelab).

## Limites du compte Apple gratuit

3 apps actives · 10 App IDs/semaine · cert 7 jours (d'où le CronJob à ~5 j).
Les IDs d'apps connues (com.spotify.client…) sont réservés → re-bundle via `zsign -b`
vers un ID unique (com.sideloop.*), donc l'app re-signée est **distincte** de l'originale.
