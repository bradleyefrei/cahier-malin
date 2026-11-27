# Cahier Malin

Application Windows qui aide à prendre ses cours et à les réviser :

1. **Enregistrer** le cours au micro. La voix est transcrite sur le PC par Whisper (faster-whisper), sans internet. La langue est détectée automatiquement, phrase par phrase.
2. **Rédiger** le cours : une IA locale et gratuite (Ollama + `gemma3:4b`) retire le bruit de la classe et produit un cours structuré.
3. **Réviser** : fiches, flashcards (3 niveaux), quiz, moyens mnémotechniques, défis Feynman, carte mentale.
4. **Planning du soir** : à partir de la date du contrôle, séances de 25 min entre 21:00 et 23:00 (fin à 22:25 la veille d'un contrôle).
5. **Automatique** : chaque soir à partir de 18:00, tous les cours en attente sont rédigés avec leurs fiches et leurs quiz.
6. **Rangement par matière** : `Mes cours\<Matière>\<date - titre>\` contient l'audio (Opus), la transcription, `cours.md` et `fiches et quiz.md`.

## Lancer l'appli

Prérequis : Windows, Python 3.13, [Ollama](https://ollama.com).

```bash
py -3.13 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
ollama pull gemma3:4b
.venv\Scripts\pythonw cahier_malin.py
```

Le modèle Whisper `small` (~460 Mo) se télécharge au premier enregistrement.

## Organisation du code

| Fichier | Rôle |
|---|---|
| `cahier_malin.py` | Point d'entrée. Fenêtre pywebview (port local fixe 47821), pont `window.pywebview.api` vers Python. |
| `cahier_malin.py` → `Transcriber` | Micro (sounddevice, 16 kHz), découpe en morceaux de 20 à 40 s dans les silences, transcription Whisper, prises audio en WAV. |
| `cahier_malin.py` → `LocalAI` | Appels à Ollama (`/api/chat` en streaming, `format` = schéma JSON avec limites `maxItems`/`maxLength`). |
| `cahier_malin.py` → `Library` | Rangement par matière, export Markdown, compression de l'audio en Opus. |
| `cahier_malin.py` → `Api` | Stockage clé/valeur en fichiers JSON dans `%LOCALAPPDATA%\Cahier Malin\donnees`. |
| `app/index.html` | Toute l'interface (HTML, CSS, JS sans framework). Détecte l'appli Windows par `window.pywebview` ; sinon fonctionne comme une page web (Web Speech API + clé API Claude). |
| `app/anthropic.mjs` | SDK Anthropic regroupé (esbuild depuis `src/anthropic-entry.js`), utilisé seulement par la version web. |
| `serveur.mjs` | Petit serveur local pour tester `app/` dans un navigateur. |

### Données d'un cours (`c_<id>.json`)

```json
{ "id": "c_…", "subject": "Anglais", "title": "…", "createdAt": "AAAA-MM-JJ", "minutes": 60,
  "status": "draft | ready", "examDate": "AAAA-MM-JJ | null",
  "notes": { "titre", "matiere", "resume", "sections": [{ "titre", "points", "exemple" }], "definitions", "a_retenir", "dates_formules", "consignes", "date_controle_mentionnee", "bruit_ignore" },
  "kit": { "fiches", "flashcards", "quiz", "mnemos", "feynman", "carte_mentale" },
  "done": {}, "cards": {}, "quizBest": null }
```

La transcription brute est à part, dans `t_c_<id>.json` (`{ "text": "…" }`). La progression (XP, série de jours) est dans `profile.json`.

## Notes pour une fusion avec une autre appli

- Les données personnelles ne sont **pas** dans ce dépôt : elles restent dans `%LOCALAPPDATA%\Cahier Malin` et `Mes cours\`.
- Le PC de développement est peu puissant (processeur basse consommation, environ 3 jetons/s) : les limites des schémas JSON et la quantité de fiches demandée sont réglées pour une rédaction en environ 25 minutes par cours.
- Les textes de l'interface sont en français.
