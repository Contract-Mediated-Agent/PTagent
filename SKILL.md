---
name: ptagent
description: Parse finite-temperature phase-transition papers and reviewed DRalgo/3DEFT sources with the self-contained PTagent backend, ask focused physics questions, and compile reviewed contracts locally to CosmoTransitions or PhaseTracer artifacts without external model services.
---

# PTagent

Use this single installed skill as the product entrypoint for PTagent. It
contains the router instructions, both internal workflows, wrapper scripts, and
a bundled backend source tree under `backend/ptagent`.

Users should be able to install this one skill directory from
`PhenoPack/PTagent` and then invoke it through their agent's skill mechanism
(for example `$ptagent` in Codex); they should not need to clone the repository
or run `pip install ptagent` / `pip install -e .`.

## What Is Bundled

```text
SKILL.md
agents/openai.yaml
references/model_program_contract.md
references/ptagent_4d_workflow.md
references/ptagent_3deft_workflow.md
scripts/_bootstrap.py
scripts/check_environment.py
scripts/check_model.py
scripts/compile_template.py
scripts/prepare_paper.py
scripts/ptagent_cli.py
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

Read the JSON output before doing anything else. If `ready` is false, ask the
user the listed environment question(s) and stop. CosmoTransitions must be
available in either the current Python or configured runtime Python before
continuing with 4D CosmoTransitions work. PhaseTracer must be found or
configured before PhaseTracer compilation or comparison work.

Do not continue to extraction, question mode, compilation, or code generation
while the gate is blocked. Do not install Python packages, download
PhaseTracer, or modify persistent config until the user explicitly approves.
When PhaseTracer is missing and the user approves a download, use the
`phasetracer_download_command` reported by the gate; it downloads the latest
PhaseTracer `main` branch. If the user already has PhaseTracer or provides a
path, validate that path only; do not check or enforce its version.

## Routing

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

Do not ask the user out of convenience. Before asking, inspect the available
materials for the selected workflow: paper/source files, current contract,
proof files, previous extracted model information in the active run, explicit
user inputs, and DRalgo output when using 3DEFT.

Ask the user only when evidence is missing, conflicting, physically ambiguous,
or guessing would make the generated model/code/report misleading or
unverifiable. Do not ask about safe conventions, notation mapping, variable
renaming, formatting choices, or backend-compatible defaults; proceed and record
the assumption.

Every question must be a blocking ambiguity and must state what was checked,
why it blocks, what physics/code/result would change, the current recommendation,
and what PTagent will do if the user accepts it.

## Backend Lookup

Wrapper scripts use this order:

1. command `--project-root`;
2. `PTAGENT_PROJECT_ROOT`;
3. this skill's bundled backend at `backend/ptagent`;
4. sibling router-skill bundled backend for legacy three-directory bundles;
5. a parent repository checkout containing `ptagent/__main__.py`;
6. an installed `ptagent` Python package.

The bundled route is the normal installed-skill path.
