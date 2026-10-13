"""Assemble translated chunks into readable files.

Reads work/<slug>/{book.json,titles.json,translated/*.json} and writes, with
one set of files per book:
  out/<slug>.zh.md    Markdown (source for the DOCX), pandoc footnote syntax
  out/<slug>.zh.html  single-file web page: table of contents, reading
                      typography, endnotes linked both ways, light/dark, print CSS
  out/<slug>.zh.pdf   printed from the HTML with Chrome (needs `playwright` and
                      Google Chrome); has a PDF outline and clickable notes
  out/<slug>.zh.docx  via pandoc; notes become real Word footnotes, headings
                      become Word heading styles (navigation pane works)

Only what has been translated is included: chapters with no translated
paragraphs are skipped, partially translated chapters keep their translated
paragraphs. Note markers whose note is not translated yet are shown as plain
superscript numbers without a link.

Arguments:
  --book SLUG Which book (directory under books/). Default: BOOK in .env, or
              the only book configured.
  --draft     Use the first-draft text even when a reviewed version exists.
  --no-pdf    Skip the PDF.
  --no-docx   Skip the DOCX.
  --title T   Override the book title.

Usage:
  python <skill>/scripts/build.py --book my-book
"""
import argparse
import html
import json
import re
import shutil
import subprocess
from pathlib import Path

from common import NOTE_RE, OUT, WORK, add_book_arg, pick_book

TR_DIR = None   # work/<slug>/translated, set in main()

# The book restarts endnote numbering per sequence (intro 1-14, main 1-1067);
# the output uses one global sequence so footnote ids are unique.
OFFSET = {}
HAVE_NOTE = set()  # global numbers whose translation exists


def gnum(seq, n):
    return int(n) + OFFSET.get(seq, 0)


def load_translations(use_reviewed):
    chapters, notes = {}, {}
    for path in sorted(TR_DIR.glob("*.json")):
        rec = json.loads(path.read_text())
        if "notes" in rec:
            zh = rec.get("zh_reviewed") if use_reviewed and rec.get("zh_reviewed") else rec["zh"]
            for k, z in zip(rec["keys"], zh):
                notes.setdefault(rec["notes"], {})[k] = re.sub(r"^\[\^\d+\]\s*", "", z)
            continue
        zh = rec.get("zh_reviewed") if use_reviewed and rec.get("zh_reviewed") else rec["zh"]
        title = (rec.get("zh_title_reviewed") if use_reviewed else None) or rec.get("zh_title")
        ch = chapters.setdefault(rec["chapter"], {"paras": {}, "title": None})
        for i, z in zip(rec["para_idx"], zh):
            ch["paras"][i] = z
        if title and rec["chunk"] == 0:
            ch["title"] = title
    return chapters, notes


def md_inline(text, seq):
    def rep(m):
        n = gnum(seq, m.group(1))
        return f"[^{n}]" if n in HAVE_NOTE else f"^{n}^"
    return NOTE_RE.sub(rep, text)


def html_inline(text, seq):
    t = html.escape(text, quote=False)
    t = re.sub(r"\*([^*]+)\*", r"<i>\1</i>", t)

    def rep(m):
        n = gnum(seq, m.group(1))
        if n in HAVE_NOTE:
            return f'<sup><a id="r{n}" href="#n{n}">{n}</a></sup>'
        return f"<sup>{n}</sup>"
    return NOTE_RE.sub(rep, t)


