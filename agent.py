"""
Agent de trading autonome — Alpaca + Claude
============================================

Boucle : lit le marché -> Claude décide (JSON) -> le code exécute -> tout est journalisé.

⚠️  Par défaut, l'agent tourne en mode PAPER (simulation, argent fictif).
    Passer en réel demande DEUX variables d'environnement explicites (voir .env.example).
    Ne bascule en réel qu'après plusieurs semaines de paper trading concluant.

Ce script n'exécute que sur TON compte, avec TES clés API. Il n'est ni un conseil
en investissement ni une garantie de performance.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from dotenv import load_dotenv

# --- SDK Alpaca ---
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, GetOrdersRequest
from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import DataFeed

# --- SDK Anthropic (Claude) ---
import anthropic


# ----------------------------------------------------------------------------
# Configuration (tout se règle via le fichier .env — voir .env.example)
# ----------------------------------------------------------------------------

load_dotenv()


def _get_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "oui", "on"}


def _get_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _get_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


class Config:
    # Clés
    ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "")
    ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

    # Environnement Alpaca
    PAPER = _get_bool("ALPACA_PAPER", True)          # True = simulation
    BASE_URL = os.getenv("ALPACA_BASE_URL", "")       # override optionnel (compte EU)
    CONFIRM_LIVE = os.getenv("I_UNDERSTAND_LIVE_RISK", "").strip().lower() == "yes"

    # Modèle Claude (les noms évoluent — vérifie docs.claude.com/en/docs/about-claude/models)
    LLM_MODEL = os.getenv("LLM_MODEL", "claude-sonnet-5")

    # Univers autorisé (liste blanche stricte — l'agent ne touchera RIEN d'autre)
    WATCHLIST = [
        s.strip().upper()
        for s in os.getenv("WATCHLIST", "AAPL,MSFT,GOOGL,AMZN,NVDA").split(",")
        if s.strip()
    ]

    # --- Garde-fous ---
    MAX_NOTIONAL_PER_ORDER = _get_float("MAX_NOTIONAL_PER_ORDER", 10.0)  # $ max par ordre
    MAX_ORDERS_PER_DAY = _get_int("MAX_ORDERS_PER_DAY", 3)
    MAX_DRAWDOWN_PCT = _get_float("MAX_DRAWDOWN_PCT", 20.0)  # % de perte -> kill switch
    START_EQUITY_FILE = Path(os.getenv("START_EQUITY_FILE", "start_equity.txt"))

    # Journalisation
    LOG_FILE = Path(os.getenv("LOG_FILE", "trades_log.jsonl"))

    # Mode "dry run" : Claude décide et on journalise, mais AUCUN ordre n'est envoyé
    DRY_RUN = _get_bool("DRY_RUN", False)

    # Recherche web : Claude cherche l'actu récente sur la watchlist avant de décider
    # (indispensable pour un bot "opportuniste sur l'actu"). Coûte quelques recherches/passe.
    ENABLE_WEB_SEARCH = _get_bool("ENABLE_WEB_SEARCH", True)
    WEB_SEARCH_MAX_USES = _get_int("WEB_SEARCH_MAX_USES", 5)


# ----------------------------------------------------------------------------
# Utilitaires
# ----------------------------------------------------------------------------

def log_event(event: dict) -> None:
    """Ajoute une ligne JSON au journal (idéal pour analyser / raconter après)."""
    event = {"ts": datetime.now(timezone.utc).isoformat(), **event}
    with Config.LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
    # écho lisible en console
    print(f"[{event['ts']}] {event.get('type', 'event')}: "
          f"{json.dumps({k: v for k, v in event.items() if k not in ('ts', 'type')}, ensure_ascii=False)}")


def build_clients():
    """Instancie les clients Alpaca (trading + données) et Anthropic."""
    missing = [n for n, v in [
        ("ALPACA_API_KEY", Config.ALPACA_API_KEY),
        ("ALPACA_SECRET_KEY", Config.ALPACA_SECRET_KEY),
        ("ANTHROPIC_API_KEY", Config.ANTHROPIC_API_KEY),
    ] if not v]
    if missing:
        sys.exit(f"❌ Variables manquantes dans .env : {', '.join(missing)}")

    trading_kwargs = dict(paper=Config.PAPER)
    if Config.BASE_URL:
        trading_kwargs["url_override"] = Config.BASE_URL

    trading = TradingClient(Config.ALPACA_API_KEY, Config.ALPACA_SECRET_KEY, **trading_kwargs)
    data = StockHistoricalDataClient(Config.ALPACA_API_KEY, Config.ALPACA_SECRET_KEY)
    llm = anthropic.Anthropic(api_key=Config.ANTHROPIC_API_KEY)
    return trading, data, llm


def get_start_equity(current_equity: float) -> float:
    """Mémorise l'équité de départ (référence du kill switch et de la performance)."""
    if Config.START_EQUITY_FILE.exists():
        try:
            stored = float(Config.START_EQUITY_FILE.read_text().strip())
            if stored > 0:                 # une valeur <= 0 est invalide (compte pas encore financé)
                return stored
        except ValueError:
            pass
    # (re)écrit une référence saine à partir de l'équité réelle du moment
    Config.START_EQUITY_FILE.write_text(str(current_equity))
    return current_equity


