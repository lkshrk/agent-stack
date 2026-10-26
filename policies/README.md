# policies

Policies that other workloads fetch from this repository at runtime instead of shipping them in an image.

| Path | Consumer |
| --- | --- |
| `pr-agent/pr-agent.toml`, `pr-agent/review.md` | The PR-Agent init container in h-cloud (`kubernetes/apps/ai/pr-agent`) fetches both from `main` at pod start and merges `review.md` into `extra_instructions`. Repository-local overrides remain possible |
