# Contributing

## Running the tests

```bash
pip install -e ".[dev]"
pytest
```

The suite builds a real (tiny) git repository and runs the whole pipeline against a stub
model, so it needs `git` configured with a name and email, and nothing else. No network,
no API key, no model.

## What the tests are for

Three things are load-bearing and have tests that state their rules:

- **the ledger** (`tests/test_ledger.py`) — precedence between `claim`, `reject`,
  `claim … from`, `merge` and tombstones. A user writes these by hand; their meaning must
  not drift.
- **the pipeline** (`tests/test_pipeline.py`) — a rebuild reproduces the catalogue, an
  update re-asks nothing, a rule moves files *and* their history, territory speaks in
  today's names.
- **the model hookup** (`tests/test_provider.py`) — what a config turns into on the wire,
  what is refused before anything is spent, and that the cache keys change when the
  request does.

## Adding a provider

Most endpoints need no code: give them a `base_url`, and pass anything unusual through
`headers`, `query` or `params`. A new `kind` is only warranted when the wire format itself
differs (as Anthropic's Messages API does). If you add one, add it to `KINDS`, to
`_auth_header`, and to `tests/test_provider.py`.

## Claims and numbers

Every number in the README and in `docs/measurements.md` came from a run that can be
repeated: say which repository, how many commits, and which model. If you change something
that should improve answers, show it with `gitchronicle eval --samples 3` before and
after — a single run of that eval moves by several points on wording alone.

## Style

Comments earn their place by saying what the code cannot: why this approach, which edge
case forced the branch, what was measured. No narration of the obvious.
