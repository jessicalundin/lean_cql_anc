import LeanCqlAnc.Basic

namespace LeanCqlAnc

-- Mirrors ANCDT01 "Should Proceed with ANC contact OR Referral":
-- the ANC.B5.DE48 observation value is in the Danger signs Choices valueset (DE50–DE62).
def HasDangerSign (p : PatientState) : Prop :=
  p.dangerSignStatus = .true

def hasDangerSignTrilean (p : PatientState) : Trilean :=
  p.dangerSignStatus

-- "Should Proceed with ANC contact OR Referral" → urgentReferral
def recommendsReferral (p : PatientState) : Trilean :=
  hasDangerSignTrilean p

-- "Should Proceed with ANC contact" → routineFollowUp (danger sign is false/DE49)
def recommendsRoutineFollowUp (p : PatientState) : Trilean :=
  Trilean.not (hasDangerSignTrilean p)

def disposition (p : PatientState) : Recommendation :=
  match hasDangerSignTrilean p with
  | .true    => .urgentReferral
  | .false   => .routineFollowUp
  | .unknown => .unknown

end LeanCqlAnc
