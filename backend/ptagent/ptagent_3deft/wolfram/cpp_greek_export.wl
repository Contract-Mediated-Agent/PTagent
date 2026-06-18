(* ::Package:: *)

(* Utilities for exporting Mathematica expressions with Greek-letter symbols
   into C++-friendly ASCII code.  This file is intentionally standalone so a
   DRalgo notebook or .m file can load it before calling CForm. *)

ClearAll[
  cppGreekRules,
  cppPiVariableRules,
  cppGreekNameRules,
  cppPiNameRules,
  cppReservedIdentifierRules,
  cppStdFunctionRules,
  cppGreekActiveRules,
  cppGreekActiveNameRules,
  cppRepairReservedIdentifier,
  cppCapitalizeIdentifierPart,
  cppTemporaryIdentifier,
  cppAsciiIdentifier,
  cppAsciiSymbol,
  cppAsciiSymbolPlan,
  cppRenameGreekSymbols,
  cppRenameGreekSymbolsWithPostRules,
  toCpp,
  toCppStd,
  cppGreekExamples,
  cppGreekTests
];

(* Exact Greek-symbol replacement rules.  These are useful for ordinary symbols
   such as \[Alpha], \[CurlyPhi], and \[CapitalLambda]. *)
cppGreekRules = {
  \[Alpha] -> alpha,
  \[Beta] -> beta,
  \[Gamma] -> gamma,
  \[Delta] -> delta,
  \[Epsilon] -> epsilon,
  \[CurlyEpsilon] -> curlyEpsilon,
  \[Zeta] -> zeta,
  \[Eta] -> eta,
  \[Theta] -> theta,
  \[CurlyTheta] -> curlyTheta,
  \[Iota] -> iota,
  \[Kappa] -> kappa,
  \[CurlyKappa] -> curlyKappa,
  \[Lambda] -> lam,
  \[Mu] -> mu,
  \[Nu] -> nu,
  \[Xi] -> xi,
  \[Omicron] -> omicron,
  \[Rho] -> rho,
  \[CurlyRho] -> curlyRho,
  \[Sigma] -> sigma,
  \[FinalSigma] -> finalSigma,
  \[Tau] -> tau,
  \[Upsilon] -> upsilon,
  \[Phi] -> phi,
  \[CurlyPhi] -> curlyPhi,
  \[Chi] -> chi,
  \[Psi] -> psi,
  \[Omega] -> omega,
  \[CapitalAlpha] -> capitalAlpha,
  \[CapitalBeta] -> capitalBeta,
  \[CapitalGamma] -> capitalGamma,
  \[CapitalDelta] -> capitalDelta,
  \[CapitalEpsilon] -> capitalEpsilon,
  \[CapitalZeta] -> capitalZeta,
  \[CapitalEta] -> capitalEta,
  \[CapitalTheta] -> capitalTheta,
  \[CapitalIota] -> capitalIota,
  \[CapitalKappa] -> capitalKappa,
  \[CapitalLambda] -> capitalLam,
  \[CapitalMu] -> capitalMu,
  \[CapitalNu] -> capitalNu,
  \[CapitalXi] -> capitalXi,
  \[CapitalOmicron] -> capitalOmicron,
  \[CapitalPi] -> capitalPi,
  \[CapitalRho] -> capitalRho,
  \[CapitalSigma] -> capitalSigma,
  \[CapitalTau] -> capitalTau,
  \[CapitalUpsilon] -> capitalUpsilon,
  \[CapitalPhi] -> capitalPhi,
  \[CapitalChi] -> capitalChi,
  \[CapitalPsi] -> capitalPsi,
  \[CapitalOmega] -> capitalOmega
};

(* Pi is a mathematical constant in Mathematica, so it is not renamed by
   default.  Enable "TreatPiAsVariable" only when Pi-like symbols are model
   variables rather than constants. *)
cppPiVariableRules = {
  \[Pi] -> pi,
  \[CurlyPi] -> curlyPi
};

(* String-level rules let us rename compound symbols such as \[Lambda]1H,
   \[Mu]S11, Y\[Phi], or c\[Theta], which exact replacement rules cannot
   catch because Mathematica treats each of those as a single Symbol. *)
