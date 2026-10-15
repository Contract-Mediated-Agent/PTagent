# PTagent

**PTagent** is an LLM-agent skill for constructing finite-temperature numerical models for cosmological phase-transition studies. It brings together model information from **SARAH-generated model files**, **research papers**, and **DRalgo sources**. The agent interprets and organizes this information and identifies unresolved physical choices for review. Dedicated backends then construct numerical effective potentials from the approved model specification.

## Installation

Install this repository as a skill in a compatible agent environment. For example, in Codex:

```text
Install the PTagent skill from https://github.com/Contract-Mediated-Agent/PTagent
```

The skill can then be invoked with `$ptagent` in Codex. PTagent bundles its computational backends, so no separate PTagent package or LLM API key is needed.

## Quick Start

**From a SARAH model**

```text
$ptagent Study the EWPT of /path/to/SARAH/Models/THDM with CosmoTransitions.
```

**From a research paper**

```text
$ptagent Study the xSM model in arXiv:2207.14519 with PhaseTracer.
```

PTagent assembles the relevant model expressions and thermal prescriptions from the source. Missing or ambiguous physical choices are presented for review. Local PDF, TeX, and Markdown files can also be used.

**From a DRalgo source**

```text
$ptagent Use xsm_test.m to generate a PhaseTracer model with the 3DEFT workflow.
```

The [bundled xSM example](assets/examples/xsm_test.m) illustrates the translation of a matched three-dimensional effective potential into a PhaseTracer-style model.


## Requirements

PTagent requires **Python 3.11+**. Python dependencies are listed in [`requirements.txt`](requirements.txt) and can be installed with:

```bash
python3 -m pip install -r requirements.txt
```

Additional software depends on the workflow. SARAH and Wolfram/Mathematica are needed to export a SARAH model, while existing Vevacious++ exports can be imported without SARAH. The DRalgo route requires Wolfram/Mathematica and DRalgo; PhaseTracer is needed to build and run PhaseTracer-style outputs. PTagent checks the environment and reports missing dependencies.

## Documentation

Detailed workflows, supported approximations, and command-line options are documented in the [SARAH workflow](references/ptagent_sarah_workflow.md), [4D model workflow](references/ptagent_4d_workflow.md), and [DRalgo/3DEFT workflow](references/ptagent_3deft_workflow.md). See [contract review](references/contract_review.md) for the approval process and [`SKILL.md`](SKILL.md) for the agent instructions.
