"""Cahier Malin, application Windows.

Ouvre l'interface (dossier app) dans une fenêtre pywebview. L'enregistrement du
micro et la transcription se font sur ce PC avec Whisper (faster-whisper), sans
internet. Les cours sont enregistrés dans %LOCALAPPDATA%\\Cahier Malin.
"""
import ctypes
import json
import os
import re
import sys
import threading
import time
import traceback
from pathlib import Path

import numpy as np
import sounddevice as sd
import webview

BASE = Path(__file__).resolve().parent
APP_DIR = BASE / "app"
DATA = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Cahier Malin"
KV_DIR = DATA / "donnees"
REC_DIR = DATA / "enregistrement en cours"
COURS_DIR = BASE / "Mes cours"   # Documents\Cahier Malin\Mes cours\<Matière>\<date - titre>
LOG = DATA / "journal.txt"
PORT = 47821
SR = 16000
MODELS = {"base": "Rapide", "small": "Recommandé", "medium": "Très précis, lent"}
LANGS = {"auto": "Détection automatique", "fr": "Français", "en": "Anglais", "es": "Espagnol", "de": "Allemand", "it": "Italien"}

KV_DIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

# Phrases que Whisper invente parfois sur du bruit ou du silence
HALLUCINATIONS = re.compile(
    r"(sous-titr|amara\.org|merci d'avoir regard|abonnez-vous|n'oubliez pas de vous abonner|merci de votre attention\.?$)",
    re.IGNORECASE,
)


def log(msg):
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except OSError:
        pass


def keep_awake(on):
    """Empêche Windows de se mettre en veille pendant un enregistrement."""
    try:
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0))
    except Exception:
        pass


