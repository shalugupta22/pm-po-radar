"""
app.py — Streamlit dashboard for the PM/PO job radar.

Reads the feed that GitHub Actions keeps fresh (data/jobs.json), and also
lets you re-scrape live on demand. Deploy free on Streamlit Community Cloud.
"""

import pathlib
import datetime as dt
import os

import pandas as pd
import streamlit as st

from radar import run_radar, load_json, DEFAULT_TITLE, DEFAULT_LOC
from matching import match_score

# Make secrets available to radar.py / resume_gen.py (which read os.getenv).
try:
    for _k in ("RAPIDAPI_KEY", "SLACK_WEBHOOK_URL", "TELEGRAM_BOT_TOKEN",
               "TELEGRAM_CHAT_ID", "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL"):
        if _k in st.secrets:
            os.environ[_k] = str(st.secrets[_k])
except Exception:
    pass  # no secrets file locally — fine

BASE = pathlib.Path(__file__).parent
JOBS = BASE / "data" / "jobs.json"

def _load_resume(name):
    p = BASE / name
    return p.read_text(encoding="utf-8") if p.exists() else ""

RESUMES = {
    "Senior Product Manager": _load_resume("resume_pm.md"),
    "Senior Product Owner":   _load_resume("resume_po.md"),
}
# Use the PM resume for the on-screen match scoring (broader keyword coverage);
# generation can still use either profile via the buttons below.
RESUME = RESUMES["Senior Product Manager"] or RESUMES["Senior Product Owner"]

st.set_page_config(page_title="PM/PO Job Radar", page_icon="🎯", layout="wide")
st.title("🎯 PM / PO Job Radar — Bengaluru")

companies = load_json(BASE / "companies.json", [])

# --- pick data source: live (this session) or the committed feed ----------
feed = st.session_state.get("live_feed")
live = feed is not None
if feed is None:
    feed = load_json(JOBS, [])

with st.sidebar:
    st.header("Controls")
    n_js = sum(1 for c in companies if c.get("ats") == "jsearch")
    st.caption(f"Tracking {len(companies)} companies · {n_js} JSearch call(s) per refresh")

    skip_js = st.checkbox("💸 Skip JSearch (0 RapidAPI calls)", value=False,
                          help="When on, Refresh pulls only the free ATS sources "
                               "(Workday/Greenhouse/Eightfold) and makes NO RapidAPI call — "
                               "uses 0 of your 200 monthly JSearch quota. Use this while testing.")

    if st.button("🔄 Refresh live now", use_container_width=True):
        run_companies = ([c for c in companies if c.get("ats") != "jsearch"]
                         if skip_js else companies)
        spin = "Scraping ATS feeds (no JSearch)…" if skip_js else "Scraping ATS + boards…"
        with st.spinner(spin):
            res = run_radar(run_companies, persist=False, notify=False)
        st.session_state["live_feed"] = res["matches"]
        if res["errors"]:
            st.warning(f"{len(res['errors'])} source(s) errored — see expander below")
            st.session_state["errors"] = res["errors"]
        st.rerun()

    if live and st.button("↩︎ Back to scheduled feed", use_container_width=True):
        st.session_state.pop("live_feed", None)
        st.session_state.pop("errors", None)
        st.rerun()

    st.divider()
    st.subheader("Filters")
    query = st.text_input("Search title / company")
    known = ["greenhouse", "lever", "ashby", "workday", "eightfold",
             "jsearch", "linkedin", "indeed", "glassdoor", "naukri"]
    present = {j.get("source", "") for j in feed if j.get("source")}
    all_sources = sorted(set(known) | present)
    sources = st.multiselect("Source", all_sources, default=all_sources)

    max_age = st.slider("Max age (days)", 1, 90, 30, 1,
                        help="Hide postings older than this. Undated roles are kept.")

    min_match = 0
    if RESUME.strip():
        min_match = st.slider("Min match %", 0, 100, 0, 5,
                              help="Hide jobs below this resume-match score")
    else:
        st.caption("Add resume.md to enable match scoring + resume generation.")

# --- header line ----------------------------------------------------------
if live:
    st.info("Showing **live** results from just now (not saved).")
else:
    when = (dt.datetime.utcfromtimestamp(JOBS.stat().st_mtime).strftime("%Y-%m-%d %H:%M UTC")
            if JOBS.exists() else "never")
    st.caption(f"Scheduled feed · last updated {when}")

# --- render ---------------------------------------------------------------
def _fmt_posted(v):
    """Normalize the varied 'updated' formats (ISO, epoch ms/s, Workday text) to a date."""
    if v is None or v == "":
        return ""
    s = str(v)
    if s.isdigit():                       # epoch seconds or ms (lever/eightfold)
        ts = int(s)
        ts = ts / 1000 if ts > 1_000_000_000_000 else ts
        try:
            return dt.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
        except Exception:
            return s
    if len(s) >= 10 and s[4:5] == "-":     # ISO datetime (greenhouse/jsearch)
        return s[:10]
    return s                               # human text (workday "Posted 3 Days Ago")


