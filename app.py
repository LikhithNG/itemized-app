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
    "DUPLICATE": ("Billed more than once", "bad",
                  "The same code appears earlier on this bill."),
    "UNREADABLE": ("Could not read this line", "info",
                   "This line was missing a code, a quantity or an amount."),
}

CSS = """
<style>
.block-container{max-width:860px}
.verdict{border:1px solid #E4E7ED;border-left-width:5px;border-radius:10px;
  padding:13px 16px;margin-bottom:10px;background:#fff}
.verdict.ok{border-left-color:#15804F}
.verdict.bad{border-left-color:#B23A33}
.verdict.warn{border-left-color:#9A6B0E}
.verdict.info{border-left-color:#5D6575}
.verdict .top{display:flex;justify-content:space-between;gap:12px;align-items:baseline}
.verdict .code{font-family:'SF Mono',Menlo,monospace;font-size:13px;color:#5D6575}
.verdict .label{font-weight:700;font-size:15px}
.verdict .amt{font-weight:700;font-size:15px;white-space:nowrap}
.verdict .desc{font-size:13.5px;color:#161A22;margin-top:4px}
.verdict .why{font-size:13px;color:#5D6575;margin-top:5px;line-height:1.5}
.verdict .src{font-size:11.5px;color:#98A0AE;margin-top:7px;word-break:break-all}
.lede{font-size:17px;color:#5D6575;line-height:1.55}
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
    if not re.search(r"\b[A-Z]?\d{3,5}[A-Z]?\b", text):
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


def verdict_card(line):
    label, tone, why = VERDICTS.get(
        line["verdict"], ("Checked", "info", ""))
    posted = line.get("posted_price")
    posted_bit = (f"<div class='why'>Hospital publishes {money(posted)} for this code"
                  + (f" · Medicare pays {money(line['medicare_payment'])}"
                     if line.get("medicare_payment") else "")
                  + "</div>") if posted else ""
    qty_bit = ""
    if line["verdict"] == "QUANTITY_SUSPECT" and posted and line["units"] > 1:
        # Two testers read the flag and the posted figure as contradicting each other: the
        # tool called the quantity wrong, then showed a comparison built on that quantity.
        # The per-unit consequence was never stated anywhere, so the amount actually at
        # stake on these lines - by far the largest on both bills - went unmentioned.
        at_one = round(line["charged"] - posted, 2)
        qty_bit = (f"<div class='why'>Billed once, this line would be {money(posted)}. "
                   f"If the quantity is wrong, {money(at_one)} of this charge is in "
                   f"question. That is not part of the unexplained difference above, "
                   f"because the rate charged for each one is correct.</div>")
    src = line.get("source_url") or ""
    src_bit = f"<div class='src'>Source: {src}</div>" if src else ""
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
st.title("Itemized")
st.markdown(
    "<p class='lede'>Check every line of a hospital bill against the prices that hospital "
    "is legally required to publish — so you can dispute the difference before paying.</p>",
    unsafe_allow_html=True)
st.divider()

st.subheader("1 · Your bill")
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

st.subheader("2 · The hospital")
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

    st.subheader("What we found")
    a, b, c = st.columns(3)
    a.metric("You were billed", money(reviewed["billed_total"]))
    b.metric("This hospital posts", money(reviewed["posted_total"]),
             help="The hospital's published rate for each code, multiplied by the quantity "
                  "on the bill. Where a quantity is itself in question, this figure uses "
                  "the quantity as billed.")
    c.metric("Unexplained difference", money(reviewed["questionable_amount"]),
             help="The gap between what you were billed and what this hospital publishes, "
                  "on the lines flagged below. It is a figure to ask about — not an amount "
                  "you are owed, and not a refund estimate.")
    st.caption("**Unexplained difference** is the gap worth raising with the billing office. "
               "It is not money you are owed and not a refund estimate — what you actually "
               "pay depends on your insurance and on the rate that applies to you.")

    flagged = [l for l in reviewed["lines"] if l["verdict"] != "VERIFIED"]
    if flagged:
        st.warning(f"**{len(flagged)} of {len(reviewed['lines'])} lines need attention.** "
                   "Each one below says what it is and where the comparison price came from.")
    else:
        st.success("Every line matches the price this hospital publishes. Nothing to dispute.")

    for line in reviewed["lines"]:
        st.markdown(verdict_card(line), unsafe_allow_html=True)

    st.subheader("In plain language")
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
