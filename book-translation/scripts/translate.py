"""Translate work/<slug>/book.json chapter by chapter, resumably.

Each chapter is split into chunks of about CHUNK_WORDS words at paragraph
boundaries. Chunks of one chapter run in order (each sees the previous chunk's
English and Chinese tail); different chapters run in parallel (CONCURRENCY).
Every finished chunk is written to <chapter>-<chunk>.json, so a
stopped run continues where it left off (work/<slug>/translated/). Output is validated (paragraph ids,
endnote markers, length). A failing chunk gets a fresh attempt with the
defects named (the earlier output is not shown, so the model does not copy
its own mistake); paragraphs still failing are re-sent one at a time; what
survives that is kept and flagged in problems.txt there.

Arguments:
  --book SLUG       Which book (directory under books/). Default: BOOK in
                    .env, or the only book configured.
  --chapters SPEC   Which chapters to run, by 1-based index into the flat
                    chapter list: "5", "1-10", "3,7,20-25". Default: all.
  --sections SUBSTR Only sections whose title contains SUBSTR (case-insensitive).
  --review          Run the second, editing pass (prompts/review.md) after the
                    draft. Roughly doubles the cost. Stored alongside the draft
                    so build.py can use either. Chunks already translated are
                    reviewed as they stand, not translated again.
  --notes           Translate the endnotes instead of chapters (in batches of
                    --notes-batch, default 50). Run this too before build.py.
  --titles          (Re)translate book and section titles -> work/<slug>/titles.json.
                    Done automatically, once, before the first chapter run.
  --redo            Ignore existing chunk files for the selected chapters.
  --dry-run         Print the chunk plan and a token/cost estimate; no API.
  --list            List chapters with their index and exit.

Usage:
  python <skill>/scripts/translate.py --book my-book --list
  python <skill>/scripts/translate.py --chapters 1 --dry-run
  python <skill>/scripts/translate.py --chapters 1            # try one chapter, read it
  python <skill>/scripts/translate.py                          # whole book
  python <skill>/scripts/translate.py --review                 # add the editing pass
  python <skill>/scripts/translate.py --notes                  # the 1,081 endnotes
"""
import argparse
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from common import (CHUNK_WORDS, CONCURRENCY, GLOSSARY_MODE, MODEL, Progress,
                    Usage, add_book_arg, call, client, format_paras,
                    glossary_block, load_glossary, parse_paras, pick_book,
                    prompt, relevant_glossary)

GLOSSARY = {}   # set in main()
TR_DIR = None   # work/<slug>/translated, set in main()
PROBLEMS = None
TITLES = None
lock = threading.Lock()


def flat_chapters(book):
    out = []
    for si, sec in enumerate(book["sections"]):
        for ci, ch in enumerate(sec["chapters"]):
            out.append({"idx": len(out) + 1, "sec": sec["title"], "note_seq": sec["note_seq"],
                        "title": ch["title"], "paragraphs": ch["paragraphs"],
                        "key": f"{len(out) + 1:03d}"})
    return out


def parse_spec(spec, n):
    if not spec:
        return set(range(1, n + 1))
    s = set()
    for part in spec.split(","):
        a, dash, b = part.partition("-")
        last = n if (dash and not b) else int(b or a)   # "6-" means 6 to the end
        s.update(range(int(a), last + 1))
    return {i for i in s if 1 <= i <= n}


def chunk_chapter(ch, target=CHUNK_WORDS):
    """Split paragraphs into consecutive groups of about `target` words."""
    chunks, cur, words = [], [], 0
    for i, p in enumerate(ch["paragraphs"]):
        w = len(p.split())
        if cur and words + w > target * 1.3:
            chunks.append(cur)
            cur, words = [], 0
        cur.append(i)
        words += w
    if cur:
        chunks.append(cur)
    return chunks


