---
name: book-translation
description: Translate a whole English book (PDF, scanned or with a text layer, hundreds of pages) into a publishable Simplified Chinese edition with an LLM pipeline — OCR for scans, structure extraction, a per-book style prompt, a glossary that keeps names consistent across chapters and volumes, automatic retries for dropped or truncated paragraphs, an optional review pass, linked footnotes, and HTML/PDF/DOCX/Markdown output, at about $1 per book on DeepSeek. Use this whenever the user wants to translate a book, a long PDF, a novel, a scholarly monograph, a scripture or classic, or a multi-volume work from English into Chinese (英译中, 翻译整本书, 把这本书翻成中文), or wants to add a new book or volume to an existing book-translation project, continue or fix one, rebuild its output, or check its quality — even if they only say "translate this PDF" and it is book-length. Not for short passages, single articles or translating into other languages.
---

# Book translation (English PDF → Chinese edition)

The pipeline lives in `scripts/` next to this file. Books live in a **project directory** the user chooses; every script is run *from that directory* and works on `books/`, `pdf/`, `work/`, `out/` and `.env` inside it:

```
<project>/
  .env                        provider, model, key (the user fills in the key)
  pdf/                        source PDFs
  books/<slug>/config.json    how to read this PDF          (you write it)
  books/<slug>/style.md       what this book is, how to render it  (you write it, user reviews)
  books/<slug>/glossary.json  name/term renderings          (generated, user reviews)
  work/<slug>/                book.json, ocr/, translated/ chunks, problems.txt
  out/<slug>.zh.{html,pdf,docx,md}
```

`S=<this skill's directory>/scripts`. Every script documents all of its arguments in its docstring (`python $S/x.py --help` or read the top of the file); read it before using an option not shown here.

## Why it is built this way

Book-length translation fails in predictable ways, and each stage exists to stop one of them:

- **Inconsistent names across chapters** translated in parallel → a glossary fixed *before* translation, sent with each chunk.
- **A generic register** → `style.md`, a description of this particular book with a worked input/output example, spliced into every prompt. The translator copies the example's shape more faithfully than any rule.
- **Silent omissions** (the model drops a sentence or a footnote marker in a long paragraph) → every chunk is validated: paragraph count, every `[^n]` marker, and Chinese length against English length. A failing chunk is retried with the defects named, then failing paragraphs one at a time; what still fails is listed in `problems.txt`.
- **Broken structure from the PDF** (paragraphs split at page breaks, footnotes inside the text, headings missed) → extraction is checked with numbers (`check.py`) before any money is spent.
- **Wasted money** → one chapter first, read by the user, before the whole book.

## Workflow

Work through these stages in order. Pause for the user at the marked checkpoints; translation choices are theirs.

### 0. Project setup (once per project)

If the user has no project directory yet, create one (ask where if unclear) with `books/ pdf/` and copy `assets/env.example` to `.env`. Then make sure the dependencies are there — in the Python environment the user already works in, or a fresh one in the project:

```bash
python3 -m venv .venv && source .venv/bin/activate      # Python 3.9+
pip install -r $S/requirements.txt
python -m playwright install chromium                   # PDF output (optional)
brew install pandoc                                     # DOCX output (optional; apt/choco elsewhere)
```

Scanned PDFs need macOS: OCR uses Apple Vision through `ocrmac`, which is local and free. On Linux or Windows, PDFs with a text layer work fully; for a scan, tell the user OCR needs a Mac. Missing PDF or DOCX tools only skip those formats — HTML and Markdown are always built.

**API keys:** never ask for, type, or echo a key. Tell the user to open `.env` and paste their key themselves. Default provider is DeepSeek (`MODEL=deepseek-flash`, cheapest, ~$1 for a 200k-word book; half price 16:30–00:30 UTC); Claude is supported (`PROVIDER=anthropic`) at much higher cost and somewhat higher quality.

### 1. Inspect the PDF

Put the PDF in `pdf/`, choose a short slug (`middlemarch`, `shiva-purana-2`), make `books/<slug>/`, then:

```bash
python $S/inspect_pdf.py pdf/<file>.pdf
```

It says TEXT or SCAN, and for text PDFs lists the type sizes (body, headings, note markers) and the large-type lines with page numbers. If this is a later volume of a book already in the project, it can `inherit` from it — see config-text.md.

### 2. Write config.json and extract

- Text layer → read `references/config-text.md`.
- Scan → read `references/config-scan.md`. OCR a sample of pages first (`ocr.py --pages`), never the whole book before the config works.

