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

The agent must not create the user's physics DRalgo model file from contract rows. The executable physics source is always a user-supplied Mathematica/Wolfram DRalgo file.

Accepted input files:

- `.m`
- `.wl`

Reject TeX, Markdown, PDF, arXiv archives, old JSON-fence templates, and non-3DEFT PTagent contracts for this workflow.

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

- `input/`: task-local copy of the reviewed user-supplied `.m`/`.wl` source; follow-up steps must depend on this copy, not on the original upload path.
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

Record the user's exact merged-contract approval and compile:

```powershell
python <skill-dir>/scripts/ptagent_cli.py --ptagent-engine 3deft approve --template <run_dir>\three_deft_contract_merged.md --gate phasetracer_compile --user-approval "<exact user sentence>"
python <skill-dir>/scripts/ptagent_cli.py --ptagent-engine 3deft compile --template <run_dir>\three_deft_contract_merged.md
```

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
- PhaseTracer compile approval: after `three_deft_contract_merged.md` has no compile blockers, ask the user to open and review it, then record their exact approval sentence.

Do not ask the user to hand-copy DRalgo-derived outputs before DRalgo has run. These are agent/DRalgo backfill items:

- scalar index maps from `PrintScalarRepPositions[]`;
- 3D coefficients from `PrintCouplings[]` and `PrintCouplingsUS[]`;
- effective potential pieces from `PrintEffectivePotential[...]`;
- beta functions from `BetaFunctions4D[]`, `BetaFunctions3DS[]`, and `BetaFunctions3DUS[]` when available.

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

## DRalgo Source Audit

The audit should summarize the reviewed source; it should not synthesize a new model file.

Focus on:

- loading block and package assumptions;
- `Group`, `CouplingName`, `RepAdjoint`;
- `RepScalar` entries, component counts, VEV/background roles, and raw source labels;
- `RepFermion1Gen`/`RepFermion` entries. For SM extensions, Standard Model one-generation fermion content is a review hint only; custom fermions require explicit review;
- scalar invariants and gradient helpers such as `CreateInvariant`, `GradMass`, `GradQuartic`, `GradCubic`, and tadpole helpers;
- Yukawa invariants and the mapping between numeric coupling, invariant label, and operator, for example `yt -> YukawaTop -> yt*(H qL uR)` when that operator is actually present;
- `AllocateTensors[...]` and `ImportModelDRalgo[...]` argument order and any explicit `Mode`;
- `PerformDRhard[]`, the single active `PerformDRsoft[...]`, and ordered print calls;
- VEV/background definitions used before `CalculatePotentialUS[]` and `PrintEffectivePotential[...]`.

Orders are source-driven. Copy explicit ordered print calls present in the reviewed source or DRalgo output, such as `PrintEffectivePotential["NNLO"]`, `PrintScalarMass["NLO"]`, `PrintScalarMassUS["NLO"]`, or `PrintPressure["LO"]`. Do not recommend LO/NLO/NNLO and do not ask the user to choose an order when the source already determines what is printed.

## RG And Scales

The user-facing scale question should stay simple:

- hard matching scale `mu4`;
- hard scale factor `xi4`, default `1`;
- `mu_3_scale`;
- `mu_3_us_scale`.

Implementation details such as ODE step count, internal beta-variable names, and solver choices should not be exposed as ordinary user questions.

DRalgo beta-function conventions:

- `BetaFunctions4D[]` is interpreted as derivatives with respect to `log(mu)`.
- DRalgo gauge beta rows may be for `g^2`; when the contract input is `g`, convert the RHS explicitly and record the conversion.
- `BetaFunctions3DS[]` is a soft-stage audit table and is not the ultrasoft running layer.
- `BetaFunctions3DUS[]` evolves matched 3D parameters from `mu_3_scale` to `mu_3_us_scale`.

For 3DUS running, use the analytic logarithmic update when the RHS has no explicit running-scale or self-state dependence. If the RHS depends on the running scale or state, generate numeric evolution in `log(mu_3_us)`.

`xi4` controls `mu4`, `Lb`, and `Lf`; it is not a running coupling and must not receive a beta function.

## Mathematica To C++ Conversion

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

The ordinary compile-time reference point is diagnostic only when
`reference_expected_v=not_applicable`. If all candidate reference points produce
non-finite or non-real values, keep generating the C++ project and report a
clear warning in stdout and metadata. Treat it as a hard blocker only when the
user supplied a numeric `reference_expected_v` that must be checked.

The contract must use:

- `<!-- PTAGENT 3DEFT contract_v1 -->`
- `ptagent.3deft.contract.v1`
- `potential_mode = external_total_3deft`

All unresolved physics-sensitive cells remain blockers. Regenerate `three_deft_user_questions.md` instead of guessing.
