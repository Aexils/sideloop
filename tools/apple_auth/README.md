# apple_auth — auth Apple + portail développeur (signer maison, Python)

Chaîne d'authentification Apple **complète, en Python, 2FA-free** pour le signer de
sideloop. C'est le morceau difficile (crypto SRP, anisette, 2FA, protocoles non
documentés) — **il fonctionne** (validé 2026-07-13 : `listTeams` → team OK).

## Ce qui tourne sur pve (état actuel)

- **Serveur anisette stable** : `anisette-v3-server` (buildé depuis les sources D,
  `/opt/anisette-v3-server`), tourne sur `127.0.0.1:6969`. Sa machine anisette
  (adi.pb) a été **trustée** par le compte via un 2FA SMS une seule fois → les
  logins suivants sont **sans 2FA**. NE PAS reprovisionner (repartirait à 2FA).
- **grandslam** (`JJTech0130/grandslam`) cloné dans `/opt/grandslam`, venv, PATCHÉ
  (voir `grandslam-gsa.patch`) :
  - `sms_second_factor` implémenté (envoi `PUT /auth/verify/phone` **sans slash**,
    soumission `POST /auth/verify/phone/securitycode`, via une `requests.Session`) ;
  - `authenticate` renvoie désormais le `spd` (adsid, GsIdmsToken, sk, c).
- **devauth.py** : `fetch_app_token()` — flux GrandSlam `apptokens`, déchiffrement
  AES-GCM du token (`et`, AAD = les 3 octets de version `b"XYZ"`, header plist à
  préfixer). Donne le token `com.apple.gs.xcode.auth` (valide 1 an).
- **dev_portal.py** : `dev_request(action, gs_token, adsid, anisette, extra)` —
  requêtes plist vers `developerservices2.apple.com/services/QH65B2/`. Auth =
  headers `X-Apple-I-Identity-Id` (adsid) + `X-Apple-GS-Token` (token) + anisette.
  ⚠ `listTeams.action` / `viewDeveloper.action` = à la RACINE ; `ios/…` pour le
  reste. `clientId=XABBG36SBA`. **Validé : listTeams → 245M5C8BJT.**

Secret : mot de passe du compte dans `/root/.sideloader-pw` (chmod 600, hors Git).
Compte : `levasseur.alexis@uqam.ca`, team `245M5C8BJT` (Individual, gratuit).

## Reste à câbler (mêmes patterns, portail dev débloqué)

1. `ios/listDevices.action` / `ios/addDevice.action` — enregistrer les UDID (3 devices).
2. `ios/listAllDevelopmentCerts.action` + `ios/submitDevelopmentCSR.action` — cert
   (générer clé+CSR openssl, soumettre, récupérer le cert).
3. `ios/listAppIds.action` / `ios/addAppId.action` — App IDs (com.google.ios.youtube,
   com.spotify.client). ⚠ limite compte gratuit : 10 App IDs/sem, 3 apps actives.
4. `ios/downloadTeamProvisioningProfile.action` — profil .mobileprovision (device+appid+cert).
5. `zsign` (déjà sur pve) : signer l'IPA avec cert+clé+profil.
6. Install via le tunnel Wi-Fi (déjà prouvé, voir mémoire `sideloop-project`).

Puis : intégrer tout ça dans `sideloop/src/sideloop/apple.py` (SEAM) + CronJob.
