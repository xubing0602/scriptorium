# Examples

- **`lamplighter/`** — a finished run of the skill on a short text-layer PDF (3 chapters, 6 endnotes, running heads and page numbers to drop). It is laid out like a real project folder:
  - `pdf/lamplighter.pdf` — the source
  - `books/lamplighter/config.json` — how the PDF is read (sizes, page ranges, header/footer margins)
  - `books/lamplighter/style.md` — the description of the book and its worked example, as Claude wrote it
  - `books/lamplighter/glossary.json` — the names and terms, as generated and then edited
  - `out/` — the Chinese edition in HTML, PDF, DOCX and Markdown
- **`ferryman_scan.pdf`** — a three-page image-only scan with footnotes under a printed rule, a running head and page numbers. A quick way to try the scan path (macOS).
- **`make_samples.py`** — regenerates both PDFs. All text in them was written for these samples.

To translate the scan yourself, open Claude Code in an empty folder and ask it to translate `examples/ferryman_scan.pdf` into Chinese. It costs well under one cent on DeepSeek.
