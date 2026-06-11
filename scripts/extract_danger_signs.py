#!/usr/bin/env python3
"""
Extract WHO ANC ANCDT01 danger signs from clinical conversation text using Claude.

Usage:
    # From a JSON file with a "conversation" array:
    python scripts/extract_danger_signs.py conversation.json

    # From a FHIR bundle (extracts the healthbench-conversation extension):
    python scripts/extract_danger_signs.py fixtures/patients/hb-vaginal-bleeding.json --fhir

    # Pipe raw text:
    echo "Patient has severe headache at 32 weeks" | python scripts/extract_danger_signs.py -

Output: FHIR R4 Bundle JSON written to stdout (or --out <file>).

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

ANC_CUSTOM_CODES = "http://fhir.org/guides/who/anc-cds/CodeSystem/anc-custom-codes"
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

EXTRACTION_PROMPT = """You are a clinical coding assistant for antenatal care (ANC).

Your task: review the clinical conversation below and identify which WHO ANC danger signs
(ANCDT01 / ANC.B5 Quick Check) are explicitly supported by the text.

Danger sign value set (system: {system}):
{sign_list}

Rules:
- Only include signs with clear textual evidence. Do NOT infer or assume.
- "central cyanosis" = blue discoloration of lips/skin, not just oxygen concern.
- "severe pain" = ANC.B5.DE13; "severe abdominal pain" = ANC.B5.DE14 (distinct signs).
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
    """Call Claude to extract danger signs from conversation text. Returns extraction dict."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    sign_list = "\n".join(
        f"  - {s['code']}  {s['display']}" for s in DANGER_SIGNS
    )
    prompt = EXTRACTION_PROMPT.format(
        system=ANC_CUSTOM_CODES,
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
    return json.loads(raw)


def extraction_to_fhir_bundle(extraction: dict, patient_id: str | None = None) -> dict:
    """Convert extraction result to a FHIR R4 Bundle with boolean Observations."""
    pid = patient_id or f"patient-{uuid.uuid4().hex[:8]}"
    today = date.today().isoformat()

    entries = [
        {
            "resource": {
                "resourceType": "Patient",
                "id": pid,
                "gender": "female",
            }
        }
    ]

    ga = extraction.get("gestational_age_weeks")
    if ga is not None:
        entries.append({
            "resource": {
                "resourceType": "Observation",
                "id": f"ga-{pid}",
                "status": "final",
                "code": {
                    "coding": [{"system": LOINC, "code": "49051-6", "display": "Gestational age in weeks"}]
                },
                "subject": {"reference": f"Patient/{pid}"},
                "effectiveDateTime": today,
                "valueQuantity": {"value": int(ga), "unit": "wk", "system": "http://unitsofmeasure.org", "code": "wk"},
            }
        })

    detected_codes = {s["code"] for s in extraction.get("danger_signs", [])}
    evidence_map = {s["code"]: s.get("evidence", "") for s in extraction.get("danger_signs", [])}

    for sign in DANGER_SIGNS:
        present = sign["code"] in detected_codes
        obs: dict = {
            "resourceType": "Observation",
            "id": f"sign-{sign['code'].lower().replace('.', '-')}-{pid}",
            "status": "final",
            "code": {
                "coding": [{"system": ANC_CUSTOM_CODES, "code": sign["code"], "display": sign["display"]}]
            },
            "subject": {"reference": f"Patient/{pid}"},
            "effectiveDateTime": today,
            "valueBoolean": present,
        }
        if present and evidence_map.get(sign["code"]):
            obs["note"] = [{"text": evidence_map[sign["code"]]}]
        entries.append({"resource": obs})

    return {
        "resourceType": "Bundle",
        "id": f"extracted-{pid}",
        "type": "collection",
        "entry": entries,
    }


def load_input(path: str, is_fhir: bool) -> tuple[str, str | None]:
    """Returns (conversation_text, patient_id_or_None)."""
    if path == "-":
        return sys.stdin.read(), None

    with open(path) as f:
        data = json.load(f)

    if is_fhir:
        # Extract healthbench-conversation extension from FHIR bundle
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
        return conversation_to_text(conversation), patient.get("id")
    else:
        # Plain JSON: expect {"conversation": [...]} or a raw list
        if isinstance(data, list):
            return conversation_to_text(data), None
        return conversation_to_text(data.get("conversation", [])), data.get("patient_id")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", help="Input file path, or '-' for stdin")
    parser.add_argument("--fhir", action="store_true", help="Input is a FHIR bundle with healthbench-conversation extension")
    parser.add_argument("--out", help="Output file path (default: stdout)")
    parser.add_argument("--extraction-only", action="store_true", help="Print raw extraction JSON, not full FHIR bundle")
    args = parser.parse_args()

    text, patient_id = load_input(args.input, args.fhir)
    extraction = extract_from_text(text)

    if args.extraction_only:
        output = json.dumps(extraction, indent=2)
    else:
        bundle = extraction_to_fhir_bundle(extraction, patient_id)
        output = json.dumps(bundle, indent=2)

    if args.out:
        with open(args.out, "w") as f:
            f.write(output)
        print(f"Written to {args.out}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
