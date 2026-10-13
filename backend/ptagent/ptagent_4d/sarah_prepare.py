"""Model-independent deterministic SARAH preparation and hash-bound approval."""
from __future__ import annotations

import copy
import hashlib
import importlib.metadata
import json
import math
import platform
import re
import shutil
from pathlib import Path
from typing import Any

from .model_defaults import DEFAULTS_ID, DEFAULT_RULES, POLICY_DEFAULTS, phase_filter_suggestion
from .safe_expression import evaluate_safe_expression, parse_safe_expression
from .sarah_contract import build_sarah_contract, _insert_model_card_rows, _replace_model_card_value, _replace_potential_compiler_expression
from .sarah_import import SarahImportError, import_sarah_model, run_sarah_import
from .template_contract import contract_to_model_ir, parse_contract, validate_contract_template, write_resolved_contract_data


def file_hash(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False, ensure_ascii=False) + "\n", encoding="utf-8")


def contract_digest(contract: dict) -> str:
    """Only approval state may change without repeating deterministic preparation."""
    payload = copy.deepcopy(contract)
    for key in ("review", "status", "source_hash"):
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
    allowed = {
        "backend": {"cosmotransitions", "phasetracer"},
        "resummation_scheme": {"none", "Parwani", "Arnold-Espinosa"},
        "counterterm": {"none", "source_polynomial"},
        "goldstone": {"included"}, "photon": {"retained"},
        "phase_filter": {"none", "negative_field_threshold"},
        "phasetracer_symmetry": {"none", "z2_reflection"},
        "cubic_power_policy": {"positive_part", "signed_abs"},
    }
    for key, options in allowed.items():
        if not isinstance(policy[key], str) or policy[key] not in options:
            raise SarahImportError(f"Unsupported {key}={policy[key]!r}; supported: {sorted(options)}. Selective species removal needs an explicit reviewed contract, not a model-name shortcut.")
    if policy["phase_filter_field"] not in model.fields:
        raise SarahImportError("The phase-filter field must be in the imported background basis.")
    threshold = policy["phase_filter_threshold"]
    if threshold is not None and (isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not math.isfinite(threshold) or threshold >= 0):
        raise SarahImportError("The phase-filter threshold must be finite and strictly negative.")
    if policy["phase_filter"] != "none" and threshold is None:
        raise SarahImportError("Choose an explicit negative threshold after reviewing the scale-based suggestion; no filter was inferred.")
    if policy["phase_filter"] == "none" and threshold is not None:
        raise SarahImportError("A threshold was supplied with phase_filter=none; resolve this conflict explicitly.")
    groups = policy["symmetry_generators"]
    if not isinstance(groups, list) or any(not isinstance(group, list) or not group or any(not isinstance(f, str) or f not in model.fields for f in group) or len(set(group)) != len(group) for group in groups):
        raise SarahImportError("symmetry_generators must be a list of nonempty field-name lists.")
    if bool(groups) != (policy["phasetracer_symmetry"] == "z2_reflection"):
        raise SarahImportError("Explicit symmetry generators are required exactly when z2_reflection is selected.")
    return policy


def _numerical_checks(model) -> dict:
    """Check the imported point; do not solve or replace the source parameters."""
    proof = {"passed": True, "scope": "Verified import and finite point evaluation; not a proof of vacuum stability or physical suitability.",
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
    proof.update(reference_point=vacuum, tree_tadpoles=tadpoles, mass_squared_eigenvalues=spectra)
    return proof


def render_prepared_contract(model, policy: dict, radiation: dict, suggestion: dict, manifest: Path) -> str:
    text = build_sarah_contract(model)
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
        "notes": "No phase discarded by default. " + suggestion["reason"],
    })
    rows = [[str(policy["phase_filter"] != "none").lower(), policy["phase_filter_field"], "<",
             policy["phase_filter_threshold"] if policy["phase_filter"] != "none" else suggestion["threshold"],
             "negative_branch", "Review full-potential symmetry before filtering.", "Explicit choice or scale-based proposal."]]
    text = _section(text, "### CosmoTransitions Phase Filter Rules",
                    _table(["enabled", "field", "comparison", "threshold", "phase_type", "reason", "source_reference"], rows))
    text = _kv(text, "### PhaseTracer Symmetry", {
        "mode": policy["phasetracer_symmetry"], "notes": "Only explicitly listed generators; no equivalence inferred from model name. Review the full potential.",
    })
    text = _section(text, "### PhaseTracer Symmetry Rules", _table(["enabled", "fields", "transformation", "reason", "source_reference"],
                    [["true", ",".join(group), "sign_flip", "Explicit field group; requires full-potential review.", "Explicit choices JSON."] for group in policy["symmetry_generators"]]))
    return text + "\n## 11. Deterministic Preparation\n\n" + DEFAULT_RULES + "\n\nSee proof_materials/sarah_preparation.json for inputs, proposals and checks. No inverse fit or model-specific algebra was applied.\n"


