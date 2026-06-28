"""
resume_gen.py — generate an ATS-tailored resume for one job (on demand).

Calls the Anthropic API ONCE per click. Needs ANTHROPIC_API_KEY.
The system prompt forbids fabrication: it only reorders/rephrases the
candidate's REAL experience to mirror the job's terminology and surface
keywords she genuinely has. Gaps are flagged, never invented.
"""

import os

MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")

SYSTEM = (
    "You are an expert resume writer optimizing a candidate's resume to pass ATS screening "
    "for ONE specific job — without fabricating anything.\n"
    "STRICT RULES:\n"
    "1. Use ONLY facts, employers, titles, dates, skills, and metrics that appear in the "
    "candidate's base resume. Never invent experience, tools, or numbers.\n"
    "2. Rephrase and reorder real bullets to mirror the job's terminology and naturally surface "
    "relevant keywords the candidate genuinely has.\n"
    "3. Front-load the strongest QUANTIFIED achievements (keep all real metrics).\n"
    "4. If the job requires something absent from the resume, do NOT add it to the body — instead "
    "list it under a short 'Gaps to address' section at the very end so the candidate can decide.\n"
    "5. Output clean, ATS-friendly Markdown: Name + contact, Summary (3-4 lines), Core Skills "
    "(comma list), Experience (company, title, dates, 3-6 bullets each), Education. "
    "No tables, columns, text boxes, images, or graphics.\n"
    "Keep it truthful and concise."
)


def generate_resume(resume_text, job, missing_keywords=None, profile=None):
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not set — needed to generate a tailored resume.")
    import anthropic

    client = anthropic.Anthropic(api_key=key)
    miss = ", ".join((missing_keywords or [])[:25])
    profile_note = ""
    if profile:
        profile_note = (
            f"\nTARGET PROFILE: {profile}. Frame the headline, summary, and bullet emphasis to "
            f"position the candidate for a {profile} role specifically — without changing any facts. "
            f"For 'Senior Product Manager', emphasize product strategy, vision, roadmap ownership, "
            f"market/customer outcomes, GTM, cross-functional leadership, and quantified business impact. "
            f"For 'Senior Product Owner', emphasize backlog ownership, sprint/PI execution, BDD acceptance "
            f"criteria, Agile/SAFe ceremonies, requirements engineering, and delivery discipline.\n"
        )
    user = (
        f"JOB: {job.get('title','')} at {job.get('company','')} ({job.get('location','')})\n"
        f"{profile_note}\n"
        f"JOB DESCRIPTION:\n{(job.get('desc') or '(no description available — use the title)')[:6000]}\n\n"
        f"KEYWORDS THE RESUME IS CURRENTLY MISSING (include ONLY where genuinely applicable, "
        f"otherwise put under 'Gaps to address'): {miss or '(none computed)'}\n\n"
        f"CANDIDATE BASE RESUME:\n{resume_text[:9000]}\n\n"
        f"Write the tailored ATS resume now."
    )
    msg = client.messages.create(
        model=MODEL, max_tokens=2200, system=SYSTEM,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