def _age_days(v):
    """Best-effort age in days across all source date formats. None = unknown."""
    import re as _re
    if v is None or v == "":
        return None
    s = str(v).strip().lower()
    if s.isdigit():                                  # epoch (lever/eightfold)
        ts = int(s)
        ts = ts / 1000 if ts > 1_000_000_000_000 else ts
        try:
            return max(0, (dt.datetime.utcnow() - dt.datetime.utcfromtimestamp(ts)).days)
        except Exception:
            return None
    if any(k in s for k in ("today", "just posted", "hour ago", "hours ago",
                            "minute", "moment")):
        return 0
    if "yesterday" in s:
        return 1
    m = _re.search(r"(\d+)\s*\+?\s*day", s)           # "Posted 30+ Days Ago" / "29 days ago"
    if m:
        return int(m.group(1)) + (1 if "+" in s else 0)
    m = _re.search(r"(\d+)\s*\+?\s*week", s)
    if m:
        return int(m.group(1)) * 7
    m = _re.search(r"(\d+)\s*\+?\s*month", s)
    if m:
        return int(m.group(1)) * 30
    m = _re.search(r"(\d{4})-(\d{2})-(\d{2})", s)     # ISO date
    if m:
        try:
            d = dt.datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            return max(0, (dt.datetime.utcnow() - d).days)
        except Exception:
            return None
    return None


df = pd.DataFrame(feed)
if df.empty:
    st.warning("No matches yet. Hit **Refresh live now**, or wait for the "
               "GitHub Action to populate `data/jobs.json`.")
