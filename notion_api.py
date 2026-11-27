"""Envoi vers Notion sans claude.ai : API officielle de Notion avec une clé d'intégration (gratuite).

La page fusionnée produit du « Markdown Notion » (le format du connecteur Notion de claude.ai :
titres <h2 color>, <callout>, <toggle>, <table>, listes, équations…). Ce module le convertit
en blocs de l'API Notion et imite les trois outils utilisés par la page :
notion-search, notion-create-pages et notion-update-page (insert_content à la fin).
"""
import json
import re
import urllib.error
import urllib.request

API = "https://api.notion.com/v1"
VERSION = "2022-06-28"
MAX_TEXT = 1900  # Notion limite chaque morceau de texte à 2000 caractères

COLORS = {"gray", "brown", "orange", "yellow", "green", "blue", "purple", "pink", "red"}
LANGS = {"plain text", "python", "javascript", "typescript", "java", "c", "c++", "c#", "php", "sql", "bash", "shell",
         "html", "css", "json", "markdown", "go", "rust", "ruby", "kotlin", "swift", "r", "powershell", "yaml", "xml",
         "latex", "matlab", "scala", "dart", "lua", "perl", "haskell", "assembly", "docker", "makefile", "mermaid"}


class NotionError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def color(c):
    c = (c or "").strip()
    if c.endswith("_bg"):
        c = c[:-3] + "_background"
    base = c.replace("_background", "")
    return c if base in COLORS else "default"


# ---------------- texte en ligne -> rich_text ----------------
TOKEN = re.compile(r"\\(.)|<br\s*/?>|<(/?)(b|i|u|s|span)(?:\s+color=\"([^\"]*)\")?>|`([^`]*)`|\$([^$\n]+)\$|\[([^\]]*)\]\(([^)\s]+)\)", re.S)


def rich(text):
    """Convertit une ligne de Markdown Notion en liste rich_text de l'API."""
    out, st, buf = [], {"b": 0, "i": 0, "u": 0, "s": 0}, []
    colors = []

    def push(content, extra=None):
        if not content:
            return
        ann = {"bold": st["b"] > 0, "italic": st["i"] > 0, "underline": st["u"] > 0,
               "strikethrough": st["s"] > 0, "code": False, "color": colors[-1] if colors else "default"}
        if extra:
            ann.update(extra.get("ann", {}))
        for k in range(0, len(content), MAX_TEXT):
            part = content[k:k + MAX_TEXT]
            if extra and extra.get("eq"):
                out.append({"type": "equation", "equation": {"expression": part}, "annotations": ann})
            else:
                t = {"content": part}
                if extra and extra.get("link"):
                    t["link"] = {"url": extra["link"]}
                out.append({"type": "text", "text": t, "annotations": ann})

    def flush():
        if buf:
            push("".join(buf))
            buf.clear()

    pos = 0
    for m in TOKEN.finditer(text):
        buf.append(text[pos:m.start()])
        pos = m.end()
        if m.group(1) is not None:          # caractère échappé
            buf.append(m.group(1))
        elif m.group(0).startswith("<br"):
            buf.append("\n")
        elif m.group(3):                     # <b> <i> <u> <s> <span>
            flush()
            tag, closing = m.group(3), m.group(2) == "/"
            if tag == "span":
                if closing:
                    if colors:
                        colors.pop()
                else:
                    colors.append(color(m.group(4)))
            else:
                st[tag] = max(0, st[tag] + (-1 if closing else 1))
        elif m.group(5) is not None:         # `code`
            flush()
            push(m.group(5), {"ann": {"code": True}})
        elif m.group(6) is not None:         # $équation$
            flush()
            push(m.group(6), {"eq": True})
        elif m.group(7) is not None:         # [texte](lien)
            flush()
            url = m.group(8)
            push(re.sub(r"\\(.)", r"\1", m.group(7)), {"link": url} if re.match(r"^(https?:|mailto:)", url) else None)
    buf.append(text[pos:])
    flush()
    return out


# ---------------- blocs ----------------
def blk(kind, text="", **extra):
    body = {"rich_text": rich(text)} if kind not in ("divider", "table_of_contents", "equation") else {}
    body.update(extra)
    return {"object": "block", "type": kind, kind: body}


LIST_RE = re.compile(r"^(\t*)(- \[( |x)\] |- |\d+\. )(.*)$")
OPEN_RE = re.compile(r"^<(callout|toggle|quote|columns|column|table)\b([^>]*)>\s*$")


def attr(attrs, name):
    m = re.search(name + r'="([^"]*)"', attrs or "")
    return m.group(1) if m else None


def parse(md):
    """Markdown Notion -> liste de blocs (avec une clé interne "_children" pour les enfants)."""
    lines = md.replace("\r\n", "\n").split("\n")
    blocks, _ = _parse(lines, 0, None)
    return blocks


