"""Gradio demo: Google CQL + Lean 4 evaluation on WHO ANC danger-sign FHIR fixtures."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Generator

import gradio as gr

ROOT = Path(__file__).parent
FIXTURES = ROOT / "fixtures" / "patients"
CQL_FILE = ROOT / "cql" / "DangerSigns.cql"
LEAN_DIR = ROOT / "lean"
LEAN_BIN = LEAN_DIR / ".lake" / "build" / "bin" / "anc-eval"

SNOMED = "http://snomed.info/sct"
LOINC = "http://loinc.org"
DANGER_SIGN_CODES = {
    "289530006": "vaginal_bleeding",
    "25064002":  "severe_headache",
    "386281004": "reduced_fetal_movement",
}
DANGER_SIGN_LABELS = {
    "vaginal_bleeding":       "Vaginal bleeding",
    "severe_headache":        "Severe headache",
    "reduced_fetal_movement": "Reduced fetal movement",
}
GA_LOINC = "49051-6"

SCENARIOS: dict[str, str] = {}


def _load_scenarios() -> None:
    global SCENARIOS
    if not FIXTURES.exists():
        return
    for path in sorted(FIXTURES.glob("*.json")):
        bundle = json.loads(path.read_text())
        summary = next(
            (e["valueString"] for e in bundle.get("extension", [])
             if "clinical-summary" in e.get("url", "")),
            path.stem,
        )
        SCENARIOS[summary] = str(path)


_load_scenarios()

# ── FHIR bundle helpers ────────────────────────────────────────────────────────

def _observations(bundle: dict) -> list[dict]:
    return [
        e["resource"] for e in bundle.get("entry", [])
        if e.get("resource", {}).get("resourceType") == "Observation"
    ]


def _obs_code(resource: dict) -> str | None:
    for coding in resource.get("code", {}).get("coding", []):
        if coding.get("system") in (SNOMED, LOINC):
            return coding.get("code")
    return None


def bundle_to_lean_json(bundle: dict) -> dict:
    patient = next(
        (e["resource"] for e in bundle.get("entry", [])
         if e.get("resource", {}).get("resourceType") == "Patient"),
        {}
    )
    result: dict = {
        "id": patient.get("id", bundle.get("id", "unknown")),
        "gestational_age_weeks": None,
        "vaginal_bleeding": "unknown",
        "severe_headache": "unknown",
        "reduced_fetal_movement": "unknown",
    }
    for obs in _observations(bundle):
        if obs.get("status") not in ("final", "amended"):
            continue
        code = _obs_code(obs)
        if code == GA_LOINC:
            result["gestational_age_weeks"] = obs.get("valueQuantity", {}).get("value")
        elif code in DANGER_SIGN_CODES:
            field = DANGER_SIGN_CODES[code]
            val = obs.get("valueBoolean")
            result[field] = "true" if val is True else ("false" if val is False else "unknown")
    return result


def get_conversation(bundle: dict) -> list[dict] | None:
    raw = next(
        (e["valueString"] for e in bundle.get("extension", [])
         if "healthbench-conversation" in e.get("url", "")),
        None,
    )
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def strip_ga_from_bundle(bundle: dict) -> dict:
    bundle = json.loads(json.dumps(bundle))
    bundle["entry"] = [
        e for e in bundle.get("entry", [])
        if _obs_code(e.get("resource", {})) != GA_LOINC
    ]
    return bundle


def strip_signs_from_bundle(bundle: dict) -> dict:
    bundle = json.loads(json.dumps(bundle))
    bundle["entry"] = [
        e for e in bundle.get("entry", [])
        if _obs_code(e.get("resource", {})) not in DANGER_SIGN_CODES
    ]
    return bundle


# ── Engine runners ─────────────────────────────────────────────────────────────

def run_google_cql(bundle_path: str) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        cql_dir = tmp / "cql"
        bundle_dir = tmp / "bundles"
        out_dir = tmp / "output"
        for d in (cql_dir, bundle_dir, out_dir):
            d.mkdir()

        shutil.copy(CQL_FILE, cql_dir / CQL_FILE.name)
        shutil.copy(bundle_path, bundle_dir / "patient.json")

        proc = subprocess.run(
            [
                "google-cql",
                f"--cql_dir={cql_dir}",
                f"--fhir_bundle_dir={bundle_dir}",
                f"--json_output_dir={out_dir}",
            ],
            capture_output=True, text=True, timeout=60,
        )
        if proc.returncode != 0:
            return {"error": proc.stderr or proc.stdout}

        result_files = list(out_dir.glob("*.json"))
        if not result_files:
            return {"error": f"No output produced.\n{proc.stdout[:300]}"}

        try:
            raw = json.loads(result_files[0].read_text())
            defs = raw.get("evalResults", [{}])[0].get("expressionDefinitions", {})
            def _val(name: str):
                return defs.get(name, {}).get("value")
            urgent = _val("Recommend Urgent Referral")
            has_danger = _val("Has Danger Sign")
            return {
                "disposition": "urgent_referral" if urgent is True else (
                    "routine_follow_up" if urgent is False else "unknown"
                ),
                "has_danger_sign": str(has_danger).lower() if has_danger is not None else "unknown",
                "engine": "google-cql",
            }
        except Exception as exc:
            return {"error": f"parse error: {exc}"}


def run_lean(lean_json_path: str) -> dict:
    if not LEAN_BIN.exists():
        return {"error": "Lean binary not found — compile first."}
    proc = subprocess.run(
        [str(LEAN_BIN), lean_json_path],
        capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0:
        return {"error": proc.stderr or proc.stdout}
    return json.loads(proc.stdout)


def lean_proof_status() -> dict:
    if not LEAN_BIN.exists():
        return {}
    proc = subprocess.run(
        [str(LEAN_BIN), "--proof-status"],
        capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0:
        return {}
    return json.loads(proc.stdout)


# ── Build ──────────────────────────────────────────────────────────────────────

def build_lean() -> Generator[str, None, None]:
    yield "Starting `lake build` — compiling Lean source and verifying proofs...\n\n"
    proc = subprocess.Popen(
        ["lake", "build"],
        cwd=str(LEAN_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    output = ""
    for line in proc.stdout:
        output += line
        yield output
    proc.wait()
    if proc.returncode == 0:
        yield output + "\n✓ Build complete. Proofs verified — binary ready."
    else:
        yield output + f"\n✗ Build failed (exit {proc.returncode})."


# ── Markdown helpers ───────────────────────────────────────────────────────────

def _conversation_md(turns: list[dict]) -> str:
    lines = []
    for t in turns:
        role = t.get("role", "")
        content = t.get("content", "").strip()
        if role == "user":
            lines.append(f"**Patient/Clinician:** {content}")
        else:
            lines.append(f"**Assistant:** {content}")
        lines.append("")
    return "\n".join(lines)


def _extraction_md(lean_json: dict) -> str:
    ga = lean_json.get("gestational_age_weeks")
    ga_str = f"{ga} weeks" if ga is not None else "not recorded"

    rows = ""
    for field, label in DANGER_SIGN_LABELS.items():
        val = lean_json.get(field, "unknown")
        if val == "true":
            icon, note = "🔴", "**present** → Observation recorded as `true`"
        elif val == "false":
            icon, note = "✓", "absent → Observation recorded as `false`"
        else:
            icon, note = "❓", "not assessed → no Observation in bundle"
        rows += f"| {icon} | **{label}** | {note} |\n"

    return f"""
