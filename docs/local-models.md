# Local models for job matching

Radius ranks jobs with two small local models. Nothing leaves the machine and no
subscription quota is used. Both run on CPU in their own containers, served by
[llama.cpp](https://github.com/ggml-org/llama.cpp).

| Container | Model | Size | Job |
|---|---|---|---|
| `llm` | [Qwen3.5-4B](https://huggingface.co/unsloth/Qwen3.5-4B-GGUF), Q4_K_M | 2.7 GB | Reads a posting and lists its duties and requirements |
| `llm-reranker` | [Qwen3-Reranker-0.6B](https://huggingface.co/ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF), Q8_0 | 0.6 GB | Checks whether a CV line shows a duty or requirement |
| `semantic-search-worker` (existing) | multilingual-e5-small (ONNX) | 0.2 GB | Picks the few CV lines worth checking |

All three are multilingual (German and English) and Apache-2.0 licensed. The weights
download once from a pinned Hugging Face revision into the `llm_models` volume, and
the server image is pinned by digest (`compose.yaml`).

## How matching works

1. **Job analysis** (once per posting, in `job-analysis-worker`). The posting is split
   into numbered lines. The LLM writes a fixed line format, enforced by a grammar,
   that names the occupational field and industry, then each duty (`D`) and each
   requirement (`R`, with must/nice and a kind such as skill, education or language).
   Every item is stored as the contiguous span of the line it cites; items the
   model reworded beyond small omissions, or whose span holds a negation the quote
   left out ("not required"), are dropped. Benefits, company descriptions and
   application instructions are never items: the prompt says so, and items from
   lines under such headings ("Was wir bieten", "Benefits", "Kontakt") are dropped,
   so "30 Tage Urlaub" or "strukturierte Einarbeitung" cannot count as
   requirements. The posting is treated as data: prompt-like text and chat control
   tokens are removed before the model sees it and the grammar allows no free-form
   answer.
2. **CV evidence units** (once per profile revision). Saved work history, education,
   skills and summary are split into short citable lines and embedded with E5.
3. **Pair evidence** (once per profile and job). For each duty and requirement, E5
   picks the eight closest CV lines, always including at least three job, study or
   saved-duty bullets: short skill lists otherwise win E5 retrieval, especially for a
   German requirement against an English CV. The reranker then reads each candidate together
   with the job's occupation and industry and scores whether the CV line actually
   shows that duty or requirement in that field. "Schulungen" in a SaaS HubSpot line
   scores near zero against "Durchführung von Schulungen" for a corrosion-protection
   role, while a coatings training line scores high. The reranker also scores role
   fit (job title versus desired and past roles) and field fit. Personal traits
   ("strukturierte Arbeitsweise", "communication skills") are not scored, since no CV
   line can prove them; the job page lists them for the cover letter instead.
4. **Scoring** (`matching_v3`, no inference). Five components in [0, 1] (duties,
   mandatory requirements, preferred requirements, role, field) form an exact additive
   ledger; bounded penalties (work style, employment type, unverified formal
   prerequisites, at most two points for language or work-permit flags) follow, then a
   monotone calibration fitted on the DEV split of the evaluation set. At most 28
   items per posting are scored, mandatory and formal requirements first; the job
   page names any requirement left unchecked. Eligibility gates (CEFR minimums,
   excluded titles, mandatory on-site, denied work permits) are unchanged and still
   hide a job only for an explicit, cited incompatibility.

A job that has not been analysed yet is **pending**: it is stored with a `pending`
flag, shows "Analysing job…" in search and never creates a Journey card. After a job
or CV edit, the previous analysis and evidence stay in use (shown as refreshing) until
the worker replaces them, so edits do not empty Journey.

## Throughput and priorities

On four laptop cores the LLM needs about 25–50 s per posting and the reranker about
40–60 s per profile and posting (eight candidate lines per item). The worker therefore analyses jobs inside a
profile's search area (and remote jobs) first, newest first, and computes their
evidence immediately; the rest of the inventory follows in the background. Each step
is published per job or pair, so a restart or model outage loses at most the item in
flight. When a model container is down, that stage backs off (up to five minutes)
while the others continue; matching and Journey keep serving the stored results. A
posting the LLM rejects as too long is retried with fewer lines, and an item that
fails is retried later with backoff instead of being dropped.

Every analysis output has its own identity (`result_hash`, from the model version and
the output). Pair evidence refers to item positions of exactly that output, so a new
model or prompt never attaches old evidence to different items. A result is published
only for the request revision the worker started from: a posting or CV edit during
inference re-queues the work instead of being acknowledged by the older result. Only
one analysis worker runs at a time (PostgreSQL advisory lock).

Both model containers have `cpu_shares: 256` so the API and crawlers stay responsive,
memory limits of 4.5 GB and 1.5 GB, and no extra Linux capabilities. The worker calls them one at a time, so they
share the same cores without competing. Tune with `LLM_THREADS`,
`LLM_MEMORY_LIMIT` and `LLM_RERANKER_MEMORY_LIMIT` in `.env`.

## Local development

```bash
scripts/dev/local-models.sh start    # downloads the pinned llama.cpp build and weights once
mise exec -- uv run python -m hiring_scraper.app.semantic_search_worker
JOB_ANALYSIS_LLM_URL=http://127.0.0.1:8091 JOB_ANALYSIS_RERANK_URL=http://127.0.0.1:8092 \
  mise exec -- uv run python -m hiring_scraper.app.job_analysis_worker
scripts/dev/local-models.sh stop
```

With Docker, `docker compose -f compose.yaml -f compose.dev.yaml up -d llm llm-reranker`
exposes the same ports.

## Evaluation

CI cannot run the models, so the evaluation records their outputs per dataset
(`tests/eval/public/v3/analyses.json` and `evidence.json`) and `tests.eval.run --matcher v3`
scores from them. Regenerate after changing a model, the prompt or the evidence
queries (entries with unchanged inputs are reused):

```bash
scripts/dev/local-models.sh start
mise exec -- uv run python -m tests.eval.v3_fixtures --dataset public
mise exec -- uv run python -m tests.eval.fit_v3            # refit weights and calibration on DEV
mise exec -- uv run python -m tests.eval.run --matcher v3 --write-baseline
```

The private real-CV set works the same way with `--dataset private`; its recorded
outputs stay next to the private data and are never committed.

## Changing a model

Model identity is part of every stored result: `job_analysis.VERSION` (LLM, prompt) and
`matching_v3.evidence.VERSION` (reranker, E5, queries). The servers run with
`--alias <model key>` and the worker rejects responses from any other model. To switch:
update the URL, file name and alias in `compose.yaml`, `scripts/dev/local-models.sh`
and the constants in `job_analysis.py` or `matching_v3/evidence.py`, regenerate the
evaluation fixtures, refit, and deploy. Existing analyses stay visible as stale until
the worker has re-analysed each job.
