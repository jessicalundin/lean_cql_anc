#!/usr/bin/env node
/**
 * Prototype CDS evaluator mirroring the Lean danger-sign slice.
 * v0.1: shared JSON fixtures (not full FHIR/CQL ELM yet).
 * Replace with cql-execution + ELM when ANC library is wired.
 */
import { readFileSync } from "fs";

const TRILEAN = { true: "true", false: "false", unknown: "unknown" };

function parseTrilean(value) {
  if (value === true || value === "true") return TRILEAN.true;
  if (value === false || value === "false") return TRILEAN.false;
  return TRILEAN.unknown;
}

function trileanOr(a, b) {
  if (a === TRILEAN.true || b === TRILEAN.true) return TRILEAN.true;
  if (a === TRILEAN.unknown || b === TRILEAN.unknown) return TRILEAN.unknown;
  return TRILEAN.false;
}

function hasDangerSign(p) {
  return trileanOr(
    trileanOr(parseTrilean(p.vaginal_bleeding), parseTrilean(p.severe_headache)),
    parseTrilean(p.reduced_fetal_movement)
  );
}

function disposition(p) {
  const ds = hasDangerSign(p);
  if (ds === TRILEAN.true) return "urgent_referral";
  if (ds === TRILEAN.false) return "routine_follow_up";
  return "unknown";
}

function evalPatient(path) {
  const p = JSON.parse(readFileSync(path, "utf8"));
  return {
    patient_id: p.id,
    disposition: disposition(p),
    has_danger_sign: hasDangerSign(p),
    engine: "cql-prototype",
  };
}

const arg = process.argv[2];
if (!arg) {
  console.error("usage: node eval_cql.js <patient.json>");
  process.exit(1);
}

console.log(JSON.stringify(evalPatient(arg)));