class Transcriber:
    """Charge le modèle Whisper et transcrit l'audio du micro morceau par morceau."""

    def __init__(self, model_name, lang="auto"):
        self.model_name = model_name
        self.lang = lang
        self.model = None
        self.model_state = "idle"  # idle | loading | ready | error
        self.model_error = ""
        self.lock = threading.Lock()
        self.buf = []          # blocs audio pas encore transcrits
        self.buf_len = 0
        self.texts = []        # texte transcrit pas encore lu par l'interface
        self.tail = ""         # fin du texte, sert de contexte au morceau suivant
        self.level = 0.0
        self.state = "idle"    # idle | listening | finishing
        self.error = ""
        self.stream = None
        self.wav = None
        self.worker = None
        self.wake = threading.Event()

    # ----- modèle -----
    def load(self, name=None):
        if name:
            if name != self.model_name:
                self.model = None
            self.model_name = name
        if self.model is not None or self.model_state == "loading":
            return
        self.model_state = "loading"
        threading.Thread(target=self._load, daemon=True).start()

    def _load(self):
        try:
            from faster_whisper import WhisperModel
            threads = max(2, min(8, (os.cpu_count() or 4) // 2))
            self.model = WhisperModel(self.model_name, device="cpu", compute_type="int8", cpu_threads=threads)
            self.model_state = "ready"
            log(f"Modèle {self.model_name} prêt")
        except Exception as e:  # pas de réseau au premier lancement, disque plein...
            self.model_state = "error"
            self.model_error = str(e)
            log("Chargement du modèle impossible : " + traceback.format_exc())

    # ----- micro -----
    def _callback(self, indata, frames, time_info, status):
        block = indata[:, 0].copy()
        with self.lock:
            self.buf.append(block)
            self.buf_len += len(block)
            if self.wav is not None:
                try:
                    self.wav.writeframes((np.clip(block, -1, 1) * 32767).astype(np.int16).tobytes())
                except Exception:
                    pass
        self.level = float(min(1.0, np.sqrt(np.mean(block ** 2)) * 12))
        if self.buf_len >= SR * 20:
            self.wake.set()

    def start(self, device=None):
        if self.state != "idle":
            return {"ok": True}
        if self.model_state == "error":
            self.model_state = "idle"
        self.load()
        try:
            self.stream = sd.InputStream(samplerate=SR, channels=1, dtype="float32", blocksize=SR // 10,
                                         device=device if device not in (None, "", -1) else None, callback=self._callback)
            self.stream.start()
        except Exception as e:
            log("Micro indisponible : " + traceback.format_exc())
            self.stream = None
            return {"ok": False, "error": "Micro introuvable ou bloqué. Vérifie qu'un micro est branché et que Windows autorise l'accès au micro (Paramètres > Confidentialité > Microphone). Détail : " + str(e)}
        # chaque prise (entre deux pauses) est gardée dans un petit fichier audio
        try:
            import wave
            REC_DIR.mkdir(parents=True, exist_ok=True)
            w = wave.open(str(REC_DIR / time.strftime("prise_%Y%m%d_%H%M%S.wav")), "wb")
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
            with self.lock:
                self.wav = w
        except Exception:
            log("Fichier audio impossible : " + traceback.format_exc())
        self.state = "listening"
        self.error = ""
        keep_awake(True)
        if not self.worker or not self.worker.is_alive():
            self.worker = threading.Thread(target=self._work, daemon=True)
            self.worker.start()
        return {"ok": True}

    def stop(self):
        if self.state != "listening":
            return {"ok": True}
        self.state = "finishing"
        try:
            if self.stream:
                self.stream.stop()
                self.stream.close()
        except Exception:
            pass
        self.stream = None
        with self.lock:
            w, self.wav = self.wav, None
        try:
            if w:
                w.close()
        except Exception:
            pass
        self.level = 0.0
        keep_awake(False)
        self.wake.set()
        return {"ok": True}

    def _take_chunk(self, final):
        """Prend un morceau de 20 à 40 s, coupé dans un moment calme."""
        with self.lock:
            if not self.buf:
                return None
            audio = np.concatenate(self.buf)
            if not final and len(audio) < SR * 20:
                return None
            cut = len(audio)
            if not final and len(audio) > SR * 20:
                lo, hi = SR * 15, min(len(audio), SR * 40)
                win = SR // 4
                seg = audio[lo:hi]
                n = len(seg) // win
                if n > 1:
                    energy = (seg[: n * win].reshape(n, win) ** 2).mean(axis=1)
                    cut = lo + int(np.argmin(energy)) * win + win // 2
            chunk, rest = audio[:cut], audio[cut:]
            self.buf = [rest] if len(rest) else []
            self.buf_len = len(rest)
            return chunk

    def _work(self):
        while True:
            self.wake.wait(timeout=1.0)
            self.wake.clear()
            final = self.state == "finishing"
            if self.model is None:
                if self.model_state == "error":
                    self.error = "Le modèle de voix n'a pas pu se charger (il faut internet la première fois). " + self.model_error
                    if final:
                        with self.lock:
                            self.buf, self.buf_len = [], 0
                        self.state = "idle"
                        return
                continue
            chunk = self._take_chunk(final)
            if chunk is not None and len(chunk) > SR // 2:
                self._transcribe(chunk)
                self.wake.set()  # il reste peut-être de l'audio en attente
                continue
            if final:
                self.state = "idle"
                return

    def _transcribe(self, audio):
        try:
            segments, _ = self.model.transcribe(
                audio, beam_size=1, vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500},
                condition_on_previous_text=False,
                # Langue détectée automatiquement, phrase par phrase (utile en cours de langue)
                language=None if self.lang == "auto" else self.lang,
                multilingual=self.lang == "auto",
                initial_prompt=self.tail[-200:] or None,
            )
            parts = []
            for s in segments:
                t = s.text.strip()
                if not t or s.no_speech_prob > 0.7 or HALLUCINATIONS.search(t):
                    continue
                parts.append(t)
            text = " ".join(parts).strip()
            if text:
                with self.lock:
                    self.texts.append(text)
                self.tail = (self.tail + " " + text)[-400:]
        except Exception:
            log("Transcription impossible : " + traceback.format_exc())
            self.error = "Un morceau n'a pas pu être transcrit (voir journal.txt)."

    def poll(self):
        with self.lock:
            text = " ".join(self.texts)
            self.texts = []
            backlog = self.buf_len / SR
        return {"state": self.state, "text": text, "level": round(self.level, 3), "backlog": round(backlog),
                "model_state": self.model_state, "error": self.error}


def _clean(name, fallback):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", str(name or "")).replace("**", "")
    name = re.sub(r"\s+", " ", name).strip(" .")[:70]
    return name or fallback


def _md(s):
    return str(s or "").strip()


class Library:
    """Range chaque cours dans Mes cours\\<Matière>\\<date - titre> : audio, transcription, cours, fiches."""

    def __init__(self):
        self.map_path = DATA / "dossiers.json"
        self.lock = threading.Lock()
        try:
            self.map = json.loads(self.map_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.map = {}

    def _save_map(self):
        self.map_path.write_text(json.dumps(self.map, ensure_ascii=False, indent=1), encoding="utf-8")

    def folder(self, course):
        """Dossier du cours ; le déplace si la matière ou le titre ont changé."""
        import shutil
        cid = course["id"]
        want = COURS_DIR / _clean(course.get("subject"), "Sans matière") / \
            f"{course.get('createdAt', '')} - {_clean(course.get('title'), 'Cours ' + cid[-4:])}"
        with self.lock:
            old = self.map.get(cid)
            old_path = COURS_DIR / old if old else None
            if old_path and old_path != want and old_path.exists():
                if want.exists():
                    want = want.with_name(want.name + " (" + cid[-4:] + ")")
                want.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(old_path), str(want))
                try:
                    old_path.parent.rmdir()  # matière vide
                except OSError:
                    pass
            want.mkdir(parents=True, exist_ok=True)
            self.map[cid] = str(want.relative_to(COURS_DIR))
            self._save_map()
        return want

    def export(self, course):
        try:
            d = self.folder(course)
            t = KV_DIR / f"t_{course['id']}.json"
            if t.exists() and not (d / "transcription.txt").exists():
                (d / "transcription.txt").write_text(json.loads(t.read_text(encoding="utf-8")).get("text", ""), encoding="utf-8")
            if course.get("notes"):
                (d / "cours.md").write_text(self._notes_md(course), encoding="utf-8")
            if course.get("kit"):
                (d / "fiches et quiz.md").write_text(self._kit_md(course), encoding="utf-8")
        except Exception:
            log("Rangement du cours impossible : " + traceback.format_exc())

    def trash(self, cid):
        import shutil
        with self.lock:
            rel = self.map.pop(cid, None)
            self._save_map()
        if rel and (COURS_DIR / rel).exists():
            dest = COURS_DIR / "_Corbeille" / Path(rel).name
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                dest = dest.with_name(dest.name + time.strftime(" %H%M%S"))
            shutil.move(str(COURS_DIR / rel), str(dest))

    @staticmethod
    def _notes_md(c):
        n = c["notes"]
        out = [f"# {_md(c.get('title') or n.get('titre'))}", f"*{_md(c.get('subject'))} · {c.get('createdAt', '')}*", "",
               "## En bref", _md(n.get("resume")), ""]
        if n.get("a_retenir"):
            out += ["## À retenir absolument"] + [f"- {_md(x)}" for x in n["a_retenir"]] + [""]
        for s in n.get("sections") or []:
            out += [f"## {_md(s.get('titre'))}"] + [f"- {_md(p)}" for p in s.get("points") or []]
            if s.get("exemple"):
                out.append(f"\n> Exemple : {_md(s['exemple'])}")
            out.append("")
        if n.get("definitions"):
            out += ["## Définitions"] + [f"- **{_md(d.get('terme'))}** : {_md(d.get('definition'))}" for d in n["definitions"]] + [""]
        if n.get("dates_formules"):
            out += ["## Dates, formules, chiffres"] + [f"- {_md(d.get('label'))} : {_md(d.get('valeur'))}" for d in n["dates_formules"]] + [""]
        if n.get("consignes"):
            out += ["## Consignes du prof"] + [f"- {_md(x)}" for x in n["consignes"]] + [""]
        return "\n".join(out)

    @staticmethod
    def _kit_md(c):
        k = c["kit"]
        out = [f"# Révisions : {_md(c.get('title'))}", ""]
        for i, f in enumerate(k.get("fiches") or [], 1):
            out += [f"## Fiche {i} : {_md(f.get('titre'))}"] + [f"- {_md(x)}" for x in f.get("essentiel") or []]
            if f.get("astuce"):
                out.append(f"\n*Astuce : {_md(f['astuce'])}*")
            out.append("")
        if k.get("flashcards"):
            out += ["## Flashcards"] + [f"- **{_md(x.get('q'))}** → {_md(x.get('r'))}" for x in k["flashcards"]] + [""]
        if k.get("quiz"):
            out.append("## Quiz")
            for i, q in enumerate(k["quiz"], 1):
                out.append(f"{i}. {_md(q.get('question'))}")
                for j, ch in enumerate(q.get("choix") or []):
                    out.append(f"   - {'ABCD'[j] if j < 4 else j}. {_md(ch)}{'  ✔' if j == q.get('bonne') else ''}")
                if q.get("explication"):
                    out.append(f"   *{_md(q['explication'])}*")
            out.append("")
        if k.get("mnemos"):
            out += ["## Moyens mnémotechniques"] + [f"- {_md(m.get('pour'))} : {_md(m.get('astuce'))}" for m in k["mnemos"]] + [""]
        mm = k.get("carte_mentale") or {}
        if mm.get("centre"):
            out += [f"## Carte mentale : {_md(mm['centre'])}"] + \
                   [f"- **{_md(b.get('titre'))}** : {', '.join(_md(x) for x in b.get('feuilles') or [])}" for b in mm.get("branches") or []]
        return "\n".join(out)

    def finalize_audio(self, course):
        """Assemble les prises du micro en un seul fichier audio compressé, dans le dossier du cours."""
        parts = sorted(REC_DIR.glob("prise_*.wav")) if REC_DIR.exists() else []
        if not parts:
            return
        threading.Thread(target=self._encode, args=(course, parts), daemon=True).start()

    def _encode(self, course, parts):
        import wave
        try:
            chunks = []
            for p in parts:
                try:
                    with wave.open(str(p)) as w:
                        chunks.append(np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16))
                except Exception:
                    log("Prise audio illisible : " + p.name)
            audio = np.concatenate(chunks) if chunks else np.zeros(0, np.int16)
            tmp = DATA / f"audio_{course['id']}.ogg"
            ok = _write_opus(audio, tmp)
            dest_dir = self.folder(course)
            if ok:
                os.replace(tmp, dest_dir / "enregistrement.ogg")
            else:  # secours : WAV non compressé
                with wave.open(str(dest_dir / "enregistrement.wav"), "wb") as w:
                    w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes(audio.tobytes())
            for p in parts:
                p.unlink(missing_ok=True)
            log(f"Enregistrement rangé dans {dest_dir}")
        except Exception:
            log("Rangement de l'audio impossible : " + traceback.format_exc())

    @staticmethod
    def discard_audio():
        for p in REC_DIR.glob("prise_*.wav") if REC_DIR.exists() else []:
            try:
                p.unlink()
            except OSError:
                pass


