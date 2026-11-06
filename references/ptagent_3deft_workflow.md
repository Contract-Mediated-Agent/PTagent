---
name: ptagent-internal-3deft-workflow
description: Build independent 3DEFT/DRalgo contracts from reviewed DRalgo source programs, run DRalgo, and compile only to PhaseTracer-compatible artifacts.
---

# PTagent 3DEFT

Use this internal workflow when the user says 3DEFT, DRalgo, dimensionally reduced EFT, hard/soft/ultrasoft matching, or asks for a 3D EFT potential to be consumed by PhaseTracer.

This workflow is separate from the internal 4D workflow. Do not use the standard finite-temperature contract, model IR, or backend renderers.

## Current Scope

3DEFT v1 supports only:

```text
reviewed user DRalgo .m/.wl -> DRalgo output capture -> reviewed 3DEFT contract -> PhaseTracer C++
```

The agent must not create the user's physics DRalgo model file from contract rows.
The executable physics source is a user-supplied Mathematica/Wolfram DRalgo file
or the explicitly requested bundled xSM example.

Accepted input files:

- `.m`
- `.wl`

Reject TeX, Markdown, PDF, arXiv archives, old JSON-fence templates, and non-3DEFT PTagent contracts for this workflow.

## Bundled xSM Example

The repository includes `assets/examples/xsm_test.m`, copied from the paper's
3DEFT supplementary source. It uses the explicit fermion-generation factor
`nF` and scalar/vector rotations before `CalculatePotentialUS[]`. Keep the
source unchanged; importing it does not settle numerical inputs or approve a
contract. It is an example, not a replacement for arbitrary user physics.

For an xSM 3DEFT example request without an uploaded/local source:

```bash
python <skill-dir>/scripts/ptagent_cli.py --ptagent-engine 3deft extract --example xsm
```

`--input xsm_test.m` uses a file in the current directory if present, otherwise
the bundled copy. For uploads or explicit paths, always pass that actual path
with `--input`; a missing custom path is an error, not an invitation to replace
it. Do not ask the user to download/upload the documented example. Installing
the repository already downloads it with the skill. If an incomplete older
installation lacks the asset, retrieve `assets/examples/xsm_test.m` from
`Contract-Mediated-Agent/PTagent` into the task workspace and pass that path;
report the repository revision and file hash. Never recreate the model with an
LLM or fetch another project's file as a substitute.

Extraction copies the selected source into `input/` and records its origin,
original path and SHA256 in the extraction report. Wolfram/DRalgo dependency
checks, grouped physical questions and final compile approval remain in force.

## Task Layout

A fresh run creates one task-local directory under the configured artifact
root, normally `PTagentRuns`:

```text
PTagentRuns/<YYYYMMDD_HHMMSS>_<safe_input_stem>/
  input/
    <uploaded-source>.m
  proof_materials/
  DRalgo_model/
  three_deft_contract_template.md
  three_deft_contract_merged.md
```

Use these roles:

- `input/`: task-local copy of the user-supplied or bundled `.m`/`.wl` source; follow-up steps must depend on this copy, not on the original upload or skill path.
- `three_deft_contract_template.md`: human-readable source of truth after extraction; placed in the run root so it is easy to find.
- `three_deft_contract_merged.md`: human-readable candidate after DRalgo output is merged; placed in the run root and reviewed before compile.
- `proof_materials/`: derived evidence and status files only.
- `DRalgo_model/`: generated Wolfram wrapper and generated PhaseTracer project.

Derived proof files include:

- `three_deft_extraction_report.json`
- `three_deft_contract_resolved.json`
- `three_deft_validation.json`
- `three_deft_user_questions.md`
- `three_deft_review_gate.md`
- `three_deft_reference_check.json`
- `dralgo_3deft_output.json`

## Commands

Extract from a reviewed DRalgo source:

```powershell
python <skill-dir>/scripts/ptagent_cli.py --ptagent-engine 3deft extract --input <dralgo.m|dralgo.wl>
```

