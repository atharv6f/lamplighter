# Lamplighter

Working name. An always-on personal agent that runs on a Mac. It takes chores from a phone (Telegram) or a terminal chat, runs scheduled jobs, and checks its own results.

The harness is written by hand in Python, to learn how one works.

## Status

Model access is built: one interface for asking a model for a reply, a scripted model for tests, a client for Anthropic's API, and a way to record calls and play them back.

Saved state is built: a log of every session's events, a ledger of the actions the agent takes, a store for large files, an inbox for incoming messages and an outbox for replies. They are kept in one SQLite file and a folder of files, and they survive the process being killed at any moment.

The agent loop and a terminal chat are built. The loop asks the model, runs the tools it asks for, and saves every step before the next, so a run cut off by a kill is carried on at the next start. It has limits on steps, time, tokens and money, and it can be stopped. Two small tools are there to try it with: `current_time` and `wait`.

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

`lamplighter.toml` holds the settings: the model, its limits and its prices, where the saved state lives, and how one run behaves. No secret goes in it.

## Chat

```
uv run --env-file .env lamplighter chat                    # start a new session and print its id
uv run --env-file .env lamplighter chat --session SESSION  # carry on a session
```

Each line you type is one message. One line is printed before each model call and each tool call, then the reply. Type `stop`, or press Ctrl-C, to stop a run. A second Ctrl-C during the same run leaves at once with exit code 130, and the next start carries on from what is saved. Ctrl-C or Ctrl-D at the prompt leaves.

One process at a time can use a store. At start, any other session with work waiting is listed with the command that carries it on.

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

The chat has its own kill tests. In one process, a chat is killed just before and just after each of its calls to a store, the model, a tool or the channel, then started again, for twelve chats. After every kill each run has ended, each tool call has one result, no tool ran twice, each message is in the log once, and each reply was shown once, in order. Under `-m crash` the restart is killed too, at each of its calls. A second test kills the real `lamplighter chat` process with `kill -9` at random moments: ten times in `make check`, two hundred under `-m crash`.

`uv run python -m scripts.chat_timing` times the chat on this machine: Ctrl-C to the prompt, the rebuild of a long session's transcript, and the longest line the terminal passes on. It calls no model.

`uv run python scripts/commit_timing.py` prints how long a commit takes on this machine, with the Mac's full flush on and off. It writes only in a temporary folder.

## Live tests

Eight tests call the real Anthropic API. Three of them run the loop on a real store with the stub tools. They cost a little money, so `make check` leaves them out.

Put your API key in a file named `.env` in this folder. Git ignores that file. It holds one line:

```
ANTHROPIC_API_KEY=your-key-here
```

Then run:

```
uv run --env-file .env pytest -m live -s
```

Each test prints the token counts and the cost of its calls, and how many stream events it did not know and skipped.
