# How this project is written

`teamspeak-prometheus` was originally written by hand. Its ongoing development
is done with AI assistance, directed by a human (Tilmann Felgner), who sets the
rules, reviews the output, runs the checks, and decides what ships.

That is a fact about this repository, not a quality claim. Tests, CI, review, and
your own reading are how to judge the code.

## The rules the models follow

`AGENTS.md` and `policies/` are normative and public on purpose. They define the
things a model cannot infer from 140 lines of Python:

* which metric names are a contract with existing users,
* that this exporter reads and never administers,
* that the ServerQuery password never reaches a log,
* what is deliberately still broken, and must not be fixed opportunistically
  ([modernization-backlog.md](modernization-backlog.md)).

`CLAUDE.md` simply includes `AGENTS.md`.

## What this means for contributors

A pull request can be typed by a human or a model. The bar is the same: one
purpose, tests for behavior changes, `make check` green, no secrets, no silent
change to the metric surface. See [../CONTRIBUTING.md](../CONTRIBUTING.md).
