#!/usr/bin/env python3
"""
Extract WHO ANC ANCDT01 danger signs from clinical conversation text using Claude
and produce a FHIR R4 Bundle using the WHO observation model:
  - Single Observation per sign with code=ANC.B5.DE48 ("Danger signs") and
    valueCodeableConcept = the specific sign code (DE50–DE62), or
    ANC.B5.DE49 ("No danger signs") when none are found.
  - Observation.encounter links to a bundled Encounter resource.

Source: https://github.com/WorldHealthOrganization/smart-anc/blob/master/input/cql/ANCDT01.cql

Usage:
    python scripts/extract_danger_signs.py conversation.json
    python scripts/extract_danger_signs.py fixtures/patients/hb-vaginal-bleeding.json --fhir
    echo "Patient has severe headache at 32 weeks" | python scripts/extract_danger_signs.py -

Output: FHIR R4 Bundle JSON (stdout or --out <file>).
Requires: ANTHROPIC_API_KEY environment variable.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import date

import anthropic

ANC_SYSTEM = "http://smart.who.int/anc/CodeSystem/anc-custom-codes"
LOINC = "http://loinc.org"

DANGER_SIGN_OBS_CODE = "ANC.B5.DE48"   # Observation.code: "Danger signs"
NO_DANGER_SIGNS_CODE = "ANC.B5.DE49"   # Observation.value: "No danger signs"

# ANCDT01 danger sign value codes (ANC.B5 Quick Check, WHO SMART ANC master branch)
# Source: input/vocabulary/valueset/valueset-anc-b5-de50.json
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

EXTRACTION_PROMPT = """You are a clinical coding assistant for antenatal care (ANC).

Your task: review the clinical conversation below and identify which WHO ANC danger signs
(ANCDT01 / ANC.B5 Quick Check) are explicitly supported by the text.

Danger sign value set (system: {system}):
{sign_list}

Rules:
- Only include signs with clear textual evidence. Do NOT infer or assume.
- "central cyanosis" = blue discoloration of lips/skin, not just oxygen concern.
- "severe pain" = ANC.B5.DE58; "severe abdominal pain" = ANC.B5.DE60 (distinct signs).
- Include gestational_age_weeks if mentioned (integer or null).
- Return ONLY valid JSON — no prose, no markdown code fences.

Return JSON in this exact schema:
{{
  "gestational_age_weeks": <integer or null>,
  "danger_signs": [
    {{"code": "<ANC.B5.DExx>", "display": "<display>", "evidence": "<exact quote from text>"}}
  ]
}}

Conversation:
{conversation}"""


def conversation_to_text(conversation: list[dict]) -> str:
    lines = []
    for turn in conversation:
        role = turn.get("role", "unknown").capitalize()
        lines.append(f"{role}: {turn.get('content', '').strip()}")
    return "\n".join(lines)


def extract_from_text(text: str) -> dict:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    sign_list = "\n".join(f"  - {s['code']}  {s['display']}" for s in DANGER_SIGNS)
    prompt = EXTRACTION_PROMPT.format(
        system=ANC_SYSTEM,
        sign_list=sign_list,
        conversation=text,
    )

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = message.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    return json.loads(raw)


def extraction_to_fhir_bundle(
    extraction: dict,
    patient_id: str | None = None,
    preserve_extensions: list[dict] | None = None,
    bundle_id: str | None = None,
) -> dict:
    """Convert extraction result to a FHIR R4 Bundle using the WHO ANCDT01 observation model.

    Each detected danger sign becomes one Observation:
      code = ANC.B5.DE48 ("Danger signs")
      valueCodeableConcept = the sign code (DE50–DE62)
      encounter = reference to the bundled Encounter

    If no danger signs are found, a single "No danger signs" (DE49) observation is produced.
    """
    pid = patient_id or f"patient-{uuid.uuid4().hex[:8]}"
    enc_id = f"enc-{pid}"
    bid = bundle_id or f"extracted-{pid}"
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
            "code": {"coding": [{"system": LOINC, "code": "49051-6", "display": "Gestational age in weeks"}]},
            "subject": {"reference": f"Patient/{pid}"},
            "encounter": {"reference": f"Encounter/{enc_id}"},
            "effectiveDateTime": today,
            "valueQuantity": {"value": int(ga), "unit": "wk", "system": "http://unitsofmeasure.org", "code": "wk"},
        }})

    detected_signs = extraction.get("danger_signs", [])

    if detected_signs:
        for sign in detected_signs:
            code = sign["code"]
            display = sign["display"]
            evidence = sign.get("evidence", "")
            obs: dict = {
                "resourceType": "Observation",
                "id": f"ds-{code.lower().replace('.', '-')}-{pid}",
                "status": "final",
                "code": {"coding": [{"system": ANC_SYSTEM, "code": DANGER_SIGN_OBS_CODE, "display": "Danger signs"}]},
                "subject": {"reference": f"Patient/{pid}"},
                "encounter": {"reference": f"Encounter/{enc_id}"},
                "effectiveDateTime": today,
                "valueCodeableConcept": {"coding": [{"system": ANC_SYSTEM, "code": code, "display": display}]},
            }
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

    bundle: dict = {"resourceType": "Bundle", "id": bid, "type": "collection"}
    if preserve_extensions:
        bundle["extension"] = preserve_extensions
    bundle["entry"] = entries
    return bundle


def load_input(path: str, is_fhir: bool) -> tuple[str, str | None, dict | None]:
    """Returns (conversation_text, patient_id_or_None, source_bundle_or_None)."""
    if path == "-":
        return sys.stdin.read(), None, None

    with open(path) as f:
        data = json.load(f)

    if is_fhir:
        conversation_raw = next(
            (e["valueString"] for e in data.get("extension", [])
             if "healthbench-conversation" in e.get("url", "")),
            None,
        )
        if conversation_raw is None:
            raise ValueError("No healthbench-conversation extension found in FHIR bundle")
        conversation = json.loads(conversation_raw)
        patient = next(
            (e["resource"] for e in data.get("entry", [])
             if e.get("resource", {}).get("resourceType") == "Patient"),
            {},
        )
        return conversation_to_text(conversation), patient.get("id"), data
    else:
        if isinstance(data, list):
            return conversation_to_text(data), None, None
        return conversation_to_text(data.get("conversation", [])), data.get("patient_id"), None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", help="Input file path, or '-' for stdin")
    parser.add_argument("--fhir", action="store_true", help="Input is a FHIR bundle with healthbench-conversation extension")
    parser.add_argument("--out", help="Output file path (default: stdout)")
    parser.add_argument("--extraction-only", action="store_true", help="Print raw extraction JSON, not full FHIR bundle")
    args = parser.parse_args()

    text, patient_id, source_bundle = load_input(args.input, args.fhir)
    extraction = extract_from_text(text)

    if args.extraction_only:
        output = json.dumps(extraction, indent=2)
    else:
        extensions = source_bundle.get("extension") if source_bundle else None
        bid = source_bundle.get("id") if source_bundle else None
        bundle = extraction_to_fhir_bundle(extraction, patient_id,
                                            preserve_extensions=extensions,
                                            bundle_id=bid)
        output = json.dumps(bundle, indent=2)

    if args.out:
        with open(args.out, "w") as f:
            f.write(output)
        print(f"Written to {args.out}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
