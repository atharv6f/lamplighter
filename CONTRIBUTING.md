# Code rules

These are the rules for all code in this repo. A tool checks the ones a tool can check. A review checks the rest.

## What the tools check

`make check` runs all of these. A change is not done until it passes.

| Rule | Tool |
|---|---|
| Formatting | `ruff format` |
| Likely bugs, unused names, import order, outdated syntax | `ruff check` |
| No function with more than 10 branches | `ruff check` (rule C901) |
| A docstring on every module, public class and public function | `ruff check` (rules D100, D101, D103) |
| Types, in strict mode | `pyright` |
| `core` never imports `adapters` | Import Linter |
| Tests pass | `pytest` |

Do not switch a check off for a line or a file without a comment that says why.

## Names and folders

- File and folder names are short and lowercase. Use an underscore only when it helps reading.
- One idea per file, and the file is named after it. `event_log.py` is a good name. `utils.py` and `helpers.py` are not.
- A name says what the thing does. `retry_budget` is a good name. `mgr2` is not.
- Nothing in the repo uses numbering, ids or terms from private planning notes. This covers code, file names, comments, commit messages and pull requests.

## Public and internal names

- A name that starts with `_` is internal to its file. Nothing outside the file uses it.
- Each package lists what it offers in `__all__` in its `__init__.py`. Other packages import only those names.
- `core` imports `ports`. An adapter imports its own port and never another adapter. Only `app/main.py` builds adapters.
- Only plain data crosses a port. No object, error class or field name from a provider's library appears outside that provider's adapter. Example: the model adapter turns the provider's "overloaded" error into this repo's own failure value before it returns.

## Comments

- A comment says why, never what. The code already says what.
- Every file starts with a one-line docstring that says what the file is for.
- A port's docstring states what its callers can rely on. It is the short form of that port's contract.
- When code changes, its comments change in the same commit. A wrong comment is worse than none.
- No commented-out code. Delete it. Git keeps the old version.
- A `TODO` says what is missing and what would make it worth doing.

## Design

The whole design uses four building blocks. Use these, and add nothing else until the code asks for it.

- An interface is a `typing.Protocol` in `ports/`. An adapter is a class in `adapters/` that satisfies it. Adapters do not share code through a base class.
- Data that crosses a port is a frozen record of plain values that can be written as JSON.
- A failure that is expected is returned as a value. An exception means a bug.
- The clock and new ids are passed in. No code reads the time or makes a random id on its own, so a test can control both.

A new abstraction is added on its second real use, not its first.

## Keeping code simple

- A function does one job. If its description needs the word "and", split it.
- A function longer than about 40 lines needs a reason.
- No parameter that is a flag switching between two behaviours. Write two functions.
- No code for a case that cannot happen. No `try` that hides an error.
- Nothing is built for a future need. Build it when the need is real.
- A helper with one caller and one line goes back inline.

## Tests

- Each port has one set of contract tests. The same tests run against every adapter of that port, the fake included.
- A test must fail when the code is broken. Check this once by breaking the code on purpose.
- Never skip or weaken a test to make the checks pass.
- The default test run makes no network call and no model call.
- Tests use made-up data. No real message, trace or secret goes into a test or a fixture.

## Changes

- One self-contained change per pull request. Around 100 changed lines is a good size. Around 1,000 is too large to review well.
- A commit message says what changed and why, in plain words.
- Before a merge: `make check` passes, the checks on GitHub are green, and a review has run with each finding fixed or answered.
