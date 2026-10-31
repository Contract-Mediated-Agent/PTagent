# SARAH/Vevacious++ Workflow

Use this route only for deterministic SARAH model exports. PTagent does not
infer a SARAH model from prose or a free-form Lagrangian.

## Source-First Preparation For All Models

`prepare-sarah` consumes the actual exported tree potential, parameter map,
mass sectors and optional companion metadata. It does not branch on model
names or supply a reconstructed model-specific potential. The same
`source-first-4d-v1` rules apply to paper workflows as review recommendations.

```bash
python <skill-dir>/scripts/ptagent_cli.py prepare-sarah \
  --vin MODEL.vin --parameter-map ScaleAndBlock.xml \
  --slha spectrum.slha --thermal-metadata ptagent_thermal_metadata.json \
  --run-dir /path/to/fresh-task \
  --choices '{"backend":"cosmotransitions"}'
```

The SLHA, metadata and `--model-source` archival directory are optional for a
partial import. Missing data remains visible in the generated contract and the
consolidated questions. Missing thermal matrices block Parwani/Arnold-Espinosa;
missing numerical parameters or renormalization scale block numerical execution.
A successful import is not a claim that the model is ready to run.

Downstream `11. Numerical Settings` use the shared 4D scan-approval gate. These
numerical controls are separate from the prepared-physics digest: confirming
`Tmax` does not require repeating SARAH export or changing physical inputs.
Changing `Tmax` still requires a new matching `Tmax_approved_value` confirmation.

If the user has not specified a benchmark, inspect the selected model's
`SPheno.m` before asking for numbers. Recommend its `DefaultInputValues` as the
starting point, with the source path and hash. `prepare-sarah --model-source`
and the default export/preparation route write `spheno_benchmark.json` with
static defaults, MINPAR/EXTPAR mappings, boundary/matching relations and
`ParametersToSolveTadpoles`. No SPheno execution is needed to read this file.
The static reader does not evaluate arbitrary Wolfram code or apply defaults.
The separate native tree exporter calls SARAH's core `SolveTadpoleEquations`
and exports bounded relation ASTs for deterministic binding. Read
`sarah_tree_bindings.md` for the interface and limits. No SPheno executable,
generated Fortran or spectrum run is needed. Never choose that route merely
because the source declaration file is called `SPheno.m`.

Separate (1) file-provided inputs, (2) quantities computable through supported
source relations and tree tadpole equations, and (3) remaining SM inputs,
renormalization scale and physical choices. Do not claim that DefaultInputValues
contains a complete spectrum or all calculation settings. Do not transfer a
previous THDM example's SM values, scale, Yukawa truncation or tree/loop equality
to another task. Do not require SPheno as the only way to complete a point.
When the current converter cannot resolve a source relation or branch, state
that limitation and request the missing binding, not an unrelated new benchmark.

Use the automatically discovered `ptagent_sarah_tree_relations.json` before
asking about missing bindings. The review preview applies example inputs only
in a separate proposal calculation, never to the actual contract. It lists
remaining independent source inputs and supported derived targets. Accept a
source benchmark, remaining SM input set, Q and tree/loop prescription in one
batch. For a no-spectrum tree-input calculation, recommend `equal_tree` for
missing Loop aliases, explaining that CW/thermal loops remain but loop matching
is absent. Do not ask again for algebraically dependent quantities after that
batch is accepted. Missing source values, multiple branches and unsupported
expressions remain explicit blockers. Do not claim full loop matching or a
physically validated vacuum from tree residuals alone.

Present acceptance of the recommended example and all remaining settings in
one batch, using `benchmark_request.missing_inputs` and the source mappings.
Preserve explicit user parameters and SLHA inputs. Bind accepted inputs through
`--slha` or `--parameters` JSON; final compilation approval is still required.
If the file/defaults are absent or unsupported, request explicit inputs.
Symbolic imports can continue with unresolved inputs.

For a prepared task, apply the accepted batch with `prepare-sarah` in a fresh
`--run-dir`, reusing the exported `.vin`, parameter map, companion metadata and
`--model-source`. Carry forward prior explicit parameters/choices; supply changed
bindings through `--parameters`/`--slha` and policies through `--choices`. Do not
edit physical rows in the existing prepared Markdown and then run `resolve`:
that invalidates its prepared-physics digest. No repeat Wolfram export is needed.
Source relations not supported by the deterministic converter remain blockers;
neither a source recommendation nor a test point authorizes guessed bindings.
Section 11 scan controls retain their separate approval path described above.

The prepared review packet includes unresolved parameters/scale and all pending
policy proposals, omits requests to reconstruct already imported formulas, and
preserves explicit overrides without treating them as compilation approval.
The graph distinguishes sourced expressions, calculated thermal/binding values,
explicit structured inputs, pending recommendations and unavailable values.
`sarah_preparation_checks.json` reports `numerical_status=not_run` for an unbound
point; `passed=true` then refers only to the completed import checks. A finite
point check is reported only with actual reference-point/tadpole/mass records,
and still does not establish vacuum stability or physical suitability.

