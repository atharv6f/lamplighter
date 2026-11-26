# Lamplighter

An always-on personal agent that runs on a Mac. The harness is written by hand in Python, so its author learns how one works.

## Who writes what

- The author types the code under `src/lamplighter/` himself. There, your job is to explain and review. Write or change code there only when he asks for it in that request.
- Outside `src/lamplighter/` you write what is asked: config, CI, scripts, docs and test scaffolding.
- When you explain a fix for code under `src/lamplighter/`, say what is wrong, why, and what kind of change fixes it. Do not paste the finished code unless he asks.

## Check your work

Run `make check` before you say a change is done, and show its output. It runs:

- `uv run ruff format --check .` for formatting. `uv run ruff format .` fixes it.
- `uv run ruff check .` for lint.
- `uv run pyright` for types.
- `uv run lint-imports` for the import rule.
- `uv run pytest -q` for tests. Run one file with `uv run pytest tests/test_setup.py`.

## Layout

- `src/lamplighter/ports/`: the interfaces the core calls. One file per interface.
- `src/lamplighter/adapters/`: code that talks to the outside world. Each adapter implements one port.
- `src/lamplighter/core/`: the agent's own logic. It imports `ports` and never `adapters`.
- `src/lamplighter/app/main.py`: the only place that builds adapters.
- `tests/`: tests. `tests/contract/` will hold one set of tests per port.

## Easy to get wrong

- Names say what a thing does. Never use numbering, ids or terms from private planning notes in code, file names, comments, commit messages or pull requests.
- No secret, real message or real trace goes into the repo, a test or a pull request. Use made-up data.
- Add nothing for a future need.

The full code rules and the review rules follow.

@CONTRIBUTING.md
@REVIEW.md
