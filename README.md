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
- Automatically extracts canonical component interactions from supported SARAH
  models and derives that metadata, without model-specific potential or Yukawa
  templates. Real SM, singlet-extension, and type-I/type-II two-doublet exports
  are covered by source-matrix and analytic-coefficient regression checks.
- Prepares every supported SARAH export using the same source-first rules,
  records parameter bindings and physical proposals, and produces a contract,
  review graph and consolidated blockers without a model-name preset.
- Reads reviewed DRalgo/3DEFT Mathematica/Wolfram sources when the user clearly
  asks for 3DEFT or supplies a DRalgo-style `.m`/`.wl` file.
- Builds evidence packets, asks all currently unresolved physics questions
  together, and fills a deterministic model contract.
- Shows the Markdown contract with a consistent provenance graph for review:
  source evidence, deterministic derivations, explicit user inputs, pending
  recommendations, and unavailable quantities are distinguished by color.
- Runs one shared, non-executing tree-level parity check for paper, SARAH, and
  3DEFT inputs, and presents possible sign-flip symmetries for human review.
- Compiles reviewed contracts locally to CosmoTransitions or PhaseTracer
  artifacts.
- Uses local code and the agent's review loop by default; it does not ask
  the user for an LLM API key.

## First Run

PTagent first checks available evidence and collects unresolved decisions into
one question batch. Already specified choices are not asked again. A missing
source or a blocked tool may require a preliminary batch; new questions after
that must identify the newly exposed ambiguity. Final compilation still needs
explicit approval of the completed contract. Each review includes a graph
ending at that contract, not a claim that phase-transition calculations ran.

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
  PTagent locates SARAH in Wolfram/Mathematica package directories automatically;
  if absent, it reports the missing dependency and asks you to install it.
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

```text
$ptagent Study the EWPT of /path/to/SARAH/Models/THDM with CosmoTransitions
```

You may use this prompt literally: PTagent finds `THDM` in the installed SARAH
package without asking you to replace the placeholder or confirm a discovered
path. You can also supply a real model directory; your file is never replaced
silently. Automatic lookup supports standard macOS, Linux and Windows package
directories and a bounded Wolfram package-path query. An explicit `--sarah-root`
or `PTAGENT_SARAH_ROOT`/`SARAH_ROOT` can select a nonstandard installation.

```bash
python scripts/ptagent_cli.py export-sarah \
  --model THDM --choices '{"backend":"cosmotransitions"}' \
  --run-dir /path/to/run
```

`export-sarah` runs the companion exporter and immediately feeds the generated
files through the deterministic importer. The default `--preset auto` applies
the same source-first preparation to every model. It writes
`input/ptagent_sarah_components.json`, differentiates the complete real-component
scalar potential, contracts the Weyl mass trace, and uses source gauge
representations for longitudinal screening. The resulting
`ptagent_thermal_metadata.json` is generated automatically; users do not need
to hand-write its tensors for supported models. If generic thermal tensors
cannot be established from the loaded model, the export remains useful but is
marked partial and resummed compilation stays blocked.

The current automatic extractor covers canonical, real-parameter,
non-supersymmetric renormalizable models with supported `SU(N)`/single-`U(1)`
bases. CP-violating scalar bases, multiple Abelian factors, unresolved component
indices, and thermal mixing across separate source sectors are explicit
blockers. See [the thermal data contract](references/sarah_thermal_metadata.md)
for the approximation, checks, and limits. Symbolic readiness does not choose
a numerical benchmark or authorize a phase-transition run.

For existing exports (no local SARAH installation required):

```bash
python scripts/ptagent_cli.py prepare-sarah \
  --vin /path/to/MODEL.vin --parameter-map /path/to/ScaleAndBlock.xml \
  --slha /path/to/spectrum.slha \
  --thermal-metadata /path/to/ptagent_thermal_metadata.json \
  --run-dir /path/to/fresh-run
```

Numerical parameters and the renormalization scale come only from the source,
SLHA or explicit JSON. There is no universal benchmark or electroweak baseline.
Generic proposals are Arnold-Espinosa, the intact source spectrum, a separately
reviewed imported polynomial correction, radiation counts from the retained
inventory, and no phase filtering or symmetry merging. These are proposals,
not SARAH-mandated physics. Missing thermal data blocks resummation; missing
parameters block numerical execution. Source/user choices take priority.
Explicit `--parameters` and `--choices` overrides are recorded; unknown or
unsupported choices fail instead of being replaced. Default rules,
supported overrides and approval instructions are in
[the SARAH workflow](references/ptagent_sarah_workflow.md).

Review the generated contract and graph once before approving compilation.
The preparation report records source/configuration hashes and software versions;
changed inputs invalidate approval. Existing contracts are never overwritten,
and different numerical points get separate prepared tasks. Symbolic-only
imports remain available with `import-sarah` or `export-sarah --preset none`.

PTagent does not infer a complete model from free-form Lagrangian prose and does
not ask an LLM to derive thermal masses or backend code. Existing paper, arXiv,
manual-contract, and 3DEFT workflows remain available.

Process a reviewed DRalgo/3DEFT source:

```text
$ptagent process /path/to/model.m with the 3DEFT workflow
```

Try the bundled xSM example without uploading or downloading a separate file:

```text
$ptagent process xsm_test.m with the 3DEFT workflow and generate a PhaseTracer model
```

The [example source](assets/examples/xsm_test.m) is included in the skill. A
user-provided file takes priority; otherwise the repository copy is used and
recorded in the task. The equivalent CLI is:

```bash
python scripts/ptagent_cli.py --ptagent-engine 3deft extract --example xsm
```

These shortcuts resolve input locations, not physics approvals. Required tools
must still be installed, and the contract is presented for review before
compilation. They do not turn a generic 3DEFT request into an xSM calculation.

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
assets/examples/xsm_test.m        bundled DRalgo xSM input from the paper supplement
backend/ptagent/                 bundled PTagent backend source
requirements.txt                 Python dependencies for normal use
```

Heavy regression tests and CI live in Contract-Mediated-Agent/PTagent-test; ordinary users
do not need the test repository.
