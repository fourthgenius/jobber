# Jobber

Jobber is a free, open-source job search assistant that runs on your own computer. It finds jobs that match **your** roles, locations and pay floor across job boards and company career pages, shows them in one clean dashboard with a CV fit score, tracks your applications, helps you fill application forms faster, and gets you ready for interviews.

Works for any field: product, engineering, data, design, marketing, sales, operations, finance, or your own custom list of titles. Works in any country and currency.

> **It never applies for you.** Jobber fills forms and drafts text, but you review and press Submit yourself, every time. It never sends messages to recruiters on your behalf.

## What you get

| Page | What it does |
|---|---|
| **Setup** (`/setup`) | First-run wizard: pick a role preset (or write your own titles), cities, remote preferences, currency and pay floor, experience, skills, job sources, your application details and your CV. |
| **Jobs** (`/`) | Every matching job, newest first, de-duplicated across sites. Fit % against your CV with reasons and gaps, salary, experience asked, source. Track each job: saved → applied → interviewing → offer / rejected, with notes. |
| **Batch apply** (`/apply`) | Queue jobs and open them a few at a time in Chrome. The included Chrome extension fills the form, attaches your CV and writes a cover letter. You press Submit. Per-site pacing avoids hammering one portal. |
| **Copilot** (`/copilot`) | Apply-today queue, follow-up reminders, outreach drafts and a contact tracker, cover letter templates, tailored CVs (printable to PDF), keyword gap vs your CV, funnel insights, company watchlist, funding news radar, optional Telegram daily digest. |
| **Interview prep** (`/prep`) | Company brief built from the job post plus research links and your notes; question bank by role type (80+ questions with tips); STAR story bank with competency coverage; timed mock practice with a self-check rubric and history. |

**AI is optional.** Everything above works with templates and rules. Add a [Claude API key](https://console.anthropic.com) (Copilot → Setup) to also get AI fit scores, written cover notes, rewritten CVs, screening answers, richer interview briefs, job-specific questions and feedback on your mock answers.

## Quick start

You need **Python 3.10+** ([python.org](https://www.python.org/downloads/)). On Windows, tick "Add Python to PATH" during install.

```bash
git clone https://github.com/fourthgenius/jobber.git
cd jobber
```

Then start it:

- **Windows:** double-click `start.bat`
- **Mac / Linux:** run `./start.sh` in a terminal

The first run creates a private Python environment (`.venv`) and installs dependencies, which takes a minute or two. Your browser then opens **http://127.0.0.1:8765** at the Setup page. Fill it in, press **Save**, go to **Jobs** and press **Find new jobs**. The first full search takes about 5-10 minutes because LinkedIn and job sites are queried slowly on purpose.

<details><summary>Manual install (any OS)</summary>

```bash
python3 -m venv .venv
# Windows: .venv\Scripts\activate     Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
python app.py
```
</details>

### LinkedIn (optional)
LinkedIn is read from its public job pages in a real, **logged-out** browser window, so no account is involved. It needs Google Chrome or Microsoft Edge installed. If neither is found, run `python -m playwright install chromium` once inside the virtual environment. To skip LinkedIn, untick it in Setup.

### Chrome extension (for faster applying)
1. Open `chrome://extensions` and turn on **Developer mode**.
2. Click **Load unpacked** and choose the `chrome-extension` folder in this project.
3. Pin "Jobber Autofill". On Greenhouse, Lever, Ashby, Workday, SmartRecruiters, Workable and most forms it fills your details from Setup and attaches your resume. Press Alt+Shift+F on any other site. It never presses Submit.

### Daily search + Telegram digest (optional)
- **Telegram:** create a bot with @BotFather, paste the token in Copilot → Setup, send "hi" to your bot, press **Find my chat** then **Send test**.
- **Windows:** Copilot → Setup → **Schedule daily** (or run `install_daily.bat`) registers a daily 08:30 task.
- **Mac / Linux:** `crontab -e` and add `30 8 * * * /full/path/to/jobber/daily.sh`.

## Where to search

| Source | How |
|---|---|
| Company career portals: Greenhouse, Lever, Ashby, SmartRecruiters, Workable | Official public job APIs. Seed companies live in `config.json` → `company_boards`; more are discovered automatically from apply links. |
| Remotive, Remote OK, Himalayas, Jobicy, We Work Remotely | Official public APIs / RSS (remote jobs). |
| LinkedIn | Public job pages, logged-out browser, slow pacing. |
| Indeed, Naukri, Glassdoor | [python-jobspy](https://github.com/speedyapply/JobSpy), public listings, no login. Naukri is India-only. |
| Anything else (Wellfound, YC, company sites…) | Use the **Save to Jobber** bookmarklet on Copilot → Setup to add any job page to your tracker. |

Please respect each site's terms of use. The app keeps volumes low, uses your own internet connection, and never logs in anywhere.

## Your data stays yours

Everything runs on `127.0.0.1` (your own machine). Your settings and data are plain files next to the app, and all of them are in `.gitignore` so they're never committed if you fork the project:

| File | What |
|---|---|
| `config.json` | Your search settings (created from `config.example.json`). |
| `profile.json` | Your application form answers (from `profile.example.json`). |
| `cv.md`, `resume.pdf` | Your CV text and the PDF attached to applications. |
| `secrets.json` | Optional Claude API key and Telegram token. |
| `jobs.db` | Jobs, statuses, notes, stories and practice history (SQLite). |

Back these up if you care about them; delete them to start fresh.

## Customising

- **Everything common** is on the Setup page.
- **Advanced** (Setup → Advanced, or edit `config.json`): `company_boards` (career portals to watch), `copilot.funding_feeds` (RSS feeds for the funding radar), `copilot.cv_angles` (role-specific CV summaries used by the rule-based CV tailor), `copilot.queue_min_fit` and other thresholds, `fx_to_home` exchange rates.
- **Interview questions:** add your own in `questions.json`; each role type has `match` words that pick it from a job title.
- **Cover letters:** edit templates on Copilot → Cover letters, or in `cover_templates.json`. Placeholders: `{role} {company} {hook} {name} {headline} {achievement} {current_company} {date}`.
- **Company directory:** `companies.json` powers the Radar tab. Replace it with companies in your own market.

## Project layout

```
app.py              local web server (stdlib only) and routes
settings.py         config / profile / CV loading and the Setup API
fetch.py            runs every enabled source, filters, stores
sources.py, ats.py, linkedin_browser.py   job sources
filters.py, fit.py  relevance filters, salary parsing, CV fit score
store.py            SQLite storage, de-duplication, status tracking
copilot.py, llm.py, alerts.py             Copilot, optional AI, Telegram
autofill.py         Batch apply + Chrome extension API
prep.py             Interview prep
*.html, theme.css   the pages
chrome-extension/   the autofill extension
```

## Contributing

Issues and pull requests are welcome: new job sources, better form-filling rules for more portals, more interview questions, translations. Please keep the two ground rules: **never auto-submit an application, and never log in to a job site on the user's behalf.**

## License

[MIT](LICENSE)