Resolve and regenerate proof files:

```powershell
python <skill-dir>/scripts/ptagent_cli.py --ptagent-engine 3deft resolve --template <run_dir>\three_deft_contract_template.md
```

Check Mathematica and DRalgo before a real run:

```powershell
python <skill-dir>/scripts/ptagent_cli.py --ptagent-engine 3deft env-check
```

If DRalgo is missing, stop and ask the user whether to install. Install only after explicit approval:

```powershell
python <skill-dir>/scripts/ptagent_cli.py --ptagent-engine 3deft install-dralgo --yes
```

Generate a marked wrapper around the reviewed source:

```powershell
python <skill-dir>/scripts/ptagent_cli.py --ptagent-engine 3deft generate-dralgo --template <run_dir>\three_deft_contract_template.md
```

Run DRalgo and normalize the marked output:

```powershell
python <skill-dir>/scripts/ptagent_cli.py --ptagent-engine 3deft run --template <run_dir>\three_deft_contract_template.md
```

Merge normalized DRalgo output back into the reviewable contract:

```powershell
python <skill-dir>/scripts/ptagent_cli.py --ptagent-engine 3deft merge-output --template <run_dir>\three_deft_contract_template.md --dralgo-output <run_dir>\proof_materials\dralgo_3deft_output.json
```

Check the proposed numerical reference before presenting the final merged
contract and graph. This is a read-only Python check, not a native calculation:

```powershell
python <skill-dir>/scripts/ptagent_cli.py --ptagent-engine 3deft check-reference --template <run_dir>\three_deft_contract_merged.md
```

It returns nonzero for an invalid reference or incomplete contract and writes
`proof_materials/three_deft_reference_check.json`. `resolve` checks construction
and review state; it does not establish the reference's numerical domain.
Inspect the exact negative root/log/power operands in `domain_diagnostics`,
active-order values, and `suggested_reference_for_review`. The suggestion is a
bounded, deterministic diagnostic search using dimensionless rescalings, not a
new benchmark or a fitted physical result. Keep all input parameters, scales
and active orders unchanged. Present any alternative in the consolidated review
batch, apply it only after acceptance, and rerun the check before final approval.
A passing check does not establish a vacuum, perturbative convergence, or valid
evolution throughout field space. If no candidate works, report the limitation;
never add `abs`, discard an imaginary part, or drop an order to manufacture a pass.

Record the user's exact merged-contract approval and compile. `approve` repeats
the reference preflight against the current specification and refuses an invalid
point without changing the contract or granting approval:

```powershell
python <skill-dir>/scripts/ptagent_cli.py --ptagent-engine 3deft approve --template <run_dir>\three_deft_contract_merged.md --gate phasetracer_compile --user-approval "<exact user sentence>"
python <skill-dir>/scripts/ptagent_cli.py --ptagent-engine 3deft compile --template <run_dir>\three_deft_contract_merged.md
```

`approve --output <run_dir>\reviewed-copy.md` writes an approved copy while
leaving the input contract unchanged. Any basename is supported by `approve`
and `compile`; approval is established by the complete contract, explicit
review/approval record, matching physical-specification hash, and numerical
reference preflight, never by a filename. The copy must remain in the same
directory as the input so relative source paths retain their meaning. Pass the
copy itself to subsequent commands. For a custom basename, resolved review
artifacts and the default generated project are placed beside the copy (the
standard merged filename retains the task's `proof_materials`/`DRalgo_model`
layout); use `compile --output-dir` for an explicit project destination.
Edits to the approved specification invalidate its approval hash regardless
of filename. Incomplete, refused, conditional, future, or stale approvals still
block compilation. Unrelated negative statements, such as a disclaimer about
who personally confirmed a delegated test, remain verbatim in the record and
do not themselves negate explicit approval. This syntax check does not establish
the speaker's identity or authority; agents must still obtain real authorization.

