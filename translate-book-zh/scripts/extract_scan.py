"""Turn OCR line boxes (ocr.py) into the same book.json that extract.py makes.

A scanned page carries no fonts, so structure is read off the geometry and the
wording instead:

  running head   lines above `head_max` of the page height
  footnotes      everything below the printed rule that ocr.py located at the
                 foot of the page ("rule"); where no rule was found, the block
                 at the foot whose characters-per-width is well above the page
                 median (smaller type). They are numbered by sequence, since
                 OCR mangles the small printed digits
  chapter        a line matching `chapter_re` ("CHAPTER FIFTY-ONE", or just
                 "EIGHTY-NINE" when OCR drops the word); the lines under it, up
                 to the first speaker or verse line, are its title. In prose
                 parts without verse numbers, `heading_re` ("IV. CONTENT OF
                 THE ...") does the same job and its title runs on for as long
                 as the lines stay in capitals
  paragraph      starts at a verse number ("4-5.", "41cd-47."), at a speaker
                 line ("The bard said:"), or at an indented first line;
                 everything else continues the paragraph above
  note markers   superscripts OCR badly ("sovereign.270" -> "sovereign. 70",
                 "GnP.6" -> "GnP.®"), so each footnote found on a page is
                 matched back into that page's text by its digits, then by a
                 superscript-shaped artifact, and failing both it is attached at
                 the end of the last paragraph on the page. The tally of the
                 three is printed, and work/<slug>/notes_report.txt lists every
                 note with how it was placed and any gap in the numbering.

Config keys (books/<slug>/config.json) beyond the ones extract.py uses:
  sections      [{"title", "pages": [first, last], "note_seq",
                "columns", "notes", "heading_re"}] in PDF page numbers;
                "columns" is 1 or 2, "notes" turns footnote
                detection off for a section that has none (default: on for a
                one-column section)
  column_split  where a two-column page divides, as a fraction of page width
                (default 0.5)
  foot_min      rows at or below this fraction of the page height that are
                nothing but a number are dropped as page numbers (default: off)
  min_wordlike  drop a row unless this share of its words could be English
                (default 0, off); for a scan that also carries another script
  chapter_end_re  for a book that closes each chapter with a colophon ("Thus
                ends the First Chapter...") instead of opening it with a
                heading: the line matching this ends the chapter, is dropped,
                and gives the chapter its number
  subworks      [{"title", "match": regex, "pages": [first, last]}] — a work
                embedded in the book whose chapters are numbered from 1 again.
                A colophon matching the regex, or falling in the page range,
                counts against that work instead of the main sequence; the page
                range is the reliable one, since the scanner mangles the name
  head_max      fraction of page height that is running head (default 0.095)
  note_y_min    footnotes cannot start above this (default 0.78)
  note_cw       how much denser than the body the footnote block's type must
                be, as a ratio of median characters per unit line width, when
                no rule was printed (default 1.08)
  note_start_re how the first line of a footnote block begins (default a
                number and a dot); "" for a book that marks notes otherwise
  note_share    how much of the block must be in the smaller type (default 0.7)
  notes_per_page  true for a book whose footnotes restart at 1 on every page.
                The note then keeps a running id of its own, while the digits
                looked for in the body stay the ones the page printed
  rule_y_min    (ocr.py) where to start looking for the footnote rule, as a
                fraction of page height (default 0.45)
  indent_min    first-line indent, as a fraction of page width (default 0.02)
  margin_pct    take the column's margin from this percentile of the line
                starts instead of the leftmost line (default 0, the leftmost),
                for a scan where the odd line spills past the measure
  title_center_min  a chapter title is centred: the title ends at the first
                row that starts within this much of the body's left margin
  title_max_width  the title also ends at the first row wider than this
                fraction of the page (default 0.55): body text runs full
                measure, titles do not
  drop_re       lines matching this are thrown away (running furniture that
                sits inside the text block)
  chapter_re    regex for a chapter heading line; group 1 must spell a number
                in words or digits ("^Chapter\\s+(\\d{1,3})$")
  verse_re      a line that starts a paragraph because it opens with a verse
                or section number (default: "1.", "4-5.", "41cd-47."); "" off
  speaker_re    a line that is a speaker heading ("The bard said:"), kept as
                its own *italic* paragraph; "" off
  heading_re    regex for an all-capitals heading in a prose section; may be
                set per section, for a book whose parts are laid out differently
  row_tol       lines within this vertical distance are one printed line
"""
import difflib
import json
import re
import statistics

