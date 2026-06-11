namespace LeanCqlAnc

/-- Three-valued logic matching CQL nullology (true / false / unknown). -/
inductive Trilean where
  | true
  | false
  | unknown
  deriving Repr, Inhabited, DecidableEq

def Trilean.and : Trilean → Trilean → Trilean
  | .false, _ => .false
  | _, .false => .false
  | .unknown, _ => .unknown
  | _, .unknown => .unknown
  | .true, .true => .true

def Trilean.or : Trilean → Trilean → Trilean
  | .true, _ => .true
  | _, .true => .true
  | .unknown, _ => .unknown
  | _, .unknown => .unknown
  | .false, .false => .false

def Trilean.not : Trilean → Trilean
  | .true => .false
  | .false => .true
  | .unknown => .unknown

def Trilean.toBool? : Trilean → Option Bool
  | .true => some true
  | .false => some false
  | .unknown => none

structure PatientState where
  id : String
  gestationalAgeWeeks : Option Nat
  vaginalBleeding : Trilean
  severeHeadache : Trilean
  reducedFetalMovement : Trilean
  deriving Repr, Inhabited

inductive Recommendation where
  | routineFollowUp
  | urgentReferral
  | unknown
  deriving Repr, Inhabited, DecidableEq

def Recommendation.toJsonString : Recommendation → String
  | .routineFollowUp => "\"routine_follow_up\""
  | .urgentReferral => "\"urgent_referral\""
  | .unknown => "\"unknown\""

end LeanCqlAnc
