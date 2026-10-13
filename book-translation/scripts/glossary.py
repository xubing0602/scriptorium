"""Build glossary.json: consistent Chinese renderings for names and terms.

Scans work/<slug>/book.json for candidate terms (italic foreign words, capitalized
names and multi-word names), then asks the model, in frequency order and in
batches, to fix a Chinese rendering for each. Earlier decisions are fed back
into later batches so families of names stay consistent. Edit the resulting
glossary.json by hand before translating; entries with "skip": true are
ignored.

Arguments:
  --book SLUG      Which book (directory under books/). Default: BOOK in .env,
                   or the only book configured.
  --out PATH       Output glossary JSON (default: books/<slug>/glossary.json).
                   Its existing entries are kept, as are those of any book this
                   one inherits from; only terms new to both are sent, and only
                   this book's own entries are written back.
  --min-freq N     Minimum occurrences for a capitalized name (default 2).
                   Italic terms are always included.
  --batch N        Terms per request (default 200).
  --candidates     Only write work/glossary_candidates.json and exit (no API).

Usage:
  python <skill>/scripts/glossary.py --book my-book
  python <skill>/scripts/glossary.py --book my-book --candidates
"""
import argparse
import json
import re
import sys

import regex  # \p{Lu}: names in OCR'd text carry diacritics (Ganeśa, Gajānana)
from collections import defaultdict

from common import (Progress, Usage, add_book_arg, call, client, load_glossary,
                    pick_book, prompt)

ITALIC_RE = re.compile(r"\*([^*]+)\*")
# a lower-case word carrying a diacritic: a transliterated foreign term that a scanned
# source cannot mark in italics (dūrvā, śūdra, yakṣas)
DIACRITIC_RE = regex.compile(r"\b\p{Ll}*[^\P{Ll}\p{InBasic_Latin}]\p{Ll}*\b")
# a run of Capitalized words, allowing hyphens and internal apostrophes
_W = r"\p{Lu}\p{Ll}+(?:[-’']\p{Lu}?\p{Ll}+)*"
NAME_RE = regex.compile(rf"\b({_W}(?:\s+{_W})*)")
SENT_END_RE = re.compile(r"(?:^|[.!?:]\s*[‘“’”]*\s*|[‘“]\s*)$")
STOP = set("""The A An And But Or Of In On At To For From By With As If Then When While Where
Who Whom Whose What Which Why How This That These Those There Here It Its He She They We You I
His Her Their Our Your My Me Him Them Us Is Are Was Were Be Been Being Do Does Did Has Have Had
Not No Nor So Yet Because Since Until Unless Though Although After Before Once Now Just Only Even
Also Thus Hence Therefore However Instead Otherwise Again Please Listen Hear Let Go Come Tell
Chapter Section Volume Part Book One Two Three Four Five Six Seven Eight Nine Ten Hundred Thousand
First Second Third Fourth Fifth Sixth Seventh Eighth Ninth Tenth Some Many Much Every Each All Any
Both Either Neither Such Same Other Another Very Too Quite Rather Indeed Truly Certainly Having
Having Being Having Everything Everyone Nothing Something Anyone Whoever Whatever Wherever
Earlier Later Meanwhile Thereafter Accordingly Immediately Suddenly Hearing Seeing Saying Thinking
Alternatively Among Beyond Following Like Through Using Without Towards Within Against Between
Knowing Having Having Having""".split())


def candidates(book, min_freq):
    freq = defaultdict(int)
    ctx = defaultdict(list)
    start_only = defaultdict(int)
    for sec in book["sections"]:
        for ch in sec["chapters"]:
            for p in [ch["title"]] + ch["paragraphs"]:
                for m in ITALIC_RE.finditer(p):
                    t = m.group(1).strip().lower().strip(",.;:!?’'")
                    if len(t) < 3 or " " in t and len(t) > 40:
                        continue
                    if t.endswith("said") or t.endswith("said:"):
                        continue        # speaker line, not a term
                    freq[t] += 1
                    if len(ctx[t]) < 3:
                        ctx[t].append(snippet(p, m.start()))
                plain = ITALIC_RE.sub(r"\1", p)
                for m in DIACRITIC_RE.finditer(plain):
                    t = m.group().lower()
                    if len(t) < 4:
                        continue
                    freq[t] += 1
                    if len(ctx[t]) < 3:
                        ctx[t].append(snippet(plain, m.start()))
                for m in NAME_RE.finditer(plain):
                    t = re.sub(r"[’']s?$", "", m.group(1).strip())
                    words = t.split()
                    at_start = bool(SENT_END_RE.search(plain[max(0, m.start() - 4): m.start()]))
                    if words[0] in STOP:
                        if len(words) == 1:
                            continue
                        t = " ".join(words[1:])
                        words = words[1:]
                    if words[-1] in STOP and len(words) > 1:
                        t = " ".join(words[:-1])
                    if len(words) == 1 and at_start:
                        start_only[t] += 1
                    freq[t] += 1
                    if len(ctx[t]) < 3:
                        ctx[t].append(snippet(plain, m.start()))
    out = {}
    for t, n in freq.items():
        if t.islower():
            if not t.isascii() and n < min_freq:
                continue      # OCR noise ("whọ"), not a term
            out[t] = n
        elif " " in t and n >= min_freq:
            out[t] = n
        elif n - start_only.get(t, 0) >= min_freq:
            out[t] = n
    return out, ctx