cppGreekNameRules = {
  "\\[Alpha]" -> "alpha",
  "\[Alpha]" -> "alpha",
  "\\[Beta]" -> "beta",
  "\[Beta]" -> "beta",
  "\\[Gamma]" -> "gamma",
  "\[Gamma]" -> "gamma",
  "\\[Delta]" -> "delta",
  "\[Delta]" -> "delta",
  "\\[Epsilon]" -> "epsilon",
  "\[Epsilon]" -> "epsilon",
  "\\[CurlyEpsilon]" -> "curlyEpsilon",
  "\[CurlyEpsilon]" -> "curlyEpsilon",
  "\\[Zeta]" -> "zeta",
  "\[Zeta]" -> "zeta",
  "\\[Eta]" -> "eta",
  "\[Eta]" -> "eta",
  "\\[Theta]" -> "theta",
  "\[Theta]" -> "theta",
  "\\[CurlyTheta]" -> "curlyTheta",
  "\[CurlyTheta]" -> "curlyTheta",
  "\\[Iota]" -> "iota",
  "\[Iota]" -> "iota",
  "\\[Kappa]" -> "kappa",
  "\[Kappa]" -> "kappa",
  "\\[CurlyKappa]" -> "curlyKappa",
  "\[CurlyKappa]" -> "curlyKappa",
  "\\[Lambda]" -> "lam",
  "\[Lambda]" -> "lam",
  "\\[Mu]" -> "mu",
  "\[Mu]" -> "mu",
  "\\[Nu]" -> "nu",
  "\[Nu]" -> "nu",
  "\\[Xi]" -> "xi",
  "\[Xi]" -> "xi",
  "\\[Omicron]" -> "omicron",
  "\[Omicron]" -> "omicron",
  "\\[Rho]" -> "rho",
  "\[Rho]" -> "rho",
  "\\[CurlyRho]" -> "curlyRho",
  "\[CurlyRho]" -> "curlyRho",
  "\\[Sigma]" -> "sigma",
  "\[Sigma]" -> "sigma",
  "\\[FinalSigma]" -> "finalSigma",
  "\[FinalSigma]" -> "finalSigma",
  "\\[Tau]" -> "tau",
  "\[Tau]" -> "tau",
  "\\[Upsilon]" -> "upsilon",
  "\[Upsilon]" -> "upsilon",
  "\\[Phi]" -> "phi",
  "\[Phi]" -> "phi",
  "\\[CurlyPhi]" -> "curlyPhi",
  "\[CurlyPhi]" -> "curlyPhi",
  "\\[Chi]" -> "chi",
  "\[Chi]" -> "chi",
  "\\[Psi]" -> "psi",
  "\[Psi]" -> "psi",
  "\\[Omega]" -> "omega",
  "\[Omega]" -> "omega",
  "\\[CapitalAlpha]" -> "capitalAlpha",
  "\[CapitalAlpha]" -> "capitalAlpha",
  "\\[CapitalBeta]" -> "capitalBeta",
  "\[CapitalBeta]" -> "capitalBeta",
  "\\[CapitalGamma]" -> "capitalGamma",
  "\[CapitalGamma]" -> "capitalGamma",
  "\\[CapitalDelta]" -> "capitalDelta",
  "\[CapitalDelta]" -> "capitalDelta",
  "\\[CapitalEpsilon]" -> "capitalEpsilon",
  "\[CapitalEpsilon]" -> "capitalEpsilon",
  "\\[CapitalZeta]" -> "capitalZeta",
  "\[CapitalZeta]" -> "capitalZeta",
  "\\[CapitalEta]" -> "capitalEta",
  "\[CapitalEta]" -> "capitalEta",
  "\\[CapitalTheta]" -> "capitalTheta",
  "\[CapitalTheta]" -> "capitalTheta",
  "\\[CapitalIota]" -> "capitalIota",
  "\[CapitalIota]" -> "capitalIota",
  "\\[CapitalKappa]" -> "capitalKappa",
  "\[CapitalKappa]" -> "capitalKappa",
  "\\[CapitalLambda]" -> "capitalLam",
  "\[CapitalLambda]" -> "capitalLam",
  "\\[CapitalMu]" -> "capitalMu",
  "\[CapitalMu]" -> "capitalMu",
  "\\[CapitalNu]" -> "capitalNu",
  "\[CapitalNu]" -> "capitalNu",
  "\\[CapitalXi]" -> "capitalXi",
  "\[CapitalXi]" -> "capitalXi",
  "\\[CapitalOmicron]" -> "capitalOmicron",
  "\[CapitalOmicron]" -> "capitalOmicron",
  "\\[CapitalPi]" -> "capitalPi",
  "\[CapitalPi]" -> "capitalPi",
  "\\[CapitalRho]" -> "capitalRho",
  "\[CapitalRho]" -> "capitalRho",
  "\\[CapitalSigma]" -> "capitalSigma",
  "\[CapitalSigma]" -> "capitalSigma",
  "\\[CapitalTau]" -> "capitalTau",
  "\[CapitalTau]" -> "capitalTau",
  "\\[CapitalUpsilon]" -> "capitalUpsilon",
  "\[CapitalUpsilon]" -> "capitalUpsilon",
  "\\[CapitalPhi]" -> "capitalPhi",
  "\[CapitalPhi]" -> "capitalPhi",
  "\\[CapitalChi]" -> "capitalChi",
  "\[CapitalChi]" -> "capitalChi",
  "\\[CapitalPsi]" -> "capitalPsi",
  "\[CapitalPsi]" -> "capitalPsi",
  "\\[CapitalOmega]" -> "capitalOmega",
  "\[CapitalOmega]" -> "capitalOmega"
};

