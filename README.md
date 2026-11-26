# Lamplighter

Working name. An always-on personal agent that runs on a Mac. It takes chores from a phone (Telegram) or a terminal chat, runs scheduled jobs, and checks its own results.

The harness is written by hand in Python, to learn how one works.

## Status

Model access is built: one interface for asking a model for a reply, a scripted model for tests, a client for Anthropic's API, and a way to record calls and play them back.

Saved state is built: a log of every session's events, a ledger of the actions the agent takes, a store for large files, and an inbox for incoming messages. They are kept in one SQLite file and a folder of files, and they survive the process being killed at any moment. The agent loop does not exist yet, so the only thing to run is the `lamplighter store` commands below.

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

`lamplighter.toml` holds the settings: the model, its limits and its prices, and where the saved state lives. No secret goes in it.

## Saved state

The store lives in `~/.lamplighter/`: `state.db` holds the events, actions and inbox, and `blobs/` holds large files named by their SHA-256. The setting refuses a folder inside `~/Documents`, `~/Desktop` or a cloud-synced folder, because a sync tool that copies a SQLite file while it is being written can damage it.

Four commands look after it. None of them makes a new store.

```
uv run lamplighter store sessions                         # every session and its unfinished actions
uv run lamplighter store sizes                            # how large the store is, and its largest sessions
uv run lamplighter store export SESSION FOLDER            # copy one session into a new folder
uv run lamplighter store delete SESSION --export-to FOLDER  # export it, check the copy, then delete it
```

`delete` always exports first. It refuses a session whose last run did not end or that has an action not yet settled, and one that names a stored file by its hash alone, because the export could not copy that file.

## Crash tests

A test process writes to a real store and is killed with `kill -9` at random moments. After every kill the store must be whole, with nothing lost that was reported saved. Ten kills run in `make check`. The long run of 200 takes a few minutes:

```
uv run pytest -m crash -s
```

On a slow drive, `LAMPLIGHTER_KILL_WAIT_MS=5,400` gives each run longer before its kill.

`uv run python scripts/commit_timing.py` prints how long a commit takes on this machine, with the Mac's full flush on and off. It writes only in a temporary folder.

## Live tests

Five tests call the real Anthropic API. They cost a little money, so `make check` leaves them out.

Put your API key in a file named `.env` in this folder. Git ignores that file. It holds one line:

```
ANTHROPIC_API_KEY=your-key-here
```

Then run:

```
uv run --env-file .env pytest -m live -s
```

Each test prints the token counts and the cost of its calls, and how many stream events it did not know and skipped.
