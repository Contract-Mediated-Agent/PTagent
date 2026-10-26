"""Model-independent deterministic SARAH preparation and hash-bound approval."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import shutil
from pathlib import Path
from typing import Any

from ptagent.runtime_metadata import collect_software_versions

from .model_defaults import DEFAULTS_ID, DEFAULT_RULES, POLICY_DEFAULTS, phase_filter_suggestion, phase_handling_recommendation
from ..symmetry_analysis import analyze_sign_flip_symmetries
from .safe_expression import evaluate_safe_expression, parse_safe_expression
from .sarah_contract import build_sarah_contract, _insert_model_card_rows, _replace_model_card_value, _replace_potential_compiler_expression
from .sarah_import import SarahImportError, import_sarah_model, run_sarah_import
from .spheno_benchmark import read_spheno_benchmark
from .template_contract import contract_to_model_ir, parse_contract, validate_contract_template, write_resolved_contract_data


def file_hash(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False, ensure_ascii=False) + "\n", encoding="utf-8")


def contract_digest(contract: dict) -> str:
    """Bind prepared physics; downstream numerical scans have a separate approval."""
    payload = copy.deepcopy(contract)
    for key in ("review", "status", "source_hash", "numerical_settings"):
        payload.pop(key, None)
    for row in payload.get("parameters", {}).get("public_inputs", []):
        row["confirmed"] = False
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _section(markdown: str, title: str, replacement: str) -> str:
    start = markdown.index(title)
    level = len(title)-len(title.lstrip("#"))
    match = re.search(r"\n#{1," + str(level) + r"} ", markdown[start+len(title):])
    end = start+len(title)+match.start()+1 if match else len(markdown)
    return markdown[:start]+title+"\n\n"+replacement.rstrip()+"\n\n"+markdown[end:]


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    return "\n".join("| " + " | ".join(str(v) for v in row) + " |" for row in [headers, ["---"]*len(headers), *rows])


def _kv(markdown: str, heading: str, values: dict) -> str:
    return _section(markdown, heading, _table(["key", "value"], list(map(list, values.items()))))


def _policies(model, choices: dict) -> dict:
    if not isinstance(choices, dict):
        raise SarahImportError("Policy overrides must be a JSON object.")
    policy = {
        **POLICY_DEFAULTS,
        "counterterm": "none" if model.extra_polynomial in {"0", "0.0"} else "source_polynomial",
        "phase_filter_field": model.fields[0], "phase_filter_threshold": None,
        "symmetry_generators": [],
    }
    unknown = set(choices)-set(policy)
    if unknown:
        raise SarahImportError("Unknown choices: " + ", ".join(sorted(unknown)))
    policy.update(choices)
    field_rows = []
    for field in model.fields:
        try:
            scale = evaluate_safe_expression(model.dsb_minimum.get(field, "0"), model.parameter_values)
        except (ValueError, KeyError):
            scale = None
        field_rows.append({"name": field, "zeroT_default": scale})
    analysis = analyze_sign_flip_symmetries(parse_safe_expression(model.tree_potential).python, model.fields)
    proposal = phase_handling_recommendation({"fields": field_rows, "symmetry_analysis": analysis.to_dict()}, policy["backend"])
    proposed_filter_rows = []
    if proposal["status"] == "pending_review":
        if policy["backend"] == "phasetracer" and not {"phasetracer_symmetry", "symmetry_generators"} & choices.keys():
            policy["phasetracer_symmetry"] = proposal["mode"]
            policy["symmetry_generators"] = [row["fields"].split(",") for row in proposal["rules"]]
        elif policy["backend"] == "cosmotransitions" and not {"phase_filter", "phase_filter_field", "phase_filter_threshold"} & choices.keys():
            policy["phase_filter"] = proposal["mode"]
            proposed_filter_rows = proposal["rules"]
            policy["phase_filter_field"] = proposed_filter_rows[0]["field"]
            policy["phase_filter_threshold"] = proposed_filter_rows[0]["threshold"]
    allowed = {
        "backend": {"cosmotransitions", "phasetracer"},
        "resummation_scheme": {"none", "Parwani", "Arnold-Espinosa"},
        "counterterm": {"none", "source_polynomial"},
        "goldstone": {"included"}, "photon": {"retained"},
        "phase_filter": {"none", "negative_field_threshold", "symmetry_orbits"},
        "phasetracer_symmetry": {"none", "z2_reflection"},
        "cubic_power_policy": {"positive_part", "signed_abs"},
    }
    for key, options in allowed.items():
        if not isinstance(policy[key], str) or policy[key] not in options:
            raise SarahImportError(f"Unsupported {key}={policy[key]!r}; supported: {sorted(options)}. Selective species removal needs an explicit reviewed contract, not a model-name shortcut.")
    filter_fields = [field.strip() for field in str(policy["phase_filter_field"]).split(",")] if policy["phase_filter"] == "symmetry_orbits" else [policy["phase_filter_field"]]
    if not filter_fields or len(set(filter_fields)) != len(filter_fields) or any(field not in model.fields for field in filter_fields):
        raise SarahImportError("The phase-filter field must be in the imported background basis.")
    threshold = policy["phase_filter_threshold"]
    orbit_mode = policy["phase_filter"] == "symmetry_orbits"
    if threshold is not None and (isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not math.isfinite(threshold) or (threshold <= 0 if orbit_mode else threshold > 0)):
        raise SarahImportError("The phase-filter threshold must be finite and nonpositive for a spatial cut, or positive for an orbit-comparison tolerance.")
    if policy["phase_filter"] != "none" and threshold is None:
        raise SarahImportError("Choose an explicit spatial-cut threshold or orbit-comparison tolerance after reviewing the proposed rules.")
    if policy["phase_filter"] == "none" and threshold is not None:
        raise SarahImportError("A threshold was supplied with phase_filter=none; resolve this conflict explicitly.")
    groups = policy["symmetry_generators"]
    if not isinstance(groups, list) or any(not isinstance(group, list) or not group or any(not isinstance(f, str) or f not in model.fields for f in group) or len(set(group)) != len(group) for group in groups):
        raise SarahImportError("symmetry_generators must be a list of nonempty field-name lists.")
    if bool(groups) != (policy["phasetracer_symmetry"] == "z2_reflection"):
        raise SarahImportError("Explicit symmetry generators are required exactly when z2_reflection is selected.")
    policy["proposed_filter_rows"] = proposed_filter_rows
    policy["symmetry_proposal"] = proposal
    return policy


def _numerical_checks(model, *, require_tree_stationary=False) -> dict:
    """Check the imported point; do not solve or replace the source parameters."""
    proof = {"passed": True, "scope": "Verified import only; numerical point evaluation was not run.",
             "numerical_status": "not_run",
             "numerical_skip_reason": "Numerical parameters or renormalization scale remain unresolved.",
             "numerical_ready": model.numerical_ready, "thermal_ready": model.thermal_ready,
             "unresolved_parameters": model.unresolved_parameters, "thermal_blockers": (model.thermal_result or {}).get("blockers", ["thermal_metadata_missing"])}
    if not model.numerical_ready:
        return proof
    import numpy as np
    import sympy as sp

    values = dict(model.parameter_values)
    vacuum = {field: evaluate_safe_expression(model.dsb_minimum.get(field, "0"), values) for field in model.fields}
    values.update(vacuum)
    tree = parse_safe_expression(model.tree_potential)
    symbols = {name: sp.Symbol(name) for name in {*tree.names, *model.fields}}
    potential = sp.sympify(tree.python, locals=symbols)
    tadpoles = [evaluate_safe_expression(str(sp.diff(potential, symbols[f])), values) for f in model.fields]
    spectra = {sector.name: np.linalg.eigvalsh([[evaluate_safe_expression(entry, values) for entry in row] for row in sector.matrix]).tolist() for sector in model.mass_sectors}
    if not all(math.isfinite(v) for v in [*vacuum.values(), *tadpoles, *(x for masses in spectra.values() for x in masses)]):
        raise SarahImportError("The imported reference point has non-finite tadpoles or masses.")
    if require_tree_stationary:
        scales = [max(1.0, sum(abs(evaluate_safe_expression(str(term), values))
                              for term in sp.Add.make_args(sp.expand(sp.diff(potential, symbols[f])))))
                  for f in model.fields]
        if any(abs(t) > 1e-10 * scale for t, scale in zip(tadpoles, scales)):
            raise SarahImportError("Native tree bindings fail the independent imported-potential tadpole check.")
        proof["native_tree_binding_check"] = {"status": "passed", "relative_tolerance": 1e-10,
                                              "term_scales": scales}
    proof.update(reference_point=vacuum, tree_tadpoles=tadpoles, mass_squared_eigenvalues=spectra,
                 numerical_status="passed", numerical_skip_reason=None,
                 scope="Verified import and finite point evaluation; not a proof of vacuum stability or physical suitability.")
    return proof


def render_prepared_contract(model, policy: dict, radiation: dict, suggestion: dict, manifest: Path) -> str:
    text = build_sarah_contract(model)
    text = text.replace(
        "Edit this Markdown file only. PTagent rebuilds `contract_resolved.json` and generated backend code from this file every time.",
        "Review this hash-bound prepared contract. Changed physical inputs or choices require prepare-sarah in a fresh task; do not mutate this Markdown to bind them.",
    )
    text = _section(text, "## 0. How To Use This File", """1. Review the contract, graph and complete question batch in `proof_materials/user_questions.md` together.
