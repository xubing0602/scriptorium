"""Generate the two sample books used to test the translate-book-zh skill.

  lamplighter.pdf   text layer: title page, contents, 3 chapters, endnotes
  ferryman_scan.pdf image-only scan: 2 chapters, footnotes under a printed rule

All text is original, written for these samples.

Arguments:
  --out DIR   Where to write the PDFs (default: this script's directory).
"""
import argparse
import io
import random
from pathlib import Path

import fitz
from PIL import Image, ImageFilter

CSS = """
* {font-family: serif;}
p {font-size: 11pt; line-height: 1.45; text-indent: 16pt; margin: 0; text-align: justify;}
p.first {text-indent: 0;}
h1 {font-size: 20pt; font-weight: bold; text-align: center; margin: 40pt 0 4pt 0;}
h2 {font-size: 14pt; font-style: italic; font-weight: normal; text-align: center; margin: 0 0 24pt 0;}
h3 {font-size: 16pt; font-weight: bold; text-align: center; margin: 10pt 0 16pt 0;}
.note {font-size: 9.5pt; text-indent: 0; margin-bottom: 4pt;}
sup {font-size: 7pt;}
"""

CH1 = """
<h1>Chapter One</h1><h2>The Harbour at Dusk</h2>
<p class="first">Every evening at a quarter past six, Tobias Wren climbed the iron stair of the old customs house and lit the first lamp on the quay. He had done it for thirty-one years, and in all that time, he told me, he had missed only four evenings: two for fever, one for his daughter's wedding, and one for a storm so violent that the harbourmaster forbade anyone to go near the water.<sup>1</sup></p>
<p>The town of Port Kessel sits at the mouth of a slow brown river, and its harbour is shaped like a hand half closed. Fishing boats come in at dusk with their lamps already burning, and from the hill above the cathedral you can watch them thread the narrow channel one by one, like beads drawn along a string. Tobias called this hour <i>the turning</i>, a phrase he had from his grandfather, who had kept the lamps before him.</p>
<p>“You must understand,” he said to me on my first evening, “that a lamp is not for the man who lights it. It is for the man who is still out there.” He pointed with his chin toward the grey line where the sea met the sky. “If I forget that, I might as well stay home.”</p>
<p>I had come to Port Kessel to write a short account of its fisheries for a society in the capital. I stayed eleven months. The account was never finished, but I filled four notebooks with what the lamplighter told me, and this book is drawn from them.<sup>2</sup></p>
<p>His house stood at the end of Rope Street, a narrow building painted the pale green that the town favours for doors and shutters. Inside there was a single long room, a stove, a shelf of almanacs going back to 1871, and a clock that ran eleven minutes fast. Tobias would not correct it. “It keeps me early,” he said, “and early is the only safe time to be.”</p>
<p>On the wall above the stove hung a framed page from an old Portuguese hymnal, which a sailor had given him in exchange for a winter's lodging. He could not read a word of it, but he liked the shape of the letters, and he said the sailor had sung it every night in a voice full of <i>saudade</i>, a longing, as the sailor explained it, for something that may never have existed.</p>
<p>That first winter I learned the round of his work: the lamps on the quay, the lamp at the end of the breakwater, the two lamps on the bridge, and the small red lamp above the door of the pilot's office, which was lit only when the bar was too dangerous to cross. Thirty-six lamps in all, and he knew each one by the sound its door made when it opened.</p>
"""

CH2 = """
<h1>Chapter Two</h1><h2>The Almanacs</h2>
<p class="first">The almanacs were the first thing in the house I was allowed to touch, and the last thing Tobias ever talked about freely. There were one hundred and fifty-three of them, bound in card, each recording the tides, the hours of sunrise and sunset, the phases of the moon, and, in the margins, in four different hands, the weather as it had actually been.<sup>3</sup></p>
<p>The oldest hand was his great-grandfather's, small and slanted and full of abbreviations. Then came his grandfather, who wrote in capitals; then his father, who wrote almost nothing; and then Tobias himself, who wrote a great deal, much of it having nothing to do with weather. Beside the entry for the ninth of March, 1952, he had written: <i>Fog. Mother died at four. Lit the lamps at six as usual.</i></p>
<p>I asked him once why he kept writing in them, since the harbour board had published proper records for decades. He considered the question for a long time.</p>
<p>“My grandfather used to say,” he answered at last, “‘A book that nobody writes in is a dead book.’ I don't know if that's true. But I didn't want to be the one to find out.”</p>
<p>The almanacs also held other things: pressed flowers, a ferry ticket from 1923, a recipe for a cough syrup made with honey and pine resin, and a letter from a man named Absalom Pryce, who had been a lighthouse keeper on the island of Skerra and who seems to have corresponded with Tobias's grandfather for twenty years.<sup>4</sup> The letters were mostly about weather, and occasionally about God, and once, memorably, about a seal that had learned to open the lighthouse door.</p>
<p>When the harbour board installed electric lamps on the quay in 1979, Tobias was offered a pension. He refused it. The board, after some debate, agreed that he might continue to light the four oil lamps on the bridge, which were considered historic, and he did so until the winter I left.</p>
"""

