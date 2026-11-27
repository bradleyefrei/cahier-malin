/* Cahier Malin × Notion (version Python) : envoi automatique vers Notion chaque soir à 23:00.
   Chargé après le script de la page : il réutilise ses fonctions (sendCourse, kitToMd, targets, S…).
   - Chaque cours rédigé et pas encore dans Notion est envoyé dans la page de sa matière (sous-page, avec les révisions).
   - Si les révisions d'un cours déjà envoyé ont été faites ensuite, elles sont ajoutées à sa page Notion.
   - Si l'ordinateur était éteint à 23:00, l'envoi se fait à la prochaine ouverture de l'appli. */
(function () {
  "use strict";
  const HOUR = 23;
  const KEY = "cmn-notion-auto";
  let running = false;

  const todayStr = () => { const d = new Date(); return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0"); };
  const last = () => { try { return localStorage.getItem(KEY) || ""; } catch (e) { return ""; } };
  const setLast = v => { try { localStorage.setItem(KEY, v); } catch (e) {} };
  const pageId = url => { const m = String(url || "").match(/([0-9a-f]{32})(?:[?#]|$)/i); return m ? m[1] : null; };

  function pending() {
    if (typeof S === "undefined" || !S.loaded) return { send: [], kits: [] };
    const ready = S.courses.filter(c => c && c.kind === "course" && c.status === "ready");
    return {
      send: ready.filter(c => !(c.notion && c.notion.url)),
      // seulement les cours envoyés AVANT d'avoir leurs révisions (kit === false), jamais en double
      kits: ready.filter(c => c.notion && c.notion.url && c.kit && c.notion.kit === false && pageId(c.notion.url)),
    };
  }

  async function run(reason) {
    if (running || (typeof aiBusy !== "undefined" && aiBusy)) return;
    const p = pending();
    if (!p.send.length && !p.kits.length) { setLast(todayStr()); return; }
    running = true;
    let ok = 0, fail = 0, firstErr = "";
    const mcp = await mcpP;
    for (const c of p.send) {
      try {
        // même matière déjà créée dans Notion (par un autre cours) : on l'utilise au lieu d'en créer une deuxième
        if (!c.subjectId || c.subjectId === "new") {
          const t = targets.find(x => x.name.trim().toLowerCase() === String(c.subject || "").trim().toLowerCase());
          if (t) c.subjectId = t.id;
        }
        setAuto("Envoi vers Notion : " + (c.title || "cours"));
        await sendCourse(c, { sub: c.subjectId && c.subjectId !== "new", kit: !!c.kit });
        if (c.notion) { c.notion.kit = !!c.kit; saveCourse(c); }
        ok++;
      } catch (e) { fail++; firstErr = firstErr || notionErr(e); }
    }
    for (const c of p.kits) {
      try {
        setAuto("Ajout des révisions dans Notion : " + (c.title || "cours"));
        await mcp.callTool("Notion", "notion-update-page", { page_id: pageId(c.notion.url), command: "insert_content", position: { type: "end" }, allow_async: false, content: kitToMd(c.kit) });
        c.notion.kit = true; saveCourse(c); ok++;
      } catch (e) { fail++; firstErr = firstErr || notionErr(e); }
    }
    setAuto("");
    running = false;
    if (!fail) setLast(todayStr());
    const msg = ok ? ok + " cours envoyé" + (ok > 1 ? "s" : "") + " dans Notion." : "";
    if (msg) { toast(msg + (fail ? " " + fail + " en échec." : ""), fail ? "err" : "ok"); notify(msg); }
    else if (fail) toast("Envoi automatique vers Notion impossible : " + firstErr, "err");
    try { rerender(); } catch (e) {}
  }

  function tick() {
    if (typeof S === "undefined" || !S.loaded) return;
    const now = new Date(), p = pending();
    if (!p.send.length && !p.kits.length) return;
    const lateCourse = p.send.some(c => (c.createdAt || "") < todayStr());  // un cours d'un jour précédent pas encore envoyé
    if ((now.getHours() >= HOUR && last() !== todayStr()) || lateCourse) run("auto");
  }

  window.cahierNotionSendNow = () => run("manuel");
  setInterval(tick, 60000);
  setTimeout(() => { if ((window.__CM || {}).sendNow) run("demande"); else tick(); }, 20000);
})();