def snippet(text, pos, width=70):
    a = max(0, pos - width // 2)
    return "…" + text[a: a + width].replace("\n", " ") + "…"


def parse_json(text):
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0))


def main():
    ap = argparse.ArgumentParser()
    add_book_arg(ap)
    ap.add_argument("--out")
    ap.add_argument("--min-freq", type=int, default=2)
    ap.add_argument("--batch", type=int, default=200)
    ap.add_argument("--candidates", action="store_true")
    a = ap.parse_args()

    bk = pick_book(a.book)
    a.out = a.out or str(bk.glossary_path)
    book = json.load(open(bk.book_json))
    freq, ctx = candidates(book, a.min_freq)
    cand_path = bk.candidates_json
    cand_path.parent.mkdir(parents=True, exist_ok=True)
    cand_path.write_text(json.dumps(
        {t: {"freq": n, "contexts": ctx[t]} for t, n in sorted(freq.items(), key=lambda kv: -kv[1])},
        ensure_ascii=False, indent=1))
    print(f"{len(freq)} candidate terms -> {cand_path}")
    if a.candidates:
        return

    own = json.load(open(a.out)) if __import__("os").path.exists(a.out) else {}
    inherited = {k: v for k, v in load_glossary(bk).items() if k not in own}
    if inherited:
        print(f"{len(inherited)} terms inherited from "
              f"{', '.join(b.slug for b in bk.lineage()[1:])}")
    existing = {**inherited, **own}
    todo = [t for t, _ in sorted(freq.items(), key=lambda kv: -kv[1]) if t not in existing]
    if not todo:
        print("nothing new to decide")
        return
    batches = [todo[i: i + a.batch] for i in range(0, len(todo), a.batch)]
    print(f"{len(todo)} new terms in {len(batches)} batches")

    cli = client()
    system = prompt("glossary", bk).replace("{BOOK}", bk.style())
    usage = Usage(__import__("common").MODEL)
    prog = Progress(len(batches), "batches")
    decided = dict(own)       # only this book's own entries are written out
    for batch in batches:
        items = [{"term": t, "freq": freq[t], "contexts": ctx[t]} for t in batch]
        # only the most relevant earlier decisions: names/epithets, to keep the prompt small
        prior = {k: v for k, v in {**existing, **decided}.items() if not v.get("skip")}
        prior_txt = json.dumps({k: v["zh"] for k, v in prior.items()}, ensure_ascii=False)
        user = (f"<decided>\n{prior_txt}\n</decided>\n\n" if prior else "") + \
            "Candidate terms:\n" + json.dumps(items, ensure_ascii=False, indent=0)
        for attempt in range(2):
            try:
                text = call(cli, system, user, usage)
                res = parse_json(text)
                break
            except (json.JSONDecodeError, RuntimeError) as e:
                print("  retry:", e, file=sys.stderr)
        else:
            sys.exit("glossary batch failed twice")
        for t in batch:
            e = res.get(t)
            if not e:
                e = {"skip": True, "note": "model gave no entry"}
            decided[t] = e
        json.dump(decided, open(a.out, "w"), ensure_ascii=False, indent=1)
        prog.tick(f"{len(batch)} terms  {usage}")
    kept = sum(1 for v in decided.values() if not v.get("skip"))
    print(f"done: {kept} glossary entries ({len(decided) - kept} skipped) -> {a.out}\n{usage}")


if __name__ == "__main__":
    main()
