#!/usr/bin/env python3
"""Keep personal data out of git on a public fork.

Run from anywhere: python tools/personal_data_guard.py [--staged|--rev REF|--worktree]

Why this exists: CI's `placeholder-integrity` job is gated to the upstream
template (`if: github.repository == 'MadsLorentzen/ai-job-search'`), because
forks are *supposed* to personalize. That gating is correct for the template
and leaves a fork with no mechanical protection at all: `/setup` writes the
candidate's name, contact details, employment history and deal-breakers into
nine **tracked** files, and `/setup`'s own visibility warning is advisory
prose the model may skip. On a public fork - GitHub cannot make a fork of a
public repo private - one `git commit && git push` publishes all of it.

This guard runs at the git layer, on every fork, and blocks the push instead
of warning about it. Three checks:

1. Placeholder sentinels. Each protected file must retain specific
   placeholder tokens that sit IN the data `/setup` rewrites - never in a
   header comment or PDF metadata, which `/setup` leaves alone (upstream
   review finding F28: a comment-located sentinel let a fully personalized
   CV pass). Personalize the file and the sentinel is gone, so the guard
   fires.
2. Personal-output paths. Staged paths matching the personal-data patterns
   `.gitignore` covers - tracker, salary data, `documents/`, generated CVs
   and cover letters, reports, Gmail state. Catches `git add -f` and any
   future weakening of the ignore rules.
3. Contact details in added lines. Email addresses and international phone
   numbers, scanned in protected and newly added files only. Reserved
   example domains (example.com/org/net, *.invalid, *.test) are allowed, so
   the template's own fixtures pass.

Honest limits. Check 3 catches `+34 600 123 456`, not a domestic `600 123
456`: a digits-only rule would fire on version numbers and dates in every
doc, and a guard that cries wolf gets bypassed. Names, street addresses and
free-prose employment history are not detected at all. Checks 1 and 2 are
the load-bearing ones; check 3 is a net for pasted CV text, not a
classifier. This raises the cost of an accident - it is not a substitute for
moving a personalized copy to a private repository (see
tools/README_PRIVACY_GUARD.md).

Escape hatches, once the remote really is private:
  ALLOW_PERSONAL_PROFILE=1  allow a personalized profile (check 1 only), while
                            still refusing tracker/salary/documents files and
                            stray contact details. This is the end state after
                            moving to a private repo.
  ALLOW_PERSONAL_DATA=1     downgrade every failure to a warning.

Stdlib only. Exit 0 on success, 1 with a failure list otherwise.
"""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Placeholder tokens that must survive in each tracked file /setup rewrites.
# Every token here is data-located: it sits in the value /setup replaces, so
# personalizing the file necessarily destroys it. The first six mirror the
# upstream CI job; the rest extend the same idea to the files it skips.
PROTECTED_SENTINELS: dict[str, list[str]] = {
    "CLAUDE.md": [
        "**Name:** [YOUR_NAME]",
        "[YOUR_LINKEDIN_HEADLINE]",
        "[DEALBREAKER_1]",
    ],
    "cv/main_example.tex": [
        r"\name{[First]}{[Last]}",
        r"\email{[your.email@example.com]}",
    ],
    "cover_letters/cover_example.tex": [
        "[YOUR NAME]",
    ],
    ".claude/skills/job-application-assistant/01-candidate-profile.md": [
        "[YOUR_EMAIL]",
    ],
    ".claude/skills/job-application-assistant/02-behavioral-profile.md": [
        "[PROFILE_TYPE]",
    ],
    ".claude/skills/job-application-assistant/04-job-evaluation.md": [
        "[YOUR_PRIMARY_SKILLS]",
    ],
    ".claude/skills/job-application-assistant/05-cv-templates.md": [
        r"\email{[YOUR_EMAIL]}",
        r"\phone[mobile]{[YOUR_PHONE]}",
    ],
    ".claude/skills/job-application-assistant/07-interview-prep.md": [
        "**S:** [CONTEXT]",
        "**R:** [OUTCOME]",
    ],
    ".claude/skills/job-scraper/search-queries.md": [
        "[YOUR_CITY]",
    ],
}

