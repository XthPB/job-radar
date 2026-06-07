# 📡 Job Radar

Tracks SWE & quant internships / new-grad / full-time roles (and events) across
companies you care about, and surfaces **new postings within ~30 minutes of
release** so you can be among the first to apply.

- **Poller** (`poll.py`) hits each company's public ATS feed (Greenhouse, Lever,
  Ashby, SmartRecruiters, Workday), diffs against the last run, and records the
  exact time each posting was first detected.
- **Dashboard** (`docs/index.html`) shows everything sorted newest-first with
  `NEW` badges, search, category + tag filters.
- **GitHub Actions** runs the poller every 30 min in the cloud (even when your
  laptop is off), commits the refreshed data, and **emails you a digest** of new
  postings.

Dependency-free — pure Python standard library.

---

## Add a company (the main thing you'll do)

Companies live in [`companies.json`](companies.json). Each entry needs the ATS
type and the company's board token:

```json
{ "name": "Stripe", "ats": "greenhouse", "token": "stripe", "tags": ["swe"] }
```

**Find the token** from the company's careers-page URL:

| ATS | Careers URL looks like | token is |
|-----|------------------------|----------|
| `greenhouse` | `boards.greenhouse.io/stripe` or `job-boards.greenhouse.io/stripe` | `stripe` |
| `lever` | `jobs.lever.co/plaid` | `plaid` |
| `ashby` | `jobs.ashbyhq.com/openai` | `openai` |
| `smartrecruiters` | `jobs.smartrecruiters.com/Company` | `Company` |
| `workday` | `company.wd5.myworkdayjobs.com/External` | see below |

**Verify before committing** — this prints the live count + sample titles:

```bash
python3 poll.py --check greenhouse stripe
python3 poll.py --check ashby openai
python3 poll.py --check workday company.wd5.myworkdayjobs.com company External
```

If it returns postings, add it to `companies.json`. If it 404s, the token/ATS
is wrong — open the careers page and check the real URL.

**Workday** entries use host/tenant/site instead of token:
```json
{ "name": "Acme", "ats": "workday",
  "host": "acme.wd5.myworkdayjobs.com", "tenant": "acme", "site": "External",
  "tags": ["swe"] }
```

**Companies without a public ATS feed** (e.g. Jane Street, Citadel) — add them
as a `link` so they still show on the dashboard as a quick-access card:
```json
{ "name": "Jane Street", "ats": "link",
  "url": "https://www.janestreet.com/join-jane-street/open-roles/", "tags": ["quant"] }
```

`tags` are free-form (e.g. `swe`, `quant`, `ai`) and become filter chips on the
dashboard. Set `"enabled": false` to mute a company without deleting it.

---

## Control which roles get tracked

[`settings.json`](settings.json) holds `role_keywords` — only titles matching one
are kept (so you don't get pinged about sales/marketing roles). Edit the list to
taste, or set it to `[]` to track every role. `exclude_keywords` drops matches.

---

## Run it locally

```bash
python3 poll.py            # poll everything, update docs/postings.json
python3 poll.py --list     # list configured companies
open docs/index.html       # view the dashboard
```

State lives in `state/seen.json` (full history incl. closed roles);
`docs/postings.json` is what the dashboard reads (active roles only).

---

## Cloud setup (GitHub Actions + Pages + email)

1. Push this folder to a GitHub repo (see below).
2. **Pages:** repo *Settings → Pages → Build from a branch → `main` / `/docs`*.
   Your dashboard will be at `https://<user>.github.io/<repo>/`.
3. **Email (optional):** repo *Settings → Secrets and variables → Actions* →
   add secrets:
   - `SMTP_HOST` (e.g. `smtp.gmail.com`), `SMTP_PORT` (`465`),
     `SMTP_USER`, `SMTP_PASS`, `MAIL_TO`
   - For Gmail, `SMTP_PASS` must be an [App Password](https://myaccount.google.com/apppasswords),
     not your normal password.
   - Optionally add a **variable** `SITE_URL` = your Pages URL (shown in emails).
4. The workflow runs every 30 min automatically; trigger it manually anytime
   from the **Actions** tab → *Job Radar poll* → *Run workflow*.

> Note: GitHub disables scheduled workflows after 60 days of no repo activity —
> the periodic commits from this workflow keep it alive. Scheduled runs can also
> be delayed a few minutes under platform load.