Optionally configure/build/run the generated PhaseTracer project:

```powershell
python <skill-dir>/scripts/ptagent_cli.py --ptagent-engine 3deft check-model --project-dir <run_dir>\DRalgo_model\<model_name>_phasetracer --phasetracer-root <PhaseTracer>
```

`--phasetracer-root` is optional only when `PTAGENT_PHASETRACER_ROOT` or
`~/.ptagent/config.toml` already provides `phasetracer_root`. If PhaseTracer is
missing, tell the user that PhaseTracer is required as a C++ source root, not as
a Python package. The recovery path is either
`python <skill-dir>/scripts/ptagent_cli.py init --phasetracer-root <PhaseTracer-root>`
or passing `--phasetracer-root <PhaseTracer-root>` to the current `check-model`
command.

Run the Mathematica replacement check for the fixed 3D-parameter V3D layer:

```powershell
python <skill-dir>/scripts/ptagent_cli.py --ptagent-engine 3deft compare-mathematica --template <run_dir>\three_deft_contract_merged.md --project-dir <run_dir>\DRalgo_model\<model_name>_phasetracer
```

## Question Mode

Present all available unresolved decisions together in one numbered batch, with related fields grouped. Before asking, fill every mechanical item supported by the reviewed source, DRalgo output, or proof files. Ask only when missing/conflicting evidence or multiple interpretations would change physics or make the model unverifiable. Do not repeat explicit user choices. If DRalgo execution reveals a new ambiguity, explain that dependency before asking a follow-up batch.

Whenever presenting either 3DEFT contract for review, follow
`contract_review.md`: show the current Markdown and its matching provenance
graph together. Include the DRalgo source/output, matching scales, EFT potential,
RG/evaluation choices, and pending PhaseTracer policies. Do not add 4D CW/Daisy
terms to a 3DEFT graph. The graph ends at the contract, before backend execution.

Do not ask about safe conventions, notation mapping, variable renaming, formatting choices, or backend-compatible defaults. Proceed with a documented assumption in those cases.

Every question should include:

- why the item matters;
- what source/proof/contract/output materials were checked;
- what information is missing or conflicting;
- what physics, code, or numerical result would change;
- the agent's current source-backed thought, clearly marked as reviewable;
- what the agent will do if the user accepts that recommendation;
- the exact contract field or table being patched;
- a number within the complete current question batch.

Do not dump raw validation blockers on the user.

Ask the user only for items that cannot be safely inferred after checking the reviewed DRalgo source, normalized DRalgo output, current contract, and proof files:

- Source model audit: fill source-level interpretation from `RepScalar`, `RepFermion1Gen`/`RepFermion`, scalar potential terms, Yukawa terms, `AllocateTensors`, `ImportModelDRalgo`, matching calls, VEV/effective-potential setup, and non-default DRalgo modes; ask only for unresolved or conflicting interpretations.
- Input parameters: fill the final constructor/RG input list, one `test_value` per input, and `input_scale_GeV` per input from source/user/proof evidence; ask only for missing numeric values or unresolved public API choices. The 3DEFT contract does not use scan/fixed metadata or a per-input renormalization-scheme column.
- RG and matching scales: infer explicit scale policies from source/output first. Ask as one connected scale policy only if the evidence does not determine them. Recommended default is `mu4 = 4*pi*exp(-EulerGamma)*xi4*T` with `xi4=1`, `mu_3_scale = T`, and `mu_3_us_scale = factor*T`, where `factor` should be reviewed from the model's soft scale convention such as `g2` when appropriate.
- Soft scalar indices: if source/output does not provide an auditable `PrintScalarRepPositions[]` map, help the user with a representation-order guess but do not treat a bare index as reviewed truth.
- Ambiguous physical labels: ask for labels only when the source names/comments do not make the label clear. Representation data alone should not be promoted to particle identities.
- PhaseTracer `apply_symmetry()` policy: run the shared conservative sign-flip
  checker on the reviewed V3D expression, then ask the user to confirm
  `symmetry_hook`, `symmetry_rules`, and `low_t_phase_guesses` before PhaseTracer
  compile. The checker may recommend independent single-field generators or a
  simultaneous multi-field generator, but it does not approve them. Present the
  recommendation and reason, and record the final status as `user_confirmed` or
  `human_reviewed`. Keep low-temperature guesses `none` unless numeric hints are
  intentionally supplied.