def _write_opus(audio_i16, path):
    """Compresse l'audio en Opus (environ 11 Mo par heure)."""
    try:
        import av
        with av.open(str(path), "w", format="ogg") as out:
            st = out.add_stream("libopus", rate=SR)
            st.layout = "mono"
            st.bit_rate = 24000
            fs = st.codec_context.frame_size or 320
            pts = 0
            for i in range(0, len(audio_i16), fs):
                blk = audio_i16[i:i + fs]
                if len(blk) < fs:
                    blk = np.pad(blk, (0, fs - len(blk)))
                fr = av.AudioFrame.from_ndarray(blk.reshape(1, -1), format="s16", layout="mono")
                fr.sample_rate = SR
                fr.pts = pts
                pts += fs
                for pk in st.encode(fr):
                    out.mux(pk)
            for pk in st.encode(None):
                out.mux(pk)
        return True
    except Exception:
        log("Compression Opus impossible, WAV utilisé : " + traceback.format_exc())
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass
        return False


OLLAMA = "http://127.0.0.1:11434"
AI_MODEL = "gemma3:4b"


def _ollama_exe():
    import shutil
    exe = shutil.which("ollama")
    if exe:
        return exe
    p = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
    return str(p) if p.exists() else None


def _http(method, path, body=None, timeout=5):
    import urllib.request
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(OLLAMA + path, data=data, method=method, headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=timeout)


