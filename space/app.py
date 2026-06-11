"""Gradio demo: WHO ANC danger sign extraction → CQL + Lean formal verification."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import uuid
from datetime import date
from pathlib import Path
from typing import Generator

import anthropic
import gradio as gr

ROOT = Path(__file__).parent
FIXTURES = ROOT / "fixtures" / "patients"
CQL_FILE = ROOT / "cql" / "DangerSigns.cql"
LEAN_DIR = ROOT / "lean"
LEAN_BIN = LEAN_DIR / ".lake" / "build" / "bin" / "anc-eval"

ANC_SYSTEM = "http://fhir.org/guides/who/anc-cds/CodeSystem/anc-custom-codes"
LOINC = "http://loinc.org"

# All 12 ANCDT01 danger signs (ANC.B5 Quick Check)
DANGER_SIGNS = [
    {"code": "ANC.B5.DE3",  "display": "Bleeding vaginally"},
    {"code": "ANC.B5.DE4",  "display": "Central cyanosis"},
    {"code": "ANC.B5.DE5",  "display": "Convulsing"},
    {"code": "ANC.B5.DE6",  "display": "Fever"},
    {"code": "ANC.B5.DE7",  "display": "Severe headache"},
    {"code": "ANC.B5.DE8",  "display": "Visual disturbance"},
    {"code": "ANC.B5.DE9",  "display": "Imminent delivery"},
    {"code": "ANC.B5.DE10", "display": "Labour"},
    {"code": "ANC.B5.DE11", "display": "Looks very ill"},
    {"code": "ANC.B5.DE12", "display": "Severe vomiting"},
    {"code": "ANC.B5.DE13", "display": "Severe pain"},
    {"code": "ANC.B5.DE14", "display": "Severe abdominal pain"},
]
DANGER_SIGN_CODE_MAP = {s["code"]: s["display"] for s in DANGER_SIGNS}
GA_LOINC = "49051-6"

# Lean model checks these three signs (RFM is a Lean prototype extension, not in ANCDT01)
LEAN_SIGN_CODES = {
    "ANC.B5.DE3": "vaginal_bleeding",
    "ANC.B5.DE7": "severe_headache",
}

EXTRACTION_PROMPT = """You are a clinical coding assistant for antenatal care (ANC).

Review this clinical conversation and identify which WHO ANC danger signs (ANCDT01 / ANC.B5 Quick Check) are explicitly supported by the text.

Danger sign value set (system: {system}):
{sign_list}

Rules:
- Only include signs with clear textual evidence. Do NOT infer or assume.
- "central cyanosis" = blue discoloration of lips/skin.
- "severe pain" = ANC.B5.DE13; "severe abdominal pain" = ANC.B5.DE14 (distinct signs).
- Include gestational_age_weeks if mentioned (integer or null).
- Return ONLY valid JSON — no prose, no code fences.

Return JSON in this exact schema:
{{
  "gestational_age_weeks": <integer or null>,
  "danger_signs": [
    {{"code": "<ANC.B5.DExx>", "display": "<display>", "evidence": "<exact quote from text>"}}
  ]
}}

