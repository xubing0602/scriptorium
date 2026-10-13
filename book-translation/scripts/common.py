"""Shared config, client, prompt loading and output parsing for the pipeline.

The scripts live in the skill; the books live in a project directory. Every
path below is relative to that project root: the current working directory,
or BOOKS_ROOT if set. Run the scripts from the project root:

  cd ~/my-translations && python <skill>/scripts/translate.py --book my-book

Configuration is read from `<root>/.env` (see assets/env.example in the skill):
  PROVIDER            anthropic | openai   (openai = any OpenAI-compatible API,
                      e.g. DeepSeek; default anthropic)
  API_KEY             the key for that provider
  BASE_URL            optional for anthropic; required for openai-compatible
                      (DeepSeek: https://api.deepseek.com)
  MODEL               e.g. claude-opus-5, deepseek-flash
  THINKING            on | off   (default on). anthropic: adaptive thinking;
                      openai: sends DeepSeek's thinking.type disabled when off
  EFFORT              anthropic only: low | medium | high | xhigh | max
  TEMPERATURE         openai only, default 1.0 (ignored in thinking mode)
  CONCURRENCY         parallel chapters (default 3)
  CHUNK_WORDS         target words per request (default 1200)
  GLOSSARY_MODE       relevant | full (default relevant): send only the glossary
                      entries that occur in the chunk, inside the user message,
                      keeping the system prompt short and fully cacheable
  BOOK                default book slug (a directory under books/) when a
                      script is run without --book
  BOOKS_ROOT          project root (default: the current directory)

One book per directory under books/<slug>/:
  books/<slug>/config.json     source PDF, layout parameters, title
  books/<slug>/style.md        what this edition is; spliced into the prompts
  books/<slug>/glossary.json   hand-editable terminology (built by glossary.py)
  books/<slug>/system.md       optional: replaces the skill's prompts/system.md
                               for this book (likewise glossary.md, review.md);
                               <root>/prompts/<name>.md does the same for every
                               book in the project
  work/<slug>/                 book.json, titles.json, ocr/, translated/
  out/<slug>.zh.{md,html,pdf,docx}

A later volume of the same work sets "inherit": "<other-slug>" in its config
and gets that book's layout settings, style.md, prompt overrides and glossary
for free; anything it defines itself wins, and its glossary.json holds only the
names the parent does not already fix. Edits to the parent's glossary therefore
reach every volume.
"""
import json
import os
import re
import time
from pathlib import Path

from dotenv import load_dotenv

SKILL = Path(__file__).resolve().parent.parent      # the skill directory
HERE = Path(os.getenv("BOOKS_ROOT") or Path.cwd()).resolve()   # the project root
load_dotenv(HERE / ".env")

PROVIDER = os.getenv("PROVIDER", "anthropic")
API_KEY = os.getenv("API_KEY") or os.getenv("ANTHROPIC_API_KEY")
BASE_URL = os.getenv("BASE_URL") or os.getenv("ANTHROPIC_BASE_URL")
MODEL = os.getenv("MODEL", "claude-opus-5")
THINKING = os.getenv("THINKING", "on").lower() != "off"
EFFORT = os.getenv("EFFORT", "high")
TEMPERATURE = float(os.getenv("TEMPERATURE", "1.0"))
CONCURRENCY = int(os.getenv("CONCURRENCY", "3"))
CHUNK_WORDS = int(os.getenv("CHUNK_WORDS", "1200"))
GLOSSARY_MODE = os.getenv("GLOSSARY_MODE", "relevant")

WORK = HERE / "work"
OUT = HERE / "out"
PROMPTS = SKILL / "prompts"            # shipped defaults
LOCAL_PROMPTS = HERE / "prompts"       # optional project-wide overrides
BOOKS = HERE / "books"
DEFAULT_BOOK = os.getenv("BOOK")


