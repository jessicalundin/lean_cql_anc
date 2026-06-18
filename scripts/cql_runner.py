#!/usr/bin/env python3
"""
Run WHO ANCDT01.cql against one or more FHIR R4 patient bundles using the
Google CQL standalone engine.

CQL source: vendor/smart-anc/input/cql/  (run scripts/setup-vendor.sh first)

Evaluated expressions (ANCDT01):
  "Should Proceed with ANC contact"                       → routine follow-up
  "Should Proceed with ANC contact OR Referral"           → urgent referral
  "Should Proceed with ANC contact OR Referral for Central cyanosis"
  "Danger signs"                                          → list of CodeableConcepts

NOTE: ANCDT01 depends on ANCContactDataElements which filters observations by
encounter.  Each patient bundle must contain an Encounter resource; the runner
passes its ID as the CQL `encounter` parameter via --cql_parameters_json.

Usage:
    python scripts/cql_runner.py fixtures/patients/hb-severe-headache.json
    python scripts/cql_runner.py fixtures/patients/
    python scripts/extract_danger_signs.py conv.json | python scripts/cql_runner.py -

Requires: google-cql on PATH (go install github.com/google/cql/cmd/cli@latest)
          vendor/smart-anc/input/cql/ populated (run scripts/setup-vendor.sh)
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CQL_DIR = REPO_ROOT / "vendor" / "smart-anc" / "input" / "cql"

# ANCDT01 expression names (WHO SMART ANC, ANCDT01.cql)
KEY_EXPRESSIONS = [
    "Should Proceed with ANC contact",
    "Should Proceed with ANC contact OR Referral",
    "Should Proceed with ANC contact OR Referral for Central cyanosis",
    "Danger signs",
]


def _encounter_id(bundle: dict) -> str | None:
    for entry in bundle.get("entry", []):
        r = entry.get("resource", {})
        if r.get("resourceType") == "Encounter":
            return r.get("id")
    return None


def run_cql(bundle_path: Path) -> dict:
    if not CQL_DIR.exists():
        return {
            "error": f"WHO CQL not found at {CQL_DIR}. Run scripts/setup-vendor.sh first.",
            "bundle": str(bundle_path),
        }

    bundle = json.loads(bundle_path.read_text())
    enc_id = _encounter_id(bundle)

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        bundle_dir = tmp / "bundles"
        out_dir = tmp / "output"
        bundle_dir.mkdir()
        out_dir.mkdir()

        shutil.copy(bundle_path, bundle_dir / "patient.json")

        cmd = [
            "google-cql",
            f"--cql_dir={CQL_DIR}",
            f"--fhir_bundle_dir={bundle_dir}",
            f"--json_output_dir={out_dir}",
        ]
        # Pass encounter parameter so ANCContactDataElements can filter by encounter.
        # Flag name may need adjustment depending on google-cql version.
        if enc_id:
            params = json.dumps({"encounter": enc_id})
            cmd.append(f"--cql_parameters_json={params}")

        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

        if proc.returncode != 0:
            return {"error": proc.stderr or proc.stdout, "bundle": str(bundle_path)}

        result_files = list(out_dir.glob("*.json"))
        if not result_files:
            return {"error": f"No output produced.\n{proc.stdout[:300]}", "bundle": str(bundle_path)}

        raw = json.loads(result_files[0].read_text())
        defs = raw.get("evalResults", [{}])[0].get("expressionDefinitions", {})

        def val(name: str):
            return defs.get(name, {}).get("value")

        urgent = val("Should Proceed with ANC contact OR Referral")
        routine = val("Should Proceed with ANC contact")
        disposition = (
            "urgent_referral" if urgent is True
            else "routine_follow_up" if routine is True
            else "unknown"
        )

        # "Danger signs" returns a list of CodeableConcept dicts; extract display strings.
        danger_signs_raw = val("Danger signs") or []
        active_signs = []
        if isinstance(danger_signs_raw, list):
            for cc in danger_signs_raw:
                for coding in cc.get("coding", []):
                    if coding.get("display"):
                        active_signs.append(coding["display"])
                        break

        return {
            "bundle": str(bundle_path),
            "disposition": disposition,
            "expressions": {name: val(name) for name in KEY_EXPRESSIONS},
            "active_danger_signs": active_signs,
            "gestational_age_weeks": None,  # GA is not part of ANCDT01; parse separately if needed
        }


def eval_bundle_data(bundle_data: dict) -> dict:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(bundle_data, f)
        tmp_path = Path(f.name)
    try:
        return run_cql(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", help="FHIR bundle file, directory of bundles, or '-' for stdin")
    parser.add_argument("--out", help="Output file (default: stdout)")
    parser.add_argument("--pretty", action="store_true", default=True)
    args = parser.parse_args()

    results = []

    if args.input == "-":
        bundle = json.loads(sys.stdin.read())
        results.append(eval_bundle_data(bundle))
    else:
        path = Path(args.input)
        if path.is_dir():
            for bundle_file in sorted(path.glob("*.json")):
                print(f"  evaluating {bundle_file.name}...", file=sys.stderr)
                results.append(run_cql(bundle_file))
        else:
            results.append(run_cql(path))

    output = json.dumps(results if len(results) > 1 else results[0], indent=2 if args.pretty else None)

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(output)
        print(f"Written to {args.out}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
