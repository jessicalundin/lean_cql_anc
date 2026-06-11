/*
  WHO SMART ANC — danger sign slice (v0.1).
  Evaluated by the Google CQL engine against FHIR R4 patient bundles.
  Cross-checked against the Lean 4 formal model (see lean/).

  SNOMED codes used:
    289530006 — Vaginal bleeding
    25064002  — Severe headache
    386281004 — Reduced fetal movement
*/
library DangerSigns version '0.1.0'

using FHIR version '4.0.1'

codesystem "SNOMED-CT": 'http://snomed.info/sct'
codesystem "LOINC": 'http://loinc.org'

code "Vaginal Bleeding Code": '289530006' from "SNOMED-CT" display 'Vaginal bleeding'
code "Severe Headache Code": '25064002' from "SNOMED-CT" display 'Severe headache'
code "Reduced Fetal Movement Code": '386281004' from "SNOMED-CT" display 'Reduced fetal movement'
code "Gestational Age Code": '49051-6' from "LOINC" display 'Gestational age in weeks'

context Patient

define "Has Vaginal Bleeding":
  exists (
    [Observation: "Vaginal Bleeding Code"] O
      where O.status in { 'final', 'amended' }
        and (O.value as FHIR.boolean).value = true
  )

define "Has Severe Headache":
  exists (
    [Observation: "Severe Headache Code"] O
      where O.status in { 'final', 'amended' }
        and (O.value as FHIR.boolean).value = true
  )

define "Has Reduced Fetal Movement":
  exists (
    [Observation: "Reduced Fetal Movement Code"] O
      where O.status in { 'final', 'amended' }
        and (O.value as FHIR.boolean).value = true
  )

define "Has Danger Sign":
  "Has Vaginal Bleeding"
    or "Has Severe Headache"
    or "Has Reduced Fetal Movement"

define "Recommend Urgent Referral":
  "Has Danger Sign" = true

define "Recommend Routine Follow Up":
  "Has Danger Sign" = false

define "Gestational Age Weeks":
  First(
    [Observation: "Gestational Age Code"] O
      where O.status in { 'final', 'amended' }
      return (O.value as FHIR.Quantity).value
  )
