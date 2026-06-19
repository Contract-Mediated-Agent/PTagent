---
name: ptagent-internal-4d-workflow
description: Parse 4D finite-temperature phase-transition papers or local TeX/Markdown/PDF/arXiv-source files with PTagent, ask focused physics questions, fill a deterministic model contract template, and compile it locally to CosmoTransitions or PhaseTracer without external model services.
---

# PTagent 4D

Use this internal workflow for the 4D finite-temperature workflow: papers, arXiv source
archives, TeX, Markdown, and PDFs that should become a reviewed
`contract_template.md` and then compile to CosmoTransitions or PhaseTracer.

If the user explicitly asks for DRalgo, 3DEFT, dimensionally reduced EFT, or
uploads a DRalgo-style Mathematica/Wolfram file, switch to the internal 3DEFT
workflow in `references/ptagent_3deft_workflow.md` instead.

## Core Rule

Do not call external model services or configure API keys. The user-facing source
of truth is `contract_template.md`. `contract_resolved.json`, `model_ir.json`,
`validation.json`, and generated backend code are derived artifacts and are
regenerated from the Markdown under `proof_materials/` and `generated_models/`.
Every new run is self-contained: input/source material lives under that task's
`input/` folder, evidence under `proof_materials/`, and compiled models under
`generated_models/`. Do not create or rely on a top-level `artifacts/input`
cache for new runs.

Backend lookup order for wrapper scripts: command `--project-root`, then
`PTAGENT_PROJECT_ROOT`, then the self-contained skill backend under
`backend/ptagent`, then a legacy sibling router-skill backend, then a parent
directory containing `ptagent/__main__.py` and `ptagent/interface/facade.py`,
then an installed Python package. Do not assume a machine-specific project path.
Default artifact root for fresh runs is resolved in this order:
command-specific `--run-dir`, `PTAGENT_ARTIFACT_ROOT`, `~/.ptagent/config.toml`
field `artifact_root`, then `~/PTagentRuns`. New users can run
`python <skill-dir>/scripts/ptagent_cli.py init --artifact-root <runs-dir> --runtime-python <python-with-cosmoTransitions>`
once to write the basic persistent config for CosmoTransitions. If
`--runtime-python` is omitted, PTagent records the Python running the init
command. PhaseTracer is extended configuration: add it later with
`python <skill-dir>/scripts/ptagent_cli.py init --phasetracer-root <PhaseTracer-source-root>`
or pass `--phasetracer-root` to a PhaseTracer command. CosmoTransitions compile/check
commands require only the configured runtime Python. PhaseTracer compile and
`compare-backends` require both the runtime Python and PhaseTracer root. Do not
rely on an implicit local Python environment or an implicit `~/src/PhaseTracer`
fallback.

## First-Use Environment Gate

At the first `$ptagent` invocation for a task or conversation, run the local
environment gate before extraction, question mode, or compilation:

```bash
python <skill-dir>/scripts/check_environment.py
```

If developing against a repository checkout, include
`--project-root <PTagent-repo-root>`. If the user provides a PhaseTracer path up
front, include `--phasetracer-root <PhaseTracer-source-root>`. Treat the script
output as an operation gate, not as a physics-contract field.

When the gate reports that no usable Python has `cosmoTransitions`, stop and ask
the user whether to install CosmoTransitions into the Python environment that
will run PTagent. Do not install packages automatically, and do not keep running
paper extraction while this environment question is unanswered.

When the gate reports that PhaseTracer is not found under the selected backend
or project directory and no valid PhaseTracer root is configured, stop and ask
whether to download PhaseTracer into the reported `phasetracer_download_dir` or
whether the user wants to provide an existing PhaseTracer source path. Use the
pinned release tag `2.2.2` only when PhaseTracer must be downloaded because no
usable local/source path exists:

```bash
git clone --branch 2.2.2 --depth 1 https://github.com/PhaseTracer/PhaseTracer.git <environment-gate-phasetracer_download_dir>
```

Do not download or clone anything until the user explicitly approves. If the
user has already installed PhaseTracer, supplies a path, or the gate finds a
usable PhaseTracer source tree under the selected backend/project directory,
validate only that the path is usable; do not inspect or enforce the
git tag/version. If the user
supplies a path, validate it with the same gate and then persist it with
`python <skill-dir>/scripts/ptagent_cli.py init --phasetracer-root <PhaseTracer-source-root>`
or pass it to PhaseTracer compile commands as `--phasetracer-root`.

## Workflow

1. After the first-use environment gate passes, decide the run mode before
   touching artifacts:
   - `fresh` is the default for a newly supplied paper/source. Use only the
     current input file, current user answers, and explicitly supplied
     reference code. Do not import formulas from old runs.
   - `continue` is only for an explicitly named existing `contract_template.md`
     or task directory. In this mode, that Markdown file is the source of truth;
     patch it, then resolve/validate/compile from it without re-extracting the
     paper unless the user asks for a fresh run.