CH3 = """
<h1>Chapter Three</h1><h2>The Last Lamp</h2>
<p class="first">The red lamp above the pilot's office was the one he cared for most. It was lit perhaps a dozen times a year, and each time it meant that some boat would have to wait outside the bar until morning, rocking in the swell with its crew awake and cold. “It's the only lamp that says no,” Tobias told me. “All the others say come home. That one says not yet.”</p>
<p>In February a gale came in from the north-west and the red lamp burned for three nights. On the second night a trawler called the <i>Margaret Ellen</i> tried the bar anyway. She was lost with two of her five men.<sup>5</sup> Tobias did not speak for a week afterward. When he did, it was to say that the lamp had done its work and the men had not listened, and that both of those things would go on being true for as long as there was a harbour.</p>
<p>I left Port Kessel in the spring. On my last evening he let me light the lamps on the bridge myself, standing beside me and correcting the angle of my match. When the fourth lamp caught he nodded once and said, “There. Now somebody out there knows where you are.”</p>
<p>I have not been back. I am told the bridge lamps are electric now, and that the house on Rope Street has been sold. But I keep, on the shelf above my own desk, the almanac for the year I spent there, which he gave me on the morning I left. In the margin beside the date of my departure, in capitals that I think he copied from his grandfather, he had written a single word: <i>CLEAR.</i><sup>6</sup></p>
"""

NOTES = """
<h3>Notes</h3>
<p class="note">1. The storm of November 1953, which also destroyed the eastern half of the breakwater.</p>
<p class="note">2. The notebooks are now held by the Kessel Town Library, which kindly allowed me to consult them again while preparing this edition.</p>
<p class="note">3. Twenty-one volumes are missing, most of them from the 1890s; Tobias believed they had been used to light the stove during a hard winter.</p>
<p class="note">4. Skerra lies some forty miles to the north. Its light was automated in 1961 and the keeper's house is now a bird observatory.</p>
<p class="note">5. Their names are recorded on the memorial stone beside the cathedral: Daniel Hesketh and Owen Marr.</p>
<p class="note">6. In the almanacs, CLEAR is the word used for a night on which every lamp could be seen from the end of the breakwater.</p>
"""

FRONT = """
<h1 style="margin-top:160pt;font-size:26pt">The Lamplighter's Almanac</h1>
<h2>Edwin Harrow</h2>
<p class="first" style="text-align:center;margin-top:220pt">Greyfield Press · 1998</p>
"""

CONTENTS = """
<h3>Contents</h3>
<p class="first">Chapter One: The Harbour at Dusk</p>
<p class="first">Chapter Two: The Almanacs</p>
<p class="first">Chapter Three: The Last Lamp</p>
<p class="first">Notes</p>
"""


def story_pages(writer, html, page_rect, margin, header=None, first_no=None):
    """Lay `html` out over as many pages as it needs; returns pages written."""
    story = fitz.Story(html=html, user_css=CSS)
    where = page_rect + (margin, margin + 14, -margin, -margin - 10)
    more, n = 1, 0
    while more:
        dev = writer.begin_page(page_rect)
        more, _ = story.place(where)
        story.draw(dev)
        writer.end_page()
        n += 1
    return n


def make_text_pdf(out):
    rect = fitz.paper_rect("a5")
    tmp = io.BytesIO()
    w = fitz.DocumentWriter(tmp)
    for html in (FRONT, CONTENTS, CH1, CH2, CH3, NOTES):
        story_pages(w, html, rect, 46)
    w.close()
    doc = fitz.open("pdf", tmp.getvalue())
    for i, page in enumerate(doc):
        if i >= 2:   # running head and page number, as a real book has
            page.insert_text((46, 34), "THE LAMPLIGHTER'S ALMANAC", fontname="tiro", fontsize=7.5)
            page.insert_text((rect.width / 2 - 4, rect.height - 24), str(i + 1),
                             fontname="tiro", fontsize=9)
    doc.save(out)
    print(f"{out}: {doc.page_count} pages")


