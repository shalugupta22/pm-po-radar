"""
radar.py — the engine.

Polls public ATS feeds (Greenhouse / Lever / Ashby / Workday), filters to
Product Owner / Product Manager roles in Bengaluru, dedups against a saved
state file, and (optionally) alerts Slack / Telegram.

Used in two places:
  * GitHub Actions (radar.py run on a cron) -> persists state + sends alerts
  * the Streamlit app (live "Refresh now" -> persist=False, just displays)
"""

import os
import re
import json
import pathlib
import requests

# ---- ATS endpoints (all public, no auth) ---------------------------------
GREENHOUSE = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
LEVER      = "https://api.lever.co/v0/postings/{slug}?mode=json"
ASHBY      = "https://api.ashbyhq.com/posting-api/job-board/{slug}"
JSEARCH    = "https://jsearch.p.rapidapi.com"          # Google-for-Jobs aggregator (use /search-v2)
EIGHTFOLD  = "https://{company}.eightfold.ai/api/apply/v2/jobs"  # SmartApply public feed

DEFAULT_TITLE = r"product\s+(owner|manager)s?"
# India-eligible only: city/India terms, or genuinely-global remote.
# Bare "remote" is intentionally NOT here — it matches "US - Remote" / "Netherlands - Remote"
# which require local work authorization and can't be done from India.
DEFAULT_LOC   = (r"bengaluru|bangalore|karnataka|\bindia\b|\bind\b|"
                 r"work from anywhere|anywhere in the world|globally remote|fully remote, india")

UA = {"User-Agent": "pm-po-radar/1.0"}
# Workday sits behind Akamai and 500s/403s non-browser agents — use a realistic one.
BROWSER_UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}


# ---- tiny json helpers ---------------------------------------------------
def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, obj):
    pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)


# ---- description helpers (used for resume matching) ----------------------
import html as _html


def _strip_html(s):
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)
    s = _html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def _clip(s, n=4000):
    return (s or "")[:n]


# ---- per-ATS fetchers (each yields a normalized dict) --------------------
def fetch_greenhouse(c):
    r = requests.get(GREENHOUSE.format(slug=c["slug"]), headers=UA, timeout=30)
    r.raise_for_status()
    for j in r.json().get("jobs", []):
        yield {
            "id": f"gh:{c['slug']}:{j['id']}",
            "company": c.get("name", c["slug"]), "source": "greenhouse",
            "title": j.get("title", ""),
            "location": (j.get("location") or {}).get("name", ""),
            "url": j.get("absolute_url", ""),
            "updated": j.get("updated_at", ""),
            "desc": _clip(_strip_html(j.get("content", ""))),
        }


def fetch_lever(c):
    r = requests.get(LEVER.format(slug=c["slug"]), headers=UA, timeout=30)
    r.raise_for_status()
    for j in r.json():
        yield {
            "id": f"lv:{c['slug']}:{j['id']}",
            "company": c.get("name", c["slug"]), "source": "lever",
            "title": j.get("text", ""),
            "location": (j.get("categories") or {}).get("location", ""),
            "url": j.get("hostedUrl", ""),
            "updated": j.get("createdAt", ""),
            "desc": _clip(j.get("descriptionPlain") or _strip_html(j.get("description", ""))),
        }


def fetch_ashby(c):
    r = requests.get(ASHBY.format(slug=c["slug"]), headers=UA, timeout=30)
    r.raise_for_status()
    for j in r.json().get("jobs", []):
        if j.get("isListed") is False:
            continue
        yield {
            "id": f"ab:{c['slug']}:{j.get('id')}",
            "company": c.get("name", c["slug"]), "source": "ashby",
            "title": j.get("title", ""),
            "location": j.get("location", ""),
            "url": j.get("jobUrl", ""),
            "updated": j.get("publishedAt", ""),
            "desc": _clip(j.get("descriptionPlain") or _strip_html(j.get("descriptionHtml", ""))),
        }


