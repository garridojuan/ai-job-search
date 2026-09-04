"""Guards for the fork-side personal-data guard.

CI's placeholder-integrity job is gated to the upstream template
(`if: github.repository == 'MadsLorentzen/ai-job-search'`), so a fork - which
is exactly where /setup writes real personal data - runs with no mechanical
protection. tools/personal_data_guard.py fills that gap at the git layer.

These tests pin the two properties that make it worth having: it stays quiet
on the pristine template (a guard that cries wolf gets bypassed), and it
actually fires on the failure it exists to catch. The sentinel tests follow
upstream review finding F28: a placeholder token only guards anything if it
sits IN the data /setup rewrites, so each one is asserted to exist in the
pristine file AND to be destroyed by a simulated personalization.
"""
import re
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GUARD_SCRIPT = REPO_ROOT / "tools" / "personal_data_guard.py"
HOOKS = REPO_ROOT / ".githooks"

sys.path.insert(0, str(REPO_ROOT / "tools"))
import personal_data_guard as guard  # noqa: E402  (imported for its rule tables)

# Any bracketed token - `[YOUR_NAME]`, `[First]`, `[your.email@example.com]`.
PLACEHOLDER = re.compile(r"\[[^\]\n]{1,60}\]")

# A personalized fork has, by design, destroyed these placeholders - that is what
# /setup does. The pristine-template assertions below only mean something on the
# upstream template, exactly like ci.yml's placeholder-integrity job, which is
# gated with `if: github.repository == 'MadsLorentzen/ai-job-search'`. Without the
# same gate here, `python -m unittest` fails on every fork that has been set up.
PERSONALIZED = "[YOUR_NAME]" not in (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
PRISTINE_ONLY = unittest.skipIf(PERSONALIZED, "profile is personalized (/setup has run)")



def personalize(text: str) -> str:
    """Stand in for /setup: every placeholder becomes real data."""
    return PLACEHOLDER.sub("Juan Garrido", text)


class TestPristineTemplateIsQuiet(unittest.TestCase):
    @PRISTINE_ONLY
    def test_worktree_audit_passes_on_the_shipped_files(self):
        result = subprocess.run(
            [sys.executable, str(GUARD_SCRIPT), "--worktree"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"the guard must pass on the un-personalized template:\n{result.stdout}",
        )

    def test_stock_example_documents_are_not_personal_paths(self):
        for path in ("cv/main_example.tex", "cover_letters/cover_example.tex",
                     "documents/README.md", "documents/cv/.gitkeep"):
            self.assertIsNone(
                guard.is_personal_path(path),
                f"{path} is tracked template content, not personal output",
            )

    def test_reserved_example_domains_are_allowed(self):
        for line in ("\\email{[your.email@example.com]}", "jane.doe@example.org", "t@example.net"):
            self.assertEqual(guard.scan_contact_details("f", [line]), [])


class TestSentinelsAreDataLocated(unittest.TestCase):
    """Each sentinel must live in the data /setup rewrites - see F28."""

    # Pristine-template assertions: a personalized fork has legitimately
    # destroyed the sentinels, which is the guard firing, not a rule-table bug.

    @PRISTINE_ONLY
    def test_every_protected_file_exists_and_carries_its_sentinels(self):
        for path, sentinels in guard.PROTECTED_SENTINELS.items():
            target = REPO_ROOT / path
            self.assertTrue(target.exists(), f"{path} is missing - the rule table is stale")
            content = target.read_text(encoding="utf-8")
            for sentinel in sentinels:
                self.assertIn(sentinel, content, f"{path} no longer contains `{sentinel}`")

    @PRISTINE_ONLY
    def test_personalizing_a_file_destroys_all_of_its_sentinels(self):
        for path, sentinels in guard.PROTECTED_SENTINELS.items():
            content = (REPO_ROOT / path).read_text(encoding="utf-8")
            personalized = personalize(content)
            self.assertNotEqual(personalized, content, f"{path}: nothing to personalize")
            surviving = [s for s in sentinels if s in personalized]
            self.assertEqual(
                surviving,
                [],
                f"{path}: a sentinel survived personalization, so the guard would pass "
                f"on committed personal data: {surviving}",
            )

    def test_check_sentinels_reports_the_missing_token(self):
        errors = guard.check_sentinels("CLAUDE.md", "no placeholders here")
        self.assertEqual(len(errors), 1)
        self.assertIn("[YOUR_NAME]", errors[0])


class TestPersonalOutputPaths(unittest.TestCase):
    def test_generated_and_sourced_personal_files_are_caught(self):
        for path in (
            "salary_data.json",
            "job_search_tracker.csv",
            "documents/cv/my_cv.pdf",
            "documents/interview/notes.md",
            "cv/main_acme_engineer.tex",
            "cv/main_acme_engineer.pdf",
            "cv/acme_ats.txt",
            "cover_letters/cover_acme_engineer.tex",
            "reports/index.html",
            "gmail_sync/state.json",
            "company_research/acme.json",
            ".claude/skills/job-scraper/job_scraper/seen_jobs.json",
            "upskill/report-2026-09.md",
            ".env",
            ".env.local",
            "linkedin_Profile.pdf",
        ):
            self.assertIsNotNone(guard.is_personal_path(path), f"{path} must be blocked")

    def test_framework_files_are_not_flagged(self):
        for path in ("tools/personal_data_guard.py", "README.md", "CLAUDE.md",
                     "tests/test_personal_data_guard.py", ".claude/settings.json"):
            self.assertIsNone(guard.is_personal_path(path), f"{path} must not be blocked")

    def test_double_star_matches_at_any_depth(self):
        rx = guard.glob_to_regex("**/job_scraper/seen_jobs.json")
        self.assertTrue(rx.match("job_scraper/seen_jobs.json"))
        self.assertTrue(rx.match(".claude/skills/job-scraper/job_scraper/seen_jobs.json"))
        self.assertFalse(guard.glob_to_regex("cv/main_*.*").match("cv/sub/main_a.tex"))


class TestContactDetailScan(unittest.TestCase):
    def test_real_email_and_international_phone_are_reported(self):
        found = guard.scan_contact_details("cv.md", ["me@gmail.com", "Tel: +34 600 123 456"])
        self.assertEqual(len(found), 2, found)
        self.assertIn("email address", found[0])
        self.assertIn("phone number", found[1])

    def test_the_guards_own_fixtures_are_exempt(self):
        """The guard quotes realistic contact details to prove the scan works."""
        for path in guard.CONTACT_SCAN_EXEMPT:
            self.assertTrue((REPO_ROOT / path).exists(), f"{path} is stale in the exempt set")
            self.assertEqual(guard.scan_contact_details(path, ["me@gmail.com", "+34 600 123 456"]), [])

    def test_the_exemption_does_not_leak_to_other_files(self):
        for path in ("CLAUDE.md", "tools/salary_lookup.py", "documents/cv/x.md"):
            self.assertNotEqual(guard.scan_contact_details(path, ["me@gmail.com"]), [])

    def test_version_strings_and_dates_are_not_phone_numbers(self):
        for line in ("v1.6.0", "2026-08-19", "framework_version: 1.2.3", "commit d1504d2"):
            self.assertEqual(guard.scan_contact_details("f", [line]), [], line)


class TestHooksAreWired(unittest.TestCase):
    def test_hooks_exist_and_invoke_the_guard(self):
        for name, flag in (("pre-commit", "--staged"), ("pre-push", "--rev")):
            hook = HOOKS / name
            self.assertTrue(hook.exists(), f".githooks/{name} is missing")
            body = hook.read_text(encoding="utf-8")
            self.assertIn("personal_data_guard.py", body)
            self.assertIn(flag, body)

    def test_hooks_are_executable(self):
        for name in ("pre-commit", "pre-push"):
            self.assertTrue(
                (HOOKS / name).stat().st_mode & 0o111,
                f".githooks/{name} must be executable or git silently skips it",
            )


if __name__ == "__main__":
    unittest.main()
