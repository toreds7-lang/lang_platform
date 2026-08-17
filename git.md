# Git cheat sheet: cloning and pushing versions v1 / v2 / v3

This repo has three tagged versions on top of the regular commit history:

| Tag | What it is |
|-----|------------|
| `v1` | English-only prototype (initial commit) |
| `v2` | Multi-language support: language registry, readings, TTS, test suite |
| `v3` | Korean added as a per-request explanation language |

GitHub remote: `https://github.com/toreds7-lang/lang_platform.git`

---

## Clone a specific version from GitHub

On any PC that doesn't have the repo yet.

**Full clone, then check out the version you want:**
```
git clone https://github.com/toreds7-lang/lang_platform.git
cd lang_platform
git checkout v2
```
Replace `v2` with `v1` or `v3`. This downloads the whole history, so you can switch between tags afterwards with another `git checkout <tag>`.

**Or: clone only that one version** (faster, smaller, no history):
```
git clone --branch v3 --depth 1 https://github.com/toreds7-lang/lang_platform.git
```
This lands you directly on `v3`'s files with just that one commit downloaded.

> Checking out a tag puts you in a "detached HEAD" state — fine for running or reading that version. If you want to make commits starting from it, branch off first:
> ```
> git checkout -b my-branch v2
> ```

**After cloning, on the new PC**, recreate the two files git never tracks:
- `.env` — copy your `OPENAI_API_KEY` and `LLM_MODEL` into it (see `.env.example` in the repo for the format)
- run `run.bat` — it builds `.venv` and installs dependencies automatically

`cache/` is not needed to run the app; it rebuilds itself as you use it.

---

## Clone a specific version from your local PC

If the other machine can reach this PC directly (shared folder, USB drive, LAN path) instead of going through GitHub, clone from the local path the same way — it works exactly like a URL:

```
git clone "d:\2026_Agent\language_platform" "d:\2026_Agent\language_platform_copy"
cd "d:\2026_Agent\language_platform_copy"
git checkout v1
```

Notes specific to a local-path clone:
- The clone gets an `origin` remote pointing back at the source folder, so `git pull` there will fetch new commits made in the original repo. Run `git remote remove origin` after cloning if you want a fully independent copy.
- Add `--no-hardlinks` if the two folders are on the same drive and you want the copy to be a real, physically separate backup:
  ```
  git clone --no-hardlinks "d:\2026_Agent\language_platform" "d:\2026_Agent\language_platform_copy"
  ```

---

## Push local changes back to GitHub

From a clone whose `origin` points at GitHub (true for every clone made from the `https://github.com/...` URL above):

```
git add -A
git status --short          # sanity check: only the files you meant to change
git commit -m "describe the change"
git push origin main
```

`git push` only ever moves the branch you're on (normally `main`). It does **not** move or create tags by itself — tags need pushing explicitly.

### Tagging a new version and pushing it

Once a commit represents a new named version (v4, v5, ...):

```
git tag -a v4 -m "Version 4: <short description>"
git push origin v4
```

To tag a commit that isn't the current `HEAD` (e.g. an older one), pass its hash:
```
git tag -a v4 <commit-hash> -m "Version 4: <short description>"
git push origin v4
```

### Pushing from a different PC than the one that made the original commits

If you cloned via the local-path method (not GitHub) and now want that PC's changes on GitHub too, add the GitHub remote once, then push normally:
```
git remote add origin https://github.com/toreds7-lang/lang_platform.git
git push -u origin main
git push origin --tags       # pushes every local tag, e.g. if you tagged there too
```

---

## Quick reference

```
git tag -l -n1                       # list all versions with their message
git log --oneline --decorate --all   # see commits and which tags point where
git checkout v1                      # switch working directory to that version
git checkout main                    # back to the latest commit on the main branch
```
