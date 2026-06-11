# Hugging Face Space setup checklist

What you need from Hugging Face before deploying this demo, and what you **do not** need to share with anyone (including this repo).

---

## What you need to create on Hugging Face

### 1. Account

A free [huggingface.co](https://huggingface.co/join) account is enough. See [Compute](#compute-cpu-vs-gpu) below for hardware choice.

### 2. A new Space

Create one at: **https://huggingface.co/new-space**

| Setting | Recommended for this demo |
|---------|---------------------------|
| **Owner** | Your user or an org you control |
| **Space name** | e.g. `lean-cql-anc` (lowercase, hyphens OK) |
| **SDK** | **Docker** (needs Python + Node in one container) |
| **Hardware** | **CPU Basic** (v0.1) or **CPU Upgrade** (snappier builds); GPU not required |
| **Visibility** | Public (for demos) or Private (while iterating) |

After creation, your Space URL will be:

```text
https://huggingface.co/spaces/<USERNAME>/<SPACE_NAME>
```

Example: `https://huggingface.co/spaces/gatesfoundation/lean-cql-anc`

### 3. Write access token (for pushing code)

Create at: **https://huggingface.co/settings/tokens**

- Type: **Write** (or Fine-grained with write access to this Space)
- Store locally; **never commit it** or paste it into chat

You use it once for git authentication:

```bash
git clone https://huggingface.co/spaces/<USERNAME>/<SPACE_NAME>
cd <SPACE_NAME>
# copy contents of lean_cql_anc/space/ into this directory
git add .
git commit -m "Initial Lean + CQL ANC demo"
git push
# username: your HF username
# password: your HF write token (not your account password)
```

Or with the CLI:

```bash
uv tool install huggingface_hub   # or: uv run huggingface-cli login
huggingface-cli login             # paste write token when prompted
```

---

## Information to record (share only what's needed)

Fill this in for yourself or for automation — **do not share your token**.

| Item | Example | Needed for |
|------|---------|------------|
| **HF username** | `jessicalundin` | Clone URL, Space link |
| **Space name** | `lean-cql-anc` | Clone URL |
| **Space full ID** | `jessicalundin/lean-cql-anc` | `huggingface-cli`, GitHub Actions sync |
| **Visibility** | `public` | Whether the demo is link-shareable |
| **SDK** | `docker` | Must match `space/README.md` frontmatter |
| **Git remote URL** | `https://huggingface.co/spaces/jessicalundin/lean-cql-anc` | `git push` target |

Optional later:

| Item | When needed |
|------|-------------|
| **Org name** | If Space lives under `who-smart` / `my-lab` instead of personal user |
| **HF token in GitHub Secrets** | Only if you add a CI job that auto-pushes to the Space |
| **Secrets in Space settings** | Not needed for v0.1 (no VSAC, no API keys) |

---

## Compute: CPU vs GPU

You can change hardware anytime under **Space → Settings → Hardware**. For this demo, the choice is straightforward.

### Recommendation by version

| Version | Hardware | Why |
|---------|----------|-----|
| **v0.1** (Gradio + `cql-execution` + cached Lean JSON) | **CPU Basic** | All work is I/O and small JSON eval; no ML inference |
| **Polished public demo** | **CPU Upgrade** | Faster cold starts, more RAM, shorter Docker rebuilds |
| **v0.2** (+ Google CQL Go binary, more fixtures) | **CPU Upgrade** | Extra subprocess + larger image; still no GPU |
| **Future: LLM sidebar** (explain WHO rule in natural language) | **GPU** (e.g. T4 small) | Only if you add on-device or HF Inference API model calls |
| **Future: Lean proofs inside Space** | **CPU Upgrade** | Lean/`lake build` is CPU-bound; GPU does not accelerate proof search |

### What actually runs on the Space

| Component | Compute type |
|-----------|----------------|
| Gradio UI | CPU |
| Node `cql-execution` on FHIR fixtures | CPU (milliseconds per patient) |
| Google CQL (optional) | CPU |
| Precomputed `proof_status.json` / `lean_eval.json` | None (static files) |
| CQL→ELM translation (if ever live) | CPU (JVM); precompute in CI instead |

**GPU does not help** CQL engines, ELM evaluation, or Lean theorem proving in any meaningful way for this project.

### When upgrading CPU is worth it

- First visitor after sleep: **CPU Upgrade** wakes and serves faster than free Basic
- Docker image rebuild after each `git push`: more vCPUs shorten `uv sync` / `lake build` steps
- Demo day with a room full of people clicking scenarios: extra RAM avoids OOM if many concurrent subprocesses

### When GPU would make sense (later, optional)

Only if you extend the Space beyond clinical logic execution, for example:

- Hugging Face **Inference API** or local **transformers** model to paraphrase WHO ANC guidance
- RAG over WHO PDF / SMART ANC IG with embeddings
- AI-assisted CQL authoring assistant in the UI

None of that is in v0.1 scope. Start on **CPU Basic**, upgrade to **CPU Upgrade** if builds or cold starts feel slow, and reserve **GPU** for a deliberate ML add-on.

### Sleep and availability

| Tier | Behavior |
|------|----------|
| CPU Basic (free) | Space sleeps after inactivity; first load ~30–60s |
| CPU Upgrade (paid) | Stays warm longer; better for live presentations |
| GPU (paid) | Same sleep rules as other paid tiers; pay only if you use GPU workloads |

For a conference demo, temporarily switch to **CPU Upgrade** before the session, then scale back.

---

## What you do **not** need from Hugging Face

- GPU (unless you add an ML feature later)
- A separate "compiler" Space — CQL→ELM is precomputed in the repo
- Lean installed on HF — proofs run in GitHub Actions / locally; results are JSON artifacts
- VSAC or terminology API keys — value sets are bundled as static JSON

---

## Deploy steps (after Space exists)

Hugging Face may show a **FastAPI + requirements.txt** getting-started guide. **Ignore that** — this project uses **Gradio + uv + Lean** with its own `Dockerfile`.

### For `gatesfoundation/lean-cql-anc`

```bash
# 1. Clone the Space (use a Write token as the git password when prompted)
git clone https://huggingface.co/spaces/gatesfoundation/lean-cql-anc ~/hf-lean-cql-anc

# 2. Replace HF boilerplate with this repo (from your lean_cql_anc checkout)
cd /path/to/lean_cql_anc
./scripts/prepare-space.sh ~/hf-lean-cql-anc

# 3. Push — overwrites default FastAPI app.py, requirements.txt, Dockerfile
cd ~/hf-lean-cql-anc
git add .
git status   # should show app.py, Dockerfile, pyproject.toml, lean/, fixtures/, …
git commit -m "Lean + CQL WHO ANC demo (Gradio, uv, live Lean)"
git push
```

**Note:** After `prepare-space.sh`, the Space card is **`README.md` at the clone root** (not `space/README.md`). HF validates `short_description` in that file (max **60 characters**).

You do **not** need `hf download` for deployment — that only downloads files; `git clone` + `git push` is the normal workflow.

| HF starter file | What we use instead |
|-----------------|---------------------|
| `requirements.txt` + `pip` | `pyproject.toml` + `uv.lock` + `uv sync` |
| FastAPI `app.py` | Gradio `space/app.py` |
| Sample `Dockerfile` (Python 3.9, uvicorn) | `space/Dockerfile` (Python 3.11, uv, Lean, Node) |

HF builds the Docker image automatically. First build may take **10–20 minutes** (`uv sync` + `lake build`). Watch **Logs** on the Space page.

### Generic (any Space)

```bash
git clone https://huggingface.co/spaces/<USERNAME>/<SPACE_NAME> ~/hf-lean-cql-anc
/path/to/lean_cql_anc/scripts/prepare-space.sh ~/hf-lean-cql-anc
cd ~/hf-lean-cql-anc && git add . && git commit -m "Initial demo" && git push
```

---

## Alternative: sync from GitHub (optional)

If this repo is on GitHub, you can:

1. Keep `space/` in this monorepo as the source of truth
2. Add a GitHub Action that copies `space/` → HF Space on tag or manual dispatch
3. Store `HF_TOKEN` in GitHub repository secrets

That requires the same HF token + Space full ID — no extra HF configuration.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Build fails: `uv sync` | Ensure `pyproject.toml` and `uv.lock` are staged via `prepare-space.sh` |
| App sleeps (free tier) | Normal; first click wakes it (~30–60s) |
| Node not found | Confirm Space SDK is **Docker**, not plain Gradio |
| Google CQL column empty | Expected until you add the Go binary to Dockerfile (optional v0.2) |

---

## What to tell a collaborator

Share only:

- Public Space URL: `https://huggingface.co/spaces/<USERNAME>/<SPACE_NAME>`
- This repo link for Lean proofs and CQL source

Do **not** share: HF write token, git credentials.