- PhaseTracer compile approval: after `three_deft_contract_merged.md` has no compile blockers, ask the user to open and review it, then record their exact approval sentence using `approve`. This binds `approval_spec_hash` to the normalized physical contract. Editing parameters, expressions, mappings, scales, orders or generation options invalidates the approval; request fresh review instead of refreshing the hash automatically. Negated or conditional approval is not consent. Old approved contracts without the digest need fresh approval. The digest excludes approval bookkeeping, runtime source_hash, output location and executable location; it does not monitor the original DRalgo source file.

Do not ask the user to hand-copy DRalgo-derived outputs before DRalgo has run. These are agent/DRalgo backfill items:

- scalar index maps from `PrintScalarRepPositions[]`;
- 3D coefficients from `PrintCouplings[]` and `PrintCouplingsUS[]`;
- effective potential pieces from `PrintEffectivePotential[...]`;
- beta functions from `BetaFunctions4D[]`, `BetaFunctions3DS[]`, and `BetaFunctions3DUS[]` when available.

Potential references must use the matched 3D coefficients, including gauge
coupling squares. The merger maps even powers/products of a parent coupling
to its `_sq` coefficient, preferring the ultrasoft coefficient when present.
The matching expression already contains its temperature normalization; do not
add another factor of T or replace a linear coupling with its square. Odd,
sign-sensitive, or ambiguously named uses require an explicit reviewed convention
and block automatic rewriting. Check every retained perturbative order, not only
the total potential. Matching definitions and 4D beta functions retain their
parent input symbols. Any physical change invalidates the previous approval.

## Hard Blocks

Stop before wrapper generation/run when:

- the source is not a `.m` or `.wl` DRalgo file;
- Mathematica or DRalgo cannot be loaded;
- the source contains more than one active `PerformDRsoft[...]` call after Mathematica comments are stripped;
- required source model audit or input parameter fields remain unresolved;
- any model variable uses reserved scale names: `mu`, `mu3`, `mu_3`, `mu4`, `mu_4`, `mu_3_us`, `mu_3us`, `mu3us`, or `mu3US`.

Stop before PhaseTracer compile when:

- `three_deft_contract_merged.md` has unresolved compile blockers;
- DRalgo-derived `V3D` pieces or coefficient maps have not been merged/reviewed;
- `mu_3_scale` or `mu_3_us_scale` substitutions are unresolved;
- `symmetry_hook`, `symmetry_rules`, or `low_t_phase_guesses` have not been explicitly user-confirmed/human-reviewed for the generated PhaseTracer `apply_symmetry()`, `get_symmetry_axes()`, and `get_low_t_phases()` methods;
- the user has not approved the merged contract with an exact approval sentence;
- a custom symmetry, low-temperature phase, or beta hook policy is requested but the independent 3DEFT renderer cannot generate it.

There is no separate user approval gate for wrapper generation or Mathematica run. Source preflight and validation blockers control those steps.

## Bounded Wolfram Execution

The gate, package discovery, compatibility wrapper, source run and Mathematica
comparison share a process-local runtime configuration. Inspect `wolfram.runtime`
in the environment-gate JSON (or `Wolfram runtime:` from `3deft env-check`): it
records the launcher, selected kernel, mode, temporary directory, version,
limits and startup attempts. A failed WolframScript startup may try direct
`-script` execution of the exact kernel named by that launcher. Only the harmless
startup probe has this fallback; a package or model evaluation is never retried
on a different transport or kernel after failure.

