import LeanCqlAnc.DangerSigns

namespace LeanCqlAnc

theorem danger_sign_implies_referral (p : PatientState) (h : HasDangerSign p) :
    disposition p = .urgentReferral := by
  simp [HasDangerSign] at h
  simp [disposition, hasDangerSignTrilean, h]

theorem no_contradictory_recommendations (p : PatientState) :
    ¬ (recommendsRoutineFollowUp p = .true ∧ recommendsReferral p = .true) := by
  intro ⟨hr, href⟩
  simp [recommendsRoutineFollowUp, recommendsReferral, hasDangerSignTrilean, Trilean.not] at hr
  simp [recommendsReferral, hasDangerSignTrilean] at href
  rw [href] at hr
  simp [Trilean.not] at hr

theorem unknown_not_false (t : Trilean) (h : t = .unknown) : t ≠ .false := by
  simpa [h]

end LeanCqlAnc
