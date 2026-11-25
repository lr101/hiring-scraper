# Models for job matching

Radius ranks jobs either with GPT-6 Luna through your ChatGPT subscription (default) or
with two small local models; you switch by hand (see [Choosing the matching model](#choosing-the-matching-model)).
The local models run on CPU in their own containers, served by
[llama.cpp](https://github.com/ggml-org/llama.cpp), and start only with the
`local-models` compose profile; with **Local models** selected in Settings nothing
leaves the machine and no subscription quota is used.

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

## Choosing the matching model

Settings → Matching → **Matching model** chooses **GPT-6 Luna** (default) or **Local
models**. There is no usage cap and no automatic fallback, so only the selected model
needs to run.

- **GPT-6 Luna:** `job-analysis-worker` sends the same posting prompt to `gpt-6-luna`
  through the shared ChatGPT login (`radius_codex` volume), and asks Luna to rate each
  duty and requirement against the CV lines instead of the reranker. The `llm` and
  `llm-reranker` containers are not used and can stay stopped, which saves about 6 GB
  of RAM. When Luna cannot be reached (ChatGPT not connected, rate limit, outage,
  timeout) the stage pauses with backoff and the work stays queued; only a posting too
  long for the model is marked failed and retried later.
- **Local models:** start the containers with the profile, then switch in Settings:

  ```sh
  COMPOSE_PROFILES=local-models docker compose up -d llm llm-reranker
  ```

  To go back to Luna, switch in Settings and stop them with
  `docker compose stop llm llm-reranker`.

Switching applies to new and still-queued work; stored analyses and evidence keep
their results.

Results record their model (`provider: "luna"` in the analysis and evidence), and Luna
evidence is scored with its own calibration (`LUNA_CALIBRATION_KNOTS`, fitted on the
DEV split), so scores from both paths share the same bands. On the 71-case public set
Luna and the local models are within noise of each other: overall pairwise AUC 0.916
vs 0.893 and band error 0.016 vs 0.025, held-out AUC 0.711 vs 0.732; hard hides are
identical. Per job the two can differ by about 10 points.

Cost: each Luna call carries about 10k input tokens (half cached) and 150–260 output
tokens; one posting needs one analysis call plus one evidence call per profile. 129
calls used about 1% of a Plus plan's five-hour window and under 1% of its week.

## Laptop GPU (optional)

A laptop with an NVIDIA GPU in the same VPN can take over the local models whenever it
is on, and hand them back when it sleeps. It runs the same pinned llama.cpp build,
weights and model keys as the homelab, so stored analyses and evidence stay valid
whichever machine produced them.

**On the laptop (Fedora, NVIDIA driver 570+ working, `nvidia-smi` succeeds):**

1. Settings → Matching → Matching model → **Local models** → **Laptop GPU**: enter the
   laptop's VPN IP (or VPN host name) and save.
2. **Download setup script** (`radius-laptop-gpu.sh`, the address is filled in), then:

   ```sh
   bash radius-laptop-gpu.sh install   # once: llama.cpp CUDA 12.8 build + runtime, weights, user services
   bash radius-laptop-gpu.sh start     # whenever you want to lend the GPU
   bash radius-laptop-gpu.sh stop      # free it again
   bash radius-laptop-gpu.sh status    # both servers and GPU memory
   ```

   The script checks SHA-256 digests for every download, needs no system CUDA toolkit
   (it ships the pinned `cudart` libraries next to `llama-server`), installs two systemd
   user services (`radius-llm`, `radius-reranker`) that listen only on the VPN address,
   and opens ports 8091/8092 in the firewalld zone of the VPN interface. Traffic is plain
   HTTP inside the VPN. `autostart on` starts them with your login; `uninstall` removes
   everything again.
3. Tick **Use my laptop's GPU while it is online**. The status line shows whether each
   laptop server is up and how many requests it takes at once.

Sizing is picked from GPU memory: on a 4 GB card (for example a laptop RTX 3050 Ti) the
4B model takes two postings at once (8k tokens each, about 3.3–3.8 GB with llama.cpp's
`--fit` moving any layer that does not fit to the CPU), and the reranker runs on the
laptop CPU. With 6 GB or more the reranker joins the GPU and three or four postings run
at once. Override with `PARALLEL=… RERANK_DEVICE=gpu|cpu bash radius-laptop-gpu.sh install`.

**On the homelab server** nothing in the code or compose files needs to change:

- `job-analysis-worker` and `api` must reach the laptop's VPN address on TCP 8091 and
  8092. With the VPN client (WireGuard, Tailscale, …) on the Docker host this works out
  of the box: container traffic leaves through the host's routes. If the VPN runs in a
  container of its own, or VPN ACLs limit traffic, allow homelab → laptop on these two
  ports. Check from the worker:
  `docker compose exec job-analysis-worker python -c "import urllib.request as u; print(u.urlopen('http://LAPTOP_IP:8091/health', timeout=3).read())"`
- Keep the `llm` and `llm-reranker` containers (`COMPOSE_PROFILES=local-models`)
  running if matching should continue on the CPU while the laptop is away. Without them,
  work simply stays queued until the laptop is back.

**How the worker uses it.** Before each call the worker checks the laptop (`/health`
and `/props`, cached for 20 s) and uses it only when it answers with the pinned model
key. A failed call routes the next one back to the homelab immediately; the item stays
queued as with any outage. While the laptop serves, the worker sends as many postings
and profile/posting pairs at once as the laptop servers have slots (capped by
`JOB_ANALYSIS_MAX_PARALLEL`, default 4), and checks evidence in a second thread while
postings are analysed, so the GPU does not wait for the reranker. The homelab
containers keep one call at a time on shared cores. Batched GPU inference can differ
from the CPU in the last digits; the evaluation fixtures stay recorded on the CPU.

## Throughput and priorities

On four laptop cores the LLM needs about 25–50 s per posting and the reranker about
40–60 s per profile and posting (eight candidate lines per item). The worker therefore analyses jobs inside a
profile's search area (and remote jobs) first, newest first, and computes their
evidence immediately; the rest of the inventory follows in the background. Each step
is published per job or pair, so a restart or model outage loses at most the item in
flight. When the selected model is unavailable, that stage backs off (up to five minutes)
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
share the same cores without competing (the laptop GPU, when used, runs calls in parallel; see above). Tune with `LLM_THREADS`,
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
