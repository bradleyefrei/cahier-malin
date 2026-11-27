/* Cahier Malin × Notion, version application Python (hors de claude.ai).
   La page a été écrite pour claude.ai : elle demande ses fonctions avec claude.use(nom).
   Ce fichier fournit les mêmes fonctions, mais branchées sur le PC (Python via pywebview) :
   stockage des cours en fichiers, accès au disque, lancement du moteur Whisper + Ollama.
   Pas de Notion ni de dictée claude.ai ici : ces fonctions renvoient null, et la page le gère. */
(function () {
  "use strict";
  const CFG = window.__CM || {};
  const ready = new Promise(res => {
    if (window.pywebview && window.pywebview.api) return res();
    window.addEventListener("pywebviewready", () => res(), { once: true });
  });
  const call = async (m, ...a) => {
    await ready;
    try { return await window.pywebview.api[m](...a); }
    catch (e) { throw new Error((e && e.message) || String(e)); }
  };

  // Réglages de départ : IA locale (Ollama) et le Python qui a déjà Whisper
  try {
    const k = "cmn-dev", d = JSON.parse(localStorage.getItem(k) || "{}");
    let ch = false;
    if (!d.ai || d.ai === "claude") { d.ai = "ollama"; ch = true; }
    if (CFG.py && (!d.py || d.py === "py -3.13")) { d.py = CFG.py; ch = true; }
    if (ch) localStorage.setItem(k, JSON.stringify(d));
  } catch (e) {}

  /* ---- stockage des cours (remplace la base claude.ai) ---- */
  const db = {
    collection(path) {
      return {
        async get() {
          const rows = await call("db_list", path);
          return { docs: rows.map(r => ({ id: r.id, exists: true, data: () => r.data })) };
        },
        doc(id) {
          return {
            set: body => call("db_set", path, id, body),
            delete: () => call("db_del", path, id),
            async get() { const r = await call("db_get", path, id); return { id, exists: !!r, data: () => r || undefined }; },
          };
        },
      };
    },
  };
  const user = {
    id: async () => "local", isOwner: async () => true, canEdit: async () => true, can: async () => true,
    me: async () => ({ id: "local", name: "" }), profiles: async () => ({}),
  };

  /* ---- processus (le moteur Python) ---- */
  function spawn(cmd, args, opts) {
    const H = { out: [], err: [], exit: [], error: [] };
    let pid = null, dead = false;
    const p = {
      stdin: { async write(s) { await started; if (pid != null) await call("proc_write", pid, String(s)); } },
      stdout: { on(ev, f) { if (ev === "data") H.out.push(f); } },
      stderr: { on(ev, f) { if (ev === "data") H.err.push(f); } },
      on(ev, f) { (H[ev] || (H[ev] = [])).push(f); },
      kill() { dead = true; if (pid != null) call("proc_kill", pid).catch(() => {}); },
    };
    const full = [cmd].concat(args || []).join(" ");
    const started = call("proc_spawn", full, (opts && opts.cwd) || null).then(id => { pid = id; loop(); })
      .catch(e => { setTimeout(() => H.error.forEach(f => f(e)), 0); });
    async function loop() {
      while (!dead) {
        let r;
        try { r = await call("proc_read", pid); } catch (e) { break; }
        if (r.out) H.out.forEach(f => f(r.out));
        if (r.err) H.err.forEach(f => f(r.err));
        if (r.exited) { H.exit.forEach(f => f(r.code)); break; }
        await new Promise(s => setTimeout(s, r.out || r.err ? 30 : 150));
      }
    }
    return p;
  }

  const desktop = {
    fs: {
      exists: p => call("fs_exists", p),
      mkdir: p => call("fs_mkdir", p),
      readFile: p => call("fs_read", p),
      writeFile: (p, d) => call("fs_write", p, String(d)),
      appendFile: (p, d) => call("fs_append", p, String(d)),
      readdir: p => call("fs_readdir", p),
      rename: (a, b) => call("fs_rename", a, b),
      rmdir: p => call("fs_rmdir", p),
    },
    childProcess: { spawn },
    os: { homedir: () => call("home"), platform: async () => "win32" },
    permissions: {
      state: async () => "granted",
      request: async names => Object.fromEntries((names || ["full-access"]).map(n => [n, "granted"])),
      manage: async () => {},
    },
    shell: { openPath: p => call("open_path", p) },
    notification: { show: o => call("notify", (o && o.body) || "") },
    install: { state: async () => "installed", request: async () => "installed" },
  };
  const downloads = {
    save: async ({ filename, data }) => {
      const r = await call("save_file", filename, typeof data === "string" ? data : new TextDecoder().decode(data));
      if (!r) throw { code: "cancelled" };
      return r;
    },
  };

  const caps = { db, user, desktop, downloads };
  window.claude = { use: async name => caps[name] || null };
})();
