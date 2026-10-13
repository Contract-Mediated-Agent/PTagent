(* Canonical component extraction from an already initialized SARAH model.
   No model names, benchmark points or Yukawa types are used here. *)
ClearAll[PTagentFail, PTagentExpandIndices, PTagentComponents, PTagentReal,
  PTagentSourceString, PTagentComponentData, PTagentCompleteScalarSectors];
PTagentFail[tag_String] := Throw[tag, "PTagentComponents"];
PTagentReal[x_] := Expand[x /. conj[z_] :> Conjugate[z]];
PTagentSourceString[x_] := ToString[InputForm[
  Simplify[x] //. subExtraTermsTree //. subout /. conj[z_] :> z]];

PTagentComponents[p_] := Module[{bare, dims, tuples},
  bare = p /. conj[z_] :> z;
  dims = getIndizesOldWI[bare];
  If[!MatchQ[dims, {{_Symbol, _Integer} ...}], PTagentFail["unsupported_component_indices"]];
  If[dims === {}, Return[{p}]];
  tuples = Tuples[Range /@ dims[[All, 2]]];
  If[Head[p] === conj, conj[bare[#]] & /@ tuples, bare[#] & /@ tuples]
];

(* SARAH 4.14's ++ writer visits only mixed sectors. Register unmixed scalar
   TreeMass expressions as identity sectors in memory before that writer runs. *)
PTagentCompleteScalarSectors[] := Module[{states = Last[NameOfStates], missing, masses},
  PTagentAddedScalarSectors = {};
  PTagentUnmixedSectorBlocker = False;
  missing = Select[Particles[states], #[[4]] === S && FreeQ[DEFINITION[states][MatterSector], #[[1]]] &];
  If[AnyTrue[missing, #[[5]] =!= {} &], PTagentUnmixedSectorBlocker = True; Return[False]];
  masses = (TreeMass[#[[1]], states] &) /@ missing;
  If[!FreeQ[masses, _TreeMass], PTagentUnmixedSectorBlocker = True; Return[False]];
  Do[
    AppendTo[DEFINITION[states][MatterSector], {{p[[1]]}, {p[[1]], PTagentIdentityRotation}}];
    AppendTo[MassMatrices[states], {{masses[[i]]}}];
    AppendTo[MassMatricesFull[states], {{masses[[i]]}}];
    AppendTo[PTagentAddedScalarSectors, ToString[p[[1]]]], {i, Length[missing]}, {p, {missing[[i]]}}];
  True
];

(* Expand every contracted index, including colour indices omitted from sum[]. *)
PTagentExpandIndices[expr_] := Module[{terms, particles, expandTerm},
  particles = Select[Particles[ALL], MemberQ[{S, F}, #[[4]]] &];
  expandTerm[term_] := Module[{ranges = {}, atoms, indices, dims, result, sums},
    sums = Cases[term, sum[i_, lo_Integer, hi_Integer] :> {i, lo, hi}, Infinity];
    ranges = sums;
    Do[
      atoms = With[{head = p[[1]]}, Cases[term, HoldPattern[head[inds_List]] :> inds, Infinity, Heads -> True]];
      dims = p[[5]];
      Do[
        If[Length[inds] =!= Length[dims], PTagentFail["component_index_rank"]];
        Do[If[Head[inds[[j]]] === Symbol,
          AppendTo[ranges, {inds[[j]], 1, dims[[j, 2]]}]], {j, Length[dims]}],
        {inds, atoms}], {p, particles}];
    ranges = DeleteDuplicates[ranges];
    If[Length[DeleteDuplicates[First /@ ranges]] =!= Length[ranges], PTagentFail["inconsistent_index_ranges"]];
    result = term /. sum[_, _Integer, _Integer] -> 1;
    Do[result = With[{index = r[[1]], lo = r[[2]], hi = r[[3]]}, Sum[result, {index, lo, hi}]], {r, ranges}];
    result /. Delta[a_Integer, b_Integer] :> KroneckerDelta[a, b]
  ];
  terms = Expand[expr];
  If[Head[terms] === Plus, Total[expandTerm /@ (List @@ terms)], expandTerm[terms]]
];

PTagentComponentData[] := Catch[Module[
  {states = Last[NameOfStates], definitions, scalarRules = {}, qs = {}, mappings = {},
   scalarParents = {}, basis, count, indices, real, variables, allScalars, potential,
   background, backgroundIndices, scalarGauge, fermions, left, right, fRules,
   fermionLag, mass, traceF, scalarSources = {}, gaugeGroups, gaugeBasis = {},
   gaugeMappings = {}, gaugeAdjoint = {}, gaugeScalar = {}, gaugeFermion = {},
   group, coupling, ca, ts, tf, dims, dim, cas, f, rawGauge, groupIndex, payload},
  If[!TrueQ[SupersymmetricModel === False], PTagentFail["supersymmetric_components_not_supported"]];
  If[TrueQ[PTagentUnmixedSectorBlocker], PTagentFail["unmixed_scalar_indices_not_supported"]];
  If[TrueQ[SA`CPViolationHiggsSector], PTagentFail["cp_violating_scalar_basis_not_supported"]];
  If[Count[Gauge, {_, U[1], ___}] > 1, PTagentFail["abelian_gauge_mixing_not_supported"]];
  definitions = DEFINITION[states][MatterSector];
  Do[
    If[getTypeOld[def[[2, 1]]] === S,
      basis = Flatten[PTagentComponents /@ def[[1]]];
      real = And @@ (TrueQ[conj[#] === #] & /@ basis);
      If[!real && AnyTrue[basis, TrueQ[conj[#] === #] &], PTagentFail["mixed_real_complex_scalar_sector"]];
      count = Length[basis];
      variables = Table[Symbol["ptq" <> ToString[Length[qs] + j]], {j, If[real, count, 2 count]}];
      indices = Range[Length[qs], Length[qs] + Length[variables] - 1];
      AppendTo[mappings, <|"source_index" -> i, "indices" -> indices|>];
      qs = Join[qs, variables];
      scalarParents = Join[scalarParents, If[real, basis, Join[basis, basis]]];
      Do[
        f = basis[[j]];
        If[real, AppendTo[scalarRules, f -> variables[[j]]],
          scalarRules = Join[scalarRules, {f -> (variables[[j]] + I variables[[j + count]])/Sqrt[2],
            conj[f] -> (variables[[j]] - I variables[[j + count]])/Sqrt[2]}]], {j, count}];
      AppendTo[scalarSources, ToString[InputForm[def[[1]]]]]
    ], {i, Length[definitions]}, {def, {definitions[[i]]}}];
  If[qs === {} || Length[qs] > 64, PTagentFail["scalar_basis_size_not_supported"]];
  count = Total[Table[row[[2]] If[TrueQ[conj[row[[3]]] === row[[3]]], 1, 2] *
    Times @@ Table[If[Head[Gauge[[g, 2]]] === U, 1, Abs[row[[3 + g]]]], {g, Length[Gauge]}], {row, ScalarFields}]];
  If[count =!= Length[qs], PTagentFail["incomplete_canonical_scalar_basis"]];
  (* The potential saved before mass rotations retains every scalar component. *)
  potential = PTagentExpandIndices[potSaveTadpoleEquations /. vacuumF /. zero[_] -> 0 /. RXi[_] -> 0];
  potential = Expand[potential /. (Rule[First[#], 0] & /@ subVEVtoScalar) /. HoldPattern[0[___]] -> 0 /. scalarRules];
  potential = ComplexExpand[PTagentReal[potential]];
  If[!FreeQ[potential, _Delta | _sum | _conj | _Conjugate | _Re | _Im], PTagentFail["unresolved_scalar_components"]];
  backgroundIndices = Table[
    f = Last[rule] /. scalarRules;
    If[!MemberQ[qs, f], PTagentFail["noncanonical_background_mapping"]];
    First[First[Position[qs, f]]] - 1, {rule, subVEVtoScalar}];
  background = MapThread[Rule, {qs, ConstantArray[0, Length[qs]]}];
  Do[background[[backgroundIndices[[j]] + 1]] = qs[[backgroundIndices[[j]] + 1]] -> Last[subVEVtoScalar[[j]]], {j, Length[subVEVtoScalar]}];
  scalarGauge = DiagonalMatrix[Table[Sum[
    Gauge[[g, 4]]^2 SA`Casimir[getBlank[scalarParents[[j]]], Gauge[[g, 3]]]/If[Head[Gauge[[g, 2]]] === U, GUTren[g]^2, 1],
    {g, Length[Gauge]}], {j, Length[qs]}]];

  (* Left-handed Weyl components, including massless members of multiplets. *)
  fermions = Flatten[PTagentComponents /@ Flatten[FermionFields[[All, 1]]]];
  If[Length[fermions] > 128, PTagentFail["fermion_basis_size_not_supported"]];
  left = Table[Symbol["ptf" <> ToString[j]], {j, Length[fermions]}];
  right = Table[Symbol["ptt" <> ToString[j]], {j, Length[fermions]}];
  fRules = Flatten[Table[
    f = fermions[[j]];
    {If[Head[f] === conj, conj[f[[1]][1]], f[1]] -> left[[j]],
     If[Head[f] === conj, conj[f[[1]][2]], f[2]] -> right[[j]],
     If[Head[f] === conj, f[[1]][1], conj[f[1]]] -> 0,
     If[Head[f] === conj, f[[1]][2], conj[f[2]]] -> 0}, {j, Length[fermions]}]];
  fermionLag = PTagentExpandIndices[potSaveTadpoleEquations /. RXi[_] -> 0];
  fermionLag = Expand[fermionLag /. (Rule[First[#], 0] & /@ subVEVtoScalar) /. HoldPattern[0[___]] -> 0 /. scalarRules /. fRules];
  mass = Table[D[fermionLag, left[[i]], right[[j]]], {i, Length[left]}, {j, Length[right]}];
  If[!TrueQ[Simplify[mass - Transpose[mass]] === ConstantArray[0, Dimensions[mass]]], PTagentFail["weyl_mass_matrix_not_symmetric"]];
  If[!FreeQ[mass, Alternatives @@ Join[left, right]], PTagentFail["nonrenormalizable_fermion_interactions"]];
  traceF = Simplify[Total[Flatten[ComplexExpand[mass Conjugate[mass]]]]];

  gaugeGroups = Table[
    group = Gauge[[g]]; coupling = group[[4]];
    If[Head[group[[2]]] === U, ca = 0; dim = 1,
      If[Head[group[[2]]] =!= SU, PTagentFail["gauge_group_not_supported"]];
      ca = group[[2, 1]]; dim = ca^2 - 1];
    ts = Total[Table[
      dims = Table[If[Head[Gauge[[k, 2]]] === U, 1, Abs[row[[3+k]]]], {k, Length[Gauge]}];
      cas = If[Head[group[[2]]] === U, row[[3+g]]^2, SA`Casimir[row[[3]], group[[3]]]];
      row[[2]] Times @@ dims cas/dim If[TrueQ[conj[row[[3]]] === row[[3]]], 1/2, 1], {row, ScalarFields}]];
    tf = Total[Table[
      dims = Table[If[Head[Gauge[[k, 2]]] === U, 1, Abs[row[[3+k]]]], {k, Length[Gauge]}];
      cas = If[Head[group[[2]]] === U, row[[3+g]]^2, SA`Casimir[row[[3]], group[[3]]]];
      row[[2]] Times @@ dims cas/dim, {row, FermionFields}]];
    <|"name" -> ToString[group[[3]]], "vector" -> ToString[Gauge[[g, 1]]],
      "coupling" -> PTagentSourceString[coupling], "adjoint" -> ca,
      "scalar_trace" -> ts, "weyl_trace" -> tf|>, {g, Length[Gauge]}];
  Do[
    basis = DEFINITION[states][GaugeSector][[i, 1]];
    indices = Range[Length[gaugeBasis], Length[gaugeBasis] + Length[basis] - 1];
    AppendTo[gaugeMappings, <|"source_index" -> Length[definitions] + i, "indices" -> indices|>];
    Do[
      rawGauge = getBlank[f];
      groupIndex = Select[Range[Length[Gauge]], ToString[rawGauge] === "V" <> ToString[Gauge[[#, 1]]] &];
      If[Length[groupIndex] =!= 1, PTagentFail["gauge_basis_mapping_not_supported"]];
      group = gaugeGroups[[First[groupIndex]]]; coupling = Gauge[[First[groupIndex], 4]];
      AppendTo[gaugeBasis, ToString[InputForm[f]]];
      AppendTo[gaugeAdjoint, coupling^2 group["adjoint"]];
      AppendTo[gaugeScalar, coupling^2 group["scalar_trace"]];
      AppendTo[gaugeFermion, coupling^2 group["weyl_trace"]], {f, basis}],
    {i, Length[DEFINITION[states][GaugeSector]]}];
  payload = <|"schema" -> "ptagent.sarah_components.v1", "complete" -> True,
    "background_fields" -> (ToString /@ (Last /@ subVEVtoScalar)),
    "scalar_basis" -> (ToString /@ qs), "background_scalar_indices" -> backgroundIndices,
    "full_scalar_potential" -> PTagentSourceString[potential],
    "weyl_mass_squared_trace" -> PTagentSourceString[traceF],
    "scalar_gauge" -> Map[PTagentSourceString, scalarGauge, {2}],
    "gauge_basis" -> gaugeBasis, "gauge_groups" -> gaugeGroups,
    "gauge_adjoint" -> Map[PTagentSourceString, DiagonalMatrix[gaugeAdjoint], {2}],
    "gauge_scalar_trace" -> Map[PTagentSourceString, DiagonalMatrix[gaugeScalar], {2}],
    "gauge_fermion_trace" -> Map[PTagentSourceString, DiagonalMatrix[gaugeFermion], {2}],
    "sector_mappings" -> <|"scalar" -> mappings, "gauge" -> gaugeMappings|>,
    "fermion_components" -> (ToString[InputForm[#]] & /@ fermions),
    "scalar_source_bases" -> scalarSources,
    "source_tree" -> PTagentSourceString[potential /. background],
    "unmixed_scalar_sectors" -> If[ValueQ[PTagentAddedScalarSectors], PTagentAddedScalarSectors, {}],
    "sarah_version" -> ToString[SA`Version]|>;
  payload
], "PTagentComponents", (<|"schema" -> "ptagent.sarah_components.v1", "complete" -> False, "blockers" -> {#1}|> &)];