cppPiNameRules = {
  "\\[Pi]" -> "pi",
  "\[Pi]" -> "pi",
  "\\[CurlyPi]" -> "curlyPi",
  "\[CurlyPi]" -> "curlyPi"
};

cppReservedIdentifierRules = {
  "False" -> "False_",
  "None" -> "None_",
  "True" -> "True_",
  "alignas" -> "alignas_",
  "alignof" -> "alignof_",
  "and" -> "and_",
  "and_eq" -> "and_eq_",
  "as" -> "as_",
  "asm" -> "asm_",
  "assert" -> "assert_",
  "async" -> "async_",
  "atomic_cancel" -> "atomic_cancel_",
  "atomic_commit" -> "atomic_commit_",
  "atomic_noexcept" -> "atomic_noexcept_",
  "auto" -> "auto_",
  "await" -> "await_",
  "bitand" -> "bitand_",
  "bitor" -> "bitor_",
  "bool" -> "bool_",
  "break" -> "break_",
  "case" -> "case_",
  "catch" -> "catch_",
  "char" -> "char_",
  "char8_t" -> "char8_t_",
  "char16_t" -> "char16_t_",
  "char32_t" -> "char32_t_",
  "class" -> "class_",
  "co_await" -> "co_await_",
  "co_return" -> "co_return_",
  "co_yield" -> "co_yield_",
  "compl" -> "compl_",
  "concept" -> "concept_",
  "const" -> "const_",
  "const_cast" -> "const_cast_",
  "consteval" -> "consteval_",
  "constexpr" -> "constexpr_",
  "constinit" -> "constinit_",
  "continue" -> "continue_",
  "decltype" -> "decltype_",
  "def" -> "def_",
  "default" -> "default_",
  "del" -> "del_",
  "delete" -> "delete_",
  "do" -> "do_",
  "double" -> "double_",
  "dynamic_cast" -> "dynamic_cast_",
  "elif" -> "elif_",
  "else" -> "else_",
  "enum" -> "enum_",
  "except" -> "except_",
  "explicit" -> "explicit_",
  "export" -> "export_",
  "extern" -> "extern_",
  "false" -> "false_",
  "finally" -> "finally_",
  "float" -> "float_",
  "for" -> "for_",
  "friend" -> "friend_",
  "from" -> "from_",
  "global" -> "global_",
  "goto" -> "goto_",
  "if" -> "if_",
  "import" -> "import_",
  "in" -> "in_",
  "inline" -> "inline_",
  "int" -> "int_",
  "is" -> "is_",
  "lambda" -> "lam",
  "long" -> "long_",
  "mutable" -> "mutable_",
  "namespace" -> "namespace_",
  "new" -> "new_",
  "noexcept" -> "noexcept_",
  "nonlocal" -> "nonlocal_",
  "not" -> "not_",
  "not_eq" -> "not_eq_",
  "nullptr" -> "nullptr_",
  "operator" -> "operator_",
  "or" -> "or_",
  "or_eq" -> "or_eq_",
  "pass" -> "pass_",
  "private" -> "private_",
  "protected" -> "protected_",
  "public" -> "public_",
  "raise" -> "raise_",
  "reflexpr" -> "reflexpr_",
  "register" -> "register_",
  "reinterpret_cast" -> "reinterpret_cast_",
  "requires" -> "requires_",
  "return" -> "return_",
  "short" -> "short_",
  "signed" -> "signed_",
  "sizeof" -> "sizeof_",
  "static" -> "static_",
  "static_assert" -> "static_assert_",
  "static_cast" -> "static_cast_",
  "struct" -> "struct_",
  "switch" -> "switch_",
  "synchronized" -> "synchronized_",
  "template" -> "template_",
  "this" -> "this_",
  "thread_local" -> "thread_local_",
  "throw" -> "throw_",
  "true" -> "true_",
  "try" -> "try_",
  "typedef" -> "typedef_",
  "typeid" -> "typeid_",
  "typename" -> "typename_",
  "union" -> "union_",
  "unsigned" -> "unsigned_",
  "using" -> "using_",
  "virtual" -> "virtual_",
  "void" -> "void_",
  "volatile" -> "volatile_",
  "wchar_t" -> "wchar_t_",
  "while" -> "while_",
  "with" -> "with_",
  "xor" -> "xor_",
  "xor_eq" -> "xor_eq_",
  "yield" -> "yield_"
};