2. Answer all missing-input and pending-policy questions in one batch. Explicit choices already supplied are preserved, not asked again.
3. Apply accepted physical inputs through `prepare-sarah --parameters` or `--slha` and accepted policies through `--choices`, using a fresh `--run-dir` and the same exported source files. Preserve prior explicit values. No repeat Wolfram export is required.
4. Do not patch physical inputs, policies or formulas in this prepared Markdown and then resolve it: the prepared-physics digest would be stale. Unsupported source relations remain blockers, not permission to fit or invent values.
5. Section 11 scan controls have a separate approval path. Confirming Tmax does not change the prepared physics; record its exact approved value there.
6. After construction blockers are resolved, present the new contract and graph for separate final approval. Only actual user approval permits `approve-sarah` with the exact reviewed hash; never set approved=true manually.
7. Source evidence, explicit numerical inputs and physics answers do not by themselves authorize backend compilation.""")
    text = _kv(text, "## 8. Approval", {
        "approved": "false",
        "reviewer_notes": "Obtain separate explicit approval of the complete prepared contract, then use approve-sarah with its exact reviewed hash. Never set approved=true manually.",
    })
    text = _section(text, "## 10. Commands", "Use `prepare-sarah` with the same exports and a fresh `--run-dir` for changed parameters/policies. See `proof_materials/user_questions.md` for the complete answer batch. Use `approve-sarah --expected-sha256 <reviewed-hash> --user-approval-quote <actual-approval>` only after construction blockers are resolved and the user approves the reviewed contract.")
    scheme = policy["resummation_scheme"]
    for key, value in {
        "counterterm": "custom_expr" if policy["counterterm"] == "source_polynomial" else "none",
        "resummation_scheme": scheme, "daisy": "custom_expr" if scheme == "Arnold-Espinosa" else "none",
        "goldstone": policy["goldstone"], "photon": policy["photon"],
        "reviewer_notes": "Source-first preparation. All defaults below are proposals until final human approval.",
    }.items():
        text = _replace_model_card_value(text, key, value)
    text = _insert_model_card_rows(text, [
        ("sarah_profile_id", DEFAULTS_ID, "The same source-first rules apply to all models."),
        ("sarah_preparation_manifest", str(manifest), "Hash-bound inputs and decisions."),
        ("compile_backend", policy["backend"], "Explicit choice or generic proposed backend."),
    ])
    parsed = parse_contract(text)
    params = parsed["parameters"]
    inputs = [row for row in params["public_inputs"] if row["name"] not in radiation]
    text = _section(text, "### Public Inputs", _table(["name", "latex", "confirmed", "test_value", "source_type", "description"],
        [[row.get(k, "") for k in ("name", "latex", "confirmed", "test_value", "source_type", "description")] for row in inputs]))
    constants = [[row.get(k, "") for k in ("name", "latex", "value", "description")] for row in params["constants"]]
    constants += [[name, name, value, "Explicit value or proposed retained-spectrum count; check additional bath species in the manifest."] for name, value in radiation.items()]
    text = _section(text, "### Fixed Constants", _table(["name", "latex", "value", "description"], constants))
    text = _kv(text, "### Goldstone Handling", {
        "cw_policy": "included", "ct_derivative_policy": "not_applicable", "thermal_policy": "included",
        "regulator": "none", "replacement_formula": "0.0",
        "notes": "Retain the imported spectrum intact, including any Goldstone modes. No eigenvalue is identified or removed by name or ordering.",
    })
    implementation = {"none": "none", "Parwani": "parwani_thermal_masses", "Arnold-Espinosa": "arnold_espinosa_explicit_daisy"}[scheme]
    text = _kv(text, "### Daisy / Thermal-Mass Handling", {
        "scheme": scheme, "implementation": implementation, "longitudinal_vectors_only": "true",
        "scalar_thermal_masses": "Imported scalar M0^2+Pi(T) matrices in Section 5; missing metadata blocks resummation.",
        "gauge_thermal_masses": "Imported longitudinal M0^2+Pi(T) matrices; transverse modes unresummed.",
        "double_counting_guard": "Parwani replaces masses without a ring term; AE keeps ordinary loop masses and adds one matrix ring subtraction.",
        "cubic_power_policy": policy["cubic_power_policy"],
    })
    text = _replace_potential_compiler_expression(text, "V_daisy", "0.0", "The matrix compiler owns the ring subtraction; no second contribution.")
    text = _kv(text, "### CosmoTransitions Phase Filtering", {
        "mode": policy["phase_filter"], "custom_expr": "0.0",
        "notes": "Source-derived representative-region proposal or explicit user choice; approval is still required. " + suggestion["reason"],
    })
    rows = [[str(policy["phase_filter"] != "none").lower(), policy["phase_filter_field"], "orbit" if policy["phase_filter"] == "symmetry_orbits" else "<",
             policy["phase_filter_threshold"] if policy["phase_filter"] != "none" else suggestion["threshold"],
             "symmetry_duplicate" if policy["phase_filter"] == "symmetry_orbits" else "negative_branch",
             "Review full-potential symmetry before filtering.", "Explicit choice or scale-based proposal."]]
    if policy.get("proposed_filter_rows"):
        rows = [["true", row["field"], row["comparison"], row["threshold"], row["phase_type"], row["reason"], row["source_reference"]]
                for row in policy["proposed_filter_rows"]]
    text = _section(text, "### CosmoTransitions Phase Filter Rules",
                    _table(["enabled", "field", "comparison", "threshold", "phase_type", "reason", "source_reference"], rows))
    text = _kv(text, "### PhaseTracer Symmetry", {
        "mode": policy["phasetracer_symmetry"], "notes": "Detected or explicitly supplied generators, never inferred from model name. Review the complete potential before approval.",
    })
    text = _section(text, "### PhaseTracer Symmetry Rules", _table(["enabled", "fields", "transformation", "reason", "source_reference"],
                    [["true", ",".join(group), "sign_flip", "Detected or explicit field group; requires full-potential review.", "V0 parity check or explicit choices JSON; see manifest."] for group in policy["symmetry_generators"]]))
    return text + "\n## 12. Deterministic Preparation\n\n" + DEFAULT_RULES + "\n\nSee proof_materials/sarah_preparation.json for inputs, proposals and checks. No inverse fit or model-specific algebra was applied.\n"


def prepare_sarah(*, vin_path, parameter_map_path, run_dir, model_source=None, thermal_metadata_path=None, parameters=None, choices=None, slha_path=None,
                  tree_relations_path=None, source_parameters=None, tadpole_branch=None, tree_loop_policy="unresolved") -> dict:
    root = Path(run_dir).expanduser().resolve()
    contract_path = root / "contract_template.md"
    if contract_path.exists():
        raise SarahImportError("Preparation never overwrites an existing contract. Use a fresh --run-dir.")
    explicit = parameters or {}
    if not isinstance(explicit, dict):
        raise SarahImportError("Parameter overrides must be a JSON object.")
    if source_parameters is not None and not isinstance(source_parameters, dict):
        raise SarahImportError("Source parameter inputs must be a JSON object.")
    unbound = import_sarah_model(vin_path=vin_path, parameter_map_path=parameter_map_path,
                                thermal_metadata_path=thermal_metadata_path)
    radiation_keys = {"num_boson_dof", "num_fermion_dof"}
    known = set(unbound.unresolved_parameters) | set(unbound.parameter_values) | set(unbound.derived_parameters) | set(unbound.block_references) | {"Q", "renormScaleSq"} | radiation_keys
    if set(explicit)-known:
        raise SarahImportError("Unknown parameter names; use the source/SLHA mapping, not model-specific aliases: " + ", ".join(sorted(set(explicit)-known)))
    if "Q" in explicit and "renormScaleSq" in explicit:
        if not isinstance(explicit["Q"], (int, float)) or explicit["Q"]**2 != explicit["renormScaleSq"]:
            raise SarahImportError("Conflicting Q and renormScaleSq inputs.")
    model = import_sarah_model(vin_path=vin_path, parameter_map_path=parameter_map_path,
        thermal_metadata_path=thermal_metadata_path, parameters={k: v for k, v in explicit.items() if k not in radiation_keys},
        slha_path=slha_path)
    tree_proof = None
    tree_review = None
    effective = dict(explicit)
    if tree_relations_path is None and model_source:
        candidate = Path(vin_path).expanduser().resolve().with_name("ptagent_sarah_tree_relations.json")
        if candidate.is_file():
            tree_relations_path = candidate
    if source_parameters is not None or tree_relations_path:
        from .sarah_tree_bindings import load_tree_metadata, resolve_tree_bindings, tree_review_summary
        if not tree_relations_path or not model_source:
            raise SarahImportError("Source bindings require native tree metadata and its archived model-source directory.")
        metadata = load_tree_metadata(tree_relations_path)
        if metadata.get("complete") is True:
            load_tree_metadata(tree_relations_path, Path(model_source) / "SPheno.m")
        provenance = metadata.get("provenance", {})
        for key, path in (("vin_sha256", vin_path), ("parameter_map_sha256", parameter_map_path)):
            if provenance.get(key) != file_hash(Path(path)):
                raise SarahImportError("Tree-relation metadata is stale for " + key)
        source_dir = Path(model_source).expanduser().resolve()
        expected_sources = provenance.get("model_source_sha256")
        observed_sources = {p.name: file_hash(p) for p in sorted(source_dir.iterdir())
                            if p.is_file() and p.suffix in {".m", ".wl"}}
        if not expected_sources or expected_sources != observed_sources:
            raise SarahImportError("Tree-relation metadata is stale for its SARAH model-source files.")
        tree_review = tree_review_summary(metadata, source_parameters or {}, model.parameter_values,
                                          read_spheno_benchmark(model_source), branch=tadpole_branch)
        # Unsupported/multibranch sources still produce an honest partial review.
        binding_available = metadata.get("complete") is True and (
            tadpole_branch is not None or len(metadata.get("tree_solutions", [])) == 1)
        if binding_available or source_parameters is not None or tadpole_branch is not None or tree_loop_policy != "unresolved":
            tree_proof = resolve_tree_bindings(metadata, source_parameters or {}, existing=model.parameter_values,
                                               branch=tadpole_branch, loop_policy=tree_loop_policy)
            derived = {k: v for k, v in tree_proof["bindings"].items() if k in known and k not in radiation_keys}
            effective = {**derived, **explicit}
            model = import_sarah_model(vin_path=vin_path, parameter_map_path=parameter_map_path,
                thermal_metadata_path=thermal_metadata_path,
                parameters={k: v for k, v in effective.items() if k not in radiation_keys}, slha_path=slha_path)
    elif tadpole_branch is not None or tree_loop_policy != "unresolved":
        raise SarahImportError("A tree prescription requires native tree-relation metadata.")
    policy = _policies(model, choices or {})
    proof = _numerical_checks(model, require_tree_stationary=tree_proof is not None)
    inventory = [{"sector": s.name, "kind": s.kind, "eigenvalues": len(s.basis), "dof_per_eigenvalue": s.dof_per_eigenvalue} for s in model.mass_sectors]
    radiation = {key: sum(len(s.basis)*s.dof_per_eigenvalue for s in model.mass_sectors if (s.kind == "fermion") == (key == "num_fermion_dof")) for key in sorted(radiation_keys)}
    radiation.update({k: explicit[k] for k in radiation_keys if k in explicit})
    for value in radiation.values():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise SarahImportError("Radiation counts must be finite nonnegative numbers.")
    field = policy["phase_filter_field"]
    try:
        scale = evaluate_safe_expression(model.dsb_minimum.get(field, "0"), model.parameter_values)
    except (ValueError, KeyError):
        scale = None
    suggestion = phase_filter_suggestion(field, scale)
    if policy["phase_filter"] == "symmetry_orbits":
        suggestion = {**suggestion, "mode": "symmetry_orbits", "threshold": policy["phase_filter_threshold"],
                      "rules": policy["proposed_filter_rows"],
                      "reason": "Compare complete traced trajectories under the reviewed sign-flip group. Do not discard coupled-symmetry phases with pointwise sign cuts."}
    if model_source and not Path(model_source).expanduser().is_dir():
        raise SarahImportError("The explicit SARAH model-source directory does not exist.")
    result = run_sarah_import(vin_path=vin_path, parameter_map_path=parameter_map_path,
        thermal_metadata_path=thermal_metadata_path, parameters={k: v for k, v in effective.items() if k not in radiation_keys},
        slha_path=slha_path, run_dir=root)
    model = result.model
    input_dir, proof_dir = root / "input", root / "proof_materials"
    if tree_relations_path:
        tree_destination = input_dir / "ptagent_sarah_tree_relations.json"
        if Path(tree_relations_path).resolve() != tree_destination.resolve():
            shutil.copy2(tree_relations_path, tree_destination)
        write_json(input_dir / "source_parameters.json", source_parameters or {})
        if tree_proof is not None:
            write_json(proof_dir / "tree_parameter_bindings.json", tree_proof)
    if model_source:
        source_dir = Path(model_source).expanduser().resolve()
        if not source_dir.is_dir():
            raise SarahImportError("The explicit SARAH model-source directory does not exist.")
        archived = input_dir / "sarah_model"
        archived.mkdir(exist_ok=True)
        for path in sorted(source_dir.iterdir()):
            if path.is_file() and path.suffix in {".m", ".wl"} and path.resolve() != (archived/path.name).resolve():
                shutil.copy2(path, archived/path.name)
    write_json(input_dir/"preparation_defaults.json", {"id": DEFAULTS_ID, "rules": DEFAULT_RULES, "policies": POLICY_DEFAULTS})
    overrides = {"parameters": explicit, "choices": choices or {}}
    if source_parameters is not None:
        overrides["source_parameters"] = source_parameters
    if tree_loop_policy != "unresolved":
        overrides["tree_loop_policy"] = tree_loop_policy
    write_json(input_dir/"explicit_overrides.json", overrides)
    benchmark = read_spheno_benchmark(input_dir / "sarah_model" if model_source else None)
    benchmark["missing_inputs"] = model.benchmark_request["missing_inputs"]
    if not model.unresolved_parameters:
        benchmark.update(status="not_needed", message="Preserve the resolved benchmark. Do not replace it with SPheno example values; review any remaining scale or policy choices separately.")
    write_json(proof_dir/"spheno_benchmark.json", benchmark)
    manifest_path = proof_dir/"sarah_preparation.json"
    contract_path.write_text(render_prepared_contract(model, policy, radiation, suggestion, manifest_path), encoding="utf-8")
    checks_path = proof_dir/"sarah_preparation_checks.json"
    write_json(checks_path, proof)
    versions = collect_software_versions()
    manifest = {
        "schema": "ptagent.sarah_preparation.v2", "profile": DEFAULTS_ID,
        "contract_digest": contract_digest(parse_contract(contract_path.read_text())),
        "input_sha256": {str(p.relative_to(root)): file_hash(p) for p in sorted(input_dir.rglob("*")) if p.is_file()},
        "checks_sha256": file_hash(checks_path), "checks_file": str(checks_path), "passed": True,
        "bound_parameters": model.parameter_values, "policies": policy,
        "benchmark_recommendation": benchmark,
        "tree_review": tree_review,
        "tree_binding": {"proof": str(proof_dir / "tree_parameter_bindings.json"),
                         "proof_sha256": file_hash(proof_dir / "tree_parameter_bindings.json"),
                         "source_parameters": source_parameters or {}, "branch": tadpole_branch,
                         "loop_policy": tree_loop_policy} if tree_proof is not None else None,
        "radiation": {"values": radiation, "inventory": inventory, "explicit": sorted(radiation_keys & set(explicit)),
                      "status": "proposal_until_approval", "assumption": "Count the retained source modes only; no unlisted bath species. Review massless-vector polarization conventions and missing decoupled species."},
        "phase_filter_suggestion": suggestion, "explicit_overrides": overrides,
        "software_versions": versions, "adapter_sha256": {
            "sarah_prepare.py": file_hash(Path(__file__)),
            "sarah_review.py": file_hash(Path(__file__).with_name("sarah_review.py")),
        },
        "precedence": "explicit user inputs > SLHA > source parameter map; model-local SPheno example inputs are recommendations only until reviewed and bound; no guessed scale, Yukawa type or tadpole solution",
    }
    write_json(manifest_path, manifest)
    validation = refresh_review(contract_path)
    request = {**model.benchmark_request, "recommendation": benchmark}
    if tree_review:
        request["tree_review"] = tree_review
        request["message"] = (
            "Use tree_review and proof_materials/user_questions.md for one consolidated review. "
            "The raw missing_inputs list contains dependent implementation symbols, not independent user inputs. "
            "Bind accepted source inputs through --source-parameters; do not run SPheno. "
            "Review the tree/loop prescription, remaining SM inputs and Q together; preserve explicit values."
        )
    return {**result.to_dict(), "preparation": str(manifest_path), "checks": str(checks_path), "profile": DEFAULTS_ID,
            "benchmark_request": request, "tree_review": tree_review,
            "contract_sha256": file_hash(contract_path), "approval_required": True,
            "construction_ready": not any(i.severity == "error" and i.code != "review_required" for i in validation.issues),
            "graph": str(proof_dir/"contract_graph/contract_graph.png")}


def preparation_errors(contract: dict) -> list[str]:
    path = contract.get("model_card", {}).get("sarah_preparation_manifest")
    if not path:
        return []
    try:
        manifest_path = Path(path)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        root = manifest_path.parent.parent
        if manifest.get("schema") not in {"ptagent.sarah_preparation.v1", "ptagent.sarah_preparation.v2"} or manifest.get("passed") is not True:
            return ["Preparation has no successful machine-check record."]
        if manifest.get("contract_digest") != contract_digest(contract):
            return ["Prepared contract changed. Re-prepare the changed settings in a fresh task and obtain new approval."]
        for relative, expected in manifest["input_sha256"].items():
            if file_hash(root/relative) != expected:
                return [f"Prepared input changed: {relative}"]
        checks = Path(manifest["checks_file"])
        if file_hash(checks) != manifest["checks_sha256"] or json.loads(checks.read_text()).get("passed") is not True:
            return ["Preparation checks are stale or failed."]
        tree_binding = manifest.get("tree_binding")
        if tree_binding and file_hash(Path(tree_binding["proof"])) != tree_binding.get("proof_sha256"):
            return ["Tree parameter binding proof changed after preparation."]
        if contract.get("review", {}).get("approved"):
            approval = json.loads((manifest_path.parent/"sarah_approval.json").read_text())
            if approval.get("contract_digest") != manifest["contract_digest"] or approval.get("manifest_sha256") != file_hash(manifest_path) or not approval.get("user_approval_quote"):
                return ["Prepared contract has no matching explicit approval record."]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return [f"Cannot verify deterministic preparation: {exc}"]
    return []


def refresh_review(template: Path):
    from ptagent.review_graph import render_graph
    from .sarah_review import review_questions, render_questions
    proof_dir = template.parent / "proof_materials"
    text = template.read_text(encoding="utf-8")
    contract = parse_contract(text)
    backend = contract["model_card"]["compile_backend"]
    validation = validate_contract_template(text, compile_backend=backend)
    manifest = json.loads(Path(contract["model_card"]["sarah_preparation_manifest"]).read_text(encoding="utf-8"))
    questions = review_questions(contract, manifest, validation.to_report().to_dict())
    # These are review outputs, not mutations of the hash-bound compiler input.
    validation.contract["questions"] = questions
    write_resolved_contract_data(validation.contract, proof_dir/"contract_resolved.json")
    write_json(proof_dir/"model_ir.json", contract_to_model_ir(validation.contract, source_path=contract["model_card"]["source_vin_path"]).to_dict())
    write_json(proof_dir/"validation.json", validation.to_report().to_dict())
    (proof_dir/"user_questions.md").write_text(render_questions(contract, manifest, questions), encoding="utf-8")
    spec = preparation_graph(template, contract)
    write_json(proof_dir/"contract_graph_spec.json", spec)
    render_graph(template, spec, proof_dir/"contract_graph")
    return validation


def preparation_graph(template: Path, contract: dict) -> dict:
    from .sarah_review import review_graph
    return review_graph(template, contract)


def approve_sarah(template: Path, expected_sha256: str, user_approval_quote: str) -> dict:
    if not user_approval_quote.strip():
        raise SarahImportError("An exact user approval quote is required; a default is not approval.")
    if file_hash(template) != expected_sha256:
        raise SarahImportError("Contract changed after review. Present the current contract and request approval again.")
    text = template.read_text(encoding="utf-8")
    contract = parse_contract(text)
    if not contract["model_card"].get("sarah_preparation_manifest"):
        raise SarahImportError("approve-sarah is only for a deterministically prepared contract.")
    errors = preparation_errors(contract)
    if errors:
        raise SarahImportError("; ".join(errors))
    validation = validate_contract_template(text, review_required=False, compile_backend=contract["model_card"]["compile_backend"])
    if any(issue.severity == "error" for issue in validation.issues):
        raise SarahImportError("Cannot approve a contract with unresolved validation errors.")
    text = _kv(text, "## 8. Approval", {"approved": "true", "reviewer_notes": "Explicit approval recorded in proof_materials/sarah_approval.json for this prepared contract."})
    manifest_path = Path(contract["model_card"]["sarah_preparation_manifest"])
    write_json(template.parent/"proof_materials/sarah_approval.json", {"contract_digest": contract_digest(contract), "reviewed_template_sha256": expected_sha256, "manifest_sha256": file_hash(manifest_path), "user_approval_quote": user_approval_quote})
    (template.parent/"proof_materials/sarah_approval.txt").write_text(user_approval_quote+"\n", encoding="utf-8")
    template.write_text(text, encoding="utf-8")
    refresh_review(template)
    return {"approved": True, "template": str(template), "template_sha256": file_hash(template)}