def orders_today(trading: TradingClient) -> int:
    """Compte les ordres déjà passés aujourd'hui (respect de MAX_ORDERS_PER_DAY)."""
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    req = GetOrdersRequest(status=QueryOrderStatus.ALL, after=start, limit=500)
    return len(trading.get_orders(filter=req))


def fetch_market_snapshot(data: StockHistoricalDataClient) -> dict:
    """Récupère les dernières bougies journalières de la watchlist (résilient aux erreurs)."""
    snapshot: dict[str, list] = {}
    try:
        req = StockBarsRequest(
            symbol_or_symbols=Config.WATCHLIST,
            timeframe=TimeFrame.Day,
            start=datetime.now(timezone.utc) - timedelta(days=10),
            feed=DataFeed.IEX,  # flux gratuit
        )
        bars = data.get_stock_bars(req)
        for sym in Config.WATCHLIST:
            sym_bars = bars.data.get(sym, [])
            snapshot[sym] = [
                {"date": b.timestamp.date().isoformat(), "close": round(b.close, 2),
                 "volume": int(b.volume)}
                for b in sym_bars[-5:]
            ]
    except Exception as e:  # noqa: BLE001 — on ne bloque pas la boucle sur un souci de data
        log_event({"type": "data_warning", "message": f"Données indisponibles : {e}"})
    return snapshot


# ----------------------------------------------------------------------------
# Décision : on demande à Claude un JSON strict
# ----------------------------------------------------------------------------