cppStdFunctionRules = {
  "Power(" -> "std::pow(",
  "Sin(" -> "std::sin(",
  "Cos(" -> "std::cos(",
  "Tan(" -> "std::tan(",
  "Exp(" -> "std::exp(",
  "Log(" -> "std::log(",
  "Sqrt(" -> "std::sqrt("
};

cppGreekActiveRules[treatPi_] := If[
  TrueQ[treatPi],
  Join[cppGreekRules, cppPiVariableRules],
  cppGreekRules
];

cppGreekActiveNameRules[treatPi_] := If[
  TrueQ[treatPi],
  Join[cppGreekNameRules, cppPiNameRules],
  cppGreekNameRules
];

cppRepairReservedIdentifier[name_String, repairReserved_] := Module[
  {suffix, first},
  If[! TrueQ[repairReserved], Return[name]];
  If[name === "lambda", Return["lam"]];
  If[StringStartsQ[name, "lambda"] && StringLength[name] > StringLength["lambda"],
    suffix = StringDrop[name, StringLength["lambda"]];
    first = StringTake[suffix, 1];
    If[StringMatchQ[first, DigitCharacter] || StringMatchQ[first, UpperCaseLetter] || first === "_",
      Return["lam" <> suffix]
    ]
  ];
  Replace[name, cppReservedIdentifierRules]
];

cppCapitalizeIdentifierPart[text_String] := If[
  StringLength[text] == 0,
  text,
  ToUpperCase[StringTake[text, 1]] <> StringDrop[text, 1]
];

cppTemporaryIdentifier[name_String] := Module[
  {clean},
  clean = StringReplace[name, Except[LetterCharacter | DigitCharacter] .. -> "U"];
  If[StringLength[clean] == 0, clean = "Symbol"];
  If[StringMatchQ[StringTake[clean, 1], DigitCharacter], clean = "Symbol" <> clean];
  "pt" <> cppCapitalizeIdentifierPart[clean]
];