def _close_index(lines, i, tag):
    depth = 0
    for j in range(i, len(lines)):
        s = lines[j].strip()
        if re.match(rf"^<{tag}\b[^>]*>$", s):
            depth += 1
        elif s == f"</{tag}>":
            depth -= 1
            if depth == 0:
                return j
    return len(lines)


def _parse(lines, i, end_tag):
    out = []
    n = len(lines)
    while i < n:
        raw = lines[i]
        s = raw.strip()
        if end_tag and s == f"</{end_tag}>":
            return out, i + 1
        if not s:
            i += 1
            continue
        if s == "<table-of-contents />":
            out.append(blk("table_of_contents")); i += 1; continue
        if s == "---":
            out.append(blk("divider")); i += 1; continue
        m = re.match(r"^<h([1-6])(?:\s+color=\"([^\"]*)\")?>(.*)</h\1>$", s)
        if m:
            lvl = min(int(m.group(1)), 3)
            out.append(blk(f"heading_{lvl}", m.group(3), color=color(m.group(2)))); i += 1; continue
        if s == "$$":
            j = i + 1
            while j < n and lines[j].strip() != "$$":
                j += 1
            out.append(blk("equation", expression="\n".join(lines[i + 1:j]).strip() or " "))
            i = j + 1; continue
        if s.startswith("```"):
            lang = s[3:].strip().lower() or "plain text"
            j = i + 1
            while j < n and not lines[j].strip().startswith("```"):
                j += 1
            code = "\n".join(lines[i + 1:j])
            b = {"object": "block", "type": "code", "code": {
                "rich_text": [{"type": "text", "text": {"content": code[k:k + MAX_TEXT]}} for k in range(0, max(len(code), 1), MAX_TEXT)],
                "language": lang if lang in LANGS else "plain text"}}
            out.append(b); i = j + 1; continue
        m = OPEN_RE.match(s)
        if m:
            tag, attrs = m.group(1), m.group(2)
            if tag == "table":
                j = _close_index(lines, i, "table")
                out.append(_table(lines[i + 1:j], attr(attrs, "header-row") == "true")); i = j + 1; continue
            if tag in ("columns", "column"):
                inner, i = _parse(lines, i + 1, tag)
                out.extend(inner)  # colonnes affichées l'une sous l'autre
                continue
            inner, i = _parse(lines, i + 1, tag)
            if tag == "toggle":
                title = ""
                if inner and inner[0]["type"] == "paragraph":
                    title_blk = inner.pop(0)
                    title = title_blk["paragraph"]["rich_text"]
                lvl = {"h1": 1, "h2": 2, "h3": 3}.get(attr(attrs, "heading") or "", 0)
                kind = f"heading_{lvl}" if lvl else "toggle"
                b = {"object": "block", "type": kind, kind: {"rich_text": title or rich("Section"), "color": color(attr(attrs, "color"))}}
                if lvl:
                    b[kind]["is_toggleable"] = True
            else:  # callout, quote : le premier paragraphe devient le texte du bloc
                first = inner.pop(0)[("paragraph")]["rich_text"] if inner and inner[0]["type"] == "paragraph" else []
                kind = "callout" if tag == "callout" else "quote"
                b = {"object": "block", "type": kind, kind: {"rich_text": first, "color": color(attr(attrs, "color"))}}
                if kind == "callout":
                    b[kind]["icon"] = {"type": "emoji", "emoji": attr(attrs, "icon") or "💡"}
            if inner:
                b["_children"] = inner
            out.append(b)
            continue
        if LIST_RE.match(raw):
            items, i = _list(lines, i, 0)
            out.extend(items); continue
        m = re.match(r"^<p(?:\s+color=\"([^\"]*)\")?>(.*)</p>$", s)
        if m:
            out.append(blk("paragraph", m.group(2), color=color(m.group(1)))); i += 1; continue
        if s == "<p />":
            i += 1; continue
        # paragraphe : lignes jusqu'à une ligne vide ou un autre bloc
        para = [s]
        i += 1
        while i < n and lines[i].strip() and not _starts_block(lines[i]) and not (end_tag and lines[i].strip() == f"</{end_tag}>"):
            para.append(lines[i].strip()); i += 1
        out.append(blk("paragraph", "\n".join(para)))
    return out, i


def _starts_block(line):
    s = line.strip()
    return bool(LIST_RE.match(line) or OPEN_RE.match(s) or s.startswith("```") or s in ("$$", "---", "<table-of-contents />")
                or re.match(r"^<h[1-6]\b", s) or re.match(r"^</(callout|toggle|quote|columns|column|table)>$", s))


