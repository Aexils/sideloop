"""2FA unique : faire truster CE Mac par le compte Apple de sideloop.

Lancé par bootstrap-2fa.sh, dans un terminal (mot de passe et code SMS au
clavier). Fait UN login GrandSlam avec l'anisette native du Mac, exactement
comme le refresh (même UDID, même numéro de série, même client-info akd) ;
Apple demande la 2FA une fois, après quoi ce Mac est une machine trustée et
le CronJob se connecte sans 2FA.
"""

import getpass
import json
import subprocess
import sys

from grandslam import gsa


class MacAnisette(gsa.Anisette):
    """L'anisette du Mac lue directement (`anisette-server --once`), sans HTTP."""

    def __init__(self, binary: str) -> None:
        self._name = "mac"
        self._url = binary
        self._anisette = json.loads(subprocess.check_output([binary, "--once"]))

    # Même identité que NativeAnisette dans src/sideloop/signing.py.
    client = property(lambda self: self._anisette["X-MMe-Client-Info"])
    device = property(lambda self: self._anisette["X-Mme-Device-Id"])
    serial = property(lambda self: self._anisette["X-Apple-I-SRL-NO"])
    local_user = property(lambda self: self._anisette["X-Apple-I-MD-LU"])
    router = property(lambda self: self._anisette["X-Apple-I-MD-RINFO"])


def main() -> int:
    binary, apple_id = sys.argv[1], sys.argv[2]
    anisette = MacAnisette(binary)
    print(f"Compte : {apple_id}\nClient : {anisette.client}")
    password = getpass.getpass("Mot de passe Apple (rien ne s'affiche) : ")
    # Le code (SMS ou appareil de confiance) est demandé par grandslam lui-même.
    spd = gsa.authenticate(apple_id, password, anisette)
    if not isinstance(spd, dict) or "adsid" not in spd:
        print("\nÉCHEC : login refusé (Status imprimé ci-dessus). Ne pas relancer en "
              "boucle : Apple verrouille les comptes martelés.")
        return 1
    print("\nOK : ce Mac est trusté pour ce compte. Le CronJob peut signer.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
