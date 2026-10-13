"""Look at a PDF before writing its config.json: text layer or scan, and the
type sizes that mark headings, body, notes.

Prints, for the whole PDF (no API, a few seconds):
  - page count, and how many pages have a text layer vs. are only images
    (the verdict decides "source": "text" or "scan")
  - for a text PDF: a histogram of type sizes, by characters set in each,
    with the fonts used and a sample of the text — the body size is the big
    one, headings are the few large sizes, note numbers a small size made of
    digits
  - the lines set larger than the body, with their page numbers: chapter and
    part headings, which give body_pages, section_starts and chapter_size
  - repeated lines at the top and foot of pages (running heads, page
    numbers), with suggested head_max / foot_min to drop them
  - where an endnotes section seems to start (a large "Notes" heading)

Arguments:
  pdf             Path to the PDF.
  --dump PAGES    Instead of the summary, print every text span on these
                  1-based pages ("12", "10-12", "3,40") with size, font,
                  superscript flag and x position — for reading off indents
                  and note-marker sizes.
  --headings N    How many large-type lines to list (default 60).

Usage:
  python <skill>/scripts/inspect_pdf.py pdf/my-book.pdf
  python <skill>/scripts/inspect_pdf.py pdf/my-book.pdf --dump 15-16
"""
import argparse
import re
from collections import Counter, defaultdict

import fitz  # pymupdf


def parse_pages(spec, n):
    out = []
    for part in spec.split(","):
        a, dash, b = part.partition("-")
        last = n if (dash and not b) else int(b or a)
        out += list(range(int(a), last + 1))
    return [p for p in sorted(set(out)) if 1 <= p <= n]


def dump(doc, pages):
    for pn in pages:
        page = doc[pn - 1]
        print(f"===== page {pn}  (width {page.rect.width:.0f}) =====")
        for b in page.get_text("dict")["blocks"]:
            for l in b.get("lines", []):
                for s in l["spans"]:
                    if not s["text"].strip():
                        continue
                    sup = " sup" if s["flags"] & 1 else ""
                    ital = " ital" if ("Ital" in s["font"] or s["flags"] & 2) else ""
                    print(f"  x={s['bbox'][0]:6.1f} size={s['size']:5.1f} {s['font'][:28]:28s}"
                          f"{sup}{ital}  {s['text'][:90]!r}")
            print("  --")


def summary(doc, n_headings):
    n = doc.page_count
    text_pages, image_pages = [], []
    size_chars = Counter()
    size_fonts = defaultdict(Counter)
    size_sample = {}
    digit_spans = Counter()            # digits-only spans by size: note markers
    sup_digits = 0
    lines_by_size = []                 # (size, page, text)
    edge = []                          # (y top, y bottom as page fractions, text) per line
    for pn in range(n):
        page = doc[pn]
        txt = page.get_text().strip()
        if len(txt) < 20:
            if page.get_images():
                image_pages.append(pn + 1)
            continue
        text_pages.append(pn + 1)
        for b in page.get_text("dict")["blocks"]:
            for l in b.get("lines", []):
                spans = [s for s in l["spans"] if s["text"].strip()]
                if not spans:
                    continue
                for s in spans:
                    sz = round(s["size"], 1)
                    t = s["text"].strip()
                    if t.isdigit() and len(t) <= 4:
                        digit_spans[sz] += 1
                        if s["flags"] & 1:
                            sup_digits += 1
                        continue
                    size_chars[sz] += len(t)
                    size_fonts[sz][s["font"]] += len(t)
                    if sz not in size_sample or (len(size_sample[sz][1]) < 25 < len(t)):
                        size_sample[sz] = (pn + 1, t[:70])
                h = page.rect.height
                edge.append((l["bbox"][1] / h, l["bbox"][3] / h,
                             " ".join(s["text"].strip() for s in spans)))
                big = max(spans, key=lambda s: len(s["text"]))
                lines_by_size.append((round(big["size"], 1), pn + 1,
                                      " ".join(s["text"].strip() for s in spans)))

    print(f"{n} pages: {len(text_pages)} with a text layer, {len(image_pages)} image-only")
    if len(text_pages) < 0.5 * n:
        print('verdict: SCAN -> "source": "scan"; run ocr.py, then read work/<slug>/ocr.txt')
        if text_pages:
            print(f"  ({len(text_pages)} pages do carry text: often front matter or a "
                  f"publisher's stamp — check whether it is real OCR'd text)")
        return
    print('verdict: TEXT -> "source": "text"')
    if image_pages:
        print(f"  image-only pages (skipped by extract.py): {image_pages[:30]}"
              f"{' …' if len(image_pages) > 30 else ''}")
    total = sum(size_chars.values()) or 1
    body = size_chars.most_common(1)[0][0]
    print(f"\ntype sizes by characters set (body is probably {body}):")
    for sz in sorted(size_chars, reverse=True):
        share = size_chars[sz] / total
        if share < 0.0005 and size_chars[sz] < 40:
            continue
        fonts = ", ".join(f for f, _ in size_fonts[sz].most_common(2))
        pg, sample = size_sample.get(sz, ("", ""))
        print(f"  {sz:5.1f}  {share:6.1%}  {fonts[:50]:50s}  p{pg} {sample!r}")
    if digit_spans:
        print("\ndigits-only spans (note markers, page numbers) by size: "
              + ", ".join(f"{sz}: {c}" for sz, c in sorted(digit_spans.items(),
                                                           key=lambda kv: -kv[1])[:6])
              + f"; {sup_digits} flagged superscript")
    large = [(sz, pg, t) for sz, pg, t in lines_by_size if sz >= body + 1.5 and len(t) > 1]
    print(f"\nlines set larger than the body ({len(large)}; first {n_headings}):")
    for sz, pg, t in large[:n_headings]:
        print(f"  p{pg:<4d} {sz:5.1f}  {t[:80]}")
    # running heads and page numbers: short lines at the top or foot of many pages
    tops = [(y0, y1, t) for y0, y1, t in edge if y1 < 0.12]
    foots = [(y0, y1, t) for y0, y1, t in edge if y0 > 0.88]
    canon = Counter(re.sub(r"\d+", "#", t).strip() for _, _, t in tops + foots)
    rep = [(t, c) for t, c in canon.most_common(6) if c >= max(3, 0.2 * len(text_pages))]
    if rep:
        print("\nrepeated lines at the top/foot of pages (running heads, page numbers): "
              + "; ".join(f"{t!r} x{c}" for t, c in rep))
        hm = max((y1 for y0, y1, t in tops if re.sub(r"\d+", "#", t).strip() in dict(rep)), default=None)
        fm = min((y0 for y0, y1, t in foots if re.sub(r"\d+", "#", t).strip() in dict(rep)), default=None)
        print("  suggested: " + ", ".join(x for x in (
            f'"head_max": {hm + 0.01:.2f}' if hm else "", f'"foot_min": {fm - 0.01:.2f}' if fm else "") if x)
              + " — check against a chapter-opening page, whose heading may sit high")
    notes_heads = [pg for sz, pg, t in lines_by_size
                   if sz > body and re.fullmatch(r"(?i)(end\s*)?notes", t.strip())]
    if notes_heads:
        print(f"\nlarge 'Notes' headings on pages {notes_heads} (the last is usually the "
              f"endnotes section; one in the first pages is the contents)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--dump")
    ap.add_argument("--headings", type=int, default=60)
    a = ap.parse_args()
    doc = fitz.open(a.pdf)
    if a.dump:
        dump(doc, parse_pages(a.dump, doc.page_count))
    else:
        summary(doc, a.headings)


if __name__ == "__main__":
    main()
