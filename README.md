# PTagent

PTagent is a self-contained agent skill package for finite-temperature
phase-transition papers, deterministic SARAH/Vevacious++ model exports, and
reviewed DRalgo/3DEFT sources. Install this
repository as one skill in a compatible agent environment, then invoke the
PTagent skill through that environment's skill mechanism.

The skill includes its own `backend/ptagent` source tree. Users do not need to
clone a second repository, run `pip install ptagent`, or provide an API key.

## Install

For example, in Codex you can ask:

```text
Install the PTagent skill from https://github.com/Contract-Mediated-Agent/PTagent
```

Or run the installer script directly:

```bash
python ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo Contract-Mediated-Agent/PTagent \
  --path . \
  --name ptagent
```

After installation in Codex, the skill is available as `$ptagent`. Other
skill-compatible agents may expose the same installed directory through their
own invocation syntax.

## What The Skill Does

- Reads arXiv IDs, local PDFs, TeX, Markdown, and arXiv source archives for 4D
  finite-temperature phase-transition models.
- Imports SARAH `MakeVevacious[Version -> "++"]` output (`MODEL.vin` and
  `ScaleAndBlock.xml`) without requiring SARAH on the import machine.
- Resolves real numerical parameter points from SLHA or explicit JSON, records
  source hashes, and keeps unresolved values as visible compile blockers.
- Accepts optional companion thermal metadata and deterministically constructs
  scalar thermal self-energies and longitudinal gauge Debye matrices.
- Reads reviewed DRalgo/3DEFT Mathematica/Wolfram sources when the user clearly
  asks for 3DEFT or supplies a DRalgo-style `.m`/`.wl` file.
- Builds evidence packets, asks only blocking physics questions, and fills a
  deterministic model contract.
- Runs one shared, non-executing tree-level parity check for paper, SARAH, and
  3DEFT inputs, and presents possible sign-flip symmetries for human review.
- Compiles reviewed contracts locally to CosmoTransitions or PhaseTracer
  artifacts.
- Uses local code and the agent's review loop by default; it does not ask
  the user for an LLM API key.

## First Run

On first use, PTagent checks the local environment before doing heavy work.
The check reports missing software as advisories and lists exactly which
operations are blocked in `operation_blockers`.

Python and CosmoTransitions are reported separately:

- Python must be usable for the requested workflow.
- CosmoTransitions is required for 4D CosmoTransitions compile/check/run
  operations.
- PhaseTracer is optional until PhaseTracer compilation, backend comparison, or
  generated PhaseTracer model checks are requested.
  On Windows, Linux-style roots such as `/home/user/src/PhaseTracer` are
  validated inside installed WSL distributions.
- `import-sarah` requires `defusedxml`, but does not require Wolfram or SARAH.
- `export-sarah` requires an existing SARAH installation and `wolframscript`.
- 3DEFT `.m`/`.wl` extraction requires a local Wolfram/wolframscript runtime;
  DRalgo is additionally required before running the marked DRalgo workflow.

Python dependencies are listed in `requirements.txt`. If your Python allows
normal package installation, install them with:

```bash
python3 -m pip install -r requirements.txt
```

If Homebrew Python reports `externally-managed-environment`, use a conda
environment or another user-managed Python instead of forcing system-wide
installation.

## Examples

Analyze an arXiv paper:

```text
$ptagent analyze arXiv:2207.14519
```

Analyze a local PDF:

```text
$ptagent analyze /path/to/paper.pdf
```

Analyze a local TeX source:

```text
$ptagent extract /path/to/main.tex
```

Import existing SARAH Vevacious++ output:

```bash
python scripts/ptagent_cli.py import-sarah \
  --vin /path/to/MODEL.vin \
  --parameter-map /path/to/ScaleAndBlock.xml \
  --slha /path/to/spectrum.slha
```

With companion metadata, add
`--thermal-metadata /path/to/ptagent_thermal_metadata.json`. Without it,
PTagent still imports the tree potential and zero-temperature spectra, sets
`thermal_ready=false`, and blocks Parwani or Arnold-Espinosa compilation.

Generate the inputs from an existing SARAH model when SARAH is available:

```bash
python scripts/ptagent_cli.py export-sarah \
  --sarah-root /path/to/SARAH \
  --model MODEL_NAME \
  --run-dir /path/to/run
```

`export-sarah` runs the companion exporter and immediately feeds the generated
files through the same deterministic importer, so the task receives its proof
materials and `contract_template.md` in one command. If generic thermal tensors
cannot be established from the loaded model, the export remains useful but is
marked partial and resummed compilation stays blocked.

PTagent does not infer a complete model from free-form Lagrangian prose and does
not ask an LLM to derive thermal masses or backend code. Existing paper, arXiv,
manual-contract, and 3DEFT workflows remain available.

Process a reviewed DRalgo/3DEFT source:

```text
$ptagent process /path/to/model.m with the 3DEFT workflow
```

Compile an already reviewed contract:

```text
$ptagent compile /path/to/contract_template.md with PhaseTracer
```

## Repository Layout

```text
SKILL.md                         skill entrypoint and router instructions
agents/openai.yaml               OpenAI/Codex-compatible skill metadata
references/                      workflow and contract reference documents
scripts/                         wrapper scripts used by the skill
backend/ptagent/                 bundled PTagent backend source
requirements.txt                 Python dependencies for normal use
```

Heavy regression tests and CI live in Contract-Mediated-Agent/PTagent-test; ordinary users
do not need the test repository.