HEAD_MAX = 0.095
NOTE_Y_MIN = 0.78
NOTE_CW = 1.08
NOTE_START_RE = r"^\d{1,2}[.\s]"
NOTE_SHARE = 0.7
INDENT_MIN = 0.02
ROW_TOL = 0.008
CHAPTER_RE = r"^(?:CHAPTER\s+)?([A-Z][A-Z\s\-]{2,30})[^\w\s]?$"
# "1.", "4-5.", "38-41ab.", "41cd-47.", "7c-11.", "4-7b."
VERSE_RE = r"^\d{1,4}(?:[a-f]{1,2})?(?:\s*[-–]\s*\d{1,4}(?:[a-f]{1,2})?)?\."
SPEAKER_RE = r"^.{2,45}\ssaid:$"
# what a superscript note number degrades into. `*` is one of them, so the
# closing `*` of a speaker line is skipped where this is used.
ARTIFACT_RE = r"(?<=[A-Za-z.,;:!?)\"'”’])\s?[®©°•*†‡´`'\"?^~]{1,2}(?=\s|$)"
# a row that is nothing but a number: a page number or a signature mark when it
# sits in the very bottom margin, a verse number anywhere above it
FOOT_RE = re.compile(r"^[\[({]?\s*[\dIlOo]{1,4}\s*[\])}.]?\s*$")

NUM_WORDS = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5, "SIX": 6,
             "SEVEN": 7, "EIGHT": 8, "NINE": 9, "TEN": 10, "ELEVEN": 11,
             "TWELVE": 12, "THIRTEEN": 13, "FOURTEEN": 14, "FIFTEEN": 15,
             "SIXTEEN": 16, "SEVENTEEN": 17, "EIGHTEEN": 18, "NINETEEN": 19,
             "TWENTY": 20, "THIRTY": 30, "FORTY": 40, "FIFTY": 50, "SIXTY": 60,
             "SEVENTY": 70, "EIGHTY": 80, "NINETY": 90, "HUNDRED": 100, "AND": 0}
# longest first, so SIXTY is read before SIX and NINETEEN before NINE
NUM_ORDER = sorted(NUM_WORDS, key=len, reverse=True)


def word_number(words):
    """'FIFTY-ONE', 'TWENTYONE', 'ELEVENI', '12' -> 51, 21, 11, 12. None if not a number.

    A scanned heading loses the space inside "TWENTY ONE" and picks up a stray
    letter from the line above, so the string is read greedily rather than
    split, and one trailing character is forgiven."""
    if words.strip().isdigit():
        return int(words.strip()) or None
    s = re.sub(r"[^A-Z]", "", words.upper())
    n = 0
    while s:
        for w in NUM_ORDER:
            if s.startswith(w):
                n = max(n, 1) * 100 if w == "HUNDRED" else n + NUM_WORDS[w]
                s = s[len(w):]
                break
        else:
            if n and len(s) <= 1:
                break
            return None
    return n or None


def caps(t):
    """Is this line set in capitals? (Ś, Ā and friends count too.)"""
    letters = [c for c in t if c.isalpha()]
    return bool(letters) and sum(c.isupper() for c in letters) >= 0.8 * len(letters)


