"""Moteur local de Cahier Malin x Notion (lancé par l'app de bureau).

Micro + transcription Whisper (faster-whisper) sur ce PC, sans internet,
compression de l'audio en Opus, et IA locale gratuite (Ollama).
Dialogue avec la page : une commande JSON par ligne sur l'entrée standard,
un événement JSON par ligne sur la sortie standard.
"""
import json
import os
import re
import sys
import threading
import time
import traceback
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdin.reconfigure(encoding="utf-8")
except Exception:
    pass

DATA = Path(__file__).resolve().parent.parent
REC_DIR = DATA / "enregistrement en cours"
LOG = DATA / "journal.txt"
SR = 16000
OUT = threading.Lock()
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

HALLUCINATIONS = re.compile(
    r"(sous-titr|amara\.org|merci d'avoir regard|abonnez-vous|n'oubliez pas de vous abonner|merci de votre attention\.?$)",
    re.IGNORECASE,
)


def emit(**o):
    with OUT:
        try:
            sys.stdout.write(json.dumps(o, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        except Exception:
            pass


def log(msg):
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except OSError:
        pass


def keep_awake(on):
    """Empêche Windows de se mettre en veille pendant un enregistrement ou une rédaction."""
    try:
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0))
    except Exception:
        pass


def has(mod):
    import importlib.util
    try:
        return importlib.util.find_spec(mod) is not None
    except Exception:
        return False


def modules():
    return {m: has(m) for m in ("numpy", "sounddevice", "faster_whisper", "av")}


def leftover():
    return len(list(REC_DIR.glob("prise_*.wav"))) if REC_DIR.exists() else 0


def list_mics():
    mics = []
    try:
        import sounddevice as sd
        default = sd.default.device[0]
        seen = set()
        for i, d in enumerate(sd.query_devices()):
            api = sd.query_hostapis(d["hostapi"])["name"]
            if d["max_input_channels"] > 0 and (api == "MME" or os.name != "nt") and d["name"] not in seen:
                seen.add(d["name"])
                mics.append({"id": i, "name": d["name"], "default": i == default})
    except Exception as e:
        log("Liste des micros impossible : " + traceback.format_exc())
        return {"mics": [], "error": str(e)}
    return {"mics": mics}


