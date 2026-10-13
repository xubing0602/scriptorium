"""OCR a scanned PDF into per-page line boxes (macOS Vision, local and free).

For books whose config.json says "source": "scan". Each page is rendered at
--dpi and recognized with Apple's Vision framework through `ocrmac`; the lines,
their confidences and their boxes (normalized, origin top-left, reading order)
are written to work/<slug>/ocr/p0001.json, together with "rule": where the
horizontal line above the footnotes is (the one reliable way to tell footnotes
from body text on a scanned page), — one file per page, so a stopped run
resumes and a single bad page can be redone. A plain-text dump of everything
goes to work/<slug>/ocr.txt for eyeballing; extract_scan.py reads the JSON.

Arguments:
  --book SLUG    Which book (directory under books/). Default: BOOK in .env,
                 or the only book configured.
  --pages SPEC   PDF pages to do, 1-based: "5", "1-50", "3,7,20-25".
                 Default: every page of the PDF.
  --dpi N[,N]    Render resolutions to try (default: ocr_dpi in config.json,
                 else 150,300). Vision sometimes runs two printed lines into
                 one and loses the overlap, and which resolution it happens at
                 differs page by page, so each page is recognized at every
                 resolution given and the reading with the most printed lines
                 (then the most characters) is kept. On a two-column book a
                 reading that runs one column's line into the other's is
                 charged for the scrambling as well as the lost line.
  --workers N    Parallel processes (default: 6).
  --redo         Re-OCR pages that already have a JSON file.
  --text-only    Only rebuild work/<slug>/ocr.txt from existing JSON; no OCR.
  --rules-only   Only (re)find the footnote rule on pages already OCR'd.
                 Use after changing rule_y_min in config.json.

Usage:
  python <skill>/scripts/ocr.py --book my-book --pages 46-60   # try a few pages first
  python <skill>/scripts/ocr.py --book my-book                 # the whole book
"""
import argparse
import json
import re
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from common import Progress, add_book_arg, fmt_secs, pick_book


def find_rule(img, y_min=0.45):
    """Vertical position (0-1) of the footnote separator line, or None.

    A printed rule is a long unbroken dark run; a line of text never is. The
    search starts at `y_min` of the page height: high enough to skip the body,
    low enough for a book whose footnotes take half the page."""
    import numpy as np
    a = np.array(img.convert("L"))
    dark = a < 140
    h, w = dark.shape
    for y in range(int(h * y_min), h):
        row = dark[y]
        if not row.any():
            continue
        edges = np.flatnonzero(np.diff(np.concatenate(([0], row.view(np.int8), [0]))))
        if (edges[1::2] - edges[0::2]).max() > 0.12 * w:
            return round(y / h, 4)
    return None


def rules_page(args):
    """Add "rule" to a page JSON that was written before rules existed."""
    import io

    import fitz
    from PIL import Image
    pdf, page_no, out_path, rule_y_min = args
    rec = json.loads(Path(out_path).read_text())
    doc = fitz.open(pdf)
    img = Image.open(io.BytesIO(doc[page_no - 1].get_pixmap(dpi=100).tobytes("png")))
    doc.close()
    rec["rule"] = find_rule(img, rule_y_min)
    Path(out_path).write_text(json.dumps(rec, ensure_ascii=False))
    return page_no, rec["rule"]


def page_score(lines, gutter):
    """How complete a reading of the page this is: printed lines recognized.

    On a two-column page Vision sometimes runs a line of one column into the
    line beside it, which both loses a printed line and scrambles two of them,
    so each merge is charged for more than the line it costs. Columns are
    counted separately, or lines that sit side by side would count as one."""
    from extract_scan import rows
    if not gutter:
        return len(rows(lines, 0.008))
    crossed = [l for l in lines if l["x"] < gutter - 0.02 and l["x"] + l["w"] > gutter + 0.02]
    sides = [[l for l in lines if l not in crossed and l["x"] + l["w"] / 2 < gutter],
             [l for l in lines if l not in crossed and l["x"] + l["w"] / 2 >= gutter]]
    return sum(len(rows(g, 0.008)) for g in sides) + len(crossed) - 3 * len(crossed)


def recognize(page, dpi, clip=None):
    """Vision's reading of a page, or of a region of it, in page coordinates."""
    import io

    import fitz
    from ocrmac import ocrmac
    from PIL import Image
    r = page.rect
    box = (fitz.Rect(r.x0 + clip[0] * r.width, r.y0 + clip[1] * r.height,
                     r.x0 + clip[2] * r.width, r.y0 + clip[3] * r.height)
           if clip else None)
    pix = page.get_pixmap(dpi=dpi, clip=box)
    img = Image.open(io.BytesIO(pix.tobytes("png")))
    x0, y0, sx, sy = (clip[0], clip[1], clip[2] - clip[0], clip[3] - clip[1]) if clip \
        else (0.0, 0.0, 1.0, 1.0)
    lines = []
    for text, conf, (x, y, w, h) in ocrmac.OCR(
            img, language_preference=["en-US"], recognition_level="accurate").recognize():
        # Vision's origin is bottom-left; store top-left so reading order is a sort
        lines.append({"t": text, "c": round(conf, 3), "x": round(x0 + x * sx, 4),
                      "y": round(y0 + (1.0 - y - h) * sy, 4),
                      "w": round(w * sx, 4), "h": round(h * sy, 4)})
    return lines, img, pix