HTML_CSS = """
:root{--bg:#fafaf9;--ink:#1c1c1e;--muted:#5a5a60;--rule:#d9d9d6;--accent:#3b4a6b;--note:#f2f2ef}
@media(prefers-color-scheme:dark){:root{--bg:#141416;--ink:#d9d9d4;--muted:#9a9aa0;--rule:#2c2c30;--accent:#9fb0d6;--note:#1c1c1f}}
*{box-sizing:border-box}
html{background:var(--bg);color:var(--ink);font:18px/1.95 "Noto Serif SC","Source Han Serif SC","Songti SC","STSong",serif;-webkit-font-smoothing:antialiased}
body{margin:0;display:grid;grid-template-columns:minmax(0,1fr)}
@media(min-width:1100px){body{grid-template-columns:260px minmax(0,1fr)}}
nav{padding:2.5rem 1.25rem;border-bottom:1px solid var(--rule)}
@media(min-width:1100px){nav{position:sticky;top:0;height:100vh;overflow:auto;border-bottom:0;border-right:1px solid var(--rule)}}
nav .t{font-size:.95rem;letter-spacing:.08em;color:var(--muted);margin:0 0 1rem}
nav ol{list-style:none;margin:0;padding:0}
nav li.sec{margin:1rem 0 .25rem;font-size:.85rem;letter-spacing:.06em;color:var(--muted)}
nav a{display:block;font-size:.9rem;line-height:1.6;color:var(--ink);text-decoration:none;padding:.1rem 0}
nav a:hover{color:var(--accent)}
main{max-width:40rem;padding:3rem 1.25rem 6rem;margin:0 auto;width:100%}
header{margin:0 0 4rem}
header h1{font-weight:500;font-size:1.9rem;letter-spacing:.04em;margin:0;border:0;padding:0;color:var(--ink)}
header p{text-indent:0;color:var(--muted);margin:.5rem 0 0;font-size:.95rem}
h1{font-weight:400;font-size:1.05rem;letter-spacing:.12em;color:var(--muted);margin:4rem 0 1.5rem;padding-top:1.5rem;border-top:1px solid var(--rule)}
h2{font-weight:500;font-size:1.35rem;line-height:1.5;margin:3.5rem 0 1.5rem}
p{margin:0 0 .9em;text-indent:2em;text-align:justify}
sup{font-size:.7em;line-height:0;vertical-align:.45em;margin:0 .05em}
sup a{color:var(--accent);text-decoration:none;padding:0 .15em}
sup a:hover{text-decoration:underline}
#notes{margin-top:5rem;padding-top:1.5rem;border-top:1px solid var(--rule)}
#notes h2{font-size:1.05rem;letter-spacing:.12em;color:var(--muted);font-weight:400}
#notes p{text-indent:0;font-size:.95rem;line-height:1.8;padding-left:3.2em;position:relative}
#notes p>a:first-child{position:absolute;left:0;width:2.6em;text-align:right;color:var(--accent);text-decoration:none}
#notes p:target{background:var(--note);margin-left:-.75rem;margin-right:-.75rem;padding-right:.75rem;padding-left:calc(3.2em + .75rem);border-radius:2px}
@media print{
  html{font-size:11pt;line-height:1.8;background:#fff;color:#000}
  body{display:block}
  nav{position:static;height:auto;border:0;page-break-after:always;padding:0}
  nav a{color:#000}
  main{max-width:none;padding:0;margin:0}
  h1{page-break-before:always;border:0;margin-top:0}
  h2{page-break-after:avoid}
  p{orphans:3;widows:3}
  sup a,#notes p>a:first-child{color:#000}
  #notes{page-break-before:always}
  @page{size:A4;margin:22mm 20mm}
}
"""


def render_html(title, subtitle, chapters, notes_html):
    body = [f"<header><h1>{html.escape(title)}</h1><p>{html.escape(subtitle)}</p></header>"]
    toc = ['<nav><p class="t">目录</p><ol>']
    last_sec = None
    for n, (sec, ch_title, hz) in enumerate(chapters):
        if sec is not None and sec != last_sec:
            body.append(f"<h1>{html.escape(sec)}</h1>")
            toc.append(f'<li class="sec">{html.escape(sec)}</li>')
            last_sec = sec
        plain = NOTE_RE.sub("", re.sub(r"<[^>]+>", "", ch_title))
        toc.append(f'<li><a href="#c{n}">{html.escape(plain)}</a></li>')
        body.append(f'<section id="c{n}">{hz}</section>')
    if notes_html:
        toc.append('<li class="sec"><a href="#notes">注释</a></li>')
        body.append('<div id="notes"><h2>注释</h2>' + "\n".join(notes_html) + "</div>")
    toc.append("</ol></nav>")
    return (f'<!doctype html><html lang="zh-Hans"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>{html.escape(title)}</title><style>{HTML_CSS}</style></head><body>"
            + "".join(toc) + "<main>" + "\n".join(body) + "</main></body></html>")


def make_pdf(html_path, pdf_path):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright not installed (pip install playwright); skipping PDF")
        return
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome")
        except Exception:
            browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(html_path.resolve().as_uri())
        page.emulate_media(media="print")
        page.pdf(path=str(pdf_path), format="A4", print_background=True,
                 prefer_css_page_size=True, outline=True, tagged=True)
        browser.close()
    print(f"wrote {pdf_path}")


def reference_docx():
    """pandoc's default reference.docx with Chinese serif set on the body and
    heading styles, so Word does not fall back to a random CJK font."""
    ref = WORK / "reference.docx"   # style template, shared by all books
    if ref.exists():
        return ref
    subprocess.run(["pandoc", "-o", str(ref), "--print-default-data-file", "reference.docx"], check=True)
    try:
        from docx import Document
        from docx.oxml.ns import qn
        from docx.shared import Pt
    except ImportError:
        return ref
    d = Document(str(ref))
    for name, size in (("Normal", 11.5), ("First Paragraph", 11.5), ("Body Text", 11.5),
                       ("Heading 1", 18), ("Heading 2", 14), ("Footnote Text", 9.5), ("Title", 24)):
        try:
            st = d.styles[name]
        except KeyError:
            continue
        st.font.name = "Songti SC"
        st.font.size = Pt(size)
        rpr = st.element.get_or_add_rPr()
        rfonts = rpr.find(qn("w:rFonts"))
        if rfonts is None:
            rfonts = rpr.makeelement(qn("w:rFonts"), {})
            rpr.append(rfonts)
        for k in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
            rfonts.set(qn(k), "Songti SC")
    for name in ("Normal", "First Paragraph", "Body Text"):
        try:
            pf = d.styles[name].paragraph_format
            pf.line_spacing = 1.5
            pf.space_after = Pt(6)
        except KeyError:
            pass
    d.save(str(ref))
    return ref


