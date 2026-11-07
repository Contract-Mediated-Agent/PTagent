---
name: ptagent
description: Build reviewed finite-temperature phase-transition models from papers, SARAH/Vevacious++ exports, and DRalgo/3DEFT sources, then compile local contracts to CosmoTransitions or PhaseTracer without external model services.
---

# PTagent

Use this single installed skill as the product entrypoint for PTagent. It
contains the router instructions, both internal workflows, wrapper scripts, and
a bundled backend source tree under `backend/ptagent`.

Users should be able to install this one skill directory from
`Contract-Mediated-Agent/PTagent` and then invoke it through their agent's skill mechanism
(for example `$ptagent` in Codex); they should not need to clone the repository
or run `pip install ptagent` / `pip install -e .`.

## What Is Bundled

```text
SKILL.md
agents/openai.yaml
references/model_program_contract.md
references/ptagent_sarah_workflow.md
references/sarah_thermal_metadata.md
references/ptagent_4d_workflow.md
references/ptagent_3deft_workflow.md
scripts/_bootstrap.py
scripts/check_environment.py
scripts/check_model.py
scripts/compile_template.py
scripts/prepare_paper.py
scripts/ptagent_cli.py
scripts/render_contract_graph.py
assets/examples/xsm_test.m
backend/ptagent/
requirements.txt
```

Python dependencies are still external.

## Required First Step: Environment Gate

The agent MUST run the environment gate before routing.

For every new PTagent skill task or conversation, the first action MUST run
the environment gate before routing to 4D or 3DEFT work:

```bash
python <skill-dir>/scripts/check_environment.py
```

Select `--workflow 4d` for a paper/arXiv or manual 4D request, `--workflow sarah`
for SARAH, and `--workflow 3deft` for DRalgo. This input-type hint limits
preflight probes; it does not process the model or bypass the gate. A pure 4D
preflight does not start Wolfram or search SARAH package paths. Unchecked
operations remain blocked until their own preflight is run; omitted workflow
retains the full environment check.

Use the installed skill directory as `<skill-dir>`. Scripts inside a product
checkout also find their own bundled backend, so normally omit `--project-root`.
Use that override only to select another product/backend checkout containing
`backend/ptagent` or `ptagent`; it is not the task directory or workspace parent.
If the user
provides an existing PhaseTracer source path, pass
`--phasetracer-root <PhaseTracer-source-root>`.
For a SARAH model request, also pass `--sarah-model <model-name-or-directory>`;
the literal documentation path `/path/to/SARAH/Models/THDM` is accepted.

Read the JSON output before doing anything else. Treat the environment gate as
an advisory preflight plus per-operation blockers, not as one global stop/go
flag. The `ready` field only means baseline 4D extraction can start; before any
specific operation, inspect `operation_blockers[<operation>]` and continue only
when that list is empty.

Check `4d_extract_pdf` for PDF inputs, `sarah_prepare` for deterministic SARAH
preparation, and `review_graph` before rendering the contract graph. Inspect
`python_modules.current` and `python_modules.runtime` for failed imports and
their diagnostics. A working skill interpreter does not imply a working
CosmoTransitions runtime (or vice versa). Use Python 3.11 or newer.

If `ready` is false, stop. Do not continue to extraction, question mode, compilation, or code generation
until the reported baseline blockers are resolved. A true `ready` value does
not override a nonempty blocker list for the requested operation.

Python and CosmoTransitions are reported separately. CosmoTransitions must be
available in the configured runtime Python before 4D CosmoTransitions compile,
check, or run operations. PhaseTracer must be found or configured before
PhaseTracer compilation, backend comparison, or generated PhaseTracer model
checks. On Windows, Linux-style roots such as `/home/user/src/PhaseTracer`
are validated inside installed WSL distributions. 3DEFT extraction of local
sources requires a Wolfram/wolframscript runtime; DRalgo itself is additionally
required before running the marked DRalgo workflow.

For SARAH routes, inspect `operation_blockers.sarah_import` or
`operation_blockers.sarah_export`. Importing existing Vevacious++ files needs
only the Python dependencies. Exporting them from a SARAH model additionally
needs `wolframscript` and an installed SARAH package. The gate automatically
searches Wolfram/Mathematica package directories; an explicit `--sarah-root`
or configured `PTAGENT_SARAH_ROOT`/`SARAH_ROOT` takes precedence. Use the reported
root/model directly without asking the user to confirm its location. If missing,
explain the checked paths and request installation of the missing dependency;
never install SARAH or Wolfram automatically. A failed package-path probe is not
proof that SARAH is absent.

