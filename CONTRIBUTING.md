# Contributing

Thanks for helping! Stardeck aims to stay small, reliable with small local models, and easy to
self-host.

## Setup

```bash
./scripts/dev.sh      # or run backend and frontend separately, see README
```

You need Python 3.12 with [uv](https://docs.astral.sh/uv/), Node 22, and an OpenAI-compatible
model server (LM Studio, Ollama, vLLM...) for anything that calls the model.

## Before opening a pull request

```bash
cd backend && uv run ruff check . && uv run pytest
cd frontend && npm run lint && npm run build
```

If you touch prompts or the widget engine, run the evals for at least one model and include the
before/after summary in the PR:

```bash
cd backend && uv run python evals/run_eval.py --model <model>
```

## Guidelines

- The model proposes, code decides. Prefer a deterministic check or fix over a longer prompt.
- Never let anything write to a user's database. Every query goes through `query.run`.
- Anything the assistant changes must be a proposal the user confirms.
- New UI copy is for non-technical users: plain words, no jargon, SQL available on request.
- Charts follow the conventions in `frontend/src/lib/chartOptions.ts` (single axis, thin marks,
  validated palette, table view for every chart).

## License

By contributing you agree that your contributions are licensed under the Apache-2.0 license.