else:
    if sources:
        df = df[df["source"].isin(sources)]
    if query:
        mask = (df["title"].str.contains(query, case=False, na=False) |
                df["company"].str.contains(query, case=False, na=False))
        df = df[mask]

    df["posted"] = df["updated"].map(_fmt_posted)
    df["_age"] = df["updated"].map(_age_days)
    df = df[df["_age"].isna() | (df["_age"] <= max_age)]  # drop stale; keep undated

    scored = bool(RESUME.strip())
    if scored:
        # compute match %, matched/missing keywords per row
        results = df.apply(lambda r: match_score(RESUME, r.to_dict()), axis=1)
        df["match"] = [s for s, _, _ in results]
        df["_matched"] = [m for _, m, _ in results]
        df["_missing"] = [mi for _, _, mi in results]
        df = df[df["match"] >= min_match]
        df = df.sort_values(["match", "updated"], ascending=[False, False])
    else:
        df = df.sort_values("updated", ascending=False)

    st.metric("Open matches", len(df))

    cols = (["title", "company", "location", "posted", "match", "source", "url"]
            if scored else ["title", "company", "location", "posted", "source", "url"])
    colcfg = {
        "title": "Role", "company": "Company", "location": "Location",
        "posted": "Posted", "source": "ATS",
        "url": st.column_config.LinkColumn("Apply", display_text="Open →"),
    }
    if scored:
        colcfg["match"] = st.column_config.ProgressColumn(
            "Match", min_value=0, max_value=100, format="%d%%")
    st.dataframe(df[cols], use_container_width=True, hide_index=True, column_config=colcfg)

    # ---- per-job tailoring panel -----------------------------------------
    if scored and len(df):
        st.divider()
        st.subheader("🎯 Tailor a resume to a role")
        labels = [f"{int(r['match'])}%  ·  {r['title']} — {r['company']}"
                  for _, r in df.iterrows()]
        idx = st.selectbox("Pick a role", range(len(labels)),
                           format_func=lambda i: labels[i])
        job = df.iloc[idx].to_dict()

        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**✅ Matched keywords (your strengths)**")
            st.write(", ".join(job.get("_matched", [])) or "—")
        with c2:
            st.markdown("**⚠️ Missing keywords (gaps in your resume)**")
            st.write(", ".join(job.get("_missing", [])) or "—")

        # --- JD availability badge -------------------------------------------------
        has_desc = bool((job.get("desc") or "").strip())
        if has_desc:
            st.success(f"✅ Full JD available ({len(job['desc'])} chars) — deep tailoring possible.")
        else:
            if job.get("source") == "workday":
                st.warning("⚠️ Workday listing has no JD in the feed. "
                           "Clicking Generate will fetch the full JD on demand (one extra HTTP call, free). "
                           "Tailoring will then be as deep as Greenhouse/Lever roles.")
            else:
                st.warning("⚠️ JD body not available for this source — tailoring will be "
                           "title-only (shallow). Pick a Greenhouse/Lever/Ashby/jsearch row for deep tailoring.")

        strong = job["match"] >= 60
        if not strong and has_desc:
            st.info(f"This role matches {int(job['match'])}%. The generator works best on "
                    "strong matches (≥60%) — weaker ones usually mean a real skills gap, "
                    "not just wording.")
        has_key = bool(os.getenv("ANTHROPIC_API_KEY"))
        st.markdown("**Generate a tailored, ATS-friendly resume** "
                    "(rewrites Shalu's real experience to match this JD — no fabrication):")
        gcols = st.columns(2)
        clicked_profile = None
        if gcols[0].button("📝 Senior Product Manager profile",
                           disabled=not has_key or not RESUMES["Senior Product Manager"].strip(),
                           use_container_width=True, key=f"gen_pm_{job['id']}"):
            clicked_profile = "Senior Product Manager"
        if gcols[1].button("📝 Senior Product Owner profile",
                           disabled=not has_key or not RESUMES["Senior Product Owner"].strip(),
                           use_container_width=True, key=f"gen_po_{job['id']}"):
            clicked_profile = "Senior Product Owner"
        if not has_key:
            st.caption("Set `ANTHROPIC_API_KEY` in secrets to enable generation.")

        if clicked_profile:
            # On-demand Workday JD fetch — costs zero RapidAPI quota.
            if not has_desc and job.get("source") == "workday" and job.get("url"):
                with st.spinner("Fetching full JD from Workday…"):
                    try:
                        import requests, re as _re
                        url = job["url"]
                        # Workday job URL -> CXS job-details endpoint
                        m = _re.match(r"https?://([^/]+)/([^/]+)(/job/.*)", url)
                        if m:
                            host, site, path = m.group(1), m.group(2), m.group(3)
                            tenant = host.split(".")[0]
                            cxs = f"https://{host}/wday/cxs/{tenant}/{site}{path}"
                            ua = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                                 "AppleWebKit/537.36 (KHTML, like Gecko) "
                                                 "Chrome/124.0 Safari/537.36"),
                                  "Accept": "application/json"}
                            r = requests.get(cxs, headers=ua, timeout=20)
                            if r.status_code == 200:
                                jd = r.json().get("jobPostingInfo", {}).get("jobDescription", "")
                                jd = _re.sub(r"<[^>]+>", " ", jd or "")
                                jd = _re.sub(r"\s+", " ", jd).strip()
                                if jd:
                                    job["desc"] = jd[:6000]
                                    st.success(f"Fetched {len(jd)} chars of JD — tailoring will be deep.")
                                else:
                                    st.info("Workday returned no description text — tailoring will be title-only.")
                            else:
                                st.info(f"Workday JD fetch returned HTTP {r.status_code} — tailoring will be title-only.")
                    except Exception as e:
                        st.info(f"Workday JD fetch failed ({e}) — tailoring will be title-only.")

            from resume_gen import generate_resume
            with st.spinner(f"Tailoring {clicked_profile} resume (truthful, no fabrication)…"):
                try:
                    base = RESUMES[clicked_profile]
                    out = generate_resume(base, job, job.get("_missing", []),
                                          profile=clicked_profile)
                    # generate_resume now returns {"resume": ..., "gaps": ...}
                    if isinstance(out, str):
                        out = {"resume": out, "gaps": ""}
                    st.session_state[f"resume::{job['id']}::{clicked_profile}"] = out
                    st.session_state[f"resume_last::{job['id']}"] = clicked_profile
                except Exception as e:
                    st.error(f"Generation failed: {e}")

        last = st.session_state.get(f"resume_last::{job['id']}")
        saved = st.session_state.get(f"resume::{job['id']}::{last}") if last else None
        if saved:
            resume_md = saved["resume"]
            gaps_md   = saved.get("gaps", "")

            st.markdown(f"**Tailored resume — {last} profile.** Review before using; "
                        "it only reorders/rephrases what's in the base resume.")
            st.text_area("Result", resume_md, height=400)
            base_name = (f"resume_{last.replace(' ','')}_{job['company']}"
                         .replace(" ", "_").replace("&", "and").replace("/", "-"))
            d1, d2, d3 = st.columns(3)
            d1.download_button("⬇️ Markdown", resume_md, file_name=base_name + ".md",
                               use_container_width=True)
            try:
                from export import md_to_pdf_bytes
                d2.download_button("⬇️ PDF", md_to_pdf_bytes(resume_md),
                                   file_name=base_name + ".pdf", mime="application/pdf",
                                   use_container_width=True)
            except Exception:
                d2.caption("PDF needs `reportlab`")
            try:
                from export import md_to_docx_bytes
                d3.download_button(
                    "⬇️ Word", md_to_docx_bytes(resume_md), file_name=base_name + ".docx",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    use_container_width=True)
            except Exception:
                d3.caption("DOCX needs `python-docx`")

            # Coaching panel — visible to YOU, never goes into the downloaded resume.
            if gaps_md:
                with st.expander("💡 Before you apply — gap analysis (NOT in the downloaded resume)",
                                 expanded=True):
                    st.markdown(gaps_md)
                    st.caption("This is private coaching guidance — it's deliberately excluded "
                               "from the resume file you submit.")

if st.session_state.get("errors"):
    with st.expander(f"⚠️ {len(st.session_state['errors'])} source errors"):
        for e in st.session_state["errors"]:
            st.text(e)

st.caption(f"Filters: title `{DEFAULT_TITLE}` · location `{DEFAULT_LOC}` "
           "(edit defaults in radar.py)")
