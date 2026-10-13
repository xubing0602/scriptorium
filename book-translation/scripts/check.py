"""Sanity checks with numbers, so a bad extraction or a lossy translation is
caught before money is spent on it or before the book is handed over. No API.

Default (after extract.py), reads work/<slug>/book.json and reports:
  - sections, chapters, paragraphs, words, notes
  - chapters that look wrong: almost empty, or far longer than the rest
    (a missed heading merges two chapters), duplicate titles
  - share of paragraphs that end mid-sentence: a paragraph broken by a page
    or by a misread indent. Under ~15% is normal for prose with quotations
    and verse; well above that means the paragraph rule in config.json is wrong
  - share of one-line paragraphs: every line turned into its own paragraph
  - note markers whose note does not exist, notes no marker points to
  - rows that do not look like English (scanner noise, a second script)
  - for scans: share of OCR'd words that never reached book.json (text lost
    to head_max / foot_min / drop_re / min_wordlike)

--output (after build.py), reads out/<slug>.zh.md and the translated chunks:
  - chapters translated / total, notes translated / total
  - footnotes defined but never referenced, references with no definition:
    a marker the model dropped from a long paragraph
  - Chinese paragraphs with much untranslated English left in them
  - what is still listed in problems.txt

Exit status is 1 when a check fails badly, so it can gate a run.

Arguments:
  --book SLUG   Which book (default: BOOK in .env, or the only book).
  --output      Check the built output instead of the extraction.
  --show N      Print up to N examples for each finding (default 5).

Usage:
  python <skill>/scripts/check.py --book my-book
  python <skill>/scripts/check.py --book my-book --output
"""
import argparse
import json
import re
import statistics
import sys

from common import NOTE_RE, add_book_arg, pick_book

END_RE = re.compile(r"""[.!?:;…)\]”"’'*]\s*(\[\^\d+\]\s*)*$""")
VOWEL_RE = re.compile(r"[aeiouyAEIOUY]")


def englishness(t):
    toks = [w for w in re.findall(r"[A-Za-z'-]+", t) if len(w) > 1]
    if len(toks) < 4:
        return 1.0
    return sum(1 for w in toks if VOWEL_RE.search(w) and len(w) <= 14) / len(toks)


def check_extract(bk, show):
    book = json.loads(bk.book_json.read_text())
    chs = [(s["title"], s["note_seq"], c) for s in book["sections"] for c in s["chapters"]]
    paras = [(i, p) for i, (_, _, c) in enumerate(chs) for p in c["paragraphs"]]
    words = [sum(len(p.split()) for p in c["paragraphs"]) for _, _, c in chs]
    n_notes = sum(len(v) for v in book["notes"].values())
    print(f"{bk}: {len(book['sections'])} sections, {len(chs)} chapters, {len(paras)} paragraphs, "
          f"{sum(words):,} words, {n_notes} notes")
    bad = False

    med = statistics.median(words) if words else 0
    tiny = [(i, w) for i, w in enumerate(words) if w < 60]
    huge = [(i, w) for i, w in enumerate(words) if med and w > 4 * med and w > 3000]
    print(f"\nchapter length: median {med:,.0f} words, {len(tiny)} under 60, {len(huge)} over 4x median")
    for i, w in (tiny + huge)[:show]:
        print(f"  ch {i + 1} ({w} w): {chs[i][2]['title'][:70]}")
    titles = [c["title"] for _, _, c in chs]
    dup = sorted({t for t in titles if titles.count(t) > 1 and re.search(r"\d", t)})
    if dup:
        print(f"  duplicate numbered titles (a heading misread or a colophon lost): {dup[:show]}")

    long_paras = [(i, p) for i, p in paras if len(p.split()) >= 12]
    mid = [(i, p) for i, p in long_paras if not END_RE.search(p.strip())]
    share = len(mid) / max(len(long_paras), 1)
    one_line = [(i, p) for i, p in paras if len(p.split()) < 12 and not END_RE.search(p.strip())]
    print(f"\nparagraphs ending mid-sentence: {share:.0%} ({len(mid)} of {len(long_paras)})"
          + ("   <- HIGH: paragraph rule or page joins are wrong" if share > 0.25 else ""))
    for i, p in mid[:show]:
        print(f"  ch {i + 1}: …{p[-80:]!r}")
    print(f"short fragments (<12 words, no sentence end): {len(one_line)}"
          + ("   <- many: lines are becoming paragraphs" if len(one_line) > 0.1 * len(paras) else ""))
    for i, p in one_line[:show]:
        print(f"  ch {i + 1}: {p!r}")
    bad |= share > 0.35

    unresolved, used = [], set()
    for i, (sec, seq, c) in enumerate(chs):
        for p in c["paragraphs"] + [c["title"]]:
            for n in NOTE_RE.findall(p):
                used.add((seq, n))
                if n not in book["notes"].get(seq, {}):
                    unresolved.append((i, seq, n))
    orphan = [(seq, n) for seq, d in book["notes"].items() for n in d if (seq, n) not in used]
    print(f"\nnote markers with no note: {len(unresolved)};  notes with no marker: {len(orphan)}")
    for i, seq, n in unresolved[:show]:
        print(f"  ch {i + 1}: [^{n}] in sequence {seq!r}")
    if orphan:
        print(f"  unreferenced: {orphan[:show * 2]}")
    bad |= n_notes and (len(unresolved) + len(orphan)) > 0.1 * n_notes

    noise = [(i, p) for i, p in paras if englishness(p) < 0.6]
    print(f"\nparagraphs that do not read as English: {len(noise)}")
    for i, p in noise[:show]:
        print(f"  ch {i + 1}: {p[:80]!r}")
    if bk.cfg.get("source") == "scan" and bk.ocr.exists():
        # every OCR'd word should land in the body or a note; a large gap means
        # rows were dropped as running heads, page numbers or noise
        pages = {pn for sc in bk.cfg.get("sections", []) for pn in range(sc["pages"][0], sc["pages"][1] + 1)}
        ocr_words = 0
        for f in bk.ocr.glob("p*.json"):
            if int(f.stem[1:]) in pages:
                ocr_words += sum(len(l["t"].split()) for l in json.loads(f.read_text())["lines"])
        got = sum(words) + sum(len(t.split()) for d in book["notes"].values() for t in d.values())
        lost = 1 - got / max(ocr_words, 1)
        print(f"\nOCR'd words that did not reach book.json: {lost:.1%} ({ocr_words:,} read, {got:,} kept)"
              + ("   <- HIGH: rows are being dropped; check head_max / foot_min / drop_re / "
                 "min_wordlike against ocr.txt" if lost > 0.06 else
                 "  (running heads and page numbers account for a few %)"))
        bad |= lost > 0.12
    if book.get("image_pages"):
        print(f"\npages skipped (no text): {book['image_pages'][:20]}")
    return bad