Wolfram discovery and execution use the shared runtime reported under
`wolfram.runtime`. A bounded startup probe may fall back to the exact local
kernel reported by `wolframscript`; it never substitutes another installation
or retries an already started model evaluation. If `runtime.mode` is `kernel`,
reuse its `kernel` path as `PTAGENT_WOLFRAM_KERNEL` for subsequent task commands.
Keep the same task-local temporary directory (`PTAGENT_WOLFRAM_TMPDIR`) and
execution context. Successful read-only probes are reused within a command;
separate commands revalidate startup. Do not persistently reconfigure Wolfram.
A timeout or package load error does not prove missing installation, activation
failure or a permission problem. Inspect the recorded startup attempts and
stage-specific diagnostics before proposing installation or requesting broader
execution access. Proceed only after the requested operation's check succeeds.

Do not install Python packages, download PhaseTracer, install DRalgo, or modify
persistent config until the user explicitly approves. When PhaseTracer is
missing and the user approves a download, use the `phasetracer_download_command`
reported by the gate; it downloads the latest PhaseTracer `main` branch. If the
user already has PhaseTracer or provides a path, validate that path only; do not
check or enforce its version.
Validation means checking the generated-code interfaces and compiling, linking
and loading a minimal consumer of the existing libraries, not accepting a
directory name. Without an explicit/configured root, use the gate's bounded
local discovery and record its selected root and rejected candidates. Never
silently replace an incompatible explicitly selected checkout. Missing builds,
architecture mismatches and restricted probes require distinct diagnostics.
Distinguish `phasetracer_source_ready`, `phasetracer_build_required`, and
`phasetracer_runtime_ready`: eligible source can be initialized, emitted against,
or built after approval without already having compiled libraries. Runtime
readiness is established only by a successful native compile/link/load check;
source eligibility alone is never evidence that a calculation ran.
When only the dependency build is missing, request approval to build that
checkout's sources inside the task build directory and obtain any missing
prerequisites, not to download a duplicate. Generated projects keep native
libraries inside their own build directory; do not manually rebuild shared
PhaseTracer installations as a workaround. Regenerate older generated projects
to obtain the isolated build layout. Include
download and build scope together in the dependency request for a new install.
Keep the selected Python invocation path intact: resolving a virtual
environment's executable symlink can accidentally select the base interpreter.
The discovery report lists its bounded ancestor/sibling search directories.
Failure to find a checkout in that scope does not mean it is absent from the
machine; use a supplied or previously selected path without another search.

After dependency-install approval, prefer a task-local virtual environment and
use its interpreter with `-m pip install --no-user -r <skill-dir>/requirements.txt`.
An inherited pip `user=true` setting conflicts with virtual environments;
override it for this invocation instead of editing global configuration or
using `--break-system-packages`. Keep using that interpreter for the workflow.

For PhaseTracer delivery, retain `phasetracer_run.json` and its per-attempt
configure/build/run logs. Read `execution_results` for Tc/Tn and their separate
statuses; do not rerun the model solely to recover successful output. An
unavailable Tn is not a numerical value or proof of no physical transition.
Use the normal compile/run entrypoints for numerical delivery. Set their
documented runtime/resource options when needed; do not write a separate
driver or patch generated code to fill a missing workflow step. A missing
standard operation is a reported limitation, not permission to bypass review.

## Routing

Use the SARAH route when the user supplies a Vevacious++ v2 `.vin` file,
`ScaleAndBlock.xml`, or explicitly asks to export an existing SARAH model. Read
`references/ptagent_sarah_workflow.md`; read
`references/sarah_thermal_metadata.md` when companion thermal data is present
or requested. A `.vin` file is auto-routed only after its XML root and v2 model
version are verified. A SARAH `.m` or `.wl` model is never auto-routed; use
`export-sarah` explicitly.

Treat `Study the EWPT of /path/to/SARAH/Models/THDM with CosmoTransitions`
as a request for the installed THDM model, not a request for the user to replace
the placeholder. Pass the model name or this placeholder to `export-sarah`
without `--sarah-root` when automatic discovery succeeded. Existing user model
directories take priority. A nonexistent real/custom path must be reported;
do not silently replace it with another model. Preserve the user's backend
selection through `--choices '{"backend":"cosmotransitions"}'`.

