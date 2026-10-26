"""Review-only presentation for hash-bound SARAH preparations."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


REPREPARE = (
    "Apply accepted inputs with prepare-sarah --parameters or --slha and policies with --choices, "
    "using --source-parameters for source-symbol scalar/array inputs and --tree-loop-policy equal_tree only when that prescription is accepted. "
    "using the same exports, optional --model-source/--thermal-metadata, and a fresh --run-dir. "
    "Preserve existing explicit inputs. Do not patch prepared physics in Markdown and rerun resolve: "
    "that invalidates its digest. No repeat Wolfram export is needed. Unsupported conversions remain blockers."
)


def _policy_rows(contract, manifest):
    policies = manifest["policies"]
    rows = [
        ("backend", "Backend", ["/model_card/compile_backend"], "defaults"),
        ("resummation_scheme", "Resummation", ["/model_card/resummation_scheme", "/implementation/daisy/scheme"], "thermal"),
        ("cubic_power_policy", "Cubic powers", ["/implementation/daisy/cubic_power_policy"], "thermal"),
        ("goldstone", "Goldstones", ["/model_card/goldstone", "/implementation/goldstone/cw_policy", "/implementation/goldstone/thermal_policy"], "spectrum"),
        ("photon", "Photon modes", ["/model_card/photon"], "spectrum"),
        ("counterterm", "Extra polynomial policy", ["/model_card/counterterm", "/implementation/counterterms/mode"], "extra"),
    ]
    if policies["backend"] == "cosmotransitions":
        base = "/implementation/phase_filter"
        rules = contract["implementation"]["phase_filter"]["rules"]
        rows.append(("phase_filter", "Phase handling", [base + "/mode"], "tree"))
        for key, label, column in (("phase_filter_field", "Field group", "field"),
                                   ("phase_filter_threshold", "Field tolerance", "threshold")):
            if rules:
                rows.append((key, label, [f"{base}/rules/{i}/{column}" for i in range(len(rules))], "tree"))
    else:
        rows.append(("phasetracer_symmetry", "PhaseTracer symmetry", ["/implementation/symmetry/mode"], "tree"))
        if contract["implementation"]["symmetry"]["rules"]:
            rows.append(("symmetry_generators", "Sign-flip generators", ["/implementation/symmetry/rules"], "tree"))
    return rows


def review_questions(contract, manifest, report):
    """Use both unresolved fields and unaccepted policies, not blank-template prompts."""
    questions = []
    explicit = manifest.get("explicit_overrides", {})
    choices = explicit.get("choices", {})
    benchmark = manifest.get("benchmark_recommendation", {})
    approved = contract["review"]["approved"]

    def add(field, title, question, checked, impact, recommendation, action=REPREPARE):
        questions.append(dict(field=field, title=title, question=question, checked=checked,
                              impact=impact, recommendation=recommendation, action=action))

    inputs = contract["parameters"]["public_inputs"]
    missing = [r["name"] for r in inputs if r.get("test_value") in {None, "", "ASK_USER"}]
    names = [n for n in missing if n != "renormScaleSq"]
    if inputs and not approved:
        example = benchmark.get("default_inputs", {}) if benchmark.get("status") == "pending_review" else {}
        tree = manifest.get("tree_review") or {}
        native = tree.get("status") == "pending_review"
        independent = tree.get("missing_source_inputs", [])
        unmapped = sorted(set(names) - set(tree.get("supported_aliases", [])))
        add("parameters.public_inputs", "Input basis and numerical point",
            ("Review the source benchmark and tree input prescription together. Remaining independent source inputs: "
             + (", ".join(independent) or "none")
             + ". Additional unmapped bindings: " + (", ".join(unmapped) or "none")
             + ". Do not enter derived VEVs, matrix elements or tadpole masses individually."
             if native else "Confirm the public input basis and provide remaining numeric bindings: " + (", ".join(names) or "input confirmations only") + "."),
            "Imported expressions, parameter map, SLHA/explicit bindings, SPheno defaults, boundary/matching relations and tadpole parameter declarations.",
            "Changes the build interface, vacuum and field-dependent spectrum.",
            ("Propose the source example, not yet applied: " + json.dumps(example, sort_keys=True) + ". " if example else "Preserve the supplied point. ")
            + ("Use the native SARAH tree relations without running SPheno. Propose equal_tree for missing Loop aliases only as a reviewed tree input prescription, keeping CW/thermal loops but not claiming loop matching. Preserve existing SLHA and explicit Loop values. Inspect installed source numerical definitions before proposing any remaining SM input set, and state its mass/scale convention. Include this choice, Q and all other pending policies in this first batch; never ask again for derived elements after acceptance."
               if native else "Distinguish source inputs, supported derived quantities and remaining SM inputs; do not treat all unresolved implementation symbols as independent scan coordinates. No guessed Yukawas or tree/loop equality."))
        if tree.get("status") in {"branch_required", "blocked", "unavailable"}:
            add("parameters.tree_binding", "Native tree binding limitation", json.dumps(tree, sort_keys=True),
                "Native SARAH solutions, restricted source relations and source hashes.",
                "Prevents automatic tree input binding; source import remains available.",
                "Select an explicit branch or supply only the unsupported bindings. SPheno is not a required dependency.")
    if "renormScaleSq" in missing:
        add("parameters.renormalization_scale", "Renormalization scale",
            "Provide a positive Q or renormScaleSq consistent with the numerical point.",
            "Source scale selection and available SLHA/explicit parameters; no numerical scale resolved.",
            "Changes the Coleman-Weinberg logarithms.", "Use the point's sourced or explicitly selected scale; do not substitute a scale bound or invent a value.")
    if not approved:
        pending = [(key, label) for key, label, _, _ in _policy_rows(contract, manifest) if key not in choices]
        if pending:
            proposed = {key: manifest["policies"][key] for key, _ in pending}
            add("implementation.policies", "Pending physical policies",
                "Accept or revise the pending proposals: " + json.dumps(proposed, sort_keys=True) + ".",
                "Source spectrum, extra polynomial, thermal derivation and tree-level sign-flip analysis; explicit choices were excluded from this question.",
                "Changes resummation, special-mode treatment, counterterm interpretation or phase handling.",
                "Review the proposed values together. Preserve the imported extra polynomial only with its reviewed interpretation; it is not an inferred on-shell fit. Check full-potential symmetry before use. Coupled flips require whole-trajectory symmetry_orbits in symmetry_phase_history, retaining all native phases for Tc/tunnelling; tolerances are numerical conventions and need sensitivity checks.")
        add("loops.spectrum", "Spectrum and loop assembly",
            "Accept the complete imported spectrum, standard Coleman-Weinberg and thermal integrals, with no selective species removal?",
            "Imported matrices, d.o.f., CW constants and source gauge/scheme; matrix-based thermal routing.",
            "Changes the effective potential and which species contribute.",
            "Retain the source spectrum; a changed species inventory requires a consistently reviewed thermal treatment. Source gauge and scheme are already recorded, not guessed.")
        radiation = {k: v for k, v in manifest["radiation"]["values"].items() if k not in explicit.get("parameters", {})}
        if radiation:
            add("parameters.radiation", "Radiation inventory",
                "Confirm or revise retained-mode totals: " + json.dumps(radiation, sort_keys=True) + ".",
                "Every retained sector and its multiplicity in sarah_preparation.json radiation.inventory.",
                "Changes field-independent radiation and downstream thermodynamics.",
                "These are retained-inventory counts, not an assumed complete bath. Review massless-vector polarizations and unlisted decoupled species. Preserve explicit count overrides.")
    for issue in report.get("issues", []):
        if issue["code"] == "scan_approval_required":
            add("numerical_settings.Tmax", "Temperature range (scanning only)", issue["message"],
                "Current numerical settings and available source/model scales.",
                "Limits the temperatures searched; not a potential-compilation blocker.",
                issue.get("suggested_action", "Confirm the proposed range or supply a justified alternative."),
                "After explicit confirmation, update Section 11 Tmax and matching Tmax_approved_value, set Tmax_approved=true. This separate scan control does not require new physical preparation.")
        elif issue.get("severity") == "error" and issue["code"] not in {
            "input_basis_unconfirmed", "placeholder_value", "missing_renormalization_scale", "review_required"
        }:
            add(issue["field_key"], "Additional construction blocker", issue["message"],
                "Current deterministic validation report.", "Prevents valid model construction.", issue.get("suggested_action", "Resolve from source evidence without guessing."))
    return questions


def render_questions(contract, manifest, questions):
    lines = ["# Prepared SARAH review", "", REPREPARE, "",
             "Review contract_template.md and proof_materials/contract_graph/contract_graph.png together.",
             "Final compilation approval is a separate gate after all construction blockers are resolved; use approve-sarah with the exact reviewed hash and actual approval quote.", "",
             "## Already supplied", "", "Explicit overrides (not compilation approval):",
             "```json", json.dumps(manifest.get("explicit_overrides", {}), indent=2, sort_keys=True), "```", "",
             "## All pending decisions", "", f"Questions in this batch: {len(questions)}", ""]
    for i, q in enumerate(questions, 1):
        lines += [f"### Q{i}. {q['title']}", "", q["question"], "",
                  "- Checked: " + q["checked"], "- Impact: " + q["impact"],
                  "- Recommendation: " + q["recommendation"], "- On acceptance: " + q["action"], ""]
    if not questions:
        lines.append("No outstanding construction/policy questions. Preserve the current approval state.")
    return "\n".join(lines) + "\n"


def review_graph(template: Path, contract: dict) -> dict:
    root = template.parent
    manifest_path = Path(contract["model_card"]["sarah_preparation_manifest"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    explicit = manifest.get("explicit_overrides", {})
    explicit_file = root / "input/explicit_overrides.json"
    source_file = Path(contract["model_card"]["source_vin_path"])
    sources = [{"id": "source", "label": "SARAH export", "kind": "source",
                "evidence": [{"file": str(source_file), "location": "VevaciousModelFile"}]},
               {"id": "defaults", "label": "Pending review", "kind": "proposal"}]
    branches, links = [], []
    pending, supplied = [], []
    approval_file = root / "proof_materials/sarah_approval.txt"
    approved = contract["review"]["approved"]

    def port(name, label, kind, refs, file=None, calculation=None):
        row = dict(id=name, label=label, kind=kind, refs=refs)
        if not refs:
            row["context_only"] = True
        if file:
            row["evidence"] = [{"file": str(file), "location": name}]
        if calculation:
            row["calculation"] = calculation
        return row

    def policy(name, label, refs, explicit_value=False, anchor=None):
        if explicit_value or approved:
            file = explicit_file if explicit_value else approval_file
            row = port(name, label, "user", refs, file)
            # This is the exact saved structured input, never invented prose approval.
            row["user_quote"] = file.read_text(encoding="utf-8").strip()
            supplied.append(row)
        else:
            row = port(name, label, "proposal", refs, manifest_path)
            pending.append(row)
        if anchor:
            links.append([anchor, name])

    field_refs = [f"/fields/{i}/name" for i in range(len(contract["fields"]))]
    spectrum_refs, thermal_refs = [], []
    for group in ("boson_matrices", "fermion_matrices"):
        for i, matrix in enumerate(contract["masses"].get(group, [])):
            base = f"/masses/{group}/{i}"
            spectrum_refs.extend(base + "/" + key for key in ("matrix", "basis", "dof_per_eigenvalue", "c"))
            if matrix.get("thermal_matrix"):
                thermal_refs.append(base + "/thermal_matrix")
    inputs = [port("tree", "Tree potential and fields", "source", ["/potential/V0", *field_refs], source_file),
              port("extra", "Exported polynomial (source only)", "source", [], source_file),
              port("spectrum", "Zero-T matrices, counts, CW", "source", spectrum_refs, source_file),
              port("gauge", "Source gauge and scheme", "source", ["/model_card/gauge", "/model_card/renormalization_scheme"], source_file)]
    imported_file = root / "proof_materials/sarah_import.json"
    imported = json.loads(imported_file.read_text(encoding="utf-8"))
    inputs[1]["evidence"] = [
        {"file": str(source_file), "location": "ExtraPolynomialPart (source context, not the selected CT policy)"},
        {"file": str(imported_file), "location": "/extra_polynomial", "quote": imported["extra_polynomial"]},
    ]
    branches.append(dict(source="source", inputs=inputs, outputs=[]))
    thermal_file = root / "proof_materials/thermal_derivation.json"
    thermal_hash = contract["model_card"].get("source_thermal_metadata_sha256")
    metadata_file = next((p for p in sorted((root / "input").rglob("*.json"))
                          if hashlib.sha256(p.read_bytes()).hexdigest() == thermal_hash), None)
    ready = str(contract["model_card"].get("thermal_ready")).lower() == "true" and bool(thermal_refs) and metadata_file is not None
    thermal = port("thermal", "Thermal matrices" if ready else "Thermal data unavailable", "derived" if ready else "blocked",
                   thermal_refs or ["/model_card/thermal_ready"], thermal_file if ready else None,
                   "Tensor contractions and source-basis alignment, then M0^2 + Pi(T)." if ready else None)
    branches.append(dict(source="source", operation="Thermal construction", inputs=[
        port("thermal_inputs", "Companion data and basis", "source" if ready else "blocked",
             ["/model_card/source_thermal_metadata_sha256"], metadata_file if ready else None)], outputs=[thermal]))
    branches.append(dict(source="source", operation="Sign-flip analysis", inputs=[
        port("symmetry_input", "Tree expression", "source", [], source_file)], outputs=[
        port("symmetry", "Tree symmetry candidates", "derived", ["/symmetry_analysis"], root / "proof_materials/contract_resolved.json",
             "Conservative AST parity check of V0; full-potential invariance still needs review.")]))
    links.append(["tree", "symmetry_input"])
    for key, label, refs, anchor in _policy_rows(contract, manifest):
        value = manifest["policies"][key]
        display = str(value) if not isinstance(value, list) else "see rules"
        policy(key, label + ": " + display, refs, key in explicit.get("choices", {}),
               "symmetry" if anchor == "tree" else anchor if anchor != "defaults" else None)
    policy("spectrum_policy", "Complete spectrum; standard loops", ["/model_card/species_policy", "/loops/zero_temperature", "/loops/thermal"], anchor="spectrum")
    constants = contract["parameters"]["constants"]
    derived_refs, explicit_refs = [], []
    for i, row in enumerate(constants):
        name, ref = row["name"], f"/parameters/constants/{i}/value"
        if name in manifest["radiation"]["values"]:
            policy(name, f"{name}: {row['value']}", [ref], name in explicit.get("parameters", {}), "spectrum")
        elif name in explicit.get("parameters", {}):
            explicit_refs.append(ref)
        else:
            derived_refs.append(ref)
    if explicit_refs:
        policy("explicit_parameters", "Explicit parameter values", explicit_refs, True)
    if derived_refs:
        branches.append(dict(source="source", operation="Parameter binding", inputs=[
            port("binding_inputs", "Source parameter map", "source", [], Path(contract["model_card"]["source_parameter_map_path"]))], outputs=[
            port("bindings", "Resolved values (not approval)", "derived", derived_refs, root / "proof_materials/sarah_import.json",
                 "Deterministic explicit/SLHA/source-map precedence; Q is squared when supplied.")]))
        if explicit.get("parameters"):
            policy("binding_explicit", "Supplied binding inputs", [], True)
            links.append(["binding_explicit", "bindings"])
        if manifest.get("tree_binding"):
            branches[-1]["inputs"].append(port("tree_relations", "Native tree relations", "source", [],
                                              root / "input/ptagent_sarah_tree_relations.json"))
            if explicit.get("source_parameters"):
                policy("source_parameters", "Supplied source inputs", [], True)
                links.append(["source_parameters", "bindings"])
            if explicit.get("tree_loop_policy"):
                policy("tree_loop_policy", "Tree input matching; no loop shifts", [], True)
                links.append(["tree_loop_policy", "bindings"])
    for scale in (False, True):
        refs = [f"/parameters/public_inputs/{i}/test_value" for i, row in enumerate(contract["parameters"]["public_inputs"])
                if (row["name"] == "renormScaleSq") == scale and row.get("test_value") in {None, "", "ASK_USER"}]
        if refs:
            pending.append(port("missing_scale" if scale else "missing_parameters", "Renormalization scale missing" if scale else "Numerical bindings missing", "blocked", refs))
    if contract["parameters"]["public_inputs"] and not approved:
        policy("input_basis", "Public input basis pending", [
            f"/parameters/public_inputs/{i}/confirmed" for i in range(len(contract["parameters"]["public_inputs"]))])
    benchmark = manifest.get("benchmark_recommendation", {})
    if benchmark.get("status") == "pending_review" and benchmark.get("source"):
        source = benchmark["source"]["path"]
        sources.append(dict(id="benchmark_source", label="Model source defaults", kind="source", evidence=[dict(file=source, location="DefaultInputValues")]))
        branches.append(dict(source="benchmark_source", operation="Static source reading", inputs=[
            port("benchmark_inputs", "Example inputs and relations", "source", [], source)], outputs=[
            port("benchmark", "Source example (not applied)", "proposal", [], root / "proof_materials/spheno_benchmark.json")]))
        if contract["parameters"]["public_inputs"] and not approved:
            links.append(["benchmark", "input_basis"])
    if str(contract["numerical_settings"].get("Tmax_approved")).lower() == "true":
        branches.append(dict(source="source", inputs=[port("scan", "Recorded scan settings", "derived",
            ["/numerical_settings"], template, "Separate recorded scan settings; not physical preparation approval.")], outputs=[]))
    else:
        pending.append(port("scan", "Temperature range review", "proposal", ["/numerical_settings"], template))
    if pending:
        branches.append(dict(source="defaults", inputs=pending, outputs=[]))
    else:
        sources = [s for s in sources if s["id"] != "defaults"]
    if supplied:
        sources.append(dict(id="explicit", label="Explicit inputs / review", kind="user"))
        branches.append(dict(source="explicit", inputs=supplied, outputs=[]))
    return dict(version=1, template_sha256=hashlib.sha256(template.read_bytes()).hexdigest(), sources=sources, branches=branches, links=links)
