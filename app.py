"""Itemized — check every line of a hospital bill against the prices that hospital publishes.

Streamlit front end over the Assignment 4 pipeline. The modules in itemized/ are imported
unchanged, so what this interface shows is what the pipeline produces — there is no second
implementation to drift.
"""
import os
import pathlib
import re
import sys

import streamlit as st

# Must be the first Streamlit call on the page, before anything else can write to it.
st.set_page_config(page_title="Itemized — check a hospital bill",
                   page_icon="🧾", layout="centered")

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "itemized"))

REFERENCE = HERE / "data" / "a3_reference_table.csv"
SAMPLES = sorted((HERE / "samples").glob("*.txt"))


SECRET_KEYS = ("GROQ_API_KEY", "GROQ_MODEL", "GROQ_POOL", "LLM_PROVIDER",
               "GEMINI_API_KEY", "GEMINI_MODEL", "GEMINI_POOL")
CRED_TRACE = []


def _load_credentials():
    """Promote credentials into the environment, where the pipeline modules read them.

    On Streamlit Cloud they arrive as secrets; locally they come from .env.

    This used to read st.secrets only when a secrets.toml existed at one of two guessed
    paths, to avoid the warning Streamlit renders when you touch st.secrets with none set.
    That is the wrong shape of check: where the platform keeps that file is its business and
    it has moved before. The read is attempted directly now and the warning caught, so a
    secret that exists is always found.

    CRED_TRACE records what happened, never a value, so a server that behaves differently
    from a laptop can be diagnosed from the page rather than by guessing.
    """
    try:
        # load_if_toml_exists() asks Streamlit where its own secrets live and returns
        # quietly when there are none. Reading st.secrets directly instead prints a warning
        # onto the page, listing absolute paths, every time the app runs on a laptop.
        if st.secrets.load_if_toml_exists():
            available = set(st.secrets.keys())
            CRED_TRACE.append(f"secrets file found · {len(available)} key(s) in it")
            for key in SECRET_KEYS:
                if key in available:
                    os.environ.setdefault(key, str(st.secrets[key]))
                    CRED_TRACE.append(f"{key} · promoted from secrets")
        else:
            CRED_TRACE.append("no secrets file (expected on a laptop, not on a server)")
    except Exception as e:
        CRED_TRACE.append(f"secrets unreadable ({type(e).__name__})")

    env = HERE / ".env"
    if env.exists():
        CRED_TRACE.append(".env present · loaded")
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("\"\'"))
    else:
        CRED_TRACE.append(".env absent (expected on a server)")

    os.environ.setdefault("LLM_PROVIDER", "groq")
    # groq.py falls back to llama-3.3-70b-versatile, which this project's key cannot reach —
    # it returns 404 model_not_found. Default to models the key actually serves, so the app
    # needs only GROQ_API_KEY to run and cannot be broken by a missing optional secret.
    os.environ.setdefault("GROQ_MODEL", "openai/gpt-oss-120b")
    os.environ.setdefault("GROQ_POOL",
                          "openai/gpt-oss-120b,openai/gpt-oss-20b,qwen/qwen3.8-27b")


_load_credentials()


PLACEHOLDERS = {"your-key", "AIza…", "AIza...", "gsk_…", "gsk_...", "changeme", "xxx"}


def key_problem(name):
    """Why a credential cannot work, when the reason is setup rather than weather.

    Only two faults are worth reporting before a request is made: the key is absent, or it
    is still the placeholder. Nothing else can be known from the string.

    This used to also check the prefix and length - GROQ_API_KEY had to start with `gsk_`,
    GEMINI_API_KEY with `AIza` and run to 30 characters. Google now issues Gemini keys
    beginning `AQ.`, and the check turned a working key into "the vision key does not look
    like a Google API key" and stopped the app before it ever called the API. A format
    guess that refuses a valid credential is worse than no check at all, so the shape of
    the key is left to the service that issued it.
    """
    key = os.environ.get(name, "").strip()
    if not key:
        return "no key is configured on this server"
    if "…" in key or key in PLACEHOLDERS:
        return "the key is still the placeholder text"
    return None


def vision_problem():
    """Reading a photograph needs a vision model. Groq's pool here is text only, so this
    path depends on a Gemini credential, and a missing one is not something waiting fixes.
    """
    fault = key_problem("GEMINI_API_KEY")
    if fault == "no key is configured on this server":
        return ("no vision key is configured on this server, so photographs cannot be read "
                "here yet")
    return fault


def credential_problem():
    """Tell a missing key apart from a failing service, so the error can say which."""
    return key_problem("GROQ_API_KEY")


# Verdicts are written for the pipeline. A patient needs a sentence, not a constant.
VERDICTS = {
    "VERIFIED": ("Matches the posted price", "ok",
                 "The hospital charged what it publishes for this code."),
    "ABOVE_POSTED": ("Charged above the posted price", "bad",
                     "This line costs more than the price this hospital publishes."),
    "QUANTITY_SUSPECT": ("Quantity looks wrong", "warn",
                         "This is billed many times over for something a single visit "
                         "does not usually repeat."),
    "NOT_POSTED": ("No published price to compare", "info",
                   "This hospital does not publish a price for this code, so it cannot "
                   "be checked. Worth asking about."),
    # A tester noted that two bags of saline on one visit is ordinary, so presenting a repeat
    # as an established error overstates what this check can know. Amber, not red.
    "DUPLICATE": ("Billed more than once", "warn",
                  "The same code appears earlier on this bill. That can be legitimate — two "
                  "bags of saline, two X-rays — so it is worth asking whether both were "
                  "actually given rather than assuming an error."),
    "UNREADABLE": ("Could not read this line", "info",
                   "This line was missing a code, a quantity or an amount."),
}

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,600;1,6..72,400&family=Inter:wght@400;500;600;700&display=swap');