def check_output(bk, show):
    book = json.loads(bk.book_json.read_text())
    md_path = bk.out("md")
    if not md_path.exists():
        sys.exit(f"{md_path} missing - run build.py first")
    md = md_path.read_text()
    defs = set(re.findall(r"^\[\^(\d+)\]:", md, re.M))
    refs = set(re.findall(r"\[\^(\d+)\](?!:)", md))
    n_ch = sum(len(s["chapters"]) for s in book["sections"])
    n_notes = sum(len(v) for v in book["notes"].values())
    chunks = [json.loads(p.read_text()) for p in sorted(bk.translated.glob("*.json"))]
    done_ch = {c["chapter"] for c in chunks if "chapter" in c}
    print(f"{bk}: {len(done_ch)}/{n_ch} chapters translated, {len(defs)}/{n_notes} notes in output")
    unref, undef = sorted(defs - refs, key=int), sorted(refs - defs, key=int)
    print(f"notes defined but never referenced (marker dropped in translation): {len(unref)}")
    if unref:
        print(f"  {unref[:show * 3]}")
    print(f"references with no note text (note not translated yet?): {len(undef)}")
    english = []
    for c in chunks:
        for en, zh in zip(c["en"], c.get("zh_reviewed") or c["zh"]):
            latin = len(re.findall(r"[A-Za-z]", NOTE_RE.sub("", zh)))
            han = len(re.findall(r"[一-鿿]", zh))
            if latin > 40 and han < 0.15 * latin:   # romanized term lists still carry Chinese
                english.append((c.get("chapter", c.get("notes")), zh))
    print(f"paragraphs left mostly in English: {len(english)}")
    for k, zh in english[:show]:
        print(f"  {k}: {zh[:80]!r}")
    if bk.problems.exists():
        lines = bk.problems.read_text().strip().splitlines()
        print(f"problems.txt: {len(lines)} flagged chunks")
        for l in lines[:show]:
            print(f"  {l[:110]}")
    else:
        print("problems.txt: none")
    return bool(unref) and len(unref) > 0.02 * max(len(defs), 1)


def main():
    ap = argparse.ArgumentParser()
    add_book_arg(ap)
    ap.add_argument("--output", action="store_true")
    ap.add_argument("--show", type=int, default=5)
    a = ap.parse_args()
    bk = pick_book(a.book)
    if not bk.book_json.exists():
        sys.exit(f"{bk.book_json} missing - run extract.py first")
    bad = check_output(bk, a.show) if a.output else check_extract(bk, a.show)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
