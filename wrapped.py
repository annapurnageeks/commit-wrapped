#!/usr/bin/env python3
"""
commit-wrapped: your year in code, as a story you can share. Like Spotify Wrapped, but for git.

Usage:
    python3 wrapped.py ~/code                    # finds every repo in ~/code, writes wrapped.html
    python3 wrapped.py ~/code --open             # ...and opens it in your browser
    python3 wrapped.py . --everyone              # a project's year: every contributor, not just you
    python3 wrapped.py ~/code --private          # hide repo and file names (for private work)
    python3 wrapped.py ~/code --brief            # print the facts as JSON, for an AI to write captions
    python3 wrapped.py ~/code --script captions.json   # use your own captions

How it works, in three steps:
    1. find_repos()     looks for git repositories in the folders you give it
    2. read_commits()   asks git for your commits this year and how many lines each one changed
    3. build_facts()    turns those commits into stats, and render() puts them into player.html

Everything runs on your machine. Only the Python standard library is used: nothing to install,
and nothing is uploaded anywhere.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import webbrowser
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT_URL = "https://github.com/annapurnageeks/commit-wrapped"

# Generated or vendored files. Counting them would make your stats lie.
NOISE_FILES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb", "Cargo.lock", "poetry.lock",
    "Pipfile.lock", "uv.lock", "go.sum", "composer.lock", "Gemfile.lock", "flake.lock",
}
NOISE_DIRS = {"node_modules", "vendor", "dist", "build", ".next", "__pycache__", "third_party", "target",
              ".venv", "venv"}
NOISE_SUFFIXES = (".min.js", ".min.css", ".map", ".snap", ".lock", ".svg")

# Folders we never search for repositories (they are huge and never contain your own work).
SKIP_WHILE_SEARCHING = NOISE_DIRS | {"Library", ".Trash", ".cache", ".npm", ".cargo", ".rustup", "Applications"}

# File extension -> (language, color). Colors follow GitHub's, lightened where too dark on a dark screen.
LANGUAGES = {}
for _exts, _lang in [
    ("py pyi", ("Python", "#4b8bd4")), ("ipynb", ("Jupyter", "#f37726")),
    ("js mjs cjs jsx", ("JavaScript", "#f1e05a")), ("ts tsx mts cts", ("TypeScript", "#3178c6")),
    ("go", ("Go", "#00ADD8")), ("rs", ("Rust", "#dea584")), ("java", ("Java", "#d08a3c")),
    ("kt kts", ("Kotlin", "#A97BFF")), ("swift", ("Swift", "#F05138")), ("c h", ("C", "#a8b9cc")),
    ("cc cpp cxx hpp hh", ("C++", "#f34b7d")), ("cs", ("C#", "#2fa84f")), ("rb", ("Ruby", "#e0453c")),
    ("php", ("PHP", "#8892bf")), ("html htm", ("HTML", "#e34c26")), ("css scss sass less", ("CSS", "#a07ee6")),
    ("vue", ("Vue", "#41b883")), ("svelte", ("Svelte", "#ff3e00")), ("dart", ("Dart", "#00B4AB")),
    ("lua", ("Lua", "#6c7fd8")), ("zig", ("Zig", "#ec915c")), ("ex exs", ("Elixir", "#9b6bb5")),
    ("hs", ("Haskell", "#8f7fd1")), ("scala", ("Scala", "#de3c4b")), ("sql", ("SQL", "#e38c00")),
    ("sh bash zsh fish", ("Shell", "#89e051")), ("md mdx rst txt adoc", ("Docs", "#c9b98f")),
    ("json yml yaml toml ini cfg xml", ("Config", "#8b95a1")),
]:
    for _ext in _exts.split():
        LANGUAGES["." + _ext] = _lang
UNKNOWN_LANGUAGE = ("Other", "#6b7280")
NOT_CODE = {"Docs", "Config", "Other"}   # shown in the breakdown, never as "your top language"

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December"]

# Words that say nothing about you, left out of "your favourite word".
STOPWORDS = set("""
a an the and or but to of in on for with from by at as is are was be been it its this that these those
into onto up out off over under via vs not no yes do does did done make made add added adds update
updated updates use used using new more some all any can should will would just so then than when
also only now get got set my our your we i you he she they them us me v2 v1 etc wip
""".split())


# ---------------------------------------------------------------------------------------------
# 1. Finding repositories
# ---------------------------------------------------------------------------------------------

def is_repo(folder):
    return (Path(folder) / ".git").exists()


def find_repos(paths, max_depth=4):
    """Every git repository in `paths`. A path that is not a repo is searched (a few levels deep)."""
    found = []
    for start in paths:
        start = Path(start).expanduser().resolve()
        if is_repo(start):
            found.append(start)
            continue
        for folder, subfolders, _ in os.walk(start):
            depth = len(Path(folder).relative_to(start).parts)
            if is_repo(folder):
                found.append(Path(folder))
                subfolders[:] = []      # don't look for repos inside a repo
            elif depth >= max_depth:
                subfolders[:] = []
            else:
                subfolders[:] = [s for s in subfolders if s not in SKIP_WHILE_SEARCHING and not s.startswith(".")]
    return sorted(set(found))


# ---------------------------------------------------------------------------------------------
# 2. Reading commits
# ---------------------------------------------------------------------------------------------

def git_config(repo, key):
    result = subprocess.run(["git", "-C", str(repo), "config", key], capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    return result.stdout.strip()


def my_identities(repos, extra):
    """Names and emails that count as 'you': your git config in each repo, plus any --author."""
    identities = {a.lower() for a in extra}
    if not extra:
        for repo in repos:
            for key in ("user.name", "user.email"):
                value = git_config(repo, key)
                if value:
                    identities.add(value.lower())
    return identities


def is_noise(path):
    parts = path.split("/")
    return parts[-1] in NOISE_FILES or path.endswith(NOISE_SUFFIXES) or any(p in NOISE_DIRS for p in parts[:-1])


def read_commits(repo, year, all_branches=False):
    """Every non-merge commit in `repo` written in `year`, newest first.

    Times are the author's own local time (from %aI), so "you commit at 2 AM" means 2 AM where
    you were, not where this script runs.
    """
    command = ["git", "-C", str(repo), "-c", "core.quotepath=off", "log", "--no-merges", "--no-renames",
               "--numstat", f"--since={year - 1}-12-30", "--format=%x1e%H%x1f%aI%x1f%aN%x1f%aE%x1f%s"]
    if all_branches:
        command.append("--all")
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:      # an empty repo, or not readable: just skip it
        return []
    commits = []
    for line in result.stdout.split("\n"):  # not splitlines(): it also splits on \x1e
        if line.startswith("\x1e"):  # a new commit starts: hash, time, author, email, message
            full_hash, when, name, email, subject = line[1:].split("\x1f", 4)
            when = when.replace("Z", "+00:00")    # newer git writes UTC as "Z"; Python < 3.11 can't read it
            commits.append({"hash": full_hash, "when": datetime.fromisoformat(when), "name": name,
                            "email": email, "subject": subject.strip(), "repo": repo.name, "files": []})
        elif line and commits:       # one changed file: "added<TAB>deleted<TAB>path"
            added, deleted, path = line.split("\t", 2)
            if added != "-" and not is_noise(path):   # "-" means a binary file
                commits[-1]["files"].append((path, int(added), int(deleted)))
    return [c for c in commits if c["when"].year == year]


def is_mine(commit, identities):
    return commit["name"].lower() in identities or commit["email"].lower() in identities


# ---------------------------------------------------------------------------------------------
# 3. Turning commits into facts
# ---------------------------------------------------------------------------------------------

def language_of(path):
    return LANGUAGES.get(Path(path).suffix.lower(), UNKNOWN_LANGUAGE)


def runs(days):
    """Longest run of consecutive days in a sorted list of dates, and the longest gap between them."""
    best = (1, days[0], days[0])
    gap = (0, days[0], days[0])
    start = days[0]
    for before, after in zip(days, days[1:]):
        if (after - before).days == 1:
            if (after - start).days + 1 > best[0]:
                best = ((after - start).days + 1, start, after)
        else:
            start = after
            if (after - before).days - 1 > gap[0]:
                gap = ((after - before).days - 1, before, after)
    return best, gap


def hour_label(hour):
    return f"{hour % 12 or 12} {'AM' if hour < 12 else 'PM'}"


def time_of_day(hour):
    if 5 <= hour < 9:
        return "the early morning"
    if 9 <= hour < 12:
        return "the morning"
    if 12 <= hour < 17:
        return "the afternoon"
    if 17 <= hour < 22:
        return "the evening"
    return "the middle of the night"


def words_in(message):
    return [w for w in re.findall(r"[a-z][a-z'-]{2,}", message.lower()) if w not in STOPWORDS]


def pick_personality(traits):
    """Each trait has a value and the threshold where it becomes "you". The strongest one wins."""
    best = None
    for key, (value, threshold, emoji, title, reason) in traits.items():
        score = value / threshold if threshold else 0
        if score >= 1 and (best is None or score > best[0]):
            best = (score, key, emoji, title, reason)
    if best is None:
        return {"key": "builder", "emoji": "🏗️", "title": "The Builder",
                "reason": "Steady, balanced, always shipping. No bad habits worth roasting."}
    _, key, emoji, title, reason = best
    return {"key": key, "emoji": emoji, "title": title, "reason": reason}


def build_facts(commits, year, name, everyone=False, private=False, today=None):
    """Everything the story shows, as plain numbers and strings."""
    today = today or date.today()
    commits = sorted(commits, key=lambda c: c["when"])
    days = sorted({c["when"].date() for c in commits})
    added = sum(a for c in commits for _, a, _ in c["files"])
    removed = sum(d for c in commits for _, _, d in c["files"])

    by_hour, by_weekday, by_month, by_day = [0] * 24, [0] * 7, [0] * 12, Counter()
    for c in commits:
        by_hour[c["when"].hour] += 1
        by_weekday[c["when"].weekday()] += 1
        by_month[c["when"].month - 1] += 1
        by_day[c["when"].date().isoformat()] += 1

    languages, files, repos = Counter(), Counter(), Counter()
    for c in commits:
        repos[c["repo"]] += 1
        for path, a, d in c["files"]:
            languages[language_of(path)] += a + d
            files[(c["repo"], path)] += 1
    language_total = sum(languages.values()) or 1

    streak, pause = runs(days)
    peak_hour = max(range(24), key=lambda h: by_hour[h])
    peak_day = max(range(7), key=lambda d: by_weekday[d])
    peak_month = max(range(12), key=lambda m: by_month[m])
    busiest_date, busiest_count = by_day.most_common(1)[0]
    biggest = max(commits, key=lambda c: sum(a + d for _, a, d in c["files"]))

    messages = [c["subject"] for c in commits if c["subject"]]
    word_counts = Counter(w for m in messages for w in set(words_in(m)))
    fixes = sum(1 for m in messages if re.search(r"\bfix", m, re.IGNORECASE))
    night = sum(by_hour[h] for h in (22, 23, 0, 1, 2, 3, 4))
    early = sum(by_hour[h] for h in (5, 6, 7, 8))
    weekend = by_weekday[5] + by_weekday[6]
    average_length = sum(len(m) for m in messages) / max(len(messages), 1)
    shortest = min(messages, key=len) if messages else ""
    total = len(commits)

    # The year so far: up to today for the current year, all of it for a past one.
    last_day = min(today, date(year, 12, 31))
    elapsed = (last_day - date(year, 1, 1)).days + 1

    people = Counter(c["name"] for c in commits)

    top_file = files.most_common(1)[0] if files else None

    # How the personality lines talk: to you, or about the project.
    if everyone:
        you, you_l, your, your_l = name, "anyone here", "The", "all"
    else:
        you, you_l, your, your_l = "You", "you", "Your", "your"
    traits = {
        "night_owl": (night / total, 0.25, "🦉", "The Night Owl",
                      f"{round(100 * night / total)}% of {your_l} commits landed between 10 PM and 5 AM."),
        "early_bird": (early / total, 0.2, "🌅", "The Early Bird",
                       f"{round(100 * early / total)}% of {your_l} commits were in before 9 AM."),
        "weekend_warrior": (weekend / total, 0.33, "⚔️", "The Weekend Warrior",
                            f"{round(100 * weekend / total)}% of {your_l} commits happened on weekends."),
        "minimalist": (removed / max(added, 1), 1.0, "✂️", "The Minimalist",
                       f"{you} deleted {removed:,} lines and only wrote {added:,}. Less code, fewer bugs."),
        "firefighter": (fixes / total, 0.3, "🧯", "The Firefighter",
                        f"{fixes} of {your_l} {total} commits say \"fix\". Something was always on fire."),
        "explorer": (len(repos) / 8, 1.0, "🧭", "The Explorer",
                     f"{you} committed to {len(repos)} different repositories. Why pick one?"),
        "machine": (streak[0] / 21, 1.0, "🤖", "The Machine",
                    f"A {streak[0]}-day streak. Does {you_l} ever sleep?"),
        "haiku_poet": (12 / max(average_length, 1), 1.0, "🍃", "The Haiku Poet",
                       f"{your} average commit message is {round(average_length)} characters. Brevity is a gift."),
        "novelist": (average_length / 60, 1.0, "📜", "The Novelist",
                     f"{your} average commit message is {round(average_length)} characters long. Future readers are grateful."),
    }
    if total < 10:   # too few commits to judge anyone's habits
        traits = {}

    facts = {
        "year": year,
        "name": name,
        "everyone": everyone,
        "private": private,
        "generated": today.isoformat(),
        "project_url": PROJECT_URL,
        "commits": total,
        "people": len(people),
        "top_people": [{"name": n, "commits": k} for n, k in people.most_common(5)] if everyone else [],
        "active_days": len(days),
        "elapsed_days": elapsed,
        "added": added,
        "removed": removed,
        "repos": [{"name": f"Repo {i + 1}" if private else r, "commits": k}
                  for i, (r, k) in enumerate(repos.most_common(5))],
        "repo_count": len(repos),
        "files_touched": len(files),
        "streak": {"days": streak[0], "start": streak[1].isoformat(), "end": streak[2].isoformat()},
        "longest_break": {"days": pause[0], "start": pause[1].isoformat(), "end": pause[2].isoformat()},
        "by_hour": by_hour,
        "by_weekday": by_weekday,
        "by_month": by_month,
        "by_day": dict(by_day),
        "peak_hour": peak_hour,
        "peak_hour_label": hour_label(peak_hour),
        "peak_time_of_day": time_of_day(peak_hour),
        "peak_weekday": WEEKDAYS[peak_day],
        "peak_month": MONTHS[peak_month],
        "busiest_day": {"date": busiest_date, "commits": busiest_count},
        "night_commits": night,
        "weekend_share": round(weekend / total, 3),
        "languages": [{"name": lang, "color": color, "share": round(n / language_total, 3)}
                      for (lang, color), n in languages.most_common(6)],
        "top_language": next((lang for (lang, _), _ in languages.most_common() if lang not in NOT_CODE), None),
        "top_file": {"path": top_file[0][1], "repo": top_file[0][0],
                     "commits": top_file[1]} if top_file and not private else None,
        "biggest_commit": {
            "message": None if private else biggest["subject"],
            "repo": None if private else biggest["repo"],
            "date": biggest["when"].date().isoformat(),
            "added": sum(a for _, a, _ in biggest["files"]),
            "removed": sum(d for _, _, d in biggest["files"]),
        },
        "first_commit": {"message": "" if private else commits[0]["subject"],
                         "date": commits[0]["when"].date().isoformat()},
        "messages": {
            "average_length": round(average_length),
            "fixes": fixes,
            "favourite_word": word_counts.most_common(1)[0][0] if word_counts and not private else None,
            "favourite_word_count": word_counts.most_common(1)[0][1] if word_counts and not private else 0,
            "shortest": None if private else shortest,
        },
        "personality": pick_personality(traits),
    }
    facts["captions"] = default_captions(facts)
    return facts


def plural(n, word):
    return f"{n:,} {word}{'' if n == 1 else 's'}"


def pretty_date(iso):
    d = date.fromisoformat(iso)
    return f"{MONTHS[d.month - 1]} {d.day}"


def default_captions(f):
    """One line of text for each slide. An AI (or you) can replace any of them with --script."""
    you = "this project" if f["everyone"] else "you"
    word = f["messages"]["favourite_word"]
    lang = f["top_language"] or "Plain text"
    captions = {
        "intro": f"{f['year']} is almost over. Let's look at what {you} built.",
        "commits": (f"On {f['active_days']} of the year's {f['elapsed_days']} days"
                    + (f", across {f['repo_count']} repos." if f["repo_count"] > 1 else ".")),
        "lines": (f"Net +{f['added'] - f['removed']:,} lines. The codebase grew."
                  if f["added"] >= f["removed"] else "More deleted than written. The codebase got lighter."),
        "months": f"{f['peak_month']} was the big one.",
        "hours": f"Most of the work happened in {f['peak_time_of_day']}, around {f['peak_hour_label']}.",
        "weekdays": f"Favourite day to ship: {f['peak_weekday']}.",
        "streak": (f"The longest break was {f['longest_break']['days']} days, "
                   f"{pretty_date(f['longest_break']['start'])} to {pretty_date(f['longest_break']['end'])}."
                   if f["longest_break"]["days"] else "Not a single day off. Respect."),
        "calendar": f"The busiest day was {pretty_date(f['busiest_day']['date'])}, with {f['busiest_day']['commits']} commits.",
        "languages": f"{lang} did most of the talking.",
        "files": ("Every project has one file that everybody touches." if f["everyone"]
                  else "Every project has one file that keeps pulling you back."),
        "biggest": "The biggest single commit of the year.",
        "messages": (f"\"{word}\" appeared in {f['messages']['favourite_word_count']} commit messages."
                     if word else f"{plural(f['messages']['fixes'], 'commit')} said \"fix\"."),
        "personality": f["personality"]["reason"],
        "outro": f"That was {f['year']}. Here's to the next one.",
    }
    if f["everyone"]:
        captions["people"] = f"{f['people']:,} people made commits this year."
    return captions


# ---------------------------------------------------------------------------------------------
# 4. Writing the story
# ---------------------------------------------------------------------------------------------

def apply_script(facts, path):
    """Replace the default captions (and optionally the name) with ones from a JSON file."""
    script = json.loads(Path(path).read_text(encoding="utf-8"))
    for key, text in script.get("captions", {}).items():
        if key not in facts["captions"]:
            sys.exit(f"commit-wrapped: unknown caption \"{key}\". Known: {', '.join(facts['captions'])}")
        facts["captions"][key] = text
    if script.get("name"):
        facts["name"] = script["name"]
    if script.get("personality"):
        facts["personality"].update(script["personality"])


def render(facts, output):
    """Put the facts into player.html. The result is one file that works offline."""
    template = (HERE / "player.html").read_text(encoding="utf-8")
    data = json.dumps(facts, ensure_ascii=False).replace("</", "<\\/")  # can't end the <script> early
    Path(output).write_text(template.replace("/*__FACTS__*/null", data), encoding="utf-8")


def main():
    # Windows may print with an old code page; never crash on an emoji or someone's name.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Your year in code, as a story you can share.")
    parser.add_argument("paths", nargs="*", default=["."], help="repos, or folders to search for repos")
    parser.add_argument("-o", "--output", default="wrapped.html", help="where to write it (default: wrapped.html)")
    parser.add_argument("--year", type=int, default=date.today().year, help="which year (default: this year)")
    parser.add_argument("--author", action="append", default=[],
                        help="your name or email, if git config doesn't know it (repeat for several)")
    parser.add_argument("--name", help="the name shown on the story (default: your git user.name)")
    parser.add_argument("--everyone", action="store_true", help="count every contributor: a project's year")
    parser.add_argument("--private", action="store_true", help="hide repo names, file paths and commit messages")
    parser.add_argument("--all-branches", action="store_true", help="also count commits not on the current branch")
    parser.add_argument("--brief", action="store_true", help="print the facts as JSON instead of writing the story")
    parser.add_argument("--script", help="a JSON file with your own captions")
    parser.add_argument("--open", action="store_true", help="open the result in your browser")
    args = parser.parse_args()

    repos = find_repos(args.paths)
    if not repos:
        sys.exit("commit-wrapped: no git repositories found there. Point me at a repo or a folder of repos.")

    identities = my_identities(repos, args.author)
    commits = []
    for repo in repos:
        for commit in read_commits(repo, args.year, args.all_branches):
            if args.everyone or is_mine(commit, identities):
                commits.append(commit)

    if not commits:
        who = "anyone" if args.everyone else f"you ({', '.join(sorted(identities)) or 'unknown'})"
        sys.exit(f"commit-wrapped: found {len(repos)} repos but no commits by {who} in {args.year}.\n"
                 f"Try --author \"Your Name\" or --author you@example.com.")

    if args.everyone:
        name = args.name or (repos[0].name if len(repos) == 1 else "Your projects")
    else:
        name = args.name or next((git_config(r, "user.name") for r in repos if git_config(r, "user.name")), "You")

    facts = build_facts(commits, args.year, name, everyone=args.everyone, private=args.private)
    if args.script:
        apply_script(facts, args.script)

    if args.brief:
        brief = {k: v for k, v in facts.items() if k != "by_day"}
        print(json.dumps(brief, indent=2, ensure_ascii=False))
        return

    render(facts, args.output)
    print(f"{plural(len(commits), 'commit')} from {plural(len(repos), 'repo')} -> {args.output}")
    if args.open:
        webbrowser.open(Path(args.output).resolve().as_uri())


if __name__ == "__main__":
    main()
