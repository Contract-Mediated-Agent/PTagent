from __future__ import annotations

import ast
import hashlib
import json
import keyword
import re
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .artifact_layout import generated_models_dir, model_artifact_slug
from .compiler import CompileBlocked, CompileResult, compile_usage_instructions
from .config import Settings
from .latex_display import clean_latex_evidence_for_markdown, render_latex_evidence_block
from .model_preflight import model_focus_terms, selected_model_from_memory
from .schemas import (
    DaisyInfo,
    FieldDefinition,
    FormulaRecord,
    ModelIR,
    ParameterDefinition,
    PaperMemory,
    PotentialPieces,
    ValidationIssue,
    ValidationReport,
    ZeroTempLoopInfo,
)


CONTRACT_SCHEMA = "ptagent.contract.template.v1"
CONTRACT_MARKER = "<!-- PTAGENT template_contract: contract_v1 -->"
PLACEHOLDER_VALUES = {
    "",
    "?",
    "ASK_USER",
    "AI infer",
    "unclear",
    "unknown",
    "None?",
    "review_required",
    "needs_review",
    "unreviewed",
    "ambiguous",
    "conflict",
    "TBD",
    "TODO",
}
_DENY_PARAMETER_SYMBOLS = {
    "V",
    "V0",
    "V_0",
    "V1",
    "V_1",
    "V1T",
    "V_1T",
    "Veff",
    "V_eff",
    "VCW",
    "V_CW",
    "VCT",
    "V_CT",
    "VT",
    "V_T",
    "Vdaisy",
    "V_daisy",
    "M",
    "M2",
    "M_2",
    "MS",
    "M_S",
    "MV",
    "M_V",
    "MF",
    "M_F",
    "Phi",
    "phi",
    "T",
    "formula_id",
    "source_id",
    "source_ids",
    "pymupdf_blocks",
    "paper_md",
    "pdf2md",
}

ZERO_LOOP_MODES = {"none", "standard_CW_V1", "paper_os_like_V1", "custom_expr"}
THERMAL_MODES = {"none", "standard_thermal_integrals", "custom_expr"}
DAISY_MODES = {"none", "custom_expr"}
COUNTERTERM_MODES = {"none", "implicit_in_V1", "explicit_linear_system", "custom_expr"}
RESUMMATION_SCHEMES = {"none", "Parwani", "Arnold-Espinosa", "custom_expr"}
PHASE_FILTER_MODES = {"none", "negative_field_threshold", "custom_expr"}
PHASETRACER_SYMMETRY_MODES = {"none", "z2_reflection"}
THERMAL_MASS_IMPLEMENTATIONS = {
    "none",
    "not_applicable",
    "parwani_thermal_masses",
    "arnold_espinosa_explicit_daisy",
    "custom_expr",
}
DAISY_NON_FORMULA_MARKERS = {
    "same_as_zeroT",
    "not_applicable",
    "covered_by_unified_V_daisy",
    "covered_by_custom_V_daisy",
    "parwani_replacement_only",
}
THERMAL_BASIS_MIXING_REVIEW_MARKERS = (
    "final eigenvalue",
    "final eigenvalues",
    "source-given eigenvalue",
    "source-given eigenvalues",
    "paper-given eigenvalue",
    "paper-given eigenvalues",
    "paper gives the eigenvalue",
    "paper gives the eigenvalues",
    "already diagonalized",
    "already diagonalised",
    "reviewed final eigenvalue",
    "reviewed final eigenvalues",
)
PARTIAL_THERMAL_SELF_ENERGY_MARKERS = (
    "delta pi",
    "deltapi",
    "additional contribution",
    "additional self energy",
    "extra contribution",
    "extra scalar contribution",
    "extra doublet contribution",
    "incremental contribution",
)
TOTAL_THERMAL_MASS_MARKERS = (
    "total",
    "full",
    "complete",
    "added to",
    "adding them together",
    "sum of",
    "baseline",
    "sm plus",
)
REVIEW_STATUS_CHOICES = {"needs_review", "agent_reviewed", "human_modified"}
REVIEW_COMPILE_READY_STATUS = "agent_reviewed"
REQUIRED_MODEL_CARD_KEYS = (
    "model_short_name",
    "counterterm",
    "resummation_scheme",
    "goldstone",
    "photon",
    "gauge",
)
MISSING_MODEL_CARD_VALUES = {"", "ASK_USER", "unclear", "unknown", "review_required"}

_NP_FUNCTIONS = {
    "abs",
    "arccos",
    "arcsin",
    "arctan",
    "cos",
    "cosh",
    "exp",
    "log",
    "maximum",
    "minimum",
    "logical_and",
    "logical_or",
    "any",
    "all",
    "pi",
    "power",
    "sign",
    "sin",
    "sinh",
    "sqrt",
    "tan",
    "tanh",
    "where",
}
_BARE_FUNCTIONS = {"abs", "max", "min", "pow"}
_GREEK_LATEX_WORDS = {
    "alpha",
    "beta",
    "gamma",
    "delta",
    "epsilon",
    "varepsilon",
    "zeta",
    "eta",
    "theta",
    "vartheta",
    "iota",
    "kappa",
    "lambda",
    "mu",
    "nu",
    "xi",
    "pi",
    "rho",
    "sigma",
    "tau",
    "upsilon",
    "phi",
    "varphi",
    "chi",
    "psi",
    "omega",
}


@dataclass
class ContractValidation:
    contract: dict[str, Any]
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)

    def to_report(self) -> ValidationReport:
        return ValidationReport(
            ready_for_compile=self.ok,
            ready_for_runtime=self.ok,
            issues=self.issues,
            missing_core_fields=sorted(
                {
                    issue.field_key
                    for issue in self.issues
                    if issue.severity == "error" and issue.code in {"missing_value", "placeholder_value"}
                }
            ),
            conflict_fields=sorted(
                {
                    issue.field_key
                    for issue in self.issues
                    if issue.severity == "error" and issue.code not in {"missing_value", "placeholder_value"}
                }
            ),
            closure_table=[],
        )


def is_contract_template(markdown_text: str) -> bool:
    return CONTRACT_MARKER in markdown_text


def build_contract_template(memory: PaperMemory) -> str:
    """Build the human-editable Markdown contract worksheet."""

    focus_terms = model_focus_terms(memory)
    formulas = {
        "V0": _best_formula(memory.formula_registry, "tree_potential", focus_terms=focus_terms),
        "Veff": _best_formula(memory.formula_registry, "effective_potential", focus_terms=focus_terms),
        "VCW": _best_formula(memory.formula_registry, "zero_temp_loop", focus_terms=focus_terms),
        "VCT": "",
        "scalar_mass": _best_formula(memory.formula_registry, "mass", focus_terms=focus_terms),
        "thermal": _best_formula(memory.formula_registry, "thermal", focus_terms=focus_terms),
        "daisy": _best_formula(memory.formula_registry, "daisy", focus_terms=focus_terms),
    }
    fields = _candidate_fields(memory, tree_potential_latex=formulas["V0"])
    parameters = _candidate_parameters(memory, tree_potential_latex=formulas["V0"])
    selected_model = selected_model_from_memory(memory)
    public_inputs = _public_input_candidates(parameters)
    constants = [
        {
            "name": "vh",
            "latex": "v_h",
            "value": 246.0,
            "description": "Default electroweak vev; change if the paper uses a different convention.",
        },
        {
            "name": "num_boson_dof",
            "latex": "n_b^{radiation}",
            "value": "ASK_USER",
            "description": "Ask the user for the total bosonic radiation d.o.f. used by standard thermal integrals. Start from SM baseline 28, add reviewed BSM d.o.f. beyond the one SM Higgs scalar already in the baseline, and report the count.",
        },
        {
            "name": "num_fermion_dof",
            "latex": "n_f^{radiation}",
            "value": "ASK_USER",
            "description": "Ask the user for the total fermionic radiation d.o.f. SM baseline is 90 unless the paper uses a nonstandard fermion content.",
        }
    ]
    direct_bosons = [
        {
            "enabled": "false",
            "name": "direct_mass_candidate",
            "kind": "scalar_or_vector",
            "mass_sq": "ASK_USER",
            "mass_sq_status": "needs_review",
            "dof": "ASK_USER",
            "c": 1.5,
            "usage": "zeroT_CW, finiteT_V1T",
            "thermal_policy": "none",
            "vacuum_anchor": "review_required",
            "source_latex": "see section 10: scalar_mass",
            "notes": "Disabled by default. Enable only when the paper gives reviewed diagonal field-dependent eigenvalues; otherwise fill the matrix route below.",
        }
    ]
    direct_fermions = [
        {
            "enabled": "false",
            "name": "top",
            "kind": "fermion",
            "mass_sq": "ASK_USER",
            "mass_sq_status": "needs_review",
            "dof": 12,
            "usage": "zeroT_CW, finiteT_V1T",
            "thermal_policy": "none",
            "vacuum_anchor": "none",
            "source_latex": "",
            "notes": "Set enabled=true only if this fermion is part of the reviewed potential.",
        }
    ]
    v0_python, v0_notes = _auto_v0_compiler_expression(formulas["V0"], fields)
    matrix_basis = [str(row.get("name", "")) for row in fields[:2]] or ["phi"]
    matrix_rows, matrix_verified = _auto_scalar_matrix_rows(
        v0_python,
        matrix_basis,
        scalar_mass_latex=formulas["scalar_mass"],
    )
    matrix_entry_status = "agent_reviewed" if matrix_verified else "needs_review"
    matrix_dof = "1" if matrix_verified else "ASK_USER"
    matrix_notes = (
        "Auto-derived from Hessian(V0) and machine-checked against source mass-spectrum formula(s). "
        "If the paper has another convention or later user review disagrees, set entries back to human_modified and re-resolve."
        if matrix_verified
        else (
            "Enabled by default because mixed scalar sectors must use matrix eigenvalues. "
            "Codex/PTagent may derive a Hessian candidate from V0 for discussion, but must not mark it reviewed or compile it until it is checked against the paper mass spectrum and user-confirmed when inconsistent."
        )
    )
    goldstone_defaults = _auto_goldstone_defaults(formulas)
    return _render_human_contract_template(
        paper_id=memory.paper_id,
        fields=fields,
        public_inputs=public_inputs,
        constants=constants,
        formulas=formulas,
        v0_python=v0_python,
        v0_notes=v0_notes,
        direct_bosons=direct_bosons,
        direct_fermions=direct_fermions,
        matrix_basis=matrix_basis,
        matrix_rows=matrix_rows,
        matrix_entry_status=matrix_entry_status,
        matrix_dof=matrix_dof,
        matrix_notes=matrix_notes,
        goldstone_defaults=goldstone_defaults,
        model_short_name=selected_model.short_name if selected_model else "ASK_USER",
    )


def _render_human_contract_template(
    *,
    paper_id: str,
    fields: list[dict[str, Any]],
    public_inputs: list[dict[str, Any]],
    constants: list[dict[str, Any]],
    formulas: dict[str, str],
    v0_python: str,
    v0_notes: str,
    direct_bosons: list[dict[str, Any]],
    direct_fermions: list[dict[str, Any]],
    matrix_basis: list[str],
    matrix_rows: list[list[str]],
    matrix_entry_status: str = "needs_review",
    matrix_dof: str = "ASK_USER",
    matrix_notes: str = "Enabled by default because mixed scalar sectors must use matrix eigenvalues. Ask the user if the paper instead gives direct eigenvalues.",
    goldstone_defaults: dict[str, str] | None = None,
    model_short_name: str = "ASK_USER",
) -> str:
    phase_filter_example_field = str(fields[0].get("name", "h")) if fields else "h"
    goldstone_defaults = goldstone_defaults or {}
    return "\n".join(
        [
            CONTRACT_MARKER,
            f"<!-- PTAGENT paper_id: {paper_id} -->",
            "<!-- PTAGENT workflow_mode: fresh -->",
            "<!-- PTAGENT source_of_truth: current input material plus current user answers -->",
            "",
            f"# PTagent Model Contract: {paper_id}",
            "",
            "Edit this Markdown file only. PTagent rebuilds `contract_resolved.json` and generated backend code from this file every time.",
            "Do not edit files under `proof_materials/` or `generated_models/`; they are derived artifacts.",
            "",
            "## 0. How To Use This File",
            "",
            "Mode note: this template is created in `fresh` mode. If a later generated program is wrong, switch to `continue` mode by editing this same Markdown file and resolving/compiling from it; do not re-extract the paper unless you want a new independent run.",
            "",
            "1. If no compile backend has been specified, stop and ask the user to choose exactly one supported backend: `cosmotransitions` or `phasetracer`. Any other backend is not supported yet.",
            "2. Ask one fused question packet at a time. State how many packets and underlying backend-specific blockers remain, explain the current issue in plain language, and give Codex/PTagent's current leaning as reviewable guidance.",
            "3. Resolve the public-input gate from source/user/proof evidence: record the final public input list and one numeric `test_value` for every public input. Ask the user only when candidate inputs or test values cannot be safely determined.",
            "4. Answer backend-specific phase handling: this is a mandatory user-confirmed gate. `cosmotransitions` uses phase filtering / `forbidPhaseCrit`; recommended default is `mode=none` unless the paper/reference code explicitly removes a traced phase branch. `phasetracer` uses `apply_symmetry(phi)` and `get_symmetry_axes()` to identify reviewed symmetry-equivalent field points; recommended default is `mode=none` unless the user confirms a Z2/sign-flip equivalence. The agent must present its recommendation and reason before patching these rows.",
            "5. Fill the remaining `ASK_USER` cells and review the physics choices below.",
            "6. When the reviewed model is complete, stop and ask the user to review this rendered Markdown. Set `approved` to `true` only after explicit user approval to generate/compile backend code.",
            "7. Run `python -m ptagent compile --template contract_template.md --backend cosmotransitions` or `python -m ptagent compile --template contract_template.md --backend phasetracer`; this regenerates `proof_materials/contract_resolved.json` first.",
            "8. Keep headings and table columns fixed. Add rows where needed, but do not rename sections or columns.",
            "",
            "Mandatory rule: do not compile from this template until the current run has explicit answers for public inputs, backend-specific phase handling, compile backend, and approval. Do not fill uncertain cells from old artifacts or memory.",
            "",
            "## 1. Model Card",
            "",
            _render_table_with_notes(
                ["key", "value", "notes"],
                [
                    {"key": "model_short_name", "value": model_short_name or "ASK_USER", "notes": "Reviewed HEP model acronym/short name used for generated artifact names, e.g. XSM, 2HDM, IDM. Use `model` only when no such name exists."},
                    {"key": "workflow_mode", "value": "fresh", "notes": "Choices: fresh or continue. Fresh ignores old artifacts; continue means this Markdown is the source of truth for local repairs."},
                    {"key": "source_of_truth", "value": "current_input_plus_user_answers", "notes": "In continue mode, set this to existing_contract_template.md."},
                    {"key": "zero_temperature", "value": "none", "notes": "Choices: none, standard_CW_V1, paper_os_like_V1, custom_expr."},
                    {"key": "counterterm", "value": "ASK_USER", "notes": "Choices: none, implicit_in_V1, explicit_linear_system, custom_expr. Explicit systems must fill Section 6."},
                    {"key": "thermal", "value": "none", "notes": "Choices: none, standard_thermal_integrals, custom_expr."},
                    {"key": "daisy", "value": "none", "notes": "Choices: none, custom_expr. Use custom_expr only for an explicit Arnold-Espinosa Daisy term; Parwani uses no separate V_daisy."},
                    {"key": "resummation_scheme", "value": "ASK_USER", "notes": "Choices: none, Parwani, Arnold-Espinosa, custom_expr."},
                    {"key": "gauge", "value": "ASK_USER", "notes": "Example: Landau, Feynman, unitary, or not_applicable."},
                    {"key": "species_policy", "value": "explicit_include_exclude_tables", "notes": "Included species appear in Direct Bosons/Fermions; omitted species appear in Excluded Species."},
                    {"key": "mass_formula_policy", "value": "field_dependent_required", "notes": "Vacuum physical-mass relations may be used only for parameter solving or vacuum checks."},
                    {"key": "goldstone", "value": "ASK_USER", "notes": "Example: excluded, included, or model-specific prescription."},
                    {"key": "photon", "value": "ASK_USER", "notes": "Example: removed, retained, or only Z-like neutral mode retained."},
                    {"key": "reviewer_notes", "value": "Fill every ASK_USER field, then approve.", "notes": "Free-form notes."},
                ],
                id_key="key",
            ),
            "",
            "## 2. Fields",
            "",
            _render_table(["name", "latex", "role", "zeroT_default", "description"], fields),
            "",
            "## 3. Parameters",
            "",
            "### Public Inputs",
            "",
            "Confirm this table first. `confirmed` must be `true`, and `test_value` is the numeric benchmark used by `build_model` and smoke tests.",
            "Remove rows that are not public scan/build inputs; move fixed conventions to Fixed Constants and solved quantities to Derived Quantities.",
            "",
            _render_table(["name", "latex", "confirmed", "test_value", "source_type", "description"], public_inputs),
            "",
            "### Fixed Constants",
            "",
            _render_table(["name", "latex", "value", "description"], constants),
            "",
            "### Derived Quantities",
            "",
            "Keep simple one-line `expr` values in the table. Add a named code block only when a derived expression needs multiple lines.",
            "Status choices: `needs_review`, `agent_reviewed`, `human_modified`. Compilation requires `agent_reviewed`.",
            "Compiler blocks may use simple assignments and may end with either a final expression or a final assignment.",
            "",
            _render_table(["name", "latex", "expr", "expr_status", "description"], []),
            "",
            "## 4. Potential",
            "",
            "Long formulas are displayed outside tables so the Markdown preview remains readable.",
            "The `Compiler expression` blocks are deterministic Python blocks that PTagent/Codex may maintain for you.",
            "For loop/counterterm pieces, `0.0` can mean the block is not a custom-expression source; the actual implementation owner should be stated in Notes and in the Model Card route.",
            "Use simple assignments to name repeated pieces; the final line may be an expression or an assignment whose target is the compiled value.",
            "",
            _render_human_potential_part("V0", formulas["V0"], v0_python, v0_notes),
            "",
            _render_human_potential_part("V_CW", formulas["VCW"], "0.0", "Zero-temperature one-loop contribution if used."),
            "",
            _render_human_potential_part("V_CT", formulas["VCT"], "0.0", "Counterterm contribution if given separately."),
            "",
            _render_human_potential_part("V_thermal", formulas["thermal"], "0.0", "Finite-temperature one-loop or high-temperature correction."),
            "",
            _render_human_potential_part("V_daisy", formulas["daisy"], "0.0", "Explicit Arnold-Espinosa Daisy contribution. Leave 0.0 for none or Parwani."),
            "",
            "### Full Effective Potential Reference",
            "",
            "Evidence only. PTagent does not compile this block directly.",
            "",
            render_latex_evidence_block(formulas["Veff"]),
            "",
            "## 5. Mass Spectrum",
            "",
            "Only enabled rows enter the generated model. Prefer source-exact field-dependent masses; do not use vacuum-only relations as field-dependent masses.",
            "",
            "### Direct Bosons",
            "",
            "Keep short `mass_sq` expressions in the table. Use `Direct Boson mass_sq: name` code blocks only for long or multi-line expressions; code blocks override table cells.",
            "Direct rows are only for source-reviewed diagonal masses or source-given final eigenvalues. If the source gives a field-basis matrix, mixing matrix, or basis thermal self-energy/Debye correction, keep that structure in `Boson Mass Matrices` instead of compressing it into a named particle row.",
            "Status choices: `needs_review`, `agent_reviewed`, `human_modified`. Compilation requires `agent_reviewed`.",
            "",
            _render_table_with_notes(["enabled", "name", "kind", "mass_sq", "mass_sq_status", "dof", "c", "usage", "thermal_policy", "vacuum_anchor", "notes"], direct_bosons),
            "",
            "### Direct Fermions",
            "",
            "Keep short `mass_sq` expressions in the table. Use `Direct Fermion mass_sq: name` code blocks only for long or multi-line expressions; code blocks override table cells.",
            "Status choices: `needs_review`, `agent_reviewed`, `human_modified`. Compilation requires `agent_reviewed`.",
            "",
            _render_table_with_notes(["enabled", "name", "kind", "mass_sq", "mass_sq_status", "dof", "usage", "thermal_policy", "vacuum_anchor", "notes"], direct_fermions),
            "",
            "### Excluded Species",
            "",
            "Use this table to record reviewed omissions such as Goldstones, photons, light fermions, or paper-specific modes. These rows are not compiled.",
            "",
            _render_table(["name", "kind", "excluded_from", "reason", "source_latex"], []),
            "",
            "### Boson Mass Matrices",
            "",
            "Default route: fill and review a field-dependent matrix, then let PTagent diagonalize it. Matrix entries live in named code blocks so long expressions do not clutter the table. Each entry heading must include both the matrix name and the component, for example `Matrix entry: scalar_matrix[h1,h2]`. Disable this matrix only when the paper gives already-reviewed diagonal field-dependent eigenvalues in Direct Bosons.",
            "This matrix route is model-independent and also applies to vector/gauge sectors. If the source gives neutral gauge-basis information such as W3/B, gamma/Z, photon/Z, Debye matrices, longitudinal/transverse mixing, or thermal self-energies in a non-mass basis, add a `kind = vector` or `kind = gauge` matrix here unless the source explicitly gives the final thermal eigenvalues.",
            "",
            "#### Matrix: scalar_matrix",
            "",
            "| key | value |",
            "|---|---|",
            "| enabled | true |",
            "| kind | scalar |",
            f"| basis | {', '.join(matrix_basis)} |",
            f"| dof_per_eigenvalue | {matrix_dof} |",
            "| c | 1.5 |",
            "| source_role | field_dependent |",
            f"| notes | {matrix_notes} |",
            "",
            "Matrix entries:",
            "",
            _render_matrix_entry_metadata_table(matrix_basis, status=matrix_entry_status),
            "",
            _render_matrix_entry_blocks("scalar_matrix", matrix_basis, matrix_rows),
            "",
            "## 6. Counterterm, Goldstone, And Daisy Contracts",
            "",
            "These tables record implementation choices that often differ across finite-temperature phase-transition papers.",
            "Leave `ASK_USER` when the paper does not clearly specify the route; compilation will block.",
            "",
            "### Counterterm Basis",
            "",
            "Fill only when `counterterm = explicit_linear_system` or a standalone reviewed V_CT is present.",
            "",
            "Reviewed algebra:",
            "",
            "$$",
            r"V_{\mathrm{CT}}(\phi)=\sum_i c_i O_i(\phi),\quad A_{ai}=L_a[O_i](\phi_a),\quad b_a=t_a-L_a[V_{\mathrm{CW}}^{\mathrm{source}}](\phi_a),\quad A c=b.",
            "$$",
            "",
            "`operator_expr` defines each basis operator `O_i`. Each Counterterm Condition row defines one linear operator `L_a`, the point `phi_a`, target `t_a`, and the CW source used for `b_a`.",
            "",
            "Short `operator_expr` entries may live in the table. Use `Counterterm operator_expr: coefficient` code blocks for multi-line operators.",
            "",
            _render_table(["enabled", "coefficient", "operator_expr", "operator_expr_status", "notes"], []),
            "",
            "### Counterterm Conditions",
            "",
            "Each condition represents one linear operator L_a applied at a reviewed point, with b_a = -L_a[V_CW] when solving CT coefficients.",
            "",
            "Short `target_expr` entries may live in the table. Use `Counterterm target_expr: name` code blocks for multi-line targets.",
            "",
            _render_table(["enabled", "name", "operator", "point", "target_expr", "target_expr_status", "cw_source", "goldstone_handling", "notes"], []),
            "",
            "### Goldstone Handling",
            "",
            _render_table_with_notes(
                ["key", "value", "notes"],
                [
                    {"key": "cw_policy", "value": goldstone_defaults.get("cw_policy", "ASK_USER"), "notes": "Choices: included, excluded, regulated, not_applicable."},
                    {"key": "ct_derivative_policy", "value": goldstone_defaults.get("ct_derivative_policy", "ASK_USER"), "notes": "Choices: same_as_CW, non_goldstone_CW_source, regulated_replacement, not_applicable."},
                    {"key": "thermal_policy", "value": goldstone_defaults.get("thermal_policy", "ASK_USER"), "notes": "Choices: included, excluded, not_applicable."},
                    {"key": "regulator", "value": goldstone_defaults.get("regulator", "ASK_USER"), "notes": "Example: none, IR_cutoff, on_shell, paper_formula."},
                    {"key": "replacement_formula", "value": "0.0", "notes": "Compiler expression added only when a reviewed regulated replacement is required. Use a code block only for multi-line formulas."},
                    {"key": "notes", "value": goldstone_defaults.get("notes", "ASK_USER"), "notes": "Source-specific explanation."},
                ],
                id_key="key",
            ),
            "",
            "### Goldstone Species Handling",
            "",
            "Fill one row per Goldstone mode or mode group whenever Goldstones exist. Keep short `replacement_expr` values in the table; put long `mass_sq` or replacement expressions in named code blocks below the table.",
            "Status choices: `needs_review`, `agent_reviewed`, `human_modified`. Compilation requires `agent_reviewed`.",
            "",
            _render_table(["enabled", "name", "mass_sq", "mass_sq_status", "dof", "cw_policy", "ct_policy", "thermal_policy", "regulator", "replacement_expr", "notes"], []),
            "",
            "### Daisy / Thermal-Mass Handling",
            "",
            _render_table_with_notes(
                ["key", "value", "notes"],
                [
                    {"key": "scheme", "value": "ASK_USER", "notes": "Choices: none, Parwani, Arnold-Espinosa, custom_expr."},
                    {"key": "implementation", "value": "ASK_USER", "notes": "Choices: none, parwani_thermal_masses, arnold_espinosa_explicit_daisy, custom_expr."},
                    {"key": "longitudinal_vectors_only", "value": "ASK_USER", "notes": "For Arnold-Espinosa gauge-boson Daisy masses, decide whether only longitudinal modes get Debye masses."},
                    {"key": "scalar_thermal_masses", "value": "see code block below", "notes": "Describe full scalar thermal masses or point to derived parameters/species rows."},
                    {"key": "gauge_thermal_masses", "value": "see code block below", "notes": "Describe full gauge-boson Debye matrices/eigenvalues and photon/Z policy; do not record only Delta Pi/additional pieces."},
                    {"key": "double_counting_guard", "value": "ASK_USER", "notes": "Parwani uses thermal masses throughout finite-T one-loop pieces and no V_daisy; Arnold-Espinosa uses ordinary masses plus one explicit V_daisy subtraction."},
                    {"key": "cubic_power_policy", "value": "ASK_USER", "notes": "Required for Arnold-Espinosa if V_daisy contains (m^2)^(3/2), e.g. positive_part, signed_abs, regulated_abs, or paper_formula."},
                ],
                id_key="key",
            ),
            "",
            "scalar_thermal_masses:",
            "",
            "```python",
            "ASK_USER",
            "```",
            "",
            "scalar_thermal_masses_derivation_notes:",
            "",
            "```text",
            "State whether the source gives the final total scalar thermal masses directly or whether this block assembles zero-temperature matrices plus baseline/SM self-energies plus BSM increments. Record branch-sensitive details such as Yukawa type, tan(beta) factors, neglected off-diagonal self-energies, and source equation/prose references.",
            "```",
            "",
            "gauge_thermal_masses:",
            "",
            "```python",
            "ASK_USER",
            "```",
            "",
            "gauge_thermal_masses_derivation_notes:",
            "",
            "```text",
            "State whether the source gives the final total gauge Debye masses/eigenvalues directly or whether this block assembles baseline/SM self-energies plus extra-field increments. Record longitudinal/transverse policy, neutral mixing basis, photon/Z branch convention, and source equation/prose references.",
            "```",
            "",
            "### Daisy Particle Terms",
            "",
            "Fill one row per bosonic mode or mode group affected by the reviewed Daisy route.",
            "",
            "- Parwani: `thermal_mass_sq` is the mass used in the one-loop finite-temperature route; `daisy_term` may be `not_applicable`.",
            "- Arnold-Espinosa: `zeroT_mass_sq` enters ordinary `V1/V1T`, `thermal_mass_sq` enters the zero-Matsubara subtraction, and `daisy_term` should match the reviewed contribution.",
            "- `thermal_mass_sq` must be the total resummed mass squared/eigenvalue used by the paper. If the source gives `Delta Pi`, an additional contribution, or an extra-field increment, assemble it with the baseline thermal self-energy and mass matrix before marking the row `agent_reviewed`.",
            "- Use `not_applicable` only when the field truly has no meaning for that row. Use `covered_by_unified_V_daisy` when the mode is included through the reviewed `V_daisy` block instead of row-by-row generation.",
            "- Keep short `zeroT_mass_sq`, `thermal_mass_sq`, and `daisy_term` expressions in the table; use named code blocks below only for long or multi-line expressions.",
            "- Status choices: `needs_review`, `agent_reviewed`, `human_modified`. Compilation requires `agent_reviewed`.",
            "",
            _render_table(["enabled", "name", "kind", "implementation_owner", "zeroT_mass_sq", "zeroT_mass_sq_status", "thermal_mass_sq", "thermal_mass_sq_status", "dof", "longitudinal_only", "daisy_term", "daisy_term_status", "notes"], []),
            "",
            "### CosmoTransitions Phase Filtering",
            "",
            "This section is used only by the CosmoTransitions backend. The phase-filter hook discards a traced phase when it returns true and renders as `forbidPhaseCrit(X)`. PhaseTracer does not consume this section; PhaseTracer symmetry double-counting is handled separately with `apply_symmetry(phi)` below.",
            "",
            _render_table_with_notes(
                ["key", "value", "notes"],
                [
                    {"key": "mode", "value": "ASK_USER", "notes": "Mandatory user-confirmed choice. Choices: none, negative_field_threshold, custom_expr. Recommendation: none unless the paper/reference code explicitly removes a duplicate or unphysical traced branch."},
                    {"key": "custom_expr", "value": "0.0", "notes": "Only used when mode=custom_expr; Python boolean expression using fields/parameters, e.g. (h < -5.0) or (s < -5.0). Python and/or are compiled to NumPy-safe elementwise logic. For PhaseTracer symmetry equivalence, use the PhaseTracer Symmetry section instead."},
                    {"key": "notes", "value": "ASK_USER", "notes": "State the recommendation and reason. If mode=none, explain that no CosmoTransitions branch is forbidden. If filtering is enabled, state what phase type or hard field region is being removed and why. For PhaseTracer Z2 equivalence, set this to none and use PhaseTracer Symmetry."},
                ],
                id_key="key",
            ),
            "",
            "### CosmoTransitions Phase Filter Rules",
            "",
            "Use rows when `mode=negative_field_threshold`. This compiles to CosmoTransitions `forbidPhaseCrit`, for example a test like `field < -5.0`. Keep the tolerance away from zero so phases located at zero are not accidentally removed by floating-point noise. For PhaseTracer symmetry-equivalent branches, use `apply_symmetry(phi)` in the next section instead of a threshold filter.",
            "",
            _render_table(
                ["enabled", "field", "comparison", "threshold", "phase_type", "reason", "source_reference"],
                [
                    {
                        "enabled": "false",
                        "field": phase_filter_example_field,
                        "comparison": "<",
                        "threshold": "-5.0",
                        "phase_type": "negative_branch",
                        "reason": "Example only: CosmoTransitions xSM-style negative branch filter.",
                        "source_reference": "xSM-style phase-filter tolerance.",
                    }
                ],
            ),
            "",
            "### PhaseTracer Symmetry",
            "",
            "PhaseTracer uses `apply_symmetry(phi)` to identify symmetry-equivalent field points so they are not counted as distinct phases. This does not create new physical vacua. It is different from phase filtering: symmetry records reviewed equivalence relations, while filtering forbids selected branches. This section is used only by the PhaseTracer backend.",
            "",
            _render_table_with_notes(
                ["key", "value", "notes"],
                [
                    {"key": "mode", "value": "ASK_USER", "notes": "Mandatory user-confirmed choice. Choices: none, z2_reflection. Recommendation: none unless the user confirms that PhaseTracer should merge reviewed field-reflection-equivalent points."},
                    {"key": "notes", "value": "ASK_USER", "notes": "State the recommendation and reason. If mode=none, explain that no apply_symmetry equivalence is applied. If enabled, state the reviewed symmetry, e.g. xSM-like h -> -h and/or s -> -s."},
                ],
                id_key="key",
            ),
            "",
            "### PhaseTracer Symmetry Rules",
            "",
            "Use rows when `mode=z2_reflection`. Each row lists one simultaneous field-sign reflection. Example: `s` means `s -> -s`; `h,s` means `(h,s) -> (-h,-s)` as one combined symmetry operation.",
            "",
            _render_table(
                ["enabled", "fields", "transformation", "reason", "source_reference"],
                [
                    {
                        "enabled": "false",
                        "fields": phase_filter_example_field,
                        "transformation": "sign_flip",
                        "reason": "Example only: Z2 reflection equivalence for PhaseTracer apply_symmetry.",
                        "source_reference": "PhaseTracer apply_symmetry convention.",
                    }
                ],
            ),
            "",
            "## 7. Generated Files",
            "",
            "| file | role | edit? |",
            "|---|---|---|",
            "| input/ | archived source material for this run | no |",
            "| contract_template.md | human source of truth | yes |",
            "| proof_materials/contract_resolved.json | regenerated machine contract | no |",
            "| proof_materials/model_ir.json | regenerated compiler view | no |",
            "| proof_materials/validation.json | regenerated validation report | no |",
            "| generated_models/cosmotransitions/*_model.py or model.py | regenerated CosmoTransitions model | no |",
            "",
            "## 8. Approval",
            "",
            "| key | value |",
            "|---|---|",
            "| approved | false |",
            "| reviewer_notes | Fill every ASK_USER field, ask the user to review the rendered Markdown, then set approved=true only after explicit approval to generate code. |",
            "",
            "## 9. Final Code Cleanup Prompt",
            "",
            "Use this as the last pass before returning any generated model program, especially when a less capable coding model edits the file:",
            "",
            "```text",
            "Clean the generated backend code without changing the reviewed physics contract.",
            "Keep only imports, attributes, helper methods, local variables, and metadata that are actually used.",
            "Use standard backend method names: CosmoTransitions uses V0, V1, V1T_from_X, Vtot, boson_massSq, fermion_massSq, approxZeroTMin, forbidPhaseCrit, and build_model; PhaseTracer uses V0, V1, V1T, V, get_*_masses_sq, get_*_dofs, get_raddof, apply_symmetry, and get_symmetry_axes.",
            "Remove paper-specific aliases, stale comments, unused temporary variables, duplicate formulas, unreachable branches, and obsolete generated-code leftovers.",
            "Do not rename public inputs, fields, or reviewed contract symbols.",
            "Do not replace reviewed field-dependent matrices with vacuum mass relations.",
            "Do not add, remove, or guess Goldstone, photon, Daisy, counterterm, phase-filter, or PhaseTracer symmetry conventions.",
            "After cleanup, run backend smoke checks and confirm the public input signature still matches the contract. CosmoTransitions smoke must exercise forbidPhaseCrit on single-point and batched X inputs; PhaseTracer smoke must exercise generated apply_symmetry equivalence outputs when symmetry is enabled.",
            "```",
            "",
            "## 10. Commands",
            "",
            "```powershell",
            "python -m ptagent guide --template path\\to\\contract_template.md",
            "python -m ptagent validate --template path\\to\\contract_template.md",
            "python -m ptagent compile --template path\\to\\contract_template.md --backend cosmotransitions",
            "python -m ptagent compile --template path\\to\\contract_template.md --backend phasetracer",
            "```",
        ]
    )


