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
VENDOR_CQL_DIR = ROOT / "vendor" / "smart-anc" / "input" / "cql"
LEAN_DIR = ROOT / "lean"
LEAN_BIN = LEAN_DIR / ".lake" / "build" / "bin" / "anc-eval"

# WHO SMART ANC code system (master branch)
ANC_SYSTEM = "http://smart.who.int/anc/CodeSystem/anc-custom-codes"
LOINC = "http://loinc.org"

DANGER_SIGN_OBS_CODE = "ANC.B5.DE48"   # Observation.code: "Danger signs"
NO_DANGER_SIGNS_CODE = "ANC.B5.DE49"   # Observation.value: "No danger signs"
GA_LOINC = "49051-6"

# ANCDT01 danger sign value codes — valueset anc-b5-de50
DANGER_SIGNS = [
    {"code": "ANC.B5.DE50", "display": "Bleeding vaginally"},
    {"code": "ANC.B5.DE51", "display": "Central cyanosis"},
    {"code": "ANC.B5.DE52", "display": "Convulsing"},
    {"code": "ANC.B5.DE53", "display": "Fever"},
    {"code": "ANC.B5.DE54", "display": "Imminent delivery"},
    {"code": "ANC.B5.DE55", "display": "Labour"},
    {"code": "ANC.B5.DE56", "display": "Looks very ill"},
    {"code": "ANC.B5.DE57", "display": "Severe headache"},
    {"code": "ANC.B5.DE58", "display": "Severe pain"},
    {"code": "ANC.B5.DE59", "display": "Severe vomiting"},
    {"code": "ANC.B5.DE60", "display": "Severe abdominal pain"},
    {"code": "ANC.B5.DE61", "display": "Unconscious"},
    {"code": "ANC.B5.DE62", "display": "Visual disturbance"},
]
DANGER_SIGN_VALUE_CODES = {s["code"] for s in DANGER_SIGNS}
DANGER_SIGN_DISPLAY = {s["code"]: s["display"] for s in DANGER_SIGNS}

