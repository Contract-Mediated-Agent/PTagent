# Batched Questions And Contract Graphs

This shared review procedure applies to paper/manual 4D, SARAH 4D, and DRalgo
3DEFT. It does not change the physics resolver or the final approval gate.

## Question Batch

1. Read all accessible current sources and proof records, the user's complete
   request and previous answers, and the current Markdown contract. Run the
   route's resolver/validation and shared symmetry check. Fill evidence-backed
   fields first; do not send the raw validation list as a questionnaire.
2. Collect every remaining decision in ONE numbered message, grouping related
   fields. For each give a concise question, evidence checked, impact, and a
   clearly labeled recommendation. Cover model/backend selection, input basis
   and values, scales, species/d.o.f., loop/resummation conventions, counterterms,
   Goldstone/photon policy and symmetry/phase handling where applicable. For
   3DEFT include matching/RG choices and unresolved source interpretations.
3. Do not ask a question already answered explicitly. Include backend-conditional
   choices in the same batch when possible. If source/model selection or a tool
   blocker prevents further analysis, collect all answerable questions now and
   state which checks require that prerequisite. Do not guess a model to avoid
   the prerequisite. Never deliberately withhold a discoverable question.
4. Apply the answer batch, revalidate and regenerate the graph. Partial answers
   are allowed; ask only for outstanding items or ambiguities newly exposed by
   an answer/tool output, explaining why they are new. A source amendment
   invalidates only affected decisions, not unrelated confirmed choices.
5. Once construction blockers are resolved, show the final contract AND graph
   and request explicit compilation approval. Answers to physics questions do
   not approve an as-yet unseen revised contract. Keep installation, executing
   external tools and final compilation within their existing approval gates.

## Graph Content And Meaning

The agent supplies an evidence-bound display specification; Python renders it.
The renderer does not infer physics, invent provenance or confirm user choices.
The Markdown remains the source of truth. Save the specification at
`proof_materials/contract_graph_spec.json` in the current run.

- Green: information explicitly in a paper or model/export file. Annotate the
  equation/page/XML location. SARAH provenance belongs to actual export files,
  not every downstream modeling choice.
- Blue: deterministic derivation with inputs and a proof/calculation record.
  Typical ports are tree potential, field normalization, mass matrices,
  eigenvalues, d.o.f./CW constants, parameter conversion and thermal matrices.
- Orange: information the user actually specified or confirmed in this task,
  supported by an exact quote saved in an input message file. Agent-reviewed
  formulas, inferred defaults and requests to analyze a model are not user
  confirmation of policies or compilation approval.
- Pale red: pending interpretation/recommendation. A tree-potential symmetry
  candidate or interpretation of `Vextra` as counterterms stays pale red until
  confirmed. Connect grounded recommendations to their evidence port, e.g.
  `Vtree -> proposed symmetry policy`. Use a single LLM source box only for
  proposals without a specific source dependency. Do not claim all model
  choices originated in the LLM.
- Gray: missing data, unbound numeric inputs or unavailable thermal results.
  Missing companion metadata must not become invented thermal masses. Distinguish
  symbolic availability from readiness for numerical calculation.

Show all applicable contract blocks and pending blockers, not just `Vtree` and
the mass spectrum. Include scales, parameter binding, species inclusion and
exclusion, radiation counts, loop/resummation choices, counterterms, gauge and
Goldstone/photon policy, and phase/symmetry handling. For 3DEFT use source and
DRalgo outputs, EFT stages, matching/RG scales, coefficient maps and the 3D
potential, not an invented four-dimensional loop assembly.

End the diagram at `Contract for review`. No solver boxes, Tc/Tn or later run
results. Use compact boxes with full names inside, quantity labels outside on
curved arrow connections, a white background, the fixed palette and readable
small labels. No top banner/legend, redundant Paper box, folded-corner decoration,
or captions under boxes. Preserve the graph's meaning when simplifying labels.

