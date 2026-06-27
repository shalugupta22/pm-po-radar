"""
matching.py — ATS-style match scoring (local, free, no API).

Scores a job against a resume the way an ATS screener roughly does:
what share of the job's salient keywords appear in the resume.
Returns (score 0-100, matched_keywords, missing_keywords).
The missing list is the useful part — it's the gap to close.
"""

import re
from collections import Counter

STOP = set("""
a an the and or of to in for with on at by from as is are be was were been being this that these those
it its their your you we our us they them he she his her i me my mine ours yours
will would can could should may might must shall do does did done have has had having
not no nor so than too very just also then once here there when where why how all any both each few more
most other some such only own same up out off over under again further once into about above below between
who whom which what whose if because while during before after through across per via within without
role roles work working team teams company companies job jobs position positions candidate candidates
experience experiences year years new join including include includes etc help helps strong ability able
across new across responsibilities responsibility requirements required preferred plus etc good great
""".split())

# Domain skills weighted higher so PM/AI/data terms count toward the score.
SKILLS = {
    "product", "roadmap", "backlog", "prioritization", "prioritisation", "stakeholder", "stakeholders",
    "agile", "scrum", "kanban", "jira", "confluence", "discovery", "okr", "okrs", "kpi", "kpis",
    "metrics", "analytics", "sql", "experimentation", "a/b", "ab", "ml", "ai", "genai", "llm", "llms",
    "rag", "data", "platform", "platforms", "api", "apis", "saas", "b2b", "b2c", "iot", "gtm",
    "go-to-market", "ux", "wireframe", "wireframes", "mvp", "lifecycle", "monetization", "pricing",
    "churn", "retention", "activation", "onboarding", "segmentation", "personas", "sprint", "prd",
    "requirements", "cross-functional", "databricks", "azure", "aws", "gcp", "tableau", "powerbi",
    "python", "governance", "compliance", "fintech", "payments", "identity", "iam", "oauth", "saml",
    "automation", "optimization", "forecasting", "classification", "modeling", "modelling", "telemetry",
    "energy", "smart", "buildings", "asset", "intelligence", "delivery", "agentic", "embeddings",
    "vector", "pipeline", "etl", "lakehouse", "spark", "delta", "rest", "microservices",
}


def _tokens(text):
    text = (text or "").lower()
    toks = re.findall(r"[a-z][a-z0-9+/#.\-]*", text)
    toks = [t.strip("./#-+") for t in toks]          # drop stray trailing/leading punctuation
    return [t for t in toks if t and t not in STOP and len(t) > 2]


def _job_keywords(job_text, top=30):
    """Salient keywords from a job: skill-lexicon hits + most frequent meaningful terms."""
    toks = _tokens(job_text)
    if not toks:
        return set()
    counts = Counter(toks)
    skill_hits = {t for t in toks if t in SKILLS}
    common = {t for t, _ in counts.most_common(top)}
    return skill_hits | common


def match_score(resume_text, job):
    """Return (score 0-100, matched[list], missing[list]) for a job dict (uses desc, else title)."""
    job_text = " ".join([job.get("title", ""), job.get("title", ""), job.get("desc", "")])
    jd_kw = _job_keywords(job_text)
    if not jd_kw:
        return 0, [], []
    res_kw = set(_tokens(resume_text))
    matched = sorted(jd_kw & res_kw)
    missing = sorted(jd_kw - res_kw)
    score = round(100 * len(matched) / len(jd_kw))
    return score, matched, missing
