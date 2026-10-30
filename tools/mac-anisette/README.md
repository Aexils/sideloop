# Anisette native du Mac

Depuis le **2026-10-30**, GrandSlam refuse toute anisette fabriquée par
anisette-v3-server (CoreADI Android) : `ec=-80045` dès `o=init`, avec n'importe quel
client-info, avec un compte fictif et avec l'anisette publique `ani.sidestore.io`
(mesuré par medic). Celle d'un **vrai Mac** passe : même requête, compte fictif,
`ec=-20970` (compte inconnu), donc le filtre est franchi. Mesuré depuis la même
connexion que pve : ce n'est pas un blocage d'IP.

Le CronJob demande donc son anisette au Mac d'Alexis, par Tailscale.

```
CronJob sideloop-refresh (pod, w1/w2) ── Calico SNAT → 10.10.10.2x
  → pve (route vers le tailnet) → 100.102.18.22:6969 (Mac, LaunchAgent)
      AOSKit  : X-Apple-I-MD / -MD-M   (OTP, machine ID)
      AuthKit : UDID, numéro de série, client-info akd
```

- `anisette-server.swift` : sert le JSON au format anisette-v3. N'écoute que sur
  l'IP tailnet ; seules les IP des workers sont servies (403 sinon). `--once`
  imprime l'anisette et sort.
- `install.sh` : compile et installe le LaunchAgent `com.sideloop.anisette`
  (log : `~/Library/Logs/sideloop-anisette.log`). Idempotent.
- `bootstrap-2fa.sh` : la 2FA unique qui fait truster ce Mac par le compte.

## Disponibilité

Le Mac est un portable : allumé en semaine aux heures de bureau, parfois le soir.
Le refresh tourne donc **toutes les heures** et :

- passe son tour sans rien historiser si le Mac est injoignable ;
- ne re-signe que les signatures de plus de 20 h (Apple sollicité ~1 fois par jour) ;
- après un login refusé, attend 3 h avant de retenter (marteler GrandSlam fait
  verrouiller les comptes, `-20209`).

Un certificat dure 7 jours ; le plus long trou (week-end) est d'environ 2,5 jours.
Si le Mac reste injoignable trop longtemps, l'alerte d'expiration de Nexus le dira.
L'**install** sur les iPhones, elle, exige toujours le Wi-Fi maison (agent pve) :
signer au bureau à 10 h et installer le soir est le cas normal.

## Pièges

- Joindre sa propre IP tailnet depuis le Mac ne marche pas : tester depuis un nœud
  (`ssh k8s-w1 curl -s http://100.102.18.22:6969/`).
- Chaque recompilation change la signature ad-hoc du binaire. Le pare-feu
  applicatif de macOS ne le bloque pas aujourd'hui (trafic tailnet) ; si ça change,
  c'est le premier suspect.
- Changer de Mac = nouvelle machine pour Apple = nouvelle 2FA.