EXTRACTION_PROMPT = """You are a clinical coding assistant for antenatal care (ANC).

Review this clinical conversation and identify which WHO ANC danger signs (ANCDT01 / ANC.B5 Quick Check) are explicitly supported by the text.

Danger sign value set (system: {system}):
{sign_list}

Rules:
- Only include signs with clear textual evidence. Do NOT infer or assume.
- "central cyanosis" = blue discoloration of lips/skin.
- "severe pain" = ANC.B5.DE58; "severe abdominal pain" = ANC.B5.DE60 (distinct signs).
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


def _encounter_id(bundle: dict) -> str | None:
    for entry in bundle.get("entry", []):
        r = entry.get("resource", {})
        if r.get("resourceType") == "Encounter":
            return r.get("id")
    return None


def get_conversation_meta(bundle: dict) -> dict | None:
    exts = bundle.get("extension", [])
    raw = next(
        (e["valueString"] for e in exts if "healthbench-conversation" in e.get("url", "")),
        None,
    )
    if raw is None:
        return None
    try:
        turns = json.loads(raw)
    except Exception:
        return None
    return {
        "turns": turns,
        "prompt_id": next((e["valueString"] for e in exts if "healthbench-prompt-id" in e.get("url", "")), None),
        "dataset": next((e["valueString"] for e in exts if "healthbench-dataset" in e.get("url", "")), None),
    }


def get_conversation(bundle: dict) -> list[dict] | None:
    meta = get_conversation_meta(bundle)
    return meta["turns"] if meta else None


def _conversation_text(turns: list[dict]) -> str:
    lines = []
    for t in turns:
        lines.append(f"{t.get('role', 'unknown').capitalize()}: {t.get('content', '').strip()}")
    return "\n".join(lines)


# ── LLM extraction ────────────────────────────────────────────────────────────

def extract_danger_signs(conversation_text: str) -> dict:
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
    """Convert Claude extraction result to a FHIR R4 Bundle using the WHO ANCDT01 observation model."""
    pid = patient_id or f"patient-{uuid.uuid4().hex[:8]}"
    enc_id = f"enc-{pid}"
    today = date.today().isoformat()

    entries: list[dict] = [
        {"resource": {"resourceType": "Patient", "id": pid, "gender": "female"}},
        {"resource": {
            "resourceType": "Encounter",
            "id": enc_id,
            "status": "finished",
            "class": {
                "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
                "code": "AMB",
                "display": "ambulatory",
            },
            "subject": {"reference": f"Patient/{pid}"},
        }},
    ]

    ga = extraction.get("gestational_age_weeks")
    if ga is not None:
        entries.append({"resource": {
            "resourceType": "Observation",
            "id": f"ga-{pid}",
            "status": "final",
            "code": {"coding": [{"system": LOINC, "code": GA_LOINC, "display": "Gestational age in weeks"}]},
            "subject": {"reference": f"Patient/{pid}"},
            "encounter": {"reference": f"Encounter/{enc_id}"},
            "effectiveDateTime": today,
            "valueQuantity": {"value": int(ga), "unit": "wk", "system": "http://unitsofmeasure.org", "code": "wk"},
        }})

    detected_signs = extraction.get("danger_signs", [])

    if detected_signs:
        for sign in detected_signs:
            obs: dict = {
                "resourceType": "Observation",
                "id": f"ds-{sign['code'].lower().replace('.', '-')}-{pid}",
                "status": "final",
                "code": {"coding": [{"system": ANC_SYSTEM, "code": DANGER_SIGN_OBS_CODE, "display": "Danger signs"}]},
                "subject": {"reference": f"Patient/{pid}"},
                "encounter": {"reference": f"Encounter/{enc_id}"},
                "effectiveDateTime": today,
                "valueCodeableConcept": {"coding": [{"system": ANC_SYSTEM, "code": sign["code"], "display": sign["display"]}]},
            }
            evidence = sign.get("evidence", "")
            if evidence:
                obs["note"] = [{"text": evidence}]
            entries.append({"resource": obs})
    else:
        entries.append({"resource": {
            "resourceType": "Observation",
            "id": f"ds-none-{pid}",
            "status": "final",
            "code": {"coding": [{"system": ANC_SYSTEM, "code": DANGER_SIGN_OBS_CODE, "display": "Danger signs"}]},
            "subject": {"reference": f"Patient/{pid}"},
            "encounter": {"reference": f"Encounter/{enc_id}"},
            "effectiveDateTime": today,
            "valueCodeableConcept": {"coding": [{"system": ANC_SYSTEM, "code": NO_DANGER_SIGNS_CODE, "display": "No danger signs"}]},
        }})

    return {"resourceType": "Bundle", "id": f"extracted-{pid}", "type": "collection", "entry": entries}


def bundle_to_lean_json(bundle: dict) -> dict:
    """Extract PatientState JSON for the Lean evaluator from a WHO-model FHIR bundle."""
    patient = next(
        (e["resource"] for e in bundle.get("entry", [])
         if e.get("resource", {}).get("resourceType") == "Patient"),
        {}
    )
    result: dict = {
        "id": patient.get("id", bundle.get("id", "unknown")),
        "gestational_age_weeks": None,
        "danger_sign_status": "unknown",
    }

    for obs in _observations(bundle):
        if obs.get("status") not in ("final", "amended", "corrected"):
            continue
        code = _obs_code(obs)
        if code == GA_LOINC:
            result["gestational_age_weeks"] = obs.get("valueQuantity", {}).get("value")
        elif code == DANGER_SIGN_OBS_CODE:
            for coding in obs.get("valueCodeableConcept", {}).get("coding", []):
                vc = coding.get("code")
                if vc in DANGER_SIGN_VALUE_CODES:
                    result["danger_sign_status"] = "true"
                    break
                elif vc == NO_DANGER_SIGNS_CODE:
                    if result["danger_sign_status"] != "true":
                        result["danger_sign_status"] = "false"

    return result


# ── Engine runners ────────────────────────────────────────────────────────────

def run_google_cql(bundle_path: str) -> dict:
    if not VENDOR_CQL_DIR.exists():
        return {"error": "WHO CQL not found. Run scripts/setup-vendor.sh first."}

    bundle = json.loads(Path(bundle_path).read_text())
    enc_id = _encounter_id(bundle)

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        cql_dir = tmp / "cql"
        bundle_dir = tmp / "bundles"
        out_dir = tmp / "output"
        for d in (cql_dir, bundle_dir, out_dir):
            d.mkdir()

        # Copy all WHO CQL source files (ANCDT01 and its dependencies)
        for f in VENDOR_CQL_DIR.glob("*.cql"):
            shutil.copy(f, cql_dir / f.name)
        opts = VENDOR_CQL_DIR / "cql-options.json"
        if opts.exists():
            shutil.copy(opts, cql_dir / opts.name)

        shutil.copy(bundle_path, bundle_dir / "patient.json")

        cmd = [
            "google-cql",
            f"--cql_dir={cql_dir}",
            f"--fhir_bundle_dir={bundle_dir}",
            f"--json_output_dir={out_dir}",
        ]
        if enc_id:
            cmd.append(f"--cql_parameters_json={json.dumps({'encounter': enc_id})}")

        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
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

            urgent = _val("Should Proceed with ANC contact OR Referral")
            routine = _val("Should Proceed with ANC contact")
            disposition = (
                "urgent_referral" if urgent is True
                else "routine_follow_up" if routine is True
                else "unknown"
            )

            danger_signs_raw = _val("Danger signs") or []
            active_signs = []
            if isinstance(danger_signs_raw, list):
                for cc in danger_signs_raw:
                    for coding in cc.get("coding", []):
                        if coding.get("display"):
                            active_signs.append(coding["display"])
                            break

            return {
                "disposition": disposition,
                "has_danger_sign": "true" if urgent is True else ("false" if routine is True else "unknown"),
                "active_signs": active_signs,
                "engine": "google-cql (ANCDT01)",
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

def _conversation_md(meta: dict) -> str:
    turns = meta.get("turns", [])
    prompt_id = meta.get("prompt_id")
    dataset = meta.get("dataset")

    header_parts = []
    if prompt_id:
        header_parts.append(f"**Prompt ID:** `{prompt_id}`")
    if dataset:
        header_parts.append(f"**Dataset:** `{dataset}`")
    header = "  ·  ".join(header_parts)

    roles = {t.get("role") for t in turns}
    one_sided = "assistant" not in roles and len(turns) > 0
    note = "\n\n_⚠ This entry shows the initial query only — no assistant response in the HealthBench record._" if one_sided else ""

    lines = [header, ""] if header else []
    for t in turns:
        role = t.get("role", "")
        content = t.get("content", "").strip()
        if role == "user":
            lines.append(f"**Patient/Clinician:** {content}")
        else:
            lines.append(f"**Assistant:** {content}")
        lines.append("")
    return "\n".join(lines) + note


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
    if detected_count > 0:
        return (
            f"_Claude extracted {detected_count} danger sign(s) from the conversation. "
            f"Each sign became one FHIR R4 Observation coded with ANC.B5.DE48 "
            f"(`Danger signs`) and a `valueCodeableConcept` from the WHO ANC code system._"
        )
    return (
        "_No danger signs detected. A single 'No danger signs' (ANC.B5.DE49) observation "
        "was recorded — the WHO model requires an explicit negative assertion._"
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
    conv_meta = get_conversation_meta(bundle)

    if strip_ga:
        bundle = {**bundle, "entry": [
            e for e in bundle.get("entry", [])
            if _obs_code(e.get("resource", {})) != GA_LOINC
        ]}
    if strip_signs:
        bundle = {**bundle, "entry": [
            e for e in bundle.get("entry", [])
            if _obs_code(e.get("resource", {})) != DANGER_SIGN_OBS_CODE
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

    # Build extraction-like display dict from the WHO observations in the bundle
    detected_signs = []
    for obs in _observations(bundle):
        if _obs_code(obs) != DANGER_SIGN_OBS_CODE:
            continue
        for coding in obs.get("valueCodeableConcept", {}).get("coding", []):
            vc = coding.get("code")
            if vc in DANGER_SIGN_VALUE_CODES:
                note = next((n["text"] for n in obs.get("note", [])), "")
                detected_signs.append({
                    "code": vc,
                    "display": coding.get("display", DANGER_SIGN_DISPLAY.get(vc, vc)),
                    "evidence": note,
                })
    extraction_display = {
        "gestational_age_weeks": lean_json.get("gestational_age_weeks"),
        "danger_signs": detected_signs,
    }

    conv_md = _conversation_md(conv_meta) if conv_meta else "_No conversation in this fixture._"
    extraction_md = _extraction_md(extraction_display)
    fhir_note = _fhir_extraction_note(extraction_display)
    results_md = _results_md(cql_out, lean_out)
    proofs_md = _proofs_md(proofs)

    return conv_md, extraction_md, fhir_note, results_md, proofs_md, json.dumps(bundle, indent=2)


def evaluate_live(conversation_text: str) -> tuple[str, str, str, str, str]:
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

ANCDT01_URL = "https://github.com/WorldHealthOrganization/smart-anc/blob/master/input/cql/ANCDT01.cql"

CQL_SOURCE = f"""\
-- Source: {ANCDT01_URL}
library ANCDT01