def rows(lines, tol):
    """Group OCR lines into printed lines: same row if their tops are close."""
    out = []
    for l in sorted(lines, key=lambda l: l["y"]):
        if out and abs(l["y"] - out[-1][0]["y"]) <= tol:
            out[-1].append(l)
        else:
            out.append([l])
    res = []
    for row in out:
        row.sort(key=lambda l: l["x"])
        text = " ".join(l["t"].strip() for l in row if l["t"].strip())
        if not text:
            continue
        w = sum(l["w"] for l in row)
        res.append({"t": re.sub(r"\s+", " ", text), "x": min(l["x"] for l in row),
                    "y": min(l["y"] for l in row), "w": w,
                    "cw": len(text) / max(w, 0.01)})
    return res


ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
            "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
            "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14,
            "fifteenth": 15, "sixteenth": 16, "seventeenth": 17, "eighteenth": 18,
            "nineteenth": 19, "twentieth": 20, "thirtieth": 30, "fortieth": 40,
            "fiftieth": 50, "sixtieth": 60, "seventieth": 70, "eightieth": 80,
            "ninetieth": 90, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
            "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
ORD_SKIP = {"the", "tbe", "tho", "tha", "ends", "end", "chapter", "chapler", "in"}


def ordinal_number(words):
    """'Twenty-irst' -> 21, 'Sightenth' -> 18. None if nothing matches.

    A colophon's ordinal comes through the scanner misspelt as often as not, so
    each word is matched against the vocabulary by similarity rather than
    looked up."""
    n = 0
    for w in re.split(r"[^A-Za-z]+", words.lower()):
        if len(w) < 3 or w in ORD_SKIP:
            continue
        c = difflib.get_close_matches(w, ORDINALS, n=1, cutoff=0.6)
        if not c:
            continue
        v = ORDINALS[c[0]]
        n = n + v if (v < 10 or n == 0) else v
    return n or None


def colophon_number(text, last, span=5):
    """The chapter number a closing colophon states ("Thus ends the Fifth...").

    Both of the numbers a colophon carries are damaged by the scanner — the
    ordinal word and the numeral it ends with — and neither is reliably the
    better one, so whichever lands within reach of the last chapter wins, and
    failing both the chapter simply takes the next number."""
    body = re.split(r"(?i)\bchapter", text)[0]
    d = re.search(r"[-—~]\s*(\d{1,3})\s*\.?\s*$", text)
    for n in (ordinal_number(body), int(d.group(1)) if d else None):
        if n is not None and last < n <= last + span:
            return n
    return last + 1


def columns_rows(lines, tol, ncol, split, head_max, foot_min=None, min_word=0.0):
    """Printed rows in reading order, each tagged with the column it is in.

    A two-column page must be cut into columns before its lines are grouped
    into rows, or a line from each column lands in the same row and the text
    interleaves. But a line that runs across the gutter — a chapter's closing
    colophon, a heading — belongs to neither column: it interrupts them, and
    the columns start again beneath it. So the page is cut into bands at every
    such line, and the columns are read band by band. Reading a colophon as
    part of the column its midpoint happens to fall in would put a whole column
    of the next chapter before it, and would move that column's margin, which
    is what tells a new paragraph from a continued line."""
    if ncol != 2:
        out = [r for r in rows(lines, tol) if keep_row(r, head_max, foot_min, min_word)]
        for r in out:
            r["col"] = 0
        return out
    across = [l for l in lines
              if l["x"] < split - 0.02 and l["x"] + l["w"] > split + 0.02]
    rest = [l for l in lines if l not in across]
    out = []
    edges = sorted(l["y"] for l in across) + [2.0]
    lo = -1.0
    for hi in edges:
        band = [l for l in rest if lo < l["y"] < hi]
        for i, side in enumerate(((lambda l: l["x"] + l["w"] / 2 < split),
                                  (lambda l: l["x"] + l["w"] / 2 >= split))):
            for r in rows([l for l in band if side(l)], tol):
                if keep_row(r, head_max, foot_min, min_word):
                    r["col"] = i
                    out.append(r)
        for r in rows([l for l in across if l["y"] == hi], tol):
            if keep_row(r, head_max, foot_min, min_word):
                r["col"] = 0
                out.append(r)
        lo = hi
    return out


VOWEL_RE = re.compile(r"[aeiouyAEIOUY]")


