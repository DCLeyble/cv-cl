"""
Unattended version of the /cv-cl workflow, powered by the DeepSeek API.

Each run first searches for new jobs (LinkedIn, using your URLs in
jobs/linkedin_urls.txt; Google Jobs, which also surfaces JobStreet and other
boards, when SERPAPI_API_KEY is set; Freelancer only if FREELANCER_ENABLED)
and drops them into jobs/inbox/. You can also paste job descriptions there
yourself as .txt files (add a "Company: X" line if the posting doesn't name it).
Jobs already seen are remembered in jobs/seen.json. Then run:

    python pipeline.py

For each job it grades fit, and for Strong/Partial fits writes a tailored
resume + cover letter (HTML and PDF) to output/. Every job is logged to your
Google Sheet when SHEETS_WEBHOOK_URL is set (optional). Processed
files move to jobs/done/ (or jobs/skipped/ for Weak fits). Failed jobs stay in
the inbox so the next run retries them.

Nothing is ever submitted. Every document is a draft for you to review.

Setup:
    1. Set DEEPSEEK_API_KEY (copy .env.example to .env, or export it in your shell;
       Windows: setx DEEPSEEK_API_KEY "sk-..." then open a new terminal)
    2. Optional, Google Jobs search: sign up at serpapi.com, then
       setx SERPAPI_API_KEY "..."   Each run uses one search per entry in
       QUERIES, so check that against your plan's monthly limit.
    3. Optional, Google Sheets logging:
       - New Google Sheet > Extensions > Apps Script, paste:

            function doPost(e) {
              SpreadsheetApp.getActiveSpreadsheet().getSheets()[0]
                .appendRow(JSON.parse(e.postData.contents));
              return ContentService.createTextOutput('ok');
            }

       - Deploy > New deployment > Web app, "Execute as: Me",
         "Who has access: Anyone". Copy the web app URL, then:
       - setx SHEETS_WEBHOOK_URL "https://script.google.com/macros/s/.../exec"
       Keep that URL private: anyone who has it can add rows.

Self-check (no network, no API key needed):
    python pipeline.py --selftest
"""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

BASE = Path(__file__).resolve().parent
INBOX, DONE, SKIPPED = (BASE / "jobs" / d for d in ("inbox", "done", "skipped"))
SEEN = BASE / "jobs" / "seen.json"
OUTPUT = BASE / "output"
PDF_DIR = OUTPUT / "PDF"
MODEL = "deepseek-chat"
LOG_COLUMNS = ["Date", "Company", "Role", "Fit", "Reason", "Matched keywords",
               "Missing keywords", "Resume PDF", "Cover letter PDF", "Status", "Source", "Link"]

# Job search. Edit freely: each query costs one SerpApi search per run.
QUERIES = ["Software QA Engineer", "QA Automation Engineer", "Software Tester", "Software QA Analyst"]

# LinkedIn's public (logged-out) job pages. No LinkedIn account is used. Kept deliberately
# gentle: one page (10 jobs) per search, a pause before every request, and it stops for the
# run the moment LinkedIn rate-limits. Set LINKEDIN_ENABLED = False to turn it off.
LINKEDIN_ENABLED = True
FREELANCER_ENABLED = False   # Freelancer.com gigs; set True to search it again
# Your own LinkedIn job search URLs, one per line (# for comments). Used instead of QUERIES
# for LinkedIn whenever the file has at least one URL.
LINKEDIN_URLS_FILE = BASE / "jobs" / "linkedin_urls.txt"
LINKEDIN_PAGES = 3           # 10 jobs per page, per search; page 1 alone repeats every run
LINKEDIN_DELAY = 3           # seconds before each request
LINKEDIN_POSTED = "r86400"   # posted in the last 24 hours (r604800 = last week)
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

# Phrases that must never reach a document. The prompt forbids them; this catches slips.
# ponytail: fixed phrase list, extend it when a new kind of leak shows up in a draft.
RED_FLAGS = re.compile(r"base (cv|resume)|the prompt|as an ai|within \d+[- ]hours?|"
                       r"\b\d+[- ]hour (window|turnaround)|the last (year|\d+ months)", re.I)
