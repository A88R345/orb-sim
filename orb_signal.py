"""
orb_signal.py — Logique de la stratégie ORB SHORT T2 (coeur, sans I/O).

MISE A JOUR : reécrit pour matcher EXACTEMENT le code validé retrouvé dans
le PDF d'origine (original_walkforward.py) — la version précédente de ce
fichier avait plusieurs écarts avec la vraie logique validée :
  - Filtre OR_RANGE_MIN/MAX absent -> AJOUTÉ (10-200 pts)
  - Seuil T2 = 25 pts -> CONFIRMÉ (validé par walk-forward complet, pas
    arbitraire comme indiqué avant)
  - Pullback T2 sans buffer -> CORRIGÉ (+3 pts requis, comme le code validé)
  - Recherche T2 depuis l'entrée de T1 -> CORRIGÉ (doit partir de la SORTIE de T1)
  - Cap de risque journalier T1+T2 absent -> AJOUTÉ ($800 max combiné)
  - Slippage 0.5pt -> CORRIGÉ (1.0pt, comme le code validé)
  - Commission absente -> AJOUTÉE ($0.50/contrat)
  - Cap contrats à 8 -> CORRIGÉ (12, comme le code validé)
  - Sizing par formule de risque -> AJOUTÉ (capital $50k, 1% risque/trade)

Toutes les heures sont en UTC. Rappel des correspondances (heure d'été
Paris = UTC+2) :
  - Pré-session (régression 12 bougies) : 10:30 -> 13:30 UTC (12h30 -> 15h30 Paris)
  - Opening Range (2 bougies 15min)     : 13:30 -> 14:00 UTC (15h30 -> 16h00 Paris)
  - Fenêtre d'entrée (T1 et T2)         : 14:00 -> 17:00 UTC (16h00 -> 19h00 Paris)
  - Flat obligatoire                    : 19:30 UTC           (21h30 Paris)
"""

from __future__ import annotations
import datetime as dt
from dataclasses import dataclass
import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# PARAMÈTRES — tous confirmés exacts contre le code validé d'origine
# ---------------------------------------------------------------------------
LOOKBACK_CANDLES = 12          # 12 x 15min = 3h de régression pré-session
SL_MULT = 0.2                  # SL = entry + SL_MULT * OR_range
TP_MULT = 0.6                  # TP = entry - TP_MULT * OR_range
SLIPPAGE_PTS = 1.0              # confirmé (etait 0.5 avant, faux)
COMMISSION_PER_CONTRACT = 0.50   # confirmé, absente avant

OR_RANGE_MIN = 10.0             # confirmé, absent avant -> jours filtrés en dehors
OR_RANGE_MAX = 200.0

T2_ENABLED = True
T2_RANGE_THRESHOLD_PTS = 25.0    # confirmé par walk-forward complet (pas arbitraire)
PULLBACK_PTS = 3.0               # confirmé, absent avant (pullback devait etre > or_low + 3)
MAX_DAILY_RISK_USD = 800.0        # confirmé, absent avant (cap risque T1+T2 combine)

# Sizing (formule confirmée du code validé)
CAPITAL = 50_000.0
RISK_PCT = 0.01
POINT_VALUE_USD = 2.0            # MNQ = $2/point
MAX_CONTRACTS = 12                # confirmé (etait 8 avant, faux)
MIN_CONTRACTS = 1

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
    contracts: int
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
        self.pnl_usd = self.pnl_pts * POINT_VALUE_USD * self.contracts - COMMISSION_PER_CONTRACT * self.contracts


def _at(day: pd.Timestamp, t: dt.time) -> pd.Timestamp:
    return pd.Timestamp.combine(day.date(), t).tz_localize("UTC")


def size_contracts(sl_pts: float, risk_budget: float = None) -> int:
    """Formule de sizing confirmée : floor(risque$ / (sl_pts * valeur_point)), cap 1-12."""
    budget = risk_budget if risk_budget is not None else (CAPITAL * RISK_PCT)
    if sl_pts <= 0:
        return MIN_CONTRACTS
    n = int(budget / (sl_pts * POINT_VALUE_USD))
    return max(MIN_CONTRACTS, min(MAX_CONTRACTS, n))


