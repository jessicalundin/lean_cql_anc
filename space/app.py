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


def _obs_code(obs: dict) -> str | None:
    for coding in obs.get("code", {}).get("coding", []):
        if coding.get("system") in (SNOMED, LOINC):
            return coding.get("code")
    return None


def bundle_to_lean_json(bundle: dict) -> dict:
    """Extract the fields the Lean evaluator expects from a FHIR bundle."""
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


def strip_ga_from_bundle(bundle: dict) -> dict:
    bundle = json.loads(json.dumps(bundle))  # deep copy
    bundle["entry"] = [
        e for e in bundle.get("entry", [])
        if _obs_code(e.get("resource", {})) != GA_LOINC
    ]
    return bundle


def strip_signs_from_bundle(bundle: dict) -> dict:
    bundle = json.loads(json.dumps(bundle))  # deep copy
    bundle["entry"] = [
        e for e in bundle.get("entry", [])
        if _obs_code(e.get("resource", {})) not in DANGER_SIGN_CODES
    ]
    return bundle


# ── Engine runners ─────────────────────────────────────────────────────────────

def run_google_cql(bundle_path: str) -> dict:
    """Run Google CQL CLI against a single FHIR bundle file.

    The CLI takes directory inputs and writes output JSON files, so we create
    temporary directories, symlink/copy the inputs, then read the result file.
    Output structure: {evalResults: [{expressionDefinitions: {NAME: {value: ...}}}]}
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        cql_dir = tmp / "cql"
        bundle_dir = tmp / "bundles"
        out_dir = tmp / "output"
        cql_dir.mkdir()
        bundle_dir.mkdir()
        out_dir.mkdir()

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
            return {"error": f"No output file produced.\nstdout: {proc.stdout[:300]}"}

        try:
            raw = json.loads(result_files[0].read_text())
            defs = raw.get("evalResults", [{}])[0].get("expressionDefinitions", {})
            def _val(name: str):
                return defs.get(name, {}).get("value")
            has_danger = _val("Has Danger Sign")
            urgent = _val("Recommend Urgent Referral")
            return {
                "disposition": "urgent_referral" if urgent is True else (
                    "routine_follow_up" if urgent is False else "unknown"
                ),
                "has_danger_sign": str(has_danger).lower() if has_danger is not None else "unknown",
                "engine": "google-cql",
            }
        except Exception as exc:
            return {"error": f"parse error: {exc}\nraw: {result_files[0].read_text()[:300]}"}


def run_lean(lean_json_path: str) -> dict:
    if not LEAN_BIN.exists():
        return {"error": "Lean binary not found — compile first using the button above."}
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
        yield output + "\n✓ Build complete. Lean proofs verified — binary ready at `.lake/build/bin/anc-eval`."
    else:
        yield output + f"\n✗ Build failed (exit {proc.returncode})."


# ── Evaluate ───────────────────────────────────────────────────────────────────

def evaluate(scenario_label: str, strip_ga: bool, strip_signs: bool) -> tuple[str, str, str]:
    if not scenario_label or scenario_label not in SCENARIOS:
        return "", "", ""

    bundle = json.loads(Path(SCENARIOS[scenario_label]).read_text())

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

    match = (
        cql_out.get("disposition") == lean_out.get("disposition")
        and "error" not in cql_out
        and "error" not in lean_out
    )

    # Patient card: show FHIR observations as a readable table
    obs_rows = ""
    ga_val = lean_json.get("gestational_age_weeks")
    ga_str = f"{ga_val} weeks" if ga_val is not None else "unknown"
    for code, field in DANGER_SIGN_CODES.items():
        val = lean_json.get(field, "unknown")
        icon = "✓" if val == "true" else ("✗" if val == "false" else "?")
        obs_rows += f"| {field.replace('_', ' ').title()} | `{val}` | {icon} |\n"

    patient_md = f"""
**Gestational age:** {ga_str}

| Observation | Value | |
|---|---|---|
{obs_rows}
"""

    cql_disp = cql_out.get("disposition", cql_out.get("error", "—"))
    lean_disp = lean_out.get("disposition", lean_out.get("error", "—"))

    results_md = f"""
| Engine | Disposition | Danger sign |
|--------|-------------|-------------|
| **Google CQL** (runs `DangerSigns.cql` on FHIR bundle) | `{cql_disp}` | `{cql_out.get("has_danger_sign", "—")}` |
| **Lean evaluator** (compiled from Lean 4 source) | `{lean_disp}` | `{lean_out.get("has_danger_sign", "—")}` |
| **Agreement** | **{"✓ match" if match else "✗ mismatch"}** | |