# Job sites you never want, matched against the source name and the apply link.
BLOCKED_SITES = re.compile(r"jobleads", re.I)
# Job filters, applied before DeepSeek. A job is dropped only when it clearly states
# something outside these; a salary, setup, or location that isn't mentioned passes.
# Salary converted once on 2026-09-30 at 62.60 PHP per USD (exchangerate-api.com),
# 173.33 hours a month (40h x 52 weeks / 12). Not live: edit by hand if the rate moves.
MIN_PHP_MONTHLY, MIN_USD_MONTHLY = 55_000, 878.65
MIN_PHP_HOURLY, MIN_USD_HOURLY = 317.31, 5.07
PHP_PER_USD, HOURS_PER_MONTH = 62.60, 173.33
TO_MONTHLY = {"day": 21.67, "daily": 21.67, "week": 4.33, "month": 1, "mo": 1,
              "year": 1 / 12, "yr": 1 / 12, "annum": 1 / 12, "annual": 1 / 12}
SALARY = re.compile(
    r"(₱|php|\$|usd)\s*(\d[\d,]*(?:\.\d+)?)\s*(k)?"
    r"(?:\s*(?:-|–|to)\s*(?:₱|php|\$|usd)?\s*(\d[\d,]*(?:\.\d+)?)\s*(k)?)?"
    r"[^\n]{0,30}?\b(hour|hr|month|mo\b|year|yr\b|annum|annual|day|daily|week)", re.I)
REMOTE = re.compile(r"\b(remote|work[- ]from[- ]home|wfh|home[- ]based|anywhere|telecommut)", re.I)
HYBRID = re.compile(r"\bhybrid\b", re.I)
# ponytail: keyword match, so a stray "on-site training" in an otherwise silent post drops it.
ONSITE = re.compile(r"\b(on[- ]?site|office[- ]based|in[- ]office|work(ing)? from (the )?office|"
                    r"report(ing)? to (the )?office)", re.I)
METRO_MANILA = re.compile(r"manila|\bncr\b|makati|taguig|\bbgc\b|bonifacio|pasig|ortigas|"
                          r"quezon city|mandaluyong|pasay|para[ñn]aque|muntinlupa|alabang|"
                          r"las pi[ñn]as|marikina|san juan|caloocan|valenzuela|malabon|navotas|"
                          r"pateros|eastwood", re.I)
# Cheap title filter so obviously unrelated posts never cost a DeepSeek call.
# ponytail: keyword match on titles only; DeepSeek's fit grade catches the rest.
RELEVANT = re.compile(r"\b(qa|quality|test|tester|testing|sdet|automation)\b", re.I)

RULES = """
You are running NON-INTERACTIVELY. Nobody can answer questions, so:
- Never ask the user anything. Never invent a skill, tool, employer, title,
  certification, number, or achievement that is not in the base CV. If a detail
  is missing, leave it out. This overrides everything else.
- Grade fit as Strong, Partial, or Weak using the optimizer guide. If Weak, set
  "resume" and "cover_letter" to null and stop there.
- Apply the humanizer guide to every bullet and paragraph. No em dashes or en
  dashes anywhere. Plain text only, no HTML, no markdown.
- Cover letter: 3 or 4 short paragraphs. Name real gaps honestly in one line.
- Write as the candidate. Never mention the "base CV", "base resume", this
  prompt, or any source document; the reader must not see how it was written.
- Never make commitments for the candidate: no availability, turnaround times,
  start dates, rates, or promised deliverables. The candidate decides those.
- Never invent timeframes ("the last year", "for 6 months") for any experience.
  Only use dates that appear in the base CV.
- Do not turn the posting's tasks into claimed experience. Describe only tasks
  and techniques the base CV names; say how that experience applies instead.
- Never offer to share past bug reports, code, or documents from an employer.
- If the posting asks applicants to include a code, keyword, or phrase, include it.
- Keep all contact details, dates, education, and certifications exactly as in
  the base CV.

Respond with a single json object in exactly this shape:
{"company": str, "role": str, "fit": "Strong"|"Partial"|"Weak",
 "fit_reason": str (1-2 sentences), "keywords_matched": [str],
 "keywords_missing": [str],
 "resume": {"summary": str,
            "skills": [{"label": str, "value": str}],
            "experience": [{"title": str, "company": str, "dates": str, "bullets": [str]}],
            "education": [{"degree": str, "school": str, "details": str}],
            "certifications": [{"name": str, "issuer": str, "details": str}]} | null,
 "cover_letter": {"greeting": str, "paragraphs": [str]} | null}
"""


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def contact_from_cv(cv: str) -> dict:
    """Name and contact details come straight from the base CV, never the model."""
    lines = [l.strip() for l in cv.splitlines() if l.strip()]
    parts = [p.strip() for p in lines[1].split("|")]
    return {"name": lines[0].title(), "location": parts[0], "email": parts[1],
            "phone": parts[2], "last": lines[0].split()[-1].title()}


