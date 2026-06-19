# PTagent

PTagent is a self-contained Codex skill for finite-temperature phase-transition
papers and reviewed DRalgo/3DEFT sources. Install this repository as one Codex
skill, then use `$ptagent` in the Codex chat.

The skill includes its own `backend/ptagent` source tree. Users do not need to
clone a second repository, run `pip install ptagent`, or provide an API key.

## Install

In Codex, ask:

```text
Install the PTagent skill from https://github.com/PhenoPack/PTagent
```

Or run the installer script directly:

```bash
python ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo PhenoPack/PTagent \
  --path . \
  --name ptagent
```

After installation, the skill is available as `$ptagent`.

## What The Skill Does

- Reads arXiv IDs, local PDFs, TeX, Markdown, and arXiv source archives for 4D
  finite-temperature phase-transition models.
- Reads reviewed DRalgo/3DEFT Mathematica/Wolfram sources when the user clearly
  asks for 3DEFT or supplies a DRalgo-style `.m`/`.wl` file.
- Builds evidence packets, asks only blocking physics questions, and fills a
  deterministic model contract.
- Compiles reviewed contracts locally to CosmoTransitions or PhaseTracer
  artifacts.
- Uses local code and the Codex agent review loop by default; it does not ask
  the user for an LLM API key.

## First Run

On first use, PTagent checks the local environment before doing heavy work.

It reports missing Python packages or external tools and asks before installing
or downloading anything. In particular:

- CosmoTransitions must be available in the Python runtime used for 4D
  CosmoTransitions smoke checks.
- PhaseTracer is optional until PhaseTracer compilation is requested. If it is
  missing, PTagent can ask whether to download the latest PhaseTracer `main`
  branch or use a user-provided path.
- 3DEFT workflows require a local Wolfram/Mathematica runtime and DRalgo setup.

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
agents/openai.yaml               Codex skill metadata
references/                      workflow and contract reference documents
scripts/                         wrapper scripts used by the skill
backend/ptagent/                 bundled PTagent backend source
requirements.txt                 Python dependencies for normal use
```

Heavy regression tests and CI live in `PhenoPack/Ptagent-test`; ordinary users
do not need that repository.
