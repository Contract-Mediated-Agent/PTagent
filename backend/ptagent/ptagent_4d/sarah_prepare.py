"""Versioned, deterministic SARAH preparation and hash-bound review approval."""
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

from .sarah_contract import build_sarah_contract, _insert_model_card_rows, _replace_model_card_value, _replace_potential_compiler_expression
from .sarah_import import SarahImportError, import_sarah_model, run_sarah_import
from .sarah_thdm import PROFILE_PATH, audit_source, bind_parameters, check_point, derive_model, file_hash
from .template_contract import contract_to_model_ir, parse_contract, validate_contract_template, write_resolved_contract_data


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


def _policies(profile: dict, choices: dict) -> dict:
    if not isinstance(choices, dict):
        raise SarahImportError("Policy overrides must be a JSON object.")
    policy = dict(profile["policies"])
    if set(choices)-set(policy):
        raise SarahImportError("Unknown THDM choices: " + ", ".join(sorted(set(choices)-set(policy))))
    policy.update(choices)
    allowed = {"backend": {"cosmotransitions", "phasetracer"}, "resummation_scheme": {"none", "Parwani", "Arnold-Espinosa"},
               "counterterm": {"none", "source_polynomial"}, "goldstone": {"included"}, "photon": {"retained"},
               "phase_filter": {"none", "negative_field_threshold"}, "phase_filter_field": {"phi1", "phi2"},
               "phasetracer_symmetry": {"none", "simultaneous"}, "cubic_power_policy": {"positive_part", "signed_abs"}}
    for key, options in allowed.items():
        if not isinstance(policy[key], str) or policy[key] not in options:
            raise SarahImportError(f"Unsupported {key}={policy[key]!r} for this adapter; supported: {sorted(options)}. No substitute was selected.")
    threshold = policy["phase_filter_threshold"]
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not math.isfinite(threshold) or threshold >= 0:
        raise SarahImportError("The phase-filter threshold must be finite and strictly negative.")
    return policy