class Book:
    """Paths and layout config for one book (books/<slug>/config.json).

    `inherit` in config.json names an earlier book — the previous volume of the
    same work — whose settings, prompts and glossary this one starts from."""

    def __init__(self, slug, _seen=None):
        self.slug = slug
        self.dir = BOOKS / slug
        cfg_path = self.dir / "config.json"
        if not cfg_path.exists():
            raise SystemExit(f"no such book: {slug} (expected {cfg_path})")
        own = json.loads(cfg_path.read_text())
        _seen = (_seen or set()) | {slug}
        parent_slug = own.get("inherit")
        if parent_slug in _seen:
            raise SystemExit(f"books inherit in a circle: {slug} -> {parent_slug}")
        self.parent = Book(parent_slug, _seen) if parent_slug else None
        self.cfg = {**(self.parent.cfg if self.parent else {}), **own}
        self.cfg.pop("inherit", None)
        self.title = own.get("title", slug)
        self.work = WORK / slug
        self.translated = self.work / "translated"
        self.ocr = self.work / "ocr"
        self.book_json = self.work / "book.json"
        self.titles_json = self.work / "titles.json"
        self.candidates_json = self.work / "glossary_candidates.json"
        self.problems = self.translated / "problems.txt"
        self.glossary_path = self.dir / "glossary.json"

    def file(self, name):
        """A file from this book's directory, or the book it inherits from."""
        f = self.dir / name
        if f.exists():
            return f
        return self.parent.file(name) if self.parent else None

    def style(self):
        """style.md: what this particular edition is and how it is laid out,
        spliced into the {BOOK} slot of the prompts."""
        f = self.file("style.md")
        return f.read_text().strip() if f else self.title

    def lineage(self):
        """This book and the ones it inherits from, nearest first."""
        return [self] + (self.parent.lineage() if self.parent else [])

    @property
    def pdf(self):
        p = Path(self.cfg["pdf"])
        return p if p.is_absolute() else HERE / p

    def out(self, ext):
        return OUT / f"{self.slug}.zh.{ext}"

    def __str__(self):
        return f"{self.slug} ({self.title})"


def known_books():
    return sorted(d.name for d in BOOKS.glob("*") if (d / "config.json").exists())


def need_book_dir():
    if not BOOKS.is_dir():
        raise SystemExit(f"no books/ directory under {HERE}; run the scripts from the "
                         "project root, or set BOOKS_ROOT")


def pick_book(slug=None):
    """--book, else BOOK in .env, else the only book there is."""
    need_book_dir()
    slug = slug or DEFAULT_BOOK
    if not slug:
        avail = known_books()
        if len(avail) == 1:
            slug = avail[0]
        else:
            raise SystemExit("--book is required; available: " + ", ".join(avail))
    return Book(slug)


def add_book_arg(ap):
    ap.add_argument("--book", help="book slug, a directory under books/ "
                                   "(default: BOOK in .env, or the only one)")

# $/M tokens: (input, output, cached input read, cache write). Edit for your gateway.
PRICE = {
    "claude-opus-5": (5.0, 25.0, 0.5, 6.25),
    "claude-sonnet-5": (2.0, 10.0, 0.2, 2.5),
    "claude-haiku-4-5": (1.0, 5.0, 0.1, 1.25),
    "deepseek-flash": (0.3, 1.2, 0.006, 0.3),      # peak-hour list price
    "deepseek-v4-pro": (1.32, 3.96, 0.044, 1.32),
}


def client():
    if PROVIDER == "openai":
        import openai
        if not BASE_URL:
            raise SystemExit("PROVIDER=openai needs BASE_URL in .env")
        return openai.OpenAI(api_key=API_KEY, base_url=BASE_URL, max_retries=4, timeout=900)
    import anthropic
    return anthropic.Anthropic(api_key=API_KEY, base_url=BASE_URL, max_retries=4, timeout=600)


