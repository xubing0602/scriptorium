# config.json for a scanned PDF

A scan carries no text and no fonts. `ocr.py` reads each page with Apple Vision (macOS only, local, free; through `ocrmac`) into line boxes; `extract_scan.py` rebuilds structure from **geometry and wording**: where a line sits, how far it is indented, how dense its type is, whether it matches a heading pattern.

## Order of work

1. Write a first config with just `title`, `pdf`, `"source": "scan"` and one section spanning the whole PDF.
2. OCR a sample first — a chapter opening, a page with footnotes, a page from each distinct part:
   `python <skill>/scripts/ocr.py --book SLUG --pages 20-30`
3. Read `work/<slug>/ocr.txt` (all text, one block per page) and look at a page image or two (render with pymupdf: `fitz.open(pdf)[n].get_pixmap(dpi=100).save("p.png")`, then view it) to see how headings, paragraphs and footnotes look.
4. Fill in `sections`, the heading patterns and the footnote settings; run `extract.py` and `check.py` on the sample. Iterate until check.py is clean.
5. OCR the whole book (minutes to an hour; it resumes), extract, check again.

## Keys

| key | meaning |
|---|---|
| `sections` | **required.** `[{"title", "pages": [first, last], "note_seq", "columns", "notes", "heading_re"}]`, 1-based PDF pages. One entry per distinct part (preface, introduction, main text, appendix). `columns` 1 or 2; `notes: false` turns footnote detection off (default: on for one-column sections) |
| `ocr_dpi` | resolutions to read each page at; the reading with more printed lines wins (default `[150, 300]`; `[300, 600]` for small type) |
| `chapter_re` | regex for a chapter heading line; group 1 spells the number in words or digits. Default matches `CHAPTER FIFTY-ONE` or a bare `EIGHTY-NINE`. For `Chapter 12`: `"^Chapter\\s+(\\d{1,3})\\.?$"`. The lines under the heading, until the first body line, become the chapter title |
| `heading_re` | regex for an all-capitals heading in a prose section (`"^[IVX]+\\.\\s+\\S"` for `IV. THE …`); may be set per section. The title runs on while lines stay in capitals |
| `title_center_min` | chapter titles are centred: the title ends at the first row starting within this fraction of the left margin |
| `chapter_end_re` | for books that **close** chapters with a colophon (“Thus ends the fifth chapter…”) instead of opening them with a heading: the matching line ends the chapter and gives its number (ordinal read fuzzily, trailing numeral as fallback) |
| `subworks` | `[{"title", "match", "pages"}]` — an embedded work that numbers its chapters from 1 again; identified by page range first, since the scanner mangles names |
| `verse_re` | a line opening with a verse/section number starts a paragraph (default `1.`, `4-5.`, `41cd-47.`); `""` turns it off — do that for ordinary prose, where a line starting with `1.` is a list item mid-paragraph |
| `speaker_re` | a speaker heading line (`The bard said:`) becomes its own `*italic*` paragraph; default `^.{2,45}\ssaid:$`; `""` off |
| `indent_min` | first-line indent, fraction of page width, that starts a paragraph (default 0.02) |
| `margin_pct` | take each column's margin from this percentile of line starts, not the leftmost line (default 0) — set ~0.1 when stray lines poke past the margin and every line becomes a paragraph |
| `head_max` | top fraction of the page that is running head (default 0.095) |
| `foot_min` | rows below this fraction that are only a number are page numbers (default off) |
| `drop_re` | lines matching this are dropped (running heads that sit inside the text block) |
| `min_wordlike` | drop rows where fewer than this share of words look English (default 0); ~0.55 for a scan that also prints another script |
| `rule_y_min` | where ocr.py starts looking for the printed rule above footnotes (default 0.45) |
| `note_y_min` | footnotes cannot start above this (default 0.78) |
| `note_cw` | when no rule is printed: how much denser (characters per width) the footnote type is than the body (default 1.08) |
| `note_start_re` | how the first footnote line begins (default a number and a dot) |
| `note_share` | share of the footnote block that must be in the smaller type (default 0.7) |
| `notes_per_page` | `true` when footnotes restart at 1 on every page |
| `column_split`, `ocr_crops`, `span_re` | two-column books, below |
| `row_tol` | lines within this vertical distance are one printed line (default 0.008) |

## What was learned the hard way