2. Save or identify the attached paper/source file path.
3. For `fresh`, run `scripts/prepare_paper.py --mode fresh --input <path>` from this skill. If the user has
   already specified the target model branch, pass it as `--model <short-name>`,
   for example `--model XSM` or `--model 2HDM`. The script first performs a
   lightweight pre-contract model scan. If it detects multiple candidate model
   potentials and no model was specified, or the specified model does not
   overlap with the scan candidates, it writes
   `proof_materials/model_candidates.md`, `proof_materials/model_candidates.json`,
   and `proof_materials/user_questions.md`, then stops before generating
   `contract_template.md`. If the user/source does not provide a common short
   name, use `model` for naming instead of asking solely about the artifact
   name. After the user chooses the target model, rerun with
   `--model <choice>`. Otherwise it calls local PTagent extraction and writes a
   fixed human-review `contract_template.md`, copies the input/source material
   under the task-local `input/` folder, and writes evidence/question files
   under `proof_materials/`.
   For local arXiv/source archives, the reader must prefer TeX first, then
   Markdown, then PDF.
4. For `continue`, do not run extraction by default. Open the existing
   `contract_template.md`, make the user's requested corrections there, and run
   `python <skill-dir>/scripts/ptagent_cli.py resolve --template <contract_template.md>`.
5. Before question mode, inspect the paper/source material, current
   `contract_template.md`, generated proof files, previous extracted model
   information in this active run, explicit user inputs, and backend context.
   Do not ask the user out of convenience. Ask only if evidence is missing,
   conflicting, physically ambiguous, or guessing would make the generated
   model/code/report misleading or unverifiable.
   - Public inputs: fill the final public input list and one numeric
     `test_value` per input when source/user/proof evidence supports them. Ask
     only when the final user-facing input basis or test values remain
     unresolved after that review.
   - Model branch / short name: before full contract generation, if the source
     contains multiple candidate model potentials and the user has not already
     specified the target model, ask which branch is being implemented before
     asking branch-specific parameters. If the user specified a model that does
     not overlap with the preflight scan candidates, stop and ask the user to
     resolve that mismatch before generating the contract. If the user-specified
     model matches the scan, or the preflight scan found a single branch, carry
     that choice into
     `model_card.model_short_name` as the reviewed HEP acronym/short name, such
     as `XSM`, `2HDM`, or `IDM`. If no common short name exists, use `model`
     without asking solely about artifact naming.
   - Backend selection: treat the compile backend as an operation gate, not as
     contract physics. If the user has already specified `cosmotransitions` or
     `phasetracer`, carry that choice as compile context; otherwise ask which
     supported backend to generate. Do not compile for any other backend.
   - Backend-specific phase handling: always ask the user to confirm this
     policy for the selected backend. First inspect source, contract, proof
     files, and explicit user inputs, then present a recommendation and reason.
     For `cosmotransitions`, ask whether `forbidPhaseCrit` should discard any
     traced phase. Recommend `mode=none` unless the paper/reference code
     explicitly removes a duplicate or unphysical branch; if filtering is
     intended, ask for the field, comparison, threshold, phase type, and reason.
     For `phasetracer`, do not ask the CosmoTransitions-style phase-filter
     question; ask whether PhaseTracer should use `apply_symmetry(phi)` to
     identify reviewed symmetry-equivalent field points and avoid duplicate
     phase counting. Recommend `mode=none` unless the user confirms a reviewed
     Z2/sign-flip equivalence; if enabled, ask for the sign-flip field groups
     used by `apply_symmetry` / `get_symmetry_axes`. One contract row creates
     one generated symmetry partner: `h,s` means `h -> -h and s -> -s`, while
     two separate rows `h` and `s` mean `h -> -h or s -> -s`. Do not encode
     independent alternatives as `h,s`. Show reference-code/paper hints, such
     as xSM-style `field < -5.0`, only as suggestions.
   - Compile approval: after the Markdown template has no blockers, stop and
     ask the user to review the rendered `contract_template.md` and explicitly
     approve backend code generation/compilation. Do not treat a request like
     "write a model" as approval to compile.
   Ask only one question packet at a time. Each question must correspond to a
   blocking ambiguity and must state the checked materials, missing/conflicting
   information, why it blocks, what physics/code/result would change, the
   current recommendation, and what PTagent will do if the user accepts it.
   If the source contains multiple candidate model potentials, ask which model
   branch/potential is being implemented before asking parameters specific to
   any one model.
   For concrete model-construction questions, include Codex/PTagent's current
   leaning in parentheses or a `My current leaning` note when the source evidence
   supports one, and explain the reason briefly. This note is reviewable guidance,
   not a replacement for the user's answer. If the uncertainty does not affect
   physics correctness, executable behavior, or numerical interpretation,
   proceed with a documented assumption instead of asking.
6. If blockers remain after evidence review, continue question mode. The user
   may answer any subset in chat. First resolve every item that can be supported
   by source evidence, user-provided values, current `contract_template.md`, or
   deterministic proof files. Do not ask the user for mechanical items the agent
   can prove. For physics-sensitive items with missing or conflicting evidence,
   leave `ASK_USER` and ask.