def wordlike(t):
    """Share of this row's words that could be English words.

    Devanagari that the recognizer tried to read as Latin comes back as runs of
    vowelless rubbish ("al :Dy b be blelLk", "bbalak Baa2 bakgsy"). Rows with
    too few real words are page noise, not text. A row with almost no letters —
    a verse number — is left to the other tests."""
    toks = [w for w in re.findall(r"[A-Za-z'-]+", t) if len(w) > 1]
    if len(toks) < 3:
        return 1.0
    return sum(1 for w in toks if VOWEL_RE.search(w) and len(w) <= 14) / len(toks)


def keep_row(r, head_max, foot_min, min_word=0.0):
    """Is this a row of text, rather than page furniture?"""
    if r["y"] < head_max:
        return False                                      # running head
    if min_word and wordlike(r["t"]) < min_word:
        return False                                      # not English at all
    if FOOT_RE.match(r["t"].strip()):
        if foot_min and r["y"] >= foot_min:
            return False                                  # page number
        if 0.45 < r["x"] + r["w"] / 2 < 0.55:
            return False       # chapter number, centred between the columns
    return True


def half_colophon(row, body, end_re):
    """Is this colophon just the start of another one on the same page?

    Reading the columns from their own crops cuts a colophon in two, so the
    page can carry both the whole line and the half the left crop saw. The
    half opens the same way and stops short, and would otherwise close a
    chapter of its own."""
    t = row["t"].strip()
    if not end_re.search(t):
        return False
    return any(o is not row and o["t"].strip().startswith(t[:18])
               and len(o["t"].strip()) > len(t) and end_re.search(o["t"])
               for o in body)


def set_indents(body, pct=0.0):
    """Each row's indent from the margin of its own column.

    Measured on the body alone: footnotes are set to a wider measure, and
    counting them would put the margin to the left of every body line.

    `pct` guards against the other way of getting this wrong. The margin is
    normally the leftmost line, but one line that starts a little further left
    — a letter bleeding past the measure, a word pulled out of the gutter —
    moves it, and then every line on the page looks indented and becomes a
    paragraph of its own. Taking a low percentile instead of the minimum
    ignores a few such lines; 0 keeps the minimum."""
    for col in {r["col"] for r in body}:
        rs = [r for r in body if r["col"] == col]
        xs = sorted(r["x"] for r in rs)
        left = xs[int(len(xs) * pct)] if pct and len(xs) >= 8 else xs[0]
        for r in rs:
            r["ind"] = r["x"] - left


def split_notes(page_rows, cfg, rule=None):
    """(body rows, footnote rows): below the printed rule, else by type size."""
    if len(page_rows) < 4:
        return page_rows, []
    if rule:
        i = next((i for i, r in enumerate(page_rows) if r["y"] > rule - 0.004), len(page_rows))
        return page_rows[:i], page_rows[i:]
    i = split_by_type(page_rows, cfg)
    return (page_rows[:i], page_rows[i:]) if i else (page_rows, [])


def split_by_type(page_rows, cfg):
    """Index of the first footnote row, judged by type size, or None.

    Many scans print no rule above their footnotes, or print one too faint to
    find in the page image. Footnotes are set in smaller type, which shows up as
    more characters per unit of line width — but only just: the densest body
    lines of a page overlap the loosest footnote lines, so no single row can be
    judged on its own. So the size of body type is measured where nothing else
    can be — the top of the page — and the footnote block is the first block,
    low enough and opening with a printed note number, in which most of the
    lines are smaller than that. Taking the median of the block instead would
    find a split too high up, because the real notes below drag it down."""
    y_min = cfg.get("note_y_min", NOTE_Y_MIN)
    size = cfg.get("note_cw", NOTE_CW)
    share = cfg.get("note_share", NOTE_SHARE)
    start = re.compile(cfg.get("note_start_re", NOTE_START_RE))
    upper = [r["cw"] for r in page_rows if r["y"] < y_min]
    if len(upper) < 3:
        return None
    small = statistics.median(upper) * size
    for i in range(3, len(page_rows)):
        if page_rows[i]["y"] < y_min or not start.match(page_rows[i]["t"].strip()):
            continue
        block = page_rows[i:]
        if sum(r["cw"] >= small for r in block) >= share * len(block):
            return i
    return None