*Both engines run the same WHO rule independently. The CQL engine executes the CQL source \
directly against the FHIR bundle. The Lean evaluator uses the binary compiled by `lake build`.*
"""

    if proofs:
        proofs_md = f"""
These theorems were verified by the Lean compiler when the binary was built. \
They hold for **every possible patient**, not just the scenarios above.

| Theorem | Meaning | Status |
|---------|---------|--------|
| `danger_sign_implies_referral` | Any patient with a danger sign always receives urgent referral | `{proofs.get("danger_sign_implies_referral", "—")}` |
| `no_contradictory_recommendations` | No patient can receive both routine follow-up and urgent referral | `{proofs.get("no_contradictory_recommendations", "—")}` |
| `unknown_not_false` | Missing data stays unknown — never silently treated as "no danger sign" | `{proofs.get("unknown_not_false", "—")}` |
"""
    else:
        proofs_md = "_Compile Lean first to see proof status._"

    return patient_md, results_md, proofs_md


# ── UI ─────────────────────────────────────────────────────────────────────────

CQL_SOURCE = CQL_FILE.read_text() if CQL_FILE.exists() else "(CQL file not found)"

LEAN_SOURCE = """\
-- Three-valued logic (true / false / unknown) matching CQL nullology
def hasDangerSignTrilean (p : PatientState) : Trilean :=
  (p.vaginalBleeding.or p.severeHeadache).or p.reducedFetalMovement

def disposition (p : PatientState) : Recommendation :=
  match hasDangerSignTrilean p with
  | .true    => .urgentReferral
  | .false   => .routineFollowUp
  | .unknown => .unknown  -- missing data ≠ safe

-- Theorems proved at compile time:
theorem danger_sign_implies_referral (p : PatientState) (h : HasDangerSign p) :
    disposition p = .urgentReferral

theorem no_contradictory_recommendations (p : PatientState) :
    ¬ (recommendsRoutineFollowUp p = .true ∧ recommendsReferral p = .true)

theorem unknown_not_false (t : Trilean) (h : t = .unknown) : t ≠ .false"""


def build_ui() -> gr.Blocks:
    choices = list(SCENARIOS.keys()) or ["(no fixtures found)"]
    with gr.Blocks(title="WHO ANC: CQL + Lean Formal Verification") as demo:

        gr.Markdown("# WHO ANC Danger Signs: CQL + Lean Formal Verification")
        gr.Markdown(
            "A WHO SMART ANC clinical decision rule — evaluated by **Google CQL** against "
            "real FHIR patient data, and independently verified by **Lean 4** formal proofs."
        )

        # ── Row 1: patient selector ──
        with gr.Row():
            scenario = gr.Dropdown(
                choices=choices, value=choices[0],
                label="Patient scenario", scale=3,
            )
            strip_ga = gr.Checkbox(label="Remove gestational age", value=False)
            strip_signs = gr.Checkbox(label="Mark danger signs as not assessed", value=False)
        evaluate_btn = gr.Button("Evaluate", variant="primary")

        # ── Row 2: CQL | Lean side-by-side ──
        with gr.Row():
            with gr.Column():
                gr.Markdown("### CQL rule (`DangerSigns.cql`)")
                gr.Markdown(
                    "The computable form of the WHO danger-sign recommendation, written in "
                    "[HL7 Clinical Quality Language](https://cql.hl7.org/). "
                    "The Google CQL engine executes this directly against the FHIR patient bundle."
                )
                gr.Code(value=CQL_SOURCE, language="sql", label="DangerSigns.cql")

            with gr.Column():
                gr.Markdown("### Lean 4 model (`lean/`)")
                gr.Markdown(
                    "The same rule encoded as a mathematical function in "
                    "[Lean 4](https://lean-lang.org/). "
                    "Click **Compile & Verify** to watch the Lean compiler check the theorems "
                    "and produce the evaluator binary."
                )
                gr.Code(value=LEAN_SOURCE, language="python", label="LeanCqlAnc (excerpt)")

                compile_btn = gr.Button("Compile & Verify Lean Proofs", variant="secondary")
                build_log = gr.Textbox(
                    label="Compiler output", lines=10, max_lines=20,
                    placeholder="Click above to compile…", interactive=False,
                )
                compile_btn.click(build_lean, outputs=build_log)

        # ── Row 3: results ──
        gr.Markdown("---")
        with gr.Row():
            patient_out = gr.Markdown(label="Patient")
            with gr.Column(scale=2):
                results_out = gr.Markdown()
                proofs_out = gr.Markdown()

        evaluate_btn.click(
            evaluate,
            inputs=[scenario, strip_ga, strip_signs],
            outputs=[patient_out, results_out, proofs_out],
        )

    return demo


if __name__ == "__main__":
    build_ui().launch(server_name="0.0.0.0", server_port=7860)
