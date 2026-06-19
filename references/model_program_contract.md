# PTagent Model Contract

Do not hand-write backend model code when a PTagent contract template is
available. Fill the human-readable sections in `contract_template.md` and let
the local compiler write the backend artifact.

The Markdown worksheet is the user-edited contract source. JSON is only an
internal derived object and must not be edited separately.

Each task directory is self-contained. Store copied uploads, arXiv archives,
expanded TeX, Markdown, and PDF fallback material under that task's `input/`
folder. Store evidence and derived JSON under `proof_materials/`, and generated
Python under `generated_models/`. Do not create or rely on a top-level
`artifacts/input` cache for new runs.

The canonical blank worksheet is the Markdown emitted by
`ptagent.ptagent_4d.template_contract.build_contract_template()` /
`_render_human_contract_template()`. Do not search old `artifacts/` runs for a
template skeleton unless the user explicitly asks to continue from an old run.
Old generated templates may be used only as evidence/examples, not as the
source of truth for a new model.

## Fresh And Continue Modes

Use `fresh` mode for a newly supplied paper/source. In this mode the source of
truth is the current input material plus current user answers and explicitly
provided reference code. Prior chat conclusions, old artifacts, and old
generated backend code may be used only as conflict checks if the user asks; do not
copy them into the new contract.

Use `continue` mode only when the user explicitly names an existing
`contract_template.md` or task directory. In this mode that Markdown file is the
source of truth. Patch only the reviewed Markdown, preserve unrelated reviewed
rows, regenerate derived JSON with
`python <skill-dir>/scripts/ptagent_cli.py resolve --template ...`, and compile
only after explicit approval. Do not re-extract the paper unless the user asks
for a fresh run.

## Mandatory User Gates

These gates are required for every new model run, even when the source appears
clear or previous chat context contains answers:

1. Public input gate: show the inferred candidate public inputs beside the
   reason/evidence for each candidate. Ask the user to confirm the final public
   input list and provide one numeric `test_value` for every public input.
2. Backend-specific phase-handling gate: ask which backend will be compiled
   before asking this. For `cosmotransitions`, ask whether the
   `forbidPhaseCrit` phase-filter hook should discard any traced phases. If
   yes, ask for field, comparison, threshold, phase type, and reason.
   Reference-code patterns such as `field < -5.0` are suggestions, never
   automatic choices. For `phasetracer`, do not ask the CosmoTransitions-style
   phase-filter question; ask whether PhaseTracer should use
   `apply_symmetry(phi)` / `get_symmetry_axes()` to identify reviewed Z2
   sign-flip equivalences and avoid duplicate phase counting.
3. Compile gate: after the Markdown contract has no blockers, stop and ask the
   user to review the rendered `contract_template.md` and explicitly approve
   backend code generation/compilation. Do not interpret "write a model" as
   compile approval.

If any physics choice is ambiguous, leave `ASK_USER` in the Markdown and ask the
user. Do not fill uncertain fields from memory, model-family convention, or
previous artifacts.

## Required Contract Sections

- `fields`: ordered background fields. These become `X[..., i]`.
- `parameters.public_inputs`: constructor/build inputs. Each row needs `name`,
  `latex`, `confirmed=true`, and a numeric `test_value`. The test value is the
  generated `build_model` default and the smoke-test benchmark. Compilation must
  block until the user confirms both the input list and every test value in the
  current run.
- `parameters.constants`: fixed values such as `vh`, gauge couplings, or Yukawa
  defaults when they are not scan inputs.
- `parameters.derived`: Python expressions evaluated after inputs/constants.
- `potential.V0.python`: tree-level potential expression.
- potential pieces under `## 4. Potential`: V0, VCW, VCT, thermal, Daisy, and
  the full potential reference formula.
- Model Card rows under `## 1. Model Card`: zero-temperature loop mode,
  counterterm mode, finite-temperature loop mode, Daisy mode, resummation
  scheme, gauge, Goldstone policy, and photon/Z-like neutral-mode policy.
