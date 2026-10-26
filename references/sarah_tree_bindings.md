# Native SARAH Tree Parameter Binding

This extension reads static source boundary/matching declarations and calls SARAH's
core `SolveTadpoleEquations` for tree solutions. It does not execute `SPheno.m`,
generate a SPheno program, run SPheno, or calculate numerical loop tadpoles.
The declarations currently come from the model-local file named `SPheno.m`;
reading that file as source data does not require SPheno.

`export-sarah` saves `input/ptagent_sarah_tree_relations.json`. Preparation finds
this sibling file automatically when the archived model source is supplied.
The source-hashed
metadata contains a restricted expression AST, native solution branches, tadpole
equations, and aliases to the exported Vevacious/SLHA names. No model-name branch,
manually supplied potential, or inverse parameter fit is used.

To bind inputs on export, use `--source-parameters source_inputs.json` with
`--preset auto`. Existing exports accept `prepare-sarah --tree-relations tree.json
--source-parameters source_inputs.json`, alongside the usual VIN, parameter map,
archived model-source, thermal metadata, and run-directory arguments. JSON keys
are source symbols; values are real scalars, vectors, or matrices. All source
defaults remain recommendations until explicitly supplied. SM inputs and the
scale must also be supplied explicitly when needed. Explicit normalized inputs
and SLHA values retain their existing precedence over derived values.

The preparation manifest's `tree_review` previews source defaults without
applying them. `missing_source_inputs` lists independent missing dependencies,
not all unresolved generated matrix elements. Unsupported exports and multiple
branches produce a partial review with precise blockers. An explicitly requested
unsupported binding fails instead of silently ignoring the request.

For users without a loop-matched spectrum, recommend tree input matching and
`equal_tree` together with the benchmark, SM input convention and Q in the first
review batch. Once accepted, pass the accepted source inputs in one JSON object
and the explicit prescription below; do not ask separately for derived VEVs,
Yukawa entries or tadpole masses. Do not run SPheno automatically. The selected
source numerical definitions may provide SM input proposals, but no SM numbers
or renormalization scale are hard-coded by this binder.

The default `--tree-loop-policy unresolved` binds only tree parameters. Missing
Loop values still block numerical preparation. `--tree-loop-policy equal_tree`
is an explicit prescription for filling missing Loop aliases from their Tree
partners, recorded in the proof; independently supplied Loop values are preserved.
This prescription is not a calculation of loop matching. Selecting it never
approves a physics contract or bypasses its normal compile gate.
It does not remove CW or finite-temperature loop corrections. A polynomial
made solely of the now-equal Tree/Loop differences vanishes; other imported
extra terms must not be silently removed. The loop-corrected vacuum may move.

A unique native tadpole branch is selected automatically; multiple branches need
an explicit zero-based `--tadpole-branch`. Singular/nonreal solutions, inconsistent
explicit tadpole inputs, changed source files, and failed residual checks are
rejected. Missing inputs and dependency cycles remain reported as unresolved.
The preparation archive includes source inputs, metadata and a hashed binding
proof. Residuals are checked both against the native equations and by the existing
independent check of the imported Vevacious tree potential.

The current grammar supports scalar arithmetic, elementary functions and real
arrays of rank at most two with at most 64 entries. Scalar factors may multiply
arrays. General tensor contractions, complex parameters, computed source
assignments, RGE evolution, and numerical one-loop matching are unsupported.
Unsupported expressions must remain blockers; do not replace them with a model
specific derivation. Finite stationary test points do not establish a stable or
phenomenologically viable vacuum.

The accompanying isolated feature tests use native SARAH THDM and SM exports.
Their additional numerical SM values and `equal_tree` fixtures test software
behavior and are not approved phase-transition benchmarks.