def _render_human_potential_part(name: str, source_latex: str, python_expr: str, notes: str) -> str:
    return "\n".join(
        [
            f"### Potential Part: {name}",
            "",
            "Source LaTeX:",
            "",
            render_latex_evidence_block(source_latex),
            "",
            "Reviewed formula:",
            "",
            "```text",
            clean_latex_evidence_for_markdown(source_latex) or "ASK_USER",
            "```",
            "",
            "Compiler expression:",
            "",
            "```python",
            str(python_expr),
            "```",
            "",
            f"Notes: {notes}",
        ]
    )


def parse_contract(markdown_text: str) -> dict[str, Any]:
    return _parse_markdown_contract(markdown_text)


def _parse_markdown_contract(markdown_text: str) -> dict[str, Any]:
    if CONTRACT_MARKER not in markdown_text:
        raise ValueError("No PTAGENT contract_v1 Markdown contract marker was found.")
    paper_id = _metadata_value(markdown_text, "paper_id") or _heading_suffix(
        markdown_text, "PTagent Model Contract:"
    )
    paper_id = paper_id or "uploaded_model"
    model_card = _key_value_table(_section(markdown_text, "1. Model Card"))
    fields = _parse_rows_by_section(markdown_text, "2. Fields")
    parameter_section = _section(markdown_text, "3. Parameters")
    public_inputs = _parse_rows_by_subsection(parameter_section, "Public Inputs")
    constants = _parse_rows_by_subsection(parameter_section, "Fixed Constants")
    derived_section = _subsection(parameter_section, "Derived Quantities")
    derived = _attach_named_expression_blocks(
        _parse_first_table(derived_section),
        derived_section,
        "Derived Expression",
        "expr",
        allow_table_cell=True,
    )
    potential_section = _section(markdown_text, "4. Potential")
    potential_parts = {
        name: _parse_potential_part(potential_section, name)
        for name in ("V0", "V_CW", "V_CT", "V_thermal", "V_daisy")
    }
    model_short_name = str(model_card.get("model_short_name", "")).strip()
    model_name = model_short_name if model_short_name and not _unresolved_contract_choice(model_short_name) else "model"
    loops = _loops_from_model_card(model_card, potential_parts)
    mass_section = _section(markdown_text, "5. Mass Spectrum")
    direct_boson_section = _subsection(mass_section, "Direct Bosons")
    direct_fermion_section = _subsection(mass_section, "Direct Fermions")
    direct_boson_rows = _attach_named_expression_blocks(
        _expand_note_refs(_parse_first_table(direct_boson_section), direct_boson_section),
        direct_boson_section,
        "Direct Boson mass_sq",
        "mass_sq",
        allow_table_cell=True,
    )
    direct_fermion_rows = _attach_named_expression_blocks(
        _expand_note_refs(_parse_first_table(direct_fermion_section), direct_fermion_section),
        direct_fermion_section,
        "Direct Fermion mass_sq",
        "mass_sq",
        allow_table_cell=True,
    )
    bosons = [
        _enabled_species_row(row, require_c=True)
        for row in direct_boson_rows
        if _enabled(row)
    ]
    fermions = [
        _enabled_species_row(row, require_c=False)
        for row in direct_fermion_rows
        if _enabled(row)
    ]
    matrix_section = _subsection(mass_section, "Boson Mass Matrices")
    boson_matrices = _parse_boson_matrices(matrix_section)
    excluded_species = _parse_rows_by_subsection(mass_section, "Excluded Species")
    implementation_section = _section(markdown_text, "6. Counterterm, Goldstone, And Daisy Contracts")
    implementation = _parse_implementation_contracts(implementation_section, model_card)
    approval = _key_value_table(_section(markdown_text, "8. Approval"))
    return {
        "schema": CONTRACT_SCHEMA,
        "paper_id": paper_id,
        "model_name": model_name,
        "model_short_name": model_short_name,
        "model_card": model_card,
        "status": "reviewed" if _truthy(approval.get("approved", "")) else "needs_user_input",
        "review": {
            "approved": _truthy(approval.get("approved", "")),
            "notes": approval.get("reviewer_notes", ""),
        },
        "fields": [_clean_field(row) for row in fields if str(row.get("name", "")).strip()],
        "parameters": {
            "public_inputs": [_clean_public_input(row) for row in public_inputs if str(row.get("name", "")).strip()],
            "constants": [_clean_constant(row) for row in constants if str(row.get("name", "")).strip()],
            "derived": [_clean_derived(row) for row in derived if str(row.get("name", "")).strip()],
        },
        "potential": {
            "V0": {
                "python": potential_parts["V0"].get("python", "ASK_USER"),
                "source_latex": potential_parts["V0"].get("source_latex", ""),
                "notes": potential_parts["V0"].get("notes", ""),
            },
            "pieces": potential_parts,
        },
        "masses": {
            "bosons": bosons,
            "fermions": fermions,
            "boson_matrices": boson_matrices,
            "excluded_species": [_clean_excluded_species(row) for row in excluded_species if str(row.get("name", "")).strip()],
        },
        "loops": loops,
        "implementation": implementation,
        "questions": _default_questions({}),
    }


def _metadata_value(markdown_text: str, key: str) -> str:
    match = re.search(rf"<!--\s*PTAGENT\s+{re.escape(key)}:\s*(.*?)\s*-->", markdown_text)
    return match.group(1).strip() if match else ""


def _heading_suffix(markdown_text: str, prefix: str) -> str:
    for line in markdown_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# ") and prefix in stripped:
            return stripped.split(prefix, 1)[1].strip()
    return ""


def _section(markdown_text: str, title: str) -> str:
    pattern = re.compile(rf"^##\s+{re.escape(title)}\s*$", re.MULTILINE)
    match = pattern.search(markdown_text)
    if not match:
        return ""
    next_match = re.search(r"^##\s+", markdown_text[match.end() :], flags=re.MULTILINE)
    end = match.end() + next_match.start() if next_match else len(markdown_text)
    return markdown_text[match.end() : end].strip()


def _subsection(section_text: str, title: str) -> str:
    pattern = re.compile(rf"^###\s+{re.escape(title)}\s*$", re.MULTILINE)
    match = pattern.search(section_text)
    if not match:
        return ""
    next_match = re.search(r"^###\s+", section_text[match.end() :], flags=re.MULTILINE)
    end = match.end() + next_match.start() if next_match else len(section_text)
    return section_text[match.end() : end].strip()


def _parse_rows_by_section(markdown_text: str, title: str) -> list[dict[str, str]]:
    return _parse_first_table(_section(markdown_text, title))


def _parse_rows_by_subsection(section_text: str, title: str) -> list[dict[str, str]]:
    return _parse_first_table(_subsection(section_text, title))


def _attach_named_expression_blocks(
    rows: list[dict[str, str]],
    section_text: str,
    heading_prefix: str,
    key: str,
    *,
    allow_table_cell: bool = False,
    name_key: str = "name",
) -> list[dict[str, str]]:
    expressions = _named_expression_block_map(section_text, heading_prefix, key)
    updated: list[dict[str, str]] = []
    for row in rows:
        name = str(row.get(name_key, "")).strip()
        row = dict(row)
        row[key] = expressions.get(name, row.get(key, "") if allow_table_cell else "")
        updated.append(row)
    return updated


def _expand_note_refs(
    rows: list[dict[str, str]],
    section_text: str,
    *,
    note_key: str = "notes",
) -> list[dict[str, str]]:
    note_map = _note_ref_map(section_text)
    if not note_map:
        return rows
    expanded: list[dict[str, str]] = []
    for row in rows:
        row = dict(row)
        note_ref = str(row.get(note_key, "")).strip()
        if note_ref in note_map:
            row[note_key] = note_map[note_ref]
        expanded.append(row)
    return expanded


def _note_ref_map(section_text: str) -> dict[str, str]:
    notes: dict[str, str] = {}
    for match in re.finditer(r"^\s*-\s*(N\d+)\s*(?:\([^)]*\))?:\s*(.*?)\s*$", section_text, flags=re.MULTILINE):
        key = match.group(1).strip()
        text = match.group(2).strip()
        if key and text:
            notes[key] = text
    return notes


def _named_expression_block_map(section_text: str, heading_prefix: str, key: str) -> dict[str, str]:
    pattern = re.compile(rf"^#{{4,6}}\s+{re.escape(heading_prefix)}:\s*(.*?)\s*$", flags=re.MULTILINE)
    matches = list(pattern.finditer(section_text))
    expressions: dict[str, str] = {}
    for index, match in enumerate(matches):
        name = match.group(1).strip()
        if not name:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(section_text)
        block = section_text[match.end() : end].strip()
        expr = _code_block_after_label(block, key, default="")
        if not expr:
            expr = _first_code_block(block)
        if expr:
            expressions[name] = expr
    return expressions


def _parse_first_table(markdown_text: str) -> list[dict[str, str]]:
    tables = _parse_tables(markdown_text)
    return tables[0][1] if tables else []


def _key_value_table(markdown_text: str) -> dict[str, str]:
    rows = _parse_first_table(markdown_text)
    result: dict[str, str] = {}
    for row in rows:
        key = str(row.get("key", "")).strip()
        if key:
            result[key] = str(row.get("value", "")).strip()
    return result


def _parse_tables(markdown_text: str) -> list[tuple[list[str], list[dict[str, str]]]]:
    lines = markdown_text.splitlines()
    tables: list[tuple[list[str], list[dict[str, str]]]] = []
    index = 0
    while index < len(lines):
        if not _is_table_line(lines[index]):
            index += 1
            continue
        block: list[str] = []
        while index < len(lines) and _is_table_line(lines[index]):
            block.append(lines[index])
            index += 1
        if len(block) >= 2:
            headers = _table_cells(block[0])
            rows: list[dict[str, str]] = []
            for line in block[2:]:
                cells = _table_cells(line)
                if not cells or all(not cell for cell in cells):
                    continue
                row = {header: cells[pos] if pos < len(cells) else "" for pos, header in enumerate(headers)}
                rows.append(row)
            tables.append((headers, rows))
        else:
            index += 1
    return tables