- direct species masses: field-dependent mass squared expressions, d.o.f., and
  Coleman-Weinberg constants, with usage, thermal policy, and vacuum-anchor
  metadata.
- dimension/Hessian consistency notes: if a source mass formula appears
  dimensionally inconsistent with its compiled role, document the suspected TeX
  typo, recommend the Hessian-derived or dimensionally consistent repair, and
  ask the user to confirm before compiling.
- excluded species table: reviewed omissions such as photons, Goldstones, or
  light fermions. These rows are not compiled but document the convention.
- boson mass matrices: field-dependent matrices whose eigenvalues enter the
  bosonic spectrum. Prefer this route by default when a matrix is present or
  field mixing is plausible; ask the user to confirm entries and d.o.f. per
  eigenvalue before compiling. Disable it only for reviewed direct eigenvalues.
- `masses.fermions`: field-dependent fermionic mass squared expressions and d.o.f.
- `loops.zero_temperature`: `none`, `standard_CW_V1`, or
  `custom_expr`.
- Renormalization scale: `standard_CW_V1` and explicit counterterm systems that
  differentiate a CW source require one reviewed scale row in public inputs,
  fixed constants, or derived quantities. Use paper/source information such as
  `Q`, `Qren`, or `mu_R` when available and describe ambiguous names as the
  Coleman-Weinberg/renormalization scale. If the paper does not provide a
  scale, ask the user before compile. Generated CosmoTransitions code must set
  `self.renormScaleSq = scale**2`; generated PhaseTracer code must use the same
  reviewed scale for native CW sums.
- `loops.thermal`: `none`, `standard_thermal_integrals`, or `custom_expr`.
- `loops.daisy`: `none` or `custom_expr`. Use `custom_expr` for an explicit
  Arnold-Espinosa Daisy term; Parwani must keep this as `none`.
- Section 6 implementation contract: CT basis/conditions, Goldstone handling,
  Daisy/thermal-mass route, backend-specific phase handling, and
  double-counting guard.
- Detailed Section 6 rows:
  - Counterterms: show `V_CT=sum_i c_i O_i`, `A_ai=L_a[O_i]`,
    `b_a=t_a-L_a[V_CW_source]`, and `A c=b`; fill visible basis and condition
    tables rather than hiding fitted coefficients.
  - Goldstones: when Goldstones exist, list each mode or mode group with
    mass_sq, d.o.f., CW policy, CT policy, thermal policy, regulator, and any
    replacement expression.
  - Daisy: when the scheme is not `none`, list every affected particle/mode
    group with zeroT mass, thermal mass, d.o.f., longitudinal-only flag, and
    the reviewed Daisy term or Parwani replacement route.
  - CosmoTransitions phase filtering: explicitly ask the user to decide whether
    to implement the CosmoTransitions phase-filter hook after presenting the
    agent's recommendation and reason. Recommend `mode=none` unless the
    paper/reference code intentionally removes a duplicate or unphysical branch.
    This maps to `forbidPhaseCrit`. It
    discards traced phases when the method returns true; it is not part of
    `Vtot`. If enabled, list the phase type, field component, comparison,
    numeric threshold, and reason. For xSM-like Z2 mirror branches in
    CosmoTransitions, the standard pattern is a tolerance such as
    `field < -5.0`, not `field < 0`.
    Custom expressions may use Python `and`/`or` in the Markdown contract, but
    generated code must convert them to NumPy-safe elementwise boolean logic and
    smoke-test both single-point and batched `X` inputs. PhaseTracer does not
    consume this section.
  - PhaseTracer symmetry: explicitly ask the user to decide whether to implement
    `apply_symmetry(phi)` and `get_symmetry_axes()` after presenting the agent's
    recommendation and reason. Recommend `mode=none` unless the user confirms a
    reviewed sign-flip equivalence. This is required for the PhaseTracer backend
    instead of a CosmoTransitions-style phase-filter question. It identifies
    symmetry-equivalent field points so they are not counted as distinct phases;
    it does not create new physical vacua. For a Z2 reflection, list each field
    or simultaneous field group whose sign is flipped, for example `s` for
    `s -> -s` or `h,s` for `h -> -h and s -> -s`. One row creates one generated
    symmetry partner; two separate rows `h` and `s` mean `h -> -h or s -> -s`.
    Do not encode independent alternatives as `h,s`.
