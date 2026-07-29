"""
run_orb_sim.py — Orchestrateur.

Récupère les données, calcule les trades du jour (T1/T2), compare avec
le CSV déjà loggé (idempotent — ne double jamais une entrée déjà notée),
ajoute les nouveaux événements, notifie Discord pour toute nouveauté
(entrée ou sortie), et laisse le CSV prêt à être commité par le workflow.

Conçu pour tourner plusieurs fois par jour sans effet de bord : à chaque
run, tout est recalculé depuis les données dispo, donc un run manqué,
en retard, ou en double ne pose aucun problème.
"""

from pathlib import Path

import pandas as pd

from orb_data import fetch_15min
from orb_signal import run_day, TradeEvent
from orb_notify import notify

CSV_PATH = Path("data/trades_sim.csv")
COLUMNS = [
    "date", "trade_id", "direction", "entry_time", "entry_price", "sl", "tp",
    "or_low", "or_high", "bias_slope", "exit_time", "exit_price",
    "exit_reason", "pnl_pts", "pnl_usd",
]


def load_existing() -> pd.DataFrame:
    if CSV_PATH.exists():
        df = pd.read_csv(CSV_PATH)
        for col in ["entry_time", "exit_time"]:
            df[col] = pd.to_datetime(df[col], utc=True, errors="coerce")
        return df
    return pd.DataFrame(columns=COLUMNS)


def events_to_rows(events: list[TradeEvent], date_str: str) -> list[dict]:
    rows = []
    for e in events:
        rows.append({
            "date": date_str,
            "trade_id": e.trade_id,
            "direction": e.direction,
            "entry_time": e.entry_time,
            "entry_price": round(e.entry_price, 2),
            "sl": round(e.sl, 2),
            "tp": round(e.tp, 2),
            "or_low": round(e.or_low, 2),
            "or_high": round(e.or_high, 2),
            "bias_slope": round(e.bias_slope, 4),
            "exit_time": e.exit_time,
            "exit_price": round(e.exit_price, 2) if e.exit_price is not None else None,
            "exit_reason": e.exit_reason,
            "pnl_pts": round(e.pnl_pts, 2) if e.pnl_pts is not None else None,
            "pnl_usd": round(e.pnl_usd, 2) if e.pnl_usd is not None else None,
        })
    return rows


def notify_entry(r: dict) -> None:
    notify(
        f"**Signal {r['trade_id']} SHORT détecté — {r['date']}**\n"
        f"Entrée : {r['entry_price']} | SL : {r['sl']} | TP : {r['tp']}\n"
        f"OR : {r['or_low']}–{r['or_high']} | Pente pré-session : {r['bias_slope']}"
    )


def notify_exit(r: dict) -> None:
    sign = "+" if (r["pnl_usd"] or 0) >= 0 else ""
    notify(
        f"**Trade {r['trade_id']} clôturé ({r['exit_reason']}) — {r['date']}**\n"
        f"Sortie : {r['exit_price']} | PnL : {r['pnl_pts']} pts "
        f"({sign}{r['pnl_usd']}$ sur MNQ)"
    )


def main() -> None:
    today = pd.Timestamp.now(tz="UTC").normalize()
    date_str = today.strftime("%Y-%m-%d")

    df = fetch_15min(lookback_days=5)
    events = run_day(df, today)
    new_rows = events_to_rows(events, date_str)

    existing = load_existing()
    already_logged = set(
        zip(existing["date"].astype(str), existing["trade_id"])
    ) if not existing.empty else set()

    to_append = [r for r in new_rows if (r["date"], r["trade_id"]) not in already_logged]
    to_update = [r for r in new_rows if (r["date"], r["trade_id"]) in already_logged]

    if to_append:
        CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
        new_df = pd.DataFrame(to_append, columns=COLUMNS)
        existing = pd.concat([existing, new_df], ignore_index=True)
        for r in to_append:
            notify_entry(r)
            if r["exit_reason"] is not None:
                # cas rare : SL/TP touché dans la même bougie que l'entrée
                notify_exit(r)

    for r in to_update:
        mask = (existing["date"].astype(str) == r["date"]) & (existing["trade_id"] == r["trade_id"])
        was_open = existing.loc[mask, "exit_reason"].isna().all()
        for col in COLUMNS:
            existing.loc[mask, col] = r[col]
        if was_open and r["exit_reason"] is not None:
            notify_exit(r)

    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing.to_csv(CSV_PATH, index=False)
    print(f"[{date_str}] {len(events)} événement(s) traité(s) — CSV à jour : {CSV_PATH}")


if __name__ == "__main__":
    main()