:root{
  --ink:#14181F; --muted:#59616F; --faint:#8891A0; --line:#E4E8F0; --rule:#121620;
  --bad:#A32B24; --warn:#8A5E0A; --ok:#136B45; --info:#48505E; --accent:#1B4590;
  --bad-bg:#FDF4F3; --warn-bg:#FDF9F0; --ok-bg:#F2F8F5; --info-bg:#F6F8FB;
  --paper:#FCFCFD;
  --sans:'Inter',-apple-system,'Segoe UI',Roboto,sans-serif;
  --serif:'Newsreader',Georgia,'Times New Roman',serif;
}
html,body,[class*="css"]{font-family:var(--sans)}
.block-container{max-width:900px;padding-top:2rem}
.stApp{background:var(--paper)}

/* ---- masthead ---------------------------------------------------------- */
.mast{border-bottom:3px solid var(--rule);padding-bottom:18px;margin-bottom:4px}
.mast h1{font-family:var(--serif);font-size:54px;font-weight:600;letter-spacing:-1.4px;
  line-height:.95;margin:2px 0 11px;color:var(--ink)}
.mast .sub{font-size:16.5px;color:var(--muted);line-height:1.5;max-width:60ch;margin:0;
  font-weight:400}
.badge{display:inline-block;font-size:10.5px;letter-spacing:1.1px;text-transform:uppercase;
  font-weight:700;color:var(--accent);background:#EAF0FA;border:1px solid #D4E0F2;
  border-radius:999px;padding:4px 12px}

/* ---- step headers ------------------------------------------------------ */
.step{display:flex;align-items:center;gap:11px;margin:30px 0 11px}
.step .n{width:25px;height:25px;border-radius:50%;background:var(--rule);color:#fff;
  font-family:var(--serif);font-size:14px;font-weight:600;display:flex;align-items:center;
  justify-content:center;flex:0 0 25px;padding-bottom:1px}
.step .t{font-family:var(--serif);font-size:22px;font-weight:600;color:var(--ink);
  letter-spacing:-.3px}