Successful read-only probes are reused within that Python process. Separate CLI
commands revalidate; there is no persistent verified-runtime or package cache.
To reuse a reported `mode=kernel` selection in later task commands, set
`PTAGENT_WOLFRAM_KERNEL` to that receipt's `kernel` in the command environment.
`WOLFRAMSCRIPT_KERNELPATH` is also accepted. An inaccessible configured kernel
blocks execution rather than silently selecting another installation.
`--wolframscript` on `run` or `generate-dralgo` selects the launcher consistently,
including compatibility discovery. None of these options modifies Wolfram's
persistent configuration, activates it, or installs a dependency.

Process-local limits and temporary files:

- `PTAGENT_WOLFRAM_PROBE_TIMEOUT`: positive finite seconds for each startup or
  package-path probe; default 15. A startup fallback has its own bounded probe.
- DRalgo initialization has a separate bounded 45-second default.
- `PTAGENT_WOLFRAM_TIMEOUT`: positive finite seconds per source capture stage;
  default 600. `3deft run --timeout-seconds N` overrides it for that run. An
  automatic scalar-map/full-capture sequence can execute two bounded stages.
- `PTAGENT_WOLFRAM_TMPDIR`: an existing task-local writable directory. Otherwise
  the inherited temporary directory is used. Relative paths are bound once,
  before child working-directory changes; the parent environment is not changed.

Timeout, inaccessible runtime, package not found on the selected search path,
and package initialization failure are distinct diagnostics. A timeout alone
does not establish missing packages, a license problem, or a permission problem.
Source stdout/stderr and execution receipts are retained under `proof_materials/`
as `dralgo_source_*`, and `dralgo_autostage_*` when a second stage is needed,
including on timeout. Owned POSIX process groups are stopped on completion,
timeout or interruption; model runs never wait indefinitely for inherited pipes.

## Opt-In Legacy Package Compatibility