cppAsciiIdentifier[name_String, treatPi_] := Module[
  {pairs, orderedPairs, pos, out, rest, match, token, replacement, char},
  pairs = List @@@ cppGreekActiveNameRules[treatPi];
  orderedPairs = ReverseSortBy[pairs, StringLength[First[#]] &];
  pos = 1;
  out = "";
  While[pos <= StringLength[name],
    rest = StringDrop[name, pos - 1];
    match = SelectFirst[orderedPairs, StringStartsQ[rest, #[[1]]] &, Missing["notfound"]];
    If[ListQ[match],
      token = match[[1]];
      replacement = match[[2]];
      If[StringLength[out] > 0, replacement = cppCapitalizeIdentifierPart[replacement]];
      out = out <> replacement;
      pos += StringLength[token],
      char = StringTake[name, {pos}];
      out = out <> char;
      pos += 1
    ];
  ];
  out
];

cppAsciiSymbol[s_Symbol, treatPi_, repairReserved_] := Module[
  {name, ascii},
  name = ToString[Unevaluated[s], InputForm];
  If[! TrueQ[treatPi] && (Unevaluated[s] === Pi || name === "Pi" || name === "\\[Pi]"),
    Return[s]
  ];
  ascii = cppAsciiIdentifier[name, treatPi];
  ascii = cppRepairReservedIdentifier[ascii, repairReserved];
  If[ascii === name, s, Symbol[ascii]]
];

cppAsciiSymbolPlan[s_Symbol, treatPi_, repairReserved_] := Module[
  {name, ascii, temporary},
  name = ToString[Unevaluated[s], InputForm];
  If[! TrueQ[treatPi] && (Unevaluated[s] === Pi || name === "Pi" || name === "\\[Pi]"),
    Return[{s, {}}]
  ];
  ascii = cppAsciiIdentifier[name, treatPi];
  ascii = cppRepairReservedIdentifier[ascii, repairReserved];
  If[ascii === name, Return[{s, {}}]];
  If[StringContainsQ[ascii, "_"],
    temporary = cppTemporaryIdentifier[ascii];
    Return[{Symbol[temporary], {temporary -> ascii}}]
  ];
  {Symbol[ascii], {}}
];

cppRenameGreekSymbols[expr_, treatPi_, repairReserved_] := Module[
  {renamed},
  renamed = expr /. cppGreekActiveRules[treatPi];
  renamed /. s_Symbol :> cppAsciiSymbol[Unevaluated[s], treatPi, repairReserved]
];

cppRenameGreekSymbolsWithPostRules[expr_, treatPi_, repairReserved_] := Module[
  {renamed, postRules = {}, planned},
  renamed = expr /. cppGreekActiveRules[treatPi];
  renamed = renamed /. s_Symbol :> (
    planned = cppAsciiSymbolPlan[Unevaluated[s], treatPi, repairReserved];
    postRules = Join[postRules, planned[[2]]];
    planned[[1]]
  );
  {renamed, DeleteDuplicates[postRules]}
];

Options[toCpp] = {"TreatPiAsVariable" -> False, "RepairReservedIdentifiers" -> True};
Options[toCppStd] = Options[toCpp];

toCpp[expr_, OptionsPattern[]] := Module[
  {renamed, postRules, text},
  {renamed, postRules} = cppRenameGreekSymbolsWithPostRules[
    expr,
    OptionValue["TreatPiAsVariable"],
    OptionValue["RepairReservedIdentifiers"]
  ];
  text = ToString[CForm[renamed], PageWidth -> Infinity];
  StringReplace[text, postRules]
];

toCppStd[expr_, opts : OptionsPattern[]] := StringReplace[
  toCpp[expr, opts],
  cppStdFunctionRules
];

cppGreekExamples[] := Module[
  {expr},
  expr = \[CurlyPhi]^2 + \[Alpha] Sin[\[Beta] x] + \[CapitalLambda] x^4;
  <|
    "toCpp" -> toCpp[expr],
    "toCppStd" -> toCppStd[expr]
  |>
];

cppGreekTests[] := Module[
  {expr, compact, plusTerms},
  compact[s_String] := StringReplace[s, WhitespaceCharacter .. -> ""];
  plusTerms[s_String] := Sort[StringSplit[compact[s], "+"]];
  expr = \[CurlyPhi]^2 + \[Alpha] Sin[\[Beta] x] + \[CapitalLambda] x^4;
  TestReport[{
    VerificationTest[
      plusTerms[toCpp[expr]],
      Sort[{"Power(curlyPhi,2)", "alpha*Sin(beta*x)", "capitalLam*Power(x,4)"}],
      TestID -> "toCpp basic Greek export"
    ],
    VerificationTest[
      plusTerms[toCppStd[expr]],
      Sort[{"std::pow(curlyPhi,2)", "alpha*std::sin(beta*x)", "capitalLam*std::pow(x,4)"}],
      TestID -> "toCppStd basic Greek export"
    ],
    VerificationTest[
      plusTerms[toCpp[\[Lambda]1H + \[Mu]S11 + c\[Theta] + Y\[Phi]]],
      Sort[{"lam1H", "muS11", "cTheta", "YPhi"}],
      TestID -> "compound Greek symbols"
    ],
    VerificationTest[
      compact[toCpp[\[Lambda]]],
      "lam",
      TestID -> "reserved bare lambda is repaired"
    ],
    VerificationTest[
      compact[toCpp[Pi*x]],
      "Pi*x",
      TestID -> "Pi remains a constant by default"
    ],
    VerificationTest[
      plusTerms[toCpp[\[Pi]*x + \[CurlyPi], "TreatPiAsVariable" -> True]],
      Sort[{"pi*x", "curlyPi"}],
      TestID -> "Pi-like variables are opt-in"
    ],
    VerificationTest[
      plusTerms[toCpp[x + y + mDM + lambda1]],
      Sort[{"x", "y", "mDM", "lam1"}],
      TestID -> "ordinary ASCII variables are preserved except lambda prefixes"
    ],
    VerificationTest[
      plusTerms[toCpp[class + double + namespace + return + yield]],
      Sort[{"class_", "double_", "namespace_", "return_", "yield_"}],
      TestID -> "Python and C++ reserved identifiers are repaired"
    ]
  }]
];
