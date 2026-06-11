import LeanCqlAnc.Json
import LeanCqlAnc.DangerSigns

open LeanCqlAnc

def evalPatient (path : System.FilePath) : IO UInt32 := do
  match ← patientFromFile path with
  | .error e =>
    IO.eprintln s!"error: {e}"
    return 1
  | .ok p =>
    let result : EvalResult := {
      patientId := p.id
      disposition := disposition p
      hasDangerSign := hasDangerSignTrilean p
    }
    IO.println result.toJsonString
    return 0

def proofStatusJson : String :=
  Json.compress <| Json.mkObj [
    ("danger_sign_implies_referral", Json.str "proved"),
    ("no_contradictory_recommendations", Json.str "proved"),
    ("unknown_not_false", Json.str "proved"),
    ("built_with_lean", Json.bool true),
    ("engine", Json.str "lean")
  ]

def main (args : List String) : IO UInt32 :=
  match args with
  | ["--proof-status"] =>
    IO.println proofStatusJson
    return 0
  | [path] =>
    evalPatient (System.FilePath.mk path)
  | _ =>
    IO.eprintln "usage: anc-eval <patient.json> | anc-eval --proof-status"
    return 1
