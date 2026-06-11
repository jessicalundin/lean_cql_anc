"""
filter_healthbench_anc.py
─────────────────────────────────────────────────────────────────────────────
Filter the OpenAI HealthBench dataset for conversations relevant to
WHO Antenatal Care (ANC) — the clinical scope addressed by the CQL
ANCCohort library (http://hl7.org/fhir/uv/cql/Library/ANCCohort).

WHO ANC CQL scope (2016 guidelines, 8-contact model):
  A. Nutritional interventions      (iron/folate, calcium, vitamin D, zinc,
                                      balanced diet, energy/protein, caffeine)
  B. Maternal & fetal assessment    (anaemia, BP/pre-eclampsia, gestational
                                      diabetes, HIV, TB, syphilis, ultrasound,
                                      fundal height, fetal movement, Doppler)
  C. Preventive measures            (tetanus, malaria IPTp, anti-D, ASB Abx,
                                      de-worming, bed nets)
  D. Common physiological symptoms  (nausea/vomiting, constipation, heartburn,
                                      leg cramps, varicose veins, back pain)
  E. Health system interventions    (ANC contacts schedule, group ANC,
                                      midwife-led care, task shifting)
  + Birth preparedness, postnatal / breastfeeding counselling, IPV screening

Usage
─────
    # download a HealthBench JSONL first, e.g.:
    #   curl -O https://openaipublic.blob.core.windows.net/simple-evals/healthbench/hard_2025-05-08-21-00-10.jsonl

    python scripts/filter_healthbench_anc.py --input hard_2025-05-08-21-00-10.jsonl

Output
──────
  • Console summary with counts and tag/theme breakdown
  • healthbench_anc_matches.jsonl   – matched examples (full records)
  • healthbench_anc_summary.csv     – one row per example with key metadata
"""

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from collections import Counter

# ── 1. ANC KEYWORD TAXONOMY ──────────────────────────────────────────────────