An older installed DRalgo may declare ``DRalgo` `` although its source requests
``Needs["DRalgo`DRalgo`"]``. Older GroupMath can also define an unqualified
`BlockDiagonalMatrix` that collides with a protected System function in newer
Wolfram kernels. Package discovery or exit code zero alone is not successful
initialization. Preserve failed-stage logs before retrying.

For this diagnosed combination only, an explicit process-local opt-in is
available. Set `PTAGENT_DRALGO_COMPAT=legacy-groupmath` and
`PTAGENT_DRALGO_COMPAT_SOURCE` to the reviewed task-local `.m`/`.wl` source for
the `env-check`, `generate-dralgo`, and `run` commands. Do not write persistent
configuration. These settings do not approve physics or install anything.

The loader accepts only a literal `$LoadGroupMath=True` or `False` assignment
followed by a literal DRalgo `Needs` call at the beginning of the source.
It preserves that policy, rejects additional/dynamic loading or context changes,
and verifies the installed package's declared context and, when GroupMath is
requested, the known unqualified matrix definition and System collision.
Unsupported layouts abstain instead of guessing. Generated wrappers bind the
decision to source/package hashes and require a fresh kernel.

The prelude creates only the package-local matrix symbol before loading, checks
that the warmed System matrix definitions/attributes and sample output remain
unchanged, verifies initialization and public definitions, and registers an
empty legacy alias context when needed. It never unprotects or redefines System
functions, overrides `Needs`/`Get`, or edits installed packages or model source.
This guarantee concerns the added prelude, not unrelated behavior already in
the installed package. Normal generated wrappers contain the prelude; no
task-specific bootstrap is required. Opt-in off retains the ordinary loader.

`BetaFunctions3DS[]` is optional only when the contract's entire soft-beta audit
table is explicitly `not_applicable` and the source does not request that call.
An absent definition then yields a marked `Missing["OptionalAuditUnavailable",
"BetaFunctions3DS"]` record and `unavailable_optional_audits`, never a zero beta.
Applicable soft audits, 4D/3DUS betas and requested potential orders remain
required; missing markers, failed calls and unevaluated outputs block the run.

## DRalgo Source Audit

The audit should summarize the reviewed source; it should not synthesize a new model file.

Focus on:

- loading block and package assumptions;
- `Group`, `CouplingName`, `RepAdjoint`;
- `RepScalar` entries, component counts, VEV/background roles, and raw source labels;
- `RepFermion1Gen`/`RepFermion` entries. For SM extensions, Standard Model one-generation fermion content is a review hint only; custom fermions require explicit review;
- scalar invariants and gradient helpers such as `CreateInvariant`, `GradMass`, `GradQuartic`, `GradCubic`, and tadpole helpers;
- Yukawa invariants and the mapping between numeric coupling, invariant label, and operator, using only the symbols and interactions found in the current source; do not infer a particle label or coupling from another example;
- `AllocateTensors[...]` and `ImportModelDRalgo[...]` argument order and any explicit `Mode`;
- `PerformDRhard[]`, the single active `PerformDRsoft[...]`, and ordered print calls;
- VEV/background definitions used before `CalculatePotentialUS[]` and `PrintEffectivePotential[...]`.

Orders are source-driven. Copy explicit ordered print calls present in the reviewed source or DRalgo output, such as `PrintEffectivePotential["NNLO"]`, `PrintScalarMass["NLO"]`, `PrintScalarMassUS["NLO"]`, or `PrintPressure["LO"]`. Do not recommend LO/NLO/NNLO and do not ask the user to choose an order when the source already determines what is printed.

## RG And Scales

Generated models integrate only from the reviewed input scale to the scale
requested by the current evaluation, using Boost Odeint RK4 and a cache of
successful exact-target states. Construction does not evolve a fixed global
range. Explicit RG bounds remain applicability limits; no spline extrapolation
or benchmark-specific cutoff is permitted. Report nonfinite running with the
input/requested scale and last finite step; it is not a measured Landau pole.

The generated `fixed_probe` evaluates canonical 3D fields with frozen
coefficients and running inputs independently of RG initialization. Its metadata
records column order and batch usage. This checks expression translation, not
independent source-native DRalgo matching. Preserve failures at invalid field
points and distinguish them from successful evaluations. `check-model` still
checks the full runtime path as well as the fixed reference; it does not by
itself execute a phase history or establish Tc/Tn.

The user-facing scale question should stay simple:

- hard matching scale `mu4`;
- hard scale factor `xi4`, default `1`;
- `mu_3_scale`;
- `mu_3_us_scale`.

Preserve source-specified and already reviewed scales. Otherwise propose the
hard thermal scale and soft scale `T` for review, and ask for an ultrasoft
expression such as `factor*T`. Determine that dimensionless factor from the
actual source coupling convention or an explicit reviewed numerical input.
List the source gauge groups/couplings without selecting a gauge sector or
assuming that a symbol denotes a coupling rather than its square. No universal
gauge-group or named-coupling ultrasoft prescription is implied.

Implementation details such as ODE step count, internal beta-variable names, and solver choices should not be exposed as ordinary user questions.

DRalgo beta-function conventions:

- `BetaFunctions4D[]` is interpreted as derivatives with respect to `log(mu)`.
- DRalgo gauge beta rows may be for `g^2`; when the contract input is `g`, convert the RHS explicitly and record the conversion.
- `BetaFunctions3DS[]` is a soft-stage audit table and is not the ultrasoft running layer.
- `BetaFunctions3DUS[]` evolves matched 3D parameters from `mu_3_scale` to `mu_3_us_scale`.

For 3DUS running, use the analytic logarithmic update when the RHS has no explicit running-scale or self-state dependence. If the RHS depends on the running scale or state, generate numeric evolution in `log(mu_3_us)`.

`xi4` is a scale factor, not a running coupling, and must not receive a beta
function. Use it only as specified in the reviewed hard-scale expression,
without multiplying it a second time. Derive `Lb` from the actual matching
scale as `2*log(mu4/T) - 2*log(4*pi) + 2*EulerGamma`, and `Lf = Lb + 4*log(2)`.
The shortcut `Lb = 2*log(xi4)` applies only to the default hard-scale policy.
Preserve explicit reviewed log inputs instead of overwriting them.

## Mathematica To C++ Conversion

Unicode Greek characters and their known Wolfram escaped spellings share one
source identity during conversion and symbol-audit validation. ASCII lookalikes
are not aliases: `phi`, `\[Phi]`, and `\[CurlyPhi]` are distinct source symbols.
Explicit field mappings remain authoritative. When captured potential output
contains a source-symbol-to-compiler-name map, `merge-output` aligns the matching
3D field names, audit identifiers, and symmetry field groups with that map while
retaining source spelling, field slots, and reflection grouping. Conflicting
maps or collisions with other field/input/coefficient names block the merge;
missing source evidence never licenses a guessed rename. A merge resets compile
approval and the resulting contract requires fresh review.

Use the stable two-map route:

1. map raw Mathematica/DRalgo symbols to temporary CForm-safe symbols;
2. call CForm or the Python converter;
3. map temporary symbols to final compiler identifiers recorded in the Mathematica Symbol Audit.

Greek and reserved identifiers must be repaired consistently. Examples:

- `\\[Lambda]` or `lambda` -> `lam`;
- `\\[Lambda]1H` or `lambda1H` -> `lam1H`;
- `class`, `double`, `namespace`, `yield` -> `class_`, `double_`, `namespace_`, `yield_`.

When a temporary represents the square of one quantity, use the `_sq` suffix, such as `h_sq`, `g2_sq`, or `T_sq`.

Use `ptagent/ptagent_3deft/wolfram/cpp_greek_export.wl` when a Wolfram-side helper is useful. It keeps Mathematica `Pi` as the mathematical constant by default and only treats Pi-like symbols as variables when explicitly requested.

## PhaseTracer Renderer Policy

The generated model must:

- be an `EffectivePotential::Potential` subclass;
- evaluate the reviewed total 3DEFT potential only;
- compute running 4D inputs, matched 3D parameters, optional 3DUS running, and `V3D` in separate functions;
- convert PhaseTracer coordinates to 3D fields with the default `field_i_3d = phi[i]/sqrt(T)`;
- return the default `V(phi,T) = T * V3D(fields_3d,T)`;
- generate `run_model.cpp` as the default executable entry point;
- avoid artifacts or physics terms outside the reviewed 3DEFT contract data.

Supported field normalizations are identity, `phi/sqrt(T)` (`canonical_3d`),
and `phi*sqrt(T)` (`phase_tracer_times_sqrt_t`). Extra wave-function factors
such as `Z(T)` are unsupported and block compilation; never replace them
silently with a leading-order map. 3DEFT numerical evaluation requires finite
fields and parameters, strictly positive temperature and RG/matching scales.
Negative mass squares, couplings and potential values remain permitted.

Reference comparisons must be layer-local. Because the generated PhaseTracer
model may run 4D RG, 4D-to-3D matching, and optional 3DUS running at runtime,
do not compare a Mathematica/DRalgo V value against the full `V(phi,T)` path
unless both sides use the same runtime scales and evolved parameters. The
stable comparison mode is: freeze a reviewed set of 3D/3DUS parameters, evaluate
the DRalgo-derived `V3D` expression directly at fixed 3D fields, evaluate the
generated C++ direct-V helper with the same fixed parameters, then compare only
those two `V` values.

The generated `run_model.cpp` must therefore print `Reference.mode
fixed_3d_parameters_direct_v3d`, `Reference.DirectV3D`, `Reference.V`, and the
corresponding expected/difference values from metadata. A full `model.V(phi,T)`
value may be printed only as `Reference.FullPathVDiagnostic`; it is not the
Mathematica/C++ conversion check.

The Mathematica side of this check is performed by `compare-mathematica`. It
must generate a Wolfram script that maps compiler identifiers to Mathematica-safe
temporary symbols, applies replacement rules from `metadata.fixed_3d_reference`,
evaluates the reviewed `V3D_LO`, `V3D_NLO`, `V3D_NNLO`, total `V3D`, prefactor,
and `V = prefactor*V3D`, and writes a JSON report. Do not ask the user to
transcribe these numbers by hand.

The ordinary reference point is diagnostic only when
`reference_expected_v=not_applicable`. Preserve that reviewed point even if it
produces a nonfinite or non-real value. The compiler may report bounded,
scale-relative alternative diagnostic points, with their evaluation results,
but must not substitute one automatically. Alternatives remain unconfirmed and
require an edited contract and renewed approval before use. Keep generating
the C++ project with an explicit warning and `generated_unverified` status when
no valid approved reference is available in a legacy approved contract. New
approvals require a passing `check-reference` preflight; an old approval does
not turn a failed point into a valid one. A numeric `reference_expected_v`
supplied by the user is a required check, not a value to replace or fit.

Generation, build/run success, and numerical verification are distinct states.
`check-model` records `generated_unverified`, `passed`, or `failed`, using finite
reference values and `abs(actual-expected) <= atol + rtol*max(abs(actual),abs(expected),1)`.
Keep raw values, differences and tolerances in the report. No valid reference
point means `generated_unverified`, never numerical success; do not proceed to
production phase scans on an unverified or failed model.

Each `check-model` attempt keeps full configure/build/run stdout and stderr in
`logs/model-check-*/`, including successful stages and partial timeout output.
The per-attempt `report.json` and current `model_check.json` identify stage
commands, exit codes, elapsed seconds and log paths. `execution_status` describes
process execution separately from numerical validation `status`. Stages skipped
by `--configure-only` or `--no-run` are not reported as executed. Read the saved
logs instead of rebuilding or rerunning just to retrieve diagnostics; later
attempts do not overwrite earlier attempt records.

The fixed-expression comparison is `fixed_3d_expression_check`; it does not
independently validate the source DRalgo RG/matching chain. A
`source_dralgo_end_to_end_comparison` must obtain its matched parameters and
active perturbative contributions from the original DRalgo calculation at
multiple positive temperatures and field points, with identical scales and
field maps. An unavailable source/runtime or an unexecuted comparison must be
reported as `not_run` or unverified, never passed. Inactive orders are
`not_applicable`, not invented zero contributions.

Use `compare-mathematica --mode source-dralgo --template CONTRACT --project-dir
PROJECT --points points.json --source-driver source_numerics.wl` for this separate
comparison. The points file is a list of `{"temperature": ..., "fields": [...]}`
records with at least two temperatures and two distinct field points per
temperature. Build the generated `source_probe` target first.

This interface requires a reviewed source-native numerical driver defining
`PTAgentSourceReference[temperature, fields, settings]`; it does not automatically
derive such a driver from an arbitrary DRalgo script. The driver must execute
the original RG/matching/running route and return the requested settings,
points, stage statuses, matching parameters and order-by-order values. Do not
construct this reference by translating the contract formulas back to Wolfram.
A missing driver, unavailable runtime or failed source execution is recorded as
`not_run`. Synthetic driver tests validate the comparison interface only, not
an actual DRalgo model or its physics. Source-native xSM numerical validation
remains incomplete until that driver and a working DRalgo runtime are available.

The contract must use:

- `<!-- PTAGENT 3DEFT contract_v1 -->`
- `ptagent.3deft.contract.v1`
- `potential_mode = external_total_3deft`

All unresolved physics-sensitive cells remain blockers. Regenerate `three_deft_user_questions.md` instead of guessing.