- `num_boson_dof` and `num_fermion_dof` whenever standard thermal integrals
  are used. These are total thermal-radiation d.o.f.; backend `V1T`
  implementations subtract explicit species internally. Ask the user to confirm the totals.
  Use SM baseline 28/90, add reviewed BSM d.o.f. beyond the one SM Higgs scalar
  already in that baseline, and report the additions to the user.
  If no enabled `masses.fermions` rows are reviewed, keep the explicit fermion
  spectrum empty. Do not add placeholder fermion species to work around
  CosmoTransitions smoke failures; `num_fermion_dof` remains only total
  radiation metadata in that case.

## Expression Rules

Use Python expressions, not LaTeX. Allowed names are fields, public inputs,
constants, derived parameters, `T`, and approved `np.*` functions such as
`np.sqrt`, `np.log`, `np.sin`, `np.cos`, `np.exp`, `np.where`, and `np.pi`.

Do not use runtime `eval`, helper code snippets, imports, file access, or hidden
matrices. If a formula cannot be expressed in the current contract, ask the user
or extend the compiler generically.

Markdown tables may render LaTeX symbol/evidence cells as math, but compiler
cells such as `mass_sq`, `expr`, and numeric defaults stay as plain Python text.
Do not mix prose and LaTeX formula fragments in one table cell; use `notes` for
prose and evidence/math blocks for formulas.

## Physics Role Rules

Keep these roles separate:

- field-dependent mass matrices/eigenvalues for `boson_massSq`, `fermion_massSq`,
  standard Coleman-Weinberg, thermal corrections, and Daisy routing;
- vacuum physical-mass relations only for parameter inversion and vacuum checks;
- thermal-resummed masses only for the reviewed Daisy route.

Daisy resummation must be one of the two standard routes unless the paper
explicitly defines a custom prescription:

- Parwani: replace one-loop finite-temperature mass eigenvalues by reviewed
  lowest-order thermal mass eigenvalues. Do not add a separate `V_daisy`.
  Since `V1` then depends on `T`, backend routes that separate thermal
  derivatives from the full potential must include that temperature-dependent
  `V1` contribution in the derivative path.
- Arnold-Espinosa: use ordinary field-dependent masses in `V1`/`V1T`, and add
  one explicit `V_daisy = -T/(12*pi) sum_i n_i[(M_i^2(phi,T))^(3/2) -
  (m_i^2(phi))^(3/2)]` over reviewed zero-Matsubara modes. `V1T_from_X` must
  include this explicit `V_daisy` in backends that separate thermal derivatives
  from the full potential.

For Arnold-Espinosa, the contract must state how `(m^2)^(3/2)` is evaluated
when a mass squared is negative. Do not emit raw `m2**1.5` unless the reviewed
paper/code guarantees nonnegative arguments. Use an explicit reviewed convention
such as positive-part, signed-abs, regulated-abs, or a paper formula.

Do not treat `ring` as a third implementation route. In this context it names
the ring/daisy diagrams being resummed.

If the paper gives explicit thermal eigenvalues, use those directly in a reviewed
custom expression or species route. If it gives only a thermal matrix, the user
must confirm the working basis and diagonalization rule before compilation.

Goldstone and CT handling must be explicit. If CT conditions differentiate
`V_CW`, the contract must say whether that source includes Goldstones, excludes
them, or adds a regulated replacement. Do not finite-difference full
Goldstone-including `V_CW` and then add a replacement term again.

Photon handling must also be explicit. If a neutral thermal mass matrix has a
massless photon-like branch and a massive Z-like branch, the contract should
state which branch contributes to V1T/Daisy and which branch is excluded.
When vector thermal masses distinguish longitudinal/transverse modes or give a
neutral gauge-basis matrix, use reviewed mode groups or a reviewed matrix route
instead of a single shared vector thermal mass.

