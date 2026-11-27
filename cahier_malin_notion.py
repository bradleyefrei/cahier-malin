"""Cahier Malin × Notion, en application Python, hors de claude.ai.

Ouvre la page fusionnée (fusion/index.html) dans une fenêtre pywebview et lui
fournit ce que claude.ai lui donnait (voir fusion/claude-shim.js) :
- stockage des cours en fichiers JSON dans %LOCALAPPDATA%\\Cahier Malin\\fusion-db ;
- accès au disque et lancement du moteur Python (Whisper + Ollama) ;
- enregistrement de fichiers et notifications Windows.
L'IA est Ollama (gratuite, sur le PC). Notion n'est pas disponible hors de claude.ai.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

import webview

BASE = Path(__file__).resolve().parent
PAGE_SRC = BASE / "fusion" / "index.html"
PAGE_RUN = BASE / "fusion" / "_app.html"   # copie de la page avec le remplaçant de claude.ai
DATA = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Cahier Malin"
DB_DIR = DATA / "fusion-db"
LOG = DATA / "journal-fusion.txt"
PORT = 47822
PYTHON = BASE / ".venv" / "Scripts" / "python.exe"

DB_DIR.mkdir(parents=True, exist_ok=True)


def log(msg):
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except OSError:
        pass


def build_page():
    """Insère la configuration et claude-shim.js juste après <head>, avant tous les scripts de la page."""
    html = PAGE_SRC.read_text(encoding="utf-8")
    cfg = {"py": f'"{PYTHON}"' if PYTHON.exists() else "py -3.13"}
    inject = ("<script>window.__CM=" + json.dumps(cfg).replace("<", "\\u003c") + "</script>"
              '<script src="claude-shim.js"></script>')
    html = re.sub(r"<head[^>]*>", lambda m: m.group(0) + inject, html, count=1, flags=re.I)
    PAGE_RUN.write_text(html, encoding="utf-8")


def _p(path):
    return Path(str(path).replace("/", os.sep)).expanduser()


class Proc:
    def __init__(self, cmd, cwd):
        self.p = subprocess.Popen(cmd, shell=True, cwd=cwd or None, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.out, self.err, self.lock = [], [], threading.Lock()
        threading.Thread(target=self._pump, args=(self.p.stdout, self.out), daemon=True).start()
        threading.Thread(target=self._pump, args=(self.p.stderr, self.err), daemon=True).start()

    def _pump(self, stream, sink):
        for raw in iter(stream.readline, b""):
            with self.lock:
                sink.append(raw.decode("utf-8", "replace"))

    def read(self):
        with self.lock:
            out, err = "".join(self.out), "".join(self.err)
            self.out.clear(); self.err.clear()
        code = self.p.poll()
        # attendre que les dernières lignes soient lues avant d'annoncer la fin
        done = code is not None and not out and not err
        return {"out": out, "err": err, "exited": done, "code": code}


class Api:
    """Fonctions appelées par claude-shim.js (window.pywebview.api)."""

    def __init__(self):
        self._procs = {}
        self._n = 0
        self._window = None

    # ----- stockage des cours -----
    @staticmethod
    def _doc(doc_id):
        if not re.fullmatch(r"[A-Za-z0-9_\-.~:@+]{1,200}", doc_id or ""):
            raise ValueError("identifiant invalide")
        return DB_DIR / (doc_id + ".json")

    def db_list(self, path):
        rows = []
        for f in sorted(DB_DIR.glob("*.json")):
            try:
                rows.append({"id": f.stem, "data": json.loads(f.read_text(encoding="utf-8"))})
            except (OSError, ValueError):
                log("Fichier illisible : " + f.name)
        return rows

    def db_get(self, path, doc_id):
        try:
            return json.loads(self._doc(doc_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def db_set(self, path, doc_id, body):
        f = self._doc(doc_id)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, f)
        return True

    def db_del(self, path, doc_id):
        try:
            self._doc(doc_id).unlink()
        except FileNotFoundError:
            pass
        return True

    # ----- fichiers -----
    def home(self):
        return str(Path.home())

    def fs_exists(self, p):
        return _p(p).exists()

    def fs_mkdir(self, p):
        _p(p).mkdir(parents=True, exist_ok=True)
        return True

    def fs_read(self, p):
        return _p(p).read_text(encoding="utf-8")

    def fs_write(self, p, data):
        f = _p(p)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(data, encoding="utf-8")
        return True

    def fs_append(self, p, data):
        f = _p(p)
        f.parent.mkdir(parents=True, exist_ok=True)
        with f.open("a", encoding="utf-8") as h:
            h.write(data)
        return True

    def fs_readdir(self, p):
        return [{"name": e.name, "isFile": e.is_file(), "isDirectory": e.is_dir()} for e in _p(p).iterdir()]

    def fs_rename(self, a, b):
        shutil.move(str(_p(a)), str(_p(b)))
        return True

    def fs_rmdir(self, p):
        _p(p).rmdir()  # dossiers vides seulement
        return True

    def open_path(self, p):
        os.startfile(str(_p(p)))
        return True

    # ----- processus (moteur Whisper + Ollama) -----
    def proc_spawn(self, cmd, cwd=None):
        self._n += 1
        self._procs[self._n] = Proc(cmd, str(_p(cwd)) if cwd else None)
        return self._n

    def proc_write(self, pid, text):
        pr = self._procs.get(pid)
        if pr and pr.p.poll() is None:
            pr.p.stdin.write(text.encode("utf-8"))
            pr.p.stdin.flush()
        return True

    def proc_read(self, pid):
        pr = self._procs.get(pid)
        if not pr:
            return {"out": "", "err": "", "exited": True, "code": -1}
        r = pr.read()
        if r["exited"]:
            self._procs.pop(pid, None)
        return r

    def proc_kill(self, pid):
        pr = self._procs.pop(pid, None)
        if pr and pr.p.poll() is None:
            pr.p.kill()
        return True

    def stop_all(self):
        for pr in list(self._procs.values()):
            try:
                pr.p.stdin.close()
                pr.p.wait(timeout=3)
            except Exception:
                pr.p.kill()

    # ----- enregistrer un fichier, notification -----
    def save_file(self, filename, content):
        ext = Path(filename).suffix.lstrip(".") or "txt"
        r = self._window.create_file_dialog(webview.FileDialog.SAVE, save_filename=filename,
                                            file_types=(f"Fichier {ext} (*.{ext})", "Tous les fichiers (*.*)"))
        if not r:
            return None
        path = r if isinstance(r, str) else r[0]
        Path(path).write_text(content, encoding="utf-8")
        return path

    def notify(self, body):
        """Notification Windows (bulle), sans module supplémentaire."""
        try:
            ps = ("[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;"
                  "$x = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(1);"
                  "$x.GetElementsByTagName('text').Item(0).AppendChild($x.CreateTextNode($env:CM_MSG)) > $null;"
                  "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Cahier Malin').Show([Windows.UI.Notifications.ToastNotification]::new($x))")
            subprocess.Popen(["powershell", "-NoProfile", "-Command", ps], env=dict(os.environ, CM_MSG=str(body)[:300]),
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception:
            log("Notification impossible : " + traceback.format_exc())
        return True


def main():
    build_page()
    api = Api()
    window = webview.create_window("Cahier Malin × Notion", url=str(PAGE_RUN), js_api=api, width=1280, height=900,
                                   min_size=(480, 600), minimized="--demarrage" in sys.argv)
    api._window = window
    window.events.closing += api.stop_all
    webview.start(http_server=True, http_port=PORT, private_mode=False, storage_path=str(DATA / "fenetre-fusion"),
                  debug="--debug" in sys.argv)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log("Erreur au démarrage : " + traceback.format_exc())
        raise