7. Patch only `contract_template.md` with the user's answers. The user may also
   edit that Markdown file directly.
8. Run `python <skill-dir>/scripts/ptagent_cli.py resolve --template <contract_template.md>` after each
   answer batch. This regenerates `proof_materials/contract_resolved.json`,
   `proof_materials/model_ir.json`, `proof_materials/validation.json`, and
   `proof_materials/user_questions.md`.
9. Replace every unresolved `ASK_USER` in the human Markdown source. When every
   field is resolved, stop and ask the user to review the rendered Markdown.
   Do not set `approved=true` and do not compile until the user explicitly says
   to approve/generate/compile the model.
10. Only after explicit user approval, set `approved` to `true` and verify the
   backend-selection gate. If the user has not already specified one, ask them
   to choose `cosmotransitions` or `phasetracer`; any other backend name is not
   supported yet. Run
   `scripts/compile_template.py --template <contract_template.md> --backend cosmotransitions`
   or
   `scripts/compile_template.py --template <contract_template.md> --backend phasetracer --transition-smoke`.
   Do not pass PhaseTracer-only flags such as `--transition-smoke`,
   `--phasetracer-root`, `--linux-runner`, `--wsl-distro`, or
   `--no-clang-format` with `--backend cosmotransitions`.
   For PhaseTracer, `--transition-smoke` is a hard model-delivery requirement
   whenever smoke checks are run. Skip it only when the user explicitly requests
   compile-only/no-transition, or when the local PhaseTracer environment cannot
   run transitions; in that case, do not call the model delivery complete and
   report the exact reason transition testing was skipped.
   To compare independently generated CosmoTransitions Python and PhaseTracer
   C++ artifacts from the same reviewed contract, run
   `python <skill-dir>/scripts/ptagent_cli.py compare-backends --template <contract_template.md>` or
   `scripts/compile_template.py --template <contract_template.md> --compare-backends`.
   Do not combine `--compare-backends` with a single `--backend` value.
   Compare mode reuses default generated artifacts when their stored contract
   hash matches the current resolved contract; pass `--force-regenerate` only
   when the user explicitly wants both backend artifacts rebuilt before
   comparison.
11. Run the generated-code cleanup checklist below as a final pass. This pass may
   remove unused code or stale comments, but must not change reviewed physics
   formulas, public inputs, field ordering, or implementation conventions.
12. Hard output gate: do not mark the task complete until you have shown a
   Model Delivery Receipt for the generated backend artifact, or the exact
   remaining template/build blockers. For a successful CosmoTransitions
   delivery, the receipt must include the compiled Python model path, import
   smoke result, whether `calcTcTrans()` was run, `PTAGENT_TCTRANS`/Tc rows when
   run or an explicit skip reason, exact `python <model.py>` and `build_model`
   call instructions, warnings, and physics caveats. For a successful
   PhaseTracer delivery, the receipt must include the compiled model path,
   potential-smoke result, transition-smoke result or explicit skip reason,
   transition/Tc rows when run, exact call instructions, warnings, and physics
   caveats.

After a user edits the template manually, regenerate the guide with:

```powershell
python <skill-dir>/scripts/ptagent_cli.py resolve --template path\to\contract_template.md
```

## What To Ask

Before compiling, ask only for blocking ambiguities that remain unresolved after
reviewing the source material, current contract, generated proof files, previous
extracted model information in this active run, explicit user inputs, and
backend context:

- ask the public input basis question only when the final user-facing input list
  is not explicit in the source, user request, or reviewed contract. Otherwise
  fill it and cite the evidence in the contract/proof files;
- before generating a full contract, ask for the model branch when the
  pre-contract scan finds multiple candidate model potentials and the user has
  not specified one, or when the user-specified model does not overlap with the
  scan candidates. Use a common HEP acronym such as `XSM` or `2HDM` when the
  user/source provides one; otherwise use `model` without asking solely about
  the artifact name;
- ask for a numeric `test_value` only when no source/user/proof-file value is
  available;
- ask which backend to compile only if the user/source/task has not specified
  one, and
  show the supported choices `cosmotransitions` and `phasetracer`;
- after the backend is selected, always ask the user to confirm backend-specific
  phase handling after presenting a source-backed recommendation and reason.
  `cosmotransitions` uses phase filtering / `forbidPhaseCrit`; recommend
  `mode=none` unless the paper/reference code intentionally removes a traced
  branch. `phasetracer` uses `apply_symmetry(phi)` to identify reviewed
  symmetry-equivalent field points and avoid duplicate phase counting; recommend
  `mode=none` unless the user confirms a reviewed sign-flip equivalence;
- ask only the current next question packet, not a full batch of unrelated
  questions; include the current question number (`1 of N`), how many packets
  remain after this answer, the underlying blocker count, and a plain-language
  explanation;
- do not ask about safe conventions, notation mapping, variable renaming,
  formatting choices, or backend-compatible defaults. Proceed and record the
  assumption;
- when asking, state what materials were checked, what information is missing
  or conflicting, why the ambiguity blocks, what physics/code/result would
  change, the current recommendation, and what PTagent will do if accepted;