def render_prepared_contract(model, profile: dict, policy: dict, values: dict, manifest: Path) -> str:
    text = build_sarah_contract(model)
    for key, value in {"counterterm": "custom_expr" if policy["counterterm"] == "source_polynomial" else "none",
                       "resummation_scheme": policy["resummation_scheme"], "daisy": "custom_expr" if policy["resummation_scheme"] == "Arnold-Espinosa" else "none",
                       "goldstone": "included", "photon": "retained",
                       "reviewer_notes": f"Proposed versioned preset {profile['id']}; review all defaults and explicit overrides together. No approval inferred."}.items():
        text = _replace_model_card_value(text, key, value)
    text = _insert_model_card_rows(text, [("sarah_profile_id", profile["id"], "Versioned source-checked configuration, not a fitted physical benchmark."),
                                        ("sarah_preparation_manifest", str(manifest), "Locks construction choices and checks before compilation."),
                                        ("compile_backend", policy["backend"], "Explicit override or fixed preset default.")])
    # This first adapter freezes a point. New scan points go through preparation
    # again so tree-vacuum and Goldstone checks cannot be silently bypassed.
    constants = [[k, k, format(v, ".17g"), "Frozen source/SLHA/explicit binding; see preparation manifest."] for k, v in sorted(model.parameter_values.items()) if k in model.unresolved_parameters or k.startswith("SLHA_") or k in {"M112Tree", "M222Tree", "M112Loop", "M222Loop"}]
    constants += [["renormScaleSq", "Q^2", format(model.renormalization_scale_sq, ".17g"), "Resolved scale squared; no backend fallback."]]
    constants += [[k, k, format(values[k], ".17g"), "Versioned radiation prescription, subject to final approval."] for k in ("num_boson_dof", "num_fermion_dof")]
    constants += [[k, k, str(v), "Fixed numerical derivative step."] for k, v in profile["numerics"].items()]
    params = "### Public Inputs\n\nThis is a frozen numerical point. Supply a new parameter JSON to prepare another point.\n\n"
    params += _table(["name", "latex", "confirmed", "test_value", "source_type", "description"], [])
    params += "\n\n### Fixed Constants\n\n"+_table(["name", "latex", "value", "description"], constants)
    params += "\n\n### Derived Quantities\n\nSymbolic tadpole solutions and their numerical bindings are recorded in sarah_preparation_checks.json.\n\n"+_table(["name", "latex", "expr", "expr_status", "description"], [])
    text = _section(text, "## 3. Parameters", params)
    text = _kv(text, "### Goldstone Handling", {"cw_policy": "included", "ct_derivative_policy": "not_applicable", "thermal_policy": "included", "regulator": "none", "replacement_formula": "0.0", "notes": "Goldstones are retained in complete imported scalar matrices; informational species below are not added twice."})
    sectors = {sector.name: sector for sector in model.mass_sectors}
    rows = []
    for label, sector_name, dof in (("G0", "scalar_2_Ah", 1), ("Gcharged", "scalar_3_Hm", 2)):
        a, b, d = sectors[sector_name].matrix[0][0], sectors[sector_name].matrix[0][1], sectors[sector_name].matrix[1][1]
        expr = f"0.5*(({a})+({d})-np.sqrt((({a})-({d}))**2+4*({b})**2))"
        rows.append(["true", label, expr, "agent_reviewed", dof, "included", "not_applicable", "included", "none", "0.0", "Smaller branch is Goldstone at the checked vacuum; full matrix is authoritative."])
    text = _section(text, "### Goldstone Species Handling", _table(["enabled", "name", "mass_sq", "mass_sq_status", "dof", "cw_policy", "ct_policy", "thermal_policy", "regulator", "replacement_expr", "notes"], rows))
    scheme = policy["resummation_scheme"]
    implementation = {"none": "none", "Parwani": "parwani_thermal_masses", "Arnold-Espinosa": "arnold_espinosa_explicit_daisy"}[scheme]
    text = _kv(text, "### Daisy / Thermal-Mass Handling", {"scheme": scheme, "implementation": implementation, "longitudinal_vectors_only": "true", "scalar_thermal_masses": "Complete scalar matrices M0^2+Pi(T) in Section 5.", "gauge_thermal_masses": "Complete longitudinal gauge matrices M0^2+Pi(T) in Section 5; transverse modes unresummed.", "double_counting_guard": "Parwani replaces loop masses without a ring term; AE keeps ordinary loop masses and adds exactly one matrix ring subtraction.", "cubic_power_policy": policy["cubic_power_policy"]})
    text = _replace_potential_compiler_expression(text, "V_daisy", "0.0", "The deterministic compiler owns the complete matrix ring subtraction for Arnold-Espinosa; this placeholder is not an additional term.")
    text = _kv(text, "### CosmoTransitions Phase Filtering", {"mode": policy["phase_filter"], "custom_expr": "0.0", "notes": "Versioned preset convention for simultaneous reflection only; preserve the origin. Subject to approval."})
    filter_rows = [["true", policy["phase_filter_field"], "<", policy["phase_filter_threshold"], "mirror_branch", "Simultaneous sign reflection checked for all matrices and thermal terms.", profile["id"]]] if policy["phase_filter"] != "none" else []
    text = _section(text, "### CosmoTransitions Phase Filter Rules", _table(["enabled", "field", "comparison", "threshold", "phase_type", "reason", "source_reference"], filter_rows))
    text = _kv(text, "### PhaseTracer Symmetry", {"mode": "z2_reflection" if policy["phasetracer_symmetry"] == "simultaneous" else "none", "notes": "Only simultaneous (phi1,phi2) reversal; not independent reflections. Subject to approval."})
    symmetry_rows = [["true", "phi1,phi2", "sign_flip", "All source matrices and thermal corrections preserve the joint reflection.", profile["id"]]] if policy["phasetracer_symmetry"] == "simultaneous" else []
    text = _section(text, "### PhaseTracer Symmetry Rules", _table(["enabled", "fields", "transformation", "reason", "source_reference"], symmetry_rows))
    text += "\n## 11. Deterministic Preparation\n\n"+profile["scope"]+"\n\n"
    text += "This contract uses fixed defaults unless explicitly overridden. They are not user-confirmed until final approval. See `proof_materials/sarah_preparation.json` for input precedence, source hashes, chosen parameters, policies and software versions. Local tree checks do not prove a global or loop-corrected vacuum. No physical-spectrum inverse fit or automatic scan was performed.\n"
    return text