def make_docx(md_path, docx_path, title):
    if not shutil.which("pandoc"):
        print("pandoc not found (brew install pandoc); skipping DOCX")
        return
    subprocess.run(["pandoc", str(md_path), "-o", str(docx_path), "--from", "markdown",
                    "--toc", "--toc-depth=2", "--reference-doc", str(reference_docx()),
                    "--metadata", f"title={title}", "--metadata", "lang=zh-Hans"], check=True)
    print(f"wrote {docx_path}")


def main():
    ap = argparse.ArgumentParser()
    add_book_arg(ap)
    ap.add_argument("--draft", action="store_true")
    ap.add_argument("--no-pdf", action="store_true")
    ap.add_argument("--no-docx", action="store_true")
    ap.add_argument("--title")
    a = ap.parse_args()

    global TR_DIR
    bk = pick_book(a.book)
    TR_DIR = bk.translated
    book = json.load(open(bk.book_json))
    titles = json.loads(bk.titles_json.read_text()) if bk.titles_json.exists() else {}
    title = a.title or titles.get(book["title"]) or book["title"]
    off = 0
    for seq, notes in book["notes"].items():
        OFFSET[seq] = off
        off += max(int(k) for k in notes)
    tr, tr_notes = load_translations(not a.draft)
    for seq, d in tr_notes.items():
        HAVE_NOTE.update(gnum(seq, k) for k in d)
    OUT.mkdir(exist_ok=True)
    missing = []

    md = [f"---\ntitle: \"{title}\"\nlang: zh-Hans\n---\n"]
    chapters = []  # (section zh, chapter title, html)
    idx, skipped = 0, 0
    for sec in book["sections"]:
        seq = sec["note_seq"]
        # a part's title can carry a note of its own; titles.json is keyed
        # by the heading without it
        plain = NOTE_RE.sub("", sec["title"]).strip()
        sec_zh = titles.get(plain, plain)
        sec_md = sec_zh + "".join(f"[^{n}]" for n in NOTE_RE.findall(sec["title"]))
        sec_open = False
        for ch in sec["chapters"]:
            idx += 1
            t = tr.get(f"{idx:03d}")
            if not t or not t["paras"]:
                skipped += 1
                continue
            if not sec_open and len(book["sections"]) > 1:
                md.append(f"\n# {md_inline(sec_md, seq)}\n")
            sec_open = True
            ch_title = t["title"] or ch["title"]
            md.append(f"\n## {md_inline(ch_title, seq)}\n")
            hz = [f"<h2>{html_inline(ch_title, seq)}</h2>"]
            for i in range(len(ch["paragraphs"])):
                zh = t["paras"].get(i)
                if zh is None:
                    missing.append(f"chapter {idx} paragraph {i + 1}")
                    continue
                if not zh.strip():
                    continue       # a line the scanner lost; nothing to print
                md.append(md_inline(zh, seq) + "\n")
                hz.append(f"<p>{html_inline(zh, seq)}</p>")
            # one section only: it is the whole book, and its heading says nothing
            chapters.append((sec_zh if len(book["sections"]) > 1 else None,
                             ch_title, "\n".join(hz)))

    notes_html = []
    for seq, notes in book["notes"].items():
        for k in sorted(notes, key=int):
            zh = tr_notes.get(seq, {}).get(k)
            if zh is None:
                continue
            n = gnum(seq, k)
            md.append(f"[^{n}]: {zh}\n")
            notes_html.append(f'<p id="n{n}"><a href="#r{n}">{n}</a>{html_inline(zh, seq)}</p>')

    md_path, html_path = bk.out("md"), bk.out("html")
    md_path.write_text("\n".join(md))
    print(f"wrote {md_path}: {len(chapters)} chapters, {skipped} not yet translated, "
          f"{len(notes_html)}/{off} notes translated")
    subtitle = f"{book['title']} — 中译"
    html_path.write_text(render_html(title, subtitle, chapters, notes_html))
    print(f"wrote {html_path}")
    if not a.no_pdf:
        make_pdf(html_path, bk.out("pdf"))
    if not a.no_docx:
        make_docx(md_path, bk.out("docx"), title)
    if missing:
        print(f"{len(missing)} paragraphs in partly translated chapters were omitted, e.g. {missing[:5]}")


if __name__ == "__main__":
    main()