using FHIR version '4.0.1'

include FHIRHelpers version '4.0.1'
include ANCConfig called Config
include ANCConcepts called Cx
include ANCDataElements called PatientData
include ANCContactDataElements called ContactData

context Patient

define "Danger signs":
  ContactData."Danger signs"

define "Should Proceed with ANC contact":
  ContactData."Danger signs" in Cx."Danger Signs - No danger signs Choices"

define "Should Proceed with ANC contact OR Referral for Central cyanosis":
  ContactData."Danger signs" in Cx."Danger Signs - Central cyanosis Choices"

define "Should Proceed with ANC contact OR Referral":
  ContactData."Danger signs" in Cx."Danger signs Choices"
"""

LEAN_SOURCE = """\
-- PatientState mirrors the WHO ANCDT01 single-observation model:
-- dangerSignStatus: Trilean  (true=sign present / false=DE49 / unknown=not assessed)
def hasDangerSignTrilean (p : PatientState) : Trilean :=
  p.dangerSignStatus

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

                evaluate_btn = gr.Button("Load Scenario", variant="primary")

                with gr.Accordion("Clinical conversation (HealthBench source)", open=True):
                    conv_out = gr.Markdown(value="_Select a scenario above, then click Load Scenario._")

                with gr.Accordion("Step 1 — Danger signs in FHIR (WHO ANCDT01 model)", open=True):
                    gr.Markdown(
                        "Each ANC.B5 danger sign is one FHIR Observation with "
                        f"`code = ANC.B5.DE48` (Danger signs) and `valueCodeableConcept` = the specific sign, "
                        f"per the [WHO SMART ANC ANCDT01 library]({ANCDT01_URL})."
                    )
                    extraction_f_out = gr.Markdown()
                    fhir_note_f_out = gr.Markdown()

                with gr.Accordion("Step 2 — The clinical rule (CQL + Lean)", open=False):
                    with gr.Row():
                        with gr.Column():
                            gr.Markdown("### CQL rule (ANCDT01.cql — WHO source)")
                            gr.Markdown(
                                f"Executed by **Google CQL** against the FHIR bundle. "
                                f"Source: [{ANCDT01_URL}]({ANCDT01_URL}). "
                                f"Run `scripts/setup-vendor.sh` to fetch the full library."
                            )
                            gr.Code(value=CQL_SOURCE, language="sql", label="ANCDT01.cql (excerpt)")
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
