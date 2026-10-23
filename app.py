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


def _load_credentials():
    """Promote credentials into the environment, where the pipeline modules read them.

    On Streamlit Cloud they arrive as secrets; locally they come from .env. st.secrets is
    only touched when a secrets file exists, because reading it without one renders a
    warning onto the page itself.
    """
    secret_files = [HERE / ".streamlit" / "secrets.toml",
                    pathlib.Path.home() / ".streamlit" / "secrets.toml"]
    if any(p.exists() for p in secret_files):
        for key in ("GROQ_API_KEY", "GROQ_MODEL", "GROQ_POOL", "LLM_PROVIDER"):
            try:
                if key in st.secrets:
                    os.environ.setdefault(key, str(st.secrets[key]))
            except Exception:
                pass

    env = HERE / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("\"'"))

    os.environ.setdefault("LLM_PROVIDER", "groq")
    # groq.py falls back to llama-3.3-70b-versatile, which this project's key cannot reach —
    # it returns 404 model_not_found. Default to models the key actually serves, so the app
    # needs only GROQ_API_KEY to run and cannot be broken by a missing optional secret.
    os.environ.setdefault("GROQ_MODEL", "openai/gpt-oss-120b")
    os.environ.setdefault("GROQ_POOL",
                          "openai/gpt-oss-120b,openai/gpt-oss-20b,qwen/qwen3.8-27b")


_load_credentials()


def credential_problem():
    """Tell a missing key apart from a failing service, so the error can say which."""
    key = os.environ.get("GROQ_API_KEY", "")
    if not key:
        return "no key is configured"
    if "…" in key or key.strip() in {"gsk_…", "gsk_...", "your-key"}:
        return "the key is still the placeholder text"
    if not key.startswith("gsk_") or len(key) < 40:
        return "the key does not look like a Groq key"
    return None

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
:root{
  --ink:#121620; --muted:#5A6373; --faint:#8A93A3; --line:#E3E8F0;
  --bad:#B4322B; --warn:#9A6B0E; --ok:#15804F; --info:#4A5568; --accent:#1E4FA3;
  --bad-bg:#FDF3F2; --warn-bg:#FDF8EC; --ok-bg:#F1F8F4; --info-bg:#F5F7FA;
}
.block-container{max-width:880px;padding-top:2.2rem}

/* ---- masthead ---------------------------------------------------------- */
.mast{border-bottom:3px solid var(--ink);padding-bottom:16px;margin-bottom:6px}
.mast h1{font-size:40px;letter-spacing:-1.1px;line-height:1;margin:0 0 9px;
  font-weight:700;color:var(--ink)}
.mast .sub{font-size:16.5px;color:var(--muted);line-height:1.5;max-width:62ch;margin:0}
.badge{display:inline-block;font-size:11px;letter-spacing:.9px;text-transform:uppercase;
  font-weight:700;color:var(--accent);background:#EAF0FB;border-radius:999px;
  padding:4px 11px;margin-bottom:11px}

/* ---- step headers ------------------------------------------------------ */
.step{display:flex;align-items:center;gap:10px;margin:26px 0 10px}
.step .n{width:23px;height:23px;border-radius:50%;background:var(--ink);color:#fff;
  font-size:12.5px;font-weight:700;display:flex;align-items:center;justify-content:center;
  flex:0 0 23px}
.step .t{font-size:18px;font-weight:700;color:var(--ink)}

