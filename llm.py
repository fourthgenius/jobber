"""Claude helpers for Jobber: fit scoring, cover notes, CV tailoring, outreach, follow-ups, interview prep.

Key: secrets.json -> "anthropic_api_key" (or the ANTHROPIC_API_KEY env var). Without a key every
function raises NoKey and the app falls back to templates, so nothing breaks.
Nothing here sends anything to a recruiter: it only drafts text for you to review.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

HERE = Path(__file__).parent
SECRETS = HERE / "secrets.json"
CV_FILE = HERE / "cv.md"
MODEL = "claude-opus-5-5"


class NoKey(RuntimeError):
    pass


def secrets() -> dict:
    if not SECRETS.exists() and (HERE / "secrets.example.json").exists():
        shutil.copy(HERE / "secrets.example.json", SECRETS)
    try:
        return json.loads(SECRETS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_secrets(upd: dict) -> dict:
    s = secrets()
    s.update({k: v for k, v in upd.items() if isinstance(v, str)})
    SECRETS.write_text(json.dumps(s, indent=2), encoding="utf-8")
    return s


def _key() -> str:
    k = (secrets().get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not k or k.startswith("PASTE"):
        raise NoKey("Add your Claude API key in secrets.json (anthropic_api_key) to turn on AI features.")
    return k


def enabled() -> bool:
    try:
        _key()
        import anthropic  # noqa: F401
        return True
    except (NoKey, ImportError):
        return False


def cv_text() -> str:
    return CV_FILE.read_text(encoding="utf-8") if CV_FILE.exists() else ""


def _client():
    import anthropic
    return anthropic.Anthropic(api_key=_key(), max_retries=3, timeout=120)


def _ask(system: str, prompt: str, schema: dict | None = None, effort: str = "low", max_tokens: int = 4000):
    """One Messages call. Returns parsed JSON when a schema is given, else text."""
    model = secrets().get("model") or MODEL
    kw = dict(model=model, max_tokens=max_tokens, system=system,
              messages=[{"role": "user", "content": prompt}],
              output_config={"effort": effort})
    if schema:
        kw["output_config"]["format"] = {"type": "json_schema", "schema": schema}
    if model.startswith(("claude-opus-5", "claude-fable", "claude-sonnet-5-5")):
        kw.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        r = _client().beta.messages.create(**kw)
    else:
        r = _client().messages.create(**kw)
    if r.stop_reason == "refusal":
        raise RuntimeError("Claude declined this request.")
    text = next((b.text for b in r.content if b.type == "text"), "")
    return json.loads(text) if schema else text.strip()


def _target() -> str:
    """One line describing what the candidate is looking for, from config.json."""
    import settings
    cfg = settings.load_cfg()
    roles = ", ".join(cfg.get("search_terms", [])[:5]) or "their target roles"
    locs = [L["label"] for L in cfg.get("locations", [])] + (["remote"] if cfg["remote"].get("enabled") else [])
    floor = settings.money(cfg["min_salary"], cfg) if cfg.get("min_salary") else "not set"
    yrs = cfg.get("profile", {}).get("years_experience", "?")
    return (f"Target roles: {roles}. Locations: {', '.join(locs) or 'any'}. Pay floor: {floor}. "
            f"Experience: about {yrs} years.")


SYS_BASE = ("You are a sharp career coach and recruiter. You help one candidate. "
            "Use only facts from the candidate's CV and profile. Never invent experience, numbers, employers or "
            "skills. Write plainly in a professional tone, no emoji, no clichés.")


def _sys() -> str:  # built on demand so it always reflects the current config
    return SYS_BASE + " " + _target()


def _job_block(j: dict) -> str:
    return (f"JOB: {j.get('title')} at {j.get('company')} ({j.get('location') or ''})\n"
            f"Salary: {j.get('salary_text') or 'not listed'}\n"
            f"Description:\n{(j.get('description') or '(no description captured)')[:7000]}")


FIT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["score", "verdict", "reasons", "gaps", "keywords", "best_cv"],
    "properties": {
        "score": {"type": "integer"},
        "verdict": {"type": "string"},
        "reasons": {"type": "array", "items": {"type": "string"}},
        "gaps": {"type": "array", "items": {"type": "string"}},
        "keywords": {"type": "array", "items": {"type": "string"}},
        "best_cv": {"type": "string"},
    },
}


def fit(j: dict) -> dict:
    return _ask(_sys(), f"CANDIDATE CV:\n{cv_text()}\n\n{_job_block(j)}\n\n"
                "Score 0-100 how likely this candidate gets a callback (role level, years asked vs the candidate's "
                "experience, domain, must-have skills, location and pay floor from the target above). "
                "verdict: one sentence. reasons: 3 short bullets citing CV facts. gaps: up to 2. keywords: 8 "
                "must-have JD keywords for an ATS. best_cv: one or two words naming the CV angle that fits best "
                "(e.g. general, growth, technical, leadership).", FIT_SCHEMA, "low", 2000)


def cover_note(j: dict) -> str:
    return _ask(_sys(), f"CV:\n{cv_text()}\n\n{_job_block(j)}\n\nWrite a cover note of at most 130 words for this "
                "application: a hook tied to the company, 2 proof points with numbers from the CV mapped to the JD, "
                "one line on why this role now. No greeting line, no sign-off.", effort="medium")


ANS_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["answers"],
              "properties": {"answers": {"type": "array", "items": {
                  "type": "object", "additionalProperties": False, "required": ["question", "answer"],
                  "properties": {"question": {"type": "string"}, "answer": {"type": "string"}}}}}}


def screening_answers(j: dict, questions: list[str]) -> list[dict]:
    qs = "\n".join(f"- {q}" for q in questions if q.strip())
    return _ask(_sys(), f"CV:\n{cv_text()}\n\n{_job_block(j)}\n\nAnswer each application question in 40-90 words, "
                f"first person, specific to this job, facts from the CV only:\n{qs}", ANS_SCHEMA, "medium")["answers"]


def tailor_cv(j: dict, keywords: list[str] | None = None) -> str:
    return _ask(_sys() + " You are rewriting a CV for one job. Keep every fact true; you may reorder, reword, "
                "and choose which bullets to emphasise. Keep it to one page.",
                f"BASE CV (markdown):\n{cv_text()}\n\n{_job_block(j)}\n\nATS keywords to work in where TRUE: "
                f"{', '.join(keywords or [])}\n\nReturn the full tailored CV in the same markdown structure "
                "(# Name, contact line, ## sections, - bullets). Rewrite the summary for this role, put the 3 most "
                "relevant bullets first in each role, and mirror the JD's wording where the CV supports it. "
                "Return only the markdown.", effort="medium", max_tokens=6000)


OUT_SCHEMA = {"type": "object", "additionalProperties": False,
              "required": ["hiring_manager_note", "alumni_note", "recruiter_note", "who_to_find"],
              "properties": {"hiring_manager_note": {"type": "string"}, "alumni_note": {"type": "string"},
                             "recruiter_note": {"type": "string"},
                             "who_to_find": {"type": "array", "items": {"type": "string"}}}}


def outreach(j: dict) -> dict:
    return _ask(_sys(), f"CV:\n{cv_text()}\n\n{_job_block(j)}\n\nDraft three LinkedIn connection notes, each under "
                "290 characters: to the likely hiring manager, to an alum of the candidate's school or a former "
                "colleague at the company asking for a referral, and to the recruiter. Concrete, one proof point each, a clear small ask. "
                "who_to_find: 3 job titles of people worth contacting for this role.", OUT_SCHEMA, "low", 2000)


def follow_up(j: dict, days: int) -> str:
    return _ask(_sys(), f"CV:\n{cv_text()}\n\n{_job_block(j)}\n\nThe candidate applied {days} days ago and heard "
                "nothing. Write a polite follow-up email under 90 words to the recruiter: subject line first "
                "('Subject: ...'), restate fit in one line with one number, ask about next steps.", effort="low")


def interview_prep(j: dict) -> str:
    return _ask(_sys(), f"CV:\n{cv_text()}\n\n{_job_block(j)}\n\nBuild a concise interview prep pack in markdown: "
                "## Company in 5 bullets (only what the JD says, mark guesses as guesses), ## Likely questions "
                "(8: role-specific skills, problem solving, execution, behavioural, one case or exercise typical for "
                "this role), ## Your stories (map 5 CV wins to "
                "those questions, STAR in one line each), ## Questions to ask them (4).",
                effort="medium", max_tokens=6000)


# ---------------------------------------------------------------- Interview prep (prep.py)
def company_brief(company: str, j: dict | None, notes: str = "") -> str:
    jb = _job_block(j) if j else f"Company: {company} (no job description saved)"
    return _ask(_sys(), f"CV:\n{cv_text()}\n\n{jb}\n\nUser notes about the company:\n{notes or '(none)'}\n\n"
                "Write an interview brief in markdown: ## What they do (from the JD and notes; label anything else "
                "'from general knowledge, verify'), ## What this team likely needs, ## How your CV maps to it "
                "(4 bullets citing CV facts), ## Risks to address (2), ## Smart questions to ask (4). Under 350 words.",
                effort="medium", max_tokens=3000)


MOCK_SCHEMA = {"type": "object", "additionalProperties": False,
               "required": ["score", "strengths", "improve", "better_answer"],
               "properties": {"score": {"type": "integer"}, "strengths": {"type": "array", "items": {"type": "string"}},
                              "improve": {"type": "array", "items": {"type": "string"}},
                              "better_answer": {"type": "string"}}}


def mock_feedback(question: str, answer: str, role: str = "") -> dict:
    return _ask(_sys(), f"CV:\n{cv_text()}\n\nInterview question ({role or 'general'}): {question}\n\n"
                f"Candidate's answer:\n{answer[:4000]}\n\nAct as the interviewer. score: 1-5. strengths: up to 3. "
                "improve: up to 3 specific fixes (structure, specifics, numbers, ownership, brevity). better_answer: "
                "a tighter version under 180 words using only facts from the answer and CV.", MOCK_SCHEMA, "medium", 3000)


QS_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["questions"],
             "properties": {"questions": {"type": "array", "items": {"type": "string"}}}}


def job_questions(j: dict) -> list[str]:
    return _ask(_sys(), f"{_job_block(j)}\n\nList 10 interview questions this company is likely to ask for this "
                "role, specific to the JD: mix skills, scenario, behavioural and one take-home or case style.",
                QS_SCHEMA, "low", 2000)["questions"]
