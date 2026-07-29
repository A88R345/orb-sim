"""
orb_notify.py — Envoi de notifications Discord via webhook.

L'URL du webhook est lue depuis la variable d'environnement
DISCORD_WEBHOOK_URL (stockée en secret GitHub Actions -> jamais en dur
dans le code, jamais commitée).
"""

import os
import requests

WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")


def notify(message: str) -> None:
    if not WEBHOOK_URL:
        print("[notify] DISCORD_WEBHOOK_URL non défini — notification affichée seulement :")
        print(message)
        return
    try:
        resp = requests.post(WEBHOOK_URL, json={"content": message}, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        # Un échec Discord ne doit jamais faire planter le run ni corrompre le CSV
        print(f"[notify] échec de l'envoi Discord : {e}")
