"""Extract a PDF book into structured JSON for translation.

Dispatches on "source" in books/<slug>/config.json:
  "text"  the PDF has a text layer: blocks are classified by font size here
  "scan"  the PDF is page images: run ocr.py first, then extract_scan.py does
          the same job from OCR line geometry

Output: work/<slug>/book.json
  {
    "title": str,
    "sections": [
      {"title": str, "note_seq": str,
       "chapters": [
         {"title": str, "pages": [start, end],
          "paragraphs": [str, ...]       # markdown-ish: *italic*, [^12] note markers
         }]}],
    "notes": {"<note_seq>": {"12": "note text", ...}}
  }

Arguments:
  --book SLUG    Which book (directory under books/). Default: BOOK in .env,
                 or the only book configured. Its config.json holds the PDF
                 path, page ranges, font sizes and note-sequence mapping.
  --pdf PATH     Override the PDF path from the config.
  --out PATH     Override the output path (default work/<slug>/book.json).

Layout keys a "text" book may set beyond the sizes, for conversions that do not
mark everything the usual way:
  note_digit_spans  any span that is only digits is a note marker, whatever its
                    size (the conversion sets superscripts in the body type)
  chapter_re        chapters and sections are printed at the same size: a
                    heading matching this regex is a chapter, the rest sections
  para_by_block     paragraphs are not indented but set off by space, so each
                    text block starts one — unless the block before it ended
                    mid-sentence, which means a paragraph broken across pages
  section_titles    rename a section the book prints under a bare half-title,
                    e.g. "Part One" -> "Book II: Part One"
  note_superscript  a digits-only span the PDF flags as superscript is a
                    marker, whatever its size
  head_max          blocks wholly above this fraction of the page height are
                    running heads, dropped (e.g. 0.08)
  foot_min          blocks wholly below this fraction are footers and page
                    numbers, dropped (e.g. 0.93)
  drop_re           blocks matching this regex are dropped

Required keys: "source": "text", "title", "pdf", "body_pages", "chapter_size".
Everything else has a default; see references/config-text.md in the skill.

Usage:
  python <skill>/scripts/extract.py --book my-book
"""
import argparse
import json
import re
import sys
from pathlib import Path

import fitz  # pymupdf

from common import add_book_arg, pick_book

# a block that ends a sentence: with "para_by_block", the next block starts a
# new paragraph; one that ends mid-sentence is a paragraph broken by a page
SENT_END_RE = re.compile(r"""[.!?][’”"')\]]*$""")
# typographic ligatures some PDFs keep as single characters ("ﬁrst")
LIGATURES = str.maketrans({"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl",
                           "ﬅ": "st", "ﬆ": "st"})
# an endnote printed as "12. text" or "12) text" rather than with a marker span
NOTE_LEAD_RE = re.compile(r"^(\d{1,4})[.)]\s+(?=\S)")


def span_kind(s, cfg):
    size = round(s["size"], 1)
    if s["text"].strip().isdigit() and (cfg.get("note_digit_spans")
                                        or size == cfg.get("note_size")
                                        or (s.get("flags", 0) & 1 and cfg.get("note_superscript"))):
        return "note"           # a superscript note number, set in the body type
    if size >= cfg.get("dropcap_size", 999):
        return "dropcap"
    if size >= cfg.get("section_size", 999):
        return "section"
    if size >= cfg["chapter_size"]:
        return "chapter"
    return "body"


def render_line(spans, cfg):
    """Turn a line's spans into text with *italic* and [^n] markers."""
    out = []
    for s in spans:
        t = s["text"].translate(LIGATURES)
        if t.strip() in cfg.get("drop_strings", []):
            continue
        kind = span_kind(s, cfg)
        if kind == "note":
            out.append(f"[^{t.strip()}]")
            continue
        italic = "Italic" in s["font"] or "Ital" in s["font"]
        if italic and t.strip():
            lead = t[: len(t) - len(t.lstrip())]
            trail = t[len(t.rstrip()):]
            out.append(f"{lead}*{t.strip()}*{trail}")
        else:
            out.append(t)
    text = "".join(out)
    # merge adjacent italic runs split by the PDF: "*foo* *bar*" -> "*foo bar*"
    text = re.sub(r"\*(\s+)\*", r"\1", text)
    return text