class LocalAI:
    """IA gratuite qui tourne sur ce PC grâce à Ollama."""

    def __init__(self):
        self.jobs = {}
        self.pull = {"state": "idle", "done": 0, "total": 0, "error": ""}
        self.lock = threading.Lock()

    def server_ok(self):
        try:
            with _http("GET", "/api/version", timeout=2) as r:
                return r.status == 200
        except Exception:
            return False

    def ensure_server(self):
        if self.server_ok():
            return True
        exe = _ollama_exe()
        if not exe:
            return False
        import subprocess
        subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        for _ in range(40):
            time.sleep(0.5)
            if self.server_ok():
                return True
        return False

    def has_model(self):
        try:
            with _http("GET", "/api/tags") as r:
                names = [m.get("name", "") for m in json.load(r).get("models", [])]
            return any(n == AI_MODEL or n.startswith(AI_MODEL + "-") for n in names)
        except Exception:
            return False

    def status(self):
        installed = _ollama_exe() is not None
        running = installed and self.ensure_server()
        return {"installed": installed, "running": running, "model": AI_MODEL,
                "has_model": running and self.has_model(), "pull": dict(self.pull)}

    def start_pull(self):
        if self.pull["state"] == "pulling":
            return
        self.pull = {"state": "pulling", "done": 0, "total": 0, "error": ""}
        threading.Thread(target=self._pull, daemon=True).start()

    def _pull(self):
        try:
            if not self.ensure_server():
                raise RuntimeError("Ollama n'est pas installé ou ne démarre pas.")
            with _http("POST", "/api/pull", {"model": AI_MODEL, "stream": True}, timeout=3600) as r:
                for line in r:
                    if not line.strip():
                        continue
                    ev = json.loads(line)
                    if ev.get("error"):
                        raise RuntimeError(ev["error"])
                    if ev.get("total"):
                        self.pull["total"], self.pull["done"] = ev["total"], ev.get("completed", 0)
            self.pull["state"] = "done"
        except Exception as e:
            log("Téléchargement du modèle impossible : " + traceback.format_exc())
            self.pull.update(state="error", error=str(e))

    def start(self, prompt, schema=None, max_tokens=4096):
        if not self.ensure_server():
            return {"error": "L'IA locale (Ollama) n'est pas lancée. Vérifie qu'Ollama est installé, puis relance Cahier Malin."}
        if not self.has_model():
            return {"error": "Le modèle d'IA n'est pas encore téléchargé. Va dans Réglages > IA de rédaction."}
        job_id = str(int(time.time() * 1000))
        job = {"text": "", "done": False, "error": "", "cancel": False, "phase": "reading", "started": time.time()}
        self.jobs[job_id] = job
        threading.Thread(target=self._run, args=(job, prompt, schema, max_tokens), daemon=True).start()
        return {"id": job_id}

    def _run(self, job, prompt, schema, max_tokens):
        # taille du contexte : le texte du cours + la réponse
        need = len(prompt) // 3 + max_tokens + 512
        num_ctx = min(32768, max(4096, (need // 4096 + 1) * 4096))
        body = {"model": AI_MODEL, "messages": [{"role": "user", "content": prompt}], "stream": True,
                "keep_alive": "10m", "options": {"num_ctx": num_ctx, "temperature": 0.3, "num_predict": max_tokens, "repeat_penalty": 1.15}}
        if schema:
            body["format"] = schema
        keep_awake(True)  # pas de mise en veille automatique pendant que l'IA rédige
        try:
            with _http("POST", "/api/chat", body, timeout=1800) as r:
                for line in r:
                    if job["cancel"]:
                        break
                    if not line.strip():
                        continue
                    ev = json.loads(line)
                    if ev.get("error"):
                        raise RuntimeError(ev["error"])
                    piece = (ev.get("message") or {}).get("content", "")
                    if piece:
                        job["phase"] = "writing"
                        job["text"] += piece
                    if ev.get("done"):
                        if ev.get("done_reason") == "length":
                            job["error"] = "La réponse a été coupée (cours très long)."
                        break
        except Exception as e:
            if not job["cancel"]:
                log("IA locale : " + traceback.format_exc())
                job["error"] = "L'IA locale s'est arrêtée : " + str(e)
        keep_awake(False)
        job["done"] = True

    def poll(self, job_id):
        job = self.jobs.get(job_id)
        if not job:
            return {"done": True, "error": "Tâche introuvable."}
        out = {"done": job["done"], "text": job["text"], "error": job["error"], "phase": job["phase"],
               "elapsed": int(time.time() - job["started"])}
        if job["done"]:
            self.jobs.pop(job_id, None)
        return out

    def cancel(self, job_id):
        job = self.jobs.get(job_id)
        if job:
            job["cancel"] = True
        return True


class Api:
    """Fonctions appelées depuis l'interface (window.pywebview.api)."""

    def __init__(self):
        self._settings_path = DATA / "parametres.json"
        self._prefs = self._read_prefs()
        self._tr = Transcriber(self._prefs.get("model", "small"), self._prefs.get("lang", "auto"))
        self._window = None
        self._ai = LocalAI()
        self._lib = Library()

    def _read_prefs(self):
        try:
            return json.loads(self._settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _write_prefs(self):
        self._settings_path.write_text(json.dumps(self._prefs, ensure_ascii=False), encoding="utf-8")

    # ----- stockage des cours -----
    @staticmethod
    def _path(key):
        if not re.fullmatch(r"[A-Za-z0-9_\-]{1,80}", key or ""):
            raise ValueError("clé invalide")
        return KV_DIR / (key + ".json")

    def kv_get(self, key):
        try:
            return json.loads(self._path(key).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def kv_set(self, key, value):
        p = self._path(key)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
        if key.startswith("c_") and isinstance(value, dict):
            self._lib.export(value)
        return True

    def kv_del(self, key):
        try:
            self._path(key).unlink()
        except FileNotFoundError:
            pass
        if key.startswith("c_"):
            self._lib.trash(key)
        return True

    # ----- rangement par matière -----
    def finalize_recording(self, course_id):
        c = self.kv_get(course_id)
        if c:
            self._lib.finalize_audio(c)
        return True

    def discard_recording(self):
        Library.discard_audio()
        return True

    def open_folder(self, course_id=None):
        rel = self._lib.map.get(course_id) if course_id else None
        target = COURS_DIR / rel if rel else COURS_DIR
        target.mkdir(parents=True, exist_ok=True)
        os.startfile(str(target))
        return True

    def kv_all(self):
        out = {}
        for p in KV_DIR.glob("*.json"):
            try:
                out[p.stem] = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                log("Fichier illisible : " + p.name)
        return out

    # ----- enregistrement -----
    def voice_status(self):
        return {"model": self._tr.model_name, "model_state": self._tr.model_state, "model_error": self._tr.model_error,
                "models": MODELS, "mic": self._prefs.get("mic"), "lang": self._tr.lang, "langs": LANGS}

    def list_mics(self):
        mics = []
        try:
            default = sd.default.device[0]
            seen = set()
            for i, d in enumerate(sd.query_devices()):
                if d["max_input_channels"] > 0 and sd.query_hostapis(d["hostapi"])["name"] == "MME" and d["name"] not in seen:
                    seen.add(d["name"])
                    mics.append({"id": i, "name": d["name"], "default": i == default})
        except Exception:
            log("Liste des micros impossible : " + traceback.format_exc())
        return mics

    def set_voice(self, model=None, mic=None, lang=None):
        if lang in LANGS:
            self._prefs["lang"] = lang
            self._tr.lang = lang
        if model in MODELS:
            self._prefs["model"] = model
            self._tr.load(model)
        if mic is not None:
            self._prefs["mic"] = None if mic in ("", -1) else int(mic)
        self._write_prefs()
        return self.voice_status()

    def start_recording(self):
        return self._tr.start(self._prefs.get("mic"))

    def stop_recording(self):
        return self._tr.stop()

    def poll(self):
        return self._tr.poll()

    # ----- IA locale gratuite -----
    def ai_status(self):
        return self._ai.status()

    def ai_pull(self):
        self._ai.start_pull()
        return True

    def ai_start(self, prompt, schema=None, max_tokens=4096):
        return self._ai.start(prompt, schema, int(max_tokens or 4096))

    def ai_poll(self, job_id):
        return self._ai.poll(job_id)

    def ai_cancel(self, job_id):
        return self._ai.cancel(job_id)

    # ----- fichiers -----
    def save_file(self, filename, content):
        ext = Path(filename).suffix.lstrip(".") or "txt"
        result = self._window.create_file_dialog(webview.FileDialog.SAVE, save_filename=filename,
                                                file_types=(f"Fichier {ext} (*.{ext})", "Tous les fichiers (*.*)"))
        if not result:
            return None
        path = result if isinstance(result, str) else result[0]
        Path(path).write_text(content, encoding="utf-8")
        return path


def main():
    api = Api()
    api._tr.load()  # prépare Whisper pendant que la fenêtre s'ouvre
    for p in KV_DIR.glob("c_*.json"):  # range les cours existants par matière
        c = api.kv_get(p.stem)
        if isinstance(c, dict) and c.get("id"):
            api._lib.export(c)
    window = webview.create_window("Cahier Malin", url=str(APP_DIR / "index.html"), js_api=api,
                                   width=1180, height=860, min_size=(420, 600), background_color="#0D1124",
                                   minimized="--demarrage" in sys.argv)
    api._window = window
    window.events.closing += lambda: api._tr.stop()
    webview.start(http_server=True, http_port=PORT, private_mode=False, storage_path=str(DATA / "fenetre"),
                  debug="--debug" in sys.argv)


if __name__ == "__main__":
    os.chdir(APP_DIR)
    try:
        main()
    except Exception:
        log("Erreur au démarrage : " + traceback.format_exc())
        raise