def fetch_workday(c):
    # tenant / wd / site all come straight from the company's careers URL, e.g.
    # https://freseniusmedicalcare.wd3.myworkdayjobs.com/fme
    #          tenant ^^^^^^^^^^^^^^^^^^^^   wd ^^^   site ^^^
    host = f"https://{c['tenant']}.{c['wd']}.myworkdayjobs.com"
    cxs  = f"{host}/wday/cxs/{c['tenant']}/{c['site']}/jobs"
    offset, limit, total = 0, 20, None
    while True:
        r = requests.post(cxs,
                          json={"appliedFacets": {}, "limit": limit,
                                "offset": offset, "searchText": ""},
                          headers={**BROWSER_UA, "Content-Type": "application/json"},
                          timeout=30)
        r.raise_for_status()
        data = r.json()
        postings = data.get("jobPostings", [])
        total = data.get("total", 0) if total is None else total
        for j in postings:
            path = j.get("externalPath", "")
            yield {
                "id": f"wd:{c['tenant']}:{path}",
                "company": c.get("name", c["tenant"]), "source": "workday",
                "title": j.get("title", ""),
                "location": j.get("locationsText", ""),
                "url": f"{host}/{c['site']}{path}",
                "updated": j.get("postedOn", ""),
                "desc": "",  # Workday listing has no description; match falls back to title
            }
        offset += limit
        if not postings or offset >= total:
            break


def _find_jobs_list(payload):
    """Return the first list-of-dicts that looks like jobs, wherever it's nested.
    Handles both the old flat `data: [...]` and v2's wrapped shapes."""
    found = []

    def walk(x):
        if isinstance(x, list):
            if x and all(isinstance(i, dict) for i in x):
                found.append(x)
            for i in x:
                walk(i)
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)

    walk(payload)
    jobish = {"job_id", "job_title", "employer_name", "job_apply_link", "title"}
    for lst in found:
        keys = set().union(*[set(d.keys()) for d in lst[:3]])
        if jobish & keys:
            return lst
    return found[0] if found else []


def fetch_jsearch(c):
    # One source -> LinkedIn + Indeed + Glassdoor + Naukri + more, via Google for Jobs.
    # Needs a (free) RapidAPI key in env: RAPIDAPI_KEY.
    key = os.getenv("RAPIDAPI_KEY")
    if not key:
        raise RuntimeError("RAPIDAPI_KEY not set — needed for the 'jsearch' source")
    headers = {"X-RapidAPI-Key": key, "X-RapidAPI-Host": "jsearch.p.rapidapi.com"}
    params = {
        "query": c.get("query", "product manager OR product owner in Bengaluru, India"),
        "country": c.get("country", "in"),
        "date_posted": c.get("date_posted", "week"),  # all | today | 3days | week | month
    }
    resp = None
    for path in ("/search-v2", "/search"):
        p = dict(params)
        if path == "/search":
            p.update({"page": "1", "num_pages": str(c.get("num_pages", 1))})
        r = requests.get(JSEARCH + path, headers=headers, params=p, timeout=30)
        if r.status_code == 404:
            continue
        r.raise_for_status()
        resp = r
        break
    if resp is None:
        raise RuntimeError("jsearch: neither /search-v2 nor /search responded (404)")

    payload = resp.json()
    if isinstance(payload, dict) and payload.get("status") == "ERROR":
        raise RuntimeError(f"jsearch API error: {payload.get('error')}")

    for j in _find_jobs_list(payload):
        if not isinstance(j, dict):
            continue
        country = j.get("job_country") or ""
        country = "India" if country.upper() == "IN" else country
        loc = (", ".join(x for x in [j.get("job_city"), j.get("job_state"), country] if x)
               or j.get("job_location") or "")
        yield {
            "id": f"js:{j.get('job_id') or j.get('id')}",
            "company": j.get("employer_name") or j.get("company") or "",
            "source": (j.get("job_publisher") or "jsearch").lower(),  # e.g. linkedin / naukri
            "title": j.get("job_title") or j.get("title") or j.get("name") or "",
            "location": loc,
            "url": (j.get("job_apply_link") or j.get("apply_link")
                    or j.get("job_google_link") or j.get("url") or ""),
            "updated": j.get("job_posted_at_datetime_utc") or j.get("job_posted_at") or "",
            "desc": _clip(j.get("job_description") or ""),
        }


def fetch_eightfold(c):
    # Eightfold-powered career sites expose a public "SmartApply" feed, no auth.
    # company + domain come from the careers URL, e.g. aexp.eightfold.ai -> company="aexp",
    # domain="aexp.com". (A few tenants use the newer "PCSX" API instead — if a company
    # returns nothing here, that's why; route those via the Apify Eightfold actor.)
    company = c["company"]
    domain  = c.get("domain", f"{company}.com")
    base    = f"https://{company}.eightfold.ai"
    start, num = 0, 100
    while True:
        params = {"domain": domain, "hl": "en", "start": start, "num": num}
        r = requests.get(EIGHTFOLD.format(company=company), params=params,
                         headers={"Accept": "application/json"}, timeout=30)
        r.raise_for_status()
        data = r.json()
        positions = data.get("positions") or []
        total = data.get("totalJobs", 0)
        for j in positions:
            jid  = j.get("id")
            locs = j.get("locations") or ([j["location"]] if j.get("location") else [])
            yield {
                "id": f"ef:{company}:{jid}",
                "company": c.get("name", company), "source": "eightfold",
                "title": j.get("name", ""),
                "location": "; ".join(x for x in locs if x),
                "url": j.get("canonicalPositionUrl") or f"{base}/careers/job/{jid}",
                "updated": j.get("t_update", ""),
                "desc": _clip(_strip_html(j.get("job_description") or "")),
            }
        start += num
        if not positions or start >= total or start >= 1000:
            break


FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "workday": fetch_workday,
    "jsearch": fetch_jsearch,
    "eightfold": fetch_eightfold,
}


# ---- notifications -------------------------------------------------------
def _fmt(jobs):
    lines = [f"*{len(jobs)} new PM/PO role(s):*"]
    for j in jobs:
        lines.append(f"• <{j['url']}|{j['title']}> — {j['company']} "
                     f"({j['location']}) · {j['source']}")
    return "\n".join(lines)


def notify_slack(webhook, jobs):
    try:
        requests.post(webhook, json={"text": _fmt(jobs)}, timeout=15)
    except Exception as e:
        print("slack notify failed:", e)


def notify_telegram(token, chat_id, jobs):
    text = _fmt(jobs).replace("*", "")
    try:
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat_id, "text": text,
                            "disable_web_page_preview": True}, timeout=15)
    except Exception as e:
        print("telegram notify failed:", e)


# ---- the run -------------------------------------------------------------
def run_radar(companies, title_regex=DEFAULT_TITLE, loc_regex=DEFAULT_LOC,
              state_dir="data", persist=True, notify=True):
    import re
    title_pat = re.compile(title_regex, re.I)
    loc_pat   = re.compile(loc_regex, re.I)

    seen_path = os.path.join(state_dir, "seen.json")
    jobs_path = os.path.join(state_dir, "jobs.json")
    seen = set(load_json(seen_path, [])) if persist else set()

    matches, errors = [], []
    for c in companies:
        fn = FETCHERS.get(c.get("ats"))
        if not fn:
            errors.append(f"unknown ats: {c}")
            continue
        try:
            for job in fn(c):
                if title_pat.search(job["title"] or "") and loc_pat.search(job["location"] or ""):
                    matches.append(job)
        except Exception as e:
            who = c.get("slug") or c.get("tenant") or "?"
            errors.append(f"{c.get('ats')}:{who} -> {e}")

    new = [j for j in matches if j["id"] not in seen]

    if persist:
        save_json(jobs_path, matches)                 # current open matches
        seen.update(j["id"] for j in matches)
        save_json(seen_path, sorted(seen))            # remember to suppress repeats

    if notify and new:
        slack = os.getenv("SLACK_WEBHOOK_URL")
        tg_token = os.getenv("TELEGRAM_BOT_TOKEN")
        tg_chat  = os.getenv("TELEGRAM_CHAT_ID")
        if slack:
            notify_slack(slack, new)
        if tg_token and tg_chat:
            notify_telegram(tg_token, tg_chat, new)

    return {"matches": matches, "new": new, "errors": errors}


def _load_local_secrets(path=".streamlit/secrets.toml"):
    """For plain `python radar.py` runs: load .streamlit/secrets.toml into the
    environment if not already set. (Streamlit loads this automatically; the bare
    CLI does not, which is why a key in secrets.toml looked 'unset' from the CLI.)"""
    try:
        import tomllib  # stdlib on Python 3.11+
    except ModuleNotFoundError:
        return
    p = pathlib.Path(__file__).parent / path
    if not p.exists():
        return
    try:
        with open(p, "rb") as f:
            for k, v in tomllib.load(f).items():
                os.environ.setdefault(k, str(v))
    except Exception:
        pass


if __name__ == "__main__":
    _load_local_secrets()
    base = pathlib.Path(__file__).parent
    companies = load_json(base / "companies.json", [])
    res = run_radar(
        companies,
        title_regex=os.getenv("TITLE_REGEX", DEFAULT_TITLE),
        loc_regex=os.getenv("LOC_REGEX", DEFAULT_LOC),
        state_dir=str(base / "data"),
        persist=True, notify=True,
    )
    print(f"matches={len(res['matches'])} new={len(res['new'])} errors={len(res['errors'])}")
    for e in res["errors"]:
        print("ERR", e)
