# config.json for a PDF with a text layer

`extract.py` reads the PDF's own text spans and classifies every block by **type size**: headings are set larger than the body, note numbers smaller. Italic fonts become `*italic*`, note numbers become `[^n]`. So the config is mostly the sizes, read off `inspect_pdf.py`.

## Reading inspect_pdf.py

```
type sizes by characters set (body is probably 15.0):
   30.0    0.0%  LiberationSerif                 p ''                        <- drop cap
   25.0    0.0%  LiberationSerif-Bold            p268 'Sati Khanda (…)'      <- part titles
   21.2    0.5%  LiberationSerif-Bold            p49 '(Greatness of …)'      <- chapter titles
   15.0   99.1%  LiberationSerif, …-Italic       p6 'Bibek Debroy is …'      <- body
   11.2    0.3%  LiberationSerif                 p5 'Sati Khanda …'          <- contents page, ignore
digits-only spans by size: 8.7: 2162, 15.0: 1; 1079 flagged superscript       <- note markers at 8.7

lines set larger than the body:
  p10    25.0  Introduction
  p48    25.0  The Skanda Purana Segment
  p49    21.2  Chapter 1-1(1)
  …
an endnotes heading ('Notes') on page 609
```

A running head or page number that is not dropped ends up glued to the end of a paragraph — the most common cause of "paragraphs ending mid-sentence" in check.py. Use the suggested `head_max` / `foot_min`.

Then `--dump` two or three pages (a chapter opening, a mid-chapter page, a notes page) to confirm: what x-offset an indented first line has (→ `indent_min`), whether note markers are their own spans, whether the body has italics at all.

Thresholds are lower bounds: a span is a chapter heading when its size is `>= chapter_size`, a section heading when `>= section_size`, a drop cap when `>= dropcap_size`. So set each a little below the size observed (21.2 → `21.0`).

## Keys

| key | required | meaning |
|---|---|---|
| `title` | yes | the book's title as printed: `"Middlemarch"`. It is translated once and becomes the heading of the Chinese edition, so leave author, translator and edition out (they belong in style.md) |
| `pdf` | yes | path relative to the project root, e.g. `"pdf/middlemarch.pdf"` |
| `source` | yes | `"text"` |
| `body_pages` | yes | `[first, last]`, 1-based PDF page numbers (not printed page numbers) of the text to translate — from the first real heading (Preface/Introduction/Chapter 1) to the page before the endnotes or back matter |
| `chapter_size` | yes | lower bound of the chapter-heading size |
| `section_size` | no | lower bound of part/book/section headings (default: none) |
| `dropcap_size` | no | lower bound of drop-cap initials, dropped (default: none) |
| `note_size` | no | the size of superscript note numbers |
| `note_superscript` | no | `true`: a digits-only span flagged superscript is a marker, whatever its size |
| `note_digit_spans` | no | `true`: *any* digits-only span is a marker (a conversion that sets superscripts in the body type). Only when page numbers are not in the text layer |
| `notes_pages` | no | `[first, last]` of the endnotes section; omit for a book without endnotes. Each note starts at a marker-sized number span or at a block opening `12.` / `12)` |
| `note_seq_order` | no | names of the numbering sequences in the endnotes, in order. Notes restart at 1 per chapter or per part: one name per restart, e.g. `["intro", "main"]` or `["ch1", "ch2", …]`. Default `["main"]` |
| `note_seq_by_section` | no | `{section title: sequence}` for sections whose notes are not in the default sequence |
| `default_note_seq` | no | default `"main"` |
| `indent_min` | no | points the first line of a block must be indented to start a paragraph (default 8) |
| `para_by_block` | no | `true` when paragraphs are not indented but separated by space: each block is a paragraph unless the previous one ended mid-sentence |
| `head_max` | no | blocks wholly above this fraction of the page height are running heads, dropped (inspect_pdf suggests a value) |
| `foot_min` | no | blocks wholly below this fraction are footers / page numbers, dropped |
| `drop_re` | no | blocks matching this regex are dropped |
| `drop_strings` | no | exact span texts to remove (watermarks: `"OceanofPDF.com"`) |
| `first_section_title` | no | section name for chapters before any section heading (default: the title) |
| `section_starts` | no | `{title: page}` to open a section at a page where no heading is detected (a front-matter Introduction) |
| `chapter_re` | no | when chapters and parts share one size: a heading matching this regex is a chapter, the rest are sections |
| `section_titles` | no | rename a bare half-title: `{"Part One": "Book II: Part One"}` |

A book whose notes are footnotes at the foot of each page (not endnotes) is not handled by the text extractor; if the footnotes matter, treat the book as a scan (`"source": "scan"`, which runs OCR and finds footnotes by layout), or accept losing them.

## Example

```json
{
  "title": "Shiva Purana, Volume 1 (tr. Bibek Debroy)",
  "pdf": "pdf/Shiva-Purana-Vol.1.pdf",
  "source": "text",
  "body_pages": [10, 608],
  "notes_pages": [609, 666],
  "note_size": 8.7,
  "section_size": 25.0,
  "chapter_size": 21.0,
  "dropcap_size": 30.0,
  "indent_min": 8,
  "drop_strings": ["OceanofPDF.com"],
  "first_section_title": "Introduction",
  "section_starts": {"Introduction": 10},
  "note_seq_order": ["intro", "main"],
  "note_seq_by_section": {"Introduction": "intro"}
}
```

## A later volume of the same work

```json
{
  "inherit": "shiva-purana-1",
  "title": "Shiva Purana, Volume 2 (tr. Bibek Debroy)",
  "pdf": "pdf/Shiva Purana Volume 2.pdf",
  "body_pages": [10, 579],
  "notes_pages": [580, 634]
}
```

Everything else — sizes, `style.md`, prompt overrides, glossary, already-decided section titles — comes from the parent. Inspect the new PDF anyway: two volumes of one edition can come from different conversions (one volume had a single body size, no italics, and note numbers in body type; it needed `note_digit_spans`, `chapter_re` and `para_by_block`). Correcting a name in the parent's glossary corrects it in every volume.

## After extract.py

Run `check.py`. On a clean text PDF: 0 unresolved markers, 0 orphan notes, mid-sentence paragraph endings near 0–10%. Look at the chapter list (`translate.py --list`): a chapter of a few words is usually a heading split over two lines or a half-title carrying a footnote; a chapter four times the median is a missed heading.