Conversation:
{conversation}"""

SCENARIOS: dict[str, str] = {}


def _load_scenarios() -> None:
    global SCENARIOS
    if not FIXTURES.exists():
        return
    for path in sorted(FIXTURES.glob("*.json")):
        bundle = json.loads(path.read_text())
        exts = bundle.get("extension", [])
        # Only include fixtures that have a HealthBench conversation
        if not any("healthbench-conversation" in e.get("url", "") for e in exts):
            continue
        summary = next(
            (e["valueString"] for e in exts if "clinical-summary" in e.get("url", "")),
            path.stem,
        )
        SCENARIOS[summary] = str(path)


_load_scenarios()

# ── FHIR helpers ──────────────────────────────────────────────────────────────

def _observations(bundle: dict) -> list[dict]:
    return [
        e["resource"] for e in bundle.get("entry", [])
        if e.get("resource", {}).get("resourceType") == "Observation"
    ]


def _obs_code(resource: dict) -> str | None:
    for coding in resource.get("code", {}).get("coding", []):
        if coding.get("system") in (ANC_SYSTEM, LOINC):
            return coding.get("code")
    return None


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


def _conversation_text(turns: list[dict]) -> str:
    lines = []
    for t in turns:
        lines.append(f"{t.get('role', 'unknown').capitalize()}: {t.get('content', '').strip()}")
    return "\n".join(lines)


# ── LLM extraction ────────────────────────────────────────────────────────────

def extract_danger_signs(conversation_text: str) -> dict:
    """Call Claude to extract ANCDT01 danger signs. Returns extraction dict."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {"error": "ANTHROPIC_API_KEY not set", "gestational_age_weeks": None, "danger_signs": []}

    sign_list = "\n".join(f"  - {s['code']}  {s['display']}" for s in DANGER_SIGNS)
    prompt = EXTRACTION_PROMPT.format(
        system=ANC_SYSTEM,
        sign_list=sign_list,
        conversation=conversation_text,
    )
    try:
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        return json.loads(message.content[0].text.strip())
    except Exception as exc:
        return {"error": str(exc), "gestational_age_weeks": None, "danger_signs": []}


def extraction_to_fhir_bundle(extraction: dict, patient_id: str | None = None) -> dict:
    """Convert Claude extraction result to FHIR R4 Bundle."""
    pid = patient_id or f"patient-{uuid.uuid4().hex[:8]}"
    today = date.today().isoformat()

    entries: list[dict] = [
        {"resource": {"resourceType": "Patient", "id": pid, "gender": "female"}}
    ]

    ga = extraction.get("gestational_age_weeks")
    if ga is not None:
        entries.append({"resource": {
            "resourceType": "Observation",
            "id": f"ga-{pid}",
            "status": "final",
            "code": {"coding": [{"system": LOINC, "code": GA_LOINC, "display": "Gestational age in weeks"}]},
            "subject": {"reference": f"Patient/{pid}"},
            "effectiveDateTime": today,
            "valueQuantity": {"value": int(ga), "unit": "wk", "system": "http://unitsofmeasure.org", "code": "wk"},
        }})

    detected = {s["code"] for s in extraction.get("danger_signs", [])}
    evidence_map = {s["code"]: s.get("evidence", "") for s in extraction.get("danger_signs", [])}

    for sign in DANGER_SIGNS:
        present = sign["code"] in detected
        obs: dict = {
            "resourceType": "Observation",
            "id": f"sign-{sign['code'].lower().replace('.', '-')}-{pid}",
            "status": "final",
            "code": {"coding": [{"system": ANC_SYSTEM, "code": sign["code"], "display": sign["display"]}]},
            "subject": {"reference": f"Patient/{pid}"},
            "effectiveDateTime": today,
            "valueBoolean": present,
        }
        if present and evidence_map.get(sign["code"]):
            obs["note"] = [{"text": evidence_map[sign["code"]]}]
        entries.append({"resource": obs})

    return {"resourceType": "Bundle", "id": f"extracted-{pid}", "type": "collection", "entry": entries}


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
        if obs.get("status") not in ("final", "amended", "corrected"):
            continue
        code = _obs_code(obs)
        if code == GA_LOINC:
            result["gestational_age_weeks"] = obs.get("valueQuantity", {}).get("value")
        elif code in LEAN_SIGN_CODES:
            field = LEAN_SIGN_CODES[code]
            val = obs.get("valueBoolean")
            result[field] = "true" if val is True else ("false" if val is False else "unknown")
    return result


