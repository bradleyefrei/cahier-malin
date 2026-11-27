# Beta-fusion-cahiermalin-x-cahierdecours
en collaboration avec bradley

**Cahier Malin × Notion** (version bêta) : la fusion de deux applis de prise de cours.

- [Cahier de cours](https://github.com/LB-HI/Cahier-de-cours-ft-notion) : éditeur riche (feuille Seyès) qui compile les cours dans Notion.
- [Cahier Malin](https://github.com/bradleyefrei/cahier-malin) : enregistrement du cours, rédaction par IA, kit de révision et planning du soir.

Les deux projets d'origine ne sont pas modifiés : cette fusion est une app à part.

## Ce que fait l'app

**Apparence** : style violet et rose, cartes arrondies, typo Plus Jakarta Sans et Inter ; thème clair, sombre ou automatique (Réglages, ou bouton en haut à droite).

**Mes cours**
- Cours rangés par matière, avec leur état : brouillon, rédigé, révisions prêtes, contrôle dans X jours, envoyé dans Notion.
- Encadré « Ce soir » avec les séances de révision du jour.

**Écrire un cours** (éditeur de Cahier de cours)
- Titres T1 à T4 colorés, gras, italique, souligné, barré, code, liens, 9 couleurs de texte, surlignage et fond de paragraphe.
- Listes, cases à cocher, indentation, tableaux, 2 ou 3 colonnes, sections dépliantes, blocs de code, sommaire.
- Équations LaTeX (KaTeX) et palette de symboles.
- Encadrés : Correction, À retenir, Attention, Définition, Exemple, Méthode, Exercice, Astuce, Remarque.
- 🎤 **Dictée** : la voix est écrite directement dans la feuille (navigateur et app Claude).
- ⏺ **Enregistrer le cours** (app Windows) : micro + Whisper sur le PC, sans internet, langue détectée phrase par phrase ; chrono, niveau du micro et compteur de mots.
- ✨ **Rédiger avec l'IA** : les notes brutes deviennent un cours structuré (résumé, à retenir, parties, définitions, formules, consignes du prof). Le texte d'origine est conservé.

**Réviser** (Cahier Malin)
- Fiches de synthèse, flashcards à 3 niveaux, quiz avec séries, moyens mnémotechniques, défis Feynman, carte mentale.
- Question libre sur le cours, réponse par l'IA.
- Lecture à voix haute du résumé et des fiches.
- Points d'expérience et série de jours.

**Planning du soir** (Cahier Malin)
- Séances de 25 min entre 21:00 et 23:00 jusqu'à la date du contrôle (répétition espacée J+1, J+3, J+6…).
- Frise de la soirée, fin à 22:25 la veille d'un contrôle.
- Export vers l'agenda (`.ics`).

**Automatique** (Cahier Malin)
- Chaque soir à partir de 18:00 (heure réglable), les cours en attente sont rédigés avec leurs fiches et leurs quiz, si l'app est ouverte ; notification Windows à la fin.

**Rangement et sauvegarde**
- App Windows : `Mes cours\<Matière>\<date - titre>\` avec l'audio (Opus), `transcription.txt`, `cours.md`, `fiches et quiz.md`, le texte d'origine ; `_Corbeille` pour les cours supprimés.
- Navigateur : téléchargement de `cours.md` et `fiches et quiz.md`.
- Export / import JSON, et reprise des cours de l'ancienne appli Cahier Malin.

**Réglages** : micro, modèle de voix, langue, commande Python, installation des modules, IA (Claude ou Ollama), rédaction du soir, dossier « Mes cours », sauvegarde.

**Notion**
- **Chacun son Notion** : chaque personne utilise son propre connecteur Notion et choisit dans Réglages > Mon Notion sa page racine et ses matières (recherche dans son Notion). Ce choix est gardé dans son profil privé. Le propriétaire de l'app garde ses pages par défaut ; un nouvel utilisateur est invité à configurer les siennes.
- Envoi du cours à la suite de la page de la matière, dans une sous-page, ou dans une nouvelle matière.
- Option : ajouter les révisions (fiches, flashcards, quiz, méthodes) dans une section dépliante « 🧠 Révisions ».

## Navigateur ou application Windows

| | Dans claude.ai (navigateur ou app Claude) | Installée comme application Windows |
|---|---|---|
| Voix | Dictée de claude.ai (Chrome, Edge ou app Claude) | Enregistrement du cours + Whisper hors ligne, audio Opus |
| IA de rédaction | Claude via le compte, sans clé | Claude, ou IA locale gratuite Ollama + `gemma3:4b` |
| Données | Compte claude.ai (tous les appareils), sinon navigateur | Pareil, plus les fichiers dans `Mes cours\` |
| Rédaction automatique du soir | Si la page est ouverte | Si l'app est ouverte (même réduite), avec notification |

### Installer l'application Windows

1. Ouvrir l'artifact dans l'**app Claude pour Windows**, puis « Installer comme app ».
2. Dans l'app installée : **Réglages** → « Autoriser l'accès à l'ordinateur ».
3. Prérequis : [Python 3.13](https://www.python.org/). « Installer les modules manquants » lance `py -3.13 -m pip install faster-whisper sounddevice numpy av`. On peut aussi indiquer le Python de l'environnement de Cahier Malin (`…\cahier-malin\.venv\Scripts\python.exe`).
4. Optionnel : [Ollama](https://ollama.com), puis « Télécharger le modèle » dans Réglages.
5. « Reprendre les cours de l'ancienne appli Cahier Malin » importe `%LOCALAPPDATA%\Cahier Malin\donnees`.

Le modèle Whisper `small` (~460 Mo) se télécharge au premier enregistrement.

## Fonctionnement technique

`index.html` est une page autonome (HTML, CSS et JavaScript sans framework). Elle est publiée comme artifact claude.ai et utilise ses capacités :

| Capacité | Usage |
|---|---|
| `mcp` (connecteur Notion) | `notion-update-page`, `notion-create-pages`, `notion-search` (choix des pages dans Mon Notion) |
| `sample` | Rédaction du cours, kit de révision, questions |
| `dictation` | Dictée |
| `db` + `user` | Sauvegarde des cours dans l'espace privé de l'utilisateur (`data/users/<id>/`) |
| `downloads` | Export du planning `.ics`, de `cours.md`, de la sauvegarde |
| `desktop` | Installation comme application ; dans l'app, accès `full-access` (balise `claude-artifacts-manifest`) pour lancer le moteur Python et écrire dans `Mes cours\`, et notifications |

### Moteur local (app Windows)

La page embarque `moteur.py` (copie lisible dans le dépôt), l'écrit dans `%LOCALAPPDATA%\Cahier Malin\moteur\` et le lance avec Python. Il reprend le code de Cahier Malin : `Transcriber` (sounddevice 16 kHz, morceaux de 20 à 40 s coupés dans les silences, faster-whisper), compression Opus (PyAV) et `LocalAI` (Ollama). Page et moteur dialoguent en JSON, une ligne par message : commandes sur l'entrée standard (`start`, `stop`, `finalize`, `mics`, `ai`, `ai_pull`…), événements sur la sortie (`text`, `level`, `stopped`, `audio`, `ai_delta`, `ai_done`…).

Ouverte hors de claude.ai, la page fonctionne comme éditeur et outil de révision, avec une sauvegarde dans le navigateur. L'IA, la dictée et Notion n'y sont pas disponibles.

### Données d'un cours

```json
{ "kind": "course", "title": "…", "subject": "Anglais", "subjectId": "<id page Notion>",
  "createdAt": "AAAA-MM-JJ", "status": "draft | ready", "examDate": "AAAA-MM-JJ | null",
  "html": "contenu de l'éditeur", "raw": "texte avant rédaction par l'IA",
  "notes": { "titre", "resume", "sections", "definitions", "a_retenir", "dates_formules", "consignes", "date_controle_mentionnee" },
  "kit": { "fiches", "flashcards", "quiz", "mnemos", "feynman", "carte_mentale" },
  "done": {}, "cards": {}, "quizBest": null, "notion": { "url", "at" },
  "minutes": 0, "audio": false, "folder": "Matière/date - titre" }
```

### Configuration

Partage : une personne de la même organisation claude.ai peut utiliser l'artifact partagé avec son propre Notion. Une personne extérieure à l'organisation n'a pas accès aux connecteurs depuis un lien partagé : elle publie sa propre copie de `index.html` dans son claude.ai, puis configure « Mon Notion ».

Les identifiants des pages Notion (page racine et matières) sont en haut du script, dans `ROOT` et `DEFAULTS`.