def prompt(name, book=None):
    """The book's own <name>.md (or one it inherits), else the project's
    prompts/<name>.md, else the skill's."""
    if book is not None:
        own = book.file(f"{name}.md")
        if own is not None:
            return own.read_text()
    local = LOCAL_PROMPTS / f"{name}.md"
    return (local if local.exists() else PROMPTS / f"{name}.md").read_text()


def load_glossary(book):
    """The book's terminology, with inherited entries underneath its own.

    Takes a Book, or a path for a one-off file."""
    files = ([b.glossary_path for b in reversed(book.lineage())]
             if isinstance(book, Book) else [Path(book)])
    g = {}
    for f in files:
        if f.exists():
            g.update(json.loads(f.read_text()))
    return {k: v for k, v in g.items() if not v.get("skip")}


def glossary_block(g):
    """Compact one-line-per-entry rendering for the system prompt."""
    lines = []
    for term, e in sorted(g.items(), key=lambda kv: kv[0].lower()):
        flags = []
        if e.get("of"):
            flags.append(f"epithet of {e['of']}")
        if e.get("no_romanize"):
            flags.append("no_romanize")
        if e.get("note"):
            flags.append(e["note"])
        tail = f"  [{'; '.join(flags)}]" if flags else ""
        lines.append(f"{term} = {e['zh']}{tail}")
    return "\n".join(lines)


def relevant_glossary(g, text):
    """Entries whose term occurs in `text` (word match, plural -s tolerated)."""
    low = text.lower()
    out = {}
    for term, e in g.items():
        t = term.lower()
        if t in low and re.search(r"(?<![a-z])" + re.escape(t) + r"(?:s|’s|'s)?(?![a-z])", low):
            out[term] = e
    return out


class Usage:
    """Accumulates token usage across calls and prices it."""

    def __init__(self, model):
        self.model = model
        self.inp = self.out = self.cache_w = self.cache_r = 0
        self.calls = 0

    def add(self, u):
        self.calls += 1
        if hasattr(u, "prompt_tokens"):  # OpenAI-style usage
            hit = getattr(u, "prompt_cache_hit_tokens", None)
            if hit is None:
                d = getattr(u, "prompt_tokens_details", None)
                hit = getattr(d, "cached_tokens", 0) if d else 0
            self.cache_r += hit or 0
            self.inp += u.prompt_tokens - (hit or 0)
            self.out += u.completion_tokens
            return
        self.inp += u.input_tokens
        self.out += u.output_tokens
        self.cache_w += getattr(u, "cache_creation_input_tokens", 0) or 0
        self.cache_r += getattr(u, "cache_read_input_tokens", 0) or 0

    def cost(self):
        p = PRICE.get(self.model)
        if not p:
            return None
        return (self.inp * p[0] + self.out * p[1] + self.cache_r * p[2] + self.cache_w * p[3]) / 1e6

    def __str__(self):
        c = self.cost()
        cs = f" ${c:.2f}" if c is not None else ""
        return (f"{self.calls} calls, in {self.inp:,} / cached {self.cache_r:,} "
                f"(+{self.cache_w:,} written) / out {self.out:,}{cs}")


def call(cli, system, user, usage=None, max_tokens=32000):
    """One request with a (cached) system prompt. Returns the response text."""
    if PROVIDER == "openai":
        return _call_openai(cli, system, user, usage, max_tokens)
    kw = {}
    if THINKING:
        kw["thinking"] = {"type": "adaptive"}
        kw["output_config"] = {"effort": EFFORT}
    with cli.messages.stream(
        model=MODEL,
        max_tokens=max_tokens,
        system=[{"type": "text", "text": system,
                 "cache_control": {"type": "ephemeral", "ttl": "1h"}}],
        messages=[{"role": "user", "content": user}],
        **kw,
    ) as stream:
        msg = stream.get_final_message()
    if usage is not None:
        usage.add(msg.usage)
    if msg.stop_reason == "max_tokens":
        raise RuntimeError("response hit max_tokens; lower CHUNK_WORDS")
    if msg.stop_reason == "refusal":
        raise RuntimeError(f"refused: {msg.stop_details}")
    return "".join(b.text for b in msg.content if b.type == "text")