The deterministic precedence is explicit parameter JSON > SLHA > source
parameter mapping. Use names in the imported expressions or normalized SLHA
names. Model-local SPheno example values are recommendations until reviewed
and explicitly bound. There are no hard-coded Yukawa types, electroweak values,
model-specific benchmarks, inverse fits or implicit tree/loop equalities.
Native source relations and an explicitly accepted `equal_tree` prescription
are supported; their provenance and limitations are recorded separately.
Unknown names fail instead of being ignored. An unresolved input requires
source evidence or a user value, not an LLM fit.

Generic physical proposals, subordinate to source/user choices, are:

- Arnold-Espinosa with positive-part cubic powers. Missing thermal data does not
  silently select an unresummed theory.
- Retain the complete imported spectrum, without special Goldstone/photon
  removal. Selective removal needs a separately reviewed contract; do not
  identify a Goldstone from a model name or smallest eigenvalue.
- Propose the nonzero imported extra polynomial as a separate correction;
  otherwise propose no counterterm. Its physical interpretation still needs review.
- Radiation counts are the retained-mode inventory, not an assumed SM bath.
  The manifest lists every count. Explicitly review massless-vector polarization
  conventions and unlisted decoupled particles. `num_boson_dof` and
  `num_fermion_dof` in parameter JSON override the proposal.
- Derive phase-handling proposals from the imported potential's sign symmetries.
  PhaseTracer receives generator rows. CosmoTransitions receives independent
  sign cuts for single-field reflections, or `symmetry_orbits` trajectory
  comparisons for coupled reflections. A pointwise cut can discard both entire
  phases when a coupled-symmetry branch crosses the boundary. For a single-field reflection,
  suggest a negative threshold with tolerance `0.01*L`, where L is a sourced
  nonzero reference field scale. If the field step is known, use at least ten
  steps. Check factor-of-two sensitivity. Without a sourced scale or field step,
  use the exact zero boundary. These are concrete pending-review proposals, not
  user confirmations. Check all potential terms before approval and preserve
  explicit user overrides; never recommend disabled filtering by default.
  Coupled-orbit grouping is exposed as `symmetry_phase_history`, with native intervals
  recorded in `symmetry_phase_groups`. Matching checks the common interval without
  extrapolation; unequal-domain matches stay in `symmetry_phase_associations`, not
  collapsed full histories. Tc/Tn calculations retain the full phase dictionary and exclude
  only verified partners of the current source, not orientations of distinct phases.

Supported `--choices` keys are `backend`, `resummation_scheme`,
`counterterm` (`none` or `source_polynomial`), `goldstone` (`included`),
`photon` (`retained`), `cubic_power_policy` (`positive_part` or `signed_abs`),
`phase_filter` (`none`, `negative_field_threshold` or `symmetry_orbits`),
`phase_filter_field`, `phase_filter_threshold`,
`phasetracer_symmetry` (`none` or `z2_reflection`), and
`symmetry_generators` (a list of field-name lists, one simultaneous flip per list).
Unsupported choices fail; they are never replaced with a different prescription.
For `symmetry_orbits`, `phase_filter_field` is a comma-separated simultaneous
field group and `phase_filter_threshold` is a positive absolute comparison
tolerance. Multiple detected generators are proposed as separate contract rows.

Preparation records source/configuration hashes, the bound point, raw tadpole
residuals and mass eigenvalues when evaluable, proposals and software versions.
These checks do not solve tadpoles or establish vacuum stability. For a loaded
supported SARAH model, the bundled exporter extracts full component data and
the shared Python engine constructs the thermal invariants and basis mappings.
For an existing `.vin` without this companion data, the partial-import rules
still apply. No model-name dispatch or hand-written model potential is used.

Present the contract, graph and all remaining questions together. Once blockers
are resolved, obtain final approval of the exact contract hash:

```bash
python <skill-dir>/scripts/ptagent_cli.py approve-sarah \
  --template /path/to/task/contract_template.md \
  --expected-sha256 <hash-returned-by-prepare-sarah> \
  --user-approval-quote '<actual user approval>'
```

Defaults are proposals, not prior confirmation. Only a real user approval may
unlock compilation. Changed inputs invalidate approval; use a fresh prepared
task for revised parameters or policies. Existing prepared v1 contracts retain
hash verification, but no new v1 model-specific preset is generated.

## Import Existing Output

The recommended route needs no SARAH installation:

```bash
python <skill-dir>/scripts/ptagent_cli.py import-sarah \
  --vin MODEL.vin \
  --parameter-map ScaleAndBlock.xml \
  [--thermal-metadata ptagent_thermal_metadata.json] \
  [--slha spectrum.slha] \
  [--parameters parameters.json] \
  [--run-dir RUN_DIR]
```

Accept only Vevacious++ v2 XML. Do not treat an arbitrary `.vin`, `.m`, or
`.wl` file as a SARAH import. The importer rejects DTDs, entities, external XML
resources, oversized files, arbitrary expression syntax, unsupported schemes,
complex SLHA blocks, malformed matrices, and unresolved numerical parameters.