Generated CosmoTransitions code should keep standard public method names:
`V0`, `V1`, `V1T_from_X`, `Vtot`, `boson_massSq`, and `fermion_massSq`. For
OS-like one-loop prescriptions, override `V1(self, bosons, fermions)` rather
than creating ad hoc names.

For standard CosmoTransitions thermal integrals, generated code may override
`V1T(self, bosons, fermions, T, include_radiation=True)` only to preserve the
standard formula while avoiding backend empty-spectrum spline calls. If the
reviewed contract has no enabled fermion rows, `fermion_massSq` must return an
empty spectrum, explicit fermion thermal integrals must be skipped, and
`num_fermion_dof` must still contribute only through the total radiation d.o.f.

Generated PhaseTracer C++ code should keep standard native hook names such as
`V0`, `V1`, `V1T`, `V`, `get_*_masses_sq`, `get_*_dofs`, `get_raddof`,
`apply_symmetry`, and `get_symmetry_axes`, with all formulas, d.o.f., and
symmetry operations coming from the same reviewed contract.

## Final Generated-Code Cleanup Prompt

Use this as the last prompt/checklist before returning or comparing a generated
model program. It is intentionally mechanical so less capable coding models can
follow it:

```text
Clean the generated backend code without changing the reviewed physics contract.
Keep only imports, attributes, helper methods, local variables, and metadata that are actually used.
Use standard backend method names: CosmoTransitions uses V0, V1, V1T_from_X, Vtot, boson_massSq, fermion_massSq, approxZeroTMin, forbidPhaseCrit, and build_model; PhaseTracer uses V0, V1, V1T, V, get_*_masses_sq, get_*_dofs, get_raddof, apply_symmetry, and get_symmetry_axes.
Remove paper-specific aliases, stale comments, unused temporary variables, duplicate formulas, unreachable branches, and obsolete generated-code leftovers.
Do not rename public inputs, fields, or reviewed contract symbols.
Do not replace reviewed field-dependent matrices with vacuum mass relations.
Do not add, remove, or guess Goldstone, photon, Daisy, counterterm, phase-filter, or PhaseTracer symmetry conventions.
After cleanup, run backend smoke checks and confirm the public input signature still matches the contract. CosmoTransitions smoke must exercise forbidPhaseCrit on single-point and batched X inputs; PhaseTracer smoke must exercise generated apply_symmetry equivalence outputs when symmetry is enabled. Generated CosmoTransitions files should expose `build_model()` as the default direct-run entrypoint that can print `calcTcTrans()` critical-temperature rows. Generated PhaseTracer projects should use the generated `PTagentPhaseTracer::GeneratedPotential` constructor for C++ model construction and `run_model --transition` for native `Transition.TC` output. Cross-backend compare should reuse hash-matched generated artifacts and regenerate only stale or missing artifacts unless `--force-regenerate` is explicitly requested.
```

## Stop Conditions

Leave `ASK_USER` and stop when:

- public inputs or defaults are unclear;
- a parameter is both input and derived;
- source evidence conflicts and the conflict has not been reviewed;
- a mass formula may be vacuum-only rather than field-dependent;
- a mass formula appears dimensionally inconsistent with the reviewed potential
  or Hessian and the repair has not been confirmed by the user;
- an equation has been copied without semantic role review of the surrounding
  prose, equation label, and "where"/"adding"/"defined as" context;
- Daisy, Goldstone, photon/neutral-gauge, CT, or loop convention is
  underdetermined;
- a Daisy `thermal_mass_sq` or gauge Debye term may be only a `Delta Pi`,
  additional contribution, or partial self-energy rather than the full
  resummed mass/eigenvalue used in the ring term;
- backend-specific phase handling is underdetermined;
- compile approval is missing, or a public-input / backend-specific
  phase-handling gate remains unresolved after checking source material, current
  contract, generated proof files, and explicit user inputs;
- a branch choice changes formulas.

These are hard blockers, not warnings. Do not fall back to model-family
conventions or standard Debye/Goldstone/CT formulas unless the user or the source
material explicitly approves that route.
