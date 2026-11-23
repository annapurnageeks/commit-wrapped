"""Tests for commit-wrapped. Run with:  python3 -m unittest discover tests"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import wrapped  # noqa: E402


def make_repo(folder, commits, user=("Ada", "ada@example.com")):
    """Create a git repo. `commits` is a list of (ISO time with offset, author, message, {path: text})."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    run = lambda *args, env=None: subprocess.run(["git", "-C", str(folder), *args], check=True,
                                                 capture_output=True, env=env)
    run("init", "-q")
    run("config", "user.name", user[0])
    run("config", "user.email", user[1])
    for when, author, message, files in commits:
        for path, text in files.items():
            target = folder / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        env = dict(os.environ, GIT_AUTHOR_NAME=author, GIT_AUTHOR_EMAIL=f"{author.lower()}@example.com",
                   GIT_COMMITTER_NAME=author, GIT_COMMITTER_EMAIL=f"{author.lower()}@example.com",
                   GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)
        run("add", "-A")
        run("commit", "-q", "--allow-empty", "-m", message, env=env)
    return folder


def lines(n, word="line"):
    return "".join(f"{word} {i}\n" for i in range(n))


class WrappedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def facts_for(self, repo, year=2025, everyone=False, private=False, identities=("ada",)):
        commits = [c for c in wrapped.read_commits(repo, year) if everyone or wrapped.is_mine(c, set(identities))]
        return wrapped.build_facts(commits, year, "Ada", everyone=everyone, private=private,
                                   today=date(year, 12, 31))

    def test_finds_nested_repos_but_not_inside_node_modules(self):
        make_repo(self.root / "code" / "app", [("2025-01-01T10:00:00+00:00", "Ada", "init", {"a.py": "x\n"})])
        make_repo(self.root / "code" / "node_modules" / "dep", [("2025-01-01T10:00:00+00:00", "Ada", "x", {"b.js": "x\n"})])
        found = wrapped.find_repos([self.root])
        self.assertEqual([p.name for p in found], ["app"])

    def test_counts_only_this_year_and_only_my_commits(self):
        repo = make_repo(self.root / "r", [
            ("2024-12-31T23:00:00+00:00", "Ada", "last year", {"a.py": lines(5)}),
            ("2025-03-01T10:00:00+00:00", "Ada", "add parser", {"a.py": lines(10)}),
            ("2025-03-02T10:00:00+00:00", "Grace", "not mine", {"b.py": lines(50)}),
            ("2025-03-03T10:00:00+00:00", "Ada", "grow parser", {"a.py": lines(20)}),
        ])
        f = self.facts_for(repo)
        self.assertEqual(f["commits"], 2)
        self.assertEqual(f["added"], 15)          # 5 new lines, then 10 more
        self.assertEqual(f["active_days"], 2)
        self.assertEqual(f["everyone"], False)
        everyone = self.facts_for(repo, everyone=True)
        self.assertEqual(everyone["commits"], 3)
        self.assertEqual(everyone["people"], 2)

    def test_uses_the_authors_own_clock(self):
        # 23:30 in Kathmandu is 17:45 UTC: the story should say 11 PM, not 5 PM.
        repo = make_repo(self.root / "r", [("2025-05-05T23:30:00+05:45", "Ada", "late fix", {"a.py": "x\n"})])
        f = self.facts_for(repo)
        self.assertEqual(f["peak_hour"], 23)
        self.assertEqual(f["peak_hour_label"], "11 PM")
        self.assertEqual(f["peak_weekday"], "Monday")

    def test_reads_utc_times_written_as_z(self):
        repo = make_repo(self.root / "r", [("2025-05-05T10:00:00Z", "Ada", "utc", {"a.py": "x\n"})])
        self.assertEqual(self.facts_for(repo)["peak_hour"], 10)

    def test_streaks_and_breaks(self):
        days = ["01", "02", "03", "10", "11"]
        repo = make_repo(self.root / "r", [(f"2025-06-{d}T12:00:00+00:00", "Ada", f"day {d}", {f"{d}.py": "x\n"})
                                           for d in days])
        f = self.facts_for(repo)
        self.assertEqual(f["streak"], {"days": 3, "start": "2025-06-01", "end": "2025-06-03"})
        self.assertEqual(f["longest_break"]["days"], 6)

    def test_languages_ignore_lockfiles_and_skip_docs_as_top_language(self):
        repo = make_repo(self.root / "r", [
            ("2025-02-01T12:00:00+00:00", "Ada", "docs", {"README.md": lines(100)}),
            ("2025-02-02T12:00:00+00:00", "Ada", "code", {"app.py": lines(20)}),
            ("2025-02-03T12:00:00+00:00", "Ada", "deps", {"package-lock.json": lines(5000)}),
        ])
        f = self.facts_for(repo)
        self.assertEqual(f["languages"][0]["name"], "Docs")
        self.assertEqual(f["top_language"], "Python")
        self.assertNotIn("Config", [lang["name"] for lang in f["languages"]])
        self.assertEqual(f["added"], 120)

    def test_night_owl_personality(self):
        commits = [(f"2025-04-{d:02d}T02:00:00+00:00", "Ada", f"fix thing {d}", {f"f{d}.py": "x\n"}) for d in range(1, 13)]
        repo = make_repo(self.root / "r", commits)
        f = self.facts_for(repo)
        self.assertEqual(f["personality"]["key"], "night_owl")
        self.assertIn("100%", f["personality"]["reason"])

    def test_too_few_commits_get_the_default_personality(self):
        repo = make_repo(self.root / "r", [("2025-04-01T02:00:00+00:00", "Ada", "x", {"a.py": "x\n"})])
        self.assertEqual(self.facts_for(repo)["personality"]["key"], "builder")

    def test_private_mode_hides_names_paths_and_messages(self):
        repo = make_repo(self.root / "secret-project", [
            ("2025-01-05T12:00:00+00:00", "Ada", "add the launch codes", {"src/codes.py": lines(30)}),
        ])
        f = self.facts_for(repo, private=True)
        text = json.dumps(f)
        for secret in ("secret-project", "codes.py", "launch codes"):
            self.assertNotIn(secret, text)

    def test_render_cannot_break_out_of_the_script_tag(self):
        repo = make_repo(self.root / "r", [
            ("2025-01-05T12:00:00+00:00", "Ada", "</script><script>alert(1)</script>", {"a.py": "x\n"}),
        ])
        out = self.root / "wrapped.html"
        wrapped.render(self.facts_for(repo), out)
        html = out.read_text(encoding="utf-8")
        self.assertNotIn("</script><script>alert(1)", html)
        self.assertNotIn("/*__FACTS__*/", html)

    def test_script_replaces_captions_and_rejects_unknown_ones(self):
        repo = make_repo(self.root / "r", [("2025-01-05T12:00:00+00:00", "Ada", "x", {"a.py": "x\n"})])
        f = self.facts_for(repo)
        script = self.root / "captions.json"
        script.write_text(json.dumps({"captions": {"intro": "Buckle up."}}), encoding="utf-8")
        wrapped.apply_script(f, script)
        self.assertEqual(f["captions"]["intro"], "Buckle up.")
        script.write_text(json.dumps({"captions": {"nope": "x"}}), encoding="utf-8")
        with self.assertRaises(SystemExit):
            wrapped.apply_script(f, script)

    def test_command_line_end_to_end(self):
        make_repo(self.root / "code" / "app", [
            ("2025-01-05T12:00:00+00:00", "Ada", "init", {"a.py": lines(3)}),
        ])
        out = self.root / "out.html"
        run = lambda *a: subprocess.run([sys.executable, str(ROOT / "wrapped.py"), *a],
                                        capture_output=True, text=True, cwd=self.root)
        result = run(str(self.root / "code"), "--year", "2025", "-o", str(out))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"commits": 1', out.read_text(encoding="utf-8"))
        missing = run(str(self.root / "code"), "--year", "2025", "--author", "nobody@example.com")
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("no commits", missing.stderr)

    def test_output_survives_a_non_utf8_console(self):
        # Windows terminals and pipes may use an old code page such as cp1252
        make_repo(self.root / "r", [("2025-01-05T12:00:00+00:00", "Ada", "add 🚀 launch", {"a.py": "x\n"})])
        env = dict(os.environ, PYTHONIOENCODING="cp1252")
        result = subprocess.run([sys.executable, str(ROOT / "wrapped.py"), str(self.root / "r"), "--year", "2025",
                                 "--author", "ada", "--brief"], capture_output=True, text=True, encoding="utf-8", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