- always ask for explicit approval before backend code generation/compilation, but
  only after all other physics/formula/template blockers are resolved; do not
  include the approval question in the same round as unresolved construction
  questions;
- a symbol may be either public input, fixed constant, or derived parameter and
  evidence does not decide the role;
- a LaTeX formula cannot be translated into a compiler expression from nearby
  source context and deterministic parser output;
- the paper gives vacuum physical masses but code needs field-dependent masses
  and no source/potential/Hessian evidence resolves the field-dependent route;
- a scalar/vector field-dependent mass matrix is present or plausible but the
  source does not give enough entries, basis, d.o.f., or final eigenvalues;
- Daisy resummation, Goldstone, counterterm, or finite-temperature loop mode is
  unclear after reading source evidence;
- standard thermal integrals are used and `num_boson_dof` / `num_fermion_dof` are not
  reviewed;
- a model branch such as alignment limit, type I/type II, or fixed singlet-VEV
  branch affects formulas.
- backend-specific phase handling has not been explicitly user-confirmed for
  the selected backend. For `cosmotransitions`, ask whether any forbidden phase
  branch should be filtered and recommend `mode=none` unless source/reference
  evidence supports filtering. For `phasetracer`, ask for reviewed
  `apply_symmetry` sign-flip equivalence rules instead of a phase-filter
  threshold and recommend `mode=none` unless the user wants symmetry-equivalent
  points merged.

Do not ask about values that are standard fixed constants unless the paper uses
nonstandard conventions. Treat `v`, SM gauge couplings, and Yukawa couplings as
fixed/default internals unless the paper lists them as scan/public inputs.

## Evidence Triage

Treat extracted material as candidates, not truth. Accept a formula into the
contract only when its role is clear and it passes physics checks:

- Highest priority: user-provided reference code for the same paper/model,
  source-exact TeX equations, and explicit paper prose near those equations.
- Medium priority: Codex-cleaned formulas with nearby source context and
  consistent symbols.
- Low priority: PDF/OCR fragments, isolated formula candidates, section headers,
  relic-density formulas, cross sections, or labels that merely contain symbols
  like `M`, `V`, or `lambda`.
- For masses, prefer reviewed field-dependent matrices over guessed direct
  eigenvalues. Do not accept vacuum-only mass relations as field-dependent
  spectra.
- If the paper gives a matrix, or the tree potential implies off-diagonal
  Hessian terms, keep the matrix enabled by default and ask the user for the
  missing entries/d.o.f.; disable it only after direct eigenvalues are reviewed.
- If the paper gives final field-dependent or thermal eigenvalues, direct
  species rows are allowed, but mark the source evidence as final/source-given
  eigenvalues. If the paper gives a field basis, mixing matrix, or basis
  thermal self-energy/Debye correction, keep the basis structure in a reviewed
  boson mass matrix and let the backend diagonalize it.
- Check dimensions, vacuum anchors, d.o.f., and whether the formula is used in
  V_CW, V1T, Daisy, parameter solving, or only phenomenology.
- If a source TeX formula appears dimensionally inconsistent with its declared
  role, for example a mass-squared expression linear in a dimension-one field
  where the tree potential/Hessian requires a quadratic term, do not silently
  copy it and do not silently repair it. Mark the row as needing review, state
  the suspected TeX typo, show the Hessian or dimensional-consistency repair
  as PTagent's recommendation, and ask the user to confirm before compiling.
- Do a semantic role review before copying any formula into the contract. Read
  the surrounding prose, equation label, and nearby "where"/"adding"/"defined
  as" sentences, then classify the expression as a final compiled object, a
  definition, a partial correction, a closed-form eigenvalue, or an explanatory
  relation. Do not take an isolated formula and run with it just because it is
  syntactically compilable. If the role is not clear, leave `ASK_USER`.
- If evidence conflicts, keep `ASK_USER` and ask. Do not silently choose.
- Do not reuse a previous artifact as the template source for a new paper/model
  unless the user explicitly says to continue from that artifact. The canonical
  template is generated by `ptagent.ptagent_4d.template_contract.build_contract_template`
  and documented in `references/model_program_contract.md`; old artifacts are
  evidence only.
- In `fresh` mode, prior chat conclusions and old generated files are not
  sources of truth. Mention them only as possible conflict checks if the user
  explicitly asks.
- In `continue` mode, the named `contract_template.md` is the source of truth.
  Preserve reviewed rows that are unrelated to the user's requested correction,
  then regenerate derived JSON and Python from the edited template.

## Hard Blocker Policy

Compilation must block and enter question mode whenever any of these are true:

- source evidence conflicts and the conflict has not been explicitly resolved by
  the user or source-exact context;
- a mass formula may be vacuum-only, phenomenological, or a parameter relation
  rather than a field-dependent spectrum for `boson_massSq`, `fermion_massSq`,
  `V1T`, or Daisy;
- a field-dependent mass formula appears dimensionally inconsistent or
  conflicts with the Hessian of the reviewed tree-level potential, unless the
  user has explicitly confirmed the repaired expression;