def join(prev, add):
    """Append a wrapped line, undoing end-of-line hyphenation."""
    if prev.endswith("-") and add[:1].islower():
        return prev[:-1] + add
    return prev + " " + add


def parse_notes(note_rows, last):
    """Footnote block -> [(number, text)].

    The printed number at the start of a note survives OCR more often than the
    superscript in the text does, so it is trusted whenever it is a plausible
    step forward; otherwise the note simply takes the next number, and any gap
    is listed in notes_report.txt."""
    out = []
    for r in note_rows:
        t = r["t"].strip()
        m = re.match(r"^(\d{1,4})[.\s]\s*(?=\S)", t)
        n = int(m.group(1)) if m else None
        if n is not None and last < n <= last + 40:
            out.append([n, t[m.end():]])
            last = n
        elif re.match(r"^[^\w\s]{1,2}\s+[A-Z\"'(]", t):      # mangled superscript digit
            last += 1
            out.append([last, re.sub(r"^[^\w\s]{1,2}\s+", "", t)])
        elif not out:
            last += 1
            out.append([last, t])
        else:
            out[-1][1] = join(out[-1][1], t)
    return [(n, txt.strip()) for n, txt in out]


def closes_quote(t, pos):
    """Does the double quote at t[pos] close a quotation opened earlier in the
    paragraph? Then it is punctuation the note sat on, and must stay."""
    before = t[:pos]
    return (before.count('"') + before.count("“") - before.count("”")) % 2 == 1


def place_markers(paras, numbers, stats):
    """Put [^id] into this page's paragraphs where the superscript was printed.

    `numbers` is (printed number, id): in a book that restarts its footnotes on
    every page the two differ — the page prints "1" but the note is the 214th of
    the book, so the digits to look for and the marker to write are not the
    same."""
    if not paras:
        return
    at = (0, 0)            # notes are printed in reading order, so markers go in too

    def first_hit(i, regexes):
        """Earliest usable match in paragraph i after the last marker placed."""
        best = None
        for cre, quote in regexes:
            for m in cre.finditer(paras[i], at[1] if i == at[0] else 0):
                if (paras[i].startswith("*") and m.end() == len(paras[i])) \
                        or "[^" in paras[i][max(0, m.start() - 6):m.start()]:
                    continue
                if best is None or m.start() < best.start():
                    best = m
                break
        return best

    for n, nid in numbers:
        placed = False
        digit = lambda d: (re.compile(r"(?<=[A-Za-z.,;:!?)\"'”’])\s?" + d + r"(?![\d])"), False)
        for key, regexes in (("exact", [digit(str(n))]),
                             ("partial", [digit(str(n)[-2:]), digit(str(n)[-1])] if n > 9 else []),
                             ("artifact", [(re.compile(ARTIFACT_RE), False)])):
            # within a tier, every pattern competes and the earliest hit wins:
            # trying one pattern across the whole page first lets this note
            # take the next note's artifact further down
            for regs in ([regexes] if key == "artifact" else [[r] for r in regexes]):
                for i in range(at[0], len(paras)):
                    m = first_hit(i, regs)
                    if m:
                        a, b = m.start(), m.end()
                        if m.group().strip() in ('"', "”") and closes_quote(paras[i], b - 1):
                            a = b    # the superscript sat on a closing quote: keep the quote
                        paras[i] = paras[i][:a] + f"[^{nid}]" + paras[i][b:]
                        at = (i, a + len(f"[^{nid}]"))
                        stats[key] += 1
                        placed = True
                        break
                if placed:
                    break
            if placed:
                break
        if not placed:
            paras[-1] = paras[-1].rstrip() + f"[^{nid}]"
            stats["appended"] += 1


NEVER = re.compile(r"(?!)")


def opt_re(cfg, key, default):
    """A configured pattern; "" (or null) switches the rule off."""
    v = cfg.get(key, default)
    return re.compile(v) if v else NEVER