def glossary_for(text):
    if GLOSSARY_MODE != "relevant" or not GLOSSARY:
        return ""
    g = relevant_glossary(GLOSSARY, text)
    if not g:
        return ""
    return ("<glossary>\nUse exactly these renderings for the terms below "
            "(the general rules are in your instructions):\n" + glossary_block(g) + "\n</glossary>\n")


def build_user(ch, idxs, prev):
    paras = [ch["paragraphs"][i] for i in idxs]
    title = ch["title"] if idxs[0] == 0 else None
    parts = []
    if prev:
        parts.append("<previous>\n" + prev + "\n</previous>\n")
    parts.append(glossary_for(" ".join(paras) + " " + (title or "")))
    parts.append(f"<section>{ch['sec']}</section>")
    parts.append(format_paras(paras, title))
    return "\n".join(parts), paras, title


def prev_context(ch, idxs, done):
    """Last 2 paragraphs before this chunk: English, plus Chinese if translated."""
    if idxs[0] == 0:
        return None
    lo = max(0, idxs[0] - 2)
    lines = []
    for i in range(lo, idxs[0]):
        en = ch["paragraphs"][i]
        lines.append(f"<en>{en[-900:] if len(en) > 900 else en}</en>")
        zh = done.get(i)
        if zh:
            lines.append(f"<zh>{zh[-450:] if len(zh) > 450 else zh}</zh>")
    return "\n".join(lines)


PROB_PARA_RE = re.compile(r"^paragraph (\d+)")


def robust_translate(cli, system, user, paras, usage, single_user):
    """Translate `paras` (already formatted inside `user`) with escalating
    retries: whole chunk again with defects named, then each still-bad
    paragraph alone via single_user(para, defect). Returns (title, zh, problems)."""
    text = call(cli, system, user, usage)
    title, zh, problems = parse_paras(text, len(paras), paras)
    if problems:
        hint = ("\n\n<attention>\nA previous attempt at this chunk had these defects. "
                "Avoid them this time:\n- " + "\n- ".join(problems) + "\n</attention>")
        text2 = call(cli, system, user + hint, usage)
        title2, zh2, problems2 = parse_paras(text2, len(paras), paras)
        if len(problems2) <= len(problems):
            title, zh, problems = title2 or title, zh2, problems2
    if problems:
        remaining = []
        by_para = {}
        for pr in problems:
            m = PROB_PARA_RE.match(pr)
            if m:
                by_para.setdefault(int(m.group(1)), []).append(pr)
            else:
                remaining.append(pr)
        for i, prs in by_para.items():
            t3 = call(cli, system, single_user(paras[i - 1], prs), usage)
            _, z3, p3 = parse_paras(t3, 1, [paras[i - 1]])
            if not p3 and z3[0]:
                zh[i - 1] = z3[0]
            else:
                remaining += prs
        problems = remaining
    return title, zh, problems


def translate_chunk(cli, system, ch, ci, idxs, done, usage, review_system=None):
    prev = prev_context(ch, idxs, done)
    user, paras, title = build_user(ch, idxs, prev)

    def single_user(para, defects):
        return (glossary_for(para) + f"<section>{ch['sec']}</section>\n"
                "<instructions>Translate this single paragraph from the chapter. A previous attempt "
                "had these defects, so take particular care: " + "; ".join(defects) +
                ". Every [^n] marker must appear in the Chinese, right after the phrase it "
                "follows in English.</instructions>\n" + format_paras([para]))

    zh_title, zh, problems_all = robust_translate(cli, system, user, paras, usage, single_user)
    rec = {"chapter": ch["key"], "chunk": ci, "para_idx": idxs, "title": ch["title"],
           "zh_title": zh_title, "en": paras, "zh": zh, "problems": problems_all}
    if review_system:
        review_chunk(cli, review_system, user, paras, rec, usage)
    return rec


