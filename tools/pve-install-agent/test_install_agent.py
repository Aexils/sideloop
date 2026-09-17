"""Scénarios de joignabilité de l'agent d'install — `python3 test_install_agent.py`.

Sans dépendance (ni pytest) : l'agent tourne sur pve avec le python du système.
Le scénario 2 est une NON-RÉGRESSION : l'agent redémarrait tunneld dès qu'une
cible manquait, ce qui détruisait les tunnels des appareils présents et perdait
le run entier (2026-09-17). Les stubs remplacent tunneld et l'horloge.
"""
import importlib.util, types
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "agent", str(Path(__file__).with_name("install_agent.py")))
ag = importlib.util.module_from_spec(spec); spec.loader.exec_module(ag)

A, B, C = "udid-A", "udid-B", "udid-C"
TARGETS = {A, B, C}

def setup(listed, responding, *, back_after_sec=None, back=None):
    """Horloge fictive ; `back_after_sec` = délai de rétablissement après restart."""
    st = {"restarts": 0, "slept": 0}
    clock = {"t": 1000.0}
    def sleep(sec):
        clock["t"] += sec; st["slept"] += sec
    ag.time = types.SimpleNamespace(time=lambda: clock["t"], sleep=sleep)

    def settled():
        return (st["restarts"] == 0
                or (back_after_sec is not None and st["slept"] >= back_after_sec))
    ag._tunneld_udids = lambda: list(listed) if settled() else []
    ag.tunnel_ready = lambda u: u in (responding if st["restarts"] == 0
                                      else (back if back is not None else responding))
    ag.restart_tunneld = lambda: st.__setitem__("restarts", st["restarts"] + 1)
    ag._tunneld_up = lambda timeout=60: True
    return st

ag.absence_reason = lambda u: "pas annoncé par tunneld (appareil hors du Wi-Fi maison ?)"

def run(label, expect, st):
    got = ag.reachable_udids(set(TARGETS))
    print(f"{'PASS' if got == expect else 'FAIL'}  {label}")
    print(f"        joignables={sorted(got)} restarts={st['restarts']} attente={st['slept']}s")
    assert got == expect, f"attendu {sorted(expect)}, obtenu {sorted(got)}"

print("=== 1. tout répond → aucun restart ===")
st = setup({A,B,C}, {A,B,C}); run("3/3 joignables", {A,B,C}, st)
assert st["restarts"] == 0, "ne doit PAS redémarrer tunneld"

print("\n=== 2. LE BUG DU 2026-09-17 : un absent, deux vivants ===")
st = setup({A,B,C}, {A,B})            # C parti de la maison, entrée tunneld périmée
run("sert les 2 présents", {A,B}, st)
assert st["restarts"] == 0, "AVANT : restart → les 3 devenaient muets, run perdu"

print("\n=== 2b. plus rien ne répond, mais tous PRÉSENTS et en veille ===")
st = setup({A,B,C}, set())
ag.absence_reason = lambda u: "présent sur le réseau (192.168.2.x) mais lockdown ne répond pas — appareil verrouillé ou en veille"
run("pas de restart : un reboot de tunneld ne réveille personne", set(), st)
assert st["restarts"] == 0, "restart inutile de 3 min, et casse les tunnels des autres"
ag.absence_reason = lambda u: "pas annoncé par tunneld (appareil hors du Wi-Fi maison ?)"

print("\n=== 3. plus rien → restart, tunnels lents (78 s, comme mesuré) ===")
st = setup({A,B,C}, set(), back_after_sec=78, back={A,B,C})
run("attend au-delà de l'ancien plafond de 45 s", {A,B,C}, st)
assert st["restarts"] == 1 and st["slept"] > 45, f"n'a attendu que {st['slept']}s"

print("\n=== 4. plus rien, et un appareil vraiment parti → sortie anticipée ===")
st = setup({A,B,C}, set(), back_after_sec=16, back={A,B})
run("ne bloque pas 180 s pour l'absent", {A,B}, st)
assert st["slept"] < ag.TUNNEL_SETTLE_SEC, "doit sortir avant le plafond"

print("\nTOUS LES SCÉNARIOS PASSENT")