def clean(text: str) -> str:
    """Escape for HTML and enforce the no-dash rule even if the model slips."""
    return html.escape(text.replace(" — ", ", ").replace("—", ", ").replace("–", "-"))


def call_deepseek(system: str, user: str) -> dict:
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "response_format": {"type": "json_object"},
        "max_tokens": 8000,
    }).encode()
    req = urllib.request.Request(
        "https://api.deepseek.com/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {os.environ['DEEPSEEK_API_KEY']}"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.loads(json.load(resp)["choices"][0]["message"]["content"])


def fill(template: str, values: dict) -> str:
    for key, value in values.items():
        template = template.replace("{{" + key + "}}", value)
    return template


def build_resume(data: dict, c: dict, template: str) -> str:
    r = data["resume"]
    skills = "\n".join(
        f'      <tr><td class="label">{clean(s["label"])}</td><td>{clean(s["value"])}</td></tr>'
        for s in r["skills"])
    experience = "\n".join(
        f'''    <div class="experience-item">
      <div class="job-header">
        <div>
          <div class="job-title">{clean(e["title"])}</div>
          <div class="job-company">{clean(e["company"])}</div>
        </div>
        <div class="job-date">{clean(e["dates"])}</div>
      </div>
      <ul>
{chr(10).join(f"        <li>{clean(b)}</li>" for b in e["bullets"])}
      </ul>
    </div>''' for e in r["experience"])

    def item(title, sub, right):
        return f'''    <div class="education-item">
      <div>
        <div class="edu-degree">{clean(title)}</div>
        <div class="edu-school">{clean(sub)}</div>
      </div>
      <div class="edu-details">{clean(right)}</div>
    </div>'''

    education = "\n".join(item(e["degree"], e["school"], e["details"]) for e in r["education"])
    certs = "\n".join(item(x["name"], x["issuer"], x["details"]) for x in r.get("certifications") or [])
    additional = (f'  <section class="additional-section">\n    <h2>Certifications</h2>\n{certs}\n  </section>'
                  if certs else "")
    return fill(template, {
        "NAME": clean(c["name"]), "LOCATION": clean(c["location"]), "EMAIL": clean(c["email"]),
        "PHONE": clean(c["phone"]), "POSITION": clean(data["role"]), "SUMMARY": clean(r["summary"]),
        "SKILLS_TABLE": skills, "EXPERIENCE_ITEMS": experience,
        "EDUCATION_ITEMS": education, "ADDITIONAL_SECTIONS": additional})


def build_cover(data: dict, c: dict, template: str) -> str:
    cl = data["cover_letter"]
    today = date.today()
    return fill(template, {
        "NAME": clean(c["name"]), "LOCATION": clean(c["location"]), "EMAIL": clean(c["email"]),
        "PHONE": clean(c["phone"]), "POSITION": clean(data["role"]),
        "DATE": f"{today:%B} {today.day}, {today.year}", "GREETING": clean(cl["greeting"]),
        "PARAGRAPHS": "\n\n".join(f"  <p>{clean(p)}</p>" for p in cl["paragraphs"])})


def output_stem(company: str, last: str) -> str:
    """[Company]_[LastName], with a number added rather than overwriting an old application."""
    slug = re.sub(r"[^A-Za-z0-9]", "", company) or "Company"
    stem, n = f"{slug}_{last}", 2
    while (OUTPUT / f"{stem}_Resume.html").exists():
        stem, n = f"{slug}{n}_{last}", n + 1
    return stem


def log(row: dict) -> None:
    """Append one row to the Google Sheet. Raises on failure, so the job stays in the inbox."""
    if not os.environ.get("SHEETS_WEBHOOK_URL"):
        return
    body = json.dumps([row[k] for k in LOG_COLUMNS]).encode()
    for wait in (5, 15, None):  # Google intermittently refuses or stalls; it usually works on retry
        req = urllib.request.Request(os.environ["SHEETS_WEBHOOK_URL"], data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                reply = resp.read().decode("utf-8", "replace").strip()
        except (TimeoutError, urllib.error.URLError) as exc:
            # ponytail: a timeout may have saved the row anyway, so a retry can duplicate it.
            # A visible duplicate beats a silently missing row.
            reply = f"no reply ({exc})"
        # Apps Script answers HTTP 200 even when the script fails, so only an exact "ok" counts.
        if reply == "ok":
            return
        if wait:
            time.sleep(wait)
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", reply))[:200]
    raise RuntimeError(f"Google Sheet did not accept the row after 3 tries: {text}")


def salary_ok(text: str) -> bool:
    """False only if every stated PHP/USD salary is below the minimum. Nothing stated passes."""
    verdicts = []
    for cur, lo, lo_k, hi, hi_k, period in SALARY.findall(text):
        amount = float((hi or lo).replace(",", "")) * (1000 if (hi_k if hi else lo_k) else 1)
        usd, period = cur.lower() in ("$", "usd"), period.lower()
        if period in ("hour", "hr"):
            ok = amount >= (MIN_USD_HOURLY if usd else MIN_PHP_HOURLY)
            monthly = amount * HOURS_PER_MONTH
        else:
            monthly = amount * TO_MONTHLY[period]
            ok = monthly >= (MIN_USD_MONTHLY if usd else MIN_PHP_MONTHLY)
        if monthly * (PHP_PER_USD if usd else 1) >= 10_000:  # smaller is an allowance, not pay
            verdicts.append(ok)
    return not verdicts or any(verdicts)


def filter_reason(job: dict) -> str | None:
    """Why a job fails the site / salary / setup / location filters, or None if it passes."""
    if BLOCKED_SITES.search(f"{job.get('source', '')} {job.get('link', '')}"):
        return "blocked site"
    text = f"{job['title']} {job['location']} {job['salary']} {job['description']}"
    remote = bool(job.get("remote") or REMOTE.search(text))
    if not salary_ok(f"{job['salary']} {job['description']}"):
        return "salary"
    if remote:  # work from home: location doesn't matter
        return None
    if not HYBRID.search(text) and ONSITE.search(text):
        return "onsite"
    loc = job["location"].strip()
    if loc.lower() not in ("", "philippines") and not METRO_MANILA.search(loc):
        return "location"
    return None


def get_json(url: str, params: dict) -> dict:
    with urllib.request.urlopen(f"{url}?{urllib.parse.urlencode(params)}", timeout=60) as resp:
        return json.load(resp)


class Blocked(Exception):
    """LinkedIn is rate-limiting us. Stop asking for the rest of this run."""


def linkedin_get(url: str) -> str:
    time.sleep(LINKEDIN_DELAY)
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": BROWSER_UA}),
                                    timeout=30) as resp:
            return resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        if exc.code in (429, 999):  # 999 is LinkedIn's "go away"
            raise Blocked(f"LinkedIn returned {exc.code}") from exc
        raise


def html_field(chunk: str, cls: str) -> str:
    m = re.search(r'class="[^"]*\b' + cls + r'\b[^"]*"[^>]*>(.*?)</', chunk, re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", m.group(1)))).strip() if m else ""


def parse_linkedin_cards(page: str) -> list[dict]:
    """Job cards from a LinkedIn guest search page."""
    # ponytail: regex on LinkedIn's CSS class names; if they rename them, this returns [] until updated.
    cards = []
    for chunk in page.split('data-entity-urn="urn:li:jobPosting:')[1:]:
        job_id = re.match(r"\d+", chunk)
        if not job_id:
            continue
        link = re.search(r'class="base-card__full-link[^"]*"\s+href="([^"?]+)', chunk)
        cards.append({"id": job_id.group(0), "title": html_field(chunk, "base-search-card__title"),
                      "company": html_field(chunk, "base-search-card__subtitle"),
                      "location": html_field(chunk, "job-search-card__location"),
                      "salary": html_field(chunk, "job-search-card__salary-info"),
                      "link": link.group(1) if link else f"https://www.linkedin.com/jobs/view/{job_id.group(0)}"})
    return cards


def linkedin_params(url: str) -> dict | None:
    """The filters from a LinkedIn job search URL copied out of your browser, or None if it isn't one."""
    parts = urllib.parse.urlparse(url.strip())
    if "linkedin.com" not in parts.netloc or "/jobs" not in parts.path:
        return None
    # Drop the browser-only bits; keep every filter (keywords, location, f_TPR, f_E, f_JT, ...).
    return {k: v for k, v in urllib.parse.parse_qsl(parts.query)
            if k not in ("currentJobId", "origin", "refresh", "start", "trk")}


def linkedin_searches() -> list[dict]:
    """Your pasted searches if jobs/linkedin_urls.txt has any, otherwise one per entry in QUERIES."""
    lines = LINKEDIN_URLS_FILE.read_text(encoding="utf-8").splitlines() if LINKEDIN_URLS_FILE.exists() else []
    searches = []
    for line in (l.strip() for l in lines):
        if not line or line.startswith("#"):
            continue
        params = linkedin_params(line)
        if params:
            searches.append(params)
        else:
            print(f"  Skipping, not a LinkedIn job search URL: {line[:80]}")
    return searches or [{"keywords": q, "location": "Philippines", "f_TPR": LINKEDIN_POSTED} for q in QUERIES]


def search_linkedin(params: dict, seen: set) -> list[dict]:
    """LinkedIn public listings. Full descriptions are fetched only for new, relevant jobs.

    The guest search ignores LinkedIn's remote/hybrid filter (tested 2026-10-01), so work
    setup is left to filter_reason, which reads it from each job's own description.
    """
    jobs = []
    for page in range(LINKEDIN_PAGES):
        cards = parse_linkedin_cards(linkedin_get(
            "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?"
            + urllib.parse.urlencode({**params, "start": page * 10})))
        for card in cards:
            job = {"key": f"li_{card['id']}", "title": card["title"], "company": card["company"],
                   "location": card["location"], "source": "LinkedIn", "link": card["link"],
                   "salary": card["salary"], "remote": False, "description": ""}
            # Skip before fetching the description: already seen or not a QA title.
            if job["key"] in seen or not RELEVANT.search(job["title"]):
                continue
            try:
                detail = linkedin_get(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{card['id']}")
            except Blocked:
                raise
            except Exception:
                continue  # one unreadable posting must not stop the others
            desc = re.search(r'class="show-more-less-html__markup[^"]*"[^>]*>(.*?)</div>', detail, re.S)
            text = html.unescape(re.sub(r"<(br|/p|/li)[^>]*>", "\n", desc.group(1))) if desc else ""
            job["description"] = re.sub(r"<[^>]+>", "", text).strip()
            jobs.append(job)
        if len(cards) < 10:
            break  # no more pages
    return jobs


def search_google_jobs(query: str, seen: set = None) -> list[dict]:
    """Google Jobs via SerpApi. Covers LinkedIn, JobStreet and other boards Google indexes."""
    data = get_json("https://serpapi.com/search.json", {
        "engine": "google_jobs", "q": query, "location": "Philippines", "gl": "ph", "hl": "en",
        "api_key": os.environ["SERPAPI_API_KEY"]})
    return [{"key": f"gj_{j['job_id']}", "title": j["title"], "company": j.get("company_name", ""),
             "location": j.get("location", ""), "source": f"Google Jobs ({j.get('via', 'unknown')})",
             "link": (j.get("apply_options") or [{}])[0].get("link") or j.get("share_link", ""),
             "salary": j.get("detected_extensions", {}).get("salary", ""),
             "remote": j.get("detected_extensions", {}).get("work_from_home", False),
             "description": j.get("description", "")}
            for j in data.get("jobs_results", [])]


def search_freelancer(query: str, seen: set = None) -> list[dict]:
    """Freelancer.com's public project search. No key needed."""
    data = get_json("https://www.freelancer.com/api/projects/0.1/projects/active/", {
        "query": query, "limit": 20, "full_description": "true"})
    def salary(p):  # only hourly USD/PHP budgets compare to a salary; fixed-price gigs don't
        cur = {"USD": "$", "PHP": "₱"}.get(p.get("currency", {}).get("code"))
        top = (p.get("budget") or {}).get("maximum")
        return f"{cur}{top} per hour" if p.get("type") == "hourly" and cur and top else ""

    return [{"key": f"fl_{p['id']}", "title": p["title"], "company": "Freelancer client",
             "location": "Remote", "source": "Freelancer",
             "link": f"https://www.freelancer.com/projects/{p.get('seo_url', p['id'])}",
             "salary": salary(p), "remote": True, "description": p.get("description", "")}
            for p in data.get("result", {}).get("projects", [])]


def inbox_text(job: dict) -> str:
    return (f"Title: {job['title']}\nCompany: {job['company']}\nLocation: {job['location']}\n"
            f"Source: {job['source']}\nLink: {job['link']}\n\n{job['description']}\n")


def find_jobs() -> None:
    """Drop new, relevant postings into the inbox. Never refetches a job it has already seen."""
    seen = set(json.loads(SEEN.read_text())) if SEEN.exists() else set()
    sources = [(search_freelancer, QUERIES)] if FREELANCER_ENABLED else []
    if os.environ.get("SERPAPI_API_KEY"):
        sources.insert(0, (search_google_jobs, QUERIES))
    else:
        print("SERPAPI_API_KEY not set, skipping Google Jobs (JobStreet, Kalibrr, etc.).")
    if LINKEDIN_ENABLED:
        sources.insert(0, (search_linkedin, linkedin_searches()))
    added, dropped = 0, {}
    for search, searches in sources:
        for query in searches:
            try:
                results = search(query, seen)
            except Blocked as exc:  # stop asking LinkedIn for the rest of this run
                print(f"  {search.__name__} stopped: {exc}. Try again later.")
                break
            except Exception as exc:  # one source failing must not stop the others
                print(f"  {search.__name__} {query!r} failed: {exc}")
                continue
            for job in results:
                if job["key"] in seen or not job["description"] or not RELEVANT.search(job["title"]):
                    continue
                seen.add(job["key"])
                reason = filter_reason(job)
                if reason:
                    dropped[reason] = dropped.get(reason, 0) + 1
                    continue
                name = re.sub(r"[^A-Za-z0-9_-]", "", job["key"])[:60]
                (INBOX / f"{name}.txt").write_text(inbox_text(job), encoding="utf-8")
                added += 1
    SEEN.write_text(json.dumps(sorted(seen)))
    detail = ", ".join(f"{n} {r}" for r, n in dropped.items())
    print(f"Found {added} new job(s)." + (f" Filtered out: {detail}." if dropped else ""))


def process(job: Path, system: str, cv: str, c: dict, templates: tuple, browser) -> None:
    text = read(job)
    data = call_deepseek(system, f"BASE CV:\n{cv}\n\nJOB DESCRIPTION:\n{text}")
    source, link = (re.search(rf"^{k}: (.+)$", text, re.M) for k in ("Source", "Link"))
    row = {"Date": date.today().isoformat(), "Company": data["company"], "Role": data["role"],
           "Fit": data["fit"], "Reason": data["fit_reason"],
           "Matched keywords": ", ".join(data["keywords_matched"]),
           "Missing keywords": ", ".join(data["keywords_missing"]),
           "Resume PDF": "", "Cover letter PDF": "",
           "Source": source.group(1) if source else f"Manual ({job.name})",
           "Link": link.group(1) if link else ""}

    if data["fit"] == "Weak" or not data.get("resume"):
        row["Status"] = "Skipped (Weak fit)"
        dest = SKIPPED
    else:
        from html_to_pdf import render_one
        # Freelancer posts have no company name, so name their files by project id.
        company = f"Freelancer {job.stem[3:]}" if job.stem.startswith("fl_") else data["company"]
        stem = output_stem(company, c["last"])
        flags = set()
        for kind, html_text in (("Resume", build_resume(data, c, templates[0])),
                                ("CoverLetter", build_cover(data, c, templates[1]))):
            html_path, pdf_path = OUTPUT / f"{stem}_{kind}.html", PDF_DIR / f"{stem}_{kind}.pdf"
            html_path.write_text(html_text, encoding="utf-8")
            render_one(browser, html_path, pdf_path)
            row["Resume PDF" if kind == "Resume" else "Cover letter PDF"] = str(pdf_path.relative_to(BASE))
            flags |= {m.group(0) for m in RED_FLAGS.finditer(html.unescape(html_text))}
        row["Status"] = (f"NEEDS EDIT, found: {', '.join(sorted(flags))}" if flags
                         else "Draft, review before sending")
        dest = DONE

    log(row)
    shutil.move(str(job), dest / job.name)
    print(f"  {data['fit']:<8} {data['company']} | {data['role']} -> {row['Status']}")


def load_env() -> None:
    """Read KEY=value lines from .env into os.environ (existing variables win)."""
    env = BASE / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            k, sep, v = line.partition("=")
            if sep and not k.strip().startswith("#") and v.strip():
                os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


def main() -> None:
    load_env()
    if not os.environ.get("DEEPSEEK_API_KEY"):
        sys.exit("DEEPSEEK_API_KEY not set. See the setup notes at the top of pipeline.py.")
    for d in (INBOX, DONE, SKIPPED, PDF_DIR):
        d.mkdir(parents=True, exist_ok=True)
    find_jobs()
    jobs = sorted(INBOX.glob("*.txt"))
    if not jobs:
        print(f"No jobs in {INBOX}. Drop job descriptions there as .txt files.")
        return

    cv = read(BASE / "input" / "cv-base.txt")
    system = "\n\n".join([read(BASE / ".github/skills/cv-cl-optimizer/SKILL.md"),
                          read(BASE / ".github/skills/humanizer/SKILL.md"), RULES])
    templates = (read(BASE / "template/cv-template.html"),
                 read(BASE / "template/cover-letter-template.html"))
    c = contact_from_cv(cv)

    from playwright.sync_api import sync_playwright
    print(f"Processing {len(jobs)} job(s):")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for job in jobs:
            try:
                process(job, system, cv, c, templates, browser)
            except Exception as exc:  # one bad job must not stop the rest; it stays in the inbox
                print(f"  FAILED   {job.name}: {exc}")
        browser.close()


def selftest() -> None:
    cv = "JANE Q. SAMPLE\nPhilippines  |  a@b.com  |  0912  |  LinkedIn\n"
    c = contact_from_cv(cv)
    assert c == {"name": "Jane Q. Sample", "location": "Philippines",
                 "email": "a@b.com", "phone": "0912", "last": "Sample"}, c
    data = {"company": "A&B Co.", "role": "QA Engineer", "resume": {
        "summary": "Tests things — carefully & well.",
        "skills": [{"label": "Automation", "value": "Selenium <Java>"}],
        "experience": [{"title": "QA", "company": "INFOR", "dates": "2024 – Present",
                        "bullets": ["Did a thing."]}],
        "education": [{"degree": "BSIT", "school": "PLM", "details": "2024"}],
        "certifications": []},
        "cover_letter": {"greeting": "Dear Team,", "paragraphs": ["One.", "Two."]}}
    resume = build_resume(data, c, read(BASE / "template/cv-template.html"))
    cover = build_cover(data, c, read(BASE / "template/cover-letter-template.html"))
    for doc in (resume, cover):
        assert "{{" not in doc, "unfilled placeholder"
        assert "—" not in doc and "–" not in doc, "dash leaked"
    assert "carefully &amp; well" in resume and "Selenium &lt;Java&gt;" in resume
    assert "2024 - Present" in resume and "<p>Two.</p>" in cover
    assert output_stem("A&B Co.", "Sample").startswith("ABCo")
    assert all(RELEVANT.search(t) for t in ("QA Engineer II", "Software Tester", "Automation Engineer"))
    assert not any(RELEVANT.search(t) for t in ("Cold Caller", "Graphic Designer", "Contest winner"))
    job = {"key": "x", "title": "QA", "company": "Co", "location": "PH", "source": "Freelancer",
           "link": "https://x.test/1", "description": "Body."}
    assert re.search(r"^Link: (.+)$", inbox_text(job), re.M).group(1) == "https://x.test/1"
    for leak in ("My base resume does not", "within a 24-hour window", "the last year has"):
        assert RED_FLAGS.search(leak), leak
    assert not RED_FLAGS.search("I have two years of QA experience since Sep 2024.")

    for text, want in [("₱40K–₱60K a month", True), ("₱30,000 - ₱45,000 per month", False),
                       ("PHP 60,000 monthly", True), ("$6/hr", True), ("$4 per hour", False),
                       ("$1,200 per month", True), ("$700 a month", False), ("No salary listed", True),
                       ("₱3,000 monthly internet allowance", True), ("₱720,000 per year", True)]:
        assert salary_ok(text) is want, (text, want)

    def j(location="", description="", salary="", remote=False):
        return {"title": "QA Engineer", "location": location, "salary": salary,
                "description": description, "remote": remote}
    assert filter_reason(j("Makati, Metro Manila, Philippines", "Hybrid, 3 days onsite")) is None
    assert filter_reason(j("Taguig, Philippines")) is None                      # setup not stated
    assert filter_reason(j("Philippines", "This is an onsite role.")) == "onsite"
    assert filter_reason(j("Cebu City, Cebu, Philippines", "Hybrid setup")) == "location"
    assert filter_reason(j("Cebu City, Cebu, Philippines", remote=True)) is None  # WFH anywhere
    assert filter_reason(j("Anywhere", "Fully remote")) is None
    assert filter_reason(j("Pasig", salary="₱25K–₱35K a month")) == "salary"
    assert filter_reason({**j("Makati"), "source": "Google Jobs (JobLeads)"}) == "blocked site"
    assert filter_reason({**j("Makati"), "link": "https://www.jobleads.com/ph/job/123"}) == "blocked site"
    assert filter_reason({**j("Makati"), "source": "Google Jobs (Jobstreet)"}) is None

    page = ('<li><div class="base-card job-search-card" data-entity-urn="urn:li:jobPosting:123">'
            '<a class="base-card__full-link absolute" href="https://ph.linkedin.com/jobs/view/qa-123?trk=x">'
            '</a><h3 class="base-search-card__title">QA &amp; Test Engineer</h3>'
            '<h4 class="base-search-card__subtitle"><a href="#">Acme</a></h4>'
            '<span class="job-search-card__location">Makati, Philippines</span>'
            '<span class="job-search-card__salary-info">₱60K/month</span></div></li>'
            '<li><div data-entity-urn="urn:li:jobPosting:456"><h3 class="base-search-card__title">Tester</h3></div></li>')
    cards = parse_linkedin_cards(page)
    assert [c["id"] for c in cards] == ["123", "456"], cards
    assert cards[0] == {"id": "123", "title": "QA & Test Engineer", "company": "Acme",
                        "location": "Makati, Philippines", "salary": "₱60K/month",
                        "link": "https://ph.linkedin.com/jobs/view/qa-123"}, cards[0]
    assert cards[1]["link"] == "https://www.linkedin.com/jobs/view/456" and cards[1]["company"] == ""

    pasted = ("https://www.linkedin.com/jobs/search/?currentJobId=4472098367&f_E=2%2C3&f_TPR=r86400"
              "&geoId=103121230&keywords=QA%20Automation&origin=JOB_SEARCH_PAGE_JOB_FILTER&refresh=true")
    assert linkedin_params(pasted) == {"f_E": "2,3", "f_TPR": "r86400", "geoId": "103121230",
                                       "keywords": "QA Automation"}, linkedin_params(pasted)
    assert linkedin_params("https://www.jobstreet.com.ph/jobs?q=qa") is None
    print("selftest ok")


if __name__ == "__main__":
    selftest() if "--selftest" in sys.argv else main()