def page_blocks(page, cfg):
    """Yield (kind, text, first_line_indented) per text block.

    Blocks wholly above head_max or below foot_min (fractions of the page
    height) are running heads, page numbers and footers, and are skipped, as
    are blocks matching drop_re."""
    h = page.rect.height
    head_max, foot_min = cfg.get("head_max", 0), cfg.get("foot_min", 1)
    drop_re = re.compile(cfg["drop_re"]) if cfg.get("drop_re") else None
    for b in page.get_text("dict")["blocks"]:
        lines = b.get("lines")
        if not lines:
            continue
        if b["bbox"][3] <= head_max * h or b["bbox"][1] >= foot_min * h:
            continue
        kinds = {span_kind(s, cfg) for l in lines for s in l["spans"] if s["text"].strip()}
        kinds -= {"note"}       # a marker says nothing about the kind of block
        texts = []
        for l in lines:
            t = render_line(l["spans"], cfg)
            if t.strip():
                texts.append(t.strip())
        if not texts:
            continue
        text = " ".join(texts)
        # de-hyphenate line breaks like "sacri- fices"
        text = re.sub(r"(\w)- (\w)", r"\1\2", text)
        text = re.sub(r"\s+", " ", text).strip()
        if drop_re and drop_re.search(text):
            continue
        if kinds == {"section"} or kinds == {"chapter"}:
            kind = kinds.pop()
            text = text.strip("*")
            rx = cfg.get("chapter_re")
            if rx:   # one type size serves both headings: the wording decides
                kind = "chapter" if re.match(rx, text) else "section"
        else:
            kind = "body"
        indented = lines[0]["bbox"][0] - b["bbox"][0] > cfg.get("indent_min", 8)
        yield kind, text, indented