**Gestational age:** {ga_str}

| | Sign | Extracted to FHIR |
|---|---|---|
{rows}
"""


def _results_md(cql_out: dict, lean_out: dict) -> str:
    match = (
        cql_out.get("disposition") == lean_out.get("disposition")
        and "error" not in cql_out
        and "error" not in lean_out
    )
    cql_disp = cql_out.get("disposition", cql_out.get("error", "—"))
    lean_disp = lean_out.get("disposition", lean_out.get("error", "—"))
    agree = "**✓ match**" if match else "**✗ mismatch**"

    return f"""
| Engine | Disposition | Danger sign |
|--------|-------------|-------------|
| **Google CQL** | `{cql_disp}` | `{cql_out.get("has_danger_sign", "—")}` |
| **Lean evaluator** | `{lean_disp}` | `{lean_out.get("has_danger_sign", "—")}` |
| Agreement | {agree} | |
"""


def _proofs_md(proofs: dict) -> str:
    if not proofs:
        return "_Compile Lean first to see proof status._"
    return f"""
| Theorem | Meaning | Status |
|---------|---------|--------|
| `danger_sign_implies_referral` | Danger sign always → urgent referral | `{proofs.get("danger_sign_implies_referral", "—")}` |
| `no_contradictory_recommendations` | Cannot receive both routine and urgent | `{proofs.get("no_contradictory_recommendations", "—")}` |
| `unknown_not_false` | Missing data ≠ "no danger sign" | `{proofs.get("unknown_not_false", "—")}` |
"""


# ── Evaluate ───────────────────────────────────────────────────────────────────

def evaluate(
    scenario_label: str, strip_ga: bool, strip_signs: bool
) -> tuple[str, str, str, str, str]:
    if not scenario_label or scenario_label not in SCENARIOS:
        return "", "", "", "", ""

    bundle = json.loads(Path(SCENARIOS[scenario_label]).read_text())
    conversation = get_conversation(bundle)

    if strip_ga:
        bundle = strip_ga_from_bundle(bundle)
    if strip_signs:
        bundle = strip_signs_from_bundle(bundle)

    tmp_dir = ROOT / "artifacts"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    bundle_tmp = tmp_dir / "_tmp_bundle.json"
    bundle_tmp.write_text(json.dumps(bundle, indent=2))

    lean_json = bundle_to_lean_json(bundle)
    lean_tmp = tmp_dir / "_tmp_lean.json"
    lean_tmp.write_text(json.dumps(lean_json, indent=2))

    cql_out = run_google_cql(str(bundle_tmp))
    lean_out = run_lean(str(lean_tmp))
    proofs = lean_proof_status()

    conv_md = _conversation_md(conversation) if conversation else ""
    extraction_md = _extraction_md(lean_json)
    results_md = _results_md(cql_out, lean_out)
    proofs_md = _proofs_md(proofs)

    return conv_md, extraction_md, results_md, proofs_md, json.dumps(lean_json, indent=2)


# ── Static source content ──────────────────────────────────────────────────────

CQL_SOURCE = CQL_FILE.read_text() if CQL_FILE.exists() else "(CQL file not found)"

LEAN_SOURCE = """\
-- Three-valued logic: true / false / unknown
def hasDangerSignTrilean (p : PatientState) : Trilean :=
  (p.vaginalBleeding.or p.severeHeadache).or p.reducedFetalMovement