def prepare_sarah(*, vin_path, parameter_map_path, run_dir, model_source=None, thermal_metadata_path=None, parameters=None, choices=None, slha_path=None) -> dict:
    root = Path(run_dir).expanduser().resolve()
    contract_path = root / "contract_template.md"
    if contract_path.exists():
        raise SarahImportError("Preparation never overwrites an existing contract. Use a fresh --run-dir.")
    explicit = parameters or {}
    if not isinstance(explicit, dict):
        raise SarahImportError("Parameter overrides must be a JSON object.")
    unbound = import_sarah_model(vin_path=vin_path, parameter_map_path=parameter_map_path)
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
    policy = _policies(model, choices or {})
    proof = _numerical_checks(model)
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
    if model_source and not Path(model_source).expanduser().is_dir():
        raise SarahImportError("The explicit SARAH model-source directory does not exist.")
    result = run_sarah_import(vin_path=vin_path, parameter_map_path=parameter_map_path,
        thermal_metadata_path=thermal_metadata_path, parameters={k: v for k, v in explicit.items() if k not in radiation_keys},
        slha_path=slha_path, run_dir=root)
    model = result.model
    input_dir, proof_dir = root / "input", root / "proof_materials"
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
    write_json(input_dir/"explicit_overrides.json", {"parameters": explicit, "choices": choices or {}})
    manifest_path = proof_dir/"sarah_preparation.json"
    contract_path.write_text(render_prepared_contract(model, policy, radiation, suggestion, manifest_path), encoding="utf-8")
    checks_path = proof_dir/"sarah_preparation_checks.json"
    write_json(checks_path, proof)
    versions = {"python": platform.python_version()}
    for package in ("numpy", "sympy", "scipy", "cosmoTransitions"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "not_installed"
    manifest = {
        "schema": "ptagent.sarah_preparation.v2", "profile": DEFAULTS_ID,
        "contract_digest": contract_digest(parse_contract(contract_path.read_text())),
        "input_sha256": {str(p.relative_to(root)): file_hash(p) for p in sorted(input_dir.rglob("*")) if p.is_file()},
        "checks_sha256": file_hash(checks_path), "checks_file": str(checks_path), "passed": True,
        "bound_parameters": model.parameter_values, "policies": policy,
        "radiation": {"values": radiation, "inventory": inventory, "explicit": sorted(radiation_keys & set(explicit)),
                      "status": "proposal_until_approval", "assumption": "Count the retained source modes only; no unlisted bath species. Review massless-vector polarization conventions and missing decoupled species."},
        "phase_filter_suggestion": suggestion, "explicit_overrides": {"parameters": explicit, "choices": choices or {}},
        "software_versions": versions, "adapter_sha256": {"sarah_prepare.py": file_hash(Path(__file__))},
        "precedence": "explicit user inputs > SLHA > source parameter map; no guessed benchmark, scale, Yukawa type or tadpole solution",
    }
    write_json(manifest_path, manifest)
    validation = refresh_review(contract_path)
    return {**result.to_dict(), "preparation": str(manifest_path), "checks": str(checks_path), "profile": DEFAULTS_ID,
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
        if contract.get("review", {}).get("approved"):
            approval = json.loads((manifest_path.parent/"sarah_approval.json").read_text())
            if approval.get("contract_digest") != manifest["contract_digest"] or approval.get("manifest_sha256") != file_hash(manifest_path) or not approval.get("user_approval_quote"):
                return ["Prepared contract has no matching explicit approval record."]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return [f"Cannot verify deterministic preparation: {exc}"]
    return []


def refresh_review(template: Path):
    from ptagent.review_graph import render_graph
    from .user_guidance import build_user_guidance_markdown
    proof_dir = template.parent / "proof_materials"
    text = template.read_text(encoding="utf-8")
    contract = parse_contract(text)
    backend = contract["model_card"]["compile_backend"]
    validation = validate_contract_template(text, compile_backend=backend)
    write_resolved_contract_data(validation.contract, proof_dir/"contract_resolved.json")
    write_json(proof_dir/"model_ir.json", contract_to_model_ir(validation.contract, source_path=contract["model_card"]["source_vin_path"]).to_dict())
    write_json(proof_dir/"validation.json", validation.to_report().to_dict())
    (proof_dir/"user_questions.md").write_text(build_user_guidance_markdown(
        template_markdown=text, validation=validation.to_report(), compile_backend=backend), encoding="utf-8")
    spec = preparation_graph(template, contract)
    write_json(proof_dir/"contract_graph_spec.json", spec)
    render_graph(template, spec, proof_dir/"contract_graph")
    return validation


def preparation_graph(template: Path, contract: dict) -> dict:
    root = template.parent
    source_file = contract["model_card"]["source_vin_path"]
    def item(name, label, kind, refs, file, calculation=None):
        row = {"id": name, "label": label, "kind": kind, "refs": refs,
               "evidence": [{"file": str(file), "location": name}]}
        if calculation:
            row["calculation"] = calculation
        return row
    source = item("source", "SARAH export", "source", [], source_file)
    default_file = root/"input/preparation_defaults.json"
    if not default_file.exists():
        default_file = Path(contract["model_card"]["sarah_preparation_manifest"])
    proposal = item("defaults", "Review proposals", "proposal", [], default_file)
    source.pop("refs"); proposal.pop("refs")
    policies = [item("policies", "Spectrum, radiation and physical prescriptions", "proposal",
                     ["/parameters", "/implementation", "/loops", "/model_card/compile_backend"], root/"proof_materials/sarah_preparation.json")]
    if contract["review"]["approved"]:
        approval = json.loads((root/"proof_materials/sarah_approval.json").read_text())
        proposal.update(label="User approval", kind="user")
        for row in policies:
            row.update(kind="user", user_quote=approval["user_approval_quote"],
                       evidence=[{"file": str(root/"proof_materials/sarah_approval.txt"), "location": "approval"}])
    branches = [
        {"source": "source", "inputs": [
            item("tree", "Tree potential and background fields", "source", ["/potential/V0", "/fields"], source_file),
            item("spectrum", "Imported scalar, gauge and fermion matrices", "source", ["/masses"], source_file)], "outputs": []},
        {"source": "defaults", "inputs": policies, "outputs": []},
    ]
    thermal_file = root/"proof_materials/thermal_derivation.json"
    thermal_hash = contract["model_card"].get("source_thermal_metadata_sha256")
    metadata_file = next((p for p in sorted((root/"input").rglob("*.json")) if file_hash(p) == thermal_hash), None)
    if str(contract["model_card"].get("thermal_ready")).lower() == "true" and thermal_file.exists() and metadata_file:
        branches.insert(1, {"source": "source", "operation": "Thermal matrices", "inputs": [
            item("metadata", "Companion invariants and basis mappings", "source", ["/model_card/source_thermal_metadata_sha256"], metadata_file)],
            "outputs": [item("thermal", "Scalar and longitudinal self energies", "derived", ["/masses/boson_matrices"],
                thermal_file, "Model-independent one-loop tensor contractions and alignment checks")]})
    return {"version": 1, "template_sha256": file_hash(template), "sources": [source, proposal], "branches": branches, "links": []}


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
