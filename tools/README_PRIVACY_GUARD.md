# Personal-data guard

`tools/personal_data_guard.py` stops your job-search profile from reaching a
public remote. It runs as a git hook, so it blocks the commit and the push
rather than warning after the fact.

## Why a fork needs this

The upstream template already has a `placeholder-integrity` CI job, but it is
gated to the template repo itself:

```yaml
placeholder-integrity:
  if: github.repository == 'MadsLorentzen/ai-job-search'
```

That gating is correct — a fork is *supposed* to personalize these files — and
it means a fork runs with **no mechanical protection at all**. `/setup` writes
your name, contact details, employment history, salary expectations and
deal-breakers into nine **tracked** files:

| File | What `/setup` puts there |
|------|--------------------------|
| `CLAUDE.md` | Name, city, languages, employment status, education, experience, deal-breakers |
| `cv/main_example.tex` | Name, address, phone, email |
| `cover_letters/cover_example.tex` | Name and contact block |
| `.claude/skills/job-application-assistant/01-candidate-profile.md` | Full structured profile |
| `.claude/skills/job-application-assistant/02-behavioral-profile.md` | Behavioral assessment |
| `.claude/skills/job-application-assistant/04-job-evaluation.md` | Skill gaps, financial situation, career goals |
| `.claude/skills/job-application-assistant/05-cv-templates.md` | Contact block, profile statements |
| `.claude/skills/job-application-assistant/07-interview-prep.md` | STAR stories from real projects |
| `.claude/skills/job-scraper/search-queries.md` | Home city, target roles, commute limits |

`/setup` does print a warning when `origin` is a public fork, but it is
advisory prose — and **GitHub cannot make a fork of a public repository
private**, so on a fork one `git commit && git push` publishes all of it.

## Install

Per clone, on every machine you use (`core.hooksPath` is local git config and
is not carried by a push):

```bash
python3 tools/personal_data_guard.py --install-hook
```

Verify with `git config core.hooksPath` — it should print `.githooks`.

## What it checks

1. **Placeholder sentinels.** Each protected file must keep specific
   placeholder tokens. Every token sits *in the data* `/setup` rewrites, never
   in a header comment or PDF metadata — upstream review finding F28 showed a
   comment-located sentinel let a fully personalized CV pass the check.
   Personalize the file and the sentinel is gone, so the guard fires.
2. **Personal-output paths.** Staged paths matching the personal-data rules
   `.gitignore` covers: tracker, salary data, `documents/`, generated CVs and
   cover letters, reports, Gmail sync state, scraper state. Catches
   `git add -f` and any future weakening of the ignore rules.
3. **Contact details in added lines.** Email addresses and international phone
   numbers, in protected files and newly added files. Reserved documentation
   domains (`example.com/org/net`, `.invalid`, `.test`) are allowed, so the
   template's own fixtures pass.

Both hooks are wired: `pre-commit` checks the staged change, `pre-push` checks
everything the pushed tree would publish — so a commit made with `--no-verify`
still cannot reach the remote unnoticed.

## Honest limits

- Check 3 catches `+34 600 123 456`, **not** a domestic `600 123 456`. A
  digits-only rule fires on version numbers and dates in every doc, and a
  guard that cries wolf gets bypassed.
- Names, street addresses and free-prose employment history are **not**
  detected. Checks 1 and 2 are the load-bearing ones; check 3 is a net for
  pasted CV text, not a classifier.
- Hooks are local config. A fresh clone has no protection until you run
  `--install-hook` again.
- This raises the cost of an accident. It is **not** a substitute for a
  private repository.

## Manual use

```bash
python3 tools/personal_data_guard.py --worktree       # audit files on disk
python3 tools/personal_data_guard.py --staged         # audit the staged commit
python3 tools/personal_data_guard.py --rev HEAD       # audit what a ref publishes
python3 tools/personal_data_guard.py --rev origin/master
```

## The real fix: a private repository

The guard buys you safety on a public fork; it does not make the fork private.
To keep a personalized copy, move to a private repo and keep the template as
`upstream` (this is SETUP.md section 8's recipe):

1. Create a **new empty private repository** on GitHub — do *not* use the
   "Fork" button, a fork of a public repo cannot be private. Say
   `garridojuan/job-search-private`.
2. Repoint the remotes:
   ```bash
   git remote rename origin public-fork
   git remote add origin https://github.com/<you>/job-search-private.git
   git remote add upstream https://github.com/MadsLorentzen/ai-job-search.git
   git push -u origin master
   ```
3. Confirm the new remote really is private (GitHub shows a `Private` badge
   next to the repo name), then delete the public fork if you no longer need
   it: **Settings → General → Danger Zone → Delete this repository**.
4. Personalize there, and allow your profile through the guard:
   ```bash
   ALLOW_PERSONAL_PROFILE=1 git commit -m "setup: my profile"
   ```
   Check 1 stands down; tracker, salary data, `documents/` and stray contact
   details stay blocked, which is still what you want in a private repo.

To persist that on the private clone, set it once:

```bash
git config --local alias.pcommit '!ALLOW_PERSONAL_PROFILE=1 git commit'
```

`ALLOW_PERSONAL_DATA=1` downgrades *every* check to a warning. Reach for it
only when you know exactly what you are letting through.

## If personal data was already pushed

Deleting the file in a new commit is not enough — the old commit stays
reachable, and on a public repo it may already be forked, cached or indexed.
Treat the data as disclosed: rotate anything credential-like, then rewrite
history (`git filter-repo`) or delete the repository outright. GitHub's
guidance on [removing sensitive data](https://docs.github.com/en/authentication/keeping-your-account-secure/removing-sensitive-data-from-a-repository)
covers the full procedure, including asking GitHub Support to purge cached
views.
