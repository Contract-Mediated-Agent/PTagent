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

The deterministic precedence is explicit parameter JSON > SLHA > source
parameter mapping. Use names in the imported expressions or normalized SLHA
names. There are no hard-coded Yukawa types, electroweak values, source-example
benchmarks, mass-to-coupling inversions or tree/loop parameter equalities.
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
- No phase filter or symmetry merge by default. For a reviewed reflection,
  suggest a negative threshold with tolerance `0.01*L`, where L is a sourced
  nonzero reference field scale. If the field step is known, use at least ten
  steps. Check factor-of-two sensitivity. No reference scale means no guessed
  threshold. Suggestions stay disabled until confirmed.

Supported `--choices` keys are `backend`, `resummation_scheme`,
`counterterm` (`none` or `source_polynomial`), `goldstone` (`included`),
`photon` (`retained`), `cubic_power_policy` (`positive_part` or `signed_abs`),
`phase_filter` (`none` or `negative_field_threshold`),
`phase_filter_field`, `phase_filter_threshold`,
`phasetracer_symmetry` (`none` or `z2_reflection`), and
`symmetry_generators` (a list of field-name lists, one simultaneous flip per list).
Unsupported choices fail; they are never replaced with a different prescription.

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
