(* Source-only bridge: native SARAH tree solutions and restricted matching ASTs.
   Reads the static Python declaration report, never Get[]s SPheno.m. *)
ClearAll[PTreeFail, PTreeName, PTreeAST, PTreeRead, PTagentExportTreeRelations];
PTreeFail[tag_] := Throw[tag, "PTagentTreeRelations"];
PTreeName[x_] := ToString[x, InputForm];
PTreeAST[x_?NumberQ] := If[TrueQ[Im[N[x]] == 0],
  <|"op" -> "number", "value" -> N[x]|>, PTreeFail["complex_literal"]];
PTreeAST[x_Symbol] := <|"op" -> "symbol", "name" -> PTreeName[x]|>;
PTreeAST[x_List] := <|"op" -> "array", "args" -> (PTreeAST /@ x)|>;
PTreeAST[x_] := Module[{op},
  op = Switch[Head[x], Plus, "add", Times, "mul", Power, "pow",
    Sin, "sin", Cos, "cos", Tan, "tan", ArcTan, "atan", Sqrt, "sqrt",
    Exp, "exp", Log, "log", Abs, "abs", conj | Conjugate, "conj", _, ""];
  If[op === "", PTreeFail["unsupported_expression_head:" <> PTreeName[Head[x]]]];
  <|"op" -> op, "args" -> (PTreeAST /@ (List @@ x))|>
];
PTreeRead[text_String] := Module[{held, allowed, bad},
  If[StringLength[text] > 4096, PTreeFail["source_expression_too_large"]];
  held = ToExpression[text, InputForm, HoldComplete];
  allowed = {HoldComplete, Plus, Times, Power, Sin, Cos, Tan, ArcTan, Sqrt,
    Exp, Log, Abs, List, conj, Conjugate};
  bad = Count[held, x_ /; (!AtomQ[Unevaluated[x]] &&
    !MemberQ[allowed, Head[Unevaluated[x]]]), {0, Infinity}];
  If[bad > 0 || !FreeQ[held, _String] || LeafCount[held] > 512,
    PTreeFail["unsupported_static_expression"]];
  ReleaseHold[held]
];
PTagentExportTreeRelations[path_String, specPath_String] := Module[{payload},
  payload = Catch[Module[{spec, states, targets, equations, solutions, rows,
    symbols, bindings, dims, aliases, sourceHashes, node},
    spec = Import[specPath, "RawJSON"];
    If[!AssociationQ[spec], PTreeFail["invalid_declaration_report"]];
    states = Last[NameOfStates];
    targets = PTreeRead /@ spec["tadpole_parameters"];
    If[targets === {} || !And @@ (MatchQ[#, _Symbol] & /@ targets),
      PTreeFail["missing_scalar_tadpole_parameters"]];
    equations = TadpoleEquations[states];
    TimeConstrained[SolveTadpoleEquations[states, targets], 60,
      PTreeFail["native_tree_solution_timed_out"]];
    solutions = resTree;
    If[!MatchQ[solutions, {{_Rule ..} ..}], PTreeFail["no_explicit_tree_solutions"]];
    rows = Map[Function[row, <|"target" -> PTreeName[PTreeRead[row["parameter"]]],
      "expression" -> PTreeAST[PTreeRead[row["expression"]]],
      "source_location" -> row["source_location"]|>],
      Join[spec["boundary_conditions"], spec["matching_conditions"]]];
    symbols = DeleteDuplicates[Join[targets, PTreeRead /@ Lookup[rows, "target"]]];
    bindings = Table[
      dims = getDimParameter[symbol];
      If[!ListQ[dims] || !And @@ (IntegerQ[#] && # > 0 & /@ dims),
        PTreeFail["unsupported_parameter_shape:" <> PTreeName[symbol]]];
      If[dims === {} || Times @@ dims === 1,
        dims = {}; aliases = PTagentSourceString[symbol],
        If[Length[dims] > 2 || Times @@ dims > 64, PTreeFail["parameter_shape_limit"]];
        aliases = If[Length[dims] == 1,
          Table[PTagentSourceString[symbol[j]], {j, dims[[1]]}],
          Table[PTagentSourceString[symbol[j,k]], {j, dims[[1]]}, {k, dims[[2]]}]]];
      If[MemberQ[First /@ subVEVtoScalar, symbol] && LHBlockName[symbol] =!= "None",
        aliases = LHBlockName[symbol] <> "[" <> ToString[LHPos[symbol]] <> "]"];
      <|"source_symbol" -> PTreeName[symbol], "shape" -> dims, "aliases" -> aliases,
        "tadpole_parameter" -> MemberQ[targets, symbol]|>, {symbol, symbols}];
    sourceHashes = If[AssociationQ[spec["source"]], spec["source"], <||>];
    <|"schema" -> "ptagent.sarah_tree_relations.v1", "complete" -> True,
      "model" -> Model`Name, "relations" -> rows, "bindings" -> bindings,
      "tadpole_equations" -> (PTreeAST /@ equations),
      "tadpole_parameters" -> (PTreeName /@ targets),
      "tree_solutions" -> Map[Function[solution,
        Association[Map[(PTreeName[First[#]] -> PTreeAST[Last[#]]) &, solution]]], solutions],
      "loop_status" -> "not_evaluated", "blockers" -> {},
      "declaration_source" -> sourceHashes|>
  ], "PTagentTreeRelations", (<|"schema" -> "ptagent.sarah_tree_relations.v1",
    "complete" -> False, "blockers" -> {#1}|> &)];
  Export[path, payload, "RawJSON"];
  payload
];
