# Coding environment UX

How engineers work in **Cursor / VS Code** alongside the **Hugging Face Space** IDE-style demo.

---

## Local setup (Cursor or VS Code)

### 1. Open the repo

```bash
git clone <this-repo> lean_cql_anc
cd lean_cql_anc
cursor .   # or: code .
```

Install recommended extensions when prompted (`.vscode/extensions.json`):

| Extension | Purpose |
|-----------|---------|
| **Lean 4** (`leanprover.lean4`) | Goals, Infoview, go-to-definition, `lake build` integration |
| **YAML** | FHIR bundles (later) |
| **Python** | Gradio Space / `app.py` |
| **ESLint** | `space/scripts/eval_cql.js` |

### 2. Install Python deps with [uv](https://docs.astral.sh/uv/)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync                    # creates .venv from pyproject.toml + uv.lock
uv run python space/app.py # local Gradio demo on :7860
```

Or: `./scripts/run-space.sh`

### 3. Install Lean

```bash
curl -fsSL https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh | sh -s -- -y
elan default stable
cd lean && lake build
```

Or use **Dev Containers**: Command Palette → *Dev Containers: Reopen in Container* (`.devcontainer/devcontainer.json` runs `uv sync`, elan, and `lake build`).

### 4. IDE layout (recommended)

```
┌──────────────────────────────────────────────────────────────────┐
│ EXPLORER          │  lean/LeanCqlAnc/Proofs.lean    │ INFOVIEW  │
│ ├─ cql/           │  theorem danger_sign_implies... │ ▼ goals   │
│ ├─ lean/          │    rcases h with ...            │ Expected  │
│ ├─ fixtures/      │                                 │ type      │
│ └─ space/         ├─────────────────────────────────┤           │
│                   │  TERMINAL                         │           │
│                   │  $ lake build                     │           │
│                   │  $ anc-eval fixtures/...json      │           │
└──────────────────────────────────────────────────────────────────┘
```

**Split editors:** open `cql/DangerSigns.cql` left, `lean/LeanCqlAnc/DangerSigns.lean` right — same clinical rule, two formalisms.

### 5. Keyboard / command workflow

| Action | How |
|--------|-----|
| Sync Python env | Tasks → **uv: sync** |
| Run Gradio locally | Tasks → **uv: run Space (Gradio)** |
| Build proofs + `anc-eval` | `Cmd+Shift+B` → **Lean: lake build** (default build task) |
| Run evaluator on fixture | Tasks → **Lean: anc-eval (current fixture)** |
| Proof status JSON | Tasks → **Lean: proof status** |
| CQL prototype eval | Tasks → **CQL: eval prototype (Node)** |
| Step proof | Click theorem → Infoview → tactic buttons / goal view |

### 6. Files to know

| Path | Role |
|------|------|
| `cql/DangerSigns.cql` | Authoring view (WHO ANC danger signs) |
| `lean/LeanCqlAnc/DangerSigns.lean` | Executable semantics |
| `lean/LeanCqlAnc/Proofs.lean` | Safety theorems — edit here to see proof goals |
| `lean/AncEval/Main.lean` | CLI used by Space and terminal |
| `fixtures/patients/*.json` | Shared test patients |
| `space/app.py` | Public Gradio demo |
| `pyproject.toml` / `uv.lock` | Python deps (Gradio, huggingface-hub) |

### 7. Typical edit loop

1. Change disposition logic in `DangerSigns.lean`.
2. `lake build` — proofs fail if safety breaks (intentional gate).
3. Fix `Proofs.lean` or restore logic.
4. `anc-eval fixtures/patients/danger-sign-bleeding.json` — JSON result.
5. Compare with `node space/scripts/eval_cql.js fixtures/patients/...`.

---

## Hugging Face Space: IDE tab

The Space mirrors the coding environment for audiences without a local install:

| Local IDE | Space equivalent |
|-----------|------------------|
| File explorer | **File** dropdown (`DangerSigns.cql`, `Proofs.lean`, …) |
| Editor | **Code** panel (syntax-highlighted, read-only in v0.1) |
| Terminal | **Terminal** panel — live `anc-eval` output on Evaluate |
| Infoview / goals | **Proofs** tab — theorem status from `lake build` |
| Run/debug | **Evaluate** tab — patient scenario + engine comparison |

Open the Space → **Code workspace** tab to browse the same files as the repo.

---

## Parity: local ↔ Space

```bash
# Stage lean + fixtures + cql for HF push
./scripts/prepare-space.sh /path/to/hf-space-clone
cd /path/to/hf-space-clone && git push
```

The Docker image runs `lake build` at build time; the Space runs `anc-eval` **live** on each evaluation (same binary as local).

---

## Cursor-specific tips

- **@ workspace** — ask about `Proofs.lean` or CQL↔Lean alignment in chat.
- **Multi-root** — not needed; single repo root is enough.
- **Lean Infoview** — keep open while editing `Proofs.lean`; goals update on save after `lake build`.
