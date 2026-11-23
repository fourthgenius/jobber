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


# ---------------------------------------------------------------- Interview prep per job (prep.py job_prep)
_STR = {"type": "string"}
_ARR = {"type": "array", "items": _STR}
BRIEF_SCHEMA = {"type": "object", "additionalProperties": False,
                "required": ["what_they_do", "products", "takeaways", "peers", "rounds"],
                "properties": {"what_they_do": _ARR, "products": _ARR, "takeaways": _ARR, "peers": _ARR,
                               "rounds": {"type": "array", "items": {
                                   "type": "object", "additionalProperties": False, "required": ["name", "what", "prep"],
                                   "properties": {"name": _STR, "what": _STR, "prep": _STR}}}}}


def prep_brief(j: dict, research: dict, wiki: str = "") -> dict:
    """Company brief from the JD + free research (Wikipedia, news headlines). Guesses are labelled."""
    news = "\n".join(f"- {n['date']}: {n['title']}" for n in research.get("news", []))
    return _ask(_sys(), f"{_job_block(j)}\n\nWIKIPEDIA:\n{wiki or '(none)'}\n\nRECENT HEADLINES:\n{news or '(none)'}\n\n"
                f"Sector (from a list): {research.get('sector') or 'unknown'}. Peers in that list: "
                f"{', '.join(research.get('peers', [])) or 'none'}\n\n"
                "Write an interview brief for this candidate. what_they_do: 3 bullets (business, customers, how they "
                "make money). products: up to 4 main products. takeaways: 3 things from the headlines worth "
                "mentioning in the interview (empty if no headlines). peers: up to 5 real competitors. rounds: the "
                "4-6 interview rounds most likely for THIS role at THIS company, each with what it tests and how to "
                "prep. Anything not in the sources above: add ' (verify)'.", BRIEF_SCHEMA, "medium", 4000)


PREPQ_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["questions"],
                "properties": {"questions": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["q", "kind", "why", "look_for", "req", "source", "outline", "text"],
                    "properties": {"q": _STR, "kind": _STR, "why": _STR, "look_for": _ARR, "req": _STR, "source": _STR,
                                   "outline": {"type": "array", "items": {"type": "array", "items": _STR}},
                                   "text": _STR}}}}}


def prep_questions(j: dict, brief: dict, stories: list[dict]) -> list[dict]:
    st = "\n".join(f"- {s['title']}: {s['situation']} | {s['action']} | {s['result']}" for s in stories[:12])
    r = _ask(_sys(), f"CV:\n{cv_text()}\n\nSTORY BANK:\n{st or '(empty)'}\n\n{_job_block(j)}\n\n"
             f"Company notes: {'; '.join(brief.get('what_they_do', [])[:3])}\n\n"
             "Write the 10 questions this candidate is most likely to get for THIS job, in interview order: an "
             "opener, why this company, 4-5 from the JD's must-haves, 1-2 probing the biggest gaps between the JD "
             "and the CV, one role-typical case, one behavioural. For each: kind (Opener, Motivation, a competency "
             "name, Gap, Case or Behavioural), why (which JD line or gap triggers it), look_for (3-4 things a great "
             "answer shows), req (the JD requirement it tests, quoted, or ''), source (which CV role or story the "
             "answer uses), outline (4 [label, text] pairs, STAR or a case framework), text (a first-person spoken "
             "answer of 120-200 words using ONLY CV/story facts; put anything unknown in [brackets]).",
             PREPQ_SCHEMA, "medium", 12000)
    out = []
    for i, q in enumerate(r["questions"]):
        out.append({"id": f"q{i + 1}", "q": q["q"], "kind": q["kind"], "why": q["why"], "look_for": q["look_for"],
                    "comp": None, "req": q["req"], "also": [],
                    "answer": {"source": q["source"], "outline": [p[:2] for p in q["outline"] if len(p) >= 2],
                               "text": q["text"], "gap": "[" in q["text"] and len(q["text"]) < 160}})
    return out


COACH_SCHEMA = {"type": "object", "additionalProperties": False,
                "required": ["score", "dims", "strengths", "improve", "better_answer", "follow_up"],
                "properties": {"score": {"type": "number"},
                               "dims": {"type": "object", "additionalProperties": False,
                                        "required": ["Structure", "Specifics", "Ownership", "Relevance", "Length"],
                                        "properties": {k: {"type": "integer"} for k in
                                                       ("Structure", "Specifics", "Ownership", "Relevance", "Length")}},
                               "strengths": _ARR, "improve": _ARR, "better_answer": _STR, "follow_up": _STR}}


def coach_answer(j: dict, q: dict, answer: str, history: list, seconds: int = 0) -> dict:
    convo = "\n".join(f"Q: {h.get('q')}\nA: {h.get('a')}" for h in history[-4:])
    return _ask(_sys(), f"CV:\n{cv_text()}\n\n{_job_block(j)}\n\nEarlier in this mock interview:\n{convo or '(start)'}\n\n"
                f"QUESTION ({q.get('kind')}): {q.get('q')}\nIt tests: {q.get('req') or q.get('why')}\n"
                f"A great answer shows: {'; '.join(q.get('look_for', []))}\n\nCANDIDATE ANSWER"
                f"{f' (spoken, {seconds}s)' if seconds else ''}:\n{answer[:5000]}\n\n"
                "You are the interviewer for this job. dims: score 1-5 each (Structure, Specifics = numbers and "
                "concrete detail, Ownership = their own actions, Relevance to the question and JD, Length = right "
                "size for 1-2 minutes spoken). score: the mean, one decimal. strengths: up to 3, quote their words. "
                "improve: up to 4 specific fixes, each naming what to add or cut. better_answer: a stronger version "
                "under 200 words using only facts from their answer and CV, [brackets] for missing facts. "
                "follow_up: the single probing follow-up question a real interviewer would ask next.",
                COACH_SCHEMA, "medium", 4000)
