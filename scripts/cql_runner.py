#!/usr/bin/env python3
"""
Run WHO ANC DangerSigns.cql against one or more FHIR R4 patient bundles
using the Google CQL standalone engine.

Usage:
    # Single bundle:
    python scripts/cql_runner.py fixtures/patients/severe-headache.json

    # All bundles in a directory:
    python scripts/cql_runner.py fixtures/patients/

    # Bundle produced by extract_danger_signs.py (piped):
    python scripts/extract_danger_signs.py conv.json | python scripts/cql_runner.py -

    # Full pipeline:
    python scripts/extract_danger_signs.py hb-vaginal-bleeding.json --fhir \\
      | python scripts/cql_runner.py - --out results/hb-vaginal-bleeding.json

Output: JSON with CQL evaluation results per bundle.

Requires: google-cql binary on PATH (go install github.com/google/cql/cmd/cli@latest).
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
CQL_DIR = REPO_ROOT / "cql"

KEY_EXPRESSIONS = [
    "Recommend Urgent Referral",
    "Recommend Routine Follow Up",
    "Has Danger Sign",
    "Has Bleeding Vaginally",
    "Has Central Cyanosis",
    "Is Convulsing",
    "Has Fever",
    "Has Severe Headache",
    "Has Visual Disturbance",
    "Imminent Delivery Indicated",
    "In Labour",
    "Looks Very Ill",
    "Has Severe Vomiting",
    "Has Severe Pain",
    "Has Severe Abdominal Pain",
    "Gestational Age Weeks",
]


def run_cql(bundle_path: Path) -> dict:
    """Run google-cql against a single bundle. Returns structured result dict."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        bundle_dir = tmp / "bundles"
        out_dir = tmp / "output"
        bundle_dir.mkdir()
        out_dir.mkdir()

        shutil.copy(bundle_path, bundle_dir / "patient.json")

        proc = subprocess.run(
            [
                "google-cql",
                f"--cql_dir={CQL_DIR}",
                f"--fhir_bundle_dir={bundle_dir}",
                f"--json_output_dir={out_dir}",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )

        if proc.returncode != 0:
            return {"error": proc.stderr or proc.stdout, "bundle": str(bundle_path)}

        result_files = list(out_dir.glob("*.json"))
        if not result_files:
            return {"error": f"No output produced.\n{proc.stdout[:300]}", "bundle": str(bundle_path)}

        raw = json.loads(result_files[0].read_text())
        defs = raw.get("evalResults", [{}])[0].get("expressionDefinitions", {})

        def val(name: str):
            return defs.get(name, {}).get("value")

        urgent = val("Recommend Urgent Referral")
        result = {
            "bundle": str(bundle_path),
            "disposition": (
                "urgent_referral" if urgent is True
                else "routine_follow_up" if urgent is False
                else "unknown"
            ),
            "expressions": {
                name: val(name) for name in KEY_EXPRESSIONS
            },
        }

        # Collect active danger signs
        active_signs = [
            name for name in KEY_EXPRESSIONS
            if name not in ("Recommend Urgent Referral", "Recommend Routine Follow Up",
                            "Has Danger Sign", "Gestational Age Weeks", "Has Central Cyanosis")
            and val(name) is True
        ]
        result["active_danger_signs"] = active_signs
        result["gestational_age_weeks"] = val("Gestational Age Weeks")
        return result


def eval_bundle_data(bundle_data: dict) -> dict:
    """Write bundle to a temp file and evaluate it."""
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
    parser.add_argument("--pretty", action="store_true", default=True, help="Pretty-print JSON output")
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
