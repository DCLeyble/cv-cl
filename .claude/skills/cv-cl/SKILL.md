---
name: cv-cl
description: Guided CV and cover letter tailoring workflow. Use when the user wants to tailor their CV or resume to a job description, grade their fit for a role, make a resume ATS-friendly, or generate a matching cover letter. Runs a phased workflow that pauses for confirmation - it gathers the job description and role, grades the fit, extracts ATS keywords, rewrites the CV truthfully, humanizes the wording, and writes HTML output to output/ (PDFs only on explicit request).
---

# CV + Cover Letter Agent (Guided Workflow)

Orchestrate the phases below one at a time, pausing for the user's input and confirmation
before advancing. Never skip a phase and never jump ahead to the output.

The candidate's base CV is at `input/cv-base.txt`. Always read it before grading.

## Reference guides (read these, do not work from memory)

- **Optimizer** - `.github/skills/cv-cl-optimizer/SKILL.md`. How to grade fit, extract ATS
  keywords, rewrite the CV, and write the cover letter. Read it before Phase 1.
- **Humanizer** - `.github/skills/humanizer/SKILL.md`. How to make the final wording sound
  human. Read it before Phase 4.

These two files are shared with the VS Code Copilot agent at
`.github/agents/cv-cl-agent.agent.md`. They are the single source of truth. Do not copy them
into `.claude/`, and if you change one, the change applies to both agents.

Ignore the optimizer skill's reference to `/mnt/user-data/uploads`. That path does not exist
here. Inputs arrive by paste, by local file path, or by URL.

## The workflow (follow exactly, in order)

Announce which phase you are entering as you go, so the user can follow along.

### Phase 0 - Gather inputs

Ask the user for:
1. **Job description** - pasted text, a local file path to read, or a URL to fetch with WebFetch
2. **Role / position title**
3. Optional: **company name** (needed for file naming)
4. Optional: any highlights or extra context not already in the base CV

Do NOT proceed until you have the job description and the role. If either is missing, ask
again before continuing.

### Phase 1 - Job match grade

Read `input/cv-base.txt` and the optimizer guide, then grade the fit:
- **Strong** - core requirements clearly met
- **Partial** - meets many requirements but has real gaps
- **Weak** - missing multiple core requirements

Show brief reasoning: what is met, what is missing or thin, and whether gaps are bridgeable
by transferable experience. Be plain about weaknesses without being discouraging.

**Checkpoint:** if the fit is **Partial or Weak**, stop and use AskUserQuestion to ask
whether to continue anyway. Respect the answer either way. If the fit is Strong, say so in a
line or two and move straight on.

### Phase 2 - Extract ATS keywords

Following the optimizer guide, list the keywords, tools, frameworks, and phrases the posting
scans for, and mark which ones match real experience in the base CV. Show the list to the user.

### Phase 3 - Rewrite the CV

Using the optimizer guide, rewrite the CV content truthfully and specifically for this role:
- Never add skills, employers, titles, certifications, or achievements not in the base CV.
- Reorder and re-weight real experience to emphasize what matters here. Do not fabricate.
- Quantify only where the source CV supports it.
- Match the job's vocabulary only where it honestly describes the candidate.

### Phase 4 - Humanize

Read `.github/skills/humanizer/SKILL.md` and apply it to every bullet and paragraph of the
draft CV and cover letter. Cut AI filler, vary rhythm and sentence length, remove em dashes.
Keep every claim intact. This pass is mandatory before output.

### Phase 5 - Generate HTML output

1. **CV** - fill the placeholders in `cv-template.html` (project root; Tailwind layout) using its placeholders: {{name}}, {{location}}, {{phone}}, {{email}}, {{linkedin_url}}/{{linkedin}} (omit if no URL is known), {{#summary_bullets}}, {{job1_company}}/{{job1_title}}/{{job1_dates}}/{{#job1_bullets}}, the same for job2, {{skills}}, {{tools}}, {{degree}}, {{university_location}}, {{graduation_year}}, {{#certifications}}. It must render as exactly one A4 page when printed with the tailored content.
2. **Cover letter** - a separate standalone HTML file, targeted to the company and role,
   3-4 short paragraphs max.

Save both to `output/`, asking for the company name if it is still unknown:
- `output/[Company]_[LastName]_Resume.html`
- `output/[Company]_[LastName]_CoverLetter.html`

This phase produces HTML only. Stop here and let the user review before any PDF work.

### Phase 6 - Generate PDF

Run this only after the user explicitly asks (for example "generate PDF" or "go").

1. Confirm the user has reviewed the HTML in `output/`.
2. Run `python html_to_pdf.py`. By default this writes one PDF per HTML file, keeping the
   resume and cover letter separate:
   `output/PDF/[Company]_[LastName]_Resume.pdf` and `output/PDF/[Company]_[LastName]_CoverLetter.pdf`.
3. If the user prefers a single merged file, run `python html_to_pdf.py --combine` to produce
   `output/PDF/[Company]_[LastName]_Application.pdf` per company.
4. **One page per document.** The resume and the cover letter must each come out as exactly
   one A4 page. The script prints a WARNING for any PDF longer than one page. If that
   happens, tighten the HTML (trim or merge bullets, shorten wording, reduce spacing) and
   re-run until every PDF is one page. Do not shrink the font below 12px to force a fit.
   With `--combine`, each source document is still one page (a resume plus cover letter is 2 pages total).
5. Tell the user where the PDFs landed. The script can be re-run anytime after edits.

### Phase 7 - Summary

Present a short summary:
- **Fit assessment** (from Phase 1)
- **Key improvements made** - what changed and why
- **ATS keywords identified** (from Phase 2), and which ones made it into the final resume

Keep it tight. The documents are the deliverable; this is just the map of what changed.

## Hard rules

- Never invent credentials, employers, tools, certifications, or achievements that were not
  supplied. This rule overrides everything else here.
- Keep the language honest, specific, and recruiter-friendly.
- Prefer clean, ATS-friendly, print-ready HTML with semantic tags (real `<h1>`/`<h2>`, single
  column, no tables for layout, no content baked into images).
- The CV must stay aligned with the `cv-template.html` (project root) structure.
- Never use em dashes in the final documents. Use commas, periods, or parentheses.
- Do not skip phases or run ahead of the user's input.