def disposition (p : PatientState) : Recommendation :=
  match hasDangerSignTrilean p with
  | .true    => .urgentReferral
  | .false   => .routineFollowUp
  | .unknown => .unknown  -- missing data ≠ safe

-- Proved at compile time for ALL possible patients:
theorem danger_sign_implies_referral (p : PatientState)
    (h : HasDangerSign p) : disposition p = .urgentReferral

theorem no_contradictory_recommendations (p : PatientState) :
    ¬ (recommendsRoutineFollowUp p = .true
       ∧ recommendsReferral p = .true)

theorem unknown_not_false (t : Trilean)
    (h : t = .unknown) : t ≠ .false"""


# ── UI ─────────────────────────────────────────────────────────────────────────

def build_ui() -> gr.Blocks:
    choices = list(SCENARIOS.keys()) or ["(no fixtures found)"]

    with gr.Blocks(title="WHO ANC: CQL + Lean") as demo:

        gr.Markdown("# WHO ANC Danger Signs: CQL + Lean Formal Verification")
        gr.Markdown(
            "Select a patient scenario to trace the full pipeline: "
            "**clinical conversation → FHIR extraction → Google CQL evaluation → Lean formal proof.**"
        )

        # ── Patient selector ──
        with gr.Row():
            scenario = gr.Dropdown(
                choices=choices, value=choices[0],
                label="Patient scenario", scale=3,
            )
            strip_ga = gr.Checkbox(label="Remove gestational age", value=False)
            strip_signs = gr.Checkbox(label="Mark danger signs as not assessed", value=False)
        evaluate_btn = gr.Button("Evaluate", variant="primary")

        # ── Conversation (visible only for HealthBench fixtures) ──
        with gr.Accordion("Clinical conversation (HealthBench source)", open=True):
            conv_out = gr.Markdown(
                value="_Select a HealthBench scenario and click Evaluate to see the source conversation._"
            )

        # ── Extraction ──
        with gr.Accordion("Step 1 — Extracted to FHIR", open=True):
            gr.Markdown(
                "Danger signs and gestational age identified from the conversation "
                "and encoded as FHIR R4 Observations (SNOMED-CT codes)."
            )
            extraction_out = gr.Markdown()

        # ── CQL | Lean side-by-side ──
        with gr.Accordion("Step 2 — The clinical rule (CQL + Lean)", open=False):
            with gr.Row():
                with gr.Column():
                    gr.Markdown("### CQL rule (`DangerSigns.cql`)")
                    gr.Markdown(
                        "Executed by **Google CQL** directly against the FHIR bundle above. "
                        "This is the authoritative HL7 representation of the WHO danger-sign rule."
                    )
                    gr.Code(value=CQL_SOURCE, language="sql", label="DangerSigns.cql")
                with gr.Column():
                    gr.Markdown("### Lean 4 model (`lean/`)")
                    gr.Markdown(
                        "The same rule in [Lean 4](https://lean-lang.org/). "
                        "Click **Compile** to watch the Lean compiler verify the safety theorems "
                        "and produce the evaluator binary."
                    )
                    gr.Code(value=LEAN_SOURCE, language="python", label="LeanCqlAnc (excerpt)")
                    compile_btn = gr.Button("Compile & Verify Lean Proofs", variant="secondary")
                    build_log = gr.Textbox(
                        label="Compiler output", lines=8, max_lines=20,
                        placeholder="Click above to compile…", interactive=False,
                    )
                    compile_btn.click(build_lean, outputs=build_log)

        # ── Results ──
        gr.Markdown("## Step 3 — Evaluation results")
        with gr.Row():
            with gr.Column(scale=1):
                gr.Markdown("### FHIR bundle (sent to both engines)")
                fhir_out = gr.Code(language="json", label="Patient JSON")
            with gr.Column(scale=2):
                gr.Markdown("### Engine agreement")
                results_out = gr.Markdown()
                gr.Markdown("### Lean proofs")
                proofs_out = gr.Markdown(
                    value="_Compile Lean first to see proof status._"
                )

        evaluate_btn.click(
            evaluate,
            inputs=[scenario, strip_ga, strip_signs],
            outputs=[conv_out, extraction_out, results_out, proofs_out, fhir_out],
        )

    return demo


if __name__ == "__main__":
    build_ui().launch(server_name="0.0.0.0", server_port=7860)