SYSTEM_PROMPT = """Tu es un agent de trading qui gère un très petit portefeuille expérimental.
Tu ne peux agir QUE sur les tickers de la watchlist fournie.

STRATÉGIE (fixée par l'utilisateur) :
- Profil : ÉQUILIBRÉ. Diversifie plutôt que de tout miser sur une valeur, garde des tailles
  de position modérées, ne fais pas de paris démesurés. Ni frileux, ni all-in.
- Logique : OPPORTUNISTE SUR L'ACTU. Ta décision doit s'appuyer sur des catalyseurs récents
  et concrets : résultats trimestriels, annonces produit, guidance, notes d'analystes,
  rachats/procès/régulation, macro (taux, inflation) touchant ces valeurs.
- Sers-toi de l'outil de recherche web pour VÉRIFIER l'actu des derniers jours sur les
  tickers de la watchlist AVANT de décider. Si tu ne trouves pas de catalyseur clair et
  récent, préfère HOLD : pas d'actu solide = pas de trade forcé.

Après tes recherches, tu renvoies UNIQUEMENT un objet JSON valide (le tout dernier élément
de ta réponse), sans balises Markdown.

IMPORTANT : ne rédige PAS de longue analyse en Markdown. Limite tout commentaire à 3-4 lignes
maximum, puis donne immédiatement le JSON COMPLET. Le JSON est obligatoire et doit toujours
apparaître en entier — il est prioritaire sur ton analyse.

Format EXACT :
{
  "decisions": [
    {"action": "BUY", "symbol": "AAPL", "notional": 10.0, "reason": "catalyseur précis + source"},
    {"action": "SELL", "symbol": "MSFT", "reason": "..."},
    {"action": "HOLD", "symbol": "NVDA", "reason": "..."}
  ],
  "overall_reasoning": "synthèse courte, en français clair, de ta lecture de l'actu et de tes choix du jour (2-4 phrases, lisible par un humain non expert)",
  "strategie_globale": "en 1-2 phrases, ta ligne directrice d'ensemble du moment (ta philosophie de gestion actuelle sur ce portefeuille), pas seulement pour la prochaine passe"
}

Règles :
- "BUY" : "notional" = montant en USD à investir (le code le plafonnera si besoin).
- "SELL" : liquide toute la position sur ce ticker (pas de "notional").
- "HOLD" : ne rien faire.
- Chaque "reason" doit citer le catalyseur concret qui justifie l'action, en français.
- "overall_reasoning" et "strategie_globale" sont destinés à être affichés sur un site public :
  écris-les pour être compris par ta communauté, pas seulement par un trader.
- Petit capital, objectif d'apprentissage : mieux vaut peu d'ordres bien motivés."""


def ask_claude(llm: anthropic.Anthropic, context: dict) -> dict:
    user_msg = (
        "Voici l'état actuel du portefeuille. Cherche d'abord l'actu récente (derniers jours) "
        "sur les tickers de la watchlist, puis décide des actions du jour et termine par le "
        "JSON strict demandé.\n\n"
        + json.dumps(context, ensure_ascii=False, indent=2)
    )

    kwargs = dict(
        model=Config.LLM_MODEL,
        max_tokens=4000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )
    # Outil de recherche web (exécuté côté serveur par l'API Anthropic)
    if Config.ENABLE_WEB_SEARCH:
        kwargs["tools"] = [{
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": Config.WEB_SEARCH_MAX_USES,
        }]

    resp = llm.messages.create(**kwargs)

    # On ne garde que le texte (on ignore les blocs de recherche web)
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()

    # Extraction robuste du JSON, même précédé du raisonnement
    start, end = text.find("{"), text.rfind("}")
    candidate = text[start:end + 1] if start != -1 and end != -1 else text
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        log_event({"type": "parse_error", "raw": text[:800]})
        return {"decisions": [], "overall_reasoning": "réponse illisible, aucune action"}


# ----------------------------------------------------------------------------
# Exécution des ordres (avec application stricte des garde-fous)
# ----------------------------------------------------------------------------

def execute_decision(trading: TradingClient, decision: dict, buying_power: float,
                     positions: dict) -> None:
    action = str(decision.get("action", "HOLD")).upper()
    symbol = str(decision.get("symbol", "")).upper()
    reason = decision.get("reason", "")

    # Garde-fou 1 : liste blanche
    if symbol not in Config.WATCHLIST:
        log_event({"type": "blocked", "reason": "hors watchlist", "symbol": symbol})
        return

    if action == "HOLD":
        log_event({"type": "hold", "symbol": symbol, "reason": reason})
        return

    if Config.DRY_RUN:
        log_event({"type": "dry_run", "action": action, "symbol": symbol, "reason": reason})
        return

    try:
        if action == "BUY":
            # Garde-fou 2 : plafond par ordre + liquidités disponibles
            notional = float(decision.get("notional", 0) or 0)
            notional = min(notional, Config.MAX_NOTIONAL_PER_ORDER, buying_power)
            if notional < 1:
                log_event({"type": "skip", "reason": "montant trop faible/insuffisant",
                           "symbol": symbol})
                return
            order = trading.submit_order(MarketOrderRequest(
                symbol=symbol,
                notional=round(notional, 2),
                side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY,
            ))
            log_event({"type": "order", "action": "BUY", "symbol": symbol,
                       "notional": round(notional, 2), "order_id": str(order.id),
                       "reason": reason})

        elif action == "SELL":
            if symbol not in positions:
                log_event({"type": "skip", "reason": "aucune position à vendre",
                           "symbol": symbol})
                return
            trading.close_position(symbol)  # liquide toute la position
            log_event({"type": "order", "action": "SELL", "symbol": symbol,
                       "closed_qty": positions[symbol], "reason": reason})
        else:
            log_event({"type": "blocked", "reason": f"action inconnue: {action}",
                       "symbol": symbol})
    except Exception as e:  # noqa: BLE001
        log_event({"type": "order_error", "symbol": symbol, "action": action,
                   "message": str(e)})


