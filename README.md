# 🎯 PM / PO Job Radar — Bengaluru

Watches companies' **own ATS feeds** (Greenhouse, Lever, Ashby, Workday) for new
**Product Owner / Product Manager** roles in Bengaluru, alerts you the moment one
appears, and shows everything in a Streamlit dashboard.

**How it stays real-time without you doing anything:** a GitHub Action runs the
radar on a schedule (hourly by default), pushes a Slack/Telegram alert for any
*new* match, and commits the latest list into `data/jobs.json`. The Streamlit app
reads that file — so the dashboard is always fresh even while it's asleep.

```
radar.py    engine: fetch ATS feeds, filter, dedup, notify
app.py      Streamlit dashboard (reads data/jobs.json + live "Refresh now")
companies.json   your target list  ← EDIT THIS
data/       jobs.json (current matches) + seen.json (dedup state)
.github/workflows/radar.yml   the hourly scheduler + alerter
```

---

## 1. Edit your target list

Open `companies.json`. Each company is one line. Find a company's **slug** from
its careers URL:

| ATS | careers URL looks like | entry |
|-----|------------------------|-------|
| Greenhouse | `boards.greenhouse.io/sarvam` | `{"ats":"greenhouse","slug":"sarvam"}` |
| Lever | `jobs.lever.co/acme` | `{"ats":"lever","slug":"acme"}` |
| Ashby | `jobs.ashbyhq.com/acme` | `{"ats":"ashby","slug":"acme"}` |
| Workday | `acme.wd3.myworkdayjobs.com/External` | `{"ats":"workday","tenant":"acme","wd":"wd3","site":"External"}` |
| Eightfold | `acme.eightfold.ai/careers` | `{"ats":"eightfold","company":"acme","domain":"acme.com"}` |
| **JSearch** | (job boards via Google for Jobs) | `{"ats":"jsearch","query":"product manager OR product owner in Bengaluru, India","country":"in","date_posted":"week"}` |

**Job boards (LinkedIn / Indeed / Glassdoor / Naukri)** don't have free open APIs and forbid
scraping. Instead, the `jsearch` source pulls them all in one call via Google for Jobs.
It needs a free RapidAPI key — sign up at rapidapi.com, subscribe to **JSearch**, copy your
key, and set it as `RAPIDAPI_KEY` (in secrets locally / repo secret on GitHub). Keep
`num_pages` at 1 and `date_posted` at `week` to stay inside the free quota.

> Niche Indian boards (Instahyre, IIMjobs, Talent500) have no public API — use each one's
> own saved-search **email alert** instead (set once, free). Much of Naukri/IIMjobs also
> shows up through `jsearch` already.

The seeded entries are **examples — replace them.** Unknown/dead slugs are skipped
safely (they show up under "source errors").

To change *what* it matches, edit `DEFAULT_TITLE` / `DEFAULT_LOC` at the top of
`radar.py` (they're regexes).

---

## 2. Run locally

```bash
pip install -r requirements.txt
python radar.py          # one scrape: prints counts, writes data/, sends alerts if configured
streamlit run app.py     # opens the dashboard at http://localhost:8501
```

For local alerts, copy `.streamlit/secrets.toml.example` → `.streamlit/secrets.toml`
and fill it in (or `export SLACK_WEBHOOK_URL=...` before `python radar.py`).

---

## 3. Push to GitHub

```bash
git init
git add .
git commit -m "PM/PO job radar"
git branch -M main
git remote add origin https://github.com/<you>/pm-po-radar.git
git push -u origin main
```

A **public** repo is simplest (Community Cloud allows only one private app at a time).

---

## 4. Publish the dashboard on Streamlit Community Cloud (free)

1. Go to **share.streamlit.io**, sign in with GitHub, authorize access.
2. Click **Create app → "Yup, I have an app."**
3. Repository `you/pm-po-radar`, branch `main`, main file `app.py`. Optionally set a
   custom subdomain. Click **Deploy**.
4. Live in a few minutes at `https://<subdomain>.streamlit.app`. Pushes to `main`
   redeploy automatically.

> Note: free apps **sleep after 12h without traffic** — that's fine, because the
> GitHub Action (next step) is what does the round-the-clock watching. The app is
> just the viewer.

---

## 5. Turn on real-time alerts (GitHub Actions)

The workflow `.github/workflows/radar.yml` is already in the repo. It runs hourly,
alerts on new matches, and commits the refreshed `data/`.

Add your alert credentials as repo secrets — **Settings → Secrets and variables →
Actions → New repository secret:**

- `SLACK_WEBHOOK_URL` — create one at api.slack.com → *Incoming Webhooks*, **or**
- `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` — from @BotFather and @userinfobot.

Then **Actions tab → pm-po-radar → Run workflow** to test immediately. After that
it runs on its own. First run marks everything currently open as "seen" silently;
only genuinely new postings ping you afterward.

**Cadence:** edit the `cron` in `radar.yml`. Default is `0 */4 * * *` (every 4h).
**Watch the JSearch quota:** the free tier is 200 calls/month and each run makes one
`jsearch` call (plus free, unlimited ATS calls). Every 4h ≈ 180/month — fits. Hourly
≈ 720/month and will exhaust it in ~8 days. Each "Refresh live now" click is also one
call. Basic is hard-capped, so you can't be overage-charged — it just stops returning
board results until the month resets.

---

## Reality check on "as soon as it's live"

ATS feeds carry a job the instant the req opens, but nothing *pushes* it to you —
so your true latency is the gap to the next scheduled run. Poll faster for tighter
latency; there's no setting that beats "poll + diff." Workday is the highest-value
source for GCC/enterprise targets (it's where Fresenius lives); Greenhouse/Lever/
Ashby cover the startup/scaleup tier.
