# CV Tailor

Tailors your CV and a cover letter to a job description. It grades your fit, extracts the ATS keywords the posting scans for, rewrites your CV truthfully, runs a humanizer pass, and writes print-ready HTML (and optionally PDF). It never invents experience: it only uses what is in your base CV. Nothing is ever submitted. Every document is a draft for you to review.

Two ways to run it:

| | Path A: VS Code Copilot | Path B: DeepSeek API |
|---|---|---|
| Style | Guided chat, pauses for your input at each phase | Unattended batch, one job file in, documents out |
| Needs | VS Code with GitHub Copilot | A DeepSeek API key |
| Command | Pick the **cv-cl-agent** agent, type `start` | `python pipeline.py` |

(Claude Code users can also run `/cv-cl` from `.claude/skills/cv-cl`.)

## Setup (both paths)

Requires Python 3.10+.

```bash
git clone <this repo> && cd <this repo>
pip install -r requirements.txt
playwright install chromium
cp input/cv-base.example.txt input/cv-base.txt     # Windows: copy input\cv-base.example.txt input\cv-base.txt
```

Then edit `input/cv-base.txt` with your real CV. Keep the same layout as the example. This file is git-ignored, so your details stay local.

## Path A: VS Code Copilot

1. Open this folder in VS Code.
2. In Copilot Chat, open the agent picker and select **cv-cl-agent**.
3. Type `start`, then follow the phases: job description, role, optional company name.
4. Review the HTML in `output/`, then ask for the PDF when you are happy with it.

| Phase | Step |
|---|---|
| 0 | Gather the job description, role, and company |
| 1 | Job match grade (Strong, Partial, Weak). Stops and asks you if the fit is not strong |
| 2 | Extract ATS keywords and mark which ones you honestly match |
| 3 | Rewrite the CV truthfully for the role |
| 4 | Humanize the wording |
| 5 | Write HTML to `output/[Company]_[LastName]_Resume.html` and `_CoverLetter.html` |
| 6 | PDF, only when you ask |
| 7 | Summary of fit, changes, and keywords |

## Path B: DeepSeek API

1. Get a key from the DeepSeek platform and set it. Either copy `.env.example` to `.env` and fill it in, or export it:

   ```bash
   export DEEPSEEK_API_KEY="sk-..."          # macOS / Linux
   ```
   ```powershell
   setx DEEPSEEK_API_KEY "sk-..."            # Windows, then open a new terminal
   ```

2. Drop each job description into `jobs/inbox/` as a `.txt` file. Add a line `Company: Acme` at the top if the posting does not name the company.
3. Run:

   ```bash
   python pipeline.py
   ```

   Strong and Partial fits get a tailored resume and cover letter (HTML and PDF) in `output/`. Weak fits move to `jobs/skipped/`. Processed files move to `jobs/done/`. Failed jobs stay in the inbox and are retried next run.

Optional extras, all configured through environment variables or `.env`:
- `SERPAPI_API_KEY`: also search Google Jobs (LinkedIn, JobStreet and others). Each run uses searches from your SerpApi quota.
- `SHEETS_WEBHOOK_URL`: log every job to a Google Sheet. The Apps Script to paste is at the top of `pipeline.py`.
- `jobs/linkedin_urls.txt`: LinkedIn search URLs to scan for new jobs.

Check the install without a network or API key: `python pipeline.py --selftest`.

## PDF

```bash
python html_to_pdf.py             # one PDF per HTML file, in output/PDF/
python html_to_pdf.py --combine   # resume + cover letter merged per company
```

Each document should come out as exactly one A4 page. The script warns if one runs longer.

## Layout

```
.github/agents/cv-cl-agent.agent.md   Copilot agent (Path A)
.github/skills/cv-cl-optimizer/       grading, keywords, rewrite rules (shared by both paths)
.github/skills/humanizer/             final wording pass (shared by both paths)
.claude/skills/cv-cl/                 Claude Code skill (optional)
pipeline.py                           DeepSeek batch runner (Path B)
html_to_pdf.py                        HTML to PDF
template/                             resume and cover letter templates used by pipeline.py
cv-template.html                      resume template used by the Copilot agent
input/cv-base.txt                     your CV (you create this, git-ignored)
output/                               generated files (git-ignored)
```

The two skill files are the single source of truth for both paths. Edit them to change how every run behaves.

## License

MIT