def review_chunk(cli, review_system, user, paras, rec, usage):
    """The editing pass over an existing draft, in place on `rec`. A reply that
    does not parse is asked for once more; if it still fails, the draft stands
    and the failure is recorded in review_problems."""
    ruser = user + "\n\n<draft>\n" + format_paras(rec["zh"], rec.get("zh_title")) + "\n</draft>"
    for _ in range(2):
        rtext = call(cli, review_system, ruser, usage)
        r_title, r_zh, r_problems = parse_paras(rtext, len(paras), paras)
        if not r_problems:
            m = re.search(r"<changes>(\d+)</changes>", rtext)
            rec["zh_reviewed"] = r_zh
            rec["zh_title_reviewed"] = r_title or rec.get("zh_title")
            rec["review_changes"] = int(m.group(1)) if m else None
            rec.pop("review_problems", None)
            return
    rec["review_problems"] = r_problems


def run_chapter(cli, system, ch, usage, prog, review_system, redo):
    done = {}
    chunks = chunk_chapter(ch)
    for ci, idxs in enumerate(chunks):
        path = TR_DIR / f"{ch['key']}-{ci:02d}.json"
        if path.exists() and not redo:
            rec = json.loads(path.read_text())
            if review_system and "zh_reviewed" not in rec:
                # review the draft that is there; never re-translate it, which
                # would throw away a draft that may have been read or hand-edited
                t0 = time.time()
                user, paras, _ = build_user(ch, idxs, prev_context(ch, idxs, done))
                review_chunk(cli, review_system, user, paras, rec, usage)
                path.write_text(json.dumps(rec, ensure_ascii=False, indent=1))
                with lock:
                    prog.tick(f"ch {ch['idx']} chunk {ci + 1}/{len(chunks)} reviewed "
                              f"{int(time.time() - t0)}s  {usage}")
            for i, z in zip(rec["para_idx"], rec["zh"]):
                done[i] = z
            continue
        t0 = time.time()
        rec = translate_chunk(cli, system, ch, ci, idxs, done, usage, review_system)
        path.write_text(json.dumps(rec, ensure_ascii=False, indent=1))
        for i, z in zip(idxs, rec["zh"]):
            done[i] = z
        if rec["problems"]:
            with lock, open(PROBLEMS, "a") as f:
                f.write(f"{path.name}: {'; '.join(rec['problems'])}\n")
        with lock:
            prog.tick(f"ch {ch['idx']} chunk {ci + 1}/{len(chunks)} "
                      f"{int(time.time() - t0)}s  {usage}")