- Goldstone handling is unclear for CW terms, CT derivative sources, thermal
  terms, or regulated replacements;
- photon or neutral gauge-boson branch handling is unclear, especially when a
  massless photon-like eigenvalue and a massive Z-like eigenvalue both appear;
- Daisy route is unclear between none, Parwani, Arnold-Espinosa, and custom;
- counterterm status, CT basis, CT equations, or the CW source used in CT
  finite differences is unclear.
- backend-specific phase handling has not been explicitly user-confirmed.
  CosmoTransitions phase filtering affects which traced phases enter transition
  finding; PhaseTracer symmetry identifies which field points are
  symmetry-equivalent through `apply_symmetry(phi)` so they are not counted as
  distinct phases. The agent must give a recommendation and reason before
  asking.
- backend selection or compile approval is missing for this run, or a
  public-input / backend-specific phase-handling gate remains unresolved after
  checking source material, current contract, generated proof files, and
  explicit user inputs.

Do not resolve these by model-family convention or by a standard formula from
memory. Leave `ASK_USER`, regenerate `proof_materials/user_questions.md`, and ask the user.

## Template Rules

The human Markdown worksheet inside `contract_template.md` is the source of
truth. Derived JSON must not be hand-edited:

- `model_card.model_short_name`: reviewed model acronym/short name used for
  artifact paths under `generated_models/`, for example `XSM`, `2HDM`, or
  `IDM`. This is model metadata, not a backend field. Use a common acronym when
  the user/source provides one; otherwise use `model`.
- `fields`: ordered background fields, matching the last axis of `X`.
- `parameters.public_inputs`: only build/scan inputs. Every row must have
  `confirmed=true` and a numeric `test_value`; code generation must block until
  the user confirms both the input list and each test value. Fixed conventions
  and solved parameters must be moved out of this table.
- `parameters.constants`: fixed conventions such as `vh=246`.
- `parameters.derived`: formulas solved from inputs/constants, in dependency order.
- potential pieces: V0, VCW, VCT, thermal, Daisy, and full-potential reference.
- potential assembly: choose `none`, standard backend-native loop pieces, or a
  reviewed custom expression assembled from named pieces.
- model card implementation choices: counterterm mode, resummation scheme,
  gauge convention, Goldstone policy, and photon/Z-like neutral-mode policy.
- direct species masses: field-dependent mass squared expressions, d.o.f., and
  Coleman-Weinberg constants, plus usage/thermal/vacuum-anchor metadata.
- boson mass matrices: reviewed field-dependent matrices that the compiler
  diagonalizes with `np.linalg.eigvalsh`. A new template should default to an
  enabled matrix placeholder; it blocks compilation until the user confirms the
  matrix entries and the d.o.f. per eigenvalue.
- Section 6 implementation contract: CT basis/conditions, Goldstone handling,
  Daisy/thermal-mass route, backend-specific phase handling, and double-counting guard. The Markdown must be
  detailed enough for a beginner to audit: include visible CT algebra
  `V_CT=sum_i c_i O_i`, `A_ai=L_a[O_i]`, `b_a=t_a-L_a[V_CW_source]`, and
  `A c=b`; list Goldstone species/mode groups when Goldstones exist; list every
  Daisy-resummed particle/mode group when the Daisy scheme is not `none`.
- CosmoTransitions phase filtering: for `cosmotransitions`, choose `none`,
  `negative_field_threshold`, or `custom_expr`. This is not a potential term;
  it maps to `forbidPhaseCrit`. If enabled, the template must state the removed
  phase type, field component, comparison, and numeric tolerance threshold.
  Custom expressions may be written naturally with Python `and`/`or`; the
  compiler must render them as backend-safe boolean logic and smoke tests must
  exercise the CosmoTransitions phase-filter hook. PhaseTracer does not consume
  this section.
- PhaseTracer symmetry: for `phasetracer`, choose `none` or `z2_reflection` in
  `PhaseTracer Symmetry`. If enabled, list the field or simultaneous field
  groups whose sign is flipped. The generated C++ maps this to
  `apply_symmetry(phi)` and `get_symmetry_axes()` to identify equivalent field
  points. This does not create new physical vacua. One contract row creates one
  generated symmetry partner: `h,s` means `h -> -h and s -> -s`, while two
  separate rows `h` and `s` mean `h -> -h or s -> -s`. Do not encode independent
  alternatives as `h,s`.
- Daisy resummation has two standard routes:
  - Parwani: replace one-loop finite-temperature mass eigenvalues by reviewed
    lowest-order thermal mass eigenvalues and add no separate `V_daisy`.
    Because this makes the one-loop `V1` temperature-dependent, generated
    `V1T_from_X` must include the temperature-dependent `V1` contribution as
    well as `V1T`.
  - Arnold-Espinosa: keep ordinary field-dependent masses in `V1`/`V1T` and add
    one explicit `V_daisy = -T/(12*pi) sum_i n_i[(M_i^2(phi,T))^(3/2) -
    (m_i^2(phi))^(3/2)]` term for the reviewed zero-Matsubara modes. Generated
    `V1T_from_X` must include this explicit `V_daisy`, since CosmoTransitions
    uses `V1T_from_X` for temperature derivatives and energy densities.
    The contract must also state the reviewed convention for `(m^2)^(3/2)` when
    a mass squared can be negative, e.g. positive_part, signed_abs,
    regulated_abs, or a paper-specific formula.
  The word ring refers to daisy/ring diagrams, not to a separate third
  implementation route.