def _call_openai(cli, system, user, usage, max_tokens):
    """OpenAI-compatible chat completion (DeepSeek etc.). Streams to avoid
    gateway timeouts on long outputs; DeepSeek caches the system prefix itself."""
    extra = {}
    if MODEL.startswith("deepseek"):
        extra["thinking"] = {"type": "enabled" if THINKING else "disabled"}
    stream = cli.chat.completions.create(
        model=MODEL,
        max_tokens=max_tokens,
        temperature=TEMPERATURE,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
        stream=True,
        stream_options={"include_usage": True},
        extra_body=extra or None,
    )
    parts, finish, u = [], None, None
    for ev in stream:
        if ev.usage:
            u = ev.usage
        if not ev.choices:
            continue
        c = ev.choices[0]
        if c.delta and c.delta.content:
            parts.append(c.delta.content)
        if c.finish_reason:
            finish = c.finish_reason
    if usage is not None and u is not None:
        usage.add(u)
    if finish == "length":
        raise RuntimeError("response hit max_tokens; lower CHUNK_WORDS")
    return "".join(parts)


P_RE = re.compile(r"<p id=\"(\d+)\">(.*?)</p>", re.S)
T_RE = re.compile(r"<title>(.*?)</title>", re.S)
NOTE_RE = re.compile(r"\[\^(\d+)\]")


def format_paras(paras, title=None):
    parts = []
    if title:
        parts.append(f"<title>{title}</title>")
    parts += [f'<p id="{i + 1}">{p}</p>' for i, p in enumerate(paras)]
    return "\n".join(parts)


def parse_paras(text, n_expected, sources):
    """Parse <p id> blocks; return (title, [zh...], problems)."""
    found = {int(m.group(1)): m.group(2).strip() for m in P_RE.finditer(text)}
    title = (T_RE.search(text) or [None, None])[1]
    problems = []
    out = []
    for i in range(1, n_expected + 1):
        if i not in found:
            problems.append(f"paragraph {i} missing")
            out.append("")
            continue
        zh = found[i]
        want = sorted(NOTE_RE.findall(sources[i - 1]))
        got = sorted(NOTE_RE.findall(zh))
        if want != got:
            problems.append(f"paragraph {i}: note markers {want} -> {got}")
        if not zh:
            problems.append(f"paragraph {i} empty")
        else:
            # Chinese normally runs 1.4-2.0 chars per English word; well below that
            # almost always means sentences were dropped.
            words = len(sources[i - 1].split())
            # romanized terms kept in the Chinese count too, else notes heavy
            # with foreign words are flagged although complete
            han = len(re.findall(r"[\u4e00-\u9fff]", zh)) + 1.5 * len(re.findall(r"[A-Za-z]{2,}", zh))
            if words >= 40 and han / words < 0.8:
                problems.append(f"paragraph {i}: too short ({han} chars for {words} words) - "
                                f"probably omitted sentences; translate every sentence")
        out.append(zh)
    extra = set(found) - set(range(1, n_expected + 1))
    if extra:
        problems.append(f"unexpected paragraph ids {sorted(extra)}")
    return title.strip() if title else None, out, problems


def fmt_secs(s):
    s = int(s)
    return f"{s // 3600}h{(s % 3600) // 60:02d}m" if s >= 3600 else f"{s // 60}m{s % 60:02d}s"


class Progress:
    def __init__(self, total, unit="chunks"):
        self.total, self.unit, self.done, self.t0 = total, unit, 0, time.time()

    def tick(self, label=""):
        self.done += 1
        el = time.time() - self.t0
        eta = el / self.done * (self.total - self.done)
        print(f"[{self.done}/{self.total}] {fmt_secs(el)} elapsed, ETA {fmt_secs(eta)}  {label}",
              flush=True)
