# Writing books/<slug>/style.md

`style.md` is spliced into the `{BOOK}` slot of all three prompts — translation, glossary and review. The shipped prompts are deliberately general (any English book → Chinese); everything particular to this book goes here. It is where a book's quality is won or lost, so write it from the book itself, not from its title: read the front matter, a chapter opening, a typical middle page, a page of notes, and anything odd (verse, letters, tables, lists, dialogue-heavy passages).

Keep it to roughly 200–600 words plus the worked example. Every sentence should change what the translator does; drop anything that would not.

## What to cover

1. **What the edition is.** Author, translator (if the English is itself a translation), publisher/series, year; what kind of text (novel, history, philosophy, scripture, memoir, technical); how literal or free the English is. One short paragraph.
2. **Register and reference translations.** What a good Chinese edition of this kind of book reads like, ideally named: 杨绛译《堂吉诃德》, 王佐良's essays, 商务印书馆 汉译世界学术名著 for scholarly prose, 黄宝生/季羡林 for Indian classics, 傅雷 for French-flavoured fiction. Say what to avoid (too colloquial, too 文言, news-agency flatness).
3. **Who speaks.** Narrator, frame stories, dialogue conventions, letters or documents quoted at length.
4. **Names.** Which transliteration tradition (新华社标准译名; Buddhist-Indological; established 汉译 of Greek/Latin classics); established Chinese names of main figures; people with many names or epithets that must stay distinct. The book's own title in Chinese if it already has one: `The book's title is 《米德尔马契》.`
5. **Foreign terms and italics.** What the italics mark in this book (foreign terms, titles, emphasis, speaker lines) and whether foreign terms get a romanized form in parentheses on first use (`苦行（tapas）`). If italics are lost (scans), say so, and say how to recognize foreign terms anyway (diacritics).
6. **Structure markers.** Verse or section numbers at paragraph starts and exactly how to render them; chapter-title shape; speaker lines; epigraphs; numbered lists.
7. **Source defects.** For scans: diacritics unreliable, the same name spelled several ways (one name — translate identically), garbled words (translate the obvious intended word silently; when unreadable, translate what is recoverable and never transliterate garbage as if it were a name).
8. **A worked example** — input exactly as the translator will receive it (`<title>`, `<p id>`, `*italic*`, `[^n]`) and the output you want, hand-written to show the register, the title shape, a name, a marker, a quotation. Pick real paragraphs from `work/<slug>/book.json`. The translator copies the example's shape more faithfully than any rule; get it right.

## Template

~~~markdown
<Author>'s <title> (<translator>, <publisher/series>, <year>). <What kind of text it is; how literal the English is; what the notes are.>

<Register: what the Chinese should read like, with a named reference translation; what to avoid.>

<Who narrates; frame; dialogue conventions.>

<Names: transliteration tradition; main figures' established names; multi-named figures that stay distinct.> The book's own title is 《…》.

<Italics and foreign terms: what italics mean here; romanize on first use or not.>

<Structure: verse/section numbers, chapter-title shape, speaker lines.>

<Source defects, for scans.>

Worked example — input:

```
<title>Chapter 5 …</title>
<p id="1">…</p>
<p id="2">…[^12]</p>
```

Output:

```
<title>第五章 …</title>
<p id="1">…</p>
<p id="2">…[^12]</p>
```
~~~

## Genre notes

**Fiction.** Dialogue carries the book: natural spoken Chinese for speech, written Chinese for narration; keep each character's voice (class, age, dialect → register, not invented dialect). Free indirect speech stays ambiguous. Puns and wordplay: translate the effect where possible; never add a footnote.

**Scholarly nonfiction.** Precision over elegance: terms of art fixed in the glossary, argumentative connectives kept (but not 翻译腔), citations and cross-references untouched, quoted titles in 《》 with established Chinese titles. Long sentences still get split.

**Classical, religious and historical texts.** Repetition, formulae and lists are the texture — do not smooth them. A traditional register (庄重、典雅，带古典叙事色彩，但不是文言). Distinct epithets stay distinct. See `references/genres/indic-classics.md` for a fully worked convention set (Indian epics and Puranas) that can be adapted for other traditions.

**Memoir, essays, travel.** First-person voice: 我, short sentences, the author's humour kept dry. Place names: established Chinese name, else 新华社 standard.

## A later volume

A volume that `inherit`s needs no style.md of its own; it uses the parent's, so register and title shape stay identical. Write one only if the new volume genuinely differs (different translator, different structure), and then copy the parent's and change only what differs.