- Daisy `thermal_mass_sq` entries must be the full resummed mass squared
  entering the reviewed route, not just a thermal self-energy increment. When
  the source writes `Delta Pi`, "additional contribution", "extra scalar
  doublet contribution", or then says "adding them together", keep the row in
  `needs_review` until the total Debye/self-energy contribution and the final
  mass matrix/eigenvalues have been assembled from the source. In particular,
  gauge-boson Daisy rows must distinguish the zero-temperature mass matrix, SM
  or baseline thermal self-energies, BSM increments, and the final longitudinal
  eigenvalues used in `V_daisy`.
- When a source distinguishes longitudinal and transverse vector thermal
  masses, or gives neutral gauge-basis information involving photon/gamma or
  B/W/Z mixing, the contract must make that structure explicit. Prefer reviewed
  mode groups or reviewed gauge-basis matrices over a single shared vector
  thermal mass. Do not compress W3/B, gamma/Z, photon/Z, or any other neutral
  gauge-basis thermal correction into a named Z/photon row unless the source
  explicitly gives the final thermal eigenvalues; ask the user when the branch
  assignment or photon-like mode is unclear.
- Every thermal-mass summary block should include derivation notes explaining
  whether the source gives the final total thermal mass directly or whether the
  compiled expression was assembled from a zero-temperature mass/matrix plus
  baseline/SM self-energies plus BSM increments. These notes should also record
  model-branch details that affect the coefficient, such as type-I/type-II
  Yukawa assignments, tan(beta) factors, neglected off-diagonal self-energies,
  and which final eigenvalue convention is used.
- radiation d.o.f.: `num_boson_dof` and `num_fermion_dof` when standard thermal integrals are
  used. These are total radiation d.o.f.; backend `V1T` implementations subtract
  explicit species internally. Derive or use the total count from source/user
  evidence when available, and ask only when the total count is not reviewed.
  Start from SM baseline 28/90. For scalar extensions, add
  reviewed BSM real-scalar d.o.f. beyond the one SM Higgs scalar already in that
  baseline, and tell the user what was added. If Goldstones or photons are
  explicitly included/excluded, keep the total d.o.f. and explicit species list
  consistent.
  If there are no reviewed enabled fermion mass rows, keep `fermion_massSq`
  empty. Do not add unreviewed placeholder fermions to repair CosmoTransitions
  smoke failures; `num_fermion_dof` remains total radiation metadata.

Markdown tables should keep review metadata, d.o.f., policies, and status
fields readable. Keep long prose notes out of table cells: put compact note
references such as `N1` in the table and list `Notes:` immediately below the
table. Short one-line compiler expressions may live directly in table columns
such as `expr`, `mass_sq`, `operator_expr`, `target_expr`, `replacement_expr`,
`custom_expr`, `zeroT_mass_sq`, `thermal_mass_sq`, and `daisy_term`; render them
as inline code spans. Long or structured compiler expressions, especially
Goldstone masses, scalar/gauge mass matrices, thermal masses, and assembled
Daisy/ring terms, should live in fenced Python code blocks under stable named
headings. Do not generate parallel display-formula columns for compiled
expressions; LaTeX belongs in evidence/review text or visible formula blocks,
while compiled Python belongs either in a short table code span or a readable
Python block.

Use semantic non-formula markers narrowly. `not_applicable` means the field
truly does not apply to that row; it must not mean "included elsewhere". For
Daisy rows, use markers such as `covered_by_unified_V_daisy` or an explicit
`implementation_owner` column when a mode is included through a reviewed
unified/custom V_daisy expression rather than generated from that row.

Mass matrices should be displayed as a compact metadata table plus one named
Python code block per matrix entry, for example
`Matrix entry: scalar_P_hH[h1,h2]` with an `entry:` code block. The heading must
identify both the matrix and the component. Avoid putting long matrix entries
directly in Markdown matrix cells, and do not include empty `notes` columns in
entry-status tables. Routine source-LaTeX blocks are optional for mass matrices;
prefer concise notes and readable compiler code unless the source formula is
needed to resolve an ambiguity.

Mass and matrix code blocks should be readable derivations, not anonymous large
expressions. Name physically meaningful pieces such as `h_sq`, `base_11`,
`theta_A_12`, `mW_sq`, `mG0_sq`, `delta_A`, and finish with a final assignment
or final expression. This is especially important for Goldstone masses,
field-dependent scalar matrices, gauge masses, and Daisy thermal masses.
Thermal-mass summary rows such as `scalar_thermal_masses` and
`gauge_thermal_masses` should also point to Python code blocks instead of
packing formulas into table cells.