/* ---- the headline figures --------------------------------------------- */
.totals{display:flex;gap:0;border:1px solid var(--line);border-radius:12px;
  overflow:hidden;margin:4px 0 10px;background:#fff}
.totals .cell{flex:1;padding:15px 17px;border-right:1px solid var(--line)}
.totals .cell:last-child{border-right:none;background:#FBF8F2}
.totals .k{font-size:11.5px;letter-spacing:.5px;text-transform:uppercase;
  color:var(--faint);font-weight:700;margin-bottom:5px}
.totals .v{font-size:25px;font-weight:700;color:var(--ink);letter-spacing:-.6px;
  line-height:1.1}
.totals .cell.hero .v{color:#8A5A00;font-size:29px}
.totals .n{font-size:11.5px;color:var(--muted);margin-top:5px;line-height:1.4}

/* ---- what to do next --------------------------------------------------- */
.todo{border:1px solid #D6E0F2;background:#F7FAFF;border-radius:12px;
  padding:16px 18px;margin:14px 0 6px}
.todo .h{font-size:13px;letter-spacing:.6px;text-transform:uppercase;font-weight:700;
  color:var(--accent);margin-bottom:9px}
.todo ol{margin:0;padding-left:19px}
.todo li{font-size:14.5px;color:var(--ink);line-height:1.55;margin-bottom:7px}
.todo li:last-child{margin-bottom:0}
.todo .tail{font-size:12.5px;color:var(--muted);margin:10px 0 0;line-height:1.5}

/* ---- line cards -------------------------------------------------------- */
.verdict{border:1px solid var(--line);border-left-width:4px;border-radius:10px;
  padding:13px 16px;margin-bottom:9px;background:#fff}
.verdict.ok{border-left-color:var(--ok);background:var(--ok-bg)}
.verdict.bad{border-left-color:var(--bad);background:var(--bad-bg)}
.verdict.warn{border-left-color:var(--warn);background:var(--warn-bg)}
.verdict.info{border-left-color:var(--info);background:var(--info-bg)}
.verdict .top{display:flex;justify-content:space-between;gap:12px;align-items:baseline}
.verdict .label{font-weight:700;font-size:14.5px;color:var(--ink)}
.verdict.ok .label{color:var(--ok)}
.verdict.bad .label{color:var(--bad)}
.verdict.warn .label{color:var(--warn)}
.verdict .amt{font-weight:700;font-size:15.5px;white-space:nowrap;color:var(--ink)}
.verdict .desc{font-size:13.5px;color:var(--ink);margin-top:4px}
.verdict .code{font-family:'SF Mono',Menlo,monospace;font-size:12.5px;color:var(--muted)}
.verdict .why{font-size:13px;color:var(--muted);margin-top:5px;line-height:1.5}
.verdict .figs{font-size:12.5px;color:var(--muted);margin-top:7px;
  padding-top:7px;border-top:1px dashed var(--line)}
.verdict .figs b{color:var(--ink);font-weight:700}
.verdict .src{font-size:12px;margin-top:6px}
.verdict .src a{color:var(--accent);text-decoration:none;border-bottom:1px solid #C3D4EE}
.verdict .src .meta{color:var(--faint)}

.sectlabel{font-size:12px;letter-spacing:.8px;text-transform:uppercase;font-weight:700;
  color:var(--faint);margin:20px 0 8px}
.lede{font-size:17px;color:var(--muted);line-height:1.55}
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


def validate(text, hospital):
    """Return a plain-English problem, or None when the bill is good enough to run."""
    if not text or not text.strip():
        return ("Paste a bill first",
                "There is nothing to check yet. Pick one of the sample bills, or paste the "
                "text of your own itemized statement into the box.")
    if len(text.strip()) < 60:
        return ("That looks too short to be a bill",
                "An itemized statement usually lists several lines, each with a code, a "
                "quantity and an amount. Paste the whole statement rather than one line.")
    # A CPT code is five digits, a HCPCS code a letter and four. The looser \d{3,5} this
    # used to match also matched "310" inside "4,310.00", so a summary bill carrying any
    # dollar amount sailed past the billing-code check and failed later with a less useful
    # message. The documented example below was unreachable for the input it describes.
    if not re.search(r"\b([A-Z]\d{4}|\d{5})\b", text):
        return ("No billing codes found",
                "Every line on an itemized bill carries a procedure code, like 74177 or "
                "J1885. None were found here — this may be a summary bill rather than an "
                "itemized one. Ask the hospital for the itemized statement.")
    if hospital is None:
        return ("We do not have this hospital's published prices",
                "This tool compares your bill against prices a hospital publishes, and it "
                "currently holds files for nine hospitals. Pick the hospital from the list "
                "below if it is there, or try one of the sample bills to see how it works.")
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
    "ABOVE_POSTED": lambda l: (
        f"Ask why code <b>{l['code']}</b> was billed {money(l['charged'])} when this hospital "
        f"publishes {money(l['posted_price'])} for it"
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


def action_list(reviewed):
    """The questions to put to the billing office, which is what this tool claims to produce.

    The positioning settled on after user testing is that Itemized turns a bill into specific
    sourced questions. Both testers independently wrote their own version of this list at the
    end of their session; the interface was making them do that work by hand.
    """
    flagged = sorted((l for l in reviewed["lines"] if l["verdict"] != "VERIFIED"),
                     key=lambda l: (ORDER.get(l["verdict"], 9), -(l.get("charged") or 0)))
    if not flagged:
        return ""
    items = "".join(f"<li>{ASKS[l['verdict']](l)}</li>" for l in flagged if l["verdict"] in ASKS)
    return ("<div class='todo'><div class='h'>What to ask the billing office</div>"
            f"<ol>{items}</ol>"
            "<p class='tail'>Take the figures with you. Every one above comes from the price "
            "file this hospital publishes itself, linked under each line below.</p></div>")


def verdict_card(line):
    label, tone, why = VERDICTS.get(
        line["verdict"], ("Checked", "info", ""))
    posted = line.get("posted_price")
    posted_bit = (f"<div class='figs'>This hospital publishes <b>{money(posted)}</b> "
                  f"for this code"
                  + (f" · Medicare pays {money(line['medicare_payment'])}"
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
        beyond_rate = round(at_one - over, 2)         # the part the headline does not hold
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
    src = line.get("source_url") or ""
    src_bit = source_line(src) if src else ""
    # Emitted as one line: a blank line inside raw HTML ends the block for Streamlit's
    # markdown parser, which then prints the trailing tags as text.
    return (
        f"<div class='verdict {tone}'>"
        f"<div class='top'><span class='label'>{label}</span>"
        f"<span class='amt'>{money(line['charged'])}</span></div>"
        f"<div class='desc'>{line.get('description','')}"
        f"<span class='code'> · {line['code']} · qty {line['units']}</span></div>"
        f"<div class='why'>{why}</div>{posted_bit}{qty_bit}{src_bit}"
        f"</div>")


st.markdown(CSS, unsafe_allow_html=True)
st.markdown(
    "<div class='mast'>"
    "<span class='badge'>45 CFR 180 · published prices</span>"
    "<h1>Itemized</h1>"
    "<p class='sub'>Check every line of a hospital bill against the prices that hospital is "
    "legally required to publish — so you know what to ask before you pay.</p>"
    "</div>", unsafe_allow_html=True)

st.markdown("<div class='step'><div class='n'>1</div><div class='t'>Your bill</div></div>",
            unsafe_allow_html=True)
tab_sample, tab_paste = st.tabs(["Use a sample bill", "Paste my own bill"])

with tab_sample:
    st.caption("No bill handy? These are realistic statements you can try the tool on.")
    choice = st.selectbox(
        "Sample statement",
        SAMPLES,
        format_func=sample_label,
        key="sample_choice")
    sample_text = choice.read_text() if choice else ""
    with st.expander("See the statement"):
        st.code(sample_text, language=None)
        st.caption("Shown exactly as it arrives. Hospitals issue itemized statements in "
                   "very different formats — some print tidy columns, others export "
                   "delimited text — and the reader is built to handle both.")

with tab_paste:
    pasted = st.text_area(
        "Paste the text of your itemized statement",
        height=220, placeholder="Copy everything from your itemized bill and paste it here…",
        help="An itemized statement lists each charge with its procedure code, quantity "
             "and amount. A summary bill showing only a total cannot be checked.",
        key="pasted_text")

bill_text = (st.session_state.get("pasted_text") or "").strip() or sample_text
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

    problem = validate(bill_text, hospital)
    if problem:
        title, detail = problem
        st.error(f"**{title}**\n\n{detail}")
        st.stop()

    try:
        with st.spinner("Reading the bill…"):
            from ai_reader import read_bill
            items, meta = read_bill(bill_text)
    except Exception:
        items, meta = [], {"ok": False, "error": "unreachable"}

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

    if not items:
        st.error("**No charges could be made out**\n\nThe statement came through, but no "
                 "line items could be read from it. Paste the itemized section — the rows "
                 "with a code, a quantity and an amount — rather than the summary page.")
        st.stop()

    from checker import check_bill
    reviewed = check_bill({"hospital_name": hospital, "line_items": items}, reference_table())

    with st.spinner("Writing your summary…"):
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
    }

result = st.session_state.get("result")

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

    st.markdown(
        "<div class='totals'>"
        f"<div class='cell'><div class='k'>You were billed</div>"
        f"<div class='v'>{money(reviewed['billed_total'])}</div></div>"
        f"<div class='cell'><div class='k'>This hospital publishes</div>"
        f"<div class='v'>{money(reviewed['posted_total'])}</div>"
        f"<div class='n'>For the same lines, at the quantities billed.</div></div>"
        f"<div class='cell hero'><div class='k'>Unexplained difference</div>"
        f"<div class='v'>{money(reviewed['questionable_amount'])}</div>"
        f"<div class='n'>A gap to ask about — not money you are owed, "
        f"and not a refund estimate.</div></div>"
        "</div>", unsafe_allow_html=True)

    flagged = [l for l in reviewed["lines"] if l["verdict"] != "VERIFIED"]
    verified = [l for l in reviewed["lines"] if l["verdict"] == "VERIFIED"]

    if flagged:
        st.markdown(action_list(reviewed), unsafe_allow_html=True)
        st.markdown(f"<div class='sectlabel'>{len(flagged)} of {len(reviewed['lines'])} lines "
                    f"need attention</div>", unsafe_allow_html=True)
        # Worst first. Nine cards in bill order buries the ones worth reading.
        for line in sorted(flagged, key=lambda l: (ORDER.get(l["verdict"], 9),
                                                   -(l.get("charged") or 0))):
            st.markdown(verdict_card(line), unsafe_allow_html=True)
    else:
        st.success("Every line matches the price this hospital publishes. Nothing to dispute.")

    if verified:
        with st.expander(f"{len(verified)} line(s) matched the published price"):
            for line in verified:
                st.markdown(verdict_card(line), unsafe_allow_html=True)

    st.markdown("<div class='step'><div class='n'>4</div><div class='t'>In plain language"
                "</div></div>", unsafe_allow_html=True)
    if narrative:
        st.write(plain(narrative))
        st.caption("Written automatically, then checked line by line against the figures "
                   "above. Any sentence citing an amount we could not verify is discarded "
                   "rather than shown.")
    else:
        st.write(plain(
            f"This statement bills {money(reviewed['billed_total'])}. Against the prices "
            f"{hospital} publishes, {money(reviewed['posted_total'])} is accounted for, "
            f"leaving {money(reviewed['questionable_amount'])} across {len(flagged)} "
            "line(s) worth raising with the billing office."))
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
             "question, the posted figure above uses the quantity as billed.", ""] +
            [f"{l['code']:>8}  qty {l['units']:>3}  {money(l['charged']):>12}  "
             f"{VERDICTS.get(l['verdict'], ('',))[0]}" for l in reviewed["lines"]]),
        file_name="itemized-review.txt", use_container_width=True)


st.divider()
st.caption(
    "Itemized compares a bill against the standard-charge file each hospital must publish "
    "under federal rule 45 CFR 180. A flagged line means the amount billed does not match "
    "the price that hospital publishes — it is not proof of fraud, and this is not legal or "
    "financial advice. Sample statements are synthetic.")
