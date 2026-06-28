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
    "for ONE specific job — without fabricating anything.\n\n"
    "STRICT RULES FOR THE RESUME BODY:\n"
    "1. Use ONLY facts, employers, titles, dates, skills, and metrics that appear in the "
    "candidate's base resume. Never invent experience, tools, or numbers.\n"
    "2. Rephrase and reorder real bullets to mirror the job's terminology and naturally surface "
    "relevant keywords the candidate genuinely has.\n"
    "3. Front-load the strongest QUANTIFIED achievements (keep all real metrics).\n"
    "4. Write in FIRST PERSON style (bullets starting with strong verbs like 'Owned', 'Led', "
    "'Reduced'). Never refer to 'the candidate' or 'Shalu' in third person inside the resume body.\n"
    "5. Output clean, ATS-friendly Markdown with this exact section order:\n"
    "   # Name\n"
    "   Headline (one line)\n"
    "   Contact line\n"
    "   ## Summary  (3-4 lines)\n"
    "   ## Core Skills  (comma-separated list)\n"
    "   ## Professional Experience  (per role: ### Company — Bengaluru, then **Title** · dates, then 3-6 bullets)\n"
    "   ## Education & Certifications\n"
    "   ## Tools & Technologies\n"
    "6. ABSOLUTELY DO NOT include any of these inside the resume body:\n"
    "   - Markdown horizontal rules (no '---' or '***' lines anywhere)\n"
    "   - Any 'Gaps to Address', 'Recommended action', 'Before applying', or coaching section\n"
    "   - Any commentary about what's missing, what the candidate should learn, or interview prep\n"
    "   - Any text in third person about the candidate\n"
    "   The resume must be a clean deliverable safe to submit AS-IS to a recruiter.\n"
    "7. If the job requires something absent from the base resume, do NOT add it to the resume. "
    "Instead, emit a SEPARATE block AFTER the resume, fenced exactly like this:\n"
    "===GAPS===\n"
    "- Skill or domain X (not present in base resume — consider addressing in cover letter)\n"
    "- Skill Y (not present — consider relevant training/certification)\n"
    "===END_GAPS===\n"
    "The app will display this block to the candidate separately; it will NOT be downloaded with the resume.\n"
    "8. No tables, columns, text boxes, images, graphics, or horizontal rules anywhere in the output.\n"
    "Keep the resume truthful, concise, and submission-ready."
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
    raw = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()

    # Split into (resume_body, gaps_block) — gaps are shown separately in the app,
    # NOT included in the downloaded resume.
    import re as _re
    gaps = ""
    m = _re.search(r"={3,}\s*GAPS\s*={3,}\s*(.*?)\s*={3,}\s*END_GAPS\s*={3,}",
                   raw, _re.S | _re.I)
    if m:
        gaps = m.group(1).strip()
        body = (raw[:m.start()] + raw[m.end():]).strip()
    else:
        body = raw

    # Belt-and-suspenders: strip any horizontal rules and any stray "Gaps to address"
    # section the model may still emit despite the prompt.
    body = _re.sub(r"^\s*[-*_]{3,}\s*$", "", body, flags=_re.M)
    body = _re.sub(
        r"\n#+\s*(gaps\s*to\s*address|before\s+you\s+apply|recommended\s+action).*",
        "", body, flags=_re.S | _re.I,
    )
    body = _re.sub(r"\n{3,}", "\n\n", body).strip()

    return {"resume": body, "gaps": gaps}