Compiler-expression status columns such as `expr_status`, `mass_sq_status`,
`entry_status`, `zeroT_mass_sq_status`, `thermal_mass_sq_status`, and
`daisy_term_status` mean that the corresponding table expression or Python code
block has a fixed review state. Allowed values are `needs_review`,
`agent_reviewed`, and `human_modified`; compilation requires `agent_reviewed`.
Use `human_modified` when a user edits a previously reviewed block so continue
mode can quickly find the changed expressions, then Codex/PTagent must recheck
the expression and set it back to `agent_reviewed` only after review. The
status is no longer tied to a separate display-formula column.

`Compiler expression` and named expression blocks may be assembled like small
Python snippets with simple temporary assignments. The final line may be either
a final expression or a final simple assignment such as `total = m + n`; that
final value is what the compiler inserts into the generated Python. Blocks must
contain no imports, function/class definitions, mutation, loops, or hidden
physics choices. Use them to name repeated masses, thermal counterparts,
counterterm pieces, or Daisy contributions instead of forcing a single
monolithic line.

When a potential part shows `Compiler expression: 0.0`, the template must make
clear whether this means a genuine zero contribution or only that the term is
owned by another reviewed route such as standard CW `V1`, standard thermal integrals, an
explicit CT linear system, or a unified `V_daisy` block.

The generated Markdown should be verbose rather than clever: show all relevant
mass matrices, state which entries are diagonalized, document each particle's
Daisy thermal mass route, explain exactly how Goldstones are included/excluded
or regulated, and make the counterterm equations visible before code generation.

If a field is ambiguous, leave `ASK_USER`; compilation must block.
Users may fill cells and add rows to repeated tables, but must not rename fixed
section headings or table columns.

LaTeX blocks are evidence/review text. `Compiler expression` blocks, named
expression code blocks, and numeric defaults are parsed into
`contract_resolved.json` by the deterministic resolver. Keep fixed table
headings stable so the resolver can attach each named code block to the matching
row.

Never use a vacuum physical-mass relation as a field-dependent mass in
`boson_massSq`, `fermion_massSq`, V1T, or Daisy. Use it only for parameter
solving, vacuum checks, or reviewer notes.

The current Markdown template is the only supported template format. Do not
recreate old JSON-fence templates or old numbered sections such as `Potential
Assembly`; regenerate a fresh `contract_template.md` instead.

Generated CosmoTransitions code must use standard method names. For an OS-like
one-loop route, override `V1(self, bosons, fermions)`; do not invent names like
private paper-specific one-loop aliases. Generated PhaseTracer C++ must use
standard native hooks such as `V0`, `V1`, `V1T`, `V`, `get_*_masses_sq`,
`get_*_dofs`, `get_raddof`, `apply_symmetry`, and `get_symmetry_axes`.

Generated model files carry `PTAGENT_TEMPLATE_SHA256` and
`PTAGENT_CONTRACT_SHA256`. When checking an existing model, compare the template
hash with the current `contract_template.md`; a missing or mismatched hash means
the Python file is stale and must be regenerated.

## Generated-Code Readability Rules

Generated CosmoTransitions code must be readable enough for a physicist to
audit. Keep the standard public hooks intact: `V0`, `V1`, `V1T_from_X`,
`Vtot`, `boson_massSq`, `fermion_massSq`, `approxZeroTMin`,
`forbidPhaseCrit`, and `build_model`.

`Vtot` should assemble named potential pieces, not contain large anonymous
algebra blocks. If an expression contains repeated masses, matrices, thermal
counterparts, counterterms, or Daisy/ring terms, give the relevant physical
quantities names and reuse them. Prefer names such as `mW_sq`, `mZ_sq`,
`M_even`, `m_even_sq`, `mh_sq`, `mH_sq`, `mW_T_sq`, and `m_even_T_sq` over
repeating the full expression inline.

Do not split every algebraic line into a helper. Add helpers only for standard
CosmoTransitions hooks, physically meaningful reusable blocks such as a mass
spectrum or counterterm solver, or a contract-reviewed convention that is used
more than once. Helpers must not choose physics. Daisy prescriptions, thermal
masses, Goldstone handling, photon/neutral gauge branches, cubic-power
conventions, and d.o.f. must come from the reviewed contract; if they are not
confirmed, compilation must block instead of using a default helper behavior.

For Arnold-Espinosa, keep ordinary field-dependent masses in `V1`/`V1T`, add a
separate reviewed `Vdaisy(X, T)` helper, and make `V1T_from_X` include that same
helper so CosmoTransitions temperature derivatives see the explicit Daisy term.
For Parwani, do not generate a separate `Vdaisy`; `V1T_from_X` must include the
temperature-dependent one-loop route implied by the reviewed thermal-mass
replacement. Never merge these two routes into one generic implementation.

Do not repair CosmoTransitions thermal smoke failures by adding unreviewed
fermion species. For a reviewed no-fermion model, preserve the empty fermion
spectrum and use a backend-safe standard `V1T` path that skips explicit fermion
thermal integrals while retaining total radiation d.o.f.