# ----------------------------------------------------------------------------
# Boucle principale (à lancer via un cron / une tâche planifiée)
# ----------------------------------------------------------------------------

def run_once() -> None:
    trading, data, llm = build_clients()

    # Sécurité : passage en réel exige un opt-in explicite
    if not Config.PAPER and not Config.CONFIRM_LIVE:
        sys.exit("❌ Mode réel demandé mais I_UNDERSTAND_LIVE_RISK != yes. Abandon.")

    mode = "RÉEL 💸" if not Config.PAPER else "PAPER (simulation)"
    log_event({"type": "start", "mode": mode, "dry_run": Config.DRY_RUN,
               "watchlist": Config.WATCHLIST})

    # Marché ouvert ?
    clock = trading.get_clock()
    if not clock.is_open:
        log_event({"type": "market_closed", "next_open": str(clock.next_open)})
        return

    # État du compte
    account = trading.get_account()
    equity = float(account.equity)
    buying_power = float(account.buying_power)
    start_equity = get_start_equity(equity)

    # Garde-fou 3 : kill switch sur drawdown
    drawdown_pct = (start_equity - equity) / start_equity * 100 if start_equity else 0
    if drawdown_pct >= Config.MAX_DRAWDOWN_PCT:
        log_event({"type": "kill_switch", "drawdown_pct": round(drawdown_pct, 2),
                   "message": "Perte max atteinte — aucun nouvel ordre."})
        return

    # Garde-fou 4 : nombre d'ordres/jour
    if orders_today(trading) >= Config.MAX_ORDERS_PER_DAY:
        log_event({"type": "limit", "message": "Quota d'ordres du jour atteint."})
        return

    # Positions actuelles
    positions = {p.symbol: float(p.qty) for p in trading.get_all_positions()}

    # Photo du marché
    market = fetch_market_snapshot(data)

    context = {
        "account": {"equity": equity, "buying_power": buying_power,
                    "start_equity": start_equity, "drawdown_pct": round(drawdown_pct, 2)},
        "positions": positions,
        "watchlist": Config.WATCHLIST,
        "recent_bars": market,
        "constraints": {"max_notional_per_order_usd": Config.MAX_NOTIONAL_PER_ORDER},
    }

    # Décision de Claude
    result = ask_claude(llm, context)
    log_event({"type": "llm_decision", "overall_reasoning": result.get("overall_reasoning", ""),
               "decisions": result.get("decisions", [])})

    # Exécution
    for decision in result.get("decisions", []):
        execute_decision(trading, decision, buying_power, positions)

    equity_after = float(trading.get_account().equity)
    log_event({"type": "end", "equity_after": equity_after})

    # Ligne récapitulative du jour — lue telle quelle par le site public.
    # (Aucun appel API supplémentaire : on ne fait que réunir ce que Claude a déjà produit.)
    log_event({
        "type": "daily_report",
        "date": datetime.now(timezone.utc).date().isoformat(),
        "equity": round(equity_after, 2),
        "start_equity": round(start_equity, 2),
        "mode": "reel" if not Config.PAPER else "paper",
        "decisions": result.get("decisions", []),
        "overall_reasoning": result.get("overall_reasoning", ""),
        "strategie_globale": result.get("strategie_globale", ""),
    })


if __name__ == "__main__":
    run_once()