# Paths that carry personal output and must never enter a commit. These
# mirror .gitignore's personal-data rules: the ignore file is the first line
# of defence, this is the one that still holds after a `git add -f` or an
# edit that weakens a pattern.
PERSONAL_PATHS = [
    ("salary_data.json", "salary benchmark data"),
    ("job_search_tracker.csv", "application tracker"),
    ("documents/**", "personal source documents (CV, LinkedIn, diplomas, references)"),
    ("cv/main_*.*", "generated CV"),
    ("cv/*.txt", "CV text extraction (full CV text)"),
    ("cover_letters/cover_*.*", "generated cover letter"),
    ("cover_letters/Cover_*.*", "generated cover letter"),
    ("reports/**", "generated application report"),
    ("gmail_sync/**", "Gmail sync state (message ids, subjects)"),
    ("company_research/*.json", "cached company research (personal search history)"),
    ("**/job_scraper/seen_jobs.json", "scraper state (job history)"),
    ("**/job_scraper/notion_sync.json", "Notion sync state"),
    ("**/job_scraper/*.md", "scraper output"),
    ("upskill/*.md", "upskill report"),
    ("**/upskill/report-*.md", "upskill report"),
    (".env", "secrets"),
    (".env.*", "secrets"),
    ("*_BehavioralReport.pdf", "behavioral report"),
    ("linkedin_Profile.pdf", "LinkedIn profile export"),
]

# Template files that live inside a personal path but are the tracked stock
# examples, plus the folder scaffolding that keeps empty dirs in git.
PERSONAL_PATH_EXCEPTIONS = [
    "cv/main_example.tex",
    "cover_letters/cover_example.tex",
    "documents/README.md",
    "documents/**/.gitkeep",
]

# Domains reserved for documentation (RFC 2606 / RFC 6761). A contact detail
# on one of these is a fixture, not a person.
EXAMPLE_EMAIL_DOMAINS = re.compile(
    r"@(?:[\w.-]+\.)?(?:example\.(?:com|org|net)|invalid|test|localhost)$", re.IGNORECASE
)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# The guard's own source, tests and docs quote realistic contact details on
# purpose - they are the fixtures that prove the scan fires. Exempting them is
# narrower than loosening the patterns, which would blind the scan everywhere.
CONTACT_SCAN_EXEMPT = frozenset({
    "tools/personal_data_guard.py",
    "tools/README_PRIVACY_GUARD.md",
    "tests/test_personal_data_guard.py",
})
# International format only - a leading + is what separates a phone number
# from a version string, a date, or an ISBN. See the docstring's limits.
PHONE_RE = re.compile(r"(?<![\w+])\+\d{1,3}[\s.\-()]{0,2}\d(?:[\s.\-()]{0,2}\d){6,13}(?![\w])")