Keep the compact port-graph layout: source files on the left, evidence and
calculation branches in the upper area, and the contract beside that cluster
on the right. User inputs and ungrounded LLM proposals occupy horizontal lanes
below it, curving upward into the contract. Grounded pending interpretations
branch from their actual evidence into separate top lanes. Avoid a tall list
of equally spaced boxes or return wires through the contract's input fan.
Keep cross-branch dependencies clear of unrelated boxes; crossing wires are
not junctions unless marked. Labels stay outside boxes, smaller than box names.

## Rendering

Inspect the current Markdown using the installed skill's backend:

```bash
python <skill-dir>/scripts/render_contract_graph.py --template <contract.md> --inspect
```

This returns the current template SHA256, parsed contract and required review
sections. Use those real values when writing the display specification. Never
copy a previous task's hashes, formulas, approvals or policies. Then render:

```bash
python <skill-dir>/scripts/render_contract_graph.py \
  --template <contract.md> --spec <run>/proof_materials/contract_graph_spec.json
```

Outputs are `proof_materials/contract_graph/contract_graph.png`, `.pdf`, `.svg`
and `.json`. The JSON binds labels to actual contract values, source hashes and
the template/spec hashes. Rebuild after every contract change. The command
fails on stale templates, invalid pointers, missing evidence, cyclic links,
omitted major review sections, unsupported user-confirmed claims, or overlapping
labels. These checks do not prove scientific truth or complete field-level
coverage: inspect every relevant contract block and proof when authoring.

Open the generated PNG and check routing, labels and provenance against the
contract. Shorten labels, split a crowded branch or use explicit newlines if
needed; do not omit physical content to pass layout checks. Show the PNG inline
in the SAME response as the contract link and the complete question batch.
If the client cannot display images, supply a direct image link and say so.
If rendering fails, report the exact problem and continue with the Markdown
review; never substitute a stale picture or claim the graph was generated.
Do not install missing plotting dependencies without permission.

## Display Specification

The JSON has `version: 1`, `template_sha256`, `sources`, `branches`, and optional
`links`. It is a display/audit artifact, never a replacement compiler input.

- A source has `id`, `label`, `kind` (`source`, `user`, `proposal`). Source-file
  nodes also have `evidence: [{"file": "input/model.vin", "location": "SARAH export"}]`.
  Paths are relative to the task directory, or absolute for explicitly used
  external files. Do not use invented paths or remote URLs as local proof.
- A branch has `source` (source id), `inputs` (quantity ports), optional
  `operation` (short full name inside a blue box), and `outputs` (derived ports).
  Without an operation its input ports connect directly to the contract. With
  an operation, inputs feed that box and outputs feed the contract. Set an
  input's `to_contract: true` only if it also directly supplies a contract item.
- Each port has a unique `id`, short `label` (mathtext is supported), `kind`
  (`source`, `derived`, `user`, `proposal`, `blocked`), and `refs`, a list of
  JSON pointers into the CURRENT parsed contract, e.g. `/potential/V0` or
  `/masses/boson_matrices/0`. Use multiple ports where provenance differs;
  never color a mixed section as though all values shared the same source.
  A source-only contextual quantity may instead set `context_only: true`.
- Source, user and derived ports require `evidence` with file and location.
  User ports also require `user_quote`, an exact substring of a saved user
  message. Derived ports require `calculation`, identifying the deterministic
  operation and assumptions; cite its actual proof file. A user authorizing a
  Hessian correction does not make its algebra user-provided: show the instruction
  in orange and the computed Hessian in blue, connected by a dependency.
- `links` is a list of `[from_port_id, to_port_id]` pairs for extra dependencies,
  including cross-branch dependencies. Put a source-grounded pending policy in
  an output port of the relevant calculation branch and link further inputs
  as needed. Do not route a symmetry recommendation solely from the LLM box.

Example port (only a schema illustration, not source evidence):

```json
{
  "id": "symmetry_policy",
  "label": "Sign-flip phase policy",
  "kind": "proposal",
  "refs": ["/implementation/symmetry"]
}
```

Use `links: [["tree_potential", "symmetry_policy"]]` only when a real
`tree_potential` port and the current task's symmetry analysis support it.
Values such as `Vextra=0`, a resummation scheme or a filter threshold must never
be classified from their names alone.
