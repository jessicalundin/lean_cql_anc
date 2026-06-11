import LeanCqlAnc.DangerSigns

namespace LeanCqlAnc

private theorem trilean_or_true_left (t : Trilean) : Trilean.true.or t = Trilean.true := by
  cases t <;> rfl

private theorem trilean_or_true_right (t : Trilean) : t.or Trilean.true = Trilean.true := by
  cases t <;> rfl

theorem danger_sign_implies_referral (p : PatientState) (h : HasDangerSign p) :
    disposition p = .urgentReferral := by
  rcases h with h | h | h
  · simp [disposition, hasDangerSignTrilean, h, trilean_or_true_left, trilean_or_true_right]
  · simp [disposition, hasDangerSignTrilean, Trilean.or, h, trilean_or_true_right]
  · simp [disposition, hasDangerSignTrilean, Trilean.or, h, trilean_or_true_right]

theorem no_contradictory_recommendations (p : PatientState) :
    ¬ (recommendsRoutineFollowUp p = .true ∧ recommendsReferral p = .true) := by
  intro ⟨hr, href⟩
  rcases hp : hasDangerSignTrilean p with h | h | h
  · simp [recommendsRoutineFollowUp, recommendsReferral, hp] at hr
  · simp [recommendsRoutineFollowUp, recommendsReferral, hp] at href
  · simp [recommendsRoutineFollowUp, recommendsReferral, hp] at hr

theorem unknown_not_false (t : Trilean) (h : t = .unknown) : t ≠ .false := by
  simpa [h]

end LeanCqlAnc
