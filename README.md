# Itemized

Check every line of a hospital bill against the prices that hospital is legally required to
publish, so you can dispute the difference before paying.

Streamlit front end over the Assignment 4 pipeline. The modules in `itemized/` are copied
unchanged from `Assignment4_Itemized/evidence/`, so the interface shows exactly what the
pipeline produces — there is no second implementation that can drift from it.

## Deploying to Streamlit Community Cloud

1. **Push this folder to a new public GitHub repo** (use the `LikhithNG` account):

   ```
   git init && git add -A && git commit -m "feat: Itemized Streamlit interface"
   git branch -M main
   git remote add origin https://github.com/LikhithNG/itemized-app.git
   git push -u origin main
   ```

   `.gitignore` already excludes `.env` and `.streamlit/secrets.toml`, so no key is pushed.
   Confirm with `git status` before the first commit.

2. **Create the app** at https://share.streamlit.io → *New app* → pick the repo, branch
   `main`, main file `app.py`.

3. **Add the key** under *Advanced settings → Secrets*, in TOML:

   ```toml
   GROQ_API_KEY = "your-key"
   LLM_PROVIDER = "groq"
   GROQ_MODEL = "openai/gpt-oss-120b"
   ```

4. **Deploy.** First build takes a few minutes. The public URL is what gets submitted and
   what testers open.

## Running locally

```
python3 -m streamlit run app.py
```

Reads credentials from a local `.env` (git-ignored) with the same keys as above.

## What the interface does

| Step | |
|---|---|
| Input | A sample statement, or the user's own pasted bill |
| Hospital | Read from the top of the bill; a dropdown appears only if detection fails |
| Check | Extracts line items, compares each to the hospital's published price |
| Output | Per-line verdict cards, a plain-language summary, and a dispute letter |

Every figure shown carries the URL of the price file it came from. Generated prose is
checked against the verified figures and discarded if it cites anything else.

## Data

`data/a3_reference_table.csv` — 262 rows, 9 hospitals, 6 states, collected in Assignment 3
from each hospital's published standard-charge file. Sample statements in `samples/` are
synthetic, built from the real published prices with planted anomalies.