Use the same source-first deterministic preparation for every supported SARAH
model. `export-sarah --preset auto` selects it without a model-name branch;
existing exports use `prepare-sarah`. Pass explicit user inputs and available
thermal metadata. When the user has not specified a benchmark, first inspect
the selected model's `SPheno.m` and recommend its `DefaultInputValues`. The
preparation report includes a static, source-hashed recommendation when that
file is available. Do not ask the user to invent a parameter point before
checking it, and do not require running SPheno merely to read these inputs.
These model-local example values are proposals, not a universal benchmark or
prior user approval. Preserve explicit user parameters and SLHA values.
Use the bundled native tree-binding route described in
`references/sarah_tree_bindings.md` before asking for derived parameters.
SARAH exports tree tadpole solutions and boundary/matching relations; PTagent
evaluates them deterministically. Do not build, install or run SPheno to fill
these inputs unless the user separately requests a SPheno spectrum calculation.
Reading `SPheno.m` as static model data does not invoke SPheno.
For a calculation without a supplied loop-matched spectrum, recommend tree
input matching and `equal_tree` for missing Loop aliases in the first consolidated
review. Explain that CW/thermal loops remain, while numerical loop tadpole
matching is not performed and the zero-temperature vacuum can shift.
This recommendation needs acceptance, not another round of questions after
the already accepted scheme has been deterministically bound. Preserve supplied
Loop values and nonzero extra terms; never erase a correction by assumption.
Use `tree_review.missing_source_inputs` to ask only for independent inputs;
do not ask for each derived VEV, Yukawa element or quadratic mass. Inspect
source numerical definitions for remaining SM inputs and label their convention;
neither historical examples nor a successful tree solve fixes Q automatically.
Use the preparation manifest's `renormalization_scale_recommendation` for a
repeatable, source-first proposal. A proposed scale remains unbound until
accepted; preserve explicit overrides and previously accepted scan settings.
Keep the final exact-contract approval gate.
Distinguish default inputs, source-defined boundary/matching relations and
tadpole-derived quantities from additional SM inputs, the renormalization
scale and physical prescriptions. Use only supported deterministic source
relations for conversions; never assume all of these settings occur in
`DefaultInputValues`, or impose tree/loop equalities without review. Propose
the source example and list remaining unresolved settings in one review batch.
Parameters and scales must be traceable to source, accepted proposals, SLHA or
explicit JSON; unresolved values still block numerical execution.
For installed supported SARAH models, `export-sarah` generates the companion
thermal data automatically from the full canonical component interactions.
Do not ask the user to supply thermal tensors before trying this built-in route.
Read the recorded component/thermal blockers when an unsupported convention is
encountered; do not replace missing contributions with task-local derivations.
The shared default recommendations are defined in
`backend/ptagent/ptagent_4d/model_defaults.py` and also apply to paper workflows.
Do not infer a multiplet normalization or substitute a potential by model name.
Do not write task-local parameter fits, tadpole/Hessian derivations, thermal
completion scripts, or contract patches for this route. Unsupported inputs must
be reported, not replaced. Present the generated contract, graph and checks in
one review; after explicit approval use `approve-sarah` with the exact reviewed
hash. Generic defaults are not prior user confirmation. Existing approved tasks
and the paper/3DEFT workflows retain their behavior.

Do not create a model from a free-form Lagrangian. Ask the user for SARAH
Vevacious++ output or an existing SARAH model that can be exported. This leaves
paper/arXiv and manual-contract workflows unchanged while keeping the new
Lagrangian-source route deterministic.

Default to the 4D workflow when the user uploads or references a paper, arXiv
source archive, TeX, Markdown, PDF, or ordinary finite-temperature
phase-transition source without explicit DRalgo or 3DEFT intent. Before acting,
read `references/ptagent_4d_workflow.md`; read
`references/model_program_contract.md` when generating, repairing, or compiling
a 4D model contract.

Use the 3DEFT workflow only when the user explicitly says DRalgo, 3DEFT,
dimensionally reduced EFT, hard/soft/ultrasoft matching, or supplies a
Mathematica/Wolfram `.m` or `.wl` file that is clearly a DRalgo/3DEFT source.
Before acting, read `references/ptagent_3deft_workflow.md`.

For 3DEFT, run `check-reference --template <merged-contract>` after source
outputs and numerical inputs are filled, before presenting the final approval
batch. Use its numerical-domain diagnostics and unconfirmed suggestions in
that review; `resolve` alone is not a numerical check. Never change the physical
benchmark or silently replace a reviewed reference to obtain finite values.
The approval command rechecks the current reference and blocks invalid points.

For the documented xSM 3DEFT example, use the user's provided `.m` file first.
If they request `xsm_test.m` without supplying a local file, use the copy already
downloaded with this repository at `assets/examples/xsm_test.m` via
`extract --example xsm`. The same fallback is built into `--input xsm_test.m`.
Do not ask them to upload the example. This is only the named xSM example, not
a default replacement for an unspecified or missing custom 3DEFT model. Source
selection does not approve the physical contract or authorize compilation.

Do not ask the user out of convenience. Before asking, inspect the available
materials for the selected workflow: paper/source files, current contract,
proof files, previous extracted model information in the active run, explicit
user inputs, and DRalgo output when using 3DEFT.

