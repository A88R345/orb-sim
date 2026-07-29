"""
orb_signal.py — Logique de la stratégie ORB SHORT T2 (coeur, sans I/O).

Toutes les heures sont en UTC. Rappel des correspondances (heure d'été
Paris = UTC+2) :
  - Pré-session (régression 12 bougies) : 10:30 -> 13:30 UTC (12h30 -> 15h30 Paris)
  - Opening Range (2 bougies 15min)     : 13:30 -> 14:00 UTC (15h30 -> 16h00 Paris)
  - Fenêtre d'entrée (T1 et T2)         : 14:00 -> 17:00 UTC (16h00 -> 19h00 Paris)
  - Flat obligatoire                    : 19:30 UTC           (21h30 Paris)

Deux paramètres n'ont pas été retrouvés dans l'historique de la conversation
récupérée (PDF) et sont donc des valeurs par défaut à calibrer toi-même en
observant les signaux loggés, pas des vérités figées :
  - T2_RANGE_THRESHOLD_PTS : seuil de range de l'OR (en points NQ) au-delà
    duquel un 2e trade (T2) est autorisé.
  - POINT_VALUE_USD : ne sert qu'à convertir le PnL en points en un PnL en
    dollars pour le log/Discord — n'affecte jamais la logique d'entrée/sortie.

Le reste (SL=0.2xRange, TP=0.6xRange, SHORT only, biais = signe d'une
régression OLS sur 12 bougies pré-session) correspond au dernier récap
complet validé dans la conversation d'origine.
"""

from __future__ import annotations
import datetime as dt
from dataclasses import dataclass
import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# PARAMÈTRES (modifiables)
# ---------------------------------------------------------------------------
LOOKBACK_CANDLES = 12          # 12 x 15min = 3h de régression pré-session
SL_MULT = 0.2                  # SL = entry + SL_MULT * OR_range
TP_MULT = 0.6                  # TP = entry - TP_MULT * OR_range
SLIPPAGE_PTS = 0.5              # points de slippage simulé à l'entrée
T2_ENABLED = True
T2_RANGE_THRESHOLD_PTS = 25.0    # <-- A CALIBRER, valeur par défaut arbitraire
POINT_VALUE_USD = 2.0           # MNQ = $2/point (mets 20.0 pour NQ)

OR_START = dt.time(13, 30)
OR_END = dt.time(14, 0)
ENTRY_END = dt.time(17, 0)
FLAT_TIME = dt.time(19, 30)


@dataclass
class TradeEvent:
    trade_id: str                 # "T1" ou "T2"
    direction: str                # "SHORT"
    entry_time: pd.Timestamp
    entry_price: float
    sl: float
    tp: float
    or_low: float
    or_high: float
    bias_slope: float
    exit_time: pd.Timestamp | None = None
    exit_price: float | None = None
    exit_reason: str | None = None     # "TP" / "SL" / "FLAT"
    pnl_pts: float | None = None
    pnl_usd: float | None = None

    def close(self, exit_time, exit_price, reason):
        self.exit_time = exit_time
        self.exit_price = exit_price
        self.exit_reason = reason
        self.pnl_pts = self.entry_price - exit_price   # SHORT : gain si le prix baisse
        self.pnl_usd = self.pnl_pts * POINT_VALUE_USD


def _at(day: pd.Timestamp, t: dt.time) -> pd.Timestamp:
    return pd.Timestamp.combine(day.date(), t).tz_localize("UTC")


def compute_bias(df: pd.DataFrame, or_start: pd.Timestamp) -> float | None:
    """Régression OLS sur les LOOKBACK_CANDLES bougies avant l'OR.
    Retourne la pente, ou None si pas encore assez de données (idempotent)."""
    window = df.loc[df.index < or_start].tail(LOOKBACK_CANDLES)
    if len(window) < LOOKBACK_CANDLES:
        return None
    y = window["Close"].values
    x = np.arange(len(y))
    slope = np.polyfit(x, y, 1)[0]
    return float(slope)