def run_notes(cli, system, book, usage, batch, redo, review_system):
    """Endnotes are short and context-free; translate them in numbered batches."""
    NOTE_HINT = ("\n\n<instructions>These are the translator's endnotes, given as paragraphs. "
                 "Same rules; there is no surrounding narrative. Keep the [^n] marker at the start "
                 "of each note exactly as given.</instructions>\n")
    jobs = []
    for seq, notes in book["notes"].items():
        keys = sorted(notes, key=int)
        for bi in range(0, len(keys), batch):
            jobs.append((seq, bi // batch, keys[bi: bi + batch]))
    prog = Progress(len(jobs), "note batches")
    for seq, bi, keys in jobs:
        path = TR_DIR / f"notes-{seq}-{bi:02d}.json"
        paras = [f"[^{k}] {book['notes'][seq][k]}" for k in keys]
        user = glossary_for(" ".join(paras)) + "<section>Endnotes</section>" + NOTE_HINT + format_paras(paras)
        if path.exists() and not redo:
            rec = json.loads(path.read_text())
            if review_system and "zh_reviewed" not in rec:
                review_chunk(cli, review_system, user, paras, rec, usage)
                path.write_text(json.dumps(rec, ensure_ascii=False, indent=1))
                prog.tick(f"notes {seq} batch {bi} reviewed  {usage}")
            else:
                prog.tick(f"notes {seq} {bi} cached")
            continue

        def single_user(para, defects):
            return (glossary_for(para) + "<section>Endnotes</section>" + NOTE_HINT +
                    "<instructions>A previous attempt had these defects: " + "; ".join(defects) +
                    "</instructions>\n" + format_paras([para]))

        _, zh, problems = robust_translate(cli, system, user, paras, usage, single_user)
        rec = {"notes": seq, "batch": bi, "keys": keys, "en": paras, "zh": zh, "problems": problems}
        if review_system:
            review_chunk(cli, review_system, user, paras, rec, usage)
        path.write_text(json.dumps(rec, ensure_ascii=False, indent=1))
        if problems:
            with open(PROBLEMS, "a") as f:
                f.write(f"{path.name}: {'; '.join(problems)}\n")
        prog.tick(f"notes {seq} batch {bi}  {usage}")


def inherited_titles(bk):
    """Headings an earlier volume of the same work already decided.

    A volume whose sections are new still shares "Introduction", the
    author's or translator's name and the way a part is titled with the volumes
    before it, and a heading is too short for the glossary to steady on its
    own: asked cold, one volume transliterated a translator's name differently
    from the volume before. So the earlier volumes' decisions are kept as they are, and shown to
    the model as the pattern for the headings that are genuinely new."""
    t = {}
    for b in reversed(bk.lineage()[1:]):
        if b.titles_json.exists():
            t.update(json.loads(b.titles_json.read_text()))
    return t


def run_titles(cli, system, book, usage, bk):
    """Book title and section titles: one small call, saved to work/titles.json."""
    names = [book["title"]] + [sec["title"] for sec in book["sections"]]
    known = inherited_titles(bk)
    ask = [n for n in names if n not in known]
    done = {n: known[n] for n in names if n in known}
    if ask:
        shown = "\n".join(f"{k} -> {v}" for k, v in known.items()) if known else ""
        user = (glossary_for(" ".join(ask)) +
                ((f"<decided>Earlier volumes of this work render these headings as "
                  f"follows. Follow the same pattern.\n{shown}</decided>\n") if shown else "") +
                "<instructions>These are the book title and the section titles of the book. "
                "Translate each as a heading (no romanized parentheses). For the book title use 《》; "
                "render any author or translator name as the glossary gives it, else transliterate it."
                "</instructions>\n" + format_paras(ask))
        text = call(cli, system, user, usage)
        _, zh, problems = parse_paras(text, len(ask), ask)
        if problems:
            raise RuntimeError(f"title translation failed: {problems}")
        done.update(zip(ask, zh))
    TITLES.write_text(json.dumps({n: done[n] for n in names}, ensure_ascii=False, indent=1))
    print("titles:", " / ".join(done[n] for n in names))


def rewrite_problems(failed):
    """problems.txt reflects the current state of the chunk files, not history."""
    lines = []
    for path in sorted(TR_DIR.glob("*.json")):
        rec = json.loads(path.read_text())
        # re-validate with the current rules (hand edits, rule changes)
        _, _, rec["problems"] = parse_paras(format_paras(rec["zh"]), len(rec["en"]), rec["en"])
        path.write_text(json.dumps(rec, ensure_ascii=False, indent=1))
        if rec.get("problems"):
            lines.append(f"{path.name}: {'; '.join(rec['problems'])}")
        if rec.get("review_problems"):
            lines.append(f"{path.name} (review): {'; '.join(rec['review_problems'])}")
    lines += failed
    if lines:
        PROBLEMS.write_text("\n".join(lines) + "\n")
    elif PROBLEMS.exists():
        PROBLEMS.unlink()
    return len(lines)


def main():
    ap = argparse.ArgumentParser()
    add_book_arg(ap)
    ap.add_argument("--chapters")
    ap.add_argument("--sections")
    ap.add_argument("--review", action="store_true")
    ap.add_argument("--notes", action="store_true")
    ap.add_argument("--titles", action="store_true")
    ap.add_argument("--notes-batch", type=int, default=50)
    ap.add_argument("--redo", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    global TR_DIR, PROBLEMS, TITLES
    bk = pick_book(a.book)
    TR_DIR = bk.translated
    PROBLEMS = bk.problems
    TITLES = bk.titles_json
    if not bk.book_json.exists():
        sys.exit(f"{bk.book_json} missing - run extract.py --book {bk.slug} first")
    book = json.load(open(bk.book_json))
    chapters = flat_chapters(book)
    if a.list:
        for ch in chapters:
            w = sum(len(p.split()) for p in ch["paragraphs"])
            print(f"{ch['idx']:3d}  {ch['sec'][:22]:22s}  {ch['title'][:60]:60s} {w:5d}w "
                  f"{len(chunk_chapter(ch))} chunks")
        return
    sel = parse_spec(a.chapters, len(chapters))
    if a.sections:
        sel = {ch["idx"] for ch in chapters if a.sections.lower() in ch["sec"].lower()} & sel
    todo = [ch for ch in chapters if ch["idx"] in sel]
    n_chunks = sum(len(chunk_chapter(ch)) for ch in todo)
    n_words = sum(len(p.split()) for ch in todo for p in ch["paragraphs"])
    global GLOSSARY
    GLOSSARY = glossary = load_glossary(bk)
    if not glossary:
        print(f"warning: {bk.glossary_path} missing or empty - run glossary.py first",
              file=sys.stderr)
    full = "\n\n# Glossary\n\n" + glossary_block(glossary) if GLOSSARY_MODE == "full" else ""
    system = prompt("system", bk).replace("{BOOK}", bk.style()) + full
    review_system = prompt("review", bk).replace("{STYLE}", system) + full if a.review else None
    sys_tokens = len(system) // 4
    print(f"{bk}: {len(todo)} chapters, {n_chunks} chunks, {n_words:,} words; model {MODEL}, "
          f"system prompt ~{sys_tokens:,} tokens ({len(glossary)} glossary entries)")
    if a.dry_run:
        in_tok = n_words * 1.4 + n_chunks * 800
        out_tok = n_words * 2.2  # Chinese output + thinking, rough
        cached = n_chunks * sys_tokens
        mult = 2 if a.review else 1
        from common import PRICE
        p = PRICE.get(MODEL)
        if p:
            cost = (in_tok * p[0] + out_tok * p[1] + cached * p[2]) / 1e6 * mult
            print(f"estimate: ~{int(in_tok):,} input, ~{int(out_tok):,} output, "
                  f"~{int(cached):,} cached tokens -> about ${cost:.0f}"
                  + (" including review" if a.review else ""))
        for ch in todo[:5]:
            print(f"  ch {ch['idx']} {ch['title'][:50]}: {[len(c) for c in chunk_chapter(ch)]} paras/chunk")
        return

    TR_DIR.mkdir(parents=True, exist_ok=True)
    cli = client()
    usage = Usage(MODEL)
    if a.titles or not TITLES.exists():
        run_titles(cli, system, book, usage, bk)
        if a.titles:
            return
    if a.notes:
        run_notes(cli, system, book, usage, a.notes_batch, a.redo, review_system)
        print(f"done  {usage}")
        n = rewrite_problems([])
        if n:
            print(f"{n} flagged chunks listed in {PROBLEMS}")
        return
    failed = []
    prog = Progress(n_chunks)
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
        futs = {ex.submit(run_chapter, cli, system, ch, usage, prog, review_system, a.redo): ch
                for ch in todo}
        for f in as_completed(futs):
            ch = futs[f]
            try:
                f.result()
            except Exception as e:  # keep other chapters going
                print(f"chapter {ch['idx']} failed: {e!r}", file=sys.stderr)
                failed.append(f"chapter {ch['idx']} FAILED (rerun it): {e!r}")
    print(f"done in {int(time.time() - t0)}s  {usage}")
    n = rewrite_problems(failed)
    if n:
        print(f"{n} flagged chunks listed in {PROBLEMS}")


if __name__ == "__main__":
    main()
