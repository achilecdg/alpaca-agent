# Agent de trading autonome — Alpaca + Claude

Une expérience : on confie un petit capital à une IA qui **décide et exécute seule** ses
ordres en bourse (actions US via Alpaca). Par défaut tout tourne en **simulation (paper)**.

Boucle à chaque exécution : lit le marché → **Claude décide (JSON)** → le code passe les
ordres → tout est journalisé pour analyse.

---

## 1. Prérequis

- **Python 3.10+**
- Un compte **Alpaca** (l'inscription à la *Trading API* est ouverte aux résidents France).
  Récupère tes clés dans l'onglet **Paper Trading** du dashboard pour commencer sans risque.
- Une **clé API Anthropic** (console.anthropic.com → API Keys). L'usage de Claude est facturé
  à l'appel, mais quelques décisions/semaine coûtent quelques centimes.

## 2. Installation

```bash
cd alpaca-agent
python -m venv .venv && source .venv/bin/activate   # optionnel mais conseillé
pip install -r requirements.txt
```

## 3. Configuration

```bash
cp .env.example .env
```

Ouvre `.env` et remplis `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `ANTHROPIC_API_KEY`.
Laisse `ALPACA_PAPER=true` pour l'instant. Ajuste éventuellement la `WATCHLIST` et les
garde-fous. **Ne partage jamais ton `.env`.**

## 4. Lancer une décision (paper)

```bash
python agent.py
```

Chaque exécution fait UNE passe : si le marché est ouvert, Claude décide et l'agent agit,
sinon il note simplement que le marché est fermé. Les événements s'affichent en console et
s'écrivent dans `trades_log.jsonl`.

Astuce pour observer avant de lâcher la bride : mets `DRY_RUN=true` — Claude décide et tout
est journalisé, mais **aucun ordre n'est réellement envoyé**.

## 5. Automatiser (le cœur de « il exécute seul »)

Le script fait une passe puis s'arrête : c'est un **cron** (Linux/Mac) ou le **Planificateur
de tâches** (Windows) qui le déclenche à la fréquence choisie. Exemple : une décision chaque
lundi à 16 h, avec un journal dédié.

```cron
0 16 * * 1  cd /chemin/vers/alpaca-agent && ./.venv/bin/python agent.py >> cron.log 2>&1
```

(Pour une cadence quotidienne : `0 16 * * 1-5`. Les horaires du marché US sont en heure de
New York — pense au décalage.)

Pour un fonctionnement **ordinateur éteint**, voir `SETUP_GITHUB.md` (GitHub Actions).

## 6. Passer en réel — seulement après du paper concluant

Deux verrous volontaires, à changer **tous les deux** dans `.env` :

```
ALPACA_PAPER=false
I_UNDERSTAND_LIVE_RISK=yes
```

Utilise alors tes clés **live** Alpaca (différentes des clés paper), après avoir approvisionné
le compte. Recommandation : plusieurs semaines de simulation d'abord, puis tes 50 €.

## 7. Garde-fous intégrés (appliqués par le code, pas par l'IA)

| Garde-fou | Variable | Effet |
|---|---|---|
| Liste blanche | `WATCHLIST` | l'agent ne touche AUCUN ticker hors liste |
| Plafond par ordre | `MAX_NOTIONAL_PER_ORDER` | montant max engagé par ordre |
| Quota journalier | `MAX_ORDERS_PER_DAY` | nombre max d'ordres par jour |
| Kill switch | `MAX_DRAWDOWN_PCT` | coupe tout achat au-delà d'un % de perte |
| Verrous réel | `ALPACA_PAPER` + `I_UNDERSTAND_LIVE_RISK` | double opt-in pour l'argent réel |

## 8. Analyser le journal

`trades_log.jsonl` contient une ligne JSON par événement (décision, ordre, raison, équité).
Idéal pour comparer la perf de l'IA à la tienne — et à un ETF témoin (MSCI World / S&P 500) —
et pour en tirer un récit.

---

## Idée d'expérience honnête

Trois concurrents, même capital et même durée : **toi**, **l'agent**, et un **ETF indiciel**
laissé tranquille. Si aucun des deux ne bat l'ETF, c'est ça, l'enseignement.

## Avertissement

Ce projet est pédagogique. Ce n'est ni un conseil en investissement, ni une garantie de
résultat. Sur un petit capital et quelques semaines, la performance tient largement à la
chance. Tu trades ton propre argent, sur ton propre compte, avec tes propres clés, sous ta
responsabilité.