def compute_opening_range(df: pd.DataFrame, or_start: pd.Timestamp, or_end: pd.Timestamp):
    or_candles = df.loc[(df.index >= or_start) & (df.index < or_end)]
    if len(or_candles) < 2:
        return None
    return float(or_candles["High"].max()), float(or_candles["Low"].min())


def _scan_exit(df: pd.DataFrame, trade: TradeEvent, after: pd.Timestamp, flat_at: pd.Timestamp):
    """Scanne les bougies après `after` pour SL, TP ou FLAT.
    Ambiguïté SL+TP dans la même bougie -> on suppose le pire cas (SL) par prudence."""
    window = df.loc[(df.index > after) & (df.index <= flat_at)]
    for ts, row in window.iterrows():
        hit_sl = row["High"] >= trade.sl
        hit_tp = row["Low"] <= trade.tp
        if hit_sl:
            trade.close(ts, trade.sl, "SL")
            return
        if hit_tp:
            trade.close(ts, trade.tp, "TP")
            return
    if not window.empty:
        trade.close(window.index[-1], window["Close"].iloc[-1], "FLAT")
    # sinon : pas encore de données après l'entrée -> trade reste ouvert (normal en cours de journée)


def run_day(df: pd.DataFrame, day: pd.Timestamp) -> list[TradeEvent]:
    """
    Calcule les trades (T1 et T2 conditionnel) pour une journée `day`, à partir
    d'un DataFrame de bougies 15min indexé en UTC (colonnes Open/High/Low/Close).
    Ne suppose rien sur l'heure d'exécution : recalcule tout depuis les données
    disponibles -> rejouable/idempotent, peu importe quand le script tourne.
    """
    events: list[TradeEvent] = []

    or_start = _at(day, OR_START)
    or_end = _at(day, OR_END)
    entry_end = _at(day, ENTRY_END)
    flat_at = _at(day, FLAT_TIME)

    slope = compute_bias(df, or_start)
    if slope is None or slope >= 0:
        return events  # pas assez de données, ou biais LONG -> stratégie SHORT only

    or_levels = compute_opening_range(df, or_start, or_end)
    if or_levels is None:
        return events  # OR pas encore formé
    or_high, or_low = or_levels
    or_range = or_high - or_low
    if or_range <= 0:
        return events

    post_or = df.loc[(df.index >= or_end) & (df.index <= entry_end)]

    # --- T1 : cassure de OR_low dans la direction du biais ---
    t1 = None
    for ts, row in post_or.iterrows():
        if row["Low"] <= or_low:
            entry_price = or_low - SLIPPAGE_PTS
            t1 = TradeEvent(
                trade_id="T1", direction="SHORT", entry_time=ts,
                entry_price=entry_price,
                sl=entry_price + SL_MULT * or_range,
                tp=entry_price - TP_MULT * or_range,
                or_low=or_low, or_high=or_high, bias_slope=slope,
            )
            events.append(t1)
            break

    if t1 is not None:
        _scan_exit(df, t1, t1.entry_time, flat_at)

    # --- T2 : conditionnel au range de l'OR (décidé AVANT le résultat de T1,
    #          donc pas de lookahead) ; déclenché par un pullback puis un
    #          nouveau breakout de OR_low, indépendamment de si T1 gagne/perd ---
    if T2_ENABLED and t1 is not None and or_range > T2_RANGE_THRESHOLD_PTS:
        after_t1 = post_or.loc[post_or.index > t1.entry_time]
        pulled_back = False
        for ts, row in after_t1.iterrows():
            if not pulled_back:
                if row["High"] > or_low:
                    pulled_back = True
                continue
            if row["Low"] <= or_low:
                entry_price = or_low - SLIPPAGE_PTS
                t2 = TradeEvent(
                    trade_id="T2", direction="SHORT", entry_time=ts,
                    entry_price=entry_price,
                    sl=entry_price + SL_MULT * or_range,
                    tp=entry_price - TP_MULT * or_range,
                    or_low=or_low, or_high=or_high, bias_slope=slope,
                )
                events.append(t2)
                _scan_exit(df, t2, t2.entry_time, flat_at)
                break

    return events
