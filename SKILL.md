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

Use the installed skill directory as `<skill-dir>`. If the user provides a
repository checkout, pass `--project-root <PTagent-repo-root>`. If the user
provides an existing PhaseTracer source path, pass
`--phasetracer-root <PhaseTracer-source-root>`.
For a SARAH model request, also pass `--sarah-model <model-name-or-directory>`;
the literal documentation path `/path/to/SARAH/Models/THDM` is accepted.

Read the JSON output before doing anything else. Treat the environment gate as
an advisory preflight plus per-operation blockers, not as one global stop/go
flag. The `ready` field only means baseline 4D extraction can start; before any
specific operation, inspect `operation_blockers[<operation>]` and continue only
when that list is empty.

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

On macOS, if `wolframscript` is found but its local evaluation probe times out,
test the configured `WolframKernel` directly. If the kernel returns normally,
do not ask the user to reactivate, reconfigure, or reinstall Mathematica. A
restricted agent sandbox may be blocking WSTP shared memory. Ask for permission
to run the existing local `wolframscript` outside that sandbox, rerun the
environment gate there, and continue only after the local probe succeeds.

Do not install Python packages, download PhaseTracer, install DRalgo, or modify
persistent config until the user explicitly approves. When PhaseTracer is
missing and the user approves a download, use the `phasetracer_download_command`
reported by the gate; it downloads the latest PhaseTracer `main` branch. If the
user already has PhaseTracer or provides a path, validate that path only; do not
check or enforce its version.

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
thermal metadata. Parameters and scales must come from the parameter map, SLHA
or explicit JSON; missing values remain blockers, not an example benchmark.
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
as tree-level candidates only: never enable a symmetry or invent a phase-filter
threshold until loop, thermal, counterterm, and gauge choices have been checked
and the user has confirmed the final policy.

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
input/confirmation, pale red is a pending recommendation (including grounded
policy suggestions), and gray is unavailable/blocked. Never label an agent's
interpretation or approval as the user's confirmation. A graph is a review
artifact, not approval to compile or evidence of a completed numerical run.

## Backend Lookup

Wrapper scripts use this order:

1. command `--project-root`;
2. `PTAGENT_PROJECT_ROOT`;
3. this skill's bundled backend at `backend/ptagent`;
4. sibling router-skill bundled backend for legacy three-directory bundles;
5. a parent repository checkout containing `ptagent/__main__.py`;
6. an installed `ptagent` Python package.

The bundled route is the normal installed-skill path.
