"""Seed a book's glossary from another book's, matching across romanizations.

For a book that covers the same people and places as one already translated
but prints their names differently: one edition writes Shiva, Daksha, Krishna,
another Śiva, Dakṣa, Kṛṣṇa (Tolstoy / Tolstoi, Dostoevsky / Dostoyevsky), which
a scanner then mangles further. `inherit` in config.json cannot join those up, because it
matches terms literally. This does, by reducing both spellings to a skeleton
that ignores diacritics and the usual spelling choices, and copies the Chinese
across. glossary.py is then left with only the names the other book never had.

Skeletons shorter than four letters are not matched: they collapse unrelated
short words onto each other ("God" and "Good", "Boon" and "Bon").

Entries typed `text` are left out: a work's name is a book title in one book
and a person or a place in the next. So is the other book's `note`, which
was written about its own context. Existing entries are never overwritten.

Arguments:
  --from SLUG    Book whose glossary.json supplies the Chinese. Required.
  --to SLUG      Book to seed. Required. Needs work/<slug>/book.json, so run
                 extract.py first.
  --min-freq N   Only seed terms occurring at least N times (default 2), the
                 same cut glossary.py makes.
  --write        Write books/<to>/glossary.json. Without it, nothing is
                 written and the matches are only printed.

Usage:
  python <skill>/scripts/seed_glossary.py --from old-book --to new-book
  python <skill>/scripts/seed_glossary.py --from old-book --to new-book --write
  python <skill>/scripts/glossary.py --book new-book      # decides what is left
"""
import argparse
import json
import re
import unicodedata
from collections import defaultdict

from common import BOOKS, WORK
from glossary import STOP, candidates


def skeleton(t):
    """Spelling-independent form of a name: Śiva and Shiva both give siva."""
    t = unicodedata.normalize("NFKD", t.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.replace("sh", "s").replace("ch", "c").replace("ri", "r")
    t = re.sub(r"(.)\1+", r"\1", t)
    return re.sub(r"[^a-z ]", "", t).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="src", required=True)
    ap.add_argument("--to", dest="dst", required=True)
    ap.add_argument("--min-freq", type=int, default=2)
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()

    src = json.loads((BOOKS / a.src / "glossary.json").read_text())
    by_skel = {}
    for term, e in src.items():
        if e.get("skip") or e.get("type") == "text":
            continue
        by_skel.setdefault(skeleton(term), (term, e))

    dst_path = BOOKS / a.dst / "glossary.json"
    dst = json.loads(dst_path.read_text()) if dst_path.exists() else {}
    book = json.loads((WORK / a.dst / "book.json").read_text())
    freq, _ = candidates(book, a.min_freq)

    seeded, groups = {}, defaultdict(list)
    for term in freq:
        skel = skeleton(term)
        if len(skel) < 4 or term in STOP or term in dst:
            continue      # "God" and "Good", "Boon" and "Bon" collapse together
        hit = by_skel.get(skel)
        if not hit:
            continue
        seeded[term] = {k: v for k, v in hit[1].items() if k != "note"}
        if term.lower() != hit[0].lower():
            groups[hit[0]].append(term)
    print(f"{len(freq)} candidates in {a.dst}, {len(seeded)} of them named in {a.src}")
    for s, ts in sorted(groups.items()):
        print(f"  {s} -> {', '.join(ts)} = {by_skel[skeleton(s)][1]['zh']}")
    if a.write:
        dst_path.write_text(json.dumps({**seeded, **dst}, ensure_ascii=False, indent=1) + "\n")
        print(f"wrote {dst_path} ({len(seeded) + len(dst)} entries)")
    else:
        print("nothing written (pass --write)")


if __name__ == "__main__":
    main()
