#!/usr/bin/env python3
"""RemotePairing (Wi-Fi) d'un iPhone iOS 17+/27 depuis pve.

Étape ONE-TIME : établit la confiance sans-fil (record RemotePairing) qui permet
ensuite à `pymobiledevice3 remote tunneld` de monter un tunnel Wi-Fi et
d'installer des apps SANS USB et SANS SideStore. À ne relancer que si le record
est perdu (reset iPhone, etc.).

Pourquoi ce wrapper plutôt que `pymobiledevice3 remote pair-host` directement :
pve est un routeur multi-interfaces (wlp4s0 192.168.2.100 = Wi-Fi maison,
vmbr0 10.10.10.1 = bridge cluster, tailscale0). Sans patch, l'annonce mDNS de
pair-host sort sur 10.10.10.1 (injoignable depuis l'iPhone) → le pairing ne peut
pas aboutir. Ce wrapper :
  1. force l'annonce mDNS sur l'IP Wi-Fi (192.168.2.100) ;
  2. force un identifiant d'hôte FRAIS (sinon l'iPhone, s'il a déjà appairé cet
     hôte via USB, se croit déjà appairé et ne relance jamais le pairing).

Usage (sur pve, iPhone sur le MÊME Wi-Fi que pve, Developer Mode ON) :
    /root/.local/share/pipx/venvs/pymobiledevice3/bin/python pair-host-pve.py

Puis sur l'iPhone : Réglages > Développeur > Mac associés > tape "PVE" > Associer
> entre le PASSCODE de l'iPhone > PUIS le code à 6 chiffres affiché par ce script.
(⚠ le code apparaît APRÈS le passcode, à taper dans les temps — c'est le piège
qui nous a coûté des heures.)

Le record est écrit dans /root/.pymobiledevice3/remote_<UDID>.plist.
"""

import socket
import sys
import uuid

import pymobiledevice3.bonjour as bonjour

# 1. Annoncer sur l'IP Wi-Fi maison (adapter si l'IP de pve change).
WIFI_IP = "192.168.2.100"
bonjour._local_addresses = lambda: [(socket.AF_INET, WIFI_IP)]

# 2. Identifiant d'hôte frais → l'iPhone voit un hôte inconnu et pair pour de vrai.
from pymobiledevice3.cli import remote as _cli_remote  # noqa: E402

_orig_host_info = _cli_remote.PairableHostInfo
_FRESH_ID = str(uuid.uuid4()).upper()


def _fresh_host_info(*args, **kwargs):
    kwargs.setdefault("identifier", _FRESH_ID)
    return _orig_host_info(*args, **kwargs)


_cli_remote.PairableHostInfo = _fresh_host_info

from pymobiledevice3.__main__ import main  # noqa: E402

sys.argv = ["pymobiledevice3", "remote", "pair-host",
            "--name", "PVE", "--port", "49333", "--timeout", "900"]
sys.exit(main())
