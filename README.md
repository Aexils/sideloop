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

Ce montage corrige une fragilité réelle (une identité réécrite à chaque démarrage ne
peut pas encaisser les re-provisions), mais **ce n'est PAS ce qui a cassé le 2026-09-10** —
voir la section suivante. ⚠ `X-Apple-I-MD` est **fenêtré dans le temps** : deux appels
rapprochés renvoient légitimement la même valeur, y compris sur une identité neuve. Ne pas
en faire un test de santé.

**Refaire l'identité** (coûte **une 2FA SMS**) — seulement si elle est réellement perdue
ou refusée. L'ordre compte : tant que le Secret contient l'ancienne identité, tout
redémarrage la re-sème (elle n'est semée que si le PVC est vide), donc on re-scelle
**avant** de repasser `reprovision` à `false`.

1. `anisette.reprovision: true` dans les values homelab → sync ArgoCD. L'initContainer
   supprime l'identité et le serveur en provisionne une neuve ;
2. refaire le **2FA SMS** contre ce pod pour truster la nouvelle machine sur le compte ;
3. récupérer `adi.pb` + `device.json` du PVC et les **re-sceller** (`kubeseal`) dans
   `anisette-identity` ;
4. repasser `anisette.reprovision: false` → sync.

Pour **restaurer** l'identité du Secret par-dessus celle du PVC (sans 2FA, si le Secret
contient une identité encore trustée) : `anisette.forceReseed: true`, sync, puis `false`.

## Apple bloque le client-info « Xcode » (depuis septembre 2026)

`gsa.apple.com` répond **503 en HTML** — avant tout examen des identifiants — à toute
requête dont `X-MMe-Client-Info` nomme `com.apple.dt.Xcode`. grandslam tente de parser ce
HTML en plist et lâche `plistlib.InvalidFileException: Invalid file`, illisible.

Mesuré le 2026-09-17, requête identique depuis le même pod :

| `X-MMe-Client-Info` | réponse |
|---|---|
| `…(com.apple.dt.Xcode/3594.4.19)` | **503** `text/html` |
| `…(com.apple.akd/1.0)` | **200**, plist, `ec=0` |

Le refus ne dépend ni du corps de la requête, ni de l'anisette, ni du compte : un plist
**vide** sans `cpd` reçoit le même 503. `signing.AkdAnisette` surcharge donc `client` pour
s'annoncer en `akd`. Même correctif en amont : AltServer 1.7.6, AltStore #1790,
SideStore, FindMy.py #271, anisette-v3-server #59.

⚠ C'est la VRAIE cause de la panne du 2026-09-10 (apps non re-signées, signatures
expirées à J+7). Le dashboard accusait la 2FA, et l'identité anisette a été suspectée à
tort — elle était intacte.

## Apple refuse les empreintes « macOS » (depuis le 12-13 octobre 2026)

GrandSlam répond `ec=-80045` / `hsc=500` / « This action cannot be completed at this
time. » dès la requête `o=init`, **avant que le mot de passe ne serve**, à toute requête
dont le `X-MMe-Client-Info` s'annonce macOS. Apple a déployé une détection anti-fraude
sur les empreintes ADI fabriquées par anisette-v3-server (CoreADI **Android** tournant
dans un macOS 13.1 virtualisé) : prétendre être un Mac ne passe plus.

Mesuré le 2026-10-16 depuis le pod, **même identité anisette, même compte** :

| `X-MMe-Client-Info` | `o=init` | `o=complete` |
|---|---|---|
| `…(com.apple.akd/1.0)` — macOS | **ec=-80045**, hsc=500 | jamais atteint |
| `<PC> <Windows;6.2(0,0);9200> <com.apple.AuthKit/1>` | **ec=0**, sp=s2k | **ec=0**, au=None |

`au=None` : l'identité anisette reste **trustée**, aucune 2FA à refaire. `signing.py`
expose donc **deux** anisettes — `GsaAnisette` (Windows) pour gsa.apple.com/GsService2,
login SRP *et* apptokens, et `AkdAnisette` (akd/macOS) pour developerservices2, qui
attend toujours l'en-tête macOS. Panne communautaire (SideStore, AltStore, iLoader,
Sideloadly) ; même contournement en amont : anisette-v3-server#61.

⚠ C'est la cause de la panne du 2026-10-13 (dernière signature réussie le 12 à 18:00).
Le dashboard a **encore** accusé la 2FA : le message d'erreur avalait le `Status`
d'Apple. `login()` relit désormais ce `Status` (`_gsa_init_status`) et distingue un refus
à l'init (le client qu'on annonce) d'un échec après init (les identifiants).

⚠ En sondant GSA en rafale on se fait **limiter en débit** : les réponses deviennent du
HTML et `apptokens` lâche un `InvalidFileException` trompeur. Espacer les essais.

## Apple refuse l'anisette-v3 elle-même (depuis le 30 octobre 2026)

`ec=-80045` dès `o=init`, cette fois avec **tous** les client-info — Windows compris —,
avec un compte fictif et avec une anisette publique : c'est l'empreinte CoreADI Android
d'anisette-v3-server qui est filtrée. L'anisette d'un **vrai Mac** passe (`ec=-20970`,
compte inconnu, sur la même requête). Le CronJob la demande au Mac d'Alexis par
Tailscale, et tourne toutes les heures pour attraper le Mac allumé :
voir [`tools/mac-anisette/`](tools/mac-anisette/README.md). Le pod `anisette` reste
déployé, inutilisé, au cas où Apple reviendrait en arrière.

## Limites du compte Apple gratuit

3 apps actives · 10 App IDs/semaine · cert 7 jours (d'où le CronJob à ~5 j).
Les IDs d'apps connues (com.spotify.client…) sont réservés → re-bundle via `zsign -b`
vers un ID unique (com.sideloop.*), donc l'app re-signée est **distincte** de l'originale.