def git(*args: str, check: bool = True) -> str:
    """Run a git command in the repo and return stdout."""
    result = subprocess.run(
        ["git", "-C", str(ROOT), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def glob_to_regex(pattern: str) -> re.Pattern:
    """Translate a gitignore-style glob to a full-path regex.

    `**/` matches any number of leading directories, `*` stops at a slash.
    """
    out = ""
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out += r"(?:.*/)?"
            i += 3
        elif pattern.startswith("**", i):
            out += r".*"
            i += 2
        elif pattern[i] == "*":
            out += r"[^/]*"
            i += 1
        elif pattern[i] == "?":
            out += r"[^/]"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.compile(rf"^{out}$")


PERSONAL_PATH_RES = [(glob_to_regex(p), p, why) for p, why in PERSONAL_PATHS]
EXCEPTION_RES = [glob_to_regex(p) for p in PERSONAL_PATH_EXCEPTIONS]


def is_personal_path(path: str) -> tuple[str, str] | None:
    """Return (pattern, reason) if the path carries personal output."""
    if any(rx.match(path) for rx in EXCEPTION_RES):
        return None
    for rx, pattern, why in PERSONAL_PATH_RES:
        if rx.match(path):
            return pattern, why
    return None


def scan_contact_details(path: str, lines: list[str]) -> list[str]:
    """Report email addresses and international phone numbers in `lines`."""
    if path in CONTACT_SCAN_EXEMPT:
        return []
    found = []
    for line in lines:
        for email in EMAIL_RE.findall(line):
            if not EXAMPLE_EMAIL_DOMAINS.search(email):
                found.append(f"{path}: email address `{email}`")
        for phone in PHONE_RE.findall(line):
            found.append(f"{path}: phone number `{phone.strip()}`")
    return found


def staged_paths() -> list[str]:
    out = git("diff", "--cached", "--name-only", "--diff-filter=ACMR")
    return [p for p in out.splitlines() if p]


def staged_added_lines() -> dict[str, list[str]]:
    """Map each staged path to the lines the commit adds to it."""
    diff = git("diff", "--cached", "--unified=0", "--diff-filter=ACMR")
    added: dict[str, list[str]] = {}
    current = None
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
        elif line.startswith("+++ "):
            current = None
        elif current and line.startswith("+") and not line.startswith("+++"):
            added.setdefault(current, []).append(line[1:])
    return added


def blob(rev: str, path: str) -> str | None:
    """File content at a revision (or in the index when rev is empty)."""
    result = subprocess.run(
        ["git", "-C", str(ROOT), "show", f"{rev}:{path}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout if result.returncode == 0 else None


def profile_allowed() -> bool:
    """True when a personalized profile is expected - i.e. a private remote."""
    return os.environ.get("ALLOW_PERSONAL_PROFILE") == "1"


def check_sentinels(path: str, content: str) -> list[str]:
    if profile_allowed():
        return []
    missing = [s for s in PROTECTED_SENTINELS[path] if s not in content]
    if not missing:
        return []
    tokens = ", ".join(f"`{s}`" for s in missing)
    return [
        f"{path}: placeholder token(s) gone ({tokens}) - this file looks personalized. "
        "Personal data in a tracked file becomes public the moment you push."
    ]


def check_staged() -> list[str]:
    """Guard a commit: sentinels, personal paths, and contact details."""
    errors: list[str] = []
    paths = staged_paths()
    added = staged_added_lines()

    for path in paths:
        hit = is_personal_path(path)
        if hit:
            pattern, why = hit
            errors.append(
                f"{path}: {why} - matches the personal-data rule `{pattern}`. "
                "This file is meant to stay out of git entirely."
            )
        if path in PROTECTED_SENTINELS:
            content = blob("", path)
            if content is None:
                errors.append(f"{path}: protected template file is being removed.")
            else:
                errors.extend(check_sentinels(path, content))

    # New files can hold a pasted CV; protected files can be edited by hand.
    added_files = set(git("diff", "--cached", "--name-only", "--diff-filter=A").split())
    for path, lines in added.items():
        if path in PROTECTED_SENTINELS or path in added_files:
            errors.extend(scan_contact_details(path, lines))
    return errors


def check_rev(rev: str) -> list[str]:
    """Guard a push: everything the pushed tree would publish."""
    errors: list[str] = []
    tracked = git("ls-tree", "-r", "--name-only", rev).splitlines()

    for path in tracked:
        hit = is_personal_path(path)
        if hit:
            pattern, why = hit
            errors.append(f"{path}: {why} - matches `{pattern}` and is committed at {rev}.")

    for path in PROTECTED_SENTINELS:
        content = blob(rev, path)
        if content is None:
            continue
        errors.extend(check_sentinels(path, content))
        errors.extend(scan_contact_details(path, content.splitlines()))
    return errors


def check_worktree() -> list[str]:
    """Audit the files on disk, committed or not."""
    errors: list[str] = []
    for path in PROTECTED_SENTINELS:
        target = ROOT / path
        if not target.exists():
            errors.append(f"{path}: protected template file is missing from the worktree.")
            continue
        content = target.read_text(encoding="utf-8", errors="replace")
        errors.extend(check_sentinels(path, content))
        errors.extend(scan_contact_details(path, content.splitlines()))
    return errors


def install_hook() -> int:
    hooks = ROOT / ".githooks"
    if not (hooks / "pre-commit").exists():
        print(f"personal_data_guard: {hooks}/pre-commit is missing", file=sys.stderr)
        return 1
    for hook in hooks.iterdir():
        hook.chmod(hook.stat().st_mode | 0o111)
    git("config", "core.hooksPath", ".githooks")
    print("personal_data_guard: hooks installed (core.hooksPath = .githooks)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--staged", action="store_true", help="check the staged commit (default)")
    mode.add_argument("--rev", metavar="REF", help="check everything a revision would publish")
    mode.add_argument("--worktree", action="store_true", help="audit the files on disk")
    mode.add_argument(
        "--install-hook", action="store_true", help="point core.hooksPath at .githooks"
    )
    args = parser.parse_args(argv)

    if args.install_hook:
        return install_hook()
    if args.rev:
        errors, what = check_rev(args.rev), f"revision {args.rev}"
    elif args.worktree:
        errors, what = check_worktree(), "worktree"
    else:
        errors, what = check_staged(), "staged changes"

    if not errors:
        print(f"personal_data_guard: OK ({what}: no personal data found)")
        return 0

    override = os.environ.get("ALLOW_PERSONAL_DATA") == "1"
    label = "warning" if override else "failure"
    print(f"personal_data_guard: {len(errors)} {label}(s) in {what}")
    for err in errors:
        print(f"  - {err}")
    if override:
        print("\nALLOW_PERSONAL_DATA=1 is set - allowing. Make sure this remote is private.")
        return 0
    print(
        "\nThis repository is a fork of a public template, so anything pushed here is\n"
        "public. If you meant to keep a personalized copy, move it to a PRIVATE\n"
        "repository first - tools/README_PRIVACY_GUARD.md has the recipe. On a private\n"
        "remote, set ALLOW_PERSONAL_PROFILE=1 to allow your profile while still\n"
        "blocking tracker, salary and documents files."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
