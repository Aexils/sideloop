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

## L'identité anisette est VIVANTE (ne pas la figer)

`adi.pb` n'est pas une constante : le serveur anisette re-provisionne sa session ADI
et **réécrit le fichier**. Elle vit donc sur le PVC `anisette-state`, qui monte tout
`~/.config/anisette-v3/`. Le Secret `anisette-identity` n'est qu'une **semence de
secours**, posée uniquement si le PVC est vide (`anisette.forceReseed: true` pour
forcer une restauration).

> Panne du **2026-09-10** : l'identité était reseedée depuis le Secret sur un
> `emptyDir` à chaque start, donc gelée. Sa provision a périmé ~30 j après le start du
> pod, l'OTP `X-Apple-I-MD` a cessé de tourner, `gsa.apple.com` a répondu **503**, et
> les apps sont mortes 7 j plus tard sans que l'alerte (« 2FA à refaire ? ») ne le dise.

**Diagnostic en 10 s** — l'OTP doit changer à chaque appel :

```sh
kubectl -n sideloop port-forward deploy/anisette 6969:6969 &
for i in 1 2 3; do curl -s localhost:6969/ | jq -r '."X-Apple-I-MD"'; sleep 1; done
# 3 valeurs identiques = provision morte (c'est ce que fait `signing.probe_anisette`)
```

**Remise en route** (coûte **une 2FA SMS**, inévitable). L'ordre compte : tant que le
Secret contient l'ancienne identité, tout redémarrage la re-sème (elle n'est semée que
si le PVC est vide) — donc on re-scelle **avant** de repasser `reprovision` à `false`.

1. `anisette.reprovision: true` dans les values homelab → sync ArgoCD. L'initContainer
   supprime l'identité et le serveur en provisionne une neuve ;
2. refaire le **2FA SMS** contre ce pod (flux de `tools/apple_auth`) pour truster la
   nouvelle machine sur le compte ;
3. récupérer `adi.pb` + `device.json` du PVC et les **re-sceller** (`kubeseal`) dans
   `anisette-identity` ;
4. repasser `anisette.reprovision: false` → sync. Le Secret est désormais une semence
   valide si le PVC est perdu ;
5. `kubectl -n sideloop create job resign-now --from=cronjob/sideloop-refresh`, puis
   réinstaller sur les devices (l'agent pve le fait au prochain passage du timer).

## Limites du compte Apple gratuit

3 apps actives · 10 App IDs/semaine · cert 7 jours (d'où le CronJob à ~5 j).
Les IDs d'apps connues (com.spotify.client…) sont réservés → re-bundle via `zsign -b`
vers un ID unique (com.sideloop.*), donc l'app re-signée est **distincte** de l'originale.
