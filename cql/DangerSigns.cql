/*
  WHO SMART ANC — Danger signs (ANCDT01 / ANC.B5 Quick Check).
  Source: https://build.fhir.org/ig/WorldHealthOrganization/smart-anc/

  Implements the ANCDT01 decision table using the official WHO ANC custom
  code system (http://fhir.org/guides/who/anc-cds/CodeSystem/anc-custom-codes)
  with data element codes ANC.B5.DE3–DE14.

  This is a self-contained prototype that mirrors ANCDT01 logic using
  individual boolean Observations (one per assessed danger sign), coded
  with the official WHO ANC data element codes. This avoids the full
  ANCDT01.cql dependency chain (ANCDataElements, ANCConcepts, WHOCommon,
  etc.) while remaining terminology-faithful to the WHO IG.

  The full ANCDT01.cql uses a multi-select Observation pattern where
  O.code = ANC.B5.DE48 (Danger signs) and O.value = the specific sign.
  That pattern requires a FHIR terminology server for value set resolution.
  This library uses the same codes in a boolean Observation pattern that
  works with the Google CQL standalone engine.

  Evaluated by the Google CQL engine against FHIR R4 patient bundles.
  Observation codings match fixtures/patients/ and the LLM extraction prompt.
*/
library DangerSigns version '0.2.0'

using FHIR version '4.0.1'

codesystem "ANC Custom Codes": 'http://fhir.org/guides/who/anc-cds/CodeSystem/anc-custom-codes'
codesystem "LOINC": 'http://loinc.org'

// ── ANC.B5 Danger sign codes ────────────────────────────────────────────────
// Source: https://build.fhir.org/ig/WorldHealthOrganization/smart-anc/CodeSystem-anc-custom-codes.html
code "Bleeding Vaginally":    'ANC.B5.DE3'  from "ANC Custom Codes" display 'Bleeding vaginally'
code "Central Cyanosis":      'ANC.B5.DE4'  from "ANC Custom Codes" display 'Central cyanosis'
code "Convulsing":            'ANC.B5.DE5'  from "ANC Custom Codes" display 'Convulsing'
code "Fever":                 'ANC.B5.DE6'  from "ANC Custom Codes" display 'Fever'
code "Severe Headache":       'ANC.B5.DE7'  from "ANC Custom Codes" display 'Severe headache'
code "Visual Disturbance":    'ANC.B5.DE8'  from "ANC Custom Codes" display 'Visual disturbance'
code "Imminent Delivery":     'ANC.B5.DE9'  from "ANC Custom Codes" display 'Imminent delivery'
code "Labour":                'ANC.B5.DE10' from "ANC Custom Codes" display 'Labour'
code "Looks Very Ill":        'ANC.B5.DE11' from "ANC Custom Codes" display 'Looks very ill'
code "Severe Vomiting":       'ANC.B5.DE12' from "ANC Custom Codes" display 'Severe vomiting'
code "Severe Pain":           'ANC.B5.DE13' from "ANC Custom Codes" display 'Severe pain'
code "Severe Abdominal Pain": 'ANC.B5.DE14' from "ANC Custom Codes" display 'Severe abdominal pain'
code "Gestational Age":       '49051-6'     from "LOINC"            display 'Gestational age in weeks'

// ── Context ─────────────────────────────────────────────────────────────────
context Patient

// ── Individual sign defines ─────────────────────────────────────────────────
define "Has Bleeding Vaginally":
  exists ([Observation: "Bleeding Vaginally"] O
    where O.status in { 'final', 'amended', 'corrected' }
      and (O.value as FHIR.boolean).value = true)

define "Has Central Cyanosis":
  exists ([Observation: "Central Cyanosis"] O
    where O.status in { 'final', 'amended', 'corrected' }
      and (O.value as FHIR.boolean).value = true)

define "Is Convulsing":
  exists ([Observation: "Convulsing"] O
    where O.status in { 'final', 'amended', 'corrected' }
      and (O.value as FHIR.boolean).value = true)

define "Has Fever":
  exists ([Observation: "Fever"] O
    where O.status in { 'final', 'amended', 'corrected' }
      and (O.value as FHIR.boolean).value = true)

define "Has Severe Headache":
  exists ([Observation: "Severe Headache"] O
    where O.status in { 'final', 'amended', 'corrected' }
      and (O.value as FHIR.boolean).value = true)

define "Has Visual Disturbance":
  exists ([Observation: "Visual Disturbance"] O
    where O.status in { 'final', 'amended', 'corrected' }
      and (O.value as FHIR.boolean).value = true)

define "Imminent Delivery Indicated":
  exists ([Observation: "Imminent Delivery"] O
    where O.status in { 'final', 'amended', 'corrected' }
      and (O.value as FHIR.boolean).value = true)

define "In Labour":
  exists ([Observation: "Labour"] O
    where O.status in { 'final', 'amended', 'corrected' }
      and (O.value as FHIR.boolean).value = true)

define "Looks Very Ill":
  exists ([Observation: "Looks Very Ill"] O
    where O.status in { 'final', 'amended', 'corrected' }
      and (O.value as FHIR.boolean).value = true)

define "Has Severe Vomiting":
  exists ([Observation: "Severe Vomiting"] O
    where O.status in { 'final', 'amended', 'corrected' }
      and (O.value as FHIR.boolean).value = true)

define "Has Severe Pain":
  exists ([Observation: "Severe Pain"] O
    where O.status in { 'final', 'amended', 'corrected' }
      and (O.value as FHIR.boolean).value = true)

define "Has Severe Abdominal Pain":
  exists ([Observation: "Severe Abdominal Pain"] O
    where O.status in { 'final', 'amended', 'corrected' }
      and (O.value as FHIR.boolean).value = true)

// ── ANCDT01: "Should Proceed with ANC contact OR Referral" ──────────────────
// Central cyanosis is a special case: triggers referral OR continued contact
define "Has Danger Sign":
  "Has Bleeding Vaginally"
    or "Is Convulsing"
    or "Has Fever"
    or "Has Severe Headache"
    or "Has Visual Disturbance"
    or "Imminent Delivery Indicated"
    or "In Labour"
    or "Looks Very Ill"
    or "Has Severe Vomiting"
    or "Has Severe Pain"
    or "Has Severe Abdominal Pain"

define "Recommend Urgent Referral":
  "Has Danger Sign" = true

define "Recommend Routine Follow Up":
  "Has Danger Sign" = false

// Central cyanosis: proceed with ANC contact OR refer (ANCDT01 special case)
define "Has Central Cyanosis Indicator":
  "Has Central Cyanosis"

define "Gestational Age Weeks":
  First(
    [Observation: "Gestational Age"] O
      where O.status in { 'final', 'amended', 'corrected' }
      return (O.value as FHIR.Quantity).value
  )
