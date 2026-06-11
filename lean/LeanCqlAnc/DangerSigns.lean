import LeanCqlAnc.Basic

namespace LeanCqlAnc

/-- Any documented ANC danger sign in the prototype slice. -/
def HasDangerSign (p : PatientState) : Prop :=
  p.vaginalBleeding = .true ∨
  p.severeHeadache = .true ∨
  p.reducedFetalMovement = .true

def hasDangerSignTrilean (p : PatientState) : Trilean :=
  (p.vaginalBleeding.or p.severeHeadache).or p.reducedFetalMovement

def recommendsReferral (p : PatientState) : Trilean :=
  hasDangerSignTrilean p

def recommendsRoutineFollowUp (p : PatientState) : Trilean :=
  match hasDangerSignTrilean p with
  | .false => .true
  | .true => .false
  | .unknown => .unknown

def disposition (p : PatientState) : Recommendation :=
  match hasDangerSignTrilean p with
  | .true => .urgentReferral
  | .false => .routineFollowUp
  | .unknown => .unknown

end LeanCqlAnc
