# Lamplighter

Working name. An always-on personal agent that runs on a Mac. It takes chores from a phone (Telegram) or a terminal chat, runs scheduled jobs, and checks its own results.

The harness is written by hand in Python, one layer at a time.

## Layout

- `context/` holds the docs for each layer, one folder per layer: `context/layer-00/`, `context/layer-01/`, and so on.

A layer folder holds only the docs that layer needs:

- `plan.md`: what the layer builds and in what order, and when it is done.
- `hld.md`: the high-level design, meaning the parts and how they connect.
- `lld.md`: the low-level design, meaning the data shapes, functions and edge cases.
- `spec.md`: the exact behaviour other layers can rely on.

## Status

Nothing is built yet. Layer 0 (project setup) is next.