```bash
python $S/extract.py --book <slug>        # -> work/<slug>/book.json
python $S/check.py --book <slug>          # numbers: chapters, mid-sentence breaks, notes
python $S/translate.py --book <slug> --list
```

Iterate on the config until `check.py` is clean or its remaining findings are understood: no unresolved note markers, a plausible chapter list, mid-sentence paragraph endings low (near 0–10% for a clean text PDF; scans of verse texts may stay at 20–25%), and for scans, only a few % of OCR'd words lost (running heads, page numbers) — more means real text is being dropped. Also open `book.json` and read a few paragraphs from different chapters — the numbers miss things eyes catch.

### 3. Write style.md  — checkpoint

Read `references/style-guide.md`, then read the book (front matter, a chapter opening, a middle page, notes) and write `books/<slug>/style.md`, including a worked example built from real paragraphs of `book.json`. For Indian classics, start from `references/genres/indic-classics.md`.

Show it to the user and ask them to review it — register, title shape, transliteration tradition, the book's Chinese title. Their corrections here are cheap; after translation they are expensive.

### 4. Glossary  — checkpoint

```bash
python $S/glossary.py --book <slug>       # a few calls, cents
```

If another book in the project shares names but spells them differently (a different edition), first run `seed_glossary.py --from <old> --to <slug>` (prints matches; add `--write`). A volume that `inherit`s gets its parent's glossary automatically and only new names are decided.

Then show the user the glossary: the most frequent entries, every `name`/`epithet` family, and anything with a `note`. Point out choices you are unsure of. They edit `glossary.json` directly (`"skip": true` drops an entry); re-running glossary.py keeps existing entries.

### 5. One chapter first  — checkpoint

```bash
python $S/translate.py --book <slug> --dry-run        # chunk plan + cost estimate
python $S/translate.py --book <slug> --chapters 1     # also translates the titles once
python $S/build.py --book <slug> --no-pdf --no-docx
```

The first run also translates the book and section titles into `work/<slug>/titles.json`; check them (the book title becomes the edition's heading) and edit that file directly if needed. Read the chapter yourself against the English — omissions, names, register, markers, quotation marks — and fix `style.md` or the glossary for anything systematic, then `--redo` the chapter. Show the user a few paragraphs side by side (English / Chinese) plus the cost so far, and get their go-ahead before the full run.

### 6. Full run

```bash
python $S/translate.py --book <slug>             # all chapters; resumable, Ctrl-C safe
python $S/translate.py --book <slug> --notes     # endnotes / footnotes
```

A whole book takes roughly 1–3 hours at `CONCURRENCY=3`. Run it in the background and report progress: the script prints `[done/total] elapsed, ETA` and running cost per chunk. Re-running skips finished chunks, so a crash or interruption costs nothing.

Optional second pass, about double the cost: `--review` sends every chunk with its draft to an editor prompt (omissions, mistranslations, glossary, translationese, minimal edits); `build.py` prefers the reviewed text, `--draft` forces the first draft. Suggest it for books where quality matters more than cost.

### 7. Problems, build, final check

```bash
cat work/<slug>/translated/problems.txt
python $S/build.py --book <slug>
python $S/check.py --book <slug> --output
```

- `problems.txt` lists chunks still flagged after retries. "too short" usually means sentences were dropped: `--chapters N --redo` that chapter. "note markers" mismatches: redo, or fix by hand in the chunk JSON. "FAILED … max_tokens": lower `CHUNK_WORDS` in `.env` (e.g. 800) and rerun that chapter. The file is rewritten on each run to reflect the current chunks.
- `check.py --output` compares note definitions with markers in the built book. A note defined but never referenced is a marker the model dropped; it also lists paragraphs left in English.
- Hand the user the paths in `out/`: the HTML (table of contents, linked notes both ways, light/dark), PDF (with outline), DOCX (real Word footnotes, heading styles), Markdown.

## Notes

- Chapters are translated in chunks of ~`CHUNK_WORDS` (1200) English words, in order within a chapter (each sees the previous chunk's tail for continuity), chapters in parallel.
- Per-book prompt overrides: `books/<slug>/system.md` (or `glossary.md`, `review.md`) replaces the shipped prompt for that book; `<project>/prompts/<name>.md` for the whole project. Prefer putting book-specific guidance in `style.md` and keeping the shipped prompts.
- Edited a glossary entry after translating? Only chunks translated afterwards use it; `--redo` the affected chapters if it matters.
- Prices in `scripts/common.py` (`PRICE`) drive the estimates; update them if the provider's prices changed.