def compute_bias(df: pd.DataFrame, or_start: pd.Timestamp, lookback_candles: int = LOOKBACK_CANDLES) -> float | None:
    """Régression OLS sur les `lookback_candles` bougies avant l'OR.
    Retourne la pente, ou None si pas encore assez de données (idempotent)."""
    window = df.loc[df.index < or_start].tail(lookback_candles)
    if len(window) < lookback_candles:
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
    SL verifie en premier en cas d'ambiguite (meme convention que le code validé)."""
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
    Recalcule tout depuis les données disponibles -> rejouable/idempotent.

    Logique EXACTE du code validé (original_walkforward.py), OR_RANGE_T2_MIN=25.
    """
    events: list[TradeEvent] = []

    or_start = _at(day, OR_START)
    or_end = _at(day, OR_END)
    entry_end = _at(day, ENTRY_END)
    flat_at = _at(day, FLAT_TIME)

    slope = compute_bias(df, or_start)
    if slope is None or slope >= 0:
        return events  # pas assez de données, ou biais pas assez baissier -> SHORT only

    or_levels = compute_opening_range(df, or_start, or_end)
    if or_levels is None:
        return events  # OR pas encore formé
    or_high, or_low = or_levels
    or_range = or_high - or_low
    if or_range < OR_RANGE_MIN or or_range > OR_RANGE_MAX:
        return events  # confirmé : jours avec range trop petit/trop grand exclus

    sl_pts = SL_MULT * or_range
    tp_pts = TP_MULT * or_range
    nb_c = size_contracts(sl_pts)

    post_or = df.loc[(df.index >= or_end) & (df.index < entry_end)]

    # --- T1 : cassure de OR_low dans la direction du biais ---
    t1 = None
    for ts, row in post_or.iterrows():
        if row["Low"] < or_low:
            entry_price = or_low - SLIPPAGE_PTS
            t1 = TradeEvent(
                trade_id="T1", direction="SHORT", entry_time=ts,
                entry_price=entry_price,
                sl=entry_price + sl_pts,
                tp=entry_price - tp_pts,
                or_low=or_low, or_high=or_high, bias_slope=slope,
                contracts=nb_c,
            )
            events.append(t1)
            break

    if t1 is not None:
        _scan_exit(df, t1, t1.entry_time, flat_at)

    # --- T2 : conditionnel au range de l'OR (décidé AVANT le résultat de T1) ---
    t2_eligible = T2_ENABLED and or_range >= T2_RANGE_THRESHOLD_PTS
    if t2_eligible and t1 is not None and t1.exit_time is not None:
        t1_risk_usd = sl_pts * POINT_VALUE_USD * nb_c
        t2_risk_budget = MAX_DAILY_RISK_USD - t1_risk_usd
        if t2_risk_budget > 0:
            nb_c_t2 = size_contracts(sl_pts, risk_budget=t2_risk_budget)
            # cherche le pullback (>= or_low + PULLBACK_PTS) PUIS une nouvelle cassure,
            # recherche demarree APRES LA SORTIE DE T1 (pas son entree)
            after_t1 = post_or.loc[post_or.index > t1.exit_time]
            pulled_back = False
            for ts, row in after_t1.iterrows():
                if not pulled_back:
                    if row["High"] >= or_low + PULLBACK_PTS:
                        pulled_back = True
                    continue
                if row["Low"] < or_low:
                    entry_price = or_low - SLIPPAGE_PTS
                    t2 = TradeEvent(
                        trade_id="T2", direction="SHORT", entry_time=ts,
                        entry_price=entry_price,
                        sl=entry_price + sl_pts,
                        tp=entry_price - tp_pts,
                        or_low=or_low, or_high=or_high, bias_slope=slope,
                        contracts=nb_c_t2,
                    )
                    events.append(t2)
                    _scan_exit(df, t2, t2.entry_time, flat_at)
                    break

    return events