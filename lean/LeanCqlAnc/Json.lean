import Lean.Json
import LeanCqlAnc.Basic

namespace LeanCqlAnc

def trileanToString : Trilean → String
  | .true    => "true"
  | .false   => "false"
  | .unknown => "unknown"

def parseTrilean (j : Json) : Except String Trilean :=
  match j.getStr? with
  | some "true"    => .ok .true
  | some "false"   => .ok .false
  | some "unknown" => .ok .unknown
  | _              => .error "expected true, false, or unknown"

def parseTrileanField (j : Json) (field : String) : Except String Trilean :=
  match j.getObjVal? field with
  | .ok v    => parseTrilean v
  | .error _ => .ok .unknown

def parseGestationalAge (j : Json) : Except String (Option Nat) :=
  match j.getObjVal? "gestational_age_weeks" with
  | .error _      => .ok none
  | .ok (.null _) => .ok none
  | .ok v =>
    match v.getNat? with
    | some n => .ok (some n)
    | none   => .error "gestational_age_weeks must be a number or null"

-- Input JSON format (produced by bundle_to_lean_json in app.py):
-- { "id": "...", "gestational_age_weeks": 28, "danger_sign_status": "false" }
def parsePatient (j : Json) : Except String PatientState := do
  let id := j.getObjValAs? String "id" |>.getD "unknown"
  let ga ← parseGestationalAge j
  let ds ← parseTrileanField j "danger_sign_status"
  return { id, gestationalAgeWeeks := ga, dangerSignStatus := ds }

def patientFromFile (path : System.FilePath) : IO (Except String PatientState) := do
  let contents ← IO.FS.readFile path
  match Json.parse contents with
  | .ok j    => pure (parsePatient j)
  | .error e => pure (.error e)

structure EvalResult where
  patientId    : String
  disposition  : Recommendation
  hasDangerSign : Trilean
  engine       : String := "lean"

def EvalResult.toJson (r : EvalResult) : Json :=
  Json.mkObj [
    ("patient_id",     Json.str r.patientId),
    ("disposition",    Json.str (match r.disposition with
      | .routineFollowUp => "routine_follow_up"
      | .urgentReferral  => "urgent_referral"
      | .unknown         => "unknown")),
    ("has_danger_sign", Json.str (trileanToString r.hasDangerSign)),
    ("engine",         Json.str r.engine)
  ]

def EvalResult.toJsonString (r : EvalResult) : String :=
  r.toJson.compress

end LeanCqlAnc
