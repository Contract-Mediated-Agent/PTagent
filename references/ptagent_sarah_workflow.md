# SARAH/Vevacious++ Workflow

Use this route only for deterministic SARAH model exports. PTagent does not
infer a SARAH model from prose or a free-form Lagrangian.

## Deterministic THDM Preparation

The audited real type-I THDM distributed with SARAH 4.14.5 has a complete
code-driven route. It is not a generic adapter for every model called THDM.
`references/sarah/thdm-type-i-v1.json` versions its defaults and source
signatures. The backend checks the source files and all eight exported mass
sectors, differentiates the full eight-real-scalar potential, solves the tree
tadpoles, contracts thermal tensors, and independently checks the high-T
supertrace. The original exports remain unchanged.

For existing exports, no Wolfram execution is needed:

```bash
python <skill-dir>/scripts/ptagent_cli.py prepare-sarah \
  --vin /path/to/THDM.vin --parameter-map /path/to/ScaleAndBlock.xml \
  --model-source /path/to/SARAH/Models/THDM --run-dir /path/to/fresh-task
```

This reads scalar example inputs from the audited `SPheno.m` numeric
`DefaultInputValues` list. It does not execute arbitrary Wolfram source during
import. Remaining defaults are v=246 GeV, Q=246 GeV, fixed illustrative SM inputs,
MSbar/Landau, Arnold-Espinosa with positive-part cubic powers, Loop=Tree and no
finite counterterms, all exported modes retained including Goldstones and the
thermal photon, and radiation d.o.f. 32/90. The CT mirror filter is phi2 < -1 GeV;
PhaseTracer uses simultaneous reflection only. The full matrices and thermal
terms are checked for that symmetry. These are reviewable preset conventions,
not source-mandated physics or an experimentally fitted benchmark.

Explicit user choices take precedence. Example:

```bash
python <skill-dir>/scripts/ptagent_cli.py prepare-sarah \
  --vin /path/to/THDM.vin --parameter-map /path/to/ScaleAndBlock.xml \
  --model-source /path/to/SARAH/Models/THDM --run-dir /path/to/another-task \
  --parameters '{"Lambda1":0.12,"Q":200}' \
  --choices '{"backend":"phasetracer","resummation_scheme":"Parwani"}'
```

Both JSON arguments also accept files. Native SLHA symbols are supported;
explicit aliases override SLHA, which overrides source/preset defaults. Do not
invent physical-mass inverse relations. Inputs such as mh/mH/mA need a separately
validated inverse adapter and are currently rejected by this route. Unsupported
particle exclusions, counterterms or modified source conventions stop with an
error instead of falling back to different physics. `source_polynomial` is the
available counterterm override when supplied loop/tree values define a nonzero
ExtraPolynomialPart. Paper and manual-contract routes remain available for
other reviewed prescriptions.

Preparation writes `sarah_preparation.json`, `sarah_preparation_checks.json`, a
complete contract, validation, a pending-question checklist, and a matching
provenance graph under `proof_materials/`. Check the command exit status and
machine reports. Matrix shape/symmetry alone is not a physical check. The point
must pass tadpole residual, scalar curvature, Goldstone and tree quartic checks;
these do not establish the global or loop-corrected vacuum. A failed fit or check
does not prove that a requested spectrum is impossible.

Show the contract and graph together with the preset summary and any explicit
overrides. There are no incremental policy questions when the preset covers all
inputs: request one final approval of the complete specification. Never claim
defaults were already confirmed. Only after the user actually approves:

```bash
python <skill-dir>/scripts/ptagent_cli.py approve-sarah \
  --template /path/to/task/contract_template.md \
  --expected-sha256 <hash-returned-by-prepare-sarah> \
  --user-approval-quote '<actual user approval>'
```

Then use the existing `compile`, `check-model` and `run` workflow. Approval updates
the graph, resolved contract and validation together. Compilation rejects stale
prepared inputs or changed construction choices. This first adapter freezes a
numerical point: new inputs require another prepared task and approval, not edits
to generated constants. Preparation never overwrites an existing contract.
Do not generate ad hoc derivation, fitting or completion scripts to bypass a
failure. Report unsupported cases in one batch and retain the diagnostic files.

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

Use this optional route only when the user already has SARAH and a loadable
model:

```bash
python <skill-dir>/scripts/ptagent_cli.py export-sarah \
  --sarah-root /path/to/SARAH \
  --model MODEL_NAME \
  [--model-search-path /path/to/models] \
  [--slha spectrum.slha] \
  --run-dir RUN_DIR
```

The wrapper runs `MakeVevacious[Version -> "++"]`, then the bundled companion
exporter. For `--model THDM`, its default `--preset auto` invokes the audited
preparation route above, including source archival and fixed defaults. Pass
`--parameters` and `--choices` to preserve explicit user inputs. `--preset none`
explicitly retains symbolic-only import. Other models invoke the same importer
used by `import-sarah`. The wrapper does not modify SARAH. If a model does not expose the tensor data required by the
companion schema, the metadata records a deterministic blocker instead of
inventing thermal masses; the zero-temperature contract is still produced.

On macOS, `wolframscript` uses WSTP shared memory for local evaluation. Some
restricted agent sandboxes can block this transport and leave `wolframscript`
at high CPU before a kernel process is launched. Diagnose this by running the
licensed `WolframKernel` directly with a trivial batch expression. If the
kernel succeeds, request permission to run the existing `wolframscript`
outside the restricted sandbox. Do not change persistent Wolfram configuration
or ask the user to reactivate Mathematica unless the direct kernel check also
fails.

## Review Boundary

The fixed THDM route above uses one final preset review. The following partial
import rules apply to other models and explicit symbolic-only imports.

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