ANC_KEYWORD_GROUPS = {
    # ── A. Nutrition ────────────────────────────────────────────────────────
    "nutrition_supplements": [
        r"\biron\b", r"\bfolate\b", r"\bfolic\s+acid\b",
        r"\bcalcium\b", r"\bvitamin\s+[dD]\b", r"\bzinc\b",
        r"\bmicronutrient\b", r"\bsupplement",
        r"\biodine\b", r"\bvitamin\s+[aA]\b",
    ],
    "nutrition_diet": [
        r"\bmaternal\s+nutrition\b", r"\bpregnancy\s+diet\b",
        r"\bcaffeine\b", r"\bbalanced\s+(diet|energy)\b",
        r"\bprotein\s+supplement\b",
    ],

    # ── B. Maternal & Fetal Assessment ──────────────────────────────────────
    "pregnancy_screening": [
        r"\bantenatal\b", r"\bprenatal\b", r"\bante\s*natal\b",
        r"\bpregnancy\s+care\b", r"\bANC\b",
        r"\bgestational\s+diabet", r"\bGDM\b",
        r"\bpre[- ]?eclampsia\b", r"\beclampsia\b",
        r"\bhypertension\s+in\s+pregnancy\b", r"\bPIH\b",
        r"\banaemia\b", r"\banemia\b",
        r"\bblood\s+pressure\b.*pregnan", r"pregnan.*\bblood\s+pressure\b",
        r"\bsymphysis[- ]fundal\b", r"\bfundal\s+height\b",
        r"\bfetal\s+(movement|kick|heart|growth|wellbeing|assessment)\b",
        r"\bbaby\s+(movement|kick|heartbeat)\b",
        r"\bDoppler\b.*pregnan", r"pregnan.*\bDoppler\b",
        r"\bultrasound\b.*pregnan", r"pregnan.*\bultrasound\b",
        r"\bfirst\s+trimester\b", r"\bsecond\s+trimester\b",
        r"\bthird\s+trimester\b",
        r"\bgestational\s+age\b", r"\bdue\s+date\b",
        r"\bgrowth\s+scan\b",
    ],
    "infection_screening": [
        r"\bsyphilis\b.*pregnan", r"pregnan.*\bsyphilis\b",
        r"\bHIV\b.*pregnan", r"pregnan.*\bHIV\b",
        r"\btuberculosis\b.*pregnan", r"pregnan.*\btuberculosis\b",
        r"\bhepatitis\b.*pregnan", r"pregnan.*\bhepatitis\b",
        r"\bGBS\b", r"\bgroup\s+B\s+strep",
        r"\basymptomatic\s+bacteriuria\b",
        r"\burinary\s+tract\s+infection\b.*pregnan",
        r"\bRh\s+factor\b", r"\bblood\s+group\b.*pregnan",
    ],

    # ── C. Preventive Measures ───────────────────────────────────────────────
    "preventive_anc": [
        r"\btetanus\b.*pregnan", r"pregnan.*\btetanus\b",
        r"\bmalaria\b.*pregnan", r"pregnan.*\bmalaria\b",
        r"\bIPTp\b", r"\bintermittent\s+preventive",
        r"\banti[- ]?D\b", r"\bRh\s+immunoglob",
        r"\bde[- ]?worming\b.*pregnan",
        r"\bbed\s*net\b.*pregnan",
        r"\baspirin\b.*pregnan", r"pregnan.*\baspirin\b",
    ],

    # ── D. Common Physiological Symptoms ────────────────────────────────────
    "pregnancy_symptoms": [
        r"\bmorning\s+sickness\b",
        r"\bnausea\b.*pregnan", r"pregnan.*\bnausea\b",
        r"\bvomiting\b.*pregnan", r"pregnan.*\bvomiting\b",
        r"\bhyperemes",
        r"\bconstipation\b.*pregnan", r"pregnan.*\bconstipation\b",
        r"\bheartburn\b.*pregnan", r"pregnan.*\bheartburn\b",
        r"\bleg\s+cramp\b.*pregnan", r"pregnan.*\bleg\s+cramp\b",
        r"\bvaricose\b.*pregnan",
        r"\bback\s+pain\b.*pregnan", r"pregnan.*\bback\s+pain\b",
        r"\bround\s+ligament\b",
        r"\bedema\b.*pregnan", r"pregnan.*\boedema\b",
        r"\bpelvic\s+girdle\b",
    ],

    # ── E. Health System / Care Delivery ────────────────────────────────────
    "anc_care_delivery": [
        r"\bANC\s+visit", r"\bantenatal\s+visit",
        r"\bprenatal\s+(visit|appointment|check)",
        r"\bmidwife\b", r"\bobstetrician\b",
        r"\bgroup\s+antenatal\b",
        r"\bbirth\s+plan\b", r"\bbirth\s+preparedness\b",
        r"\bpostnatal\s+care\b", r"\bpostpartum\b",
        r"\bbreastfeeding\b.*pregnan", r"pregnan.*\bbreastfeeding\b",
        r"\blabour\b.*pregnan", r"pregnan.*\blabour\b",
        r"\bdelivery\s+plan\b",
    ],

    # ── Core pregnancy terms (broad anchor) ────────────────────────────────
    "core_pregnancy": [
        r"\bpregnant\b", r"\bpregnancy\b", r"\bgestation\b",
        r"\bmaternal\s+health\b", r"\bexpectant\s+mother\b",
        r"\bpregnan(t|cy)\s+woman\b",
        r"\btrimester\b",
        r"\bfetus\b", r"\bfoetus\b", r"\bfetal\b", r"\bfoetal\b",
        r"\bumbilical\b", r"\bplacenta\b", r"\bamniotic\b",
        r"\bmorning\s+sickness\b", r"\bpreclampsia\b",
    ],
}

_COMPILED = {
    group: [re.compile(p, re.IGNORECASE) for p in patterns]
    for group, patterns in ANC_KEYWORD_GROUPS.items()
}

# ── 2. MATCHING LOGIC ─────────────────────────────────────────────────────────

def extract_conversation_text(example: dict) -> str:
    parts = []
    for turn in example.get("prompt", []):
        content = turn.get("content", "")
        if content:
            parts.append(content)
    return " ".join(parts)


def extract_rubric_text(example: dict) -> str:
    parts = []
    for rubric in example.get("rubrics", []):
        crit = rubric.get("criterion", "")
        if crit:
            parts.append(crit)
    if example.get("rubric"):
        parts.append(example["rubric"])
    return " ".join(parts)


