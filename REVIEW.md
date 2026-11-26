# Review instructions

The author types the code under `src/lamplighter/` by hand, to learn how an agent harness works. A review here finds real problems and explains them. It does not rewrite the code.

## How to report

- Each finding names the file and line, says what goes wrong, and gives the input or the order of events that triggers it.
- A claim about behaviour needs a line you have read. A guess from a name is not enough.
- For code under `src/lamplighter/`, describe the fix in words. Do not write the finished code unless asked.
- If nothing is wrong, say so. Do not invent findings.

## What Important means here

Important is for a finding that would do one of these:

- Give a wrong result.
- Lose a message or an action, or do one twice.
- Break a promise in a port's docstring.
- Hide a failure.
- Leak a secret or real personal data.
- Break an import rule from `CONTRIBUTING.md`.

Style, naming and tidiness are a Nit at most.

## Check in this order

1. Does the code do what the port's docstring promises? Include the cases where it is killed part way, times out or is cancelled.
2. Would each new test fail if the code were broken? Is the new behaviour tested at all?
3. Is the code more complex than the job needs? Look for code built for a future need, a flag that switches behaviour, and a wrapper with one caller.
4. Do the names and comments follow `CONTRIBUTING.md`?

## Always check

- No secret, real message, real trace or personal data in code, tests or fixtures.
- No numbering, ids or terms from private planning notes.
- No type, error class or field name from a provider's library outside that provider's adapter.
- No test skipped or weakened to make the checks pass.

## Do not report

- Anything `make check` already enforces: formatting, lint, types and import direction.
- `uv.lock` and other generated files.

## Cap the nits

Report at most five Nits per review.