The importer copies source files into `input/` and writes deterministic records
under `proof_materials/`. `contract_template.md` remains the user-reviewed
source of truth. Imported matrix entries use `source_imported`, which is valid
only when the contract contains the input paths and SHA256 provenance.

Without SLHA or explicit JSON, symbolic import may finish but numerical smoke
and phase-transition runs remain blocked. Without complete thermal metadata,
tree-level and zero-temperature spectra remain usable, but Parwani and
Arnold-Espinosa are blocked. Do not fill either gap with an LLM guess.

## Export From SARAH

For a model name or the literal README placeholder
`/path/to/SARAH/Models/THDM`, find the existing installation automatically:

```bash
python <skill-dir>/scripts/check_environment.py --sarah-model /path/to/SARAH/Models/THDM
python <skill-dir>/scripts/ptagent_cli.py export-sarah \
  --model /path/to/SARAH/Models/THDM \
  --choices '{"backend":"cosmotransitions"}' --run-dir /path/to/fresh-task
```

No path confirmation is needed. The resolver uses an explicit root, a root
containing an explicit real model directory, or `PTAGENT_SARAH_ROOT`/`SARAH_ROOT`
before standard Wolfram/Mathematica Applications directories. If necessary, a
bounded read-only Wolfram query checks ``FindFile["SARAH`"]`` and `$Path`. An
unversioned SARAH package/alias is preferred, followed by numerically ordered
versioned directories; the selected root is recorded in the export manifest.
The same import and provenance checks apply to every discovered model.

When SARAH is missing, report the search results and tell the user to install it
in their Wolfram/Mathematica Applications directory or configure an existing
root. Never install automatically. A package-path timeout requires diagnosing
the Wolfram runtime, not asserting that SARAH is uninstalled. If SARAH exists but
the requested model is missing, report that distinct problem. Never substitute
an installed model for a nonexistent explicit custom path.

Explicit paths remain supported:

```bash
python <skill-dir>/scripts/ptagent_cli.py export-sarah \
  --sarah-root /path/to/SARAH \
  --model MODEL_NAME \
  [--model-search-path /path/to/models] \
  [--slha spectrum.slha] \
  --run-dir RUN_DIR
```

The wrapper runs `MakeVevacious[Version -> "++"]`, then the bundled companion
exporter. The default `--preset auto` prepares every model with the same rules above.
Pass `--parameters` and `--choices` to preserve explicit inputs. `--preset none`
retains the raw-import worksheet. The wrapper does not modify the installed
SARAH files. Its in-memory source adapter registers unmixed scalar `TreeMass`
expressions as identity sectors because SARAH 4.14's Vevacious++ writer visits
mixed sectors only; additions are recorded in `unmixed_scalar_sectors`.

`ptagent_sarah_components.json` records the full canonical scalar potential,
the complete Weyl mass-squared trace, gauge representation contractions, and
ordered sector mappings. `sarah_components.py` derives the thermal matrices
and checks the background tree potential, scalar matrices, fermion trace and
high-temperature supertrace against the exported `.vin`. See
`sarah_thermal_metadata.md` for conventions and support boundaries. Missing
numeric parameters do not prevent this symbolic calculation. Do not ask users
to compute or enter thermal tensors when this route is available.

Unsupported source structures produce precise blockers in the metadata, while
the zero-temperature contract remains available. Existing explicitly supplied
complete companion metadata remains supported and is validated by the importer.

On macOS, `wolframscript` uses WSTP shared memory for local evaluation. Some
restricted agent sandboxes can block this transport and leave `wolframscript`
at high CPU before a kernel process is launched. Diagnose this by running the
licensed `WolframKernel` directly with a trivial batch expression. If the
kernel succeeds, request permission to run the existing `wolframscript`
outside the restricted sandbox. Do not change persistent Wolfram configuration
or ask the user to reactivate Mathematica unless the direct kernel check also
fails.

## Review Boundary

The following review boundary applies to every model, including partial imports.

Follow `contract_review.md`: show the current Markdown contract and its
evidence-bound graph together, including a partial import's unavailable thermal
data and compile blockers. SARAH exports do not by themselves approve phase
filtering, resummation, Goldstone/photon choices, or a numerical benchmark.

Do not ask the user to approve every imported matrix element. Ask for one
physics-policy batch covering species inclusion, resummation, Goldstone/photon
and gauge-mode policy, counterterms, symmetry or phase handling, and backend.
Include missing numerical inputs and renormalization scale in this same batch;
omit questions already answered by source evidence or explicit user choices.
For symmetry or phase handling, use the same deterministic tree-level
sign-flip analysis as the paper workflow. Imported metadata may support the
recommendation, but neither the metadata nor the V0 check silently approves a
PhaseTracer symmetry or a CosmoTransitions filter threshold.
Resolve the contract again after that batch. Ask separately for final compile
approval, as in the ordinary 4D workflow.