# ── Engine runners ────────────────────────────────────────────────────────────

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
            active_signs = [
                s["display"] for s in DANGER_SIGNS
                if _val(f"Has {s['display']}") is True
                or (s["code"] == "ANC.B5.DE5" and _val("Is Convulsing") is True)
                or (s["code"] == "ANC.B5.DE9" and _val("Imminent Delivery Indicated") is True)
                or (s["code"] == "ANC.B5.DE10" and _val("In Labour") is True)
                or (s["code"] == "ANC.B5.DE11" and _val("Looks Very Ill") is True)
            ]
            return {
                "disposition": "urgent_referral" if urgent is True else (
                    "routine_follow_up" if urgent is False else "unknown"
                ),
                "has_danger_sign": str(_val("Has Danger Sign")).lower() if _val("Has Danger Sign") is not None else "unknown",
                "active_signs": active_signs,
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


# ── Build ─────────────────────────────────────────────────────────────────────

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


# ── Markdown helpers ──────────────────────────────────────────────────────────

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


def _extraction_md(extraction: dict) -> str:
    if "error" in extraction:
        return f"**Extraction error:** `{extraction['error']}`"

    ga = extraction.get("gestational_age_weeks")
    ga_str = f"{ga} weeks" if ga is not None else "not mentioned"

    detected = {s["code"] for s in extraction.get("danger_signs", [])}
    evidence_map = {s["code"]: s.get("evidence", "") for s in extraction.get("danger_signs", [])}

    rows = ""
    for sign in DANGER_SIGNS:
        code = sign["code"]
        display = sign["display"]
        if code in detected:
            ev = evidence_map.get(code, "")
            icon, note = "🔴", f"**present** — _{ev}_" if ev else "**present**"
        else:
            icon, note = "–", "not detected in conversation"
        rows += f"| {icon} | `{code}` | **{display}** | {note} |\n"

    return f"""**Gestational age:** {ga_str}

| | Code | Sign | Extracted from conversation |
|---|---|---|---|
{rows}"""


def _fhir_extraction_note(extraction: dict) -> str:
    detected_count = len(extraction.get("danger_signs", []))
    total = len(DANGER_SIGNS)
    return (
        f"_Claude extracted {detected_count} of {total} danger signs from the conversation. "
        f"Each sign became one FHIR R4 Observation coded with the WHO ANC custom code system "
        f"(`anc-custom-codes`). Signs not mentioned are recorded as `valueBoolean: false`._"
    )


def _results_md(cql_out: dict, lean_out: dict) -> str:
    match = (
        cql_out.get("disposition") == lean_out.get("disposition")
        and "error" not in cql_out
        and "error" not in lean_out
    )
    cql_disp = cql_out.get("disposition", cql_out.get("error", "—"))
    lean_disp = lean_out.get("disposition", lean_out.get("error", "—"))
    agree = "**✓ match**" if match else "**✗ mismatch**"

    active = cql_out.get("active_signs", [])
    signs_str = ", ".join(f"`{s}`" for s in active) if active else "_none_"

    return f"""
| Engine | Disposition | Danger sign |
|--------|-------------|-------------|
| **Google CQL** | `{cql_disp}` | `{cql_out.get("has_danger_sign", "—")}` |
| **Lean evaluator** | `{lean_disp}` | `{lean_out.get("has_danger_sign", "—")}` |
| Agreement | {agree} | |

**Active danger signs (CQL):** {signs_str}
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


# ── Evaluate ──────────────────────────────────────────────────────────────────

def evaluate_fixture(
    scenario_label: str, strip_ga: bool, strip_signs: bool
) -> tuple[str, str, str, str, str, str]:
    if not scenario_label or scenario_label not in SCENARIOS:
        return "", "", "", "", "", ""

    bundle = json.loads(Path(SCENARIOS[scenario_label]).read_text())
    conversation = get_conversation(bundle)

    # Strip modifiers
    if strip_ga:
        bundle = {**bundle, "entry": [
            e for e in bundle.get("entry", [])
            if _obs_code(e.get("resource", {})) != GA_LOINC
        ]}
    if strip_signs:
        bundle = {**bundle, "entry": [
            e for e in bundle.get("entry", [])
            if _obs_code(e.get("resource", {})) not in DANGER_SIGN_CODE_MAP
        ]}

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

    # Build an extraction-like dict from the FHIR bundle for display
    detected_signs = []
    for obs in _observations(bundle):
        code = _obs_code(obs)
        if code in DANGER_SIGN_CODE_MAP and obs.get("valueBoolean") is True:
            note = next((n["text"] for n in obs.get("note", [])), "")
            detected_signs.append({"code": code, "display": DANGER_SIGN_CODE_MAP[code], "evidence": note})
    extraction_display = {
        "gestational_age_weeks": lean_json.get("gestational_age_weeks"),
        "danger_signs": detected_signs,
    }

    conv_md = _conversation_md(conversation) if conversation else "_No conversation in this fixture._"
    extraction_md = _extraction_md(extraction_display)
    fhir_note = _fhir_extraction_note(extraction_display)
    results_md = _results_md(cql_out, lean_out)
    proofs_md = _proofs_md(proofs)

    return conv_md, extraction_md, fhir_note, results_md, proofs_md, json.dumps(bundle, indent=2)


def evaluate_live(conversation_text: str) -> tuple[str, str, str, str, str]:
    """Run full pipeline on free-text conversation via LLM extraction."""
    if not conversation_text.strip():
        return "", "", "", "", ""

    extraction = extract_danger_signs(conversation_text)
    if "error" in extraction and not extraction.get("danger_signs"):
        err = extraction["error"]
        return "", f"**Extraction error:** `{err}`", "", "", ""

    bundle = extraction_to_fhir_bundle(extraction)

    tmp_dir = ROOT / "artifacts"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    bundle_tmp = tmp_dir / "_tmp_live_bundle.json"
    bundle_tmp.write_text(json.dumps(bundle, indent=2))

    lean_json = bundle_to_lean_json(bundle)
    lean_tmp = tmp_dir / "_tmp_live_lean.json"
    lean_tmp.write_text(json.dumps(lean_json, indent=2))

    cql_out = run_google_cql(str(bundle_tmp))
    lean_out = run_lean(str(lean_tmp))
    proofs = lean_proof_status()

    extraction_md = _extraction_md(extraction)
    fhir_note = _fhir_extraction_note(extraction)
    results_md = _results_md(cql_out, lean_out)
    proofs_md = _proofs_md(proofs)

    return extraction_md, fhir_note, results_md, proofs_md, json.dumps(bundle, indent=2)


# ── Static source content ─────────────────────────────────────────────────────

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


# ── UI ────────────────────────────────────────────────────────────────────────

def build_ui() -> gr.Blocks:
    choices = list(SCENARIOS.keys()) or ["(no fixtures found)"]

    with gr.Blocks(title="WHO ANC: CQL + Lean") as demo:

        gr.Markdown("# WHO ANC Danger Signs: CQL + Lean Formal Verification")
        gr.Markdown(
            "Two modes: **Fixture** runs pre-built FHIR patient bundles through the pipeline. "
            "**Live** lets you paste any clinical conversation — Claude extracts ANCDT01 danger signs, "
            "constructs a FHIR bundle, and evaluates it with Google CQL and Lean 4."
        )

        with gr.Tabs():

            # ── Tab 1: Fixture scenarios ──
            with gr.TabItem("Fixture scenarios"):
                gr.Markdown(
                    "Select a pre-built patient scenario to trace the pipeline: "
                    "**FHIR bundle → Google CQL evaluation → Lean formal proof.**"
                )
                with gr.Row():
                    scenario = gr.Dropdown(
                        choices=choices, value=choices[0],
                        label="Patient scenario", scale=3,
                    )
                    strip_ga = gr.Checkbox(label="Remove gestational age", value=False)
                    strip_signs = gr.Checkbox(label="Mark all signs as not assessed", value=False)
                evaluate_btn = gr.Button("Evaluate", variant="primary")

                with gr.Accordion("Clinical conversation (HealthBench source)", open=True):
                    conv_out = gr.Markdown(value="_Select a HealthBench scenario and click Evaluate._")

                with gr.Accordion("Step 1 — Danger signs in FHIR (WHO ANC codes)", open=True):
                    gr.Markdown(
                        "Danger signs encoded as FHIR R4 Observations using the "
                        "[WHO ANC custom code system](https://build.fhir.org/ig/WorldHealthOrganization/smart-anc/CodeSystem-anc-custom-codes.html) "
                        "(ANC.B5.DE3–DE14) per ANCDT01."
                    )
                    extraction_f_out = gr.Markdown()
                    fhir_note_f_out = gr.Markdown()

                with gr.Accordion("Step 2 — The clinical rule (CQL + Lean)", open=False):
                    with gr.Row():
                        with gr.Column():
                            gr.Markdown("### CQL rule (`DangerSigns.cql`)")
                            gr.Markdown(
                                "Executed by **Google CQL** against the FHIR bundle. "
                                "Implements ANCDT01 using WHO `anc-custom-codes` (ANC.B5.DE3–DE14). "
                                "The authoritative WHO [ANCDT01.cql](https://build.fhir.org/ig/WorldHealthOrganization/smart-anc/Library-ANCDT01.html) "
                                "uses the same codes via a multi-select Observation pattern; "
                                "this prototype uses individual boolean Observations for standalone engine compatibility."
                            )
                            gr.Code(value=CQL_SOURCE, language="sql", label="DangerSigns.cql")
                        with gr.Column():
                            gr.Markdown("### Lean 4 model (`lean/`)")
                            gr.Markdown(
                                "The same danger-sign rule in [Lean 4](https://lean-lang.org/). "
                                "Unlike CQL (which evaluates test cases), Lean **proves** the rule holds "
                                "for *every possible patient* at compile time. "
                                "Click **Compile** to verify the proofs."
                            )
                            gr.Code(value=LEAN_SOURCE, language="python", label="LeanCqlAnc (excerpt)")
                            compile_btn = gr.Button("Compile & Verify Lean Proofs", variant="secondary")
                            build_log = gr.Textbox(
                                label="Compiler output", lines=8, max_lines=20,
                                placeholder="Click above to compile…", interactive=False,
                            )
                            compile_btn.click(build_lean, outputs=build_log)

                gr.Markdown("## Step 3 — Evaluation results")
                with gr.Row():
                    with gr.Column(scale=1):
                        gr.Markdown("### FHIR bundle (sent to both engines)")
                        fhir_f_out = gr.Code(language="json", label="Patient JSON")
                    with gr.Column(scale=2):
                        gr.Markdown("### Engine agreement")
                        results_f_out = gr.Markdown()
                        gr.Markdown("### Lean proofs")
                        proofs_f_out = gr.Markdown(value="_Compile Lean first to see proof status._")

                evaluate_btn.click(
                    evaluate_fixture,
                    inputs=[scenario, strip_ga, strip_signs],
                    outputs=[conv_out, extraction_f_out, fhir_note_f_out, results_f_out, proofs_f_out, fhir_f_out],
                )

            # ── Tab 2: Live LLM extraction ──
            with gr.TabItem("Live extraction (Claude API)"):
                gr.Markdown(
                    "Paste any clinical conversation. Claude identifies ANCDT01 danger signs, "
                    "constructs a FHIR R4 bundle with WHO ANC codes, then evaluates with Google CQL + Lean."
                )
                conv_input = gr.Textbox(
                    label="Clinical conversation",
                    placeholder="Patient: I've had a severe headache since yesterday, I'm 34 weeks pregnant...\nClinician: ...",
                    lines=8,
                )
                extract_btn = gr.Button("Extract → FHIR → Evaluate", variant="primary")

                with gr.Accordion("Step 1 — Extracted danger signs (WHO ANC codes)", open=True):
                    extraction_l_out = gr.Markdown()
                    fhir_note_l_out = gr.Markdown()

                gr.Markdown("## Step 2 — Evaluation results")
                with gr.Row():
                    with gr.Column(scale=1):
                        gr.Markdown("### Constructed FHIR bundle")
                        fhir_l_out = gr.Code(language="json", label="Generated Patient JSON")
                    with gr.Column(scale=2):
                        gr.Markdown("### Engine agreement")
                        results_l_out = gr.Markdown()
                        gr.Markdown("### Lean proofs")
                        proofs_l_out = gr.Markdown(value="_Compile Lean (Fixture tab) first to see proof status._")

                extract_btn.click(
                    evaluate_live,
                    inputs=[conv_input],
                    outputs=[extraction_l_out, fhir_note_l_out, results_l_out, proofs_l_out, fhir_l_out],
                )

    return demo


if __name__ == "__main__":
    build_ui().launch(server_name="0.0.0.0", server_port=7860)