# ---- the scanned book: footnotes at the foot of each page, under a rule ----

SCAN_PAGES = [
    ("""<h1>CHAPTER ONE</h1><h2>The Crossing</h2>
<p class="first">The ferry at Holm Ness runs twice a day in summer and once in winter, and for forty years it was run by a woman named Agnes Thorne.<sup>1</sup> She was the third of her family to hold the licence, and she held it longer than anyone before her.</p>
<p>The crossing takes twenty minutes when the tide is slack and nearly an hour when it is not. Agnes knew the water so well that she could tell the state of the tide from the colour of the weed on the landing stage. “Green going brown,” she would say, “we'll be late,” and we always were.</p>
<p>She kept a ledger of every passenger she ever carried. It ran to nine volumes. The entries are short: a name, a date, a fare, and now and then a remark such as <i>sang the whole way</i> or <i>would not pay; let him off</i>.<sup>2</sup></p>""",
     ["1. The licence was first granted to her grandmother, Ruth Thorne, in 1889.",
      "2. The ledgers are now in the county record office at Bramwell."]),
    ("""<p>In the winter of 1963 the strait froze for the first time in living memory, and for nine days Agnes could not cross. People walked over the ice instead, carrying parcels and children, and she stood on the landing stage and watched them go. She told me it was the worst week of her life. “They didn't need me,” she said. “That's a terrible thing to find out about yourself.”</p>
<p>When the ice broke, she took the ferry out before dawn, alone, and crossed and recrossed the strait six times. Asked afterward why she had done it, she said she wanted the water to remember who it belonged to.<sup>3</sup></p>""",
     ["3. This remark is recorded in the Bramwell Gazette of 2 February 1963, though the reporter attributes it to “a local boatwoman”."]),
    ("""<h1>CHAPTER TWO</h1><h2>The Bridge</h2>
<p class="first">The bridge was first proposed in 1971 and finally opened in 1988. Agnes attended every public meeting held about it in those seventeen years, and spoke at most of them. She was not against the bridge, she said; she was against forgetting.</p>
<p>On the day it opened she made her last crossing, carrying the mayor, two councillors and a brass band, which played the whole way over. In the ledger, under the final entry, she wrote a single line: <i>Fare paid in full.</i><sup>4</sup></p>""",
     ["4. The ferry itself was sold to a museum on the mainland, where it can still be seen."]),
]


def make_scan_pdf(out, dpi=200, seed=7):
    rng = random.Random(seed)
    rect = fitz.paper_rect("a5")
    src = fitz.open()
    for i, (body, notes) in enumerate(SCAN_PAGES):
        page = src.new_page(width=rect.width, height=rect.height)
        page.insert_text((46, 34), "THE FERRYMAN'S DAUGHTER", fontname="tiro", fontsize=7.5)
        page.insert_text((rect.width / 2 - 4, rect.height - 24), str(i + 9), fontname="tiro",
                         fontsize=9)
        note_h = 14 * len(notes) + 10 * sum(len(n) > 80 for n in notes) + 14
        foot_top = rect.height - 46 - note_h
        spare, scale = page.insert_htmlbox(fitz.Rect(46, 52, rect.width - 46, foot_top - 8),
                                           body, css=CSS, scale_low=1)
        assert spare >= 0, f"page {i + 1} body overflows"
        page.draw_line((46, foot_top), (150, foot_top), width=0.6)
        nh = "".join(f'<p class="note">{n}</p>' for n in notes)
        page.insert_htmlbox(fitz.Rect(46, foot_top + 5, rect.width - 46, rect.height - 34),
                            nh, css=CSS, scale_low=1)
    images = []
    for page in src:
        pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
        img = Image.frombytes("L", (pix.width, pix.height), pix.samples)
        img = img.rotate(rng.uniform(-0.4, 0.4), fillcolor=255, resample=Image.BICUBIC)
        img = img.filter(ImageFilter.GaussianBlur(0.5))
        px = img.load()
        for _ in range(img.width * img.height // 400):          # paper speckle
            px[rng.randrange(img.width), rng.randrange(img.height)] = rng.randrange(150, 230)
        images.append(img)
    out_doc = fitz.open()
    for img in images:
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=70)
        p = out_doc.new_page(width=rect.width, height=rect.height)
        p.insert_image(p.rect, stream=buf.getvalue())
    out_doc.save(out)
    print(f"{out}: {out_doc.page_count} image-only pages")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(__file__).parent))
    a = ap.parse_args()
    d = Path(a.out)
    make_text_pdf(d / "lamplighter.pdf")
    make_scan_pdf(d / "ferryman_scan.pdf")