/* ---- the headline figures --------------------------------------------- */
.totals{display:flex;border:1px solid var(--line);border-radius:14px;overflow:hidden;
  margin:4px 0 10px;background:#fff;box-shadow:0 1px 2px rgba(20,24,31,.04)}
.totals .cell{flex:1;padding:17px 19px;border-right:1px solid var(--line)}
.totals .cell:last-child{border-right:none;background:linear-gradient(180deg,#FFFBF3,#FFF8EC)}
.totals .k{font-size:10.5px;letter-spacing:.9px;text-transform:uppercase;color:var(--faint);
  font-weight:700;margin-bottom:6px}
.totals .v{font-size:27px;font-weight:600;color:var(--ink);letter-spacing:-.8px;
  line-height:1.05;font-variant-numeric:tabular-nums}
.totals .cell.hero .v{color:#7A4F00;font-size:32px}
.totals .n{font-size:11.5px;color:var(--muted);margin-top:6px;line-height:1.45}

/* ---- what to ask ------------------------------------------------------- */
.todo{border:1px solid #CFDCF0;background:linear-gradient(180deg,#F8FBFF,#F3F7FD);
  border-radius:14px;padding:18px 20px;margin:14px 0 6px}
.todo .h{font-size:11.5px;letter-spacing:1px;text-transform:uppercase;font-weight:700;
  color:var(--accent);margin-bottom:11px}
.todo ol{margin:0;padding-left:20px}
.todo li{font-size:14.5px;color:var(--ink);line-height:1.6;margin-bottom:9px}
.todo li:last-child{margin-bottom:0}
.todo .tail{font-size:12.5px;color:var(--muted);margin:12px 0 0;line-height:1.5}

/* ---- line cards -------------------------------------------------------- */
.verdict{border:1px solid var(--line);border-left-width:4px;border-radius:12px;
  padding:14px 17px 12px;margin-bottom:10px;background:#fff;position:relative;
  box-shadow:0 1px 2px rgba(20,24,31,.03)}
.verdict.ok{border-left-color:var(--ok);background:var(--ok-bg)}
.verdict.bad{border-left-color:var(--bad);background:var(--bad-bg)}
.verdict.warn{border-left-color:var(--warn);background:var(--warn-bg)}
.verdict.info{border-left-color:var(--info);background:var(--info-bg)}
.verdict .top{display:flex;justify-content:space-between;gap:12px;align-items:baseline}
.verdict .label{font-weight:600;font-size:14.5px;color:var(--ink);letter-spacing:-.1px}
.verdict .glyph{display:inline-block;width:17px;font-weight:700;text-align:center}
.verdict.ok .label{color:var(--ok)}
.verdict.bad .label{color:var(--bad)}
.verdict.warn .label{color:var(--warn)}
.verdict.info .label{color:var(--info)}
.verdict .amt{font-weight:600;font-size:16px;white-space:nowrap;color:var(--ink);
  font-variant-numeric:tabular-nums;letter-spacing:-.3px}
.verdict .desc{font-size:13.5px;color:var(--ink);margin-top:5px}
.verdict .code{font-family:ui-monospace,'SF Mono',Menlo,monospace;font-size:12px;
  color:var(--muted);background:rgba(20,24,31,.045);padding:1px 6px;border-radius:4px}
.verdict .why{font-size:13px;color:var(--muted);margin-top:6px;line-height:1.55}
.verdict .figs{font-size:12.5px;color:var(--muted);margin-top:8px;padding-top:8px;
  border-top:1px dashed var(--line)}
.verdict .figs b{color:var(--ink);font-weight:600;font-variant-numeric:tabular-nums}
.verdict .src{font-size:11.5px;margin-top:7px}
.verdict .src a{color:var(--accent);text-decoration:none;border-bottom:1px solid #C6D6EE}
.verdict .src .meta{color:var(--faint)}

/* share-of-bill bar: which line actually is the bill */
.share{display:flex;align-items:center;gap:9px;margin-top:9px}
.share .track{flex:1;height:5px;background:rgba(20,24,31,.07);border-radius:3px;
  overflow:hidden}
.share .fill{height:100%;border-radius:3px;background:currentColor;opacity:.55}
.verdict.ok .share{color:var(--ok)}
.verdict.bad .share{color:var(--bad)}
.verdict.warn .share{color:var(--warn)}
.verdict.info .share{color:var(--info)}
.share .pct{font-size:11px;color:var(--faint);white-space:nowrap;
  font-variant-numeric:tabular-nums}

.dominant{border:1px solid #E3D6B8;background:#FFFCF4;border-radius:12px;padding:13px 17px;
  margin:0 0 10px;font-size:13.5px;color:var(--ink);line-height:1.55}
.dominant b{font-weight:600}

.unpriced{border:1px solid var(--line);background:#F6F8FB;border-radius:12px;
  padding:12px 16px;margin:0 0 6px;font-size:13px;color:var(--muted);line-height:1.55}
.unpriced b{color:var(--ink);font-weight:600}

.sectlabel{font-size:11px;letter-spacing:1px;text-transform:uppercase;font-weight:700;
  color:var(--faint);margin:22px 0 9px}

/* ---- narrow screens ---------------------------------------------------- */
@media (max-width:560px){
  .totals{flex-direction:column}
  .totals .cell{border-right:none;border-bottom:1px solid var(--line)}
  .totals .cell:last-child{border-bottom:none}
  .mast h1{font-size:40px}
  .verdict .top{flex-direction:column;gap:3px}
  .verdict .amt{font-size:18px}
}

/* ---- print: this gets taken to a phone call ---------------------------- */
@media print{
  .stApp{background:#fff}
  header,footer,[data-testid="stToolbar"],[data-testid="stStatusWidget"],
  .stButton,.stRadio,.stSelectbox,.stTextArea,[data-testid="stExpander"] button{display:none !important}
  .block-container{max-width:100%;padding:0}
  .verdict,.todo,.totals,.dominant,.unpriced{break-inside:avoid;box-shadow:none}
  .todo{border:1px solid #999;background:#fff}
  a{text-decoration:underline}
}
</style>
"""


def money(v):
    return f"${v:,.2f}"


def plain(text):
    """Streamlit reads a pair of dollar signs as LaTeX math, which silently mangles any
    sentence quoting two amounts. Escape them before writing prose to the page."""
    return (text or "").replace("$", r"\$")


@st.cache_resource(show_spinner=False)
def reference_table():
    from checker import load_reference
    return load_reference(str(REFERENCE))


def sample_label(path):
    """The hospital name off the first line of a sample.

    The samples deliberately use different formats, to show the reader coping with more
    than one. One wraps its header in rules of '=', which two testers read as a rendering
    fault in the menu rather than as part of the bill. Strip the decoration, and re-case
    only a label that arrives shouting - titling a mixed-case name turns NC into Nc.
    """
    raw = path.read_text().strip().splitlines()[0].strip("=~|-").strip()
    return raw.title() if raw.isupper() else raw


@st.cache_data(show_spinner=False)
def hospital_names():
    import csv
    with REFERENCE.open() as fh:
        return sorted({r["hospital_name"] for r in csv.DictReader(fh)})


def pdf_text(upload):
    """Pull the text layer out of an uploaded PDF.

    Most hospital statements arrive as a PDF from a patient portal or an email attachment,
    and those carry a real text layer. A photographed or scanned bill does not - it is an
    image in a PDF wrapper, and nothing here can read it. That case returns no text rather
    than a few characters of noise, so the caller can say so plainly instead of running a
    check on nothing.
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        return None, "pdf support is not installed on this server"
    try:
        reader = PdfReader(upload)
        pages = [(pg.extract_text() or "") for pg in reader.pages]
    except Exception as e:
        return None, f"this file could not be opened as a PDF ({str(e)[:80]})"
    text = "\n".join(pages).strip()
    if len(text) < 40:
        return None, "scanned"
    return text, f"{len(reader.pages)} page(s)"


def detect_hospital(text, known):
    """Bills print the hospital on the first line, so the user does not have to pick it."""
    head = "\n".join(text.strip().splitlines()[:4]).upper()
    for name in known:
        if name.upper() in head:
            return name
    first = text.strip().splitlines()[0].strip().upper() if text.strip() else ""
    for name in known:
        a, b = set(first.split()), set(name.upper().split())
        if len(a & b) >= 3:
            return name
    return None


from codes import has_code


def validate(text, hospital, is_image=False):
    """Return a plain-English problem, or None when the bill is good enough to run."""
    if not text or not text.strip():
        return ("Paste a bill first",
                "There is nothing to check yet. Pick one of the sample bills, or paste the "
                "text of your own itemized statement into the box.")
    if is_image:
        # Nothing to inspect until the model has looked at the picture. The only thing that
        # can be checked up front is that a hospital was chosen - the name cannot be read
        # off a photograph before the read happens.
        if hospital is None:
            return ("Pick the hospital that issued this bill",
                    "A photograph cannot be matched to a hospital before it is read, so "
                    "choose the hospital in step 2 above. The tool holds published prices "
                    "for nine of them.")
        return None
    if len(text.strip()) < 60:
        return ("That looks too short to be a bill",
                "An itemized statement usually lists several lines, each with a code, a "
                "quantity and an amount. Paste the whole statement rather than one line.")
    # The shape of a billing code is defined once, in codes.py, which records why the
    # looser pattern this used to carry was wrong in four separate places.
    if not has_code(text):
        return ("No billing codes found",
                "Every line on an itemized bill carries a procedure code, like 74177 or "
                "J1885. None were found here — this may be a summary bill rather than an "
                "itemized one. Ask the hospital for the itemized statement.")
    if hospital is None:
        return ("We do not have this hospital's published prices",
                "This tool compares your bill against prices a hospital publishes, and it "
                "currently holds files for nine hospitals. Pick the hospital from the list "
                "in step 2 above if it is there, or try one of the sample bills to see how "
                "it works.")
    return None


@st.cache_data(show_spinner=False)
def source_meta():
    """Readable provenance for each hospital's price file.

    Every user-test session noticed the source link and none could evaluate it - a 200-
    character CDN URL tells a patient nothing about what they are looking at. The reference
    table already carries the file's size and the date it was retrieved, so the link can
    carry a name, a date and a format instead of being its own raw address.
    """
    import csv
    out = {}
    with REFERENCE.open(encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            url = (r.get("mrf_source_url") or "").strip()
            if not url or url in out:
                continue
            try:
                mb = f"{float(r['mrf_file_mb']):,.0f} MB"
            except (TypeError, ValueError, KeyError):
                mb = ""
            out[url] = {
                "hospital": r.get("hospital_name", "").strip(),
                "retrieved": (r.get("retrieved_at") or "")[:10],
                "size": mb,
                "ext": url.rsplit(".", 1)[-1].upper()[:4] if "." in url[-6:] else "FILE",
            }
    return out


def source_line(url):
    """A citation a patient can read, with the raw URL kept as the link target."""
    m = source_meta().get(url)
    if not m:
        return f"<div class='src'><a href='{url}'>Published price file</a></div>"
    # The hospital is named once at the top of the page; repeating it on every line is noise.
    # What a reader cannot get elsewhere is the format, the size and the date.
    bits = [b for b in (m["ext"], m["size"],
                        f"retrieved {m['retrieved']}" if m["retrieved"] else "") if b]
    return (f"<div class='src'><a href='{url}'>Published standard-charge file</a>"
            f"<span class='meta'> · {' · '.join(bits)}</span></div>")


ASKS = {
    # The arithmetic has to survive being read aloud. An earlier version of this line said
    # "billed $2,457.00 when this hospital publishes $189.00 for it - a difference of
    # $567.00", which does not subtract: $189.00 is the price of one unit and there are ten
    # of them. A patient reading that to a billing office is immediately wrong on the maths.
    "ABOVE_POSTED": lambda l: (
        (f"Ask why code <b>{l['code']}</b> was billed {money(l['charged'])} for "
         f"{l['units']} units. This hospital publishes {money(l['posted_price'])} per unit, "
         f"or {money(round(l['posted_price'] * l['units'], 2))} for {l['units']}"
         if l.get("units", 1) > 1 else
         f"Ask why code <b>{l['code']}</b> was billed {money(l['charged'])} when this "
         f"hospital publishes {money(l['posted_price'])} for it")
        + (f" — a difference of {money(l['amount_over'])}." if l.get("amount_over") else ".")),
    "QUANTITY_SUSPECT": lambda l: (
        f"Ask whether all <b>{l['units']}</b> of code <b>{l['code']}</b> were actually "
        f"provided — this is normally billed once per visit."),
    "NOT_POSTED": lambda l: (
        f"Ask what this hospital publishes for code <b>{l['code']}</b>. It is not in their "
        f"published file, so this {money(l['charged'])} charge could not be checked."),
    "DUPLICATE": lambda l: (
        f"Ask whether both entries of code <b>{l['code']}</b> were provided. Two of the same "
        f"service on one visit can be legitimate — worth confirming rather than assuming."),
    "UNREADABLE": lambda l: (
        f"Ask for a clearer copy of the line showing code <b>{l['code']}</b> — it was missing "
        f"a code, quantity or amount."),
}

ORDER = {"ABOVE_POSTED": 0, "QUANTITY_SUSPECT": 1, "DUPLICATE": 2,
         "NOT_POSTED": 3, "UNREADABLE": 4, "VERIFIED": 5}


DOMINANT_SHARE = 50      # percent of a bill at which one line leads the questions


def flagged_in_reading_order(reviewed):
    """The flagged lines, worst first - with one exception that testing forced.

    The default order is certainty: a confirmed rate difference ahead of a quantity that
    still needs checking, then by amount. That is right for a bill whose lines are
    comparable in size.

    It is wrong when one line dominates. On the Alta Bates sample code 74177 is 87% of the
    bill; the page said so, told the reader "if you only have time for one question, make it
    that one", and then listed it third. Tester 3 read both and stopped there. An earlier
    pass had tried to patch this by naming the question number - which documented the
    contradiction rather than resolving it. A line worth more than half the bill now leads,
    and everything behind it keeps the certainty order.
    """
    total = reviewed.get("billed_total") or 0

    def key(line):
        share = (line.get("charged") or 0) / total * 100 if total else 0
        return (0 if share >= DOMINANT_SHARE else 1,
                ORDER.get(line["verdict"], 9),
                -(line.get("charged") or 0))

    return sorted((l for l in reviewed["lines"] if l["verdict"] != "VERIFIED"), key=key)


def line_quantity_exposure(line):
    """What one quantity-flagged line puts in question beyond the rate gap.

    A quantity flag and a rate overcharge can sit on the same line. The headline counts the
    rate part - what was charged above the posted price per unit. This is the rest: what
    falls away if the line should have been billed once rather than ten times.
    """
    posted, units = line.get("posted_price"), line.get("units") or 1
    if line.get("verdict") != "QUANTITY_SUSPECT" or not posted or units <= 1:
        return 0.0
    at_one = round((line.get("charged") or 0) - posted, 2)
    return round(at_one - (line.get("amount_over") or 0), 2)


def unpriced_sentence(unpriced, unpriced_total):
    """Why the three headline figures do not subtract into one another, in one sentence.

    Tester 3 raised this twice, in two sessions, either side of a change that did not touch
    it. The explanation already sat directly beneath the figures and he had still subtracted
    first and concluded the tool was broken - so it works as a reference and not as a
    guardrail. It now travels with the plain-language summary and the downloaded review as
    well, both of which are read on their own, away from the figures they describe.
    """
    if not unpriced:
        return ""
    codes = ", ".join(l["code"] for l in unpriced)
    return (f"The three figures above do not subtract into one another: "
            f"{money(unpriced_total)} of what was billed — "
            f"{'code' if len(unpriced) == 1 else 'codes'} {codes} — has no published price "
            f"to compare against, so it is counted in the billed total and in neither of the "
            f"other two.")


def action_list(reviewed):
    """The questions to put to the billing office, which is what this tool claims to produce.

    The positioning settled on after user testing is that Itemized turns a bill into specific
    sourced questions. Both testers independently wrote their own version of this list at the
    end of their session; the interface was making them do that work by hand.
    """
    flagged = flagged_in_reading_order(reviewed)
    if not flagged:
        return ""
    items = "".join(f"<li>{ASKS[l['verdict']](l)}</li>" for l in flagged if l["verdict"] in ASKS)
    return ("<div class='todo'><div class='h'>What to ask the billing office</div>"
            f"<ol>{items}</ol>"
            "<p class='tail'>Take the figures with you. Every one above comes from the price "
            "file this hospital publishes itself, linked under each line below.</p></div>")


# Colour is not the only signal. Roughly one man in twelve cannot separate the red and green
# these cards rely on, and this one is about money.
GLYPH = {"VERIFIED": "✓", "ABOVE_POSTED": "▲", "QUANTITY_SUSPECT": "✱",
         "NOT_POSTED": "?", "DUPLICATE": "⧉", "UNREADABLE": "—"}


def verdict_card(line, bill_total=None):
    label, tone, why = VERDICTS.get(
        line["verdict"], ("Checked", "info", ""))
    glyph = GLYPH.get(line["verdict"], "·")
    posted = line.get("posted_price")
    posted_bit = (f"<div class='figs'>This hospital publishes <b>{money(posted)}</b> "
                  f"for this code"
                  + (f" · Medicare reference {money(line['medicare_payment'])}"
                     if line.get("medicare_payment") else "")
                  + "</div>") if posted else ""
    qty_bit = ""
    if line["verdict"] == "QUANTITY_SUSPECT" and posted and line["units"] > 1:
        # A quantity flag and a rate overcharge can occur on the same line. An earlier
        # version of this card asserted that the per-unit rate was correct and that none of
        # the line reached the headline - true on a bill where the rate matches, false on
        # one where it does not, and it was shown on both. On Alta Bates 74177 each unit was
        # billed at twice the posted rate and $82,870.00 of it was already in the headline
        # while this card denied both. The two cases are now told apart.
        units = line["units"]
        over = line.get("amount_over") or 0
        at_one = round(line["charged"] - posted, 2)   # owed back if a single unit was right
        per_unit = round(line["charged"] / units, 2)
        beyond_rate = line_quantity_exposure(line)    # the part the headline does not hold
        if over > 0:
            qty_bit = (f"<div class='why'>Each one was billed at {money(per_unit)} against a "
                       f"posted {money(posted)}, and {money(over)} of that is already counted "
                       f"in the unexplained difference above. If the quantity is also wrong, "
                       f"a further {money(beyond_rate)} — {units - 1} more at the posted rate "
                       f"— is in question on top of it.</div>")
        else:
            qty_bit = (f"<div class='why'>Billed once, this line would be {money(posted)}. "
                       f"Each one was charged at the posted rate, so none of this line is "
                       f"counted in the unexplained difference above — but if the quantity "
                       f"is wrong, {money(at_one)} of this charge is in question.</div>")
    # Every card is the same height, so nothing on the page shows that one line can be
    # almost the entire bill. On the Alta Bates statement code 74177 is 87% of it. A patient
    # deciding where to spend a phone call should be able to see that without doing division.
    share_bit = ""
    if bill_total and bill_total > 0 and line.get("charged"):
        pct = line["charged"] / bill_total * 100
        share_bit = (f"<div class='share'><div class='track'>"
                     f"<div class='fill' style='width:{min(pct, 100):.1f}%'></div></div>"
                     f"<span class='pct'>{pct:.0f}% of the bill</span></div>")
    src = line.get("source_url") or ""
    src_bit = source_line(src) if src else ""
    # Emitted as one line: a blank line inside raw HTML ends the block for Streamlit's
    # markdown parser, which then prints the trailing tags as text.
    return (
        f"<div class='verdict {tone}'>"
        f"<div class='top'><span class='label'>"
        f"<span class='glyph'>{glyph}</span> {label}</span>"
        f"<span class='amt'>{money(line['charged'])}</span></div>"
        f"<div class='desc'>{line.get('description','')}"
        f"<span class='code'> · {line['code']} · qty {line['units']}</span></div>"
        f"<div class='why'>{why}</div>{posted_bit}{qty_bit}{share_bit}{src_bit}"
        f"</div>")


st.markdown(CSS, unsafe_allow_html=True)
st.markdown(
    "<div class='mast'>"
    "<span class='badge'>45 CFR 180 · published prices</span>"
    "<h1>Itemized</h1>"
    "<p class='sub'>Check every line of a hospital bill against the prices that hospital is "
    "legally required to publish — so you know what to ask before you pay.</p>"
    "</div>", unsafe_allow_html=True)

def render_diagnostics():
    """What this server can see, on request at ?diag=1.

    A deployment that behaves differently from a laptop was costing a round trip through
    the dashboard and a guess each time. The page reports on itself instead: which
    credentials arrived and by what route, whether each is well-formed, and which models
    the pool can still reach. No value is ever printed - only whether one is there.
    """
    st.markdown("#### Server diagnostics")
    st.caption("Reached with ?diag=1. Visible to anyone with the link, so it names no "
               "secret - only whether one arrived.")
    for note in CRED_TRACE:
        st.text(note)
    for name, fault in (("GROQ_API_KEY  (text)", credential_problem()),
                        ("GEMINI_API_KEY (photos)", vision_problem())):
        st.text(f"{name} · {fault if fault else 'present and well-formed'}")
    st.text(f"LLM_PROVIDER={os.environ.get('LLM_PROVIDER')} · "
            f"GEMINI_MODEL={os.environ.get('GEMINI_MODEL', 'pool default')}")
    try:
        sys.path.insert(0, str(HERE / "itemized"))
        import gemini, groq
        for label, mod in (("gemini", gemini), ("groq", groq)):
            st.text(f"{label} pool · {len(mod.pool_status()['available'])} of "
                    f"{len(mod.pool_status()['pool'])} models available"
                    + (f" · resting: {', '.join(mod.pool_status()['exhausted'])}"
                       if mod.pool_status()['exhausted'] else ""))
    except Exception as e:
        st.text(f"pool status unavailable ({type(e).__name__})")
    st.divider()


if st.query_params.get("diag"):
    render_diagnostics()

st.markdown("<div class='step'><div class='n'>1</div><div class='t'>Your bill</div></div>",
            unsafe_allow_html=True)
# Tabs do not report which one is open, and the bill source used to be "whatever is in the
# paste box, or else the selected sample". On the paste tab with an empty box that resolved
# to the sample - so pressing Check with nothing entered returned a full analysis of someone
# else's bill under the user's own heading. The choice is now explicit, because a tool that
# can show you the wrong bill's total is worse than one that asks you to pick a radio button.
MODE_SAMPLE = "Use a sample bill"
MODE_PASTE = "Paste my own bill"
MODE_PDF = "Upload a PDF"
MODE_IMAGE = "Upload a photo"
mode = st.radio("Where is the bill coming from?",
                [MODE_SAMPLE, MODE_PASTE, MODE_PDF, MODE_IMAGE],
                horizontal=True, label_visibility="collapsed", key="bill_mode")

sample_text = ""
pdf_extracted = ""
image_bytes = image_mime = None
if mode == MODE_SAMPLE:
    st.caption("No bill handy? These are realistic statements you can try the tool on.")
    choice = st.selectbox("Sample statement", SAMPLES, format_func=sample_label,
                          key="sample_choice")
    sample_text = choice.read_text() if choice else ""
    with st.expander("See the statement"):
        st.code(sample_text, language=None)
        st.caption("Shown exactly as it arrives. Hospitals issue itemized statements in "
                   "very different formats — some print tidy columns, others export "
                   "delimited text — and the reader is built to handle both.")
elif mode == MODE_PDF:
    up = st.file_uploader("Your itemized statement as a PDF", type=["pdf"],
                          key="bill_pdf",
                          help="A statement downloaded from a patient portal or emailed by "
                               "the hospital. A photograph of a paper bill will not work — "
                               "there is no text in it to read.")
    pdf_note = None
    if up is not None:
        pdf_extracted, pdf_note = pdf_text(up)
        if pdf_extracted:
            st.success(f"Read {len(pdf_extracted.split())} words from {up.name} "
                       f"({pdf_note}).")
            with st.expander("See what was read out of the PDF"):
                st.code(pdf_extracted[:4000], language=None)
                st.caption("This is the text the check runs on. If it looks wrong or "
                           "incomplete, paste the statement instead.")
        elif pdf_note == "scanned":
            st.error("**This PDF has no text in it**\n\nIt is a scan or a photograph, so "
                     "there are no characters to read — only an image of them. Open the "
                     "statement in your hospital's patient portal and download it again, or "
                     "switch to *Paste my own bill* and type the lines in.")
        else:
            st.error(f"**That file could not be read**\n\n{pdf_note.capitalize()}. Try "
                     f"downloading the statement again, or paste the text instead.")
elif mode == MODE_IMAGE:
    shot = st.file_uploader("A photo or scan of your itemized statement",
                            type=["jpg", "jpeg", "png", "webp"], key="bill_image",
                            help="A phone photo of a paper bill works. Get the whole page "
                                 "in frame, keep it straight on, and make sure the codes "
                                 "and amounts are in focus.")
    if shot is not None:
        image_bytes = shot.getvalue()
        image_mime = shot.type or "image/jpeg"
        c1, c2 = st.columns([1, 2])
        with c1:
            st.image(image_bytes, caption=shot.name, use_container_width=True)
        with c2:
            st.caption(f"{len(image_bytes)/1024:,.0f} KB · {image_mime}")
            st.caption("The photo is read once to pull out the codes, quantities and "
                       "amounts, and is not stored. Every price it is compared against "
                       "still comes from the hospital's published file — the photo supplies "
                       "the bill, never a price.")
            if len(image_bytes) > 4_000_000:
                st.warning("That is a large image. If the read fails, try a smaller photo.")
else:
    st.text_area(
        "Paste the text of your itemized statement",
        height=220, placeholder="Copy everything from your itemized bill and paste it here…",
        help="An itemized statement lists each charge with its procedure code, quantity "
             "and amount. A summary bill showing only a total cannot be checked.",
        key="pasted_text")
    st.caption("Nothing you paste is stored. It is sent to the reading model once to pull "
               "out the codes, quantities and amounts, and is not retained afterwards. You "
               "can delete the patient name and account number before pasting — the check "
               "does not use them.")

if mode == MODE_PASTE:
    bill_text = (st.session_state.get("pasted_text") or "").strip()
elif mode == MODE_PDF:
    bill_text = (pdf_extracted or "").strip()
elif mode == MODE_IMAGE:
    # There is no text to validate until the model has looked at the picture, so a photo
    # stands in for the bill text through the checks that run before the read.
    bill_text = "(photograph of a statement)" if image_bytes else ""
else:
    bill_text = sample_text
known = hospital_names()
detected = detect_hospital(bill_text, known) if bill_text else None

st.markdown("<div class='step'><div class='n'>2</div><div class='t'>The hospital</div></div>",
            unsafe_allow_html=True)
if detected:
    st.success(f"Read from the top of the bill: **{detected}**")
    hospital = detected
else:
    hospital = st.selectbox(
        "Which hospital issued this bill?",
        ["— select —"] + known,
        help="We compare against the price file this hospital publishes, so we need to "
             "know which one it is.")
    hospital = None if hospital == "— select —" else hospital

st.divider()
run = st.button("Check this bill", type="primary", use_container_width=True)

if run:
    # A new run invalidates whatever is on screen. Without this, a failed run would leave
    # the previous bill's results sitting under the error message.
    st.session_state.pop("result", None)

    problem = validate(bill_text, hospital, is_image=(mode == MODE_IMAGE))
    # Everything below the first spinner used to happen with no visible acknowledgement, so
    # a press looked like nothing had happened and got pressed again - and the second press
    # produced a detailed summary that was then replaced by the fallback as the first run
    # finished behind it. The status panel appears on the first paint of the rerun.
    status = None if problem else st.status("Checking your bill…", expanded=True)
    if problem:
        title, detail = problem
        st.error(f"**{title}**\n\n{detail}")
        st.stop()

    try:
        if mode == MODE_IMAGE:
            status.write("Looking at the photograph…")
            from ai_reader import read_bill_image
            items, meta = read_bill_image(image_bytes, image_mime)
        else:
            status.write("Reading the statement…")
            from ai_reader import read_bill
            items, meta = read_bill(bill_text)
    except Exception:
        items, meta = [], {"ok": False, "error": "unreachable"}

    if not meta.get("ok") and mode == MODE_IMAGE and vision_problem():
        st.error(f"**Photographs cannot be read on this server**\n\nReading a photo needs a "
                 f"vision model, and {vision_problem()}. This is a setup problem, not a "
                 f"problem with your photo, and trying again will not change it. Use "
                 f"*Upload a PDF* if you have the statement as a PDF, or *Paste my own bill* "
                 f"and type the lines in — both of those work.")
        st.caption(f"For the maintainer: add GEMINI_API_KEY to the app's secrets. "
                   f"error={str(meta.get('error'))[:140]}")
        st.stop()

    if not meta.get("ok"):
        setup = credential_problem()
        if setup:
            # A configuration fault, not a transient one — saying "try again" would be wrong.
            st.error(f"**This tool is not finished being set up**\n\nIt cannot reach the "
                     f"reading service because {setup}. Nothing is wrong with your bill. "
                     f"If you are testing this for me, tell me you saw this message.")
            st.caption(f"For the maintainer: provider={os.environ.get('LLM_PROVIDER')} · "
                       f"model={os.environ.get('GROQ_MODEL', 'default')} · "
                       f"error={str(meta.get('error'))[:160]}")
        else:
            st.error("**We could not read this bill just now**\n\nThe reading service did "
                     "not respond. This is on us, not your bill — wait about a minute and "
                     "press Check this bill again. Nothing you entered was lost.")
            st.caption(f"For the maintainer: {str(meta.get('error'))[:200]}")
        st.stop()

    if not items and mode == MODE_IMAGE:
        st.error("**No charges could be read from that photo**\n\nThe image came through "
                 "but no lines with a code, a quantity and an amount could be made out. "
                 "Try again with the whole page in frame, straight on, in good light — or "
                 "switch to *Paste my own bill* and type the lines in.")
        st.stop()

    if not items:
        st.error("**No charges could be made out**\n\nThe statement came through, but no "
                 "line items could be read from it. Paste the itemized section — the rows "
                 "with a code, a quantity and an amount — rather than the summary page.")
        st.stop()

    status.write(f"Matching {len(items)} line(s) against this hospital's published prices…")
    from checker import check_bill
    reviewed = check_bill({"hospital_name": hospital, "line_items": items}, reference_table())
    status.write(f"Working out what to ask about {reviewed['flagged_count']} flagged line(s)…")

    status.write("Writing your summary and letter…")
    if True:
        narrative, letter = "", ""
        try:
            from ai_advocate import write_narrative
            from provenance import verify
            summary, letter_body, _ = write_narrative(reviewed)
            # The guard is the point of the tool: prose that cites an unverifiable figure is
            # discarded rather than shown, even though showing it would look better.
            if summary and verify(summary, reviewed)[0]:
                narrative = summary
            if letter_body and verify(letter_body, reviewed)[0]:
                letter = letter_body
        except Exception:
            pass

    # Everything the page needs is stored before any of it is drawn. A download button is a
    # widget like any other: pressing one reruns the script, and `run` is False on that pass,
    # so a result computed inside `if run:` and drawn there vanishes the moment the user
    # downloads the letter. A tester hit exactly that and had to re-run the check to get the
    # review as well. Holding the result in session state also stops the summary being
    # rewritten by the model on every rerun, which made the wording change under the user.
    st.session_state["result"] = {
        "reviewed": reviewed,
        "hospital": hospital,
        "narrative": narrative,
        "letter": letter,
        "items_dropped": meta.get("items_dropped"),
        # Stamped with the exact inputs that produced it. Results used to persist across a
        # change of sample, hospital or input mode, so a newly chosen hospital appeared as a
        # heading above the previous bill's findings.
        "fingerprint": (bill_text, hospital, mode,
                        len(image_bytes) if image_bytes else 0),
    }
    status.update(label="Checked", state="complete", expanded=False)

result = st.session_state.get("result")
if result and result.get("fingerprint") != (
        bill_text, hospital, mode, len(image_bytes) if image_bytes else 0):
    # The inputs have changed since this was produced. Showing it now would caption one
    # bill's findings with another bill's hospital.
    st.session_state.pop("result", None)
    result = None
    st.info("The bill or hospital changed, so the previous results were cleared. "
            "Press **Check this bill** to run the new one.")

if result:
    reviewed = result["reviewed"]
    hospital = result["hospital"]
    narrative = result["narrative"]
    letter = result["letter"]

    if result.get("items_dropped"):
        st.info(f"{result['items_dropped']} line(s) were skipped because a code, quantity or "
                "amount could not be read. Everything below is from the lines that were "
                "complete.")

    st.markdown("<div class='step'><div class='n'>3</div><div class='t'>What we found"
                "</div></div>", unsafe_allow_html=True)

    # The three figures do not form an equation and must not look like one. billed_total
    # covers every line; posted_total covers only the lines this hospital publishes a price
    # for; the headline is the sum of rate differences, not the subtraction of the first two.
    # On the Alta Bates sample a reader who subtracts gets $87,463.95 and the page says
    # $87,098.20 - the $365.75 gap being one line with no published price at all. The old
    # caption, "For the same lines, at the quantities billed", was simply false. The unpriced
    # total is now stated rather than left as an unexplained discrepancy.
    unpriced = [l for l in reviewed["lines"] if l.get("posted_price") is None]
    unpriced_total = round(sum(l["charged"] for l in unpriced), 2)
    comparable = round(reviewed["billed_total"] - unpriced_total, 2)

    st.markdown(
        "<div class='totals'>"
        f"<div class='cell'><div class='k'>You were billed</div>"
        f"<div class='v'>{money(reviewed['billed_total'])}</div>"
        f"<div class='n'>All {reviewed['line_count']} lines on the statement.</div></div>"
        f"<div class='cell'><div class='k'>This hospital publishes</div>"
        f"<div class='v'>{money(reviewed['posted_total'])}</div>"
        f"<div class='n'>Against {money(comparable)} billed on the "
        f"{reviewed['line_count'] - len(unpriced)} lines it publishes a price for.</div></div>"
        f"<div class='cell hero'><div class='k'>Unexplained difference</div>"
        f"<div class='v'>{money(reviewed['questionable_amount'])}</div>"
        f"<div class='n'>A gap to ask about — not money you are owed, "
        f"and not a refund estimate.</div></div>"
        "</div>", unsafe_allow_html=True)

    if unpriced:
        st.markdown(
            f"<div class='unpriced'><b>{money(unpriced_total)} on "
            f"{len(unpriced)} line(s) could not be compared at all</b> — "
            f"{', '.join(l['code'] for l in unpriced)} "
            f"{'is' if len(unpriced) == 1 else 'are'} not in this hospital's published "
            f"file. That amount is in the billed total above and in neither of the other "
            f"two figures, so the three do not subtract into one another.</div>",
            unsafe_allow_html=True)

    # Change 2 held the line on the headline excluding quantity exposure and predicted that a
    # future version would need to carry a second figure. A tester then ran the Doylestown
    # sample, read "$288.70" beside a quantity warning worth $14,445, and said the
    # explanation "appears farther down" - which it did, on the card. The prediction was
    # right and the cost was real, so the figure now sits with the totals it qualifies.
    exposure = round(sum(line_quantity_exposure(l) for l in reviewed["lines"]), 2)
    if exposure > 0:
        n_qty = sum(1 for l in reviewed["lines"] if line_quantity_exposure(l) > 0)
        st.markdown(
            f"<div class='unpriced'><b>A further {money(exposure)} is in question if the "
            f"quantities are wrong</b> — {n_qty} line(s) below "
            f"{'is' if n_qty == 1 else 'are'} billed many times over for something a single "
            f"visit does not usually repeat. That amount is <b>not</b> in the unexplained "
            f"difference above, which counts only what was charged beyond the posted rate "
            f"per unit.</div>",
            unsafe_allow_html=True)

    flagged = [l for l in reviewed["lines"] if l["verdict"] != "VERIFIED"]
    verified = [l for l in reviewed["lines"] if l["verdict"] == "VERIFIED"]

    if flagged:
        biggest = max(reviewed["lines"], key=lambda l: l.get("charged") or 0)
        share = (biggest["charged"] / reviewed["billed_total"] * 100
                 if reviewed["billed_total"] else 0)
        if share >= 50 and biggest["verdict"] != "VERIFIED":
            # A line this size now leads the list, so the pointer can simply say so.
            where = " It is the first question below."
            st.markdown(
                f"<div class='dominant'><b>One line is {share:.0f}% of this bill.</b> "
                f"Code <b>{biggest['code']}</b>, at {money(biggest['charged'])}, is flagged "
                f"below.{where} If you only have time for one question, make it that one."
                f"</div>", unsafe_allow_html=True)
        st.markdown(action_list(reviewed), unsafe_allow_html=True)
        st.markdown(f"<div class='sectlabel'>{len(flagged)} of {len(reviewed['lines'])} lines "
                    f"need attention</div>", unsafe_allow_html=True)
        # Worst first, same order as the questions, so card N answers question N.
        for line in flagged_in_reading_order(reviewed):
            st.markdown(verdict_card(line, reviewed['billed_total']), unsafe_allow_html=True)
    else:
        st.success("Every line matches the price this hospital publishes. Nothing to dispute.")

    if verified:
        with st.expander(f"{len(verified)} line(s) matched the published price"):
            for line in verified:
                st.markdown(verdict_card(line, reviewed['billed_total']), unsafe_allow_html=True)

    st.markdown("<div class='step'><div class='n'>4</div><div class='t'>In plain language"
                "</div></div>", unsafe_allow_html=True)
    if narrative:
        st.write(plain(narrative))
        if unpriced:
            st.info(unpriced_sentence(unpriced, unpriced_total))
        st.caption("Written automatically, then checked line by line against the figures "
                   "above. Any sentence citing an amount we could not verify is discarded "
                   "rather than shown.")
    else:
        st.write(plain(
            f"This statement bills {money(reviewed['billed_total'])}. Against the prices "
            f"{hospital} publishes, {money(reviewed['posted_total'])} is accounted for, "
            f"leaving {money(reviewed['questionable_amount'])} across {len(flagged)} "
            "line(s) worth raising with the billing office."))
        if unpriced:
            st.info(unpriced_sentence(unpriced, unpriced_total))
        st.caption("Written from the verified figures directly. The automatic summary was "
                   "withheld because it could not be checked against them.")

    if letter:
        st.subheader("A letter you can send")
        st.caption("Addressed to the billing office, citing this hospital's own published "
                   "prices line by line. Read it before sending.")
        with st.expander("Read the letter"):
            st.write(plain(letter))
        st.download_button("Download the letter", data=letter,
                           file_name="itemized-dispute-letter.txt",
                           use_container_width=True)

    st.download_button(
        "Download this review",
        data="\n".join(
            [f"ITEMIZED — bill review", f"Hospital: {hospital}", "",
             f"Billed:                 {money(reviewed['billed_total'])}",
             f"Hospital posts:         {money(reviewed['posted_total'])}",
             f"Unexplained difference: {money(reviewed['questionable_amount'])}",
             "",
             "The unexplained difference is a gap to ask the billing office about. It is",
             "not an amount you are owed and not a refund estimate. Where a quantity is in",
             "question, the posted figure above uses the quantity as billed.", ""]
            + ([unpriced_sentence(unpriced, unpriced_total), ""] if unpriced else []) +
            [f"{l['code']:>8}  qty {l['units']:>3}  {money(l['charged']):>12}  "
             f"{VERDICTS.get(l['verdict'], ('',))[0]}" for l in reviewed["lines"]]),
        file_name="itemized-review.txt", use_container_width=True)


st.divider()
st.caption(
    "Itemized compares a bill against the standard-charge file each hospital must publish "
    "under federal rule 45 CFR 180. A flagged line means the amount billed does not match "
    "the price that hospital publishes — it is not proof of fraud, and this is not legal or "
    "financial advice. Sample statements are synthetic.")
