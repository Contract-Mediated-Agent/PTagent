(* PTagent SARAH companion exporter, schema version 1.
   This file reads SARAH state after MakeVevacious[Version -> "++"]. *)

ClearAll[PTagentString, PTagentMatrixStrings, PTagentJSONSafe, PTagentExportThermalMetadata];

PTagentString[x_] := ToString[InputForm[x]];
PTagentMatrixStrings[m_] := Map[PTagentString, m, {2}];
PTagentJSONSafe[x_Association] := Map[PTagentJSONSafe, x];
PTagentJSONSafe[x_List] := PTagentJSONSafe /@ x;
PTagentJSONSafe[x_String] := x;
PTagentJSONSafe[x_?NumberQ] := x;
PTagentJSONSafe[True] := True;
PTagentJSONSafe[False] := False;
PTagentJSONSafe[Null] := Null;
PTagentJSONSafe[x_] := PTagentString[x];

PTagentExportThermalMetadata[path_String] := Module[
  {fields, scalarPi, gaugePi, scalarBasis, gaugeBasis, mappings, complete, blockers, payload,
   representationData, couplingTensors, basisMappings, conventions, symmetries, consistency},
  fields = If[ValueQ[subVEVtoScalar], PTagentString /@ (Last /@ subVEVtoScalar), {}];

  (* A SARAH model can provide these deterministic tensors in SPheno.m or an
     auxiliary model file. Their normalization is fixed by the JSON schema and
     is checked again by the Python importer. No natural-language inference is
     used when they are absent. *)
  scalarBasis = If[ValueQ[PTagentScalarThermalBasis], PTagentString /@ PTagentScalarThermalBasis, fields];
  gaugeBasis = If[ValueQ[PTagentGaugeThermalBasis], PTagentString /@ PTagentGaugeThermalBasis, {}];
  scalarPi = If[ValueQ[PTagentScalarThermalInvariants], PTagentScalarThermalInvariants, <||>];
  gaugePi = If[ValueQ[PTagentGaugeThermalInvariants], PTagentGaugeThermalInvariants, <||>];
  mappings = If[ValueQ[PTagentThermalSectorMappings], PTagentThermalSectorMappings, <||>];
  representationData = If[
    ValueQ[PTagentRepresentationData],
    PTagentRepresentationData,
    <|
      "scalar_fields" -> If[ValueQ[ScalarFields], ScalarFields, {}],
      "fermion_fields" -> If[ValueQ[FermionFields], FermionFields, {}],
      "gauge_fields" -> If[ValueQ[Gauge], Gauge, {}]
    |>
  ];
  couplingTensors = If[ValueQ[PTagentCouplingTensors], PTagentCouplingTensors, <||>];
  basisMappings = If[
    ValueQ[PTagentBasisMappings],
    PTagentBasisMappings,
    <|"subVEVtoScalar" -> If[ValueQ[subVEVtoScalar], subVEVtoScalar, {}]|>
  ];
  conventions = If[
    ValueQ[PTagentConventions],
    PTagentConventions,
    <|"parameter_domain" -> "real", "gauge_fixing" -> "Landau", "fermion_basis" -> "Weyl"|>
  ];
  symmetries = If[ValueQ[PTagentDiscreteSymmetries], PTagentDiscreteSymmetries, {}];
  consistency = If[ValueQ[PTagentThermalConsistency], PTagentThermalConsistency, <||>];
  complete = AssociationQ[scalarPi] && AssociationQ[gaugePi] && Length[gaugeBasis] > 0 &&
    AssociationQ[mappings] && Length[representationData] > 0 && Length[couplingTensors] > 0 &&
    Length[basisMappings] > 0 && Length[conventions] > 0 && Length[consistency] > 0;
  blockers = If[complete, {}, {"thermal_tensors_not_exported_by_sarah_model"}];

  payload = <|
    "schema" -> "ptagent.sarah_thermal.v1",
    "complete" -> complete,
    "blockers" -> blockers,
    "background_fields" -> fields,
    "scalar_basis" -> scalarBasis,
    "gauge_basis" -> gaugeBasis,
    "gauge_groups" -> If[ValueQ[Gauge], PTagentString /@ Gauge, {}],
    "scalar_components" -> (Association["name" -> #] & /@ scalarBasis),
    "fermion_components" -> If[ValueQ[PTagentFermionComponents], PTagentFermionComponents, {}],
    "representation_data" -> representationData,
    "coupling_tensors" -> couplingTensors,
    "basis_mappings" -> basisMappings,
    "conventions" -> conventions,
    "discrete_symmetries" -> symmetries,
    "thermal_invariants" -> <|
      "scalar_quartic" -> Lookup[scalarPi, "scalar_quartic", {}],
      "scalar_gauge" -> Lookup[scalarPi, "scalar_gauge", {}],
      "scalar_yukawa" -> Lookup[scalarPi, "scalar_yukawa", {}],
      "gauge_adjoint" -> Lookup[gaugePi, "gauge_adjoint", {}],
      "gauge_scalar_trace" -> Lookup[gaugePi, "gauge_scalar_trace", {}],
      "gauge_fermion_trace" -> Lookup[gaugePi, "gauge_fermion_trace", {}]
    |>,
    "sector_mappings" -> mappings,
    "consistency" -> consistency,
    "provenance" -> <|
      "exporter_version" -> "ptagent-sarah-exporter-1",
      "sarah_version" -> If[ValueQ[SA`Version], PTagentString[SA`Version], "unknown"],
      "source_hashes" -> <|"vevacious_output" -> "recorded-by-python-wrapper"|>
    |>
  |>;
  Export[path, PTagentJSONSafe[payload], "RawJSON"];
  payload
];
