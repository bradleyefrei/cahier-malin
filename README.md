# Cahier Malin × Notion

Application Windows (Python) pour prendre ses cours, les réviser et les ranger dans Notion. Tout tourne sur le PC, sans compte Claude ni clé payante : la transcription par Whisper, la rédaction par une IA locale gratuite (Ollama), et l'envoi dans Notion par l'API officielle.

Fusion de deux projets : **Cahier Malin** (enregistrement, rédaction, révisions, planning) et **Cahier de cours** de [LB-HI](https://github.com/LB-HI) (éditeur Seyès et envoi dans Notion), d'après [Beta-fusion-cahiermalin-x-cahierdecours](https://github.com/LB-HI/Beta-fusion-cahiermalin-x-cahierdecours).

## Ce que fait l'appli

- **Enregistrer le cours** au micro : Whisper transcrit sur le PC, sans internet, langue détectée phrase par phrase.
- **Écrire un cours** dans un éditeur riche : titres colorés, encadrés, tableaux, colonnes, équations LaTeX, sections dépliantes.
- **Rédiger avec l'IA** : Ollama (`gemma3:4b`) remet les notes au propre (résumé, à retenir, parties, définitions, consignes).
- **Réviser** : fiches, flashcards à 3 niveaux, quiz, moyens mnémotechniques, défis Feynman, carte mentale.
- **Planning du soir** : séances de 25 min entre 21:00 et 23:00 jusqu'au contrôle.
- **Automatique** :
  - vers 18:00, les cours en attente sont rédigés avec leurs fiches et leurs quiz ;
  - à 23:00, les cours rédigés sont envoyés dans Notion, chacun dans la page de sa matière (rattrapage à l'ouverture si le PC était éteint).
- **Rangement** : `Documents\Cahier Malin\Mes cours\<Matière>\<date - titre>\` avec l'audio, la transcription, `cours.md` et `fiches et quiz.md`.

## Installation

Prérequis : Windows, [Python 3.13](https://www.python.org/), [Ollama](https://ollama.com).

```bash
py -3.13 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
ollama pull gemma3:4b
.venv\Scripts\pythonw cahier_malin_notion.py
```

Le modèle Whisper `small` (~460 Mo) se télécharge au premier enregistrement. Options de lancement : `--demarrage` (fenêtre réduite, pour le démarrage de Windows), `--envoyer-notion` (envoie tout de suite les cours pas encore dans Notion).

### Notion

1. Sur [app.notion.com/developers/connections](https://app.notion.com/developers/connections), créer une connexion avec la méthode **Jeton d'API**, dans un espace dont on est propriétaire.
2. Créer une page (par exemple « Cahier Malin - Mes cours ») et lui donner accès à la connexion (onglet **Accès au contenu**, ou ••• > Connexions sur la page).
3. Dans l'appli : **Réglages › Mon Notion**, chercher la page et la choisir comme page racine. La clé est demandée la première fois et gardée sur le PC (`%LOCALAPPDATA%\Cahier Malin\notion.json`), jamais dans ce dépôt.

## Organisation du code

| Fichier | Rôle |
|---|---|
| `fusion/index.html` | L'interface (page de la fusion, écrite au départ pour claude.ai). Non modifiée, sauf : chacun configure son propre Notion. |
| `fusion/claude-shim.js` | Remplace les fonctions de claude.ai que la page demande avec `claude.use(...)` : stockage, disque, processus, fichiers, notifications, Notion. |
| `fusion/auto-notion.js` | Envoi automatique vers Notion à 23:00, inséré dans le script principal de la page au lancement. |
| `fusion/moteur.py` | Moteur local lancé par la page : micro + Whisper, compression Opus, Ollama (dialogue en JSON par lignes). |
| `cahier_malin_notion.py` | Point d'entrée : fenêtre pywebview (port local 47822), pont Python pour le stockage et le disque, construit `fusion/_app.html`. |
| `notion_api.py` | Convertit le Markdown Notion de la page en blocs de l'API Notion et imite `notion-search`, `notion-create-pages`, `notion-update-page`. |

Les cours sont enregistrés en JSON dans `%LOCALAPPDATA%\Cahier Malin\fusion-db`.

L'ancienne application (Cahier Malin seul) reste disponible avec l'étiquette `ancienne-appli-windows`.
