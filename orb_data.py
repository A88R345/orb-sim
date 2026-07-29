"""
orb_data.py — Récupération des données NQ 15min.

Source : Yahoo Finance via yfinance (gratuit, pas de clé API, historique
intraday 15min dispo sur ~60 jours glissants — largement suffisant puisqu'on
n'a besoin que de la journée en cours).

Le signal est calculé sur NQ=F (E-mini Nasdaq 100, contrat continu). Seule
la conversion en $ dans orb_signal.POINT_VALUE_USD change si tu veux
raisonner en NQ ($20/pt) plutôt qu'en MNQ ($2/pt) — la logique de signal
est identique, ce sont les mêmes points de prix.
"""

import pandas as pd
import yfinance as yf

TICKER = "NQ=F"


def fetch_15min(lookback_days: int = 5) -> pd.DataFrame:
    """Récupère les bougies 15min des derniers `lookback_days` jours, en UTC."""
    df = yf.download(
        TICKER,
        period=f"{lookback_days}d",
        interval="15m",
        progress=False,
        auto_adjust=False,
    )
    if df.empty:
        raise RuntimeError(f"Aucune donnée reçue pour {TICKER} (yfinance a renvoyé un DataFrame vide)")

    # yfinance renvoie parfois des colonnes multi-index (ticker en 2e niveau)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")

    return df[["Open", "High", "Low", "Close", "Volume"]].sort_index()