When Section 6 provides reviewed per-particle Daisy rows with `zeroT_mass_sq`,
`thermal_mass_sq`, and `dof`, generated code should prefer named mass terms and
a small contract-driven cubic helper over a monolithic `custom_expr`. If the
rows are not structurally complete but the reviewed `V_daisy` custom expression
has the recognizable Arnold-Espinosa form `prefactor * sum_i n_i[f(M_i^2(T)) -
f(m_i^2)]`, split it conservatively into named contributions such as
`daisy_scalar_...`, `daisy_vector_...`, `m2T_...`, and `m2_...`, then assemble
the sum. This fallback may extract the mass expressions already present in the
reviewed formula, but it must not invent missing thermal masses, mode groups,
d.o.f., or cubic-power prescriptions.

## Generated-Code Cleanup Checklist

Use this as the final prompt/checklist for generated backend code, especially
when a less capable coding model edits the file:

- Clean the generated code without changing the reviewed physics contract.
- Keep only imports, attributes, helper methods, local variables, and metadata
  that are actually used.
- In every generated function, remove local variables that are assigned but
  never read later in that function. This includes stale self-attribute aliases,
  unused compiler-block temporaries, and obsolete intermediate masses.
- Use standard CosmoTransitions method names: `V0`, `V1`, `V1T_from_X`, `Vtot`,
  `boson_massSq`, `fermion_massSq`, `approxZeroTMin`, `forbidPhaseCrit`, and
  `build_model`.
- Remove paper-specific aliases, stale comments, unused temporaries, duplicate
  formulas, unreachable branches, and obsolete generated-code leftovers.
- Do not rename public inputs, fields, or reviewed contract symbols.
- Do not replace reviewed field-dependent matrices with vacuum mass relations.
- Do not add, remove, or guess Goldstone, photon, Daisy, counterterm,
  phase-filter, or PhaseTracer symmetry conventions.
- Do not add unreviewed fermion species just to avoid a CosmoTransitions
  empty-spectrum thermal edge case. Keep the explicit spectrum empty and rely on
  the generated safe standard `V1T` hook.
- Preserve intentional blank lines between physical blocks in long generated
  methods such as `boson_massSq`, `Vtot`, `V1T_from_X`, and `Vdaisy`. Cleanup may
  remove unused code, but should not collapse readable block structure into a
  monolithic expression wall.
- After cleanup, run `py_compile`/import smoke checks and confirm the public
  input signature still matches the contract. CosmoTransitions smoke must
  exercise `forbidPhaseCrit` on both single-field-vector and batched `X` inputs;
  PhaseTracer smoke must exercise generated `apply_symmetry(phi)` equivalence
  outputs when symmetry is enabled.

Generated PhaseTracer C++ code should receive the same cleanup intent:

- Keep C++ cleanup backend-local; do not route C++ through CosmoTransitions
  renderer helpers.
- Remove unused local `const double` temporaries and redundant `(void)` lines
  only when the variable is actually read later in the same C++ scope.
- If `clang-format` is available, allow the PhaseTracer backend to format
  generated `.hpp`/`.cpp` files after backend-local cleanup. Missing
  `clang-format` is optional and must not block compilation.
- Preserve field order, public inputs, reviewed formulas, d.o.f., CT equations,
  Goldstone handling, Daisy routing, and PhaseTracer symmetry rules exactly.
- For cross-backend checks, run compare mode from the reviewed contract. It
  reuses hash-matched generated artifacts or regenerates stale/missing ones,
  runs PhaseTracer `run_model`, then compares generated Python `Vtot(phi,T)`
  against C++ `V(phi,T)` at the same smoke points. The final compare output is
  mandatory and must stay short: list both model paths, how to call each model,
  the compared `phi,T` points, both backend values, absolute/relative
  differences, and one conclusion: consistent or inconsistent.

## Output Standard

This section is mandatory, not advisory. After any successful compile, show a
Model Delivery Receipt in the final response. If the receipt cannot be
completed, say the model delivery is incomplete and list the blocker.

Return:

- workflow mode: `fresh` or `continue`;
- source of truth used for this run;
- `input/` directory path for the archived source material;
- model-selection status and `proof_materials/model_candidates.md` path when
  pre-contract model selection blocks template generation;
- `contract_template.md` path;
- `proof_materials/contract_resolved.json` path;
- compiled model path if compilation succeeded;
- how to call the generated model file or PhaseTracer project;
- for CosmoTransitions: import-smoke status, whether `calcTcTrans()` was run,
  `PTAGENT_TCTRANS`/Tc rows when available, or the explicit reason transition
  running was skipped;
- for PhaseTracer: potential-smoke status, transition-smoke status,
  transition/Tc rows when available, or the explicit reason transition testing
  was skipped;
- for compare-backends: both model paths, how to call each model, compared
  points, CosmoTransitions/PhaseTracer values, absolute/relative differences,
  and a one-line consistent/inconsistent conclusion;
- how the generated artifact prints/returns critical-temperature output;
- public input signature;
- remaining template blockers, if any;
- local smoke-check result;
- short physics note on what was user-supplied versus source-extracted.