def get_example_tag_string(example: dict) -> str:
    return " ".join(example.get("example_tags", []))


def match_groups(text: str) -> list[str]:
    matched = []
    for group, patterns in _COMPILED.items():
        if any(p.search(text) for p in patterns):
            matched.append(group)
    return matched


def is_anc_relevant(example: dict) -> tuple[bool, list[str], str]:
    tag_str = get_example_tag_string(example)
    conv_text = extract_conversation_text(example)
    rubric_text = extract_rubric_text(example)

    tag_groups = match_groups(tag_str)
    if tag_groups:
        return True, tag_groups, "tags"

    conv_groups = match_groups(conv_text)
    if conv_groups:
        if "core_pregnancy" in conv_groups or len(conv_groups) >= 2:
            return True, conv_groups, "conversation"

    rubric_groups = match_groups(rubric_text)
    if rubric_groups:
        if "core_pregnancy" in rubric_groups or len(rubric_groups) >= 2:
            return True, rubric_groups, "rubrics"

    return False, [], ""


# ── 3. MAIN ───────────────────────────────────────────────────────────────────

def main(input_path: str, output_jsonl: str = "healthbench_anc_matches.jsonl",
         output_csv: str = "healthbench_anc_summary.csv") -> None:

    input_file = Path(input_path)
    if not input_file.exists():
        sys.exit(f"ERROR: File not found: {input_path}")

    total = 0
    matches = []
    theme_counter: Counter = Counter()
    group_counter: Counter = Counter()
    layer_counter: Counter = Counter()

    print(f"\nScanning {input_file.name} …")

    with input_file.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                example = json.loads(line)
            except json.JSONDecodeError:
                continue

            total += 1
            relevant, groups, layer = is_anc_relevant(example)

            if relevant:
                matches.append(example)
                layer_counter[layer] += 1
                for g in groups:
                    group_counter[g] += 1

                for tag in example.get("example_tags", []):
                    if tag.startswith("theme:"):
                        theme_counter[tag.replace("theme:", "")] += 1

    with open(output_jsonl, "w") as fh:
        for ex in matches:
            fh.write(json.dumps(ex) + "\n")

    with open(output_csv, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "prompt_id", "example_tags", "matched_layer",
            "first_user_message_preview"
        ])
        for ex in matches:
            _, groups, layer = is_anc_relevant(ex)
            tags = "|".join(ex.get("example_tags", []))
            preview = ""
            for turn in ex.get("prompt", []):
                if turn.get("role") == "user":
                    preview = turn.get("content", "")[:200].replace("\n", " ")
                    break
            writer.writerow([ex.get("prompt_id", ""), tags, layer, preview])

    pct = 100.0 * len(matches) / total if total else 0
    print(f"\n{'━'*60}")
    print(f"  Total examples scanned :  {total:,}")
    print(f"  ANC-relevant matches   :  {len(matches):,}  ({pct:.1f}%)")
    print(f"{'━'*60}")

    print("\n  Matched by layer:")
    for layer, n in sorted(layer_counter.items(), key=lambda x: -x[1]):
        print(f"    {layer:<20} {n:>5}")

    print("\n  Matched ANC keyword groups (examples may span multiple groups):")
    for group, n in sorted(group_counter.items(), key=lambda x: -x[1]):
        print(f"    {group:<30} {n:>5}")

    if theme_counter:
        print("\n  HealthBench themes among ANC matches:")
        for theme, n in sorted(theme_counter.items(), key=lambda x: -x[1]):
            print(f"    {theme:<35} {n:>5}")

    print(f"\n  Outputs written:")
    print(f"    {output_jsonl}")
    print(f"    {output_csv}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Filter HealthBench for WHO ANC CQL-relevant conversations."
    )
    parser.add_argument(
        "--input", required=True,
        help="Path to HealthBench JSONL file"
    )
    parser.add_argument(
        "--output-jsonl", default="healthbench_anc_matches.jsonl",
        help="Output JSONL file for matched examples (default: healthbench_anc_matches.jsonl)"
    )
    parser.add_argument(
        "--output-csv", default="healthbench_anc_summary.csv",
        help="Output CSV summary file (default: healthbench_anc_summary.csv)"
    )
    args = parser.parse_args()
    main(args.input, args.output_jsonl, args.output_csv)
