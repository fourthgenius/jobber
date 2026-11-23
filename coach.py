"""Interview coach: prep tailored to one job, its company and your CV.

No API key needed. Everything here is rule-based:
  - company brief: the job post, Wikipedia's summary, Google News headlines, peers from companies.json,
    and the interview rounds typical for the role and company type
  - questions: built from the job description's requirement lines, the gaps between the JD and your CV,
    the company, and the role's question bank
  - suggested answers: drafted from your own CV bullets and story bank (never invented; gaps are left as
    [brackets] for you to fill)
  - mock interview: a rubric scores each answer on structure, specifics, ownership, relevance and length,
    gives concrete fixes, rebuilds a stronger answer and asks one follow-up
With a Claude API key (secrets.json) prep.py swaps in llm.py for richer versions of the same outputs.
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote, quote_plus
from urllib.request import Request, urlopen

HERE = Path(__file__).parent
UA = {"User-Agent": "Jobber/1.0 (open-source job search helper)"}

# ---------------------------------------------------------------- competencies
# keywords found in a JD line -> the competency interviewers will test, a question, and what a great answer shows
COMPS = {
    "Execution": {
        "kw": ["launch", "ship", "deliver", "execut", "end-to-end", "end to end", "own the", "ownership", "drive",
               "timeline", "go-to-market", "gtm", "release"],
        "q": "Tell me about something you took from idea to launch. What exactly did you own?",
        "look": ["A real launch with a date and scope", "What you did vs the team", "How you unblocked delays",
                 "The measurable outcome after launch"]},
    "Prioritisation": {
        "kw": ["priorit", "roadmap", "trade-off", "tradeoff", "backlog", "sequenc", "focus"],
        "q": "Walk me through a hard trade-off you made on a roadmap. What did you say no to, and why?",
        "look": ["The options on the table", "A clear framework (impact, effort, risk, strategy)",
                 "Who disagreed and how you aligned them", "What happened as a result"]},
    "Analytics": {
        "kw": ["data", "sql", "metric", "analytic", "a/b", "experiment", "kpi", "dashboard", "insight",
               "quantitative", "measure", "funnel", "cohort"],
        "q": "Tell me about a decision you changed because of data. What did the numbers show?",
        "look": ["The metric and why it mattered", "How you got the data (SQL, tool, experiment)",
                 "The insight, in one line", "The decision and its impact, in numbers"]},
    "Customer insight": {
        "kw": ["customer", "user research", "users", "user needs", "feedback", "persona", "empathy", "interview",
               "pain point", "jobs to be done", "usability"],
        "q": "Tell me about an insight from customers that changed what you built.",
        "look": ["How you found it (calls, research, support data)", "The surprising insight",
                 "What you changed because of it", "The result for users and the business"]},
    "Stakeholders": {
        "kw": ["stakeholder", "cross-functional", "cross functional", "collaborat", "align", "influence",
               "partner with", "work closely", "engineering, design", "sales and"],
        "q": "Tell me about a time you had to align teams that wanted different things.",
        "look": ["Who wanted what, and why", "How you found common ground", "Influence without authority",
                 "The decision and how the relationship ended up"]},
    "Leadership": {
        "kw": ["lead a", "lead the", "leading a", "mentor", "manage a team", "people manager", "hiring", "coach",
               "build the team", "team of"],
        "q": "Tell me about a time you led a team through something difficult.",
        "look": ["Team size and the challenge", "What you did to set direction", "How you handled a struggling person",
                 "Outcome for the work and the team"]},
    "Strategy": {
        "kw": ["strategy", "strategic", "vision", "market", "competiti", "business case", "p&l", "revenue",
               "monetiz", "pricing", "growth", "expansion"],
        "q": "If you joined, where would you take this product or business in the next year, and why?",
        "look": ["A view of the market and the user", "2-3 bets, ranked", "How you'd measure success",
                 "Risks and what you'd test first"]},
    "Ambiguity": {
        "kw": ["ambigu", "0 to 1", "0-1", "zero to one", "fast-paced", "fast paced", "startup", "scrappy",
               "many hats", "first principles", "from scratch", "early-stage", "founder"],
        "q": "Tell me about a time you had to figure something out with no playbook.",
        "look": ["How unclear the starting point was", "How you broke the problem down",
                 "What you tried first and what you learned", "The result and the speed"]},
    "Communication": {
        "kw": ["communicat", "present", "writing", "written", "document", "prd", "storytell", "articulat", "spec"],
        "q": "Tell me about a time you had to explain something complex to a non-expert audience.",
        "look": ["Who the audience was", "How you simplified (analogy, visual, one-pager)",
                 "How you checked they understood", "What it unlocked"]},
    "Technical depth": {
        "kw": ["api", "technical", "architecture", "system design", "machine learning", "llm", "genai", " ai ",
               "infrastructure", "integration", "sdk"],
        "q": "Tell me about the most technical problem you've worked on. How did you go deep enough to decide well?",
        "look": ["The technical problem in plain words", "How you learned what you needed",
                 "A trade-off you made with engineers", "The outcome"]},
}

# typical interview loops by role type, adjusted for startup vs large company
ROUNDS = {
    "product": [("Recruiter screen", "Background, motivation, pay and notice period.", "A 60-second pitch and your pay range."),
                ("Hiring manager", "Your past products, how you think, why this team.", "3 STAR stories with numbers."),
                ("Product sense / case", "Design or improve a product, often theirs.", "Users → problems → solutions → prioritise → metric."),
                ("Analytics / execution", "Metrics, a drop in a metric, A/B tests, trade-offs.", "North-star + input metrics for their product."),
                ("Leadership / culture", "Conflict, failure, influence without authority.", "A failure story and a disagreement story.")],
    "operations": [("Recruiter screen", "Background, motivation, pay and notice period.", "A 60-second pitch."),
                   ("Hiring manager / founder", "How you think, ownership, why this company.", "Stories of things you fixed end to end."),
                   ("Case or assignment", "A business problem or a take-home (market sizing, ops plan, deck).", "Structure first; state assumptions; give a recommendation."),
                   ("Problem solving", "Guesstimates, prioritisation, unit economics.", "Practise 2 guesstimates out loud."),
                   ("Culture / team fit", "Working style, pace, ambiguity.", "Your best 'no playbook' story.")],
    "engineering": [("Recruiter screen", "Background, stack, pay and notice.", "A 60-second pitch."),
                    ("Coding round", "Data structures and algorithms, or practical coding.", "Talk while you code; test edge cases."),
                    ("System design", "Design a service end to end.", "Requirements → APIs → data → scale → trade-offs."),
                    ("Hiring manager", "Projects you owned, debugging, collaboration.", "2 deep project walk-throughs."),
                    ("Behavioural", "Conflict, ownership, failure.", "3 STAR stories.")],
    "data": [("Recruiter screen", "Background, tools, pay and notice.", "A 60-second pitch."),
             ("SQL / technical", "Live SQL, statistics, Python.", "Joins, window functions, A/B test maths."),
             ("Case / product analytics", "Investigate a metric drop or size an opportunity.", "Clarify → segment → hypothesise → test."),
             ("Hiring manager", "Projects and impact.", "2 projects where analysis changed a decision."),
             ("Behavioural", "Stakeholders, communication.", "A story explaining data to non-experts.")],
    "design": [("Recruiter screen", "Background and portfolio fit.", "A 60-second pitch."),
               ("Portfolio review", "Walk through 2 projects end to end.", "Problem → research → options → decision → outcome."),
               ("Design exercise", "Live or take-home design problem.", "Ask about users and constraints first."),
               ("Cross-functional", "Working with PM and engineering.", "A disagreement story."),
               ("Culture", "Craft, feedback, values.", "How you take critique.")],
    "marketing": [("Recruiter screen", "Background, channels, pay.", "A 60-second pitch."),
                  ("Hiring manager", "Campaigns you owned and results.", "Numbers: CAC, conversion, ROAS."),
                  ("Case / assignment", "A launch or growth plan for their product.", "Audience → message → channel → budget → metric."),
                  ("Team / culture", "Collaboration with sales and product.", "A cross-team story.")],
    "sales": [("Recruiter screen", "Background, quota history, pay.", "Your attainment numbers."),
              ("Hiring manager", "Deals you closed, pipeline habits.", "One big deal story end to end."),
              ("Role play / mock pitch", "Pitch their product or handle objections.", "Discovery questions before pitching."),
              ("Leadership / culture", "Resilience, coachability.", "A lost-deal lesson.")],
    "finance": [("Recruiter screen", "Background, qualifications, pay.", "A 60-second pitch."),
                ("Technical", "Accounting, modelling, valuation.", "Walk through the 3 statements."),
                ("Case / modelling test", "Build or critique a model.", "State assumptions clearly."),
                ("Hiring manager / culture", "Judgement and communication.", "A story where your numbers changed a decision.")],
    "general": [("Recruiter screen", "Background, motivation, pay and notice period.", "A 60-second pitch."),
                ("Hiring manager", "Your experience against the role's needs.", "3 STAR stories with numbers."),
                ("Skills round / assignment", "A task or case typical of the role.", "Ask what good looks like before starting."),
                ("Culture / final", "Values, working style, questions for them.", "3 smart questions to ask.")],
}

# ---------------------------------------------------------------- text helpers
STOP = set("""a an the and or but of to in on for with at by from as is are was were be been being this that these those
it its we our you your they their i me my he she his her them us will would can could should may might must do does did
have has had not no yes so if then than into over under about across per via all any each more most other some such only
own same too very just also etc walk through tell time know exactly role team work working experience years year strong ability skills skill using use
including include new great good well ensure help like make get able etc. who what when where why how which""".split())
FILLERS = ["um", "uh", "like", "basically", "actually", "you know", "kind of", "sort of", "literally", "i mean"]
NUM = re.compile(r"(\d[\d,.]*\s?(%|x|k|m|mn|cr|crore|lakh|lpa|bn|million|billion|users|customers|weeks|months|days)?|"
                 r"[₹$€£]\s?\d[\d,.]*)", re.I)


def words(t: str) -> list[str]:
    return [w for w in re.findall(r"[a-z][a-z0-9+/&.-]*[a-z0-9+]|[a-z]", (t or "").lower()) if w not in STOP and len(w) > 2]


def sentences(text: str) -> list[str]:
    text = re.sub(r"[*#_`>|]+", " ", text or "")
    parts = re.split(r"(?<=[.!?])\s+|\n+|\s+[-•·▪●]\s+", text)
    return [re.sub(r"\s+", " ", p).strip(" -•·▪●") for p in parts if 25 <= len(p.strip()) <= 300]


def short(s: str, n: int = 110) -> str:
    s = s.strip().rstrip(".")
    return s if len(s) <= n else s[:n].rsplit(" ", 1)[0] + "…"


# ---------------------------------------------------------------- CV parsing
def parse_cv(text: str) -> dict:
    """Markdown CV -> {name, summary, roles:[{title, company, dates}], bullets:[{text, title, company, dates}], skills}"""
    out = {"name": "", "summary": "", "roles": [], "bullets": [], "skills": []}
    sec, role = "", {"title": "", "company": "", "dates": ""}
    for ln in (text or "").splitlines():
        s = ln.strip()
        if s.startswith("# "):
            out["name"] = s[2:].strip()
        elif s.startswith("## "):
            sec = s[3:].strip().lower()
        elif s.startswith("### "):
            h = s[4:].strip()
            m = re.match(r"(.+?)\s*\(([^)]*)\)\s*$", h)
            head, dates = (m.group(1), m.group(2)) if m else (h, "")
            parts = re.split(r",\s*|\s+[|@–-]\s+|\s+at\s+", head, maxsplit=1)
            role = {"title": parts[0].strip(), "company": parts[1].strip() if len(parts) > 1 else "", "dates": dates}
            out["roles"].append(role)
        elif re.match(r"^[-*•] ", s):
            b = s[2:].strip()
            if "skill" in sec:
                out["skills"] += [x.strip() for x in re.split(r"[,;|]", b) if x.strip()]
            elif "summary" in sec or "about" in sec:
                out["summary"] += " " + b
            elif "educat" not in sec and len(b) > 20:
                out["bullets"].append({"text": b, **role})
        elif s and "summary" in sec:
            out["summary"] += " " + s
        elif s and "skill" in sec:
            out["skills"] += [x.strip() for x in re.split(r"[,;|]", s) if x.strip()]
    out["summary"] = out["summary"].strip()
    return out


def split_bullet(b: str) -> tuple[str, str]:
    """'Grew WAU 35% by redesigning onboarding' -> (action 'redesigned onboarding...', result 'grew WAU 35%')."""
    b = b.rstrip(".")
    m = re.search(r"\s(by|through|via)\s", b)
    if m and NUM.search(b[:m.start()]):
        return b[m.end():], b[:m.start()]
    m = re.search(r",?\s(which|that|resulting in|leading to|to cut|to lift|cutting|lifting|increasing|reducing)\s", b)
    if m:
        return b[:m.start()], b[m.start():].lstrip(", ")
    return b, ""


def _need(req: str) -> str:
    """'Must have: strong SQL skills…' -> 'strong SQL skills'"""
    if not req:
        return ""
    need = re.sub(r"^(must have|must|you will|you'll|you have|you are|ability to|experience (with|in)|strong|"
                  r"proven|nice to have:?|bonus:?)\s+", "", short(req, 130), flags=re.I)
    if ":" in need[:40]:
        need = need.split(":", 1)[1].strip()
    need = re.sub(r"^(you are|you're)\s+", "", need.rstrip("…").rstrip("."), flags=re.I)
    return re.sub(r"\s+(on|to|of|and|with|the|a|an|for|in|or)$", "", need)


def _defill(s: str) -> str:
    s = re.sub(r",?\s*\b(um|uh|basically|actually|literally|you know|i mean|kind of|sort of)\b,?", "", s, flags=re.I)
    s = re.sub(r",?\s+like,?\s+(?=[a-z])", " ", s)
    return re.sub(r"\s{2,}", " ", s).strip()


def _i_did(action: str) -> str:
    a = _lower1(action.strip().rstrip("."))
    return f"I got there by {a}" if re.match(r"^\w+ing\b", a) else f"I {a}"


def _lower1(s: str) -> str:
    return s[:1].lower() + s[1:] if s and not s[:2].isupper() else s


# ---------------------------------------------------------------- JD analysis
def requirements(jd: str) -> list[dict]:
    """Requirement-like lines from the JD, each tagged with the competencies it tests."""
    want = re.compile(r"\b(you will|you'll|you have|you are|responsib|require|must|should|experience|ability|"
                      r"looking for|nice to have|bonus|own|lead|drive|work with|partner|build|define|manage|"
                      r"proficien|familiar|understanding|track record|comfortable)\b", re.I)
    out = []
    for s in sentences(jd):
        s = re.sub(r"^(about the role|the role|what you'll do|responsibilities|requirements|what we're looking for)"
                   r"\s*:\s*", "", s, flags=re.I)
        low = " " + s.lower() + " "
        if ABOUT.search(s) and not re.search(r"\b(you|your)\b", low):
            continue
        comps = [k for k, c in COMPS.items() if any(_kw(w, low) for w in c["kw"])]
        if want.search(s) or comps:
            out.append({"text": s, "comps": comps, "must": bool(re.search(r"\b(must|require|need)", low)),
                        "nice": bool(re.search(r"nice to have|bonus|plus\b|preferred", low))})
    return out


def _kw(w: str, low: str) -> bool:
    return bool(re.search(r"(?<![a-z])" + re.escape(w.strip()), low)) if w.strip() == w else w in low


def comp_weights(reqs: list[dict]) -> list[tuple[str, int]]:
    w: dict[str, int] = {}
    for r in reqs:
        for c in r["comps"]:
            w[c] = w.get(c, 0) + (2 if r["must"] else 1)
    return sorted(w.items(), key=lambda x: -x[1])


# ---------------------------------------------------------------- evidence ranking
def evidence(cv: dict, stories: list[dict], query: str, comp: str | None = None, used: set | None = None) -> list[dict]:
    """Rank your CV bullets and STAR stories by overlap with a question/requirement."""
    q = set(words(query))
    kws = COMPS.get(comp, {}).get("kw", []) if comp else []
    items = [{"kind": "cv", "text": b["text"], "title": b["title"], "company": b["company"], "dates": b["dates"]}
             for b in cv["bullets"]]
    items += [{"kind": "story", "text": " ".join([s.get("situation", ""), s.get("action", ""), s.get("result", "")]),
               "story": s, "title": s.get("title", "")} for s in stories]
    for it in items:
        low = " " + it["text"].lower()
        it["score"] = (len(q & set(words(low))) * 2 + sum(1 for k in kws if _kw(k, low)) * 3
                       + (2 if NUM.search(it["text"]) else 0) + (3 if it["kind"] == "story" else 0)
                       + (6 if comp and comp in it.get("story", {}).get("tags", []) else 0)
                       - (8 if used and it["text"] in used else 0))
    return sorted(items, key=lambda x: -x["score"])


def draft_answer(ev: dict | None, req: str, company: str, comp: str | None) -> dict:
    """A STAR outline + spoken draft from one piece of your evidence. Unknowns stay in [brackets]."""
    need = _need(req)
    tie = f"That's the same muscle you need here: {_lower1(need)}." if need else \
        f"I'd bring the same approach to {company}."
    if not ev:
        return {"source": "", "outline": [
            ["Situation", "[Pick a real moment: where were you, what was the problem, how big was it?]"],
            ["Task", f"[What were you responsible for{' on ' + comp.lower() if comp else ''}?]"],
            ["Action", "[3 things YOU did, in order. Say 'I', not 'we'.]"],
            ["Result", "[A number: %, ₹, users, time saved. Then what you learned.]"]],
            "text": "[No matching example in your CV or story bank yet. Add a STAR story on the My stories tab.]",
            "gap": True}
    if ev["kind"] == "story":
        s = ev["story"]
        outline = [["Situation", s.get("situation") or "[context]"], ["Task", s.get("task") or "[your goal]"],
                   ["Action", s.get("action") or "[what you did]"], ["Result", s.get("result") or "[the number]"]]
        text = " ".join(x for x in [s.get("situation"), s.get("task"), s.get("action"), s.get("result")] if x)
        return {"source": f"Story: {s.get('title')}", "outline": outline, "text": f"{text} {tie}".strip(), "gap": False}
    action, result = split_bullet(ev["text"])
    where = f"{ev['title']} at {ev['company']}" if ev["company"] else ev["title"] or "my last role"
    outline = [["Situation", f"As {where}{' (' + ev['dates'] + ')' if ev['dates'] else ''}: "
                             "[what problem or opportunity, and why it mattered]"],
               ["Task", f"[the goal you owned{', tied to ' + comp.lower() if comp else ''}]"],
               ["Action", _i_did(action) + (" [add 2 specific steps you took]" if len(action) < 70 else "")],
               ["Result", re.sub(r"^(which|that)[ ]", "It ", result[:1].upper() + result[1:]) if result else "[the measurable result]"]]
    text = (f"When I was {where}, [the problem in one line]. I owned [the goal]. {_i_did(action)}. "
            + (f"As a result, I {_lower1(result)}. " if result and not result.lower().startswith(("which", "that"))
               else (f"As a result, it {re.sub(r'^(which|that)[ ]', '', result)}. " if result else "[The result, with a number.] "))
            + tie)
    return {"source": f"CV: {where}", "outline": outline, "text": text, "gap": False}


# ---------------------------------------------------------------- question set for one job
def question_set(j: dict, cv_text: str, stories: list[dict], gap: dict, role_bank: list[dict], role_label: str,
                 brief: dict | None = None) -> list[dict]:
    co = j.get("company") or "the company"
    title = j.get("title") or "this role"
    cv = parse_cv(cv_text)
    reqs = requirements(j.get("description") or "")
    used: set = set()
    qs: list[dict] = []

    def add(q, kind, why, look, comp=None, req="", ev_query=""):
        ev = evidence(cv, stories, ev_query or (q + " " + req), comp, used)
        best = ev[0] if ev and ev[0]["score"] > 1 else None
        if best:
            used.add(best["text"])
        qs.append({"id": f"q{len(qs) + 1}", "q": q, "kind": kind, "why": why, "look_for": look, "comp": comp,
                   "req": req, "answer": draft_answer(best, req, co, comp),
                   "also": [e["text"] for e in ev[1:3] if e["score"] > 2]})

    # 1. opener
    top = [b["text"] for b in cv["bullets"][:2]]
    intro = {"source": "CV summary + top wins", "gap": not cv["bullets"],
             "outline": [["Now", f"{cv['roles'][0]['title']} at {cv['roles'][0]['company']}" if cv["roles"] else "[current role]"],
                         ["Proof", "; ".join(short(t, 90) for t in top) or "[2 wins with numbers]"],
                         ["Why this", f"[Why {title} at {co}, in one line]"]]}
    intro["text"] = (f"{cv['summary'] or '[One line on who you are professionally.]'} "
                     + (f"Most recently, {_lower1(short(top[0], 160))}. " if top else "")
                     + (f"Before that, {_lower1(short(top[1], 140))}. " if len(top) > 1 else "")
                     + f"I'm excited about {title} at {co} because [the one reason that ties your past to their need].")
    qs.append({"id": "q1", "q": f"Walk me through your background, and why this {title} role.", "kind": "Opener",
               "why": "Almost every interview starts here. It sets the frame for everything after.",
               "look_for": ["Under 90 seconds", "Past → present → why this role", "1-2 wins with numbers",
                            "Ends on why them"], "comp": None, "req": "", "answer": intro, "also": []})

    # 2. why company
    about = (brief or {}).get("what_they_do") or []
    why_ans = {"source": "Company brief + your CV", "gap": not about,
               "outline": [["What they do", short(about[0], 140) if about else "[what they sell, to whom]"],
                           ["What excites you", "[a product detail, a recent move, or their market]"],
                           ["Why you fit", ", ".join(gap.get("matched", [])[:4]) or "[2 skills they need that you have]"]]}
    lead = short(about[0], 160) if about else ""
    lead = lead if lead.lower().startswith(co.lower()) else f"{co} {_lower1(lead)}" if lead else f"{co} [does X for Y]"
    why_ans["text"] = (lead.rstrip(".…") + ". "
                       + "What draws me is [one specific thing: product, mission, a recent launch]. "
                       + (f"And the role lines up with what I've done: {', '.join(gap.get('matched', [])[:3])}." if gap.get("matched")
                          else "And the role lines up with what I've done: [2 skills]."))
    qs.append({"id": "q2", "q": f"Why {co}? What do you know about what we do?", "kind": "Motivation",
               "why": "Tests whether you did your homework and really want this job.",
               "look_for": ["Specific facts, not flattery", "A recent product or news item", "A link to your own goals"],
               "comp": None, "req": "", "answer": why_ans, "also": []})

    # 3. competency questions from JD requirement lines
    seen, used_req = set(), set()
    for comp, _w in comp_weights(reqs):
        if len(seen) >= 5:
            break
        cands = [x for x in reqs if comp in x["comps"] and x["text"] not in used_req]
        if not cands:
            continue
        r = next((x for x in cands if x["must"]), cands[0])
        seen.add(comp)
        used_req.add(r["text"])
        add(f"The role asks you to “{short(r['text'], 120)}”. Tell me about a time you did exactly that.",
            comp, f"From the job post ({'must-have' if r['must'] else 'nice to have' if r['nice'] else 'requirement'}).",
            COMPS[comp]["look"], comp, r["text"])
    # 3b. if the JD was thin, use the role bank's competencies
    for comp in ["Execution", "Analytics", "Stakeholders", "Prioritisation"]:
        if len(seen) >= 3:
            break
        if comp not in seen:
            seen.add(comp)
            add(COMPS[comp]["q"], comp, f"A core {role_label.lower()} competency.", COMPS[comp]["look"], comp)

    # 4. gaps: things the JD wants that your CV doesn't show
    must_txt = " ".join(r["text"].lower() for r in reqs if r["must"])
    missing = sorted(gap.get("missing", []), key=lambda k: (k not in must_txt, gap["missing"].index(k)))
    for kw in missing[:2]:
        ev = evidence(cv, stories, kw, None, used)
        best = ev[0] if ev and ev[0]["score"] > 1 else None
        qs.append({"id": f"q{len(qs) + 1}", "q": f"This role leans on {kw}. Your CV doesn't mention it. How would you handle that?",
                   "kind": "Gap", "why": f"“{kw}” is in the job post but not in your CV, so expect a probe.",
                   "look_for": ["Honesty, no bluffing", "The closest thing you've done", "A concrete 30-day ramp-up plan"],
                   "comp": None, "req": kw, "also": [],
                   "answer": {"source": f"Closest CV evidence: {best['title']}" if best else "",
                              "gap": not best,
                              "outline": [["Be honest", f"I haven't owned {kw} directly."],
                                          ["Closest thing", short(best["text"], 150) if best else "[an adjacent project]"],
                                          ["Ramp-up plan", f"[First 30 days: who you'd learn from, what you'd read, a small {kw} win you'd ship]"]],
                              "text": f"I haven't owned {kw} directly yet, so I won't pretend. The closest I've done is "
                                      + (_lower1(short(best["text"], 160)) if best else "[an adjacent project]")
                                      + f". In my first 30 days I'd [learn from X, study Y] and aim to ship a small {kw} win by [date]."}})

    # 5. a role-typical skills / case question, made specific to the company
    cases = [q for q in role_bank if q.get("type") in ("case", "skills")]
    case = next((q for q in cases if re.search(r"\[(our|your|the)[^\]]*\]|\bus\b", q["q"])), cases[0] if cases else None)
    if case:
        q = re.sub(r"\[(our|your|the)[^\]]*\]", f"{co}'s main product", case["q"])
        q = re.sub(r"\bus\b", co, q)
        q = re.sub(r"\[([^\]]+)\]", lambda m: m.group(1), q)
        qs.append({"id": f"q{len(qs) + 1}", "q": q, "kind": "Case", "why": f"Typical {role_label.lower()} case round.",
                   "look_for": [case.get("tip", "Structure first")], "comp": None, "req": "", "also": [],
                   "answer": {"source": "Framework", "gap": False,
                              "outline": [["Clarify", "Goal, user, constraints. Ask 2 questions before answering."],
                                          ["Structure", case.get("tip", "")],
                                          ["Decide", "Pick one and say why. Name a metric and a risk."]],
                              "text": "Think out loud. Clarify first, lay out your structure, go deep on one branch, "
                                      "and end with a recommendation and how you'd measure it."}})
    # 6. a behavioural classic
    add("Tell me about a time something you owned failed. What did you do?", "Behavioural",
        "Every loop has one. They test self-awareness and learning.",
        ["A real failure, owned", "What you did in the moment", "What you changed after", "Brief, not defensive"],
        None, "", "failed missed mistake learned delay")
    return qs


# ---------------------------------------------------------------- company research (free sources, best effort)
def _get(url: str, timeout: int = 8) -> bytes | None:
    try:
        with urlopen(Request(url, headers=UA), timeout=timeout) as r:
            return r.read()
    except Exception:
        return None


def wikipedia(company: str) -> dict | None:
    name = re.sub(r"\s*\(.*?\)", "", company).strip()
    for cand in (name, f"{name} (company)"):
        raw = _get("https://en.wikipedia.org/api/rest_v1/page/summary/" + quote(cand.replace(" ", "_")))
        if not raw:
            continue
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        ext, desc = d.get("extract") or "", (d.get("description") or "").lower()
        if d.get("type") == "disambiguation" or not ext:
            continue
        biz = re.search(r"compan|corporat|startup|platform|firm|bank|service|software|provider|brand|business|"
                        r"manufactur|retailer|conglomerate|marketplace|app\b|fintech", desc + " " + ext[:300].lower())
        if biz:
            return {"summary": ext, "url": (d.get("content_urls") or {}).get("desktop", {}).get("page", "")}
    return None


def news(company: str, n: int = 5) -> list[dict]:
    raw = _get(f"https://news.google.com/rss/search?q={quote_plus(chr(34) + company + chr(34))}&hl=en&gl=IN&ceid=IN:en")
    if not raw:
        return []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []
    out = []
    for it in root.iter("item"):
        t = (it.findtext("title") or "").strip()
        if t:
            out.append({"title": t, "url": it.findtext("link") or "", "date": (it.findtext("pubDate") or "")[5:16]})
        if len(out) >= n:
            break
    return out


def peers(company: str, info: dict | None, n: int = 6) -> list[str]:
    if not info:
        return []
    try:
        data = json.loads((HERE / "companies.json").read_text(encoding="utf-8"))["companies"]
    except (OSError, ValueError, KeyError):
        return []
    sec = info.get("sector")
    same = [c["name"] for c in data if c.get("sector") == sec and c["name"] != info["name"]]
    return same[:n]


def rounds(role: str, info: dict | None) -> list[dict]:
    base = [{"name": a, "what": b, "prep": c} for a, b, c in ROUNDS.get(role, ROUNDS["general"])]
    cat = (info or {}).get("category", "")
    if cat in ("india_startup", "india_rising"):
        base.insert(min(2, len(base)), {"name": "Founder / leadership chat",
                                        "what": "Ownership, pace, why a startup, your thinking on their market.",
                                        "prep": "Read their last 3 news items and have a view on their next move."})
    elif cat in ("global", "india_large"):
        base.append({"name": "HR / bar-raiser", "what": "Values and consistency across your stories.",
                     "prep": "Read the company's values page and map one story to each."})
    return base


ABOUT = re.compile(r"\b(we are|we're|our mission|we build|we help|founded|backed by|our customers|our platform|"
                   r"is a leading|is building|is india's|is the|serves|trusted by|millions of)\b", re.I)


def what_they_do(j: dict) -> list[str]:
    about = ABOUT
    co = (j.get("company") or "").lower()
    return [s for s in sentences(j.get("description") or "") if about.search(s) or (co and s.lower().startswith(co))][:4]


def products(j: dict) -> list[str]:
    pat = re.compile(r"\b(platform|product|app|suite|api|solution|marketplace|tool)s?\b", re.I)
    return [s for s in sentences(j.get("description") or "") if pat.search(s) and not re.search(
        r"\b(you will|you'll|experience|requirement|must)\b", s, re.I)][:3]


# ---------------------------------------------------------------- mock interview rubric
DIMS = ["Structure", "Specifics", "Ownership", "Relevance", "Length"]


def score_answer(answer: str, q: dict, seconds: int = 0, cv_text: str = "", stories: list | None = None) -> dict:
    a = (answer or "").strip()
    low = " " + a.lower() + " "
    n = len(re.findall(r"\S+", a))
    nums = len(NUM.findall(a))
    i_n = len(re.findall(r"\b(i|i'm|i've|i'd|my|me)\b", low))
    we_n = len(re.findall(r"\b(we|we're|we've|our|us|the team)\b", low))
    fill = sum(len(re.findall(rf"\b{re.escape(f)}\b", low)) for f in FILLERS)
    kind = q.get("kind", "")
    star = {"Situation": bool(re.search(r"\b(when|at |while|in my|last year|context|back in|our team was|the problem)", low)),
            "Task": bool(re.search(r"\b(goal|task|responsib|asked to|needed to|had to|target|my job)", low)),
            "Action": bool(re.search(r"\bi (\w+ed|built|led|ran|wrote|set|took|made|chose|spoke|got|drove|did|sat)\b", low)),
            "Result": bool(re.search(r"\b(result|so that|which led|outcome|increas|reduc|grew|cut|improv|saved|won|"
                                     r"launched|learn)", low) or nums)}
    frame = len(re.findall(r"\b(first|second|third|then|finally|next|two things|three things)\b", low))
    case_like = kind in ("Case", "Opener", "Motivation", "Gap", "Follow-up")
    s_struct = 1 + sum(star.values()) if not case_like else min(5, 1 + frame + (2 if n > 80 else 0) + (1 if star["Result"] else 0))
    s_spec = min(5, 1 + nums * 2 + (1 if re.search(r"\b[A-Z][a-zA-Z]{2,}\b", a[1:]) else 0)) if n else 1
    s_own = 3 if kind == "Follow-up" and not (i_n or we_n) else 5 if i_n >= 3 and i_n >= we_n else 4 if i_n >= 2 and i_n >= we_n * 0.7 else 3 if i_n >= 1 else 1
    want = set(words(q.get("q", "") + " " + q.get("req", "") + " " + " ".join(COMPS.get(q.get("comp") or "", {}).get("kw", []))))
    hit = want & set(words(a))
    s_rel = 1 if not n else min(5, 2 + len(hit)) if want else 3
    lo, hi = (90, 180) if kind == "Opener" else (20, 120) if kind == "Follow-up" else (120, 330)
    if kind == "Follow-up":
        s_struct = 4 if n >= 15 else 2
    if seconds:
        s_len = 5 if 50 <= seconds <= 150 else 4 if 35 <= seconds <= 190 else 2
    else:
        s_len = 5 if lo <= n <= hi else 4 if lo * .6 <= n <= hi * 1.3 else 2 if n >= 25 else 1
    if fill >= 4:
        s_len = max(1, s_len - 1)
    sc = {"Structure": s_struct, "Specifics": s_spec, "Ownership": s_own, "Relevance": s_rel, "Length": s_len}
    total = round(sum(sc.values()) / len(sc), 1)

    good, fix = [], []
    if s_struct >= 4:
        good.append("Clear structure: the listener can follow situation → action → result."
                    if not case_like else "Well signposted: you laid out your thinking step by step.")
    else:
        missing = [k for k, v in star.items() if not v]
        fix.append("Answer the follow-up directly in 1-2 sentences, then stop." if kind == "Follow-up" else
                   ("Add the missing STAR parts: " + ", ".join(missing) + ". " if missing and not case_like else
                    "Signpost your answer ('Two things: first… second…') so it's easy to follow. ")
                   + "Open with one line of context, then say what YOU did.")
    if nums:
        good.append(f"You used {nums} number{'s' if nums > 1 else ''}. That makes it believable.")
    else:
        fix.append("No numbers yet. Add one result in %, ₹, users or time saved. Interviewers remember numbers.")
    if s_own >= 4:
        good.append("Strong ownership: you said what you personally did.")
    elif we_n > i_n:
        fix.append(f"You said 'we/our' {we_n} times and 'I/my' {i_n}. Swap in 'I' for the parts you drove.")
    if s_rel >= 4:
        good.append("On target: you hit the words the question and the job care about (" + ", ".join(sorted(hit)[:4]) + ").")
    elif want and n:
        miss = [w for w in words(q.get("req") or q.get("q", "")) if w not in hit and not w.endswith("ly")][:4]
        fix.append("Tie it back to the question" + (f": mention {', '.join(miss)}" if miss else "")
                   + ". End with one line on why it matters for this role.")
    if s_len <= 2:
        fix.append(f"{'Too short' if n < lo else 'Too long'}: {n} words. Aim for {lo}-{hi} words (about 1-2 minutes spoken).")
    if fill >= 3:
        fix.append(f"{fill} filler words (like, basically, um…). Pause instead.")
    if not good:
        good.append("You gave it a go. Getting a first answer down is the hardest part.")

    # stronger version: keep the user's own sentences, slot them into STAR, fill gaps from the CV
    if s_len == 4 and n < lo:
        fix.append(f"A little short ({n} words). Add one line on how you did it, aim for {lo}-{hi}.")
    sents = [_defill(s) for s in re.split(r"(?<=[.!?])\s+", a) if s.strip()]
    plain = [re.sub(r"^(first(ly)?|second(ly)?|then|next|finally|also),?\s+", "", s, flags=re.I) for s in sents]
    cv = parse_cv(cv_text)
    ev = evidence(cv, stories or [], q.get("q", "") + " " + q.get("req", "") + " " + a, q.get("comp"))
    best = ev[0] if ev and ev[0]["score"] > 1 else (ev[0] if ev else None)
    slot = {"S": [], "T": [], "A": [], "R": [], "L": []}
    for s in sents:
        l = s.lower()
        k = ("L" if re.search(r"\b(learn|takeaway|next time|differently)", l) else
             "T" if re.search(r"\b(goal|task|asked to|needed to|had to|target|my job)", l) else
             "R" if NUM.search(s) and re.search(r"\b(result|grew|cut|increas|reduc|improv|fell|rose|saved|launched|won)", l) else
             "A" if re.search(r"\b(i|my)\b", l) else
             "S" if not slot["S"] else "A")
        slot[k].append(s)
    tie = _need(q.get("req", ""))
    if kind == "Follow-up":
        better = " ".join(sents[:4]) + ("" if nums else " [Add the number: %, ₹, users or time.]")
    elif kind in ("Opener", "Motivation", "Gap") and total < 3.5 and (q.get("answer") or {}).get("text"):
        better = q["answer"]["text"]
    elif case_like:
        better = " ".join(sents[:7]) if s_struct >= 4 else \
            "Two things matter here. First, " + (_lower1(plain[0]) if plain else "[point one]") + \
            " Second, " + (_lower1(plain[1]) if len(plain) > 1 else "[point two]") + \
            " So my answer is [one-line recommendation], and I'd measure it by [metric]."
    else:
        act_b, res_b = split_bullet(best["text"]) if best and best["kind"] == "cv" else ("", "")
        acts = " ".join(re.sub(r"\bwe\b", "I", re.sub(r"^We\b", "I", x)) for x in slot["A"])
        better = " ".join(x for x in [
            " ".join(slot["S"]) or (f"When I was {best['title']} at {best['company']}, [the problem in one line]."
                                    if best and best.get("company") else "[Set the scene in one line: where, when, what was at stake.]"),
            " ".join(slot["T"]) or "My goal was [the target I owned].",
            acts or (_i_did(act_b) + "." if act_b else "[What I did, step by step, in 2-3 lines.]"),
            " ".join(slot["R"]) or ((f"As a result, it {re.sub(r'^(which|that) ', '', res_b)}." if re.match(r"(which|that) ", res_b)
                                     else f"As a result, I {_lower1(res_b)}.") if res_b else "[The result, with a number.]"),
            " ".join(slot["L"]),
            f"That's why I'm confident I can {_lower1(tie)}." if tie and len(tie) > 20 else ""] if x)
    if not case_like and total < 2.5 and best:
        better = draft_answer(best, q.get("req", ""), "this company", q.get("comp"))["text"]
    if best and best["kind"] == "cv" and not (set(words(best["text"])) & set(words(a))) and s_spec <= 3:
        fix.append(f"Your CV has a stronger example to borrow from: “{short(best['text'], 140)}”.")

    # one follow-up, aimed at the weakest dimension
    weakest = min(sc, key=lambda k: sc[k])
    follow = {"Specifics": "What was the measurable result? Give me a number.",
              "Ownership": "What did you personally do, as opposed to the team?",
              "Structure": "Take me back to the start: what was the situation, and what were you asked to achieve?",
              "Relevance": f"How does that connect to {short(q.get('req') or 'this role', 80).lower()}?",
              "Length": "Give me the 30-second version: what's the one thing you want me to remember?"}[weakest]
    if total >= 4.2:
        follow = "Good. What was the hardest moment in that, and what would you do differently now?"
    return {"score": total, "dims": sc, "strengths": good[:3], "improve": fix[:4], "better_answer": better,
            "follow_up": follow, "words": n, "numbers": nums, "star": star}
