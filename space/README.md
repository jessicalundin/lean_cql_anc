---
title: Lean + CQL WHO ANC Demo
emoji: 🏥
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
pinned: false
license: apache-2.0
short_description: WHO ANC CQL decision logic with Lean safety proofs
---

# Lean + CQL for WHO Antenatal Care

Interactive demo: **Clinical Quality Language** decision rules for WHO SMART ANC, compared with **Lean** formal verification results (precomputed).

## Pipeline

```
WHO ANC guidance → FHIR fixtures → CQL → ELM → cql-execution (live) + Lean proofs (CI)
```

## Scope (prototype)

- Danger-sign → referral logic slice
- Synthetic FHIR patient fixtures
- Three-valued null handling demonstration

## Source

Full project: [jessicalundin/lean_cql_anc](https://github.com/jessicalundin/lean_cql_anc) — see `docs/huggingface-setup.md` for deployment.
