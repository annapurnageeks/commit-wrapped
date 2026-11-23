---
name: commit-wrapped
description: Make a "Wrapped"-style year-in-review story of someone's coding year (or a project's year) from their git history, as one self-contained HTML file with animated slides and a shareable PNG card. Use when the user asks for their "year in code", "coding wrapped", "git wrapped", "GitHub wrapped", a yearly coding recap or stats, "what did I build this year", a developer year-end review, or a fun summary of a project's year.
---

# commit-wrapped

`wrapped.py`, in the same folder as this file, does the counting and the animation. Your job is to write captions that feel **personal, specific and a little bit cheeky**, the way Spotify Wrapped does.

## 1. Find the repos and read the facts

Ask where the user keeps their code if you don't know (often `~/code`, `~/projects`, `~/src` or `~`). Then:

```bash
python3 <this skill's folder>/wrapped.py <folder> --brief
```

It searches the folder for git repos, keeps the user's own commits from this year (matched by their git `user.name` and `user.email`), and prints the facts as JSON.

- **"No commits by you"?** The user may commit under another email. Look at `git log --format='%aN <%aE>' | sort | uniq -c | sort -rn | head` in one of their repos, then pass `--author "Name"` or `--author email` (repeatable).
- **A project's year instead of a person's:** add `--everyone` and `--name "Project"`.
- **Private work:** `--private` hides repo names, file paths and commit messages. Suggest it if the user will share the result and works on closed-source code.
- **A different year:** `--year 2025`.

## 2. Look closer at what's interesting

The brief already has the headline numbers. Pick 2–3 things worth a closer look and check them, for example:

```bash
git -C <repo> show --stat <hash>                       # what the biggest commit really was
git -C <repo> log --author="<name>" --since=<date> --until=<date> --oneline   # the streak, the busy month
```

Specifics make it feel personal. "You spent March rewriting the parser" beats "March was busy".

## 3. Write `captions.json`

```json
{
  "captions": {
    "intro": "...",
    "commits": "...",
    "lines": "...",
    "months": "...",
    "hours": "...",
    "weekdays": "...",
    "streak": "...",
    "calendar": "...",
    "languages": "...",
    "files": "...",
    "biggest": "...",
    "messages": "...",
    "personality": "...",
    "outro": "..."
  },
  "personality": { "title": "The Night Owl", "emoji": "🦉", "reason": "..." }
}
```

Every key is optional: anything you leave out keeps its default. `people` also exists in `--everyone` mode. `personality` is optional too; only override it if the data clearly supports a better title.

Rules for captions:

- **One sentence, at most ~90 characters.** They sit under big numbers on a phone-sized screen.
- **True.** Every claim must come from the brief or from git. Don't invent numbers.
- **Warm, cheeky, never mean.** Tease habits ("3 AM commits again?"), celebrate wins. Never mock someone's skill, and never mention coworkers by name in a personal wrapped.
- **Don't repeat the number already on the slide.** The slide shows it big; the caption adds meaning.
- **No secrets.** Commit messages and file names can be sensitive. If the user plans to share it publicly, ask before quoting a commit message, or use `--private`.

## 4. Render it

```bash
python3 <this skill's folder>/wrapped.py <folder> --script captions.json -o wrapped.html --open
```

Tell the user: tap or use ← → to move through the slides, Space to pause, H to hide the controls for screen recording. The last slide has **Save image** (a 1080×1920 PNG for stories and posts), **Copy text** and **Post on X**.