def assemble(page, dpi, crops, gutter, span_re):
    """One reading of the page, with each column recognized on its own.

    Vision reads a two-column page as one image and now and then runs a line of
    one column into the line beside it, which scrambles both. Recognizing each
    column from its own crop cannot do that. The full page is still read, but
    only for the lines that genuinely run across the gutter — a chapter's
    closing colophon — which a crop would cut in half. A line that crosses the
    gutter and is not one of those is a merge, not a printed line, so the crops
    are believed instead; `span_re` is what tells the two apart."""
    full, img, pix = recognize(page, dpi)
    if not crops:
        return full, img, pix
    across = [l for l in full
              if l["x"] < gutter - 0.02 and l["x"] + l["w"] > gutter + 0.02
              and (span_re is None or re.search(span_re, l["t"]))]
    lines = list(across)
    for clip in crops:
        mine = (clip[0] + clip[2]) / 2 < gutter
        for l in recognize(page, dpi, clip)[0]:
            if (l["x"] + l["w"] / 2 < gutter) != mine:
                continue                      # belongs to the neighbouring crop
            if any(a["y"] - 0.004 < l["y"] < a["y"] + a["h"] + 0.004 for a in across):
                continue                      # the crop's half of a spanning line
            lines.append(l)
    return lines, img, pix


def ocr_page(args):
    """Render one PDF page at each dpi and keep the best reading of it."""
    import fitz
    pdf, page_no, dpis, out_path, rule_y_min, gutter, crops, span_re = args
    doc = fitz.open(pdf)
    page = doc[page_no - 1]
    best = None
    for dpi in dpis:
        lines, img, pix = assemble(page, dpi, crops, gutter, span_re)
        lines.sort(key=lambda l: (round(l["y"] * 200), l["x"]))
        score = (page_score(lines, gutter), sum(len(l["t"]) for l in lines))
        if best is None or score > best[0]:
            best = (score, {"page": page_no, "dpi": dpi, "px": [pix.width, pix.height],
                            "rule": find_rule(img, rule_y_min), "lines": lines})
    doc.close()
    Path(out_path).write_text(json.dumps(best[1], ensure_ascii=False))
    return page_no, len(best[1]["lines"])


def parse_spec(spec, n):
    if not spec:
        return list(range(1, n + 1))
    out = []
    for part in spec.split(","):
        a, dash, b = part.partition("-")
        last = n if (dash and not b) else int(b or a)   # "6-" means 6 to the end
        out += list(range(int(a), last + 1))
    return [p for p in sorted(set(out)) if 1 <= p <= n]


def write_text_dump(bk):
    """One file with every page's text, for reading and for writing the config."""
    out = []
    for path in sorted(bk.ocr.glob("p*.json")):
        rec = json.loads(path.read_text())
        out.append(f"\n===== page {rec['page']} =====")
        out += [l["t"] for l in rec["lines"]]
    dump = bk.work / "ocr.txt"
    dump.write_text("\n".join(out) + "\n")
    print(f"wrote {dump} ({len(out):,} lines)")


def main():
    ap = argparse.ArgumentParser()
    add_book_arg(ap)
    ap.add_argument("--pages")
    ap.add_argument("--dpi")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--redo", action="store_true")
    ap.add_argument("--text-only", action="store_true")
    ap.add_argument("--rules-only", action="store_true")
    a = ap.parse_args()

    bk = pick_book(a.book)
    bk.ocr.mkdir(parents=True, exist_ok=True)
    if a.rules_only:
        a.pages = a.pages or "0"
    if a.text_only:
        write_text_dump(bk)
        return

    import fitz
    doc = fitz.open(bk.pdf)
    n_pages = doc.page_count
    doc.close()
    dpis = a.dpi or bk.cfg.get("ocr_dpi", "150,300")
    dpis = [int(d) for d in str(dpis).split(",")] if not isinstance(dpis, list) else dpis
    rule_y_min = bk.cfg.get("rule_y_min", 0.45)
    # a two-column book: read each column on its own, and prefer the reading
    # that keeps them apart
    gutter_on = any(s.get("columns") == 2 for s in bk.cfg.get("sections", []))
    crops = bk.cfg.get("ocr_crops") if gutter_on else None
    # which gutter-crossing lines are really printed that way, not two lines merged
    span_re = bk.cfg.get("span_re", bk.cfg.get("chapter_end_re")) if crops else None
    gutter = (bk.cfg.get("column_split", 0.5)
              if gutter_on else None)
    pages = parse_spec(a.pages, n_pages)
    jobs = []
    for p in pages:
        out = bk.ocr / f"p{p:04d}.json"
        if out.exists() and not a.redo:
            continue
        jobs.append((str(bk.pdf), p, dpis, str(out), rule_y_min, gutter, crops, span_re))
    print(f"{bk}: {len(pages)} pages selected of {n_pages}, {len(jobs)} to OCR "
          f"at {','.join(map(str, dpis))} dpi on {a.workers} workers")
    if jobs:
        t0 = time.time()
        prog = Progress(len(jobs), "pages")
        with ProcessPoolExecutor(max_workers=a.workers) as ex:
            futs = [ex.submit(ocr_page, j) for j in jobs]
            for f in as_completed(futs):
                page_no, n = f.result()
                prog.tick(f"page {page_no}: {n} lines")
        print(f"OCR done in {fmt_secs(time.time() - t0)}")

    todo = [(str(bk.pdf), int(f.stem[1:]), str(f), rule_y_min)
            for f in sorted(bk.ocr.glob("p*.json"))
            if a.rules_only or "rule" not in json.loads(f.read_text())]
    if todo:
        prog = Progress(len(todo), "pages")
        with ProcessPoolExecutor(max_workers=a.workers) as ex:
            for f in as_completed([ex.submit(rules_page, j) for j in todo]):
                page_no, rule = f.result()
                prog.tick(f"page {page_no} rule {rule}")
    write_text_dump(bk)


if __name__ == "__main__":
    main()