class Transcriber:
    """Micro -> morceaux de 20 à 40 s coupés dans les silences -> Whisper."""

    def __init__(self):
        self.model_name = "small"
        self.lang = "auto"
        self.model = None
        self.model_state = "idle"
        self.lock = threading.Lock()
        self.buf, self.buf_len = [], 0
        self.tail = ""
        self.level = 0.0
        self.state = "idle"
        self.stream = None
        self.wav = None
        self.worker = None
        self.wake = threading.Event()

    def configure(self, model=None, lang=None):
        if lang:
            self.lang = lang
        if model and model != self.model_name:
            self.model_name = model
            if self.model_state != "loading":
                self.model, self.model_state = None, "idle"

    def load(self):
        if self.model is not None or self.model_state == "loading":
            return
        self.model_state = "loading"
        emit(ev="model", state="loading", model=self.model_name)
        threading.Thread(target=self._load, daemon=True).start()

    def _load(self):
        try:
            from faster_whisper import WhisperModel
            threads = max(2, min(8, (os.cpu_count() or 4) // 2))
            self.model = WhisperModel(self.model_name, device="cpu", compute_type="int8", cpu_threads=threads)
            self.model_state = "ready"
            emit(ev="model", state="ready", model=self.model_name)
        except Exception as e:
            self.model_state = "error"
            log("Chargement du modèle impossible : " + traceback.format_exc())
            emit(ev="model", state="error", model=self.model_name,
                 error="Le modèle de voix n'a pas pu se charger (il faut internet la première fois). " + str(e))

    def _callback(self, indata, frames, time_info, status):
        import numpy as np
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

    def start(self, mic=None):
        if self.state != "idle":
            emit(ev="started")
            return
        try:
            import sounddevice as sd
        except Exception as e:
            emit(ev="rec_error", error="Le module sounddevice manque : installe les modules dans Réglages. " + str(e))
            return
        if self.model_state == "error":
            self.model_state = "idle"
        self.load()
        try:
            self.stream = sd.InputStream(samplerate=SR, channels=1, dtype="float32", blocksize=SR // 10,
                                         device=mic if mic not in (None, "", -1) else None, callback=self._callback)
            self.stream.start()
        except Exception as e:
            log("Micro indisponible : " + traceback.format_exc())
            self.stream = None
            emit(ev="rec_error", error="Micro introuvable ou bloqué. Vérifie qu'un micro est branché et que Windows autorise "
                 "l'accès au micro (Paramètres > Confidentialité > Microphone). Détail : " + str(e))
            return
        try:
            import wave
            REC_DIR.mkdir(parents=True, exist_ok=True)
            w = wave.open(str(REC_DIR / time.strftime("prise_%Y%m%d_%H%M%S.wav")), "wb")
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SR)
            with self.lock:
                self.wav = w
        except Exception:
            log("Fichier audio impossible : " + traceback.format_exc())
        self.state = "listening"
        keep_awake(True)
        if not self.worker or not self.worker.is_alive():
            self.worker = threading.Thread(target=self._work, daemon=True)
            self.worker.start()
        threading.Thread(target=self._meter, daemon=True).start()
        emit(ev="started")

    def _meter(self):
        while self.state == "listening":
            with self.lock:
                backlog = self.buf_len / SR
            emit(ev="level", level=round(self.level, 3), backlog=round(backlog))
            time.sleep(0.3)

    def stop(self):
        if self.state != "listening":
            if self.state == "idle":
                emit(ev="stopped")
            return
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

    def _take_chunk(self, final):
        import numpy as np
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
                if self.model_state == "error" and final:
                    with self.lock:
                        self.buf, self.buf_len = [], 0
                    self.state = "idle"
                    emit(ev="stopped")
                    return
                continue
            chunk = self._take_chunk(final)
            if chunk is not None and len(chunk) > SR // 2:
                self._transcribe(chunk)
                self.wake.set()
                continue
            if final:
                self.state = "idle"
                emit(ev="stopped")
                return

    def _transcribe(self, audio):
        try:
            segments, _ = self.model.transcribe(
                audio, beam_size=1, vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500},
                condition_on_previous_text=False,
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
                emit(ev="text", text=text)
                self.tail = (self.tail + " " + text)[-400:]
        except Exception:
            log("Transcription impossible : " + traceback.format_exc())
            emit(ev="warn", error="Un morceau n'a pas pu être transcrit (voir journal.txt).")


def _write_opus(audio_i16, path):
    """Compresse l'audio en Opus (environ 11 Mo par heure)."""
    import numpy as np
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


def _free(folder, stem, ext):
    p = folder / f"{stem}{ext}"
    n = 2
    while p.exists():
        p = folder / f"{stem} {n}{ext}"
        n += 1
    return p


def finalize(folder):
    """Assemble les prises du micro en un seul fichier audio dans le dossier du cours."""
    import wave
    import numpy as np
    parts = sorted(REC_DIR.glob("prise_*.wav")) if REC_DIR.exists() else []
    if not parts:
        emit(ev="audio", ok=False, error="Aucune prise audio à ranger.")
        return
    try:
        dest = Path(folder)
        dest.mkdir(parents=True, exist_ok=True)
        chunks = []
        for p in parts:
            try:
                with wave.open(str(p)) as w:
                    chunks.append(np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16))
            except Exception:
                log("Prise audio illisible : " + p.name)
        audio = np.concatenate(chunks) if chunks else np.zeros(0, np.int16)
        out = _free(dest, "enregistrement", ".ogg")
        if not _write_opus(audio, out):
            out = _free(dest, "enregistrement", ".wav")
            with wave.open(str(out), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(SR)
                w.writeframes(audio.tobytes())
        for p in parts:
            p.unlink(missing_ok=True)
        emit(ev="audio", ok=True, path=str(out), minutes=round(len(audio) / SR / 60, 1))
    except Exception as e:
        log("Rangement de l'audio impossible : " + traceback.format_exc())
        emit(ev="audio", ok=False, error=str(e))


def discard():
    for p in REC_DIR.glob("prise_*.wav") if REC_DIR.exists() else []:
        try:
            p.unlink()
        except OSError:
            pass
    emit(ev="discarded")


# ---------------- IA locale gratuite (Ollama) ----------------
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
    def __init__(self):
        self.jobs = {}
        self.pull = {"state": "idle", "done": 0, "total": 0, "error": ""}

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
        emit(ev="ai_status", installed=installed, running=running, model=AI_MODEL,
             has_model=bool(running and self.has_model()), pull=dict(self.pull))

    def start_pull(self):
        if self.pull["state"] == "pulling":
            return
        self.pull = {"state": "pulling", "done": 0, "total": 0, "error": ""}
        threading.Thread(target=self._pull, daemon=True).start()

    def _pull(self):
        last = 0
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
                    if time.time() - last > 1:
                        last = time.time()
                        emit(ev="pull", **self.pull)
            self.pull["state"] = "done"
        except Exception as e:
            log("Téléchargement du modèle impossible : " + traceback.format_exc())
            self.pull.update(state="error", error=str(e))
        emit(ev="pull", **self.pull)
        self.status()

    def start(self, job_id, prompt, as_json=True, max_tokens=4096):
        job = {"cancel": False}
        self.jobs[job_id] = job
        threading.Thread(target=self._run, args=(job_id, job, prompt, as_json, max_tokens), daemon=True).start()

    def _run(self, job_id, job, prompt, as_json, max_tokens):
        if not self.ensure_server():
            emit(ev="ai_done", id=job_id, error="L'IA locale (Ollama) n'est pas lancée. Vérifie qu'Ollama est installé.")
            return
        if not self.has_model():
            emit(ev="ai_done", id=job_id, error="Le modèle d'IA n'est pas encore téléchargé : Réglages > IA de rédaction.")
            return
        need = len(prompt) // 3 + max_tokens + 512
        num_ctx = min(32768, max(4096, (need // 4096 + 1) * 4096))
        body = {"model": AI_MODEL, "messages": [{"role": "user", "content": prompt}], "stream": True, "keep_alive": "10m",
                "options": {"num_ctx": num_ctx, "temperature": 0.3, "num_predict": max_tokens, "repeat_penalty": 1.15}}
        if as_json:
            body["format"] = "json"
        keep_awake(True)
        text, err, last = "", "", 0
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
                    text += (ev.get("message") or {}).get("content", "")
                    if time.time() - last > 0.7:
                        last = time.time()
                        emit(ev="ai_delta", id=job_id, text=text)
                    if ev.get("done"):
                        if ev.get("done_reason") == "length":
                            err = "La réponse a été coupée (cours très long)."
                        break
        except Exception as e:
            if not job["cancel"]:
                log("IA locale : " + traceback.format_exc())
                err = "L'IA locale s'est arrêtée : " + str(e)
        keep_awake(False)
        self.jobs.pop(job_id, None)
        if job["cancel"]:
            emit(ev="ai_done", id=job_id, error="cancelled")
        else:
            emit(ev="ai_done", id=job_id, text=text, error=err)

    def cancel(self, job_id):
        job = self.jobs.get(job_id)
        if job:
            job["cancel"] = True


def main():
    tr, ai = Transcriber(), LocalAI()
    emit(ev="hello", python=sys.version.split()[0], exe=sys.executable, modules=modules(), leftover=leftover())
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            m = json.loads(line)
        except ValueError:
            continue
        c = m.get("cmd")
        try:
            if c == "status":
                emit(ev="hello", python=sys.version.split()[0], exe=sys.executable, modules=modules(), leftover=leftover())
            elif c == "mics":
                emit(ev="mics", **list_mics())
            elif c == "preload":
                tr.configure(m.get("model"), m.get("lang"))
                if has("faster_whisper"):
                    tr.load()
            elif c == "start":
                tr.configure(m.get("model"), m.get("lang"))
                tr.start(m.get("mic"))
            elif c == "stop":
                tr.stop()
            elif c == "finalize":
                threading.Thread(target=finalize, args=(m["folder"],), daemon=True).start()
            elif c == "discard":
                discard()
            elif c == "ai_status":
                threading.Thread(target=ai.status, daemon=True).start()
            elif c == "ai_pull":
                ai.start_pull()
            elif c == "ai":
                ai.start(m["id"], m["prompt"], m.get("json", True), int(m.get("max_tokens") or 4096))
            elif c == "ai_cancel":
                ai.cancel(m.get("id"))
            elif c == "ping":
                emit(ev="pong")
        except Exception as e:
            log("Commande " + str(c) + " : " + traceback.format_exc())
            emit(ev="error", cmd=c, error=str(e))
    tr.stop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log("Erreur du moteur : " + traceback.format_exc())
        raise