Whenever a deterministic tree-level potential is available, inspect the shared
`symmetry_analysis` result before asking about phase handling. Present its
minimal sign-flip generator candidates as recommendations for PhaseTracer
symmetry or possible CosmoTransitions duplicate-branch filtering. Treat these
as tree-level evidence, then check loop, thermal, counterterm and gauge choices
for breaking terms. Recommend concrete PhaseTracer `z2_reflection` rows or a
CosmoTransitions symmetry-duplicate removal rules, not blanket `none` or
"leaving filtering disabled". Supply the rules and reasons in the single review
batch; do not ask users to reconstruct field groups or thresholds already
derivable from the available information. Independent single-field flips allow
representative sign cuts. Coupled flips require `symmetry_orbits` trajectory
comparison after tracing: a pointwise half-space cut can reject both members
when a physical branch crosses that boundary.
Use `symmetry_phase_history` for grouped presentation; retain the complete
`phases` dictionary and the native intervals in `symmetry_phase_groups`.
Matching uses one fixed transform over the common temperature interval, not
exact endpoint equality, and checks native spline residual extrema between knots.
Unequal-domain matches are recorded separately in
`symmetry_phase_associations`, not collapsed into one complete history. Tc/Tn
searches exclude a source phase's verified symmetry partners; orientations of
distinct target phases remain available. A visited partner is excluded only
where its verified domain covers the candidate's remaining cooling interval.
Inspect `symmetry_pair_diagnostics` and `symmetry_tunnelling_channels`. Uncertain
matches stay, and numerical solver failures must still be reported as failures.
Orbit tracing uses native CT routines with a displacement target no larger
than the reviewed orbit tolerance. Compilation also records a conservative
full-potential parity proof, including retained spectra and thermal terms.
Only proven transforms bypass the subtraction-sensitive numerical parity
residual; finite-value and trajectory checks always remain mandatory.
Use `phase_handling_recommendation` for these proposals. Numerical boundary
tolerances are conventions, not source evidence. Retain the origin and test
boundary sensitivity. Proposals still require final contract approval; preserve
explicit user policies. If the full potential breaks a candidate symmetry or
no symmetry is established, report that specific limitation instead of imposing
an unjustified cut.

Ask the user only when evidence is missing, conflicting, physically ambiguous,
or guessing would make the generated model/code/report misleading or
unverifiable. Do not ask about safe conventions, notation mapping, variable
renaming, formatting choices, or backend-compatible defaults; proceed and record
the assumption.

Every question must be a blocking ambiguity and must state what was checked,
why it blocks, what physics/code/result would change, the current recommendation,
and what PTagent will do if the user accepts it.

## Batched Questions And Visual Review

Read `references/contract_review.md` for every route before asking physics
questions or presenting a contract. Finish all available evidence checks first,
then present ALL unresolved decisions in one numbered message, with related
items grouped. Include backend selection and applicable conditional choices;
never deliberately disclose only the next question. Respect choices already
specified in the user's prompt. Apply an answer batch before revalidating.

Collect missing dependencies in one preflight request; never install them
without approval. A missing source, model-selection ambiguity, or blocked
operation can require a preliminary batch. Do not invent branch-specific
questions before the branch is known. Explain why any later question could not
be answered or discovered earlier. Final compile approval remains a separate
gate after construction blockers have been resolved.

Whenever showing the Markdown contract for review, show its matching provenance
graph INLINE in the same response, plus the contract link and complete pending
question checklist. Use `scripts/render_contract_graph.py` and the current
contract/evidence, not an image-generation model or a previous example's data.
The graph ends at `Contract for review`; regenerate it after changes. Green is
source evidence, blue is deterministic calculation, orange is explicit user
input/confirmation, pale red denotes recommendations or unverified-origin
choices (including grounded policy suggestions), and gray is unavailable/blocked.
Keep origin separate from review status: a saved JSON file or approval record
does not prove that a human authored a choice, and acceptance does not turn a
proposal into a deterministic derivation. Never label an agent's interpretation
or delegated approval as the user's personal confirmation. A graph is a review
artifact, not approval to compile or evidence of a completed numerical run.
If graph delivery fails, present the saved Markdown and question batch with the
exact error. Inspect SARAH's `proof_materials/review_delivery.json`: a
`graph_failed` status does not undo completed preparation or recorded approval.
Redraw from the current contract; do not repeat scientific stages for a graphic,
and never display an older graph as if it matched the current contract.

Use the source document's displayed equation numbers, including section and
appendix prefixes, in contracts and graphs. Internal formula ids and extraction
order are never equation numbers. Follow the equation-reference verification
in `references/contract_review.md`; if unresolved, cite a page or TeX label
instead of guessing. This applies to every paper, not just a named example.

## Backend Lookup

Wrapper scripts use this order:

1. command `--project-root`;
2. `PTAGENT_PROJECT_ROOT`;
3. this skill's bundled backend at `backend/ptagent`;
4. sibling router-skill bundled backend for legacy three-directory bundles;
5. a parent repository checkout containing `ptagent/__main__.py`;
6. an installed `ptagent` Python package.

The bundled route is the normal installed-skill path.
