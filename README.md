# Lamplighter

Working name. An always-on personal agent that runs on a Mac. It takes chores from a phone (Telegram) or a terminal chat, runs scheduled jobs, and checks its own results.

The harness is written by hand in Python, to learn how one works.

## Status

Model access is built: one interface for asking a model for a reply, a scripted model for tests, a client for Anthropic's API, and a way to record calls and play them back. Nothing calls it yet, so there is still nothing to run.

## Layout

- `src/lamplighter/ports/`: the interfaces the core calls.
- `src/lamplighter/adapters/`: code that talks to the outside world.
- `src/lamplighter/core/`: the agent's own logic.
- `src/lamplighter/app/main.py`: the one place that joins them.
- `tests/`: tests.

## Develop

Install [uv](https://docs.astral.sh/uv/), then:

```
uv sync
make check
```

`make check` runs the formatter check, lint, type check, import rule and tests. The code rules are in [CONTRIBUTING.md](CONTRIBUTING.md).

`lamplighter.toml` holds the settings: the model, its limits and its prices. No secret goes in it.

## Live tests

Four tests call the real Anthropic API. They cost a little money, so `make check` leaves them out.

Put your API key in a file named `.env` in this folder. Git ignores that file. It holds one line:

```
ANTHROPIC_API_KEY=your-key-here
```

Then run:

```
uv run --env-file .env pytest -m live -s
```

Each test prints the token counts and the cost of its calls, and how many stream events it did not know and skipped.
