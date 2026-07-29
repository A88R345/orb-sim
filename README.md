# orb-sim — Simulateur de signaux ORB SHORT T2

Aucune exécution. Ce projet ne fait que **détecter et logger** les signaux
de la stratégie sur NQ en temps réel, et notifier Discord. Zéro ordre,
zéro capital engagé.

## Mise en place (une seule fois)

1. **Secret GitHub** : Settings → Secrets and variables → Actions →
   New repository secret → nom `DISCORD_WEBHOOK_URL`, valeur = l'URL de
   ton webhook Discord. (déjà fait si tu avais suivi les instructions
   précédentes)
2. Vérifier que **Settings → Actions → General → Workflow permissions**
   est sur "Read and write permissions" (nécessaire pour que le bot
   puisse commiter le CSV automatiquement).
3. Pousser ces fichiers sur `main`. Le workflow se lance tout seul selon
   le planning cron, ou manuellement via l'onglet **Actions → ORB SHORT
   T2 - Simulation de signaux → Run workflow** (pratique pour tester
   sans attendre le prochain créneau).

## Ce qui est calibré vs. ce qui est un défaut arbitraire

| Paramètre | Valeur | Statut |
|---|---|---|
| SL | 0.2 × Range OR | Calibré (walk-forward OOS validé) |
| TP | 0.6 × Range OR | Calibré (walk-forward OOS validé) |
| SHORT only | oui | Calibré (LONG dégrade systématiquement) |
| Look-back régression | 12 bougies pré-session | Calibré |
| `T2_RANGE_THRESHOLD_PTS` | 25.0 (dans `orb_signal.py`) | **Arbitraire, à ajuster** |
| Sizing / risque $ par trade | non implémenté (log en points) | **À définir si besoin** |

Les deux paramètres marqués arbitraires n'ont pas été retrouvés dans
l'historique récupéré de la conversation d'origine. Ils n'affectent que
la fréquence/l'ampleur des signaux affichés, pas la logique de fond.
Ajuste `T2_RANGE_THRESHOLD_PTS` directement dans `orb_signal.py` en
observant le CSV après quelques semaines.

## Fichiers

- `orb_signal.py` — le cœur : calcul de l'OR, du biais, détection T1/T2, SL/TP.
- `orb_data.py` — récupération des données NQ 15min (Yahoo Finance, gratuit).
- `orb_notify.py` — envoi des messages Discord.
- `run_orb_sim.py` — orchestrateur, idempotent (rejouable sans jamais dupliquer une ligne).
- `data/trades_sim.csv` — le log de tous les trades simulés (créé au premier run).
- `.github/workflows/orb_sim.yml` — le scheduler cloud (remplace un VPS/PC allumé).

## Limites connues (honnêtes, pas de survente)

- **yfinance / Yahoo Finance** : gratuit et sans clé API, mais pas de SLA —
  peut ponctuellement renvoyer des données en retard ou vides. Le script
  gère ce cas proprement (aucune ligne loggée ce run-là, retenté au run
  suivant), mais ça peut faire louper un signal ponctuellement.
- **Cron GitHub Actions n'est pas garanti à la minute près** — sur le
  tier gratuit, un déclenchement peut être retardé de quelques minutes,
  parfois plus en période de forte charge sur l'infra GitHub. La logique
  idempotente absorbe ça, mais une entrée détectée en retard = un prix
  d'entrée simulé légèrement décalé du vrai breakout.
- **Le fuseau horaire est géré uniquement en UTC** dans le code — les
  correspondances heure de Paris données dans les commentaires sont
  pour la lecture humaine seulement, aucune conversion DST n'est faite
  en dur (pas nécessaire puisque tout tourne en UTC de bout en bout).
- **Ceci reste un backtest live**, pas un forward test parfait : les prix
  Yahoo pour les futures continus peuvent différer légèrement (quelques
  ticks) de ce que ton broker afficherait en direct.
