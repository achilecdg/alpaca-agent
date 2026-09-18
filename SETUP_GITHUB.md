# Faire tourner le bot dans le cloud (GitHub Actions)

Objectif : l'agent s'exécute tout seul à l'heure prévue, **même ordinateur éteint**, sur
l'infrastructure gratuite de GitHub. Aucun serveur à gérer.

## Étapes

1. **Crée un compte GitHub** (si tu n'en as pas) sur github.com.

2. **Crée un dépôt PRIVÉ** (bouton *New repository* → coche *Private*). Important qu'il soit
   privé : ton historique de décisions ne doit pas être public.

3. **Envoie le projet dans le dépôt.** Le plus simple sans ligne de commande : sur la page du
   dépôt, *Add file → Upload files*, et glisse tout le contenu du dossier `alpaca-agent`
   (y compris le dossier caché `.github`). Vérifie bien que tu N'ENVOIES PAS ton fichier
   `.env` (le `.gitignore` est là pour ça, mais reste vigilant).

4. **Ajoute tes 3 clés en secrets chiffrés.** Dans le dépôt : *Settings → Secrets and
   variables → Actions → New repository secret*. Crée exactement ces trois secrets :
   - `ALPACA_API_KEY`
   - `ALPACA_SECRET_KEY`
   - `ANTHROPIC_API_KEY`

   (Pour commencer, mets tes clés **paper**. Le workflow est réglé sur simulation par défaut.)

5. **Teste à la main.** Onglet *Actions* → workflow *trading-agent* → bouton *Run workflow*.
   Ça lance une exécution immédiate. Regarde les logs de l'étape « Lancer l'agent » : si le
   marché est fermé, c'est normal qu'il l'indique — l'essentiel est qu'il démarre sans erreur
   de clé.

6. **Laisse la planification faire le reste.** Le workflow est réglé sur **lundi 15h30 UTC**
   (milieu de matinée à New York). Modifie la ligne `cron:` dans `.github/workflows/trade.yml`
   pour changer la cadence.

## Passer en réel plus tard

Dans `.github/workflows/trade.yml`, mets `ALPACA_PAPER: "false"` **et**
`I_UNDERSTAND_LIVE_RISK: "yes"`, puis remplace les secrets Alpaca par tes clés **live**
(elles sont différentes des clés paper). Fais-le seulement après une phase de paper concluante.

## À savoir

- **Persistance du journal** : les serveurs GitHub sont éphémères, donc le workflow re-sauvegarde
  automatiquement `trades_log.jsonl` et `start_equity.txt` dans ton dépôt après chaque passe.
  C'est là que tu retrouveras tout l'historique du bot.
- **Coût** : gratuit pour cet usage (une passe de ~2 min par semaine est négligeable dans le
  quota gratuit).
- **Horaire approximatif** : GitHub peut décaler une exécution planifiée de quelques minutes.
  Sans importance pour une cadence hebdomadaire.
- **Dépôt en sommeil** : si tu ne touches pas au dépôt pendant ~60 jours, GitHub suspend les
  workflows planifiés. Un simple commit les réactive.

## Alternative : un petit VPS

Si tu préfères un « ordinateur toujours allumé » classique (chez OVH, Scaleway, Hetzner…),
tu installes le projet dessus et tu utilises un `cron` système. Plus cher (quelques €/mois) et
un peu d'administration Linux, mais le journal persiste naturellement sur le disque du serveur.
