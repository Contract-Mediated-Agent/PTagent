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

For detected sign symmetries, PTagent proposes PhaseTracer generators or
CosmoTransitions duplicate removal. Coupled reflections use trajectory
comparisons instead of pointwise cuts that could erase a physical branch.
The grouped history is for presentation; numerical Tc/Tn searches retain all
relative-orientation channels.
The complete potential and these rules remain subject to review.

The 4D temperature search limit is a separate numerical choice. PTagent explains
its proposed `Tmax` and requests confirmation before scanning; constructing and
evaluating the potential does not require scan approval. The numerical field
step `x_eps` is configurable. 3DEFT compilation approval is bound to the reviewed
physical specification: changing it requires renewed approval. Generated,
numerically verified, and failed artifacts are reported separately.

On first use, PTagent checks the local environment before doing heavy work.
The check reports missing software as advisories and lists exactly which
operations are blocked in `operation_blockers`.

Python and CosmoTransitions are reported separately:

- Python 3.11 or newer must be usable for the requested workflow. The gate
  imports dependencies to detect missing packages and broken installations.
- CosmoTransitions is required for 4D CosmoTransitions compile/check/run
  operations.
- PhaseTracer is optional until PhaseTracer compilation, backend comparison, or
  generated PhaseTracer model checks are requested.
  On Windows, Linux-style roots such as `/home/user/src/PhaseTracer` are
  validated inside installed WSL distributions.
- `import-sarah` requires `numpy`, `sympy`, and `defusedxml`, but does not
  require Wolfram or SARAH. `prepare-sarah` also renders a review graph using
  `matplotlib`.
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

The gate reports current-interpreter and configured-runtime dependencies
separately in `python_modules`. PDF inputs need at least one working PDF reader;
Markdown/TeX and SARAH imports do not. Backend comparison evaluates generated
CosmoTransitions models in the configured runtime, even when the skill itself
runs under a different Python. No packages are installed by the gate.

`requirements.txt` remains the only Python dependency configuration. Its minimum
versions are installation constraints, not a lock or a claim that every newer
release has been validated. The environment report's `software_versions` records
installed distribution versions separately for the current and configured runtime
interpreters; `python_modules` independently reports whether imports work.
SARAH import reports also record the importing interpreter's versions in
`provenance.software_versions`; no numerical runtime is claimed for symbolic import.
Preparation manifests use the same schema: the former flat package entries now
live under `software_versions.python.current.distributions`, with the Python
version under `software_versions.python.current.version`.

External-tool provenance records the selected path and, when available, its
Git commit and tracked-change status or declared package version. A missing
version is explicitly `null` with a status such as `path_only`, `not_recorded`,
or `unavailable`; it is never inferred from a directory name or download branch.
Wolfram/DRalgo runtime versions can be recorded by callers from the running
kernel without launching another licensed session. Existing installations remain
usable without version enforcement. Downloading PhaseTracer `main` does not
establish reproduction of a particular paper's software version.

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

When no numerical benchmark is specified, PTagent first recommends the selected
model's `SPheno.m` `DefaultInputValues`, if available. Reading these example
inputs does not require running SPheno. `export-sarah` archives the model source;
for existing exports, pass `--model-source /path/to/model` to `prepare-sarah`.
The report records the file hash, safely resolved default inputs, MINPAR/EXTPAR
mapping, boundary/matching relations and parameters designated for tadpole solving.
This is a source-based proposal, not an automatic spectrum calculation. It does
not override an explicit benchmark or apply values before review.

The default inputs may still need source-defined parameter conversions, tree
tadpole solutions and additional SM inputs, a renormalization scale or physical
prescriptions. PTagent distinguishes these from values actually in `SPheno.m`;
it does not assume tree and loop parameters coincide. Review the proposed point
and any missing settings together. Accepted numerical bindings use `--slha` or
`--parameters` (a JSON object or file) with source-defined names. There is no
hard-coded THDM point or universal electroweak baseline.
All supplied numbers must be finite; `Q` and `renormScaleSq` must be positive.
Negative physical mass-squared parameters and couplings remain allowed.

When inputs are incomplete, the CLI and `sarah_import.json` include one
`benchmark_request` listing all unresolved quantities, including the scale when
missing, plus source/SLHA mappings and derived expressions. Preparation also
writes `proof_materials/spheno_benchmark.json` and presents the source example
in the question guide. The static reader does not execute Wolfram code or solve
tadpoles; unsupported/computed defaults are reported rather than guessed.
Confirm the proposed point and resolve remaining inputs in one response.
Symbolic import and partial contract generation continue,
but numerical compilation and phase-transition execution remain blocked until
the inputs and the reviewed contract are complete.

Generic proposals are Arnold-Espinosa, the intact source spectrum, a separately
reviewed imported polynomial correction, radiation counts from the retained
inventory, and backend-specific handling of detected sign-flip symmetries:
PhaseTracer symmetry generators or a representative field region for
CosmoTransitions. Check these against the full potential before approval.
These are proposals,
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

Shared CLI commands identify the workflow from the contract's schema/marker
before considering backend options. Recognized legacy contract filenames remain
supported. For example, this selects 3DEFT, not 4D:

```bash
python scripts/ptagent_cli.py compile \
  --template /path/to/three_deft_contract_merged.md --backend phasetracer
```

`--backend phasetracer` selects only the output backend. A renamed contract with
its 4D or 3DEFT marker retains its workflow; ambiguous contracts require
`--ptagent-engine 4d` or `--ptagent-engine 3deft`. An explicit engine overrides
automatic detection; `--ptagent-engine auto` uses detection.

The bundled xSM example removes only a duplicate fermion-representation
assignment and an unused comment, leaving tensor construction and evaluation
order unchanged. Its source hash therefore changes; regenerate example run
records for this revision rather than reusing older source-hash records.

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