def _list(lines, i, depth):
    items = []
    while i < len(lines):
        m = LIST_RE.match(lines[i])
        if not m:
            break
        d = len(m.group(1))
        if d < depth:
            break
        if d > depth:  # enfant de l'élément précédent
            kids, i = _list(lines, i, d)
            if items:
                items[-1].setdefault("_children", []).extend(kids)
            else:
                items.extend(kids)
            continue
        mk, text = m.group(2), m.group(4)
        if mk.startswith("- ["):
            b = blk("to_do", text, checked=m.group(3) == "x")
        elif mk == "- ":
            b = blk("bulleted_list_item", text)
        else:
            b = blk("numbered_list_item", text)
        items.append(b)
        i += 1
    return items, i


def _table(lines, header):
    rows, cur, cell = [], None, None
    for ln in lines:
        s = ln.strip()
        if s == "<tr>":
            cur = []
        elif s == "</tr>":
            if cur is not None:
                rows.append(cur)
            cur = None
        elif s == "<td>":
            cell = []
        elif s == "</td>":
            if cur is not None and cell is not None:
                cur.append(rich("\n".join(cell).strip()))
            cell = None
        elif cell is not None:
            cell.append(s)
    width = max((len(r) for r in rows), default=1)
    children = [{"object": "block", "type": "table_row", "table_row": {"cells": r + [[]] * (width - len(r))}} for r in rows]
    return {"object": "block", "type": "table", "table": {"table_width": width, "has_column_header": header,
                                                         "has_row_header": False, "children": children}}


# ---------------- appels à l'API ----------------
class Notion:
    def __init__(self, token):
        self.token = token

    def req(self, method, path, body=None):
        r = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                   headers={"Authorization": "Bearer " + self.token, "Notion-Version": VERSION,
                                            "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(r, timeout=60) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            try:
                info = json.load(e)
            except Exception:
                info = {}
            code, msg = info.get("code", ""), info.get("message", str(e))
            if e.code == 401:
                raise NotionError("bad_token", "La clé Notion est refusée. Colle une nouvelle clé d'intégration.")
            if e.code == 404 or code == "object_not_found":
                raise NotionError("tool_error", "Notion ne trouve pas la page. Dans Notion, ouvre la page « Cahier Malin — Mes cours », "
                                                "menu ••• > Connexions, et ajoute ton intégration Cahier Malin.")
            raise NotionError("tool_error", msg)
        except urllib.error.URLError as e:
            raise NotionError("upstream_error", "Notion n'a pas répondu (connexion internet ?) : " + str(e.reason))

    def append(self, parent_id, blocks):
        """Ajoute des blocs à la fin d'une page ou d'un bloc, par paquets de 100, enfants compris."""
        for k in range(0, len(blocks), 100):
            batch = blocks[k:k + 100]
            clean = [{key: v for key, v in b.items() if key != "_children"} for b in batch]
            res = self.req("PATCH", f"/blocks/{parent_id}/children", {"children": clean})
            ids = [r["id"] for r in res.get("results", [])]
            for b, new_id in zip(batch, ids):
                if b.get("_children"):
                    self.append(new_id, b["_children"])

    def search(self, query, page_size=15):
        res = self.req("POST", "/search", {"query": query, "page_size": min(int(page_size or 15), 50),
                                           "filter": {"property": "object", "value": "page"}})
        out = []
        for p in res.get("results", []):
            title = ""
            for prop in (p.get("properties") or {}).values():
                if prop.get("type") == "title":
                    title = "".join(t.get("plain_text", "") for t in prop.get("title", []))
            out.append({"id": p["id"], "title": title or "Sans titre", "type": "page", "url": p.get("url", "")})
        return {"results": out}

    def create_pages(self, parent_id, pages):
        made = []
        for pg in pages:
            title = str((pg.get("properties") or {}).get("title") or "Sans titre")
            body = {"parent": {"page_id": parent_id}, "properties": {"title": {"title": rich(title)[:100] or [{"text": {"content": "Sans titre"}}]}}}
            icon = pg.get("icon")
            if icon and not icon.startswith(("http", "/")):
                body["icon"] = {"type": "emoji", "emoji": icon}
            page = self.req("POST", "/pages", body)
            if pg.get("content"):
                self.append(page["id"], parse(pg["content"]))
            made.append({"id": page["id"], "url": page.get("url", "")})
        return {"pages": made}


def call_tool(token, tool, args):
    """Imite les outils du connecteur Notion de claude.ai. Renvoie le « payload » attendu par la page."""
    n = Notion(token)
    if tool == "notion-search":
        return n.search(args.get("query", ""), args.get("page_size", 15))
    if tool == "notion-create-pages":
        parent = (args.get("parent") or {}).get("page_id")
        if not parent:
            raise NotionError("tool_error", "Page parente manquante.")
        return n.create_pages(parent, args.get("pages") or [])
    if tool == "notion-update-page":
        if args.get("command") != "insert_content":
            raise NotionError("tool_error", "Commande Notion non prise en charge : " + str(args.get("command")))
        n.append(args["page_id"], parse(args.get("content", "")))
        return {"ok": True}
    raise NotionError("not_in_manifest", "Outil Notion inconnu : " + tool)