def extract_scan(bk):
    cfg = bk.cfg
    chapter_re = re.compile(cfg.get("chapter_re", CHAPTER_RE))
    end_re = re.compile(cfg["chapter_end_re"]) if cfg.get("chapter_end_re") else None
    subworks = cfg.get("subworks", [])
    verse_re = opt_re(cfg, "verse_re", VERSE_RE)
    speaker_re = opt_re(cfg, "speaker_re", SPEAKER_RE)
    indent_min = cfg.get("indent_min", INDENT_MIN)
    per_page = cfg.get("notes_per_page", False)
    centre_min = cfg.get("title_center_min")
    title_wide = cfg.get("title_max_width", 0.55)
    drop_re = re.compile(cfg["drop_re"]) if cfg.get("drop_re") else None
    tol = cfg.get("row_tol", ROW_TOL)
    sections, notes, image_pages = [], {}, []
    stats = {"exact": 0, "partial": 0, "artifact": 0, "appended": 0}
    report = []

    for sec_cfg in cfg["sections"]:
        seq = sec_cfg.get("note_seq", "main")
        ncol = sec_cfg.get("columns", cfg.get("columns", 1))
        hr = sec_cfg.get("heading_re", cfg.get("heading_re"))
        heading_re = re.compile(hr) if hr else None
        notes_on = sec_cfg.get("notes", ncol == 1)
        n_ch = {}
        sec = {"title": sec_cfg["title"], "note_seq": seq, "chapters": []}
        sections.append(sec)
        cur_ch = None
        expected = 1
        next_id, last_id = 0, None
        in_title = in_title_caps = False
        p0, p1 = sec_cfg["pages"]
        for pn in range(p0, p1 + 1):
            path = bk.ocr / f"p{pn:04d}.json"
            if not path.exists():
                image_pages.append(pn)
                continue
            rec = json.loads(path.read_text())
            page_rows = columns_rows(rec["lines"], tol, ncol,
                                     cfg.get("column_split", 0.5),
                                     cfg.get("head_max", HEAD_MAX),
                                     cfg.get("foot_min"),
                                     cfg.get("min_wordlike", 0.0))
            if notes_on:
                body, note_rows = split_notes(page_rows, cfg, rec.get("rule"))
            else:
                body, note_rows = page_rows, []
            if not body:
                image_pages.append(pn)
                continue
            set_indents(body, cfg.get("margin_pct", 0.0))
            if end_re:
                body = [r for r in body if not half_colophon(r, body, end_re)]
            page_paras = []        # paragraphs that start or continue on this page
            for r in body:
                t = r["t"].strip()
                if drop_re and drop_re.match(t):
                    continue
                if end_re and end_re.search(t) and cur_ch is not None:
                    sub = next((w for w in subworks
                                if (re.search(w["match"], t) if w.get("match") else False)
                                or (w.get("pages") and w["pages"][0] <= pn <= w["pages"][1])),
                               None)
                    key = sub["title"] if sub else ""
                    n_ch[key] = colophon_number(t, n_ch.get(key, 0))
                    cur_ch["title"] = (f"{key}, Chapter {n_ch[key]}" if key
                                       else f"Chapter {n_ch[key]}")
                    report.append(f"chapter {cur_ch['title']}  ends page {pn}  {t[:60]}")
                    cur_ch, in_title = None, False
                    continue
                m = chapter_re.match(t)
                n = word_number(m.group(1)) if m else None
                head = heading_re.match(t) and caps(t) if heading_re else None
                if n or head:
                    cur_ch = {"title": f"Chapter {n}" if n else t,
                              "pages": [pn, pn], "paragraphs": []}
                    sec["chapters"].append(cur_ch)
                    in_title, in_title_caps = True, bool(head and not n)
                    page_paras = []
                    continue
                if cur_ch is None:
                    cur_ch = {"title": sec["title"], "pages": [pn, pn], "paragraphs": []}
                    sec["chapters"].append(cur_ch)
                    page_paras = []
                if in_title:
                    if centre_min and r["ind"] <= centre_min:
                        in_title = False
                    elif r["w"] > title_wide:
                        in_title = False      # a full-width line is body text, not title
                    elif in_title_caps and caps(t):
                        cur_ch["title"] = join(cur_ch["title"], t)
                        continue
                    elif in_title_caps or verse_re.match(t) or speaker_re.match(t):
                        in_title = False
                    else:
                        cur_ch["title"] = join(cur_ch["title"], t)
                        continue
                cur_ch["pages"][1] = pn
                paras = cur_ch["paragraphs"]
                if not paras and caps(t) and len(t) < 40:
                    cur_ch["title"] = t.title()      # PREFACE, INTRODUCTION
                    continue
                if speaker_re.match(t):
                    paras.append(f"*{t}*")
                    page_paras.append(len(paras) - 1)
                elif verse_re.match(t) or r["ind"] > indent_min or not paras:
                    paras.append(t)
                    page_paras.append(len(paras) - 1)
                else:
                    paras[-1] = join(paras[-1], t)
                    if not page_paras:
                        page_paras.append(len(paras) - 1)
            if note_rows:
                pairs = []           # (number printed on the page, id in book.json)
                if per_page:
                    k = 0
                    if last_id and not re.match(r"^1[.\s]", note_rows[0]["t"].strip()):
                        # a note too long for its page carries on under the next
                        while k < len(note_rows) and not re.match(
                                r"^\d{1,2}[.\s]", note_rows[k]["t"].strip()):
                            d = notes[seq]
                            d[str(last_id)] = join(d[str(last_id)], note_rows[k]["t"].strip())
                            k += 1
                        report.append(f"note {last_id}  continued onto page {pn}")
                    found = parse_notes(note_rows[k:], 0)
                    for n, txt in found:
                        next_id += 1
                        notes.setdefault(seq, {})[str(next_id)] = txt
                        report.append(f"note {next_id}  page {pn} printed {n}  {txt[:60]}")
                        pairs.append((n, next_id))
                        last_id = next_id
                else:
                    k = 0
                    prev = notes.get(seq, {}).get(str(expected - 1))
                    if prev and not re.search(r"""[.!?][)\]"'”’]*$""", prev.strip()):
                        # a note too long for its page carries on under the next
                        # page's rule with no number: the previous note stopped
                        # mid-sentence, and this row opens with no number (nor
                        # what OCR makes of one: "I5", "l3", "*"). It is the end
                        # of that note, not a new one — which would shift every
                        # number after it
                        while k < len(note_rows) and not re.match(
                                r"^([\dIlOoSs]{1,4}[.\s]|[^\w\s]{1,2}\s)", note_rows[k]["t"].strip()):
                            d = notes[seq]
                            d[str(expected - 1)] = join(d[str(expected - 1)], note_rows[k]["t"].strip())
                            k += 1
                        if k:
                            report.append(f"note {expected - 1}  continued onto page {pn}")
                    found = parse_notes(note_rows[k:], expected - 1)
                    for n, txt in found:
                        if str(n) in notes.get(seq, {}):
                            report.append(f"page {pn}: note {n} seen twice")
                        notes.setdefault(seq, {})[str(n)] = txt
                        report.append(f"note {n}  page {pn}  {txt[:60]}")
                        pairs.append((n, n))
                    if found:
                        expected = found[-1][0] + 1
                live = [cur_ch["paragraphs"][i] for i in page_paras]
                place_markers(live, pairs, stats)
                for i, p in zip(page_paras, live):
                    cur_ch["paragraphs"][i] = p

    for sec in sections:
        for ch in sec["chapters"]:
            ch["paragraphs"] = [p for p in ch["paragraphs"] if p.strip()]
        sec["chapters"] = [c for c in sec["chapters"] if c["paragraphs"]]
    missing = 0
    for sec in sections:
        for ch in sec["chapters"]:
            for p in ch["paragraphs"]:
                for n in re.findall(r"\[\^(\d+)\]", p):
                    if n not in notes.get(sec["note_seq"], {}):
                        missing += 1
    rep = bk.work / "notes_report.txt"
    rep.write_text(f"markers placed: {stats}\n\n" + "\n".join(report) + "\n")
    print(f"note markers: {stats}  (see {rep})")
    return {"title": cfg["title"], "sections": [s for s in sections if s["chapters"]],
            "notes": notes, "image_pages": image_pages}, missing