def prepare_sarah(*, vin_path, parameter_map_path, model_source, run_dir, parameters=None, choices=None, slha_path=None) -> dict:
    root = Path(run_dir).expanduser().resolve()
    contract_path = root / "contract_template.md"
    if contract_path.exists():
        raise SarahImportError("Preparation never overwrites an existing contract. Use a fresh --run-dir; the approved/current task remains unchanged.")
    source_dir = Path(model_source).expanduser().resolve()
    profile, defaults = audit_source(source_dir)
    policy = _policies(profile, choices or {})
    model = import_sarah_model(vin_path=vin_path, parameter_map_path=parameter_map_path)
    thermal, proof = derive_model(model, source_dir)
    external = {"renormScaleSq": model.renormalization_scale_sq} if model.renormalization_scale_sq is not None else {}
    if slha_path:
        slha_model = import_sarah_model(vin_path=vin_path, parameter_map_path=parameter_map_path, slha_path=slha_path)
        external.update({name: value for name, value in slha_model.parameter_values.items()
                         if name in model.unresolved_parameters})
        if slha_model.renormalization_scale_sq is not None:
            external["renormScaleSq"] = slha_model.renormalization_scale_sq
    explicit = parameters or {}
    if not isinstance(explicit, dict):
        raise SarahImportError("Parameter overrides must be a JSON object.")
    if "Q" in explicit and "renormScaleSq" in explicit:
        if not isinstance(explicit["Q"], (int, float)) or explicit["Q"]**2 != explicit["renormScaleSq"]:
            raise SarahImportError("Conflicting Q and renormScaleSq inputs.")
    # Normalize user aliases before merging, so explicit choices beat SLHA too.
    aliases = {**{f"Lambda{i}": f"SLHA_HMIX_{30+i}" for i in range(1, 6)}, "M12": "SLHA_HMIX_22", "g1": "SLHA_GAUGE_1", "g2": "SLHA_GAUGE_2"}
    for name, native in aliases.items():
        if name in explicit:
            if native in explicit and explicit[name] != explicit[native]:
                raise SarahImportError(f"Conflicting aliases: {name} and {native}.")
            external[native] = explicit[name]
    if "Q" in explicit:
        external.pop("renormScaleSq", None)
    if "vSM" in explicit or "tanBeta" in explicit:
        v = explicit.get("vSM", profile["parameters"]["vSM"])
        tb = explicit.get("tanBeta", defaults["tanBeta"])
        if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or x <= 0 for x in (v, tb)):
            raise SarahImportError("vSM and tanBeta must be positive finite numbers.")
        vevs = {"SLHA_HMIX_102": v/math.sqrt(1+tb**2), "SLHA_HMIX_103": v*tb/math.sqrt(1+tb**2)}
        for name, value in vevs.items():
            if name in explicit and not math.isclose(explicit[name], value, rel_tol=1e-12):
                raise SarahImportError("Conflicting reference VEV and vSM/tanBeta inputs.")
            external[name] = value
    for group, names in (("YU", ("u", "c", "t")), ("YD", ("d", "s", "b")), ("YE", ("e", "mu", "tau"))):
        for index, name in enumerate(names, 1):
            if f"m_{name}" in explicit:
                native = f"SLHA_{group}_{index}_{index}"
                if native in explicit:
                    raise SarahImportError(f"Choose a fermion mass or its native Yukawa input, not both: {name}.")
                external.pop(native, None)
    external.update(explicit)
    bound, values = bind_parameters(model, profile, defaults, external, proof)
    if policy["counterterm"] == "none" and any(bound[f"M{n}Loop"] != bound[f"M{n}Tree"] for n in ("112", "222")):
        raise SarahImportError("Explicit loop/tree values imply a nonzero source polynomial. Choose counterterm=source_polynomial or supply consistent values; no input was discarded.")
    if any(values[k] < 0 for k in ("num_boson_dof", "num_fermion_dof")):
        raise SarahImportError("Radiation degrees of freedom must be nonnegative.")
    proof = check_point(model, bound, proof)
    input_dir, proof_dir = root / "input", root / "proof_materials"
    input_dir.mkdir(parents=True, exist_ok=True)
    proof_dir.mkdir(parents=True, exist_ok=True)
    archived_source = input_dir / "sarah_model"
    archived_source.mkdir(exist_ok=True)
    for name in profile["source_signatures"]:
        if (source_dir / name).resolve() != (archived_source / name).resolve():
            shutil.copy2(source_dir / name, archived_source / name)
    shutil.copy2(PROFILE_PATH, input_dir / PROFILE_PATH.name)
    thermal_path = input_dir / "ptagent_thermal_metadata_completed.json"
    write_json(thermal_path, thermal)
    write_json(input_dir / "explicit_overrides.json", {"parameters": explicit, "choices": choices or {}})
    result = run_sarah_import(vin_path=vin_path, parameter_map_path=parameter_map_path, thermal_metadata_path=thermal_path, parameters=bound, slha_path=slha_path, run_dir=root)
    if not result.model.thermal_ready or not result.model.numerical_ready:
        raise SarahImportError("Deterministic preparation did not resolve all thermal/numerical blockers.")
    manifest_path = proof_dir / "sarah_preparation.json"
    text = render_prepared_contract(result.model, profile, policy, values, manifest_path)
    contract_path.write_text(text, encoding="utf-8")
    checks_path = proof_dir / "sarah_preparation_checks.json"
    write_json(checks_path, proof)
    versions = {"python": platform.python_version()}
    for package in ("numpy", "sympy", "scipy", "cosmoTransitions"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "not_installed"
    source_files = [p for p in input_dir.rglob("*") if p.is_file()]
    manifest = {"schema": "ptagent.sarah_preparation.v1", "profile": profile["id"], "profile_sha256": file_hash(PROFILE_PATH),
                "contract_digest": contract_digest(parse_contract(text)), "input_sha256": {str(p.relative_to(root)): file_hash(p) for p in source_files},
                "checks_sha256": file_hash(checks_path), "checks_file": str(checks_path), "passed": True,
                "source_defaults": defaults, "preset_inputs_with_alias_overrides": values, "bound_parameters": bound, "policies": policy,
                "explicit_overrides": {"parameters": explicit, "choices": choices or {}}, "software_versions": versions,
                "adapter_sha256": {name: file_hash(Path(__file__).with_name(name)) for name in ("sarah_thdm.py", "sarah_prepare.py")},
                "precedence": "explicit user inputs > SLHA bindings/scale > audited source example > versioned preset; existing contracts are never overwritten"}
    write_json(manifest_path, manifest)
    refresh_review(contract_path)
    return {**result.to_dict(), "preparation": str(manifest_path), "checks": str(checks_path), "profile": profile["id"], "contract_sha256": file_hash(contract_path), "approval_required": True, "graph": str(proof_dir / "contract_graph/contract_graph.png")}


def preparation_errors(contract: dict) -> list[str]:
    path = contract.get("model_card", {}).get("sarah_preparation_manifest")
    if not path:
        return []
    try:
        manifest_path = Path(path)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        root = manifest_path.parent.parent
        if manifest.get("schema") != "ptagent.sarah_preparation.v1" or manifest.get("passed") is not True:
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


def refresh_review(template: Path) -> None:
    from ptagent.review_graph import render_graph
    proof_dir = template.parent / "proof_materials"
    text = template.read_text(encoding="utf-8")
    contract = parse_contract(text)
    backend = contract["model_card"]["compile_backend"]
    validation = validate_contract_template(text, compile_backend=backend)
    write_resolved_contract_data(validation.contract, proof_dir/"contract_resolved.json")
    write_json(proof_dir/"model_ir.json", contract_to_model_ir(validation.contract, source_path=contract["model_card"]["source_vin_path"]).to_dict())
    write_json(proof_dir/"validation.json", validation.to_report().to_dict())
    errors = [issue.message for issue in validation.issues if issue.severity == "error" and issue.code != "review_required"]
    if errors:
        raise SarahImportError("Prepared contract validation failed: " + "; ".join(errors))
    approved = bool(contract["review"]["approved"])
    (proof_dir/"user_questions.md").write_text("# Review\n\n"+("Approved: no pending questions.\n" if approved else "No unresolved construction choices. Review the versioned defaults, explicit overrides and checks together, then approve the exact contract hash. Defaults are not source-mandated physics choices.\n"), encoding="utf-8")
    spec = preparation_graph(template, contract)
    write_json(proof_dir/"contract_graph_spec.json", spec)
    render_graph(template, spec, proof_dir/"contract_graph")


def preparation_graph(template: Path, contract: dict) -> dict:
    root = template.parent
    def evidence(file, location):
        return [{"file": str(root/file), "location": location}]
    def item(name, label, kind, refs, file, calculation=None):
        row = {"id": name, "label": label, "kind": kind, "refs": refs, "evidence": evidence(file, name)}
        if calculation:
            row["calculation"] = calculation
        return row
    source = item("source", "SARAH", "source", [], "input/sarah_model/THDM.m")
    profile = item("preset", "Preset defaults", "proposal", [], f"input/{PROFILE_PATH.name}")
    source.pop("refs"); profile.pop("refs")
    policy_kind = "proposal"
    policy_evidence = "input/"+PROFILE_PATH.name
    approval_quote = None
    if contract["review"]["approved"]:
        approval = json.loads((root/"proof_materials/sarah_approval.json").read_text())
        approval_quote = approval["user_approval_quote"]
        profile.update(label="User approval", kind="user")
        policy_kind, policy_evidence = "user", "proof_materials/sarah_approval.txt"
    policies = [item("numeric_defaults", "Numerical point, scale and radiation", policy_kind, ["/parameters"], policy_evidence),
                item("policies", "Resummation, modes and phase handling", policy_kind, ["/implementation", "/loops", "/model_card/compile_backend"], policy_evidence)]
    if approval_quote:
        for row in policies:
            row["user_quote"] = approval_quote
    return {"version": 1, "template_sha256": file_hash(template), "sources": [source, profile], "branches": [
        {"source": "source", "operation": "Model algebra", "inputs": [item("tree", "Tree potential and field definitions", "source", ["/potential/V0", "/fields/0/name", "/fields/1/name"], "input/"+Path(contract["model_card"]["source_vin_path"]).name)], "outputs": [item("point", "Tadpoles and reference vacuum", "derived", ["/fields"], "proof_materials/sarah_preparation_checks.json", "Symbolic differentiation, unique quadratic solution and frozen bindings")]},
        {"source": "source", "operation": "Thermal matrices", "inputs": [item("spectrum", "Source matrices and full multiplets", "source", ["/masses/fermion_matrices"], "input/"+Path(contract["model_card"]["source_vin_path"]).name)], "outputs": [item("thermal", "Scalar and longitudinal self energies", "derived", ["/masses/boson_matrices"], "proof_materials/sarah_preparation_checks.json", "Full-multiplet tensor contraction and independent high-T supertrace")]},
        {"source": "preset", "inputs": policies, "outputs": []}], "links": [["numeric_defaults", "point"]]}


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