**Vision merges lines.** Apple Vision sometimes runs two printed lines into one and silently loses the overlap — at one resolution but not another. That is why every page is read at two dpi values and the reading with more printed lines wins. Keep two values in `ocr_dpi`.

**Footnotes cannot be told from body text by font metrics alone.** The reliable signal is the printed rule above them: ocr.py looks for a long unbroken dark run in the page image and stores it as `"rule"`. Not every book prints one. The fallback measures body type at the top of the page (characters per unit line width) and takes as footnotes the first block that sits low (`note_y_min`), opens with a printed note number (`note_start_re`) and is mostly smaller type (`note_cw`, `note_share`). Comparing against the median of the block finds a split far too high, because real notes drag the median down.

**Superscript note numbers never survive OCR.** Each footnote's digits are matched back into the page text (exact digits, then partial, then a superscript-shaped artifact like `®`, `*`, `°`), and failing all three, appended to the page's last paragraph. `work/<slug>/notes_report.txt` lists how each was placed; many `appended` means the matching is failing.

**Footnotes restarting on each page** need `"notes_per_page": true`: each note keeps a running id, while the digits searched for in the body stay the ones printed on the page.

**Two-column pages: read each column from its own crop.** Letting Vision read the whole page merged a line of one column into the other hundreds of times; cropping cut that by 70%+. Set:

```json
"sections": [{"title": "…", "pages": [53, 601], "columns": 2, "notes": false}],
"column_split": 0.5,
"ocr_crops": [[0, 0, 0.52, 1], [0.48, 0, 1, 1]]
```

A full-page pass is still kept for lines genuinely printed across the gutter (chapter colophons, headings); `span_re` (default: `chapter_end_re`) says which crossing lines are real. The page is cut into bands at those spanning lines, and each column's paragraph indent is measured after the footnote split — footnotes are set to a wider measure.

**Colophon numbering.** A lost colophon merges two chapters; numbering from the colophon's own words (fuzzy ordinal, then trailing numeral) keeps one loss from shifting every later number.

**A scan carrying a second script** (Devanagari, Greek, Chinese alongside English) produces rows of rubbish where the recognizer tried to read it as Latin. `min_wordlike` drops them. Apple Vision cannot read Devanagari at all; a PDF that is *only* in another script needs a different OCR engine and is out of scope.

**Check the result by reading it.** Two bugs were found only by reading the finished Chinese: a column margin taken from its leftmost line made one stray line turn every line on a page into its own paragraph (fix: `margin_pct`), and a second script arriving as paragraphs (fix: `min_wordlike`). Fixing both took one book from 42% to 26% of paragraphs ending mid-sentence. check.py reports that share — compare it before and after each config change.

**Text lost at the page edges.** `head_max` drops every row above it as running head; on a tightly trimmed scan the first body line of a page (one without a chapter heading) can sit inside that band and vanish without a trace. check.py compares OCR'd words with what reached book.json: 1.5–3% lost is running heads and page numbers; more is real text. Compare the first lines of a few pages in ocr.txt with book.json and lower `head_max` (or raise `foot_min`).

## Examples

One column, footnotes restarting per page, colophon-free, speaker lines:

```json
{
  "title": "The Skanda Purana, Part I (Motilal Banarsidass)",
  "pdf": "pdf/skanda-1.pdf",
  "source": "scan",
  "ocr_dpi": [150, 300],
  "notes_per_page": true,
  "sections": [{"title": "Book I, Section I", "pages": [5, 310], "note_seq": "main"}],
  "head_max": 0.09,
  "note_y_min": 0.45,
  "note_cw": 1.07,
  "speaker_re": "^.{2,45}\\s+said\\s*:$",
  "title_center_min": 0.08,
  "drop_re": "^(SKANDA\\s+PUR|B[oO]{2}K\\s+I\\s*:)"
}
```

Plain modern prose, numbered chapters:

```json
{
  "title": "A Scanned Novel",
  "pdf": "pdf/novel.pdf",
  "source": "scan",
  "sections": [{"title": "Text", "pages": [9, 340]}],
  "chapter_re": "^(?:CHAPTER|Chapter)\\s+([A-Z][A-Za-z\\s\\-]{2,30}|\\d{1,3})\\.?$",
  "verse_re": "",
  "speaker_re": "",
  "foot_min": 0.93
}
```