def extract(pdf_path, cfg):
    doc = fitz.open(pdf_path)
    p0, p1 = cfg["body_pages"]  # 1-indexed inclusive
    sections, notes, image_pages = [], {}, []
    cur_sec, cur_ch = None, None

    def new_section(title):
        nonlocal cur_sec, cur_ch
        # a sub-section printed under a half-title of its own ("Purva Bhaga")
        # reads as nothing on its own once the half-title page is dropped
        title = cfg.get("section_titles", {}).get(title, title)
        seq = cfg.get("note_seq_by_section", {}).get(title, cfg.get("default_note_seq", "main"))
        cur_sec = {"title": title, "note_seq": seq, "chapters": []}
        sections.append(cur_sec)
        cur_ch = None

    def new_chapter(title, page):
        nonlocal cur_ch
        if cur_sec is None:
            new_section(cfg.get("first_section_title", cfg.get("title", "Text")))
        cur_ch = {"title": title, "pages": [page, page], "paragraphs": []}
        cur_sec["chapters"].append(cur_ch)

    for pn in range(p0 - 1, p1):
        page = doc[pn]
        if not page.get_text().strip() and page.get_images():
            image_pages.append(pn + 1)  # scanned page: no text layer, skipped
        for title, start in cfg.get("section_starts", {}).items():
            if start == pn + 1 and (cur_sec is None or cur_sec["title"] != title):
                new_section(title)
        for kind, text, indented in page_blocks(page, cfg):
            if kind == "section":
                if cur_sec is None or cur_sec["title"] != text:
                    new_section(text)
                continue
            if kind == "chapter":
                if cur_ch is not None and not cur_ch["paragraphs"]:
                    cur_ch["title"] += " " + text  # heading wrapped onto a 2nd line
                else:
                    new_chapter(text, pn + 1)
                continue
            if cur_ch is None:
                new_chapter(cur_sec["title"] if cur_sec else "Untitled", pn + 1)
            paras = cur_ch["paragraphs"]
            if not paras:
                # note marker (and subtitle) that belonged to the heading line
                m = re.match(r"^(\[\^\d+\](?:\s*\([^)]*\))?)\s*", text)
                if m:
                    cur_ch["title"] += " " + m.group(1)
                    text = text[m.end():]
            if paras and not indented and not (
                    cfg.get("para_by_block") and SENT_END_RE.search(paras[-1])):
                paras[-1] = (paras[-1] + " " + text).strip()
            else:
                paras.append(text)
            cur_ch["pages"][1] = pn + 1

    # endnotes: "<n> text" runs, numbering restarts mark a new sequence
    n0, n1 = cfg.get("notes_pages") or (1, 0)      # no endnotes: empty range
    seqs = cfg.get("note_seq_order", ["main"])
    seq_i, last = 0, 0
    cur = None
    for pn in range(n0 - 1, n1):
        for kind, text, _ in page_blocks(doc[pn], cfg):
            text = NOTE_LEAD_RE.sub(r"[^\1] ", text)
            for piece in re.split(r"(?=\[\^\d+\])", text):
                m = re.match(r"\[\^(\d+)\]\s*(.*)", piece, re.S)
                if not m:
                    if cur:
                        cur[1] += " " + piece.strip()
                    continue
                n = int(m.group(1))
                if n <= last:
                    seq_i += 1
                last = n
                if seq_i >= len(seqs):
                    sys.exit(f"endnote numbering restarted {seq_i + 1} times (page {pn + 1}); "
                             f"note_seq_order lists only {seqs}")
                seq = seqs[seq_i]
                cur = [n, m.group(2).strip()]
                notes.setdefault(seq, {})[str(n)] = cur
    notes = {k: {n: v[1].strip() for n, v in d.items()} for k, d in notes.items()}

    # A part's half-title can carry a footnote of its own, and the marker
    # arrives on its own as the first paragraph under the title: a chapter with
    # a number and no text. Nothing is printed there, so drop the chapter — but
    # keep the marker, on the heading the note is actually about.
    for sec in sections:
        keep = []
        for ch in sec["chapters"]:
            if any(p.strip() for p in ch["paragraphs"]):
                keep.append(ch)
                continue
            for n in re.findall(r"\[\^\d+\]", ch["title"]):
                sec["title"] += n
        sec["chapters"] = keep

    # validate every marker resolves
    missing = 0
    for sec in sections:
        for ch in sec["chapters"]:
            for p in ch["paragraphs"]:
                for n in re.findall(r"\[\^(\d+)\]", p):
                    if n not in notes.get(sec["note_seq"], {}):
                        missing += 1
    sections = [s for s in sections if s["chapters"]]
    return {"title": cfg["title"], "sections": sections, "notes": notes,
            "image_pages": image_pages}, missing


def main():
    ap = argparse.ArgumentParser()
    add_book_arg(ap)
    ap.add_argument("--pdf")
    ap.add_argument("--out")
    a = ap.parse_args()

    bk = pick_book(a.book)
    cfg = bk.cfg
    pdf = Path(a.pdf) if a.pdf else bk.pdf
    if not pdf.exists():
        sys.exit(f"PDF not found: {pdf}")
    out = Path(a.out) if a.out else bk.book_json
    if cfg.get("source") == "scan":
        from extract_scan import extract_scan
        book, missing = extract_scan(bk)
    else:
        book, missing = extract(pdf, cfg)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(book, ensure_ascii=False, indent=1))
    n_ch = sum(len(s["chapters"]) for s in book["sections"])
    n_p = sum(len(c["paragraphs"]) for s in book["sections"] for c in s["chapters"])
    n_w = sum(len(p.split()) for s in book["sections"] for c in s["chapters"] for p in c["paragraphs"])
    n_n = sum(len(v) for v in book["notes"].values())
    print(f"{len(book['sections'])} sections, {n_ch} chapters, {n_p} paragraphs, "
          f"{n_w:,} words, {n_n} notes, {missing} unresolved note markers, "
          f"{len(book.get('image_pages', []))} image-only pages skipped -> {out}")
    for s in book["sections"]:
        print(f"  {s['title']} [{s['note_seq']}]: {len(s['chapters'])} chapters")


if __name__ == "__main__":
    main()
