# Lamplighter

Working name. An always-on personal agent that runs on a Mac. It takes chores from a phone (Telegram) or a terminal chat, runs scheduled jobs, and checks its own results.

The harness is written by hand in Python, to learn how one works.

## Status

Model access is built: one interface for asking a model for a reply, a scripted model for tests, a client for Anthropic's API, and a way to record calls and play them back.

Saved state is built: a log of every session's events, a ledger of the actions the agent takes, a store for large files, an inbox for incoming messages and an outbox for replies. They are kept in one SQLite file and a folder of files, and they survive the process being killed at any moment.

The agent loop and a terminal chat are built. The loop asks the model, runs the tools it asks for, and saves every step before the next, so a run cut off by a kill is carried on at the next start. It has limits on steps, time, tokens and money, and it can be stopped.

Seven tools are built in. `read_file`, `write_file` and `edit_file` work on files in one folder, the work folder. `search_files` searches it, `shell` runs one command line in it, `web_fetch` reads a page from a public web address, and `read_output` reads the rest of a result that was too long to give whole. A change to a file goes through the action ledger, so after a kill the agent checks whether it happened and never makes it twice. Commands run inside macOS's sandbox: they can write only in the work folder and a scratch folder, cannot read your home folder or the store, and have no network.

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

`lamplighter.toml` holds the settings: the model, its limits and its prices, where the saved state lives, how one run behaves, and where the tools work. No secret goes in it.

## Chat

```
uv run --env-file .env lamplighter chat                    # start a new session and print its id
uv run --env-file .env lamplighter chat --session SESSION  # carry on a session
```

Each line you type is one message. One line is printed before each model call and each tool call, then the reply. Type `stop`, or press Ctrl-C, to stop a run. A second Ctrl-C during the same run leaves at once with exit code 130, and the next start carries on from what is saved. Ctrl-C or Ctrl-D at the prompt leaves.

One process at a time can use a store. At start, any other session with work waiting is listed with the command that carries it on, and so is the count of actions whose outcome is not known.

The tools work in `~/lamplighter-work`, and commands get a home folder and a temp folder in `~/Library/Caches/Lamplighter`. Both are made at start if they are missing. A folder reached through a symbolic link is refused: write its real path in the settings. If the sandbox cannot be put up, the chat says so and starts anyway: commands and searches then fail, and the file tools and `web_fetch` still work.

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

The chat has its own kill tests. In one process, a chat is killed just before and just after each of its calls to a store, the model, a tool or the channel, then started again. Twelve chats only read, seventeen change a made-up world, and nine write, edit and run commands in a real folder with the built-in tools. After every kill each run has ended, each tool call has one result, no tool ran twice, each message is in the log once, and each reply was shown once, in order. No change happened twice, each action's row in the ledger says what really happened, and each file holds what the ledger says. Under `-m crash` the restart is killed too, at each of its calls.

A second test kills the real `lamplighter chat` process with `kill -9` at random moments, once on stub tools and once on the built-in tools in a real folder: ten times each in `make check`, two hundred each under `-m crash`. On a Mac, `-m sandbox` also runs ten of them with every command behind the sandbox, and `-m "crash and sandbox"` runs two hundred. On another system, run the long ones with `-m "crash and not sandbox"`.

`uv run python -m scripts.chat_timing` times the chat on this machine: Ctrl-C to the prompt, the rebuild of a long session's transcript, and the longest line the terminal passes on. It calls no model.

`uv run python scripts/commit_timing.py` prints how long a commit takes on this machine, with the Mac's full flush on and off. It writes only in a temporary folder.

## Sandbox tests

The tests of the sandbox need macOS, so `make check` leaves them out. Run them on a Mac:

```
uv run pytest -m "sandbox and not crash"
```

They try each known way out of the sandbox and check what must still work inside it. CI runs them on a macOS runner.

## Live tests

Ten tests call the real Anthropic API. Five of them run the loop on a real store with the built-in tools, and their commands run in the sandbox, so they need macOS. They cost a little money, so `make check` leaves them out.

Put your API key in a file named `.env` in this folder. Git ignores that file. It holds one line:

```
ANTHROPIC_API_KEY=your-key-here
```

Then run:

```
uv run --env-file .env pytest -m live -s
```

Each test prints the token counts and the cost of its calls, and how many stream events it did not know and skipped.