def _is_table_line(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") and stripped.endswith("|")


def _table_cells(line: str) -> list[str]:
    return [_strip_table_display_wrappers(cell.strip().replace("\\|", "|")) for cell in line.strip().strip("|").split("|")]


def _strip_table_display_wrappers(cell: str) -> str:
    stripped = cell.strip()
    if _is_single_inline_math_span(stripped):
        return stripped[1:-1].strip()
    if stripped.startswith(r"\(") and stripped.endswith(r"\)"):
        return stripped[2:-2].strip()
    if _is_markdown_wrapped_code(stripped):
        return _strip_markdown_code_span(stripped)
    return stripped


def _is_single_inline_math_span(text: str) -> bool:
    if len(text) < 2 or not text.startswith("$") or not text.endswith("$") or text.startswith("$$"):
        return False
    return "$" not in text[1:-1]


def _parse_potential_part(section_text: str, name: str) -> dict[str, str]:
    block = _subsection(section_text, f"Potential Part: {name}")
    return {
        "name": name,
        "source_latex": _latex_evidence_after_label(block, "Source LaTeX", default=""),
        "python": _code_block_after_label(block, "Compiler expression", default="ASK_USER" if name == "V0" else "0.0"),
        "notes": _line_after_label(block, "Notes"),
    }


def _code_block_after_label(markdown_text: str, label: str, *, default: str = "") -> str:
    pattern = re.compile(
        rf"{re.escape(label)}:\s*\n+```(?:[A-Za-z0-9_-]+)?\s*\n(.*?)\n```",
        flags=re.DOTALL,
    )
    match = pattern.search(markdown_text)
    return match.group(1).strip() if match else default


def _first_code_block(markdown_text: str) -> str:
    match = re.search(r"```(?:[A-Za-z0-9_-]+)?\s*\n(.*?)\n```", markdown_text, flags=re.DOTALL)
    return match.group(1).strip() if match else ""


def _latex_evidence_after_label(markdown_text: str, label: str, *, default: str = "") -> str:
    code = _code_block_after_label(markdown_text, label, default="")
    if code:
        return code
    pattern = re.compile(
        rf"{re.escape(label)}:\s*\n+\$\$\s*\n?(.*?)\n?\$\$",
        flags=re.DOTALL,
    )
    match = pattern.search(markdown_text)
    return match.group(1).strip() if match else default


def _line_after_label(markdown_text: str, label: str) -> str:
    pattern = re.compile(rf"^{re.escape(label)}:\s*(.*?)\s*$", flags=re.MULTILINE)
    match = pattern.search(markdown_text)
    return match.group(1).strip() if match else ""


def _loops_from_model_card(model_card: dict[str, str], parts: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    return {
        "zero_temperature": _loop_from_model_card_value(
            mode=model_card.get("zero_temperature", "none"),
            custom_expr=_sum_named_parts(parts, ["V_CW", "V_CT"]),
        ),
        "thermal": _loop_from_model_card_value(
            mode=model_card.get("thermal", "none"),
            custom_expr=_sum_named_parts(parts, ["V_thermal"]),
        ),
        "daisy": _loop_from_model_card_value(
            mode=model_card.get("daisy", "none"),
            custom_expr=_sum_named_parts(parts, ["V_daisy"]),
        ),
    }


def _loop_from_model_card_value(*, mode: str, custom_expr: str) -> dict[str, str]:
    clean_mode = str(mode or "none").strip() or "none"
    return {
        "mode": clean_mode,
        "custom_expr": custom_expr if clean_mode == "custom_expr" else "0.0",
        "notes": "From Model Card.",
    }


def _sum_named_parts(parts: dict[str, dict[str, str]], names: list[str]) -> str:
    expressions: list[tuple[list[str], str]] = []
    for name in names:
        expr = str(parts.get(name, {}).get("python", "")).strip()
        if not expr or expr == "0.0":
            continue
        if _placeholder(expr):
            return "ASK_USER"
        try:
            expressions.append(_compiler_block_parts(expr))
        except (SyntaxError, ValueError):
            expressions.append(([], expr))
    if not expressions:
        return "0.0"
    setup_lines: list[str] = []
    terms: list[str] = []
    for setup, result in expressions:
        setup_lines.extend(setup)
        terms.append(f"({result})")
    total = " + ".join(terms)
    if not setup_lines:
        return total
    return "\n".join([*setup_lines, total])


def _enabled(row: dict[str, str]) -> bool:
    return _truthy(row.get("enabled", "true"))


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"true", "yes", "y", "1", "approved"}


def _clean_named_row(row: dict[str, str]) -> dict[str, Any]:
    return {
        "name": str(row.get("name", "")).strip(),
        "latex": str(row.get("latex", "")).strip(),
        "role": str(row.get("role", "")).strip(),
        "description": str(row.get("description", "")).strip(),
    }


def _clean_field(row: dict[str, str]) -> dict[str, Any]:
    data = _clean_named_row(row)
    data["zeroT_default"] = str(row.get("zeroT_default", "0.0")).strip() or "0.0"
    return data


def _clean_public_input(row: dict[str, str]) -> dict[str, Any]:
    data = _clean_named_row(row)
    test_value = str(row.get("test_value", row.get("default", ""))).strip()
    data["default"] = test_value
    data["test_value"] = test_value
    data["confirmed"] = str(row.get("confirmed", "")).strip()
    data["source_type"] = str(row.get("source_type", "user_supplied")).strip() or "user_supplied"
    return data


def _clean_constant(row: dict[str, str]) -> dict[str, Any]:
    data = _clean_named_row(row)
    data["value"] = str(row.get("value", "")).strip()
    return data


def _clean_derived(row: dict[str, str]) -> dict[str, Any]:
    data = _clean_named_row(row)
    data["expr"] = str(row.get("expr", "")).strip()
    data["expr_status"] = str(row.get("expr_status", "")).strip()
    return data


def _clean_excluded_species(row: dict[str, str]) -> dict[str, Any]:
    return {
        "name": str(row.get("name", "")).strip(),
        "kind": str(row.get("kind", "")).strip(),
        "excluded_from": str(row.get("excluded_from", "")).strip(),
        "reason": str(row.get("reason", "")).strip(),
        "source_latex": str(row.get("source_latex", "")).strip(),
    }


def _enabled_species_row(row: dict[str, str], *, require_c: bool) -> dict[str, Any]:
    result = {
        "name": str(row.get("name", "")).strip(),
        "kind": str(row.get("kind", "")).strip(),
        "mass_sq": str(row.get("mass_sq", "")).strip(),
        "dof": str(row.get("dof", "")).strip(),
        "usage": str(row.get("usage", "")).strip(),
        "thermal_policy": str(row.get("thermal_policy", "")).strip(),
        "vacuum_anchor": str(row.get("vacuum_anchor", "")).strip(),
        "source_latex": str(row.get("source_latex", "")).strip(),
        "notes": str(row.get("notes", "")).strip(),
    }
    result["mass_sq_status"] = str(row.get("mass_sq_status", "")).strip()
    if require_c:
        result["c"] = str(row.get("c", "")).strip()
    return result


def _parse_implementation_contracts(section_text: str, model_card: dict[str, str]) -> dict[str, Any]:
    counterterm_basis_section = _subsection(section_text, "Counterterm Basis")
    counterterm_basis_rows = _attach_named_expression_blocks(
        _expand_note_refs(_parse_first_table(counterterm_basis_section), counterterm_basis_section),
        counterterm_basis_section,
        "Counterterm operator_expr",
        "operator_expr",
        allow_table_cell=True,
        name_key="coefficient",
    )
    counterterm_basis = [
        _clean_counterterm_basis(row)
        for row in counterterm_basis_rows
        if _enabled(row)
    ]
    counterterm_condition_section = _subsection(section_text, "Counterterm Conditions")
    counterterm_condition_rows = _attach_named_expression_blocks(
        _expand_note_refs(_parse_first_table(counterterm_condition_section), counterterm_condition_section),
        counterterm_condition_section,
        "Counterterm target_expr",
        "target_expr",
        allow_table_cell=True,
    )
    counterterm_conditions = [
        _clean_counterterm_condition(row)
        for row in counterterm_condition_rows
        if _enabled(row)
    ]
    goldstone_section = _subsection(section_text, "Goldstone Handling")
    goldstone = _key_value_table(goldstone_section)
    replacement_formula = _code_block_after_label(goldstone_section, "replacement_formula", default="")
    if replacement_formula:
        goldstone["replacement_formula"] = replacement_formula
    goldstone_species_section = _subsection(section_text, "Goldstone Species Handling")
    goldstone_species_rows = _attach_named_expression_blocks(
        _expand_note_refs(_parse_first_table(goldstone_species_section), goldstone_species_section),
        goldstone_species_section,
        "Goldstone mass_sq",
        "mass_sq",
        allow_table_cell=True,
    )
    goldstone_species_rows = _attach_named_expression_blocks(
        goldstone_species_rows,
        goldstone_species_section,
        "Goldstone replacement_expr",
        "replacement_expr",
        allow_table_cell=True,
    )
    goldstone["species"] = [
        _clean_goldstone_species(row)
        for row in goldstone_species_rows
        if _enabled(row)
    ]
    daisy_section = _subsection(section_text, "Daisy / Thermal-Mass Handling")
    daisy = _key_value_table(daisy_section)
    for daisy_formula_key in (
        "scalar_thermal_masses",
        "gauge_thermal_masses",
        "scalar_thermal_masses_derivation_notes",
        "gauge_thermal_masses_derivation_notes",
    ):
        daisy_formula = _code_block_after_label(daisy_section, daisy_formula_key, default="")
        if daisy_formula:
            daisy[daisy_formula_key] = daisy_formula
    daisy_particle_section = _subsection(section_text, "Daisy Particle Terms")
    daisy_particle_rows = _expand_note_refs(_parse_first_table(daisy_particle_section), daisy_particle_section)
    for heading_prefix, key in (
        ("Daisy zeroT_mass_sq", "zeroT_mass_sq"),
        ("Daisy thermal_mass_sq", "thermal_mass_sq"),
        ("Daisy daisy_term", "daisy_term"),
    ):
        daisy_particle_rows = _attach_named_expression_blocks(
            daisy_particle_rows,
            daisy_particle_section,
            heading_prefix,
            key,
            allow_table_cell=True,
        )
    daisy["particles"] = [
        _clean_daisy_particle(row)
        for row in daisy_particle_rows
        if _enabled(row)
    ]
    phase_filter_section = _subsection(section_text, "CosmoTransitions Phase Filtering")
    phase_filter = _key_value_table(phase_filter_section)
    phase_filter_custom_expr = _code_block_after_label(phase_filter_section, "custom_expr", default="")
    if phase_filter_custom_expr:
        phase_filter["custom_expr"] = phase_filter_custom_expr
    phase_filter["rules"] = [
        _clean_phase_filter_rule(row)
        for row in _parse_rows_by_subsection(section_text, "CosmoTransitions Phase Filter Rules")
        if _enabled(row)
    ]
    symmetry_section = _subsection(section_text, "PhaseTracer Symmetry")
    symmetry = _key_value_table(symmetry_section)
    symmetry["rules"] = [
        _clean_symmetry_rule(row)
        for row in _parse_rows_by_subsection(section_text, "PhaseTracer Symmetry Rules")
        if _enabled(row)
    ]
    return {
        "model_card": dict(model_card),
        "counterterms": {
            "mode": model_card.get("counterterm", "ASK_USER"),
            "basis": counterterm_basis,
            "conditions": counterterm_conditions,
        },
        "goldstone": goldstone,
        "daisy": daisy,
        "phase_filter": phase_filter,
        "symmetry": symmetry,
    }


def _clean_counterterm_basis(row: dict[str, str]) -> dict[str, str]:
    return {
        "coefficient": str(row.get("coefficient", "")).strip(),
        "operator_expr": str(row.get("operator_expr", "")).strip(),
        "source_latex": str(row.get("source_latex", "")).strip(),
        "notes": str(row.get("notes", "")).strip(),
    }


def _clean_counterterm_condition(row: dict[str, str]) -> dict[str, str]:
    return {
        "name": str(row.get("name", "")).strip(),
        "operator": str(row.get("operator", "")).strip(),
        "point": str(row.get("point", "")).strip(),
        "target_expr": str(row.get("target_expr", "")).strip(),
        "cw_source": str(row.get("cw_source", "")).strip(),
        "goldstone_handling": str(row.get("goldstone_handling", "")).strip(),
        "notes": str(row.get("notes", "")).strip(),
    }


def _clean_goldstone_species(row: dict[str, str]) -> dict[str, str]:
    result = {
        "name": str(row.get("name", "")).strip(),
        "mass_sq": str(row.get("mass_sq", "")).strip(),
        "dof": str(row.get("dof", "")).strip(),
        "cw_policy": str(row.get("cw_policy", "")).strip(),
        "ct_policy": str(row.get("ct_policy", "")).strip(),
        "thermal_policy": str(row.get("thermal_policy", "")).strip(),
        "regulator": str(row.get("regulator", "")).strip(),
        "replacement_expr": str(row.get("replacement_expr", "")).strip(),
        "notes": str(row.get("notes", "")).strip(),
    }
    result["mass_sq_status"] = str(row.get("mass_sq_status", "")).strip()
    return result


def _clean_daisy_particle(row: dict[str, str]) -> dict[str, str]:
    result = {
        "name": str(row.get("name", "")).strip(),
        "kind": str(row.get("kind", "")).strip(),
        "implementation_owner": str(row.get("implementation_owner", "")).strip(),
        "zeroT_mass_sq": str(row.get("zeroT_mass_sq", "")).strip(),
        "thermal_mass_sq": str(row.get("thermal_mass_sq", "")).strip(),
        "dof": str(row.get("dof", "")).strip(),
        "longitudinal_only": str(row.get("longitudinal_only", "")).strip(),
        "daisy_term": str(row.get("daisy_term", "")).strip(),
        "notes": str(row.get("notes", "")).strip(),
    }
    result["zeroT_mass_sq_status"] = str(row.get("zeroT_mass_sq_status", "")).strip()
    result["thermal_mass_sq_status"] = str(row.get("thermal_mass_sq_status", "")).strip()
    result["daisy_term_status"] = str(row.get("daisy_term_status", "")).strip()
    return result


def _clean_phase_filter_rule(row: dict[str, str]) -> dict[str, str]:
    return {
        "field": str(row.get("field", "")).strip(),
        "comparison": str(row.get("comparison", "")).strip(),
        "threshold": str(row.get("threshold", "")).strip(),
        "phase_type": str(row.get("phase_type", "")).strip(),
        "reason": str(row.get("reason", "")).strip(),
        "source_reference": str(row.get("source_reference", "")).strip(),
    }


def _clean_symmetry_rule(row: dict[str, str]) -> dict[str, str]:
    return {
        "fields": str(row.get("fields", "")).strip(),
        "transformation": str(row.get("transformation", "")).strip(),
        "reason": str(row.get("reason", "")).strip(),
        "source_reference": str(row.get("source_reference", "")).strip(),
    }


def _parse_boson_matrices(section_text: str) -> list[dict[str, Any]]:
    matrices: list[dict[str, Any]] = []
    pattern = re.compile(r"^####\s+Matrix:\s*(.*?)\s*$", flags=re.MULTILINE)
    matches = list(pattern.finditer(section_text))
    for index, match in enumerate(matches):
        name = match.group(1).strip()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(section_text)
        block = section_text[match.end() : end].strip()
        tables = _parse_tables(block)
        if len(tables) < 2:
            continue
        meta = _key_value_from_rows(tables[0][1])
        if not _truthy(meta.get("enabled", "")):
            continue
        headers, rows = tables[1]
        basis = [item.strip() for item in str(meta.get("basis", "")).split(",") if item.strip()]
        matrix, matrix_entry_statuses = _parse_matrix_entries(block, headers, rows, basis)
        if not basis:
            basis = headers[1:] if "row/col" in headers else []
        matrices.append(
            {
                "name": name,
                "kind": meta.get("kind", "scalar"),
                "basis": basis,
                "matrix": matrix,
                "matrix_entry_statuses": matrix_entry_statuses,
                "dof_per_eigenvalue": meta.get("dof_per_eigenvalue", "1"),
                "c": meta.get("c", "1.5"),
                "source_role": meta.get("source_role", "field_dependent"),
                "source_latex": _code_block_after_label(block, "Source LaTeX", default=""),
                "notes": meta.get("notes", ""),
            }
        )
    return matrices


def _parse_matrix_entries(
    matrix_block: str,
    headers: list[str],
    rows: list[dict[str, str]],
    basis: list[str],
) -> tuple[list[list[str]], list[list[str]]]:
    if "row" in headers and "col" in headers:
        if not basis:
            basis = _matrix_basis_from_entry_rows(rows)
        matrix = [["" for _column in basis] for _row in basis]
        statuses = [["" for _column in basis] for _row in basis]
        block_map = _matrix_entry_block_map(matrix_block)
        for row in rows:
            row_name = str(row.get("row", "")).strip()
            col_name = str(row.get("col", "")).strip()
            if row_name not in basis or col_name not in basis:
                continue
            row_index = basis.index(row_name)
            col_index = basis.index(col_name)
            key = _normalize_matrix_entry_key(f"{row_name},{col_name}")
            expr = block_map.get(key, str(row.get("entry", "") or row.get("expr", "")).strip())
            matrix[row_index][col_index] = expr
            statuses[row_index][col_index] = str(row.get("entry_status", "")).strip()
        return matrix, statuses
    parsed_basis = basis or headers[1:]
    matrix = [
        [str(row.get(column, "")).strip() for column in parsed_basis]
        for row in rows
    ]
    return matrix, []


def _matrix_basis_from_entry_rows(rows: list[dict[str, str]]) -> list[str]:
    basis: list[str] = []
    for row in rows:
        for key in ("row", "col"):
            value = str(row.get(key, "")).strip()
            if value and value not in basis:
                basis.append(value)
    return basis


def _matrix_entry_block_map(matrix_block: str) -> dict[str, str]:
    pattern = re.compile(r"^#{5,6}\s+Matrix entry:\s*(.*?)\s*$", flags=re.MULTILINE)
    matches = list(pattern.finditer(matrix_block))
    expressions: dict[str, str] = {}
    for index, match in enumerate(matches):
        key = _normalize_matrix_entry_key(match.group(1))
        if not key:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(matrix_block)
        block = matrix_block[match.end() : end].strip()
        expr = _code_block_after_label(block, "entry", default="")
        if not expr:
            expr = _first_code_block(block)
        if expr:
            expressions[key] = expr
    return expressions


def _normalize_matrix_entry_key(text: str) -> str:
    clean = str(text or "").strip()
    bracket = re.search(r"\[(.*?)\]", clean)
    if bracket:
        clean = bracket.group(1)
    clean = clean.strip("()")
    clean = clean.replace(";", ",").replace("|", ",")
    return re.sub(r"\s+", "", clean)


def _key_value_from_rows(rows: list[dict[str, str]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in rows:
        key = str(row.get("key", "")).strip()
        if key:
            result[key] = str(row.get("value", "")).strip()
    return result


def validate_contract_template(
    markdown_text: str,
    *,
    review_required: bool = True,
    compile_backend: str | None = None,
) -> ContractValidation:
    try:
        contract = parse_contract(markdown_text)
    except ValueError as exc:
        return ContractValidation(
            contract={},
            issues=[
                ValidationIssue(
                    severity="error",
                    code="contract_parse_failed",
                    field_key="contract",
                    message=str(exc),
                    suggested_action="Use the generated Markdown contract worksheet and keep its fixed tables/code blocks intact.",
                )
            ],
        )
    return validate_contract(contract, review_required=review_required, compile_backend=compile_backend)


def resolved_contract_from_template(markdown_text: str) -> dict[str, Any]:
    """Resolve the human-edited Markdown contract into the machine contract dict."""

    return parse_contract(markdown_text)


def write_resolved_contract(markdown_text: str, path: str | Path) -> Path:
    """Write the derived contract JSON. The Markdown template remains the source of truth."""

    return write_resolved_contract_data(resolved_contract_from_template(markdown_text), path)


def write_resolved_contract_data(contract: dict[str, Any], path: str | Path) -> Path:
    """Write an already-resolved contract JSON."""

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(contract, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_contract(contract: dict[str, Any]) -> str:
    payload = json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _sha256_text(payload)


def validate_contract(
    contract: dict[str, Any],
    *,
    review_required: bool = True,
    compile_backend: str | None = None,
) -> ContractValidation:
    issues: list[ValidationIssue] = []
    if contract.get("schema") != CONTRACT_SCHEMA:
        issues.append(_error("schema", "schema_mismatch", f"Expected schema {CONTRACT_SCHEMA}."))
    review = _dict(contract.get("review"))
    if review_required and review.get("approved") is not True:
        issues.append(
            _error(
                "review.approved",
                "review_required",
                "Template compilation is blocked until review.approved is true.",
                "Check the source evidence, ask the user to review the rendered Markdown, and set review.approved=true only after explicit approval to generate code.",
            )
        )

    fields = _list(contract.get("fields"))
    if not fields:
        issues.append(_error("fields", "missing_value", "At least one background field is required."))
    field_names = _validate_named_rows(fields, "fields", issues)

    params = _dict(contract.get("parameters"))
    public_inputs = _list(params.get("public_inputs"))
    constants = _list(params.get("constants"))
    derived = _list(params.get("derived"))
    input_names = _validate_named_rows(public_inputs, "parameters.public_inputs", issues)
    constant_names = _validate_named_rows(constants, "parameters.constants", issues)
    derived_names = _validate_named_rows(derived, "parameters.derived", issues)
    _check_duplicate_names(field_names + input_names + constant_names + derived_names, "symbols", issues)

    for index, row in enumerate(public_inputs):
        default = row.get("default")
        confirmed = row.get("confirmed")
        if not _truthy(confirmed):
            issues.append(
                _error(
                    f"parameters.public_inputs[{index}].confirmed",
                    "input_basis_unconfirmed",
                    "Public input basis must be explicitly confirmed by the user before code generation.",
                    "Set `confirmed` to true only for real build/scan inputs; remove non-input rows or move them to Fixed Constants/Derived Quantities.",
                )
            )
        if _placeholder(default):
            issues.append(
                _error(
                    f"parameters.public_inputs[{index}].test_value",
                    "placeholder_value",
                    "Every confirmed public input needs a numeric test_value for build_model and smoke tests.",
                    "Ask the user for the benchmark/scan point value and enter it in Section 3 `Public Inputs`.",
                )
            )
        elif not _numeric_literal(default):
            issues.append(_error(f"parameters.public_inputs[{index}].test_value", "invalid_number", "Public input test_value must be a numeric literal."))
    for index, row in enumerate(constants):
        value = row.get("value")
        if _placeholder(value):
            issues.append(_error(f"parameters.constants[{index}].value", "placeholder_value", "Constant value is unresolved."))
        elif not _numeric_literal(value):
            issues.append(_error(f"parameters.constants[{index}].value", "invalid_number", "Constant value must be numeric."))

    allowed = set(field_names) | set(input_names) | set(constant_names)
    for index, row in enumerate(derived):
        name = str(row.get("name", ""))
        expr = str(row.get("expr", ""))
        if _placeholder(expr):
            issues.append(_error(f"parameters.derived[{index}].expr", "placeholder_value", f"Derived parameter {name or index} has no expression."))
            continue
        _require_review_status(
            row,
            status_column="expr_status",
            field_key=f"parameters.derived[{index}].expr_status",
            issues=issues,
            subject=f"Derived expression {name or index}",
        )
        _validate_expression(expr, allowed, f"parameters.derived[{index}].expr", issues)
        if name:
            allowed.add(name)

    vacuum_allowed = set(allowed) - set(field_names)
    for index, row in enumerate(fields):
        expr = str(row.get("zeroT_default", "0.0")).strip() or "0.0"
        if _placeholder(expr):
            issues.append(_error(f"fields[{index}].zeroT_default", "placeholder_value", "Zero-temperature default field value is unresolved."))
            continue
        _validate_expression(expr, vacuum_allowed, f"fields[{index}].zeroT_default", issues)

    potential = _dict(contract.get("potential"))
    v0 = _dict(potential.get("V0"))
    _require_expression(v0.get("python"), allowed, "potential.V0.python", issues)

    masses = _dict(contract.get("masses"))
    bosons = _list(masses.get("bosons"))
    boson_matrices = _list(masses.get("boson_matrices"))
    fermions = _list(masses.get("fermions"))
    boson_matrices = _list(masses.get("boson_matrices"))
    _validate_species(bosons, "masses.bosons", allowed, issues, require_c=True)
    _validate_species(fermions, "masses.fermions", allowed, issues, require_c=False)
    _validate_boson_matrices(boson_matrices, "masses.boson_matrices", set(allowed) | {"T"}, issues)
    _validate_source_basis_fidelity(contract, issues)

    loops = _dict(contract.get("loops"))
    zero = _dict(loops.get("zero_temperature"))
    thermal = _dict(loops.get("thermal"))
    daisy = _dict(loops.get("daisy"))
    _validate_mode(zero, ZERO_LOOP_MODES, "loops.zero_temperature", issues)
    _validate_mode(thermal, THERMAL_MODES, "loops.thermal", issues)
    _validate_mode(daisy, DAISY_MODES, "loops.daisy", issues)
    runtime_allowed = set(allowed) | {"T"}
    if zero.get("mode") == "custom_expr":
        _require_expression(zero.get("custom_expr"), runtime_allowed, "loops.zero_temperature.custom_expr", issues)
    if thermal.get("mode") == "custom_expr":
        _require_expression(thermal.get("custom_expr"), runtime_allowed, "loops.thermal.custom_expr", issues)
    if daisy.get("mode") == "custom_expr":
        _require_expression(daisy.get("custom_expr"), runtime_allowed, "loops.daisy.custom_expr", issues)
    if zero.get("mode") in {"standard_CW_V1", "paper_os_like_V1"} and not (bosons or fermions or boson_matrices):
        issues.append(_error("loops.zero_temperature.mode", "missing_species", "Zero-temperature loop mode requires reviewed boson or fermion species."))
    if thermal.get("mode") == "standard_thermal_integrals" and not (bosons or fermions or boson_matrices):
        issues.append(_error("loops.thermal.mode", "missing_species", "standard_thermal_integrals requires reviewed boson or fermion species."))
    if thermal.get("mode") == "standard_thermal_integrals":
        for name in ("num_boson_dof", "num_fermion_dof"):
            if name not in allowed:
                issues.append(
                    _error(
                        f"parameters.radiation_dof.{name}",
                        "missing_value",
                        f"standard_thermal_integrals requires reviewed `{name}` so radiation terms are correct.",
                        "Add it to Fixed Constants or Derived Quantities. Use SM baseline 28/90 and add reviewed extra BSM relativistic d.o.f.; tell the user what was added.",
                    )
                )
    _validate_model_card_and_implementation(
        contract,
        runtime_allowed,
        set(field_names),
        issues,
        compile_backend=compile_backend,
    )
    return ContractValidation(contract=contract, issues=issues)


def _validate_model_card_and_implementation(
    contract: dict[str, Any],
    allowed_names: set[str],
    field_names: set[str],
    issues: list[ValidationIssue],
    *,
    compile_backend: str | None = None,
) -> None:
    model_card = _dict(contract.get("model_card"))
    loops = _dict(contract.get("loops"))
    for key in REQUIRED_MODEL_CARD_KEYS:
        value = str(model_card.get(key, "")).strip()
        if _unresolved_contract_choice(value):
            issues.append(
                _error(
                    f"model_card.{key}",
                    "placeholder_value",
                    f"Model Card row {key!r} must be reviewed before code generation.",
                    "Fill the Model Card row in Section 1; use not_applicable or none only when that is the reviewed choice.",
                )
            )
    counterterm_mode = str(model_card.get("counterterm", "")).strip()
    resummation_scheme = str(model_card.get("resummation_scheme", "")).strip()
    _validate_contract_choice("model_card.counterterm", counterterm_mode, COUNTERTERM_MODES, issues)
    _validate_contract_choice("model_card.resummation_scheme", resummation_scheme, RESUMMATION_SCHEMES, issues)
    zero_mode = str(_dict(loops.get("zero_temperature")).get("mode", "")).strip()
    if counterterm_mode == "explicit_linear_system" and zero_mode == "paper_os_like_V1":
        issues.append(
            _error(
                "model_card.counterterm",
                "contract_conflict",
                "paper_os_like_V1 already encodes OS-like zero-temperature renormalization; do not combine it with explicit_linear_system counterterms.",
                "Use paper_os_like_V1 with counterterm=implicit_in_V1/none, or use standard_CW_V1/custom_expr before solving an explicit CT system.",
            )
        )

    implementation = _dict(contract.get("implementation"))
    counterterms = _dict(implementation.get("counterterms"))
    goldstone = _dict(implementation.get("goldstone"))
    daisy = _dict(implementation.get("daisy"))
    phase_filter = _dict(implementation.get("phase_filter"))
    symmetry = _dict(implementation.get("symmetry"))
    basis = _list(counterterms.get("basis"))
    conditions = _list(counterterms.get("conditions"))

    if counterterm_mode == "explicit_linear_system":
        if not basis:
            issues.append(
                _error(
                    "implementation.counterterms.basis",
                    "missing_value",
                    "Explicit counterterms require a visible CT basis table.",
                    "Fill Section 6 `Counterterm Basis`; each coefficient multiplies one operator expression.",
                )
            )
        if not conditions:
            issues.append(
                _error(
                    "implementation.counterterms.conditions",
                    "missing_value",
                    "Explicit counterterms require reviewed linear conditions.",
                    "Fill Section 6 `Counterterm Conditions` with the operators used to solve the coefficients.",
                )
            )
    elif counterterm_mode == "custom_expr":
        vct = str(
            _dict(_dict(contract.get("potential")).get("pieces")).get("V_CT", {}).get("python", "")
        ).strip()
        if _placeholder(vct) or vct == "0.0":
            issues.append(
                _error(
                    "potential.pieces.V_CT.python",
                    "placeholder_value",
                    "counterterm=custom_expr requires a reviewed V_CT compiler expression.",
                    "Fill Section 4 `Potential Part: V_CT`, or set counterterm to none/implicit_in_V1.",
                )
            )

    for index, row in enumerate(basis):
        if not isinstance(row, dict):
            issues.append(_error(f"implementation.counterterms.basis[{index}]", "invalid_row", "CT basis row must be an object."))
            continue
        coefficient = str(row.get("coefficient", "")).strip()
        if _placeholder(coefficient) or not _valid_identifier(coefficient):
            issues.append(
                _error(
                    f"implementation.counterterms.basis[{index}].coefficient",
                    "invalid_identifier",
                    "CT coefficient must be a Python-safe symbol.",
                )
            )
        _require_expression(row.get("operator_expr"), allowed_names, f"implementation.counterterms.basis[{index}].operator_expr", issues)

    for index, row in enumerate(conditions):
        if not isinstance(row, dict):
            issues.append(_error(f"implementation.counterterms.conditions[{index}]", "invalid_row", "CT condition row must be an object."))
            continue
        for column in ("name", "operator", "point", "cw_source", "goldstone_handling"):
            if _placeholder(row.get(column)):
                issues.append(
                    _error(
                        f"implementation.counterterms.conditions[{index}].{column}",
                        "placeholder_value",
                        f"CT condition column {column!r} is unresolved.",
                    )
                )
        _require_expression(
            row.get("target_expr"),
            allowed_names,
            f"implementation.counterterms.conditions[{index}].target_expr",
            issues,
        )

    required_goldstone = ("cw_policy", "ct_derivative_policy", "thermal_policy", "regulator", "replacement_formula", "notes")
    for key in required_goldstone:
        if _placeholder(goldstone.get(key)):
            issues.append(
                _error(
                    f"implementation.goldstone.{key}",
                    "placeholder_value",
                    f"Goldstone handling row {key!r} must be reviewed.",
                    "Fill Section 6 `Goldstone Handling`; use not_applicable when the model has no relevant Goldstones.",
                )
            )
    replacement = str(goldstone.get("replacement_formula", "0.0")).strip()
    no_replacement_values = {"", "0.0", "none", "not_applicable"}
    if replacement not in no_replacement_values and not _placeholder(replacement):
        _validate_expression(replacement, allowed_names, "implementation.goldstone.replacement_formula", issues)
    if str(goldstone.get("ct_derivative_policy", "")).strip() == "regulated_replacement" and replacement in no_replacement_values:
        issues.append(
            _error(
                "implementation.goldstone.replacement_formula",
                "missing_value",
                "regulated_replacement requires the reviewed replacement term, not 0.0/not_applicable.",
            )
        )
    goldstone_species = _list(goldstone.get("species"))
    if _goldstone_requires_species(goldstone) and not goldstone_species:
        issues.append(
            _error(
                "implementation.goldstone.species",
                "missing_value",
                "Goldstone handling is nontrivial but no per-species Goldstone table rows were reviewed.",
                "Fill Section 6 `Goldstone Species Handling` with one row per Goldstone mode or mode group.",
            )
        )
    _validate_goldstone_species(goldstone_species, allowed_names, issues)

    required_daisy = (
        "scheme",
        "implementation",
        "longitudinal_vectors_only",
        "scalar_thermal_masses",
        "gauge_thermal_masses",
        "double_counting_guard",
    )
    for key in required_daisy:
        if _placeholder(daisy.get(key)):
            issues.append(
                _error(
                    f"implementation.daisy.{key}",
                    "placeholder_value",
                    f"Daisy / thermal-mass row {key!r} must be reviewed.",
                    "Fill Section 6 `Daisy / Thermal-Mass Handling`; use not_applicable or none only when that is the reviewed choice.",
                )
            )
    for key in ("scalar_thermal_masses", "gauge_thermal_masses"):
        marker = _partial_thermal_self_energy_marker(daisy.get(key, ""))
        if marker:
            issues.append(
                _error(
                    f"implementation.daisy.{key}",
                    "partial_thermal_self_energy",
                    f"Daisy thermal-mass summary appears to contain only a partial self-energy ({marker!r}).",
                    "Assemble the full resummed mass/eigenvalue from the zero-temperature mass matrix plus all reviewed baseline and additional thermal self-energies before marking it reviewed.",
                )
            )
    daisy_scheme = str(daisy.get("scheme", "")).strip()
    daisy_implementation = str(daisy.get("implementation", "")).strip()
    _validate_contract_choice("implementation.daisy.scheme", daisy_scheme, RESUMMATION_SCHEMES, issues)
    _validate_contract_choice(
        "implementation.daisy.implementation",
        daisy_implementation,
        THERMAL_MASS_IMPLEMENTATIONS,
        issues,
    )
    if (
        not _unresolved_contract_choice(resummation_scheme)
        and not _unresolved_contract_choice(daisy_scheme)
        and resummation_scheme != daisy_scheme
    ):
        issues.append(
            _error(
                "implementation.daisy.scheme",
                "contract_conflict",
                "Section 1 resummation_scheme and Section 6 Daisy scheme disagree.",
                "Make the Model Card and Daisy / Thermal-Mass Handling rows state the same reviewed scheme.",
            )
        )
    if daisy_scheme != "none" and daisy_implementation in {"none", "not_applicable"}:
        issues.append(
            _error(
                "implementation.daisy.implementation",
                "contract_conflict",
                "A non-none resummation scheme needs an implementation route.",
            )
        )
    if daisy_scheme == "none" and daisy_implementation not in {"none", "not_applicable"}:
        issues.append(
            _error(
                "implementation.daisy.implementation",
                "contract_conflict",
                "Daisy implementation is enabled while the scheme is none.",
            )
        )
    daisy_particles = _list(daisy.get("particles"))
    if not _unresolved_contract_choice(daisy_scheme) and daisy_scheme != "none" and not daisy_particles:
        issues.append(
            _error(
                "implementation.daisy.particles",
                "missing_value",
                "A non-none Daisy route requires reviewed per-particle thermal-mass/Daisy rows.",
                "Fill Section 6 `Daisy Particle Terms` for every bosonic mode or mode group affected by resummation.",
            )
        )
    _validate_daisy_particles(daisy_particles, allowed_names, issues)
    daisy_mode = str(_dict(loops.get("daisy")).get("mode", "")).strip()
    if daisy_scheme == "Parwani":
        if daisy_implementation != "parwani_thermal_masses":
            issues.append(
                _error(
                    "implementation.daisy.implementation",
                    "contract_conflict",
                    "Parwani resummation must use the parwani_thermal_masses implementation route.",
                    "Parwani replaces one-loop mass eigenvalues by reviewed lowest-order thermal masses and does not add a separate Daisy potential.",
                )
            )
        if daisy_mode != "none":
            issues.append(
                _error(
                    "loops.daisy.mode",
                    "contract_conflict",
                    "Parwani resummation must not add a separate V_daisy term.",
                    "Set Model Card `daisy` to none; put the thermal-mass replacement in Section 6.",
                )
            )
    if daisy_scheme == "Arnold-Espinosa":
        if daisy_implementation != "arnold_espinosa_explicit_daisy":
            issues.append(
                _error(
                    "implementation.daisy.implementation",
                    "contract_conflict",
                    "Arnold-Espinosa resummation must use the arnold_espinosa_explicit_daisy route.",
                    "Keep ordinary field-dependent masses in V1/V1T and add the reviewed explicit V_daisy term.",
                )
            )
        if _placeholder(daisy.get("cubic_power_policy")):
            issues.append(
                _error(
                    "implementation.daisy.cubic_power_policy",
                    "placeholder_value",
                    "Arnold-Espinosa V_daisy needs a reviewed convention for `(m^2)^(3/2)` when mass squared can be negative.",
                    "State the policy in Section 6, for example `positive_part`, `signed_abs`, `regulated_abs`, or an explicit paper formula, and make the V_daisy compiler expression implement it.",
                )
            )
        if daisy_mode != "custom_expr":
            issues.append(
                _error(
                    "loops.daisy.mode",
                    "contract_conflict",
                    "Arnold-Espinosa resummation requires an explicit V_daisy expression.",
                    "Set Model Card `daisy` to custom_expr and fill Potential Part `V_daisy`.",
                )
            )
    backend = str(compile_backend or "").strip().lower()
    if backend == "phasetracer":
        _validate_phasetracer_symmetry(symmetry, field_names, issues)
    elif backend == "compare-backends":
        _validate_phase_filter(phase_filter, field_names, allowed_names, issues)
        _validate_phasetracer_symmetry(symmetry, field_names, issues)
    else:
        _validate_phase_filter(phase_filter, field_names, allowed_names, issues)


def _validate_source_basis_fidelity(contract: dict[str, Any], issues: list[ValidationIssue]) -> None:
    if not _thermal_source_suggests_vector_basis_mixing(contract):
        return
    if _has_enabled_vector_boson_matrix(contract):
        return
    if _thermal_source_declares_final_eigenvalues(contract):
        return
    issues.append(
        _error(
            "masses.boson_matrices",
            "source_basis_requires_matrix_or_eigenvalues",
            "Thermal gauge-boson source evidence appears to describe a basis/mixing structure, but no vector/gauge mass matrix or source-given final eigenvalue declaration is present.",
            "Use a `kind = vector`/`kind = gauge` Boson Mass Matrix for the reviewed basis structure, or explicitly document that the Direct Boson rows are source-given final thermal eigenvalues.",
        )
    )


def _thermal_source_suggests_vector_basis_mixing(contract: dict[str, Any]) -> bool:
    text = _thermal_source_basis_text(contract)
    if not text.strip():
        return False
    lowered = text.lower()
    if any(marker in lowered for marker in ("not_applicable", "same_as_zero", "no gauge thermal")) and not _basis_or_mixing_marker(text):
        return False
    thermal_marker = any(
        marker in lowered
        for marker in (
            "thermal",
            "debye",
            "longitudinal",
            "transverse",
            "resumm",
            "parwani",
            "daisy",
            "self-energy",
            "self energy",
            "t**2",
            "t^2",
            "temperature",
            "pi_",
            "delta pi",
        )
    )
    return thermal_marker and _basis_or_mixing_marker(text)


def _thermal_source_declares_final_eigenvalues(contract: dict[str, Any]) -> bool:
    lowered = _thermal_source_basis_text(contract).lower()
    if any(marker in lowered for marker in THERMAL_BASIS_MIXING_REVIEW_MARKERS):
        return True
    masses = _dict(contract.get("masses"))
    direct_vector_rows = [
        _dict(row)
        for row in _list(masses.get("bosons"))
        if _row_is_vector_like(_dict(row))
    ]
    if not direct_vector_rows:
        return False
    return any(
        any(marker in _row_review_text(row).lower() for marker in THERMAL_BASIS_MIXING_REVIEW_MARKERS)
        for row in direct_vector_rows
    )


def _has_enabled_vector_boson_matrix(contract: dict[str, Any]) -> bool:
    masses = _dict(contract.get("masses"))
    for row in _list(masses.get("boson_matrices")):
        item = _dict(row)
        if _truthy(item.get("enabled", True)) and _row_is_vector_like(item):
            return True
    return False


def _thermal_source_basis_text(contract: dict[str, Any]) -> str:
    implementation = _dict(contract.get("implementation"))
    daisy = _dict(implementation.get("daisy"))
    masses = _dict(contract.get("masses"))
    chunks: list[str] = [
        str(daisy.get("gauge_thermal_masses", "")),
        str(daisy.get("gauge_thermal_masses_derivation_notes", "")),
    ]
    for row in _list(daisy.get("particles")):
        item = _dict(row)
        if _row_is_vector_like(item):
            chunks.append(_row_review_text(item))
    for row in _list(masses.get("bosons")):
        item = _dict(row)
        if _row_is_vector_like(item):
            chunks.append(_row_review_text(item))
    return "\n".join(chunk for chunk in chunks if chunk)


def _row_review_text(row: dict[str, Any]) -> str:
    return " ".join(
        str(row.get(key, ""))
        for key in (
            "name",
            "kind",
            "mass_sq",
            "zeroT_mass_sq",
            "thermal_mass_sq",
            "daisy_term",
            "source_latex",
            "notes",
            "thermal_policy",
            "vacuum_anchor",
        )
    )


def _row_is_vector_like(row: dict[str, Any]) -> bool:
    kind = str(row.get("kind", "")).lower()
    return "vector" in kind or "gauge" in kind


def _basis_or_mixing_marker(text: str) -> bool:
    return bool(
        re.search(
            r"(?i)(?:\bneutral\b|mix(?:ing|ed)?|\bbasis\b|\bmatrix\b|"
            r"\bW\s*3\b|W_?3|\\gamma|\bgamma\b|\bphoton\b|"
            r"\bd[_\s-]*B\b|\bPi[_\s-]*B\b|\\Pi_?\{?B\}?|"
            r"\bB\s*/\s*W\s*3\b|\bW\s*3\s*/\s*B\b)",
            text,
        )
    )


def _goldstone_requires_species(goldstone: dict[str, Any]) -> bool:
    policies = [
        str(goldstone.get("cw_policy", "")).strip(),
        str(goldstone.get("ct_derivative_policy", "")).strip(),
        str(goldstone.get("thermal_policy", "")).strip(),
    ]
    if any(_unresolved_contract_choice(item) for item in policies):
        return False
    trivial = {"not_applicable", "none"}
    return any(item not in trivial for item in policies)


def _validate_goldstone_species(rows: list[Any], allowed_names: set[str], issues: list[ValidationIssue]) -> None:
    _validate_named_rows(rows, "implementation.goldstone.species", issues)
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        _require_expression(row.get("mass_sq"), allowed_names, f"implementation.goldstone.species[{index}].mass_sq", issues)
        _require_review_status(
            row,
            status_column="mass_sq_status",
            field_key=f"implementation.goldstone.species[{index}].mass_sq_status",
            issues=issues,
            subject=f"Goldstone mass expression {row.get('name', index)}",
        )
        if _placeholder(row.get("dof")):
            issues.append(_error(f"implementation.goldstone.species[{index}].dof", "placeholder_value", "Goldstone species d.o.f. is unresolved."))
        elif not _numeric_literal(row.get("dof")):
            issues.append(_error(f"implementation.goldstone.species[{index}].dof", "invalid_number", "Goldstone species d.o.f. must be numeric."))
        for column in ("cw_policy", "ct_policy", "thermal_policy", "regulator"):
            if _placeholder(row.get(column)):
                issues.append(
                    _error(
                        f"implementation.goldstone.species[{index}].{column}",
                        "placeholder_value",
                        f"Goldstone species column {column!r} is unresolved.",
                    )
                )
        replacement = str(row.get("replacement_expr", "")).strip()
        if replacement and replacement not in {"0.0", "none", "not_applicable"} and not _placeholder(replacement):
            _validate_expression(replacement, allowed_names, f"implementation.goldstone.species[{index}].replacement_expr", issues)


def _validate_daisy_particles(rows: list[Any], allowed_names: set[str], issues: list[ValidationIssue]) -> None:
    _validate_named_rows(rows, "implementation.daisy.particles", issues)
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        for column in ("kind", "longitudinal_only"):
            if _placeholder(row.get(column)):
                issues.append(
                    _error(
                        f"implementation.daisy.particles[{index}].{column}",
                        "placeholder_value",
                        f"Daisy particle column {column!r} is unresolved.",
                    )
                )
        for column in ("zeroT_mass_sq", "thermal_mass_sq"):
            value = str(row.get(column, "")).strip()
            if value in DAISY_NON_FORMULA_MARKERS:
                continue
            _require_expression(value, allowed_names, f"implementation.daisy.particles[{index}].{column}", issues)
            _require_review_status(
                row,
                status_column=f"{column}_status",
                field_key=f"implementation.daisy.particles[{index}].{column}_status",
                issues=issues,
                subject=f"Daisy {column} expression {row.get('name', index)}",
            )
        marker = _partial_thermal_self_energy_marker(
            " ".join(str(row.get(key, "")) for key in ("thermal_mass_sq", "notes"))
        )
        if marker:
            issues.append(
                _error(
                    f"implementation.daisy.particles[{index}].thermal_mass_sq",
                    "partial_thermal_self_energy",
                    f"Daisy particle thermal_mass_sq appears to contain only a partial self-energy ({marker!r}).",
                    "Use the full resummed mass squared/eigenvalue in thermal_mass_sq; keep partial Delta Pi/additional pieces only as derivation notes.",
                )
            )
        if _placeholder(row.get("dof")):
            issues.append(_error(f"implementation.daisy.particles[{index}].dof", "placeholder_value", "Daisy particle d.o.f. is unresolved."))
        elif not _numeric_literal(row.get("dof")):
            issues.append(_error(f"implementation.daisy.particles[{index}].dof", "invalid_number", "Daisy particle d.o.f. must be numeric."))
        daisy_term = str(row.get("daisy_term", "")).strip()
        if daisy_term and daisy_term not in {"none", *DAISY_NON_FORMULA_MARKERS} and not _placeholder(daisy_term):
            _validate_expression(daisy_term, allowed_names, f"implementation.daisy.particles[{index}].daisy_term", issues)
            _require_review_status(
                row,
                status_column="daisy_term_status",
                field_key=f"implementation.daisy.particles[{index}].daisy_term_status",
                issues=issues,
                subject=f"Daisy term expression {row.get('name', index)}",
            )


def _validate_phase_filter(
    phase_filter: dict[str, Any],
    field_names: set[str],
    allowed_names: set[str],
    issues: list[ValidationIssue],
) -> None:
    mode = str(phase_filter.get("mode", "")).strip()
    if _placeholder(mode):
        issues.append(
            _error(
                "implementation.phase_filter.mode",
                "placeholder_value",
                "Phase-filtering policy is unresolved.",
                "Ask the user to confirm the CosmoTransitions forbidden-phase policy. Recommend `none` unless the paper/reference code explicitly removes a duplicate or unphysical branch; otherwise use `negative_field_threshold` with field/comparison/threshold rows such as xSM's `h < -5.0` style and explain the reason.",
            )
        )
        return
    _validate_contract_choice("implementation.phase_filter.mode", mode, PHASE_FILTER_MODES, issues)
    rules = _list(phase_filter.get("rules"))
    if mode == "none":
        if rules:
            issues.append(
                _error(
                    "implementation.phase_filter.rules",
                    "contract_conflict",
                    "Phase filter rules are enabled while mode is none.",
                    "Either set mode to `negative_field_threshold` or disable/remove the rule rows.",
                )
            )
        return
    if mode == "custom_expr":
        _require_expression(
            phase_filter.get("custom_expr"),
            allowed_names,
            "implementation.phase_filter.custom_expr",
            issues,
        )
        return
    if mode == "negative_field_threshold":
        if not rules:
            issues.append(
                _error(
                    "implementation.phase_filter.rules",
                    "missing_value",
                    "negative_field_threshold mode requires at least one reviewed Phase Filter Rule row.",
                    "Ask the user to confirm which field branch to discard and what tolerance to use; xSM often uses `< -5.0` to remove negative mirror branches without removing phases at zero. Recommend `none` if no forbidden branch is intentionally reviewed.",
                )
            )
            return
        allowed_comparisons = {"<", "<=", ">", ">="}
        for index, row in enumerate(rules):
            if not isinstance(row, dict):
                issues.append(_error(f"implementation.phase_filter.rules[{index}]", "invalid_row", "Phase filter rule must be a table row."))
                continue
            field = str(row.get("field", "")).strip()
            comparison = str(row.get("comparison", "")).strip()
            threshold = str(row.get("threshold", "")).strip()
            phase_type = str(row.get("phase_type", "")).strip()
            reason = str(row.get("reason", "")).strip()
            if _placeholder(field):
                issues.append(_error(f"implementation.phase_filter.rules[{index}].field", "placeholder_value", "Phase filter field is unresolved."))
            elif field not in field_names:
                issues.append(
                    _error(
                        f"implementation.phase_filter.rules[{index}].field",
                        "unknown_symbol",
                        f"Phase filter field {field!r} is not one of the declared background fields.",
                    )
                )
            if comparison not in allowed_comparisons:
                issues.append(
                    _error(
                        f"implementation.phase_filter.rules[{index}].comparison",
                        "invalid_mode",
                        "Phase filter comparison must be one of `<`, `<=`, `>`, `>=`.",
                    )
                )
            if _placeholder(threshold):
                issues.append(_error(f"implementation.phase_filter.rules[{index}].threshold", "placeholder_value", "Phase filter threshold is unresolved."))
            elif not _numeric_literal(threshold):
                issues.append(_error(f"implementation.phase_filter.rules[{index}].threshold", "invalid_number", "Phase filter threshold must be numeric."))
            if _placeholder(phase_type):
                issues.append(
                    _error(
                        f"implementation.phase_filter.rules[{index}].phase_type",
                        "placeholder_value",
                        "Phase filter phase_type is unresolved.",
                        "Name the removed phase type, for example `negative_branch`, `charge_breaking_branch`, or `unphysical_large_field_branch`.",
                    )
                )
            if _placeholder(reason):
                issues.append(
                    _error(
                        f"implementation.phase_filter.rules[{index}].reason",
                        "placeholder_value",
                        "Phase filter reason is unresolved.",
                        "Explain why this phase should be discarded rather than kept as a physical phase.",
                    )
                )


def _validate_phasetracer_symmetry(
    symmetry: dict[str, Any],
    field_names: set[str],
    issues: list[ValidationIssue],
) -> None:
    mode = str(symmetry.get("mode", "")).strip()
    if _placeholder(mode):
        issues.append(
            _error(
                "implementation.symmetry.mode",
                "placeholder_value",
                "PhaseTracer symmetry policy is unresolved.",
                "Ask the user to confirm the PhaseTracer apply_symmetry policy. Recommend `none` unless the user wants PhaseTracer to merge reviewed symmetry-equivalent field points; otherwise use `z2_reflection` with reviewed field sign-flip rows and explain the reason.",
            )
        )
        return
    _validate_contract_choice("implementation.symmetry.mode", mode, PHASETRACER_SYMMETRY_MODES, issues)
    rules = _list(symmetry.get("rules"))
    if mode == "none":
        if rules:
            issues.append(
                _error(
                    "implementation.symmetry.rules",
                    "contract_conflict",
                    "PhaseTracer symmetry rules are enabled while mode is none.",
                    "Either set mode to `z2_reflection` or disable/remove the symmetry rule rows.",
                )
            )
        return
    if mode == "z2_reflection" and not rules:
        issues.append(
            _error(
                "implementation.symmetry.rules",
                "missing_value",
                "PhaseTracer z2_reflection mode requires at least one reviewed symmetry rule.",
                "List the field or simultaneous fields flipped by each Z2 symmetry, for example `s` or `h,s`.",
            )
        )
        return
    for index, row in enumerate(rules):
        if not isinstance(row, dict):
            issues.append(_error(f"implementation.symmetry.rules[{index}]", "invalid_row", "PhaseTracer symmetry rule must be a table row."))
            continue
        fields_text = str(row.get("fields", "")).strip()
        transformation = str(row.get("transformation", "")).strip()
        reason = str(row.get("reason", "")).strip()
        if _placeholder(fields_text):
            issues.append(_error(f"implementation.symmetry.rules[{index}].fields", "placeholder_value", "PhaseTracer symmetry fields are unresolved."))
        else:
            for field in _split_symmetry_fields(fields_text):
                if field not in field_names:
                    issues.append(
                        _error(
                            f"implementation.symmetry.rules[{index}].fields",
                            "unknown_symbol",
                            f"PhaseTracer symmetry field {field!r} is not one of the declared background fields.",
                        )
                    )
        if transformation != "sign_flip":
            issues.append(
                _error(
                    f"implementation.symmetry.rules[{index}].transformation",
                    "invalid_mode",
                    "PhaseTracer symmetry transformation must be `sign_flip`.",
                )
            )
        if _placeholder(reason):
            issues.append(
                _error(
                    f"implementation.symmetry.rules[{index}].reason",
                    "placeholder_value",
                    "PhaseTracer symmetry rule reason is unresolved.",
                    "Explain the reviewed symmetry, for example a Z2 field reflection.",
                )
            )


def _split_symmetry_fields(fields_text: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,;]", fields_text) if item.strip()]


def _validate_contract_choice(
    field_key: str,
    value: str,
    allowed: set[str],
    issues: list[ValidationIssue],
) -> None:
    if _unresolved_contract_choice(value):
        return
    if value not in allowed:
        issues.append(_error(field_key, "invalid_mode", f"Value must be one of {sorted(allowed)}."))


def _unresolved_contract_choice(value: Any) -> bool:
    text = str(value or "").strip()
    return _placeholder(text) or text.lower() in MISSING_MODEL_CARD_VALUES


def contract_to_model_ir(contract: dict[str, Any], *, source_path: str = "") -> ModelIR:
    params = _dict(contract.get("parameters"))
    model_card = _dict(contract.get("model_card"))
    implementation = _dict(contract.get("implementation"))
    implementation_goldstone = _dict(implementation.get("goldstone"))
    implementation_daisy = _dict(implementation.get("daisy"))
    loops = _dict(contract.get("loops"))
    zero = _dict(loops.get("zero_temperature"))
    thermal = _dict(loops.get("thermal"))
    daisy = _dict(loops.get("daisy"))
    fields = [
        FieldDefinition(
            latex_symbol=str(row.get("latex", row.get("name", ""))),
            program_symbol=str(row.get("name", "")),
            role=str(row.get("role", "background")),
        )
        for row in _list(contract.get("fields"))
    ]
    public_inputs = [
        ParameterDefinition(
            latex_symbol=str(row.get("latex", row.get("name", ""))),
            program_symbol=str(row.get("name", "")),
            value=str(row.get("default", "")),
            role=str(row.get("description", "public input")),
        )
        for row in _list(params.get("public_inputs"))
    ]
    constants = [
        ParameterDefinition(
            latex_symbol=str(row.get("latex", row.get("name", ""))),
            program_symbol=str(row.get("name", "")),
            value=str(row.get("value", "")),
            role=str(row.get("description", "constant")),
        )
        for row in _list(params.get("constants"))
    ]
    derived = [
        {
            "latex_symbol": str(row.get("latex", row.get("name", ""))),
            "program_symbol": str(row.get("name", "")),
            "expression": str(row.get("expr", "")),
            "notes": str(row.get("description", "")),
        }
        for row in _list(params.get("derived"))
    ]
    review = _dict(contract.get("review"))
    mode = "simplified_effective_potential"
    if zero.get("mode") in {"standard_CW_V1", "paper_os_like_V1"} or thermal.get("mode") == "standard_thermal_integrals":
        mode = "full_one_loop"
    masses = _dict(contract.get("masses"))
    return ModelIR(
        model_name=str(contract.get("model_name") or contract.get("model_short_name") or "model"),
        paper_id=str(contract.get("paper_id", "")),
        source_path=source_path,
        review_status="approved" if review.get("approved") is True else "needs_review",
        implementation_mode=mode,
        background_fields=fields,
        physical_fields=fields,
        parameters=[*public_inputs, *constants],
        input_parameters=public_inputs,
        fixed_parameters=constants,
        derived_parameters=derived,
        masses={
            "scalar": json.dumps(
                {
                    "direct_species": _list(masses.get("bosons")),
                    "matrices": _list(masses.get("boson_matrices")),
                },
                ensure_ascii=False,
            ),
            "fermion": json.dumps(_list(masses.get("fermions")), ensure_ascii=False),
            "vector": "",
        },
        potentials=PotentialPieces(
            V0=str(_dict(_dict(contract.get("potential")).get("V0")).get("python", "")),
            V1=str(zero.get("custom_expr", "")) if zero.get("mode") == "custom_expr" else str(zero.get("mode", "none")),
            V_T=str(thermal.get("custom_expr", "")) if thermal.get("mode") == "custom_expr" else str(thermal.get("mode", "none")),
            V_daisy=str(daisy.get("custom_expr", "")) if daisy.get("mode") == "custom_expr" else str(daisy.get("mode", "none")),
        ),
        zero_temp_loop=ZeroTempLoopInfo(
            representation=str(zero.get("mode", "none")),
            counterterm_status=str(model_card.get("counterterm", "none")),
            goldstone_prescription=str(model_card.get("goldstone", "none")),
            goldstone_applies_to=", ".join(
                item
                for item in [
                    f"CW:{implementation_goldstone.get('cw_policy', '')}",
                    f"CT:{implementation_goldstone.get('ct_derivative_policy', '')}",
                    f"thermal:{implementation_goldstone.get('thermal_policy', '')}",
                ]
                if not item.endswith(":")
            ),
        ),
        daisy=DaisyInfo(scheme=str(implementation_daisy.get("scheme") or model_card.get("resummation_scheme") or "None")),
        notes=str(review.get("notes", "")),
    )


def compile_contract_template(
    markdown_text: str,
    settings: Settings,
    *,
    output_dir: Path | None = None,
    output_path: Path | None = None,
    run_import_check: bool = True,
) -> CompileResult:
    validation = validate_contract_template(markdown_text, review_required=True, compile_backend="cosmotransitions")
    if not validation.ok:
        preview = "; ".join(f"{issue.code}: {issue.message}" for issue in validation.issues[:5])
        raise CompileBlocked("Template contract is not compile-ready: " + preview)
    contract = validation.contract
    model_path = output_path or _candidate_model_path(contract, settings, output_dir=output_dir)
    model_path.parent.mkdir(parents=True, exist_ok=True)
    model_path.write_text(
        _render_cosmotransitions_source(
            contract,
            template_sha256=_sha256_text(markdown_text),
            contract_sha256=_sha256_contract(contract),
        ),
        encoding="utf-8",
    )
    warnings: list[str] = []
    status = "compiled"
    if run_import_check:
        from .runner import import_check

        ok, message = import_check(model_path, settings)
        status = message
        if not ok:
            raise CompileBlocked(f"Compiled model failed local import/smoke check: {message}")
        for line in message.splitlines():
            if line.startswith("WARNING:"):
                warnings.append(line.removeprefix("WARNING:").strip())
    return CompileResult(
        model_path=model_path,
        import_check_status=status,
        warnings=warnings,
        usage_instructions=compile_usage_instructions(model_path, "cosmotransitions"),
    )


def _render_cosmotransitions_source(
    contract: dict[str, Any],
    *,
    template_sha256: str = "",
    contract_sha256: str = "",
) -> str:
    fields = _list(contract.get("fields"))
    params = _dict(contract.get("parameters"))
    public_inputs = _list(params.get("public_inputs"))
    constants = _list(params.get("constants"))
    derived = _list(params.get("derived"))
    masses = _dict(contract.get("masses"))
    bosons = _list(masses.get("bosons"))
    boson_matrices = _list(masses.get("boson_matrices"))
    fermions = _list(masses.get("fermions"))
    excluded_species = _list(masses.get("excluded_species"))
    potential = _dict(contract.get("potential"))
    model_card = _dict(contract.get("model_card"))
    implementation = _dict(contract.get("implementation"))
    implementation_daisy = _dict(implementation.get("daisy"))
    implementation_counterterms = _dict(implementation.get("counterterms"))
    v0_expr = str(_dict(potential.get("V0")).get("python", "0.0"))
    loops = _dict(contract.get("loops"))
    zero = _dict(loops.get("zero_temperature"))
    thermal = _dict(loops.get("thermal"))
    daisy = _dict(loops.get("daisy"))
    counterterm_mode = str(model_card.get("counterterm", "none")).strip() or "none"
    cosmotransitions_import = (
        "from cosmoTransitions import finiteT, generic_potential"
        if thermal.get("mode") == "standard_thermal_integrals"
        else "from cosmoTransitions import generic_potential"
    )
    input_names = [str(row["name"]) for row in public_inputs]
    init_args = ", ".join(f"{name}={_float_literal(row.get('default'))}" for name, row in zip(input_names, public_inputs))
    init_signature = f", {init_args}" if init_args else ""
    build_signature = init_args
    build_options_signature = f"{build_signature}, *, run_tc=True, print_tc=True" if build_signature else "*, run_tc=True, print_tc=True"
    build_call_args = ", ".join(f"{name}={name}" for name in input_names)
    input_assignments = "\n".join(f"        self.{name} = float({name})" for name in input_names)
    constant_assignments = "\n".join(
        f"        self.{row['name']} = float({_float_literal(row.get('value'))})" for row in constants
    )
    zero_t_point_expr = _render_zero_t_point(fields)
    fermion_mass_exprs = [str(row.get("mass_sq", "0.0")) for row in fermions]
    matrix_entry_exprs = _matrix_entry_expressions(boson_matrices)
    bosons_for_mass = _cosmotransitions_bosons_for_mass(bosons, implementation_daisy, model_card)
    direct_boson_exprs = [str(row.get("mass_sq", "0.0")) for row in bosons_for_mass]
    derived_assignments = _render_derived_assignments(derived, public_inputs, constants)
    field_unpack = _render_field_unpack(fields)
    v0_return = _render_compiler_return(v0_expr)
    vacuum_bindings = _render_local_bindings(
        public_inputs,
        constants,
        derived,
        used_names=_names_in_expressions([zero_t_point_expr]),
    )
    v0_bindings = _render_local_bindings(
        public_inputs,
        constants,
        derived,
        used_names=_names_in_expressions([v0_expr]),
    )
    v0_temperature_binding = "        T = 0.0\n" if "T" in _names_in_expressions([v0_expr]) else ""
    boson_bindings = _render_local_bindings(
        public_inputs,
        constants,
        derived,
        used_names=_names_in_expressions([*direct_boson_exprs, *matrix_entry_exprs]),
    )
    fermion_bindings = _render_local_bindings(
        public_inputs,
        constants,
        derived,
        used_names=_names_in_expressions(fermion_mass_exprs),
    )
    fermion_temperature_binding = "        T = 0.0\n" if "T" in _names_in_expressions(fermion_mass_exprs) else ""
    vtot_bindings = _render_local_bindings(
        public_inputs,
        constants,
        derived,
        used_names=_names_in_expressions(_loop_custom_expressions(zero, thermal)),
    )
    boson_names = [str(row.get("name", "")) for row in bosons_for_mass] + _matrix_eigen_names(boson_matrices)
    boson_dof = ", ".join([str(row.get("dof", 1)) for row in bosons_for_mass] + _matrix_repeated_values(boson_matrices, "dof_per_eigenvalue", "1"))
    boson_c = ", ".join([str(row.get("c", 1.5)) for row in bosons_for_mass] + _matrix_repeated_values(boson_matrices, "c", "1.5"))
    fermion_dof = ", ".join(str(row.get("dof", 1)) for row in fermions)
    boson_mass_body = _render_boson_mass_body(bosons_for_mass, boson_matrices)
    fermion_mass_body = _render_fermion_mass_body(fermions)
    implementation_phase_filter = _dict(implementation.get("phase_filter"))
    resummation_scheme = str(implementation_daisy.get("scheme") or model_card.get("resummation_scheme") or "")
    runtime_assignments = _render_runtime_assignments(public_inputs, constants, derived)
    vtot_lines = _render_vtot_lines(
        zero,
        thermal,
        daisy,
        counterterm_mode=counterterm_mode,
        parwani_zero_loop=resummation_scheme == "Parwani",
    )
    v1t_from_x_lines = _render_v1t_from_x_lines(
        zero,
        thermal,
        daisy,
        fields,
        public_inputs,
        constants,
        derived,
        parwani_zero_loop=resummation_scheme == "Parwani",
    )
    v1t_method = _render_safe_v1t_method(thermal)
    v1_method = _render_v1_method(zero, counterterm_mode=counterterm_mode)
    os_like_helper = _render_os_like_helper(enabled=bool(v1_method.strip()))
    daisy_method = _render_daisy_method(
        daisy,
        implementation_daisy,
        fields,
        public_inputs,
        constants,
        derived,
    )
    counterterm_methods = _render_counterterm_methods(
        implementation_counterterms,
        _dict(implementation.get("goldstone")),
        fields,
        public_inputs,
        constants,
        derived,
        counterterm_mode=counterterm_mode,
    )
    phase_filter_method = _render_phase_filter_method(
        implementation_phase_filter,
        fields,
        public_inputs,
        constants,
        derived,
    )
    metadata = {
        "contract_schema": CONTRACT_SCHEMA,
        "artifact_backend": "cosmotransitions",
        "paper_id": contract.get("paper_id", ""),
        "model_name": contract.get("model_name", ""),
        "model_short_name": contract.get("model_short_name", ""),
        "input_parameters": {name: str(row.get("description", "")) for name, row in zip(input_names, public_inputs)},
        "input_test_values": {name: str(row.get("test_value", row.get("default", ""))) for name, row in zip(input_names, public_inputs)},
        "input_confirmed": {name: str(row.get("confirmed", "")) for name, row in zip(input_names, public_inputs)},
        "default_parameter_sources": {
            name: str(row.get("source_type", "user_supplied")) for name, row in zip(input_names, public_inputs)
        },
        "field_order": [str(row.get("name", "")) for row in fields],
        "model_card": model_card,
        "implementation_contract": implementation,
        "phase_filter": implementation_phase_filter,
        "loop_modes": loops,
        "excluded_species": excluded_species,
        "species_metadata": {
            "bosons": [
                {
                    "name": str(row.get("name", "")),
                    "usage": str(row.get("usage", "")),
                    "thermal_policy": str(row.get("thermal_policy", "")),
                    "vacuum_anchor": str(row.get("vacuum_anchor", "")),
                }
                for row in bosons
            ],
            "fermions": [
                {
                    "name": str(row.get("name", "")),
                    "usage": str(row.get("usage", "")),
                    "thermal_policy": str(row.get("thermal_policy", "")),
                    "vacuum_anchor": str(row.get("vacuum_anchor", "")),
                }
                for row in fermions
            ],
        },
    }
    default_parameter_sources = metadata["default_parameter_sources"]
    default_input_values = {name: float(_float_literal(row.get("default"))) for name, row in zip(input_names, public_inputs)}
    source = f'''
from __future__ import annotations

import numpy as np
{cosmotransitions_import}


MODEL_METADATA = {json.dumps(metadata, ensure_ascii=False, indent=2)}
PTAGENT_TEMPLATE_SHA256 = "{template_sha256}"
PTAGENT_CONTRACT_SHA256 = "{contract_sha256}"
INPUT_PARAMETERS = {input_names!r}
DEFAULT_PARAMETER_SOURCES = {default_parameter_sources!r}
DEFAULT_INPUT_VALUES = {default_input_values!r}
BOSON_NAMES = {boson_names!r}
FERMION_NAMES = {[str(row.get("name", "")) for row in fermions]!r}


def _ptagent_jsonable(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {{str(key): _ptagent_jsonable(item) for key, item in value.items()}}
    if isinstance(value, (list, tuple)):
        return [_ptagent_jsonable(item) for item in value]
    return value


def _ptagent_transition_payload(trans):
    payload = _ptagent_jsonable(dict(trans))
    if "Tcrit" in payload and "Tc" not in payload:
        payload["Tc"] = payload["Tcrit"]
    return payload


def _ptagent_print_tc_transitions(TcTrans):
    print("PTAGENT_TCTRANS_COUNT", len(TcTrans))
    for index, trans in enumerate(TcTrans):
        payload = _ptagent_transition_payload(trans)
        print("PTAGENT_TCTRANS", index, "Tcrit", payload.get("Tcrit"))
        print("PTAGENT_TCTRANS", index, "low_vev", payload.get("low_vev"))
        print("PTAGENT_TCTRANS", index, "high_vev", payload.get("high_vev"))
        print("PTAGENT_TCTRANS", index, "Delta_rho", payload.get("Delta_rho"))


def _ptagent_run_tc(model, *, print_tc=True):
    try:
        TcTrans = model.calcTcTrans()
    except Exception as exc:
        model.PTAGENT_TC_TRANS = []
        model.PTAGENT_TC_STATUS = f"error: {{type(exc).__name__}}: {{exc}}"
        if print_tc:
            print("PTAGENT_TC_STATUS", model.PTAGENT_TC_STATUS)
        return []
    if TcTrans is None:
        TcTrans = []
    model.PTAGENT_TC_TRANS = TcTrans
    model.PTAGENT_TC_STATUS = f"success: {{len(TcTrans)}} critical transition(s)"
    if print_tc:
        print("PTAGENT_TC_STATUS", model.PTAGENT_TC_STATUS)
        _ptagent_print_tc_transitions(TcTrans)
    return TcTrans


def run_transitions(**parameter_overrides):
    unknown = sorted(set(parameter_overrides) - set(DEFAULT_INPUT_VALUES))
    if unknown:
        raise TypeError("Unknown public input override(s): " + ", ".join(unknown))
    parameters = dict(DEFAULT_INPUT_VALUES)
    for key, value in parameter_overrides.items():
        parameters[key] = float(value)
    model = build_model(**parameters, run_tc=False, print_tc=False)
    TcTrans = model.calcTcTrans()
    if TcTrans is None:
        TcTrans = []
    return {{
        "parameters": parameters,
        "transitions": [_ptagent_transition_payload(trans) for trans in TcTrans],
    }}


class GeneratedPotential(generic_potential.generic_potential):
    def init(self{init_signature}):
        self.Ndim = {len(fields)}
        self.x_eps = 0.001
        self.Tmax = 1000.0
{input_assignments or "        pass"}
{constant_assignments}
        self._update_derived()
{runtime_assignments}
{("        self._solve_counterterms()" if counterterm_mode == "explicit_linear_system" else "")}

    def _update_derived(self):
{derived_assignments or "        pass"}

    def _zeroT_vacuum_point(self):
{vacuum_bindings}
        return np.array([{zero_t_point_expr}], dtype=float)

    def approxZeroTMin(self):
        return [self._zeroT_vacuum_point()]

{phase_filter_method}

    def _fields(self, X):
        X = np.asanyarray(X, dtype=float)
        if X.shape[-1] != self.Ndim:
            raise ValueError(f"Expected final field axis {{self.Ndim}}, got {{X.shape[-1]}}")
{field_unpack}
        return ({", ".join(["X"] + [str(row["name"]) for row in fields])})

    def _stack_species(self, values, base):
        if not values:
            return np.zeros(base.shape + (0,), dtype=float)
        arrays = np.broadcast_arrays(*[np.asanyarray(value, dtype=float) + 0.0 * base for value in values])
        return np.stack(arrays, axis=-1)

{daisy_method}
{os_like_helper}
{v1t_method}
{v1_method}
{counterterm_methods}

    def V0(self, X):
        {", ".join(["X"] + [str(row["name"]) for row in fields])} = self._fields(X)
{v0_temperature_binding.rstrip()}
{v0_bindings}
{v0_return}

    def boson_massSq(self, X, T):
        {", ".join(["X"] + [str(row["name"]) for row in fields])} = self._fields(X)
        T = np.asanyarray(T, dtype=float)
        base = np.asanyarray(X[..., 0], dtype=float) * 0.0 + T * 0.0
{boson_bindings}
{boson_mass_body}
        dof = np.array([{boson_dof}], dtype=float)
        c = np.array([{boson_c}], dtype=float)
        return massSq, dof, c

    def fermion_massSq(self, X):
        {", ".join(["X"] + [str(row["name"]) for row in fields])} = self._fields(X)
{fermion_temperature_binding.rstrip()}
        base = np.asanyarray(X[..., 0], dtype=float) * 0.0
{fermion_bindings}
{fermion_mass_body}
        dof = np.array([{fermion_dof}], dtype=float)
        return massSq, dof

    def Vtot(self, X, T, include_radiation=True):
        {", ".join(["X"] + [str(row["name"]) for row in fields])} = self._fields(X)
        T = np.asanyarray(T, dtype=float)
{vtot_bindings}
{vtot_lines}

    def V1T_from_X(self, X, T, include_radiation=True):
{v1t_from_x_lines}


def build_model({build_options_signature}):
    model = GeneratedPotential({build_call_args})
    model.PTAGENT_TC_TRANS = []
    model.PTAGENT_TC_STATUS = "not_run"
    if run_tc:
        _ptagent_run_tc(model, print_tc=print_tc)
    return model


if __name__ == "__main__":
    build_model()
'''
    return _cleanup_generated_source(textwrap.dedent(source).strip() + "\n")


def _cleanup_generated_source(source: str) -> str:
    cleaned = source.strip() + "\n"
    for _iteration in range(12):
        try:
            tree = ast.parse(cleaned)
        except SyntaxError:
            return cleaned
        lines = cleaned.splitlines()
        remove_lines: set[int] = set()
        replace_lines: dict[int, str] = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            for stmt in node.body:
                if _is_unused_local_assignment(node, stmt):
                    end_lineno = getattr(stmt, "end_lineno", stmt.lineno)
                    remove_lines.update(range(stmt.lineno, end_lineno + 1))
                    continue
                replacement = _rewrite_unused_tuple_assignment(node, stmt, lines)
                if replacement is not None:
                    replace_lines[stmt.lineno] = replacement
        if not remove_lines and not replace_lines:
            return cleaned
        cleaned_lines = []
        for index, line in enumerate(lines, start=1):
            if index in remove_lines:
                continue
            cleaned_lines.append(replace_lines.get(index, line))
        cleaned = "\n".join(cleaned_lines).strip() + "\n"
    return cleaned


def _is_unused_local_assignment(function: ast.FunctionDef, stmt: ast.stmt) -> bool:
    if not isinstance(stmt, ast.Assign) or len(stmt.targets) != 1:
        return False
    target = stmt.targets[0]
    if not isinstance(target, ast.Name):
        return False
    name = target.id
    if name.startswith("__"):
        return False
    return not _name_is_read_after(function, name, stmt.lineno)


def _rewrite_unused_tuple_assignment(function: ast.FunctionDef, stmt: ast.stmt, lines: list[str]) -> str | None:
    if not isinstance(stmt, ast.Assign) or len(stmt.targets) != 1:
        return None
    target = stmt.targets[0]
    if not isinstance(target, ast.Tuple):
        return None
    if getattr(stmt, "end_lineno", stmt.lineno) != stmt.lineno:
        return None
    changed = False
    for element in target.elts:
        if not isinstance(element, ast.Name):
            return None
        if element.id == "_" or element.id.startswith("__"):
            continue
        if not _name_is_read_after(function, element.id, stmt.lineno):
            element.id = "_"
            changed = True
    if not changed:
        return None
    original = lines[stmt.lineno - 1]
    indent = original[: len(original) - len(original.lstrip())]
    return f"{indent}{ast.unparse(stmt)}"


def _name_is_read_after(function: ast.FunctionDef, name: str, lineno: int) -> bool:
    for node in ast.walk(function):
        if getattr(node, "lineno", 0) <= lineno:
            continue
        if isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, ast.Load):
            return True
    return False


def _render_derived_assignments(
    derived: list[dict[str, Any]],
    public_inputs: list[dict[str, Any]],
    constants: list[dict[str, Any]],
) -> str:
    lines = []
    needed_names = _names_in_expressions([str(row.get("expr", "")) for row in derived])
    for row in public_inputs:
        name = str(row["name"])
        if name in needed_names:
            lines.append(f"        {name} = self.{name}")
    for row in constants:
        name = str(row["name"])
        if name in needed_names:
            lines.append(f"        {name} = self.{name}")
    for row in derived:
        name = str(row["name"])
        expr = str(row["expr"])
        lines.extend(_render_compiler_assignment(expr, f"self.{name}"))
        if name in needed_names:
            lines.append(f"        {name} = self.{name}")
    return "\n".join(lines)


def _render_runtime_assignments(
    public_inputs: list[dict[str, Any]],
    constants: list[dict[str, Any]],
    derived: list[dict[str, Any]],
) -> str:
    names = {str(row.get("name", "")) for row in [*public_inputs, *constants, *derived]}
    lines: list[str] = []
    if "Qren" in names:
        lines.append("        self.renormScaleSq = float(self.Qren**2)")
    for name in ("num_boson_dof", "num_fermion_dof"):
        if name in names:
            lines.append(f"        self.{name} = int(round(float(self.{name})))")
    return "\n".join(lines)


def _render_field_unpack(fields: list[dict[str, Any]]) -> str:
    return "\n".join(f"        {row['name']} = X[..., {index}]" for index, row in enumerate(fields))


def _render_zero_t_point(fields: list[dict[str, Any]]) -> str:
    values = [str(row.get("zeroT_default", "0.0")).strip() or "0.0" for row in fields]
    return ", ".join(values)


def _render_local_bindings(
    public_inputs: list[dict[str, Any]],
    constants: list[dict[str, Any]],
    derived: list[dict[str, Any]],
    *,
    used_names: set[str] | None = None,
) -> str:
    rows = [*public_inputs, *constants, *derived]
    if used_names is not None:
        rows = [row for row in rows if str(row.get("name", "")) in used_names]
    return "\n".join(f"        {row['name']} = self.{row['name']}" for row in rows)


def _names_in_expressions(expressions: list[str]) -> set[str]:
    names: set[str] = set()
    for expr in expressions:
        try:
            setup, result, assigned = _compiler_block_ast(str(expr or "0.0"))
        except (SyntaxError, ValueError):
            continue
        for stmt in setup:
            names.update(node.id for node in ast.walk(stmt.value) if isinstance(node, ast.Name))
        names.update(node.id for node in ast.walk(result) if isinstance(node, ast.Name))
        names.difference_update(assigned)
    names.discard("np")
    names.difference_update(_BARE_FUNCTIONS)
    return names


def _compiler_block_parts(expr: str) -> tuple[list[str], str]:
    setup, result, _assigned = _compiler_block_ast(expr)
    return [ast.unparse(stmt) for stmt in setup], ast.unparse(result)


def _compiler_block_ast(expr: str) -> tuple[list[ast.Assign], ast.expr, set[str]]:
    text = str(expr or "0.0").strip()
    if not text:
        text = "0.0"
    try:
        tree = ast.parse(text, mode="eval")
        return [], tree.body, set()
    except SyntaxError as eval_error:
        try:
            module = ast.parse(text, mode="exec")
        except SyntaxError:
            raise eval_error
    if not module.body:
        raise ValueError("Compiler block is empty.")
    final = module.body[-1]
    setup: list[ast.Assign] = []
    assigned: set[str] = set()
    body = module.body[:-1]
    if isinstance(final, ast.Assign):
        if len(final.targets) != 1 or not isinstance(final.targets[0], ast.Name):
            raise ValueError("Final compiler-block assignment must have one simple name target.")
        body = module.body
        result: ast.expr = ast.Name(id=final.targets[0].id, ctx=ast.Load())
    elif isinstance(final, ast.Expr):
        result = final.value
    else:
        raise ValueError("Compiler block must end with a final expression or simple assignment.")
    for stmt in body:
        if not isinstance(stmt, ast.Assign) or len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
            raise ValueError("Compiler blocks may contain only simple `name = expression` assignments before the final value.")
        setup.append(stmt)
        assigned.add(stmt.targets[0].id)
    return setup, result, assigned


def _render_compiler_assignment(expr: str, target: str, *, indent: str = "        ") -> list[str]:
    setup, result = _compiler_block_parts(expr)
    return [f"{indent}{line}" for line in setup] + [f"{indent}{target} = {result}"]


def _render_compiler_accumulation(expr: str, target: str = "y", *, indent: str = "        ") -> list[str]:
    setup, result = _compiler_block_parts(expr)
    return [f"{indent}{line}" for line in setup] + [f"{indent}{target} = {target} + ({result})"]


def _render_compiler_return(expr: str, *, indent: str = "        ") -> str:
    setup, result = _compiler_block_parts(expr)
    return "\n".join([f"{indent}{line}" for line in setup] + [f"{indent}return {result}"])


def _append_blank(lines: list[str]) -> None:
    if lines and lines[-1] != "":
        lines.append("")


def _render_expression_variables(expressions: list[str], prefix: str, *, indent: str = "        ") -> tuple[list[str], list[str]]:
    lines: list[str] = []
    variables: list[str] = []
    for index, expr in enumerate(expressions):
        var = f"{prefix}{index + 1}"
        lines.extend(_render_compiler_assignment(expr, var, indent=indent))
        variables.append(var)
    return lines, variables


def _matrix_entry_expressions(matrices: list[dict[str, Any]]) -> list[str]:
    expressions: list[str] = []
    for matrix in matrices:
        for row in _list(matrix.get("matrix")):
            expressions.extend(str(expr) for expr in _list(row))
    return expressions


def _loop_custom_expressions(*loop_rows: dict[str, Any]) -> list[str]:
    return [
        str(row.get("custom_expr", "0.0"))
        for row in loop_rows
        if str(row.get("mode", "")) == "custom_expr"
    ]


def _cosmotransitions_bosons_for_mass(
    bosons: list[dict[str, Any]],
    implementation_daisy: dict[str, Any],
    model_card: dict[str, Any],
) -> list[dict[str, Any]]:
    scheme = str(implementation_daisy.get("scheme") or model_card.get("resummation_scheme") or "").strip().lower()
    if scheme != "parwani":
        return bosons

    marker_values = {"", "same_as_zeroT", "parwani_replacement_only", "not_applicable", "none"}
    thermal_by_name: dict[str, str] = {}
    for raw_row in _list(implementation_daisy.get("particles")):
        row = _dict(raw_row)
        if str(row.get("enabled", "true")).strip().lower() == "false":
            continue
        name = str(row.get("name", "")).strip()
        thermal_expr = str(row.get("thermal_mass_sq", "")).strip()
        if not name or thermal_expr in marker_values:
            continue
        owner = str(row.get("implementation_owner", "")).strip()
        if owner and owner != "parwani_thermal_masses":
            continue
        thermal_by_name[name] = thermal_expr

    if not thermal_by_name:
        return bosons

    result: list[dict[str, Any]] = []
    for raw_row in bosons:
        row = dict(raw_row)
        name = str(row.get("name", "")).strip()
        if name in thermal_by_name:
            row["mass_sq"] = thermal_by_name[name]
        result.append(row)
    return result


def _render_boson_mass_body(bosons: list[dict[str, Any]], matrices: list[dict[str, Any]]) -> str:
    lines = ["        mass_parts = []"]
    direct_vars: list[str] = []
    for index, row in enumerate(bosons):
        name = _program_symbol(str(row.get("name", f"boson_{index + 1}")))
        var = f"m2_{name}"
        lines.extend(_render_compiler_assignment(str(row.get("mass_sq", "0.0")), var))
        direct_vars.append(var)
    if direct_vars:
        _append_blank(lines)
        lines.append(f"        mass_parts.append(self._stack_species([{', '.join(direct_vars)}], base))")
    if direct_vars and matrices:
        _append_blank(lines)
    for index, matrix in enumerate(matrices):
        if index:
            _append_blank(lines)
        basis = _list(matrix.get("basis"))
        entries = _list(matrix.get("matrix"))
        size = len(basis)
        var = f"M_{_program_symbol(str(matrix.get('name', f'matrix_{index}')))}"
        lines.append(f"        {var} = np.empty(base.shape + ({size}, {size}), dtype=float)")
        for row_index, row in enumerate(entries):
            for col_index, expr in enumerate(_list(row)):
                lines.extend(_render_compiler_assignment(str(expr), f"{var}[..., {row_index}, {col_index}]"))
        lines.append(f"        mass_parts.append(np.linalg.eigvalsh({var}))")
    if direct_vars or matrices:
        _append_blank(lines)
        lines.append("        massSq = np.concatenate(mass_parts, axis=-1)")
    else:
        lines.append("        massSq = np.zeros(base.shape + (0,), dtype=float)")
    return "\n".join(lines)


def _render_fermion_mass_body(fermions: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    mass_vars: list[str] = []
    for index, row in enumerate(fermions):
        name = _program_symbol(str(row.get("name", f"fermion_{index + 1}")))
        var = f"m2_{name}"
        lines.extend(_render_compiler_assignment(str(row.get("mass_sq", "0.0")), var))
        mass_vars.append(var)
    if mass_vars:
        lines.append(f"        massSq = self._stack_species([{', '.join(mass_vars)}], base)")
    else:
        lines.append("        massSq = np.zeros(base.shape + (0,), dtype=float)")
    return "\n".join(lines)


def _render_counterterm_methods(
    counterterms: dict[str, Any],
    goldstone: dict[str, Any],
    fields: list[dict[str, Any]],
    public_inputs: list[dict[str, Any]],
    constants: list[dict[str, Any]],
    derived: list[dict[str, Any]],
    *,
    counterterm_mode: str,
) -> str:
    if counterterm_mode != "explicit_linear_system":
        return ""
    basis = _list(counterterms.get("basis"))
    conditions = _list(counterterms.get("conditions"))
    if not basis or not conditions:
        return ""

    field_names = [str(row.get("name", "")) for row in fields]
    coeff_names = [str(row.get("coefficient", f"ct_{index}")) for index, row in enumerate(basis)]
    basis_exprs = [str(row.get("operator_expr", "0.0")) for row in basis]
    goldstone_species = _list(goldstone.get("species"))
    goldstone_exprs = [str(row.get("mass_sq", "0.0")) for row in goldstone_species]
    goldstone_dof = [str(row.get("dof", "1.0")) for row in goldstone_species]
    goldstone_replacements = [
        str(row.get("replacement_expr") or goldstone.get("replacement_formula") or "0.0")
        for row in goldstone_species
    ]
    field_tuple = ", ".join(["X"] + field_names)
    axes_specs = [_parse_ct_operator(str(row.get("operator", "")), field_names) for row in conditions]
    point_specs = [_parse_ct_point_components(str(row.get("point", "")), len(field_names)) for row in conditions]
    target_exprs = [str(row.get("target_expr", "0.0")) for row in conditions]
    handlers = [str(row.get("goldstone_handling", "")).strip() for row in conditions]
    local_binding_exprs = [
        *basis_exprs,
        *goldstone_exprs,
        *goldstone_replacements,
        *target_exprs,
        *(component for point in point_specs for component in point),
    ]
    local_bindings = _render_local_bindings(
        public_inputs,
        constants,
        derived,
        used_names=_names_in_expressions(local_binding_exprs),
    )
    point_lines = ",\n            ".join(
        f"np.array([{', '.join(point)}], dtype=float)" for point in point_specs
    )
    basis_value_lines, basis_vars = _render_expression_variables(basis_exprs, "ct_basis_")
    goldstone_mass_lines, goldstone_mass_vars = _render_expression_variables(goldstone_exprs, "ct_goldstone_m2_")
    replacement_lines, replacement_vars = _render_expression_variables(goldstone_replacements, "ct_goldstone_replacement_")
    target_value_lines, target_vars = _render_expression_variables(target_exprs, "ct_target_")

    lines = [
        "    def VCW(self, X):",
        "        return generic_potential.generic_potential.V1(",
        "            self, self.boson_massSq(X, 0.0), self.fermion_massSq(X)",
        "        )",
        "",
        "    def _ct_basis_values(self, X):",
        f"        {field_tuple} = self._fields(X)",
        "        base = np.asanyarray(X[..., 0], dtype=float) * 0.0",
    ]
    if local_bindings:
        lines.extend(local_bindings.splitlines())
    lines.extend(basis_value_lines)
    lines.extend(
        [
            f"        return self._stack_species([{', '.join(basis_vars)}], base)",
            "",
            "    def _ct_goldstone_massSq(self, X):",
            f"        {field_tuple} = self._fields(X)",
            "        base = np.asanyarray(X[..., 0], dtype=float) * 0.0",
        ]
    )
    if local_bindings:
        lines.extend(local_bindings.splitlines())
    if goldstone_exprs:
        lines.extend(goldstone_mass_lines)
        lines.extend(replacement_lines)
        lines.extend(
            [
                f"        massSq = self._stack_species([{', '.join(goldstone_mass_vars)}], base)",
                f"        replacementSq = self._stack_species([{', '.join(replacement_vars)}], base)",
                f"        dof = np.array([{', '.join(goldstone_dof)}], dtype=float)",
                "        return massSq, dof, replacementSq",
            ]
        )
    else:
        lines.extend(
            [
                "        empty = np.zeros(base.shape + (0,), dtype=float)",
                "        return empty, np.zeros(0, dtype=float), empty",
            ]
        )
    lines.extend(
        [
            "",
            "    def _ct_goldstone_cw_for_ct(self, X):",
            "        massSq, dof, _replacementSq = self._ct_goldstone_massSq(X)",
            "        if massSq.shape[-1] == 0:",
            "            return np.zeros(np.shape(np.asanyarray(X)[..., 0]), dtype=float)",
            "        term = dof*massSq*massSq*(np.log(np.abs(massSq/self.renormScaleSq) + 1e-100) - 1.5)",
            "        return np.sum(term, axis=-1)/(64.0*np.pi*np.pi)",
            "",
            "    def _VCW_non_goldstone_for_ct(self, X):",
            "        return self.VCW(X) - self._ct_goldstone_cw_for_ct(X)",
            "",
            "    def _ct_apply_operator(self, func, point, axes):",
            "        eps = float(getattr(self, '_ct_eps', self.x_eps))",
            "        point = np.array(point, dtype=float)",
            "        axes = tuple(axes)",
            "        if len(axes) == 1:",
            "            step = np.zeros(self.Ndim, dtype=float)",
            "            step[axes[0]] = eps",
            "            return (func(point + step) - func(point - step))/(2.0*eps)",
            "        if len(axes) == 2 and axes[0] == axes[1]:",
            "            step = np.zeros(self.Ndim, dtype=float)",
            "            step[axes[0]] = eps",
            "            return (func(point + step) - 2.0*func(point) + func(point - step))/(eps*eps)",
            "        if len(axes) == 2:",
            "            step_a = np.zeros(self.Ndim, dtype=float)",
            "            step_b = np.zeros(self.Ndim, dtype=float)",
            "            step_a[axes[0]] = eps",
            "            step_b[axes[1]] = eps",
            "            return (",
            "                func(point + step_a + step_b)",
            "                - func(point + step_a - step_b)",
            "                - func(point - step_a + step_b)",
            "                + func(point - step_a - step_b)",
            "            )/(4.0*eps*eps)",
            "        raise ValueError(f'Unsupported CT operator axes: {axes!r}')",
            "",
            "    def _ct_goldstone_mass_derivatives(self, point):",
            "        massSq, _dof, _replacementSq = self._ct_goldstone_massSq(point)",
            "        if massSq.size == 0:",
            "            return np.zeros((0, self.Ndim), dtype=float)",
            "        eps = float(getattr(self, '_ct_eps', self.x_eps))",
            "        derivs = []",
            "        for axis in range(self.Ndim):",
            "            step = np.zeros(self.Ndim, dtype=float)",
            "            step[axis] = eps",
            "            plus = self._ct_goldstone_massSq(point + step)[0]",
            "            minus = self._ct_goldstone_massSq(point - step)[0]",
            "            derivs.append((plus - minus)/(2.0*eps))",
            "        return np.stack(derivs, axis=-1)",
            "",
            "    def _ct_regulated_goldstone_hessian(self, point):",
            "        massSq, dof, replacementSq = self._ct_goldstone_massSq(point)",
            "        if massSq.size == 0:",
            "            return np.zeros((self.Ndim, self.Ndim), dtype=float)",
            "        dm = self._ct_goldstone_mass_derivatives(point)",
            "        logs = np.log(np.abs(replacementSq/self.renormScaleSq) + 1e-100)",
            "        weights = dof*logs/(32.0*np.pi*np.pi)",
            "        return np.einsum('a,ai,aj->ij', weights, dm, dm)",
            "",
            "    def _ct_source_derivative(self, point, axes, goldstone_handling):",
            "        if goldstone_handling == 'regulated_replacement':",
            "            source = self._ct_apply_operator(self._VCW_non_goldstone_for_ct, point, axes)",
            "            if len(tuple(axes)) == 2:",
            "                hessian = self._ct_regulated_goldstone_hessian(point)",
            "                source = source + hessian[axes[0], axes[1]]",
            "            return source",
            "        return self._ct_apply_operator(self.VCW, point, axes)",
            "",
            "    def _solve_counterterms(self):",
            "        self._ct_eps = float(self.x_eps)",
        ]
    )
    if local_bindings:
        lines.extend(local_bindings.splitlines())
    lines.extend(target_value_lines)
    lines.extend(
        [
            "        points = [",
            f"            {point_lines}",
            "        ]",
            f"        axes = {axes_specs!r}",
            f"        handlers = {handlers!r}",
            f"        targets = np.array([{', '.join(target_vars)}], dtype=float)",
            "        basis_func = lambda Y: self._ct_basis_values(Y)",
            "        A = np.vstack([self._ct_apply_operator(basis_func, point, axis) for point, axis in zip(points, axes)])",
            "        b = np.array([",
            "            target - self._ct_source_derivative(point, axis, handler)",
            "            for point, axis, handler, target in zip(points, axes, handlers, targets)",
            "        ], dtype=float)",
            "        if A.shape[0] != A.shape[1]:",
            "            raise ValueError(f'CT linear system must be square, got {A.shape}')",
            "        if np.linalg.matrix_rank(A) < A.shape[1]:",
            "            raise ValueError('CT linear system is singular; review the counterterm basis and conditions')",
            "        coeffs = np.linalg.solve(A, b)",
            "        self._ct_coefficients = coeffs",
        ]
    )
    for index, name in enumerate(coeff_names):
        lines.append(f"        self.{name} = float(coeffs[{index}])")
    lines.extend(
        [
            "",
            "    def VCT(self, X):",
            "        basis = self._ct_basis_values(X)",
            "        return np.sum(basis*self._ct_coefficients, axis=-1)",
        ]
    )
    return "\n".join(lines)


def _matrix_eigen_names(matrices: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for matrix in matrices:
        base = _program_symbol(str(matrix.get("name", "matrix")))
        for index in range(len(_list(matrix.get("basis")))):
            names.append(f"{base}_eig{index + 1}")
    return names


def _matrix_repeated_values(matrices: list[dict[str, Any]], key: str, default: str) -> list[str]:
    values: list[str] = []
    for matrix in matrices:
        value = str(matrix.get(key, default))
        values.extend(value for _ in range(len(_list(matrix.get("basis")))))
    return values


def _numpy_boolean_node(node: ast.expr) -> ast.expr:
    """Rewrite Python boolean syntax into NumPy-safe elementwise logic."""

    class Transformer(ast.NodeTransformer):
        def visit_BoolOp(self, bool_node: ast.BoolOp) -> ast.expr:  # noqa: N802 - ast visitor API.
            values = [self.visit(value) for value in bool_node.values]
            if not values:
                return bool_node
            func_name = "logical_and" if isinstance(bool_node.op, ast.And) else "logical_or"
            current = values[0]
            for value in values[1:]:
                current = ast.Call(
                    func=ast.Attribute(value=ast.Name(id="np", ctx=ast.Load()), attr=func_name, ctx=ast.Load()),
                    args=[current, value],
                    keywords=[],
                )
            return ast.copy_location(current, bool_node)

        def visit_Compare(self, compare_node: ast.Compare) -> ast.expr:  # noqa: N802 - ast visitor API.
            left = self.visit(compare_node.left)
            comparators = [self.visit(item) for item in compare_node.comparators]
            if len(compare_node.ops) <= 1:
                return ast.copy_location(
                    ast.Compare(left=left, ops=compare_node.ops, comparators=comparators),
                    compare_node,
                )
            pairwise: list[ast.expr] = []
            current_left = left
            for op, current_right in zip(compare_node.ops, comparators, strict=False):
                pairwise.append(ast.Compare(left=current_left, ops=[op], comparators=[current_right]))
                current_left = current_right
            current = pairwise[0]
            for value in pairwise[1:]:
                current = ast.Call(
                    func=ast.Attribute(value=ast.Name(id="np", ctx=ast.Load()), attr="logical_and", ctx=ast.Load()),
                    args=[current, value],
                    keywords=[],
                )
            return ast.copy_location(current, compare_node)

    transformed = Transformer().visit(node)
    assert isinstance(transformed, ast.expr)
    ast.fix_missing_locations(transformed)
    return transformed


def _render_phase_filter_parts(expr: str) -> tuple[list[str], str]:
    setup, result, _assigned = _compiler_block_ast(expr)
    lines: list[str] = []
    for stmt in setup:
        target = stmt.targets[0]
        assert isinstance(target, ast.Name)
        value = _numpy_boolean_node(stmt.value)
        lines.append(f"{target.id} = {ast.unparse(value)}")
    return lines, ast.unparse(_numpy_boolean_node(result))


def _render_phase_filter_method(
    phase_filter: dict[str, Any],
    fields: list[dict[str, Any]],
    public_inputs: list[dict[str, Any]],
    constants: list[dict[str, Any]],
    derived: list[dict[str, Any]],
) -> str:
    mode = str(phase_filter.get("mode", "none")).strip() or "none"
    field_tuple = ", ".join(["X"] + [str(row["name"]) for row in fields])
    if mode == "none":
        body = """
        def forbidPhaseCrit(self, X):
            return False
        """
        return textwrap.indent(textwrap.dedent(body).strip("\n"), "    ")
    if mode == "custom_expr":
        expr = str(phase_filter.get("custom_expr", "False")).strip() or "False"
        local_bindings = _render_local_bindings(
            public_inputs,
            constants,
            derived,
            used_names=_names_in_expressions([expr]),
        )
        lines = [
            "def forbidPhaseCrit(self, X):",
            f"    {field_tuple} = self._fields(X)",
        ]
        if local_bindings:
            lines.extend(line[4:] if line.startswith("    ") else line for line in local_bindings.splitlines())
        setup, result = _render_phase_filter_parts(expr)
        lines.extend(f"    {line}" for line in setup)
        lines.extend(
            [
                f"    forbidden = ({result})",
                "    return bool(np.any(forbidden))",
            ]
        )
        return textwrap.indent("\n".join(lines), "    ")
    rules = _list(phase_filter.get("rules"))
    lines = [
        "def forbidPhaseCrit(self, X):",
        f"    {field_tuple} = self._fields(X)",
        "    forbidden = np.zeros(np.shape(X[..., 0]), dtype=bool)",
    ]
    for row in rules:
        field = str(_dict(row).get("field", "")).strip()
        comparison = str(_dict(row).get("comparison", "")).strip()
        threshold = _float_literal(_dict(row).get("threshold", 0.0))
        lines.append(f"    forbidden = np.logical_or(forbidden, {field} {comparison} {threshold})")
    lines.append("    return bool(np.any(forbidden))")
    return textwrap.indent("\n".join(lines), "    ")


def _render_vtot_lines(
    zero: dict[str, Any],
    thermal: dict[str, Any],
    daisy: dict[str, Any],
    *,
    counterterm_mode: str = "none",
    parwani_zero_loop: bool = False,
) -> str:
    zero_mode = zero.get("mode")
    thermal_mode = thermal.get("mode")
    need_zero_species = zero_mode in {"standard_CW_V1", "paper_os_like_V1"}
    need_thermal_species = thermal_mode == "standard_thermal_integrals"
    zero_boson_var = "bosons_T" if parwani_zero_loop else "bosons_0"
    need_bosons_0 = need_zero_species and not parwani_zero_loop
    need_bosons_T = need_thermal_species or (need_zero_species and parwani_zero_loop)
    lines = ["        y = self.V0(X)"]
    if need_bosons_0:
        _append_blank(lines)
        lines.append("        bosons_0 = self.boson_massSq(X, 0.0)")
    if need_bosons_T:
        _append_blank(lines)
        lines.append("        bosons_T = self.boson_massSq(X, T)")
    if need_zero_species or need_thermal_species:
        lines.append("        fermions = self.fermion_massSq(X)")
    if zero.get("mode") == "standard_CW_V1":
        _append_blank(lines)
        lines.append(f"        y = y + self.V1({zero_boson_var}, fermions)")
    elif zero.get("mode") == "paper_os_like_V1":
        _append_blank(lines)
        lines.append(f"        y = y + self.V1({zero_boson_var}, fermions)")
    elif zero.get("mode") == "custom_expr":
        _append_blank(lines)
        lines.extend(_render_compiler_accumulation(str(zero.get("custom_expr", "0.0"))))
    if counterterm_mode == "explicit_linear_system":
        _append_blank(lines)
        lines.append("        y = y + self.VCT(X)")
    if thermal.get("mode") == "standard_thermal_integrals":
        _append_blank(lines)
        lines.append("        y = y + self.V1T(bosons_T, fermions, T, include_radiation)")
    elif thermal.get("mode") == "custom_expr":
        _append_blank(lines)
        lines.extend(_render_compiler_accumulation(str(thermal.get("custom_expr", "0.0"))))
    if daisy.get("mode") == "custom_expr":
        _append_blank(lines)
        lines.append("        y = y + self.Vdaisy(X, T)")
    _append_blank(lines)
    lines.append("        return y")
    return "\n".join(lines)


def _render_os_like_helper(*, enabled: bool) -> str:
    if not enabled:
        return ""
    return textwrap.indent(
        textwrap.dedent(
            """
            def _os_like_species_sum(self, massSq, dof, vacuumMassSq):
                m2 = np.asanyarray(massSq, dtype=float)
                n = np.asanyarray(dof, dtype=float)
                m2v = np.asanyarray(vacuumMassSq, dtype=float)
                active = np.abs(m2v) > 1e-80
                denom = np.where(active, np.abs(m2v), 1.0)
                term = m2*m2*(np.log(np.abs(m2/denom) + 1e-100) - 1.5) + 2.0*m2*m2v
                term = np.where(active, term, 0.0)
                return np.sum(n*term, axis=-1)

            """
        ).strip("\n"),
        "    ",
    )


def _render_v1_method(zero: dict[str, Any], *, counterterm_mode: str = "none") -> str:
    if zero.get("mode") != "paper_os_like_V1" or counterterm_mode == "explicit_linear_system":
        return ""
    return textwrap.indent(
        textwrap.dedent(
            """
            def V1(self, bosons, fermions):
                X0 = self._zeroT_vacuum_point()
                bosons_vac = self.boson_massSq(X0, 0.0)
                fermions_vac = self.fermion_massSq(X0)
                y = self._os_like_species_sum(bosons[0], bosons[1], bosons_vac[0])
                y = y - self._os_like_species_sum(fermions[0], fermions[1], fermions_vac[0])
                return y/(64.0*np.pi*np.pi)

            """
        ).strip("\n"),
        "    ",
    )


def _render_safe_v1t_method(thermal: dict[str, Any]) -> str:
    if thermal.get("mode") != "standard_thermal_integrals":
        return ""
    return textwrap.indent(
        textwrap.dedent(
            """
            def V1T(self, bosons, fermions, T, include_radiation=True):
                T = np.asanyarray(T, dtype=float)
                T2 = (T*T)[..., np.newaxis] + 1e-100
                T4 = T*T*T*T
                boson_m2, boson_dof, _c = bosons
                y = np.zeros(np.shape(T), dtype=float)
                if boson_m2.shape[-1] > 0:
                    y = y + np.sum(boson_dof*finiteT.Jb_spline(boson_m2/T2), axis=-1)
                fermion_m2, fermion_dof = fermions
                if fermion_m2.shape[-1] > 0:
                    y = y + np.sum(fermion_dof*finiteT.Jf_spline(fermion_m2/T2), axis=-1)
                if include_radiation:
                    if self.num_boson_dof is not None:
                        explicit_boson_dof = np.sum(boson_dof) if boson_dof.size else 0.0
                        radiation_bosons = self.num_boson_dof - explicit_boson_dof
                        y = y - radiation_bosons * np.pi**4 / 45.0
                    if self.num_fermion_dof is not None:
                        explicit_fermion_dof = np.sum(fermion_dof) if fermion_dof.size else 0.0
                        radiation_fermions = self.num_fermion_dof - explicit_fermion_dof
                        y = y - radiation_fermions * 7.0*np.pi**4 / 360.0
                return y*T4/(2.0*np.pi*np.pi)

            """
        ).strip("\n"),
        "    ",
    )


def _render_daisy_method(
    daisy: dict[str, Any],
    implementation_daisy: dict[str, Any],
    fields: list[dict[str, Any]],
    public_inputs: list[dict[str, Any]],
    constants: list[dict[str, Any]],
    derived: list[dict[str, Any]],
) -> str:
    if daisy.get("mode") != "custom_expr":
        return ""
    structured = _render_structured_daisy_method(
        implementation_daisy,
        fields,
        public_inputs,
        constants,
        derived,
    )
    if structured:
        return structured
    split_custom = _render_split_custom_daisy_method(
        daisy,
        implementation_daisy,
        fields,
        public_inputs,
        constants,
        derived,
    )
    if split_custom:
        return split_custom
    expr = str(daisy.get("custom_expr", "0.0")).strip() or "0.0"
    field_tuple = ", ".join(["X"] + [str(row["name"]) for row in fields])
    local_bindings = _render_local_bindings(
        public_inputs,
        constants,
        derived,
        used_names=_names_in_expressions([expr]),
    )
    lines = [
        "def Vdaisy(self, X, T):",
        f"    {field_tuple} = self._fields(X)",
        "    T = np.asanyarray(T, dtype=float)",
    ]
    if local_bindings:
        lines.extend(line[4:] if line.startswith("    ") else line for line in local_bindings.splitlines())
    lines.extend(_render_compiler_assignment(expr, "y", indent="    "))
    lines.extend(["    return y", ""])
    return textwrap.indent("\n".join(lines), "    ")


def _render_split_custom_daisy_method(
    daisy: dict[str, Any],
    implementation_daisy: dict[str, Any],
    fields: list[dict[str, Any]],
    public_inputs: list[dict[str, Any]],
    constants: list[dict[str, Any]],
    derived: list[dict[str, Any]],
) -> str:
    expr = str(daisy.get("custom_expr", "0.0")).strip() or "0.0"
    split = _split_prefactored_sum(expr)
    if split is None:
        return ""
    prefactor, terms = split
    if len(terms) < 2:
        return ""
    labels = _daisy_contribution_labels(implementation_daisy, len(terms))
    deltas = [_extract_positive_part_daisy_delta(term) for term in terms]
    mass_decomposed = all(delta is not None for delta in deltas)
    cubic = ""
    if mass_decomposed:
        cubic = _render_daisy_cubic_helpers(str(implementation_daisy.get("cubic_power_policy", "")).strip())
        if not cubic:
            mass_decomposed = False
    field_tuple = ", ".join(["X"] + [str(row["name"]) for row in fields])
    expression_pool = [prefactor]
    if mass_decomposed:
        for delta in deltas:
            assert delta is not None
            expression_pool.extend([delta[1], delta[2]])
    else:
        expression_pool.extend(terms)
    local_bindings = _render_local_bindings(
        public_inputs,
        constants,
        derived,
        used_names=_names_in_expressions(expression_pool),
    )
    lines = []
    if cubic:
        lines.extend([cubic.rstrip(), ""])
    lines.extend([
        "def Vdaisy(self, X, T):",
        f"    {field_tuple} = self._fields(X)",
        "    T = np.asanyarray(T, dtype=float)",
    ])
    if local_bindings:
        lines.extend(line[4:] if line.startswith("    ") else line for line in local_bindings.splitlines())
    _append_blank(lines)
    lines.append(f"    prefactor = ({prefactor})")
    lines.append("    r = np.zeros(np.shape(np.asanyarray(X)[..., 0] * T), dtype=float)")
    if mass_decomposed:
        for label, delta in zip(labels, deltas):
            assert delta is not None
            dof, thermal_mass, zero_mass = delta
            thermal_var = f"m2T_{label}"
            zero_var = f"m2_{label}"
            contribution_var = f"daisy_{label}"
            _append_blank(lines)
            lines.append(f"    {thermal_var} = ({thermal_mass})")
            lines.append(f"    {zero_var} = ({zero_mass})")
            lines.append(f"    {contribution_var} = self._daisy_delta({thermal_var}, {zero_var}, {dof})")
            lines.append(f"    r = r + {contribution_var}")
    else:
        for label, term in zip(labels, terms):
            var = f"daisy_{label}"
            _append_blank(lines)
            lines.append(f"    {var} = ({term})")
            lines.append(f"    r = r + {var}")
    _append_blank(lines)
    lines.extend(["    return prefactor*r", ""])
    return textwrap.indent("\n".join(lines), "    ")


def _split_prefactored_sum(expr: str) -> tuple[str, list[str]] | None:
    try:
        node = ast.parse(str(expr or "0.0"), mode="eval").body
    except SyntaxError:
        return None
    prefactor_node: ast.AST | None = None
    sum_node: ast.AST = node
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        left_terms = _split_add_nodes(node.left)
        right_terms = _split_add_nodes(node.right)
        if len(right_terms) > 1:
            prefactor_node = node.left
            sum_node = node.right
        elif len(left_terms) > 1:
            prefactor_node = node.right
            sum_node = node.left
    terms = _split_add_nodes(sum_node)
    if len(terms) < 2:
        return None
    prefactor = ast.unparse(prefactor_node) if prefactor_node is not None else "1.0"
    return prefactor, [ast.unparse(term) for term in terms]


def _split_add_nodes(node: ast.AST) -> list[ast.AST]:
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return [*_split_add_nodes(node.left), *_split_add_nodes(node.right)]
    return [node]


def _daisy_contribution_labels(implementation_daisy: dict[str, Any], term_count: int) -> list[str]:
    labels: list[str] = []
    for raw_row in _list(implementation_daisy.get("particles")):
        row = _dict(raw_row)
        if str(row.get("enabled", "true")).strip().lower() == "false":
            continue
        name = _program_symbol(str(row.get("name", "term")))
        kind = str(row.get("kind", "")).strip().lower()
        if "matrix" in kind:
            labels.extend([f"{name}_eig1", f"{name}_eig2"])
        else:
            labels.append(name)
    if len(labels) != term_count:
        labels = [f"term_{index:02d}" for index in range(1, term_count + 1)]
    seen: dict[str, int] = {}
    unique: list[str] = []
    for label in labels:
        count = seen.get(label, 0) + 1
        seen[label] = count
        unique.append(label if count == 1 else f"{label}_{count}")
    return unique


def _extract_positive_part_daisy_delta(term: str) -> tuple[str, str, str] | None:
    try:
        node = ast.parse(str(term or "0.0"), mode="eval").body
    except SyntaxError:
        return None
    dof = "1.0"
    delta = node
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        if _ast_is_numeric(node.left):
            dof = ast.unparse(node.left)
            delta = node.right
        elif _ast_is_numeric(node.right):
            dof = ast.unparse(node.right)
            delta = node.left
    if not isinstance(delta, ast.BinOp) or not isinstance(delta.op, ast.Sub):
        return None
    thermal_mass = _extract_positive_part_cubic_mass(delta.left)
    zero_mass = _extract_positive_part_cubic_mass(delta.right)
    if thermal_mass is None or zero_mass is None:
        return None
    return dof, thermal_mass, zero_mass


def _extract_positive_part_cubic_mass(node: ast.AST) -> str | None:
    if not isinstance(node, ast.BinOp) or not isinstance(node.op, ast.Pow):
        return None
    if ast.unparse(node.right) not in {"1.5", "3 / 2", "3/2"}:
        return None
    call = node.left
    if not isinstance(call, ast.Call):
        return None
    func = call.func
    if not (
        isinstance(func, ast.Attribute)
        and func.attr == "maximum"
        and isinstance(func.value, ast.Name)
        and func.value.id == "np"
    ):
        return None
    if len(call.args) != 2:
        return None
    second = ast.unparse(call.args[1])
    if second not in {"0", "0.0"}:
        return None
    return ast.unparse(call.args[0])


def _ast_is_numeric(node: ast.AST) -> bool:
    try:
        value = ast.literal_eval(node)
    except (ValueError, TypeError):
        return False
    return isinstance(value, int | float)


def _render_structured_daisy_method(
    implementation_daisy: dict[str, Any],
    fields: list[dict[str, Any]],
    public_inputs: list[dict[str, Any]],
    constants: list[dict[str, Any]],
    derived: list[dict[str, Any]],
) -> str:
    particles = [
        _dict(row)
        for row in _list(implementation_daisy.get("particles"))
        if str(_dict(row).get("enabled", "true")).strip().lower() != "false"
    ]
    if not particles:
        return ""
    mass_terms: list[tuple[str, str, str, str]] = []
    for row in particles:
        name = _program_symbol(str(row.get("name", "daisy_particle")))
        zero_expr = str(row.get("zeroT_mass_sq", "")).strip()
        thermal_expr = str(row.get("thermal_mass_sq", "")).strip()
        dof = str(row.get("dof", "1.0")).strip()
        if thermal_expr == "same_as_zeroT":
            thermal_expr = zero_expr
        if zero_expr == "same_as_zeroT":
            zero_expr = thermal_expr
        if (
            not _valid_daisy_mass_expression(zero_expr)
            or not _valid_daisy_mass_expression(thermal_expr)
            or not _numeric_literal(dof)
        ):
            return ""
        mass_terms.append((name, zero_expr, thermal_expr, _float_literal(dof)))
    cubic = _render_daisy_cubic_helpers(str(implementation_daisy.get("cubic_power_policy", "")).strip())
    if not cubic:
        return ""
    all_exprs = [expr for _name, zero_expr, thermal_expr, _dof in mass_terms for expr in (zero_expr, thermal_expr)]
    field_tuple = ", ".join(["X"] + [str(row["name"]) for row in fields])
    local_bindings = _render_local_bindings(
        public_inputs,
        constants,
        derived,
        used_names=_names_in_expressions(all_exprs),
    )
    lines = [cubic.rstrip(), "", "def Vdaisy(self, X, T):", f"    {field_tuple} = self._fields(X)", "    T = np.asanyarray(T, dtype=float)"]
    if local_bindings:
        lines.extend(line[4:] if line.startswith("    ") else line for line in local_bindings.splitlines())
    _append_blank(lines)
    lines.append("    r = np.zeros(np.shape(np.asanyarray(X)[..., 0] * T), dtype=float)")
    for name, zero_expr, thermal_expr, dof in mass_terms:
        zero_name = f"m2_{name}"
        thermal_name = f"m2T_{name}"
        _append_blank(lines)
        lines.extend(_render_compiler_assignment(zero_expr, zero_name, indent="    "))
        lines.extend(_render_compiler_assignment(thermal_expr, thermal_name, indent="    "))
        lines.append(f"    r = r + self._daisy_delta({thermal_name}, {zero_name}, {dof})")
    _append_blank(lines)
    lines.extend(["    return -T*r/(12.0*np.pi)", ""])
    return textwrap.indent("\n".join(lines), "    ")


def _valid_daisy_mass_expression(expr: str) -> bool:
    text = str(expr or "").strip()
    return bool(text) and text not in {"none", *DAISY_NON_FORMULA_MARKERS} and not _placeholder(text)


def _partial_thermal_self_energy_marker(value: Any) -> str:
    text = str(value or "")
    if not text.strip():
        return ""
    normalized = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
    if not normalized:
        return ""
    if any(marker in normalized for marker in TOTAL_THERMAL_MASS_MARKERS):
        return ""
    for marker in PARTIAL_THERMAL_SELF_ENERGY_MARKERS:
        if marker in normalized:
            return marker
    return ""


def _render_daisy_cubic_helpers(policy: str) -> str:
    clean = policy.strip()
    if clean == "positive_part":
        cubic_body = "return np.maximum(m2, 0.0)**1.5"
    elif clean == "signed_abs":
        cubic_body = "return np.sign(m2)*np.abs(m2)**1.5"
    else:
        return ""
    return "\n".join(
        [
            "def _daisy_cubic(self, massSq):",
            "    m2 = np.asanyarray(massSq, dtype=float)",
            f"    {cubic_body}",
            "",
            "def _daisy_delta(self, thermalMassSq, zeroMassSq, dof):",
            "    return float(dof)*(self._daisy_cubic(thermalMassSq) - self._daisy_cubic(zeroMassSq))",
        ]
    )


def _render_v1t_from_x_lines(
    zero: dict[str, Any],
    thermal: dict[str, Any],
    daisy: dict[str, Any],
    fields: list[dict[str, Any]],
    public_inputs: list[dict[str, Any]],
    constants: list[dict[str, Any]],
    derived: list[dict[str, Any]],
    *,
    parwani_zero_loop: bool = False,
) -> str:
    field_tuple = ", ".join(["X"] + [str(row["name"]) for row in fields])
    local_bindings = _render_local_bindings(
        public_inputs,
        constants,
        derived,
        used_names=_names_in_expressions(
            [
                str(thermal.get("custom_expr", "0.0")),
            ]
        ),
    )
    zero_mode = zero.get("mode")
    need_zero_species = parwani_zero_loop and zero_mode in {"standard_CW_V1", "paper_os_like_V1"}
    need_thermal_species = thermal.get("mode") == "standard_thermal_integrals"
    need_species = need_zero_species or need_thermal_species
    lines = [
        f"        {field_tuple} = self._fields(X)",
        "        T = np.asanyarray(T, dtype=float)",
    ]
    if local_bindings:
        lines.extend(local_bindings.splitlines())
    _append_blank(lines)
    lines.append("        y = np.zeros(np.shape(np.asanyarray(X)[..., 0] * T), dtype=float)")
    if need_species:
        _append_blank(lines)
        lines.extend(
            [
                "        bosons = self.boson_massSq(X, T)",
                "        fermions = self.fermion_massSq(X)",
            ]
        )
    if need_zero_species:
        _append_blank(lines)
        lines.append("        y = y + self.V1(bosons, fermions)")
    if thermal.get("mode") == "standard_thermal_integrals":
        _append_blank(lines)
        lines.append("        y = y + self.V1T(bosons, fermions, T, include_radiation)")
    elif thermal.get("mode") == "custom_expr":
        _append_blank(lines)
        lines.extend(_render_compiler_accumulation(str(thermal.get("custom_expr", "0.0"))))
    if daisy.get("mode") == "custom_expr":
        _append_blank(lines)
        lines.append("        y = y + self.Vdaisy(X, T)")
    _append_blank(lines)
    lines.append("        return y")
    return "\n".join(lines)


def _candidate_model_path(contract: dict[str, Any], settings: Settings, *, output_dir: Path | None = None) -> Path:
    model_root = output_dir or generated_models_dir(settings.artifact_root)
    return model_root / "cosmotransitions" / _cosmotransitions_model_filename(contract)


def _cosmotransitions_model_filename(contract: dict[str, Any]) -> str:
    slug = model_artifact_slug(contract)
    return "model.py" if slug == "model" else f"{slug}_model.py"


def _auto_v0_compiler_expression(source_latex: str, fields: list[dict[str, Any]]) -> tuple[str, str]:
    latex = str(source_latex or "")
    field_names = {str(row.get("name", "")) for row in fields}
    if {"h", "s"}.issubset(field_names) and _looks_like_xsm_tree_potential(latex):
        return (
            "-0.5*mu_H_sq*h**2 + 0.25*lambda_H*h**4 - 0.5*mu_S_sq*s**2 + 0.25*lambda_S*s**4 + 0.25*lambda_HS*h**2*s**2",
            "Auto-drafted from source-exact xSM tree potential using H^dagger H -> h^2/2 and S -> s. Review this normalization if the paper uses a different background-field convention.",
        )
    return "ASK_USER", "Required tree-level potential."


def _auto_scalar_matrix_rows(
    v0_python: str,
    basis: list[str],
    *,
    scalar_mass_latex: str = "",
) -> tuple[list[list[str]], bool]:
    empty_rows = [[row, *["ASK_USER" for _ in basis]] for row in basis]
    if _placeholder(v0_python):
        return empty_rows, False
    try:
        hessian = _hessian_entries(v0_python, basis)
    except Exception:
        return empty_rows, False
    if not _source_mass_matches_hessian(scalar_mass_latex, hessian, basis):
        return empty_rows, False
    return [[row, *hessian[index]] for index, row in enumerate(basis)], True


def _hessian_entries(expr: str, basis: list[str]) -> list[list[str]]:
    import sympy as sp

    names = sorted(set(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", expr)))
    local_symbols = {name: sp.Symbol(name) for name in names}
    parsed = sp.sympify(expr, locals=local_symbols)
    fields = [local_symbols[name] for name in basis]
    rows: list[list[str]] = []
    for row_symbol in fields:
        row_entries: list[str] = []
        for col_symbol in fields:
            row_entries.append(sp.sstr(sp.simplify(sp.diff(parsed, row_symbol, col_symbol))))
        rows.append(row_entries)
    return rows


def _source_mass_matches_hessian(source_latex: str, hessian: list[list[str]], basis: list[str]) -> bool:
    source_entries = _source_mass_entry_map(source_latex, basis)
    if not source_entries:
        return False
    for row_index, row in enumerate(basis):
        for col_index, col in enumerate(basis):
            key = (row, col)
            reverse_key = (col, row)
            source_expr = source_entries.get(key) or source_entries.get(reverse_key)
            if not source_expr:
                return False
            if not _sympy_equivalent(source_expr, hessian[row_index][col_index]):
                return False
    return True


def _source_mass_entry_map(source_latex: str, basis: list[str]) -> dict[tuple[str, str], str]:
    text = str(source_latex or "").strip()
    if not text or "not detected" in text.lower():
        return {}
    text = text.replace("\\\\", ";")
    entries: dict[tuple[str, str], str] = {}
    for lhs, rhs in re.findall(r"([^=;&]+?)\s*=\s*([^=;&]+)", text):
        key = _mass_lhs_key(lhs, basis)
        if key is None:
            continue
        expr = _simple_latex_expr_to_python(rhs)
        if expr:
            entries[key] = expr
    return entries


def _mass_lhs_key(lhs: str, basis: list[str]) -> tuple[str, str] | None:
    clean = re.sub(r"\s+", "", lhs)
    clean = clean.replace("{", "").replace("}", "")
    clean = clean.replace("\\", "")
    clean = clean.replace("^2", "")
    lowered = clean.lower()
    for row in basis:
        for col in basis:
            variants = {
                f"m_{row}{col}",
                f"m{row}{col}",
                f"m_{row}_{col}",
                f"m{row}_{col}",
                f"M_{row}{col}",
                f"M{row}{col}",
                f"M_{row}_{col}",
                f"M{row}_{col}",
            }
            if row == col:
                variants.update({f"m_{row}", f"m{row}", f"M_{row}", f"M{row}"})
            if lowered in {variant.lower() for variant in variants}:
                return (row, col)
    return None


def _simple_latex_expr_to_python(expr: str) -> str:
    text = str(expr or "")
    text = re.sub(r"\\frac\s*\{([^{}]+)\}\s*\{([^{}]+)\}", r"((\1)/(\2))", text)
    replacements = {
        r"\mu_{H}^{2}": "mu_H_sq",
        r"\mu_H^2": "mu_H_sq",
        r"\mu_{S}^{2}": "mu_S_sq",
        r"\mu_S^2": "mu_S_sq",
        r"\lambda_{H}": "lambda_H",
        r"\lambda_H": "lambda_H",
        r"\lambda_{S}": "lambda_S",
        r"\lambda_S": "lambda_S",
        r"\lambda_{HS}": "lambda_HS",
        r"\lambda_HS": "lambda_HS",
    }
    for latex_name, python_name in replacements.items():
        text = text.replace(latex_name, python_name)
    text = text.replace("{", "").replace("}", "")
    text = text.replace("\\left", "").replace("\\right", "")
    text = text.replace("\\,", "")
    text = text.replace("\\", "")
    text = re.sub(r"\^([A-Za-z0-9_]+)", r"**\1", text)
    text = text.replace("^", "**")
    text = re.sub(r"(?<=[A-Za-z0-9_])\s+(?=[A-Za-z0-9_])", "*", text)
    return text.strip().strip(".")


def _sympy_equivalent(left: str, right: str) -> bool:
    import sympy as sp

    names = sorted(set(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", f"{left}\n{right}")))
    local_symbols = {name: sp.Symbol(name) for name in names}
    try:
        left_expr = sp.sympify(left, locals=local_symbols)
        right_expr = sp.sympify(right, locals=local_symbols)
    except Exception:
        return False
    return sp.simplify(left_expr - right_expr) == 0


def _auto_goldstone_defaults(formulas: dict[str, str]) -> dict[str, str]:
    evidence = "\n".join(str(formulas.get(key, "")) for key in ("VCW", "thermal", "daisy", "scalar_mass"))
    probe = evidence.lower()
    has_loop_evidence = any(token in probe for token in ("coleman", "v_1", "j_{b}", "j_b", "j_{f}", "j_f", "daisy", "goldstone", "\\partial"))
    if has_loop_evidence:
        return {}
    return {
        "cw_policy": "not_applicable",
        "ct_derivative_policy": "not_applicable",
        "thermal_policy": "not_applicable",
        "regulator": "none",
        "notes": "Auto-filled because no source evidence for CW, thermal-integral, Daisy, or Goldstone routes was detected in the fresh extraction.",
    }


def _looks_like_xsm_tree_potential(source_latex: str) -> bool:
    text = re.sub(r"\s+", "", str(source_latex or ""))
    required = (
        r"\mu_{H}",
        r"\lambda_{H}",
        r"\mu_{S}",
        r"\lambda_{S}",
        r"\lambda_{HS}",
        "H^{\\dagger}H",
        "S^2",
    )
    return all(token in text for token in required)


def _candidate_fields(memory: PaperMemory, *, tree_potential_latex: str = "") -> list[dict[str, Any]]:
    selected: list[str] = []
    for item in memory.symbol_registry:
        if item.role == "candidate_background_field" and item.symbol.strip() not in selected:
            selected.append(item.symbol.strip())
    if not selected:
        selected = _background_fields_from_tree_potential(tree_potential_latex) or ["phi"]
    selected = _drop_uppercase_multiplets(selected)
    return [
        {
            "name": _program_symbol(symbol),
            "latex": symbol,
            "role": "background",
            "zeroT_default": "0.0",
            "description": "Candidate background field from source evidence; review normalization.",
        }
        for symbol in selected[:4]
    ]


def _candidate_parameters(memory: PaperMemory, *, tree_potential_latex: str = "") -> list[dict[str, str]]:
    selected: list[dict[str, str]] = []
    for row in _parameter_candidates_from_tree_potential(tree_potential_latex):
        if not any(item["name"] == row["name"] for item in selected):
            selected.append(row)
    for item in memory.symbol_registry:
        if item.role not in {"coupling", "quadratic_parameter", "mass_or_mass_matrix", "vev_or_background_scale"}:
            continue
        symbol = item.symbol.strip()
        name = _program_symbol(symbol)
        if _looks_like_formula_or_matrix_label(symbol, name):
            continue
        if not symbol or any(row["name"] == name for row in selected):
            continue
        selected.append({"name": name, "latex": symbol, "description": "Candidate parameter; classify as input, constant, or derived."})
    return selected[:10]


def _background_fields_from_tree_potential(source_latex: str) -> list[str]:
    text = str(source_latex or "")
    match = re.search(r"V_?\{?0\}?\s*\(([^)]*)\)", text)
    if not match:
        return []
    result: list[str] = []
    for raw in match.group(1).split(","):
        symbol = raw.strip().strip("{} ")
        if not symbol:
            continue
        if len(symbol) == 1 and symbol.isalpha():
            result.append(symbol.lower())
        else:
            result.append(_program_symbol(symbol))
    return result[:4]


def _parameter_candidates_from_tree_potential(source_latex: str) -> list[dict[str, str]]:
    if not _looks_like_xsm_tree_potential(source_latex):
        return []
    return [
        {"name": "mu_H_sq", "latex": r"\mu_H^2", "description": "Auto-detected from the source tree potential; confirm whether this is a public scan input, fixed constant, or derived parameter."},
        {"name": "lambda_H", "latex": r"\lambda_H", "description": "Auto-detected from the source tree potential; confirm whether this is a public scan input, fixed constant, or derived parameter."},
        {"name": "mu_S_sq", "latex": r"\mu_S^2", "description": "Auto-detected from the source tree potential; confirm whether this is a public scan input, fixed constant, or derived parameter."},
        {"name": "lambda_S", "latex": r"\lambda_S", "description": "Auto-detected from the source tree potential; confirm whether this is a public scan input, fixed constant, or derived parameter."},
        {"name": "lambda_HS", "latex": r"\lambda_{HS}", "description": "Auto-detected from the source tree potential; confirm whether this is a public scan input, fixed constant, or derived parameter."},
    ]


def _drop_uppercase_multiplets(symbols: list[str]) -> list[str]:
    lower_symbols = {symbol.lower() for symbol in symbols}
    filtered: list[str] = []
    for symbol in symbols:
        if len(symbol) == 1 and symbol.isupper() and symbol.lower() in lower_symbols:
            continue
        filtered.append(symbol)
    return filtered or symbols


def _looks_like_formula_or_matrix_label(symbol: str, name: str) -> bool:
    compact_symbol = re.sub(r"[^A-Za-z0-9_]", "", symbol)
    compact_name = re.sub(r"_+", "_", name).strip("_")
    if compact_symbol in _DENY_PARAMETER_SYMBOLS or compact_name in _DENY_PARAMETER_SYMBOLS:
        return True
    lower = compact_name.lower()
    if lower in {"v", "v0", "v1", "v1t", "veff", "vcw", "vct", "vt", "vdaisy"}:
        return True
    if lower in {"formula_id", "source_id", "source_ids", "pymupdf_blocks", "paper_md", "pdf2md"}:
        return True
    if re.fullmatch(r"v(_)?[0-9a-z]*", lower) and lower.startswith(("v0", "v1")):
        return True
    if compact_name in {"M", "MS", "MV", "MF"}:
        return True
    return False


def _public_input_candidates(parameters: list[dict[str, str]]) -> list[dict[str, Any]]:
    if not parameters:
        return [
            {
                "name": "ASK_USER",
                "latex": "ASK_USER",
                "confirmed": "ASK_USER",
                "test_value": "ASK_USER",
                "description": "No public input basis was detected; fill manually.",
            }
        ]
    rows = []
    for row in parameters[:6]:
        rows.append(
            {
                "name": row["name"],
                "latex": row["latex"],
                "confirmed": "ASK_USER",
                "test_value": "ASK_USER",
                "description": row["description"],
            }
        )
    return rows


def _best_formula(records: list[FormulaRecord], kind: str, *, focus_terms: tuple[str, ...] = ()) -> str:
    candidates = [record for record in records if record.kind == kind]
    if not candidates:
        return ""
    candidates.sort(
        key=lambda record: _formula_focus_score(record, focus_terms),
        reverse=True,
    )
    return candidates[0].latex


def _formula_focus_score(record: FormulaRecord, focus_terms: tuple[str, ...]) -> float:
    score = record.confidence + len(record.latex) / 10000.0
    if not focus_terms:
        return score
    probe = "\n".join([record.latex, record.raw_text, record.context, record.label]).lower()
    for term in focus_terms:
        cleaned = str(term or "").strip().lower()
        if cleaned and cleaned in probe:
            score += 0.75
    return score


def _default_questions(formulas: dict[str, str]) -> list[dict[str, str]]:
    questions = [
        {
            "field": "parameters.public_inputs",
            "question": "Which symbols are public scan/build inputs? Confirm each row, then give one numeric test_value for build_model and smoke tests.",
        },
        {
            "field": "potential.V0.python",
            "question": "Fill the reviewed tree-level potential in the `Compiler expression` block using the chosen field and parameter names.",
        },
        {
            "field": "masses.bosons / masses.fermions",
            "question": "List every field-dependent species mass squared or reviewed mass matrix with d.o.f., Coleman-Weinberg constant, and usage.",
        },
    ]
    if formulas.get("thermal") or formulas.get("daisy"):
        questions.append(
            {
                "field": "loops",
                "question": "Should the finite-temperature part use standard thermal integrals, a custom expression, or no loop term?",
            }
        )
    return questions


def _render_table(headers: list[str], rows: list[dict[str, Any]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(_table_cell(row.get(header, ""), header=header) for header in headers) + " |")
    return "\n".join(lines)


def _render_table_with_notes(
    headers: list[str],
    rows: list[dict[str, Any]],
    *,
    note_key: str = "notes",
    id_key: str = "name",
) -> str:
    if note_key not in headers:
        return _render_table(headers, rows)
    compact_rows: list[dict[str, Any]] = []
    note_lines: list[str] = []
    note_index = 1
    for row in rows:
        compact_row = dict(row)
        note = str(compact_row.get(note_key, "")).strip()
        if note:
            note_id = f"N{note_index}"
            label = str(compact_row.get(id_key, "")).strip()
            compact_row[note_key] = note_id
            suffix = f" ({label})" if label else ""
            note_lines.append(f"- {note_id}{suffix}: {note}")
            note_index += 1
        compact_rows.append(compact_row)
    rendered = _render_table(headers, compact_rows)
    if not note_lines:
        return rendered
    return "\n\n".join([rendered, "Notes:", "\n".join(note_lines)])


def _render_matrix_entry_metadata_table(basis: list[str], *, status: str = "needs_review") -> str:
    rows = [
        {"row": row, "col": col, "entry_status": status}
        for row in basis
        for col in basis
    ]
    return _render_table(["row", "col", "entry_status"], rows)


def _render_matrix_entry_blocks(matrix_name: str, basis: list[str], rows: list[list[str]]) -> str:
    row_map = {str(row[0]): [str(cell) for cell in row[1:]] for row in rows if row}
    blocks: list[str] = []
    for row in basis:
        cells = row_map.get(row, [])
        for col_index, col in enumerate(basis):
            expr = cells[col_index] if col_index < len(cells) else "ASK_USER"
            blocks.extend(
                [
                    f"##### Matrix entry: {matrix_name}[{row},{col}]",
                    "",
                    "entry:",
                    "",
                    "```python",
                    expr or "ASK_USER",
                    "```",
                    "",
                ]
            )
    return "\n".join(blocks).rstrip()


def _table_cell(value: Any, *, header: str = "", code: bool = False) -> str:
    text = _single_line(str(value or ""))
    if not text:
        return ""
    text = _display_table_cell(text, header, code=code)
    return text.replace("|", "\\|")


def _display_table_cell(text: str, header: str, *, code: bool = False) -> str:
    """Make review formulas and compiler expressions render without Markdown damage."""

    if _placeholder(text) or _is_markdown_wrapped_math(text) or _is_markdown_wrapped_code(text):
        return text
    code_headers = {
        "expr",
        "mass_sq",
        "operator_expr",
        "target_expr",
        "replacement_expr",
        "custom_expr",
        "zeroT_mass_sq",
        "thermal_mass_sq",
        "daisy_term",
    }
    if code or header in code_headers:
        return _markdown_code_span(text)
    rendered_formula_headers = {
        "formula_latex",
        "source_latex",
    }
    if header == "latex":
        latex_text = _latex_symbol_for_table_display(text)
        if latex_text:
            return f"${clean_latex_evidence_for_markdown(latex_text, max_chars=240)}$"
    if header in rendered_formula_headers and _looks_like_latex_math_cell(text) and not _looks_like_mixed_prose(text):
        return f"${clean_latex_evidence_for_markdown(text, max_chars=360)}$"
    return text


def _latex_symbol_for_table_display(text: str) -> str:
    stripped = text.strip()
    if _looks_like_latex_math_cell(stripped):
        return stripped
    if stripped in _GREEK_LATEX_WORDS:
        return "\\" + stripped
    return ""


def _looks_like_latex_math_cell(text: str) -> bool:
    if not text or text in PLACEHOLDER_VALUES:
        return False
    if text.startswith(("see ", "review", "source", "paper ")):
        return False
    return bool(re.search(r"\\[A-Za-z]+|[_^{}=]", text))


def _looks_like_mixed_prose(text: str) -> bool:
    words = re.findall(r"[A-Za-z]{3,}", text.replace("\\", " "))
    latex_commands = re.findall(r"\\[A-Za-z]+", text)
    return len(words) - len(latex_commands) >= 4


def _is_markdown_wrapped_math(text: str) -> bool:
    stripped = text.strip()
    return len(stripped) >= 2 and stripped.startswith("$") and stripped.endswith("$")


def _is_markdown_wrapped_code(text: str) -> bool:
    stripped = text.strip()
    return len(stripped) >= 2 and stripped.startswith("`") and stripped.endswith("`")


def _strip_markdown_code_span(text: str) -> str:
    stripped = text.strip()
    match = re.match(r"^(`+)\s?(.*?)\s?\1$", stripped)
    return match.group(2).strip() if match else stripped


def _markdown_code_span(text: str) -> str:
    max_run = max((len(match.group(0)) for match in re.finditer(r"`+", text)), default=0)
    fence = "`" * (max_run + 1)
    if max_run:
        return f"{fence} {text} {fence}"
    return f"`{text}`"


def _validate_named_rows(rows: list[Any], field_key: str, issues: list[ValidationIssue]) -> list[str]:
    names: list[str] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            issues.append(_error(f"{field_key}[{index}]", "invalid_row", "Row must be a JSON object."))
            continue
        name = str(row.get("name", ""))
        if _placeholder(name):
            issues.append(_error(f"{field_key}[{index}].name", "placeholder_value", "Symbol name is unresolved."))
            continue
        if not _valid_identifier(name):
            issues.append(_error(f"{field_key}[{index}].name", "invalid_identifier", f"{name!r} is not a valid Python identifier."))
            continue
        names.append(name)
    return names


def _check_duplicate_names(names: list[str], field_key: str, issues: list[ValidationIssue]) -> None:
    seen: set[str] = set()
    for name in names:
        if name in seen:
            issues.append(_error(field_key, "duplicate_symbol", f"Symbol {name!r} appears more than once."))
        seen.add(name)


def _validate_species(
    rows: list[Any],
    field_key: str,
    allowed_names: set[str],
    issues: list[ValidationIssue],
    *,
    require_c: bool,
) -> None:
    _validate_named_rows(rows, field_key, issues)
    runtime_allowed = set(allowed_names) | {"T"}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        _require_expression(row.get("mass_sq"), runtime_allowed, f"{field_key}[{index}].mass_sq", issues)
        _require_review_status(
            row,
            status_column="mass_sq_status",
            field_key=f"{field_key}[{index}].mass_sq_status",
            issues=issues,
            subject=f"Species mass expression {row.get('name', index)}",
        )
        if _placeholder(row.get("dof")):
            issues.append(_error(f"{field_key}[{index}].dof", "placeholder_value", "Species d.o.f. is unresolved."))
        elif not _numeric_literal(row.get("dof")):
            issues.append(_error(f"{field_key}[{index}].dof", "invalid_number", "Species d.o.f. must be numeric."))
        if require_c:
            if _placeholder(row.get("c")):
                issues.append(_error(f"{field_key}[{index}].c", "placeholder_value", "Coleman-Weinberg constant c is unresolved."))
            elif not _numeric_literal(row.get("c")):
                issues.append(_error(f"{field_key}[{index}].c", "invalid_number", "Coleman-Weinberg constant c must be numeric."))
        for column in ("usage", "thermal_policy", "vacuum_anchor"):
            if _placeholder(row.get(column)):
                issues.append(
                    _error(
                        f"{field_key}[{index}].{column}",
                        "placeholder_value",
                        f"Species {column} must be reviewed so masses are not reused in the wrong potential term.",
                    )
                )


def _validate_boson_matrices(
    rows: list[Any],
    field_key: str,
    allowed_names: set[str],
    issues: list[ValidationIssue],
) -> None:
    _validate_named_rows(rows, field_key, issues)
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        basis = _list(row.get("basis"))
        matrix = _list(row.get("matrix"))
        if not basis:
            issues.append(_error(f"{field_key}[{index}].basis", "missing_value", "Mass matrix basis is missing."))
        if not matrix:
            issues.append(_error(f"{field_key}[{index}].matrix", "missing_value", "Mass matrix entries are missing."))
            continue
        if len(matrix) != len(basis) or any(not isinstance(line, list) or len(line) != len(basis) for line in matrix):
            issues.append(_error(f"{field_key}[{index}].matrix", "invalid_matrix_shape", "Mass matrix must be square with one row/column per basis entry."))
            continue
        for row_index, line in enumerate(matrix):
            for col_index, expr in enumerate(line):
                _require_expression(expr, allowed_names, f"{field_key}[{index}].matrix[{row_index}][{col_index}]", issues)
                status = _matrix_entry_status(row, row_index, col_index)
                if status:
                    _require_review_status(
                        {"entry_status": status},
                        status_column="entry_status",
                        field_key=f"{field_key}[{index}].matrix_entry_statuses[{row_index}][{col_index}]",
                        issues=issues,
                        subject=f"Matrix entry {row.get('name', index)}[{row_index},{col_index}]",
                    )
        if _placeholder(row.get("dof_per_eigenvalue")):
            issues.append(_error(f"{field_key}[{index}].dof_per_eigenvalue", "placeholder_value", "Matrix eigenvalue d.o.f. is unresolved."))
        elif not _numeric_literal(row.get("dof_per_eigenvalue")):
            issues.append(_error(f"{field_key}[{index}].dof_per_eigenvalue", "invalid_number", "Matrix eigenvalue d.o.f. must be numeric."))
        if _placeholder(row.get("c")):
            issues.append(_error(f"{field_key}[{index}].c", "placeholder_value", "Matrix Coleman-Weinberg constant c is unresolved."))
        elif not _numeric_literal(row.get("c")):
            issues.append(_error(f"{field_key}[{index}].c", "invalid_number", "Matrix Coleman-Weinberg constant c must be numeric."))
        source_role = str(row.get("source_role", "")).strip()
        if _placeholder(source_role):
            issues.append(
                _error(
                    f"{field_key}[{index}].source_role",
                    "placeholder_value",
                    "Mass matrix source role is unresolved.",
                    "Confirm this is a field-dependent mass matrix before compiling; vacuum-only physical mass relations belong in parameter solving or notes.",
                )
            )
        elif source_role != "field_dependent":
            issues.append(
                _error(
                    f"{field_key}[{index}].source_role",
                    "mass_role_conflict",
                    "Only reviewed field-dependent mass matrices may enter boson_massSq.",
                    "Do not compile vacuum physical-mass relations or phenomenology matrices as field-dependent spectra.",
                )
            )


def _matrix_entry_status(row: dict[str, Any], row_index: int, col_index: int) -> str:
    statuses = _list(row.get("matrix_entry_statuses"))
    if row_index >= len(statuses):
        return ""
    status_row = statuses[row_index]
    if not isinstance(status_row, list) or col_index >= len(status_row):
        return ""
    return str(status_row[col_index]).strip()


def _validate_mode(row: dict[str, Any], allowed: set[str], field_key: str, issues: list[ValidationIssue]) -> None:
    mode = str(row.get("mode", ""))
    if mode not in allowed:
        issues.append(_error(f"{field_key}.mode", "invalid_mode", f"Mode must be one of {sorted(allowed)}."))


def _require_expression(value: Any, allowed: set[str], field_key: str, issues: list[ValidationIssue]) -> None:
    if _placeholder(value):
        issues.append(_error(field_key, "placeholder_value", "Required compiler expression is unresolved."))
        return
    _validate_expression(str(value), allowed, field_key, issues)


def _require_review_status(
    row: dict[str, Any],
    *,
    status_column: str,
    field_key: str,
    issues: list[ValidationIssue],
    subject: str,
) -> None:
    status = str(row.get(status_column, "")).strip()
    if status not in REVIEW_STATUS_CHOICES:
        issues.append(
            _error(
                field_key,
                "invalid_review_status",
                f"{subject} review status must be one of {sorted(REVIEW_STATUS_CHOICES)}.",
                f"Use `{REVIEW_COMPILE_READY_STATUS}` only after Codex/PTagent has checked the Python block; use `human_modified` after manual edits.",
            )
        )
        return
    if status == REVIEW_COMPILE_READY_STATUS:
        return
    code = "human_modified_review_required" if status == "human_modified" else "compiler_expression_unreviewed"
    action = (
        f"Review the human-modified Python block, then set `{status_column}` to `{REVIEW_COMPILE_READY_STATUS}`."
        if status == "human_modified"
        else f"Review the named Python code block, then set `{status_column}` to `{REVIEW_COMPILE_READY_STATUS}`."
    )
    issues.append(
        _error(
            field_key,
            code,
            f"{subject} compiler expression needs Codex/PTagent review before compilation.",
            action,
        )
    )


def _validate_expression(expr: str, allowed_names: set[str], field_key: str, issues: list[ValidationIssue]) -> None:
    try:
        setup, result, assigned = _compiler_block_ast(str(expr))
    except SyntaxError as exc:
        issues.append(_error(field_key, "invalid_expression", f"Invalid Python compiler expression: {exc.msg}."))
        return
    except ValueError as exc:
        issues.append(_error(field_key, "invalid_expression", str(exc)))
        return
    visible_names = set(allowed_names)
    reserved_names = {"np", *_BARE_FUNCTIONS}
    for stmt in setup:
        target = stmt.targets[0]
        assert isinstance(target, ast.Name)
        if target.id in visible_names or target.id in reserved_names:
            issues.append(_error(field_key, "invalid_expression", f"Compiler block temporary {target.id!r} shadows an existing symbol."))
            continue
        _validate_expression_node(stmt.value, visible_names, field_key, issues)
        visible_names.add(target.id)
    for name in assigned:
        if not _valid_identifier(name):
            issues.append(_error(field_key, "invalid_identifier", f"Compiler block temporary {name!r} is not a valid Python identifier."))
    _validate_expression_node(result, visible_names, field_key, issues)


def _validate_expression_node(node: ast.AST, allowed_names: set[str], field_key: str, issues: list[ValidationIssue]) -> None:
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            if child.id in {"np", *allowed_names, *_BARE_FUNCTIONS}:
                continue
            issues.append(_error(field_key, "unknown_symbol", f"Expression uses unknown symbol {child.id!r}."))
        elif isinstance(child, ast.Attribute):
            if not (isinstance(child.value, ast.Name) and child.value.id == "np" and child.attr in _NP_FUNCTIONS):
                issues.append(_error(field_key, "invalid_expression", "Only np.<approved function> attributes are allowed."))
        elif isinstance(child, ast.Call):
            if isinstance(child.func, ast.Name) and child.func.id in _BARE_FUNCTIONS:
                continue
            if isinstance(child.func, ast.Attribute):
                continue
            issues.append(_error(field_key, "invalid_expression", "Only simple numeric calls such as np.sqrt(...) are allowed."))
        elif isinstance(
            child,
            (
                ast.Expression,
                ast.BinOp,
                ast.UnaryOp,
                ast.BoolOp,
                ast.Compare,
                ast.IfExp,
                ast.Constant,
                ast.Load,
                ast.Add,
                ast.Sub,
                ast.Mult,
                ast.Div,
                ast.Pow,
                ast.Mod,
                ast.USub,
                ast.UAdd,
                ast.And,
                ast.Or,
                ast.Eq,
                ast.NotEq,
                ast.Lt,
                ast.LtE,
                ast.Gt,
                ast.GtE,
                ast.keyword,
            ),
        ):
            continue
        elif isinstance(child, ast.operator | ast.unaryop | ast.cmpop | ast.boolop):
            continue
        else:
            issues.append(_error(field_key, "invalid_expression", f"Unsupported expression node {type(child).__name__}."))


def _placeholder(value: Any) -> bool:
    if value is None:
        return True
    text = str(value).strip()
    lower_values = {item.lower() for item in PLACEHOLDER_VALUES}
    return text in PLACEHOLDER_VALUES or text.lower() in lower_values or "ASK_USER" in text or "AI infer" in text


def _numeric_literal(value: Any) -> bool:
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True


def _float_literal(value: Any) -> str:
    return repr(float(value))


def _parse_ct_operator(operator: str, field_names: list[str]) -> tuple[int, ...]:
    text = re.sub(r"\s+", "", str(operator or ""))
    if text.startswith("d/d"):
        field = text[len("d/d") :]
        return (_field_index(field, field_names),)
    if text.startswith("d2/d"):
        rest = text[len("d2/d") :]
        first = _consume_field_name(rest, field_names)
        remainder = rest[len(first) :]
        if remainder.startswith("d"):
            remainder = remainder[1:]
        if remainder.startswith("^2"):
            second = first
        else:
            second = _consume_field_name(remainder, field_names)
        return (_field_index(first, field_names), _field_index(second, field_names))
    raise ValueError(f"Unsupported CT operator {operator!r}")


def _consume_field_name(text: str, field_names: list[str]) -> str:
    for name in sorted(field_names, key=len, reverse=True):
        if text.startswith(name):
            return name
    raise ValueError(f"Could not parse field name from CT operator fragment {text!r}.")


def _field_index(field_name: str, field_names: list[str]) -> int:
    try:
        return field_names.index(field_name)
    except ValueError as exc:
        raise ValueError(f"Unknown CT operator field {field_name!r}; expected one of {field_names!r}.") from exc


def _parse_ct_point_components(point: str, field_count: int) -> list[str]:
    text = str(point or "").strip()
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1]
    components = [item.strip() for item in text.split(",") if item.strip()]
    if len(components) != field_count:
        raise ValueError(f"CT point {point!r} must have {field_count} components.")
    return components


def _valid_identifier(value: str) -> bool:
    return value.isidentifier() and not keyword.iskeyword(value)


def _program_symbol(symbol: str) -> str:
    clean = symbol.strip().strip("$`")
    clean = re.sub(r"\\[A-Za-z]+\{([^}]*)\}", r"\1", clean)
    clean = clean.replace("\\", "")
    clean = clean.replace("{", "").replace("}", "")
    clean = clean.replace("^", "_").replace("-", "_")
    clean = re.sub(r"[^A-Za-z0-9_]", "_", clean)
    clean = re.sub(r"_+", "_", clean).strip("_")
    if not clean:
        return "ASK_USER"
    if clean[0].isdigit():
        clean = "p_" + clean
    if keyword.iskeyword(clean):
        clean = clean + "_"
    return clean


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _single_line(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().replace("|", "/")


def _error(field_key: str, code: str, message: str, suggested_action: str = "") -> ValidationIssue:
    return ValidationIssue(
        severity="error",
        code=code,
        field_key=field_key,
        message=message,
        suggested_action=suggested_action or "Fill this field in the Markdown contract template; do not infer silently.",
    )
