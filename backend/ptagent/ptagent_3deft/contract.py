from __future__ import annotations

import ast
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .layout import proof_dir_for_template

from .mathematica_expr import (
    convert_mathematica_inputform_expression,
    is_reserved_identifier,
    looks_like_mathematica_inputform,
    repair_reserved_identifier,
)


CONTRACT_MARKER = "<!-- PTAGENT 3DEFT contract_v1 -->"
CONTRACT_SCHEMA = "ptagent.3deft.contract.v1"

RESOLVED_CONTRACT_NAME = "three_deft_contract_resolved.json"
VALIDATION_NAME = "three_deft_validation.json"
USER_QUESTIONS_NAME = "three_deft_user_questions.md"
REVIEW_GATE_NAME = "three_deft_review_gate.md"
MERGED_CONTRACT_NAME = "three_deft_contract_merged.md"

MISSING_VALUES = {
    "",
    "ask_user",
    "ask user",
    "tbd",
    "todo",
    "required",
    "unknown",
    "unclear",
    "?",
    "-",
    "n/a?",
}
ORDER_VALUES = {"lo", "nlo", "nnlo", "not_applicable"}
ORDER_SOURCE_VALUES = {"dralgo_generated", "not_applicable"}
BETA_VARIABLE_VALUES_4D = {"log_mu", "ln_mu", "logmu", "log_mu_squared", "ln_mu_squared", "logmu_squared", "log_mu2", "ln_mu2", "logmu2"}
EXPRESSION_HELPER_NAMES = {
    "abs",
    "cos",
    "exp",
    "fabs",
    "log",
    "math",
    "max",
    "min",
    "np",
    "pi",
    "EulerGamma",
    "Glaisher",
    "pow",
    "sin",
    "sqrt",
    "tan",
}
DRALGO_CONSTRUCTION_SOURCE_VALUES = {"reviewed_source_file"}
FOUR_D_BETA_DEPENDENT_VARIABLE_POLICIES = {"dralgo_native", "converted_to_contract_variables"}
BOOL_TRUE = {"true", "yes", "y", "1", "approved", "reviewed"}
BOOL_FALSE = {"false", "no", "n", "0", "not_approved", "blocked"}
APPROVAL_WORDS = ("approve", "approved", "\u6279\u51c6", "\u540c\u610f", "\u5141\u8bb8")
REVIEW_WORDS = ("review", "reviewed", "\u5ba1\u9605", "\u68c0\u67e5", "\u786e\u8ba4")
SYMBOL_PHYSICS_ROLES = {
    "field",
    "input_parameter",
    "matched_3d_coefficient",
    "fixed_constant",
    "derived",
    "function",
    "temporary",
    "other",
}
PARAMETER_EVALUATOR_KEYS = (
    "function_name",
    "coefficient_order",
    "evaluator_mode",
    "running_owner",
)
PARAMETER_EVALUATOR_MODES = {"analytic", "table_interpolator", "dralgo_runtime"}
PARAMETER_RUNNING_OWNERS = {"dralgo_generated"}
THREE_D_SCALE_KEYS = ("mu_3_scale", "mu_3_us_scale")
THREE_D_SCALE_SYMBOLS = {
    "xi4": "four_d_to_three_d_matching_scale_variation",
    "xi_4": "four_d_to_three_d_matching_scale_variation",
    "mu": "four_d_to_three_d_matching_scale",
    "mu4": "four_d_to_three_d_matching_scale",
    "mu_4": "four_d_to_three_d_matching_scale",
    "mu_3": "mu_3_scale",
    "mu3": "mu_3_scale",
    "mu_3_us": "mu_3_us_scale",
    "mu_3us": "mu_3_us_scale",
    "mu3us": "mu_3_us_scale",
    "mu3US": "mu_3_us_scale",
}
FIELD_NORMALIZATION_VALUES = {
    "identity": "identity",
    "none": "identity",
    "phase_tracer": "identity",
    "phasetracer": "identity",
    "canonical": "phase_tracer_over_sqrt_t",
    "canonical_3d": "phase_tracer_over_sqrt_t",
    "phase_tracer_sqrt_t": "phase_tracer_over_sqrt_t",
    "phase_tracer_over_sqrt_t": "phase_tracer_over_sqrt_t",
    "phasetracer_sqrt_t": "phase_tracer_over_sqrt_t",
    "phasetracer_over_sqrt_t": "phase_tracer_over_sqrt_t",
    "pt_over_sqrt_t": "phase_tracer_over_sqrt_t",
    "phi_over_sqrt_t": "phase_tracer_over_sqrt_t",
    "1_sqrt_t": "phase_tracer_over_sqrt_t",
    "phase_tracer_times_sqrt_t": "phase_tracer_times_sqrt_t",
    "phasetracer_times_sqrt_t": "phase_tracer_times_sqrt_t",
    "pt_times_sqrt_t": "phase_tracer_times_sqrt_t",
    "sqrt_t_phase_tracer": "phase_tracer_times_sqrt_t",
    "sqrt_t_phasetracer": "phase_tracer_times_sqrt_t",
}
LAGRANGIAN_RESERVED_SCALE_SYMBOLS = {
    "mu",
    "mu3",
    "mu_3",
    "mu_4",
    "mu4",
    "mu_3_us",
    "mu_3us",
    "mu3_us",
    "mu3us",
    "mu3US",
}
LEGACY_THREE_D_RG_KEYS = {
    "three_d_scale_dependence_mode",
    "three_d_matching_scale",
    "three_d_phase_tracer_target_scale",
    "three_d_to_three_d_us_rg_running",
    "three_d_us_beta_function_variable",
    "three_d_us_beta_dependent_variable_policy",
    "three_d_us_rg_initial_scale",
    "three_d_us_rg_target_scale",
    "three_d_us_ode_solver",
    "three_d_us_ode_steps",
}
REVIEW_STATUS_VALUES = {
    "reviewed",
    "agent_reviewed",
    "source_reviewed",
    "user_confirmed",
    "human_reviewed",
    "dralgo_generated",
}
USER_CONFIRM_STATUS_VALUES = {
    "user_confirmed",
    "human_reviewed",
}
EVIDENCE_REVIEW_STATUS_VALUES = {
    "source_reviewed",
    "dralgo_generated",
    "user_confirmed",
    "human_reviewed",
}
PHASETRACER_INTERFACE_KEYS = (
    "phase_tracer_base_class",
    "potential_method",
    "field_count_method",
    "field_vector_order",
    "field_scale",
    "temperature_scale",
    "field_3d_to_phasetracer_map",
    "potential_prefactor_policy",
    "symmetry_hook",
    "symmetry_rules",
    "low_t_phase_guesses",
)
PHASETRACER_USER_CONFIRMED_HOOK_KEYS = {
    "symmetry_hook",
    "symmetry_rules",
    "low_t_phase_guesses",
}
class ThreeDeftBlocked(RuntimeError):
    """Raised when a 3DEFT artifact is not ready for the requested operation."""


@dataclass(frozen=True)
class ValidationIssue:
    field_key: str
    code: str
    message: str
    severity: str = "error"

    def to_dict(self) -> dict[str, str]:
        return {
            "severity": self.severity,
            "stage": _issue_stage(self),
            "field_key": self.field_key,
            "code": self.code,
            "message": self.message,
        }


@dataclass(frozen=True)
class ValidationReport:
    contract: dict[str, Any]
    issues: tuple[ValidationIssue, ...]
    require_compile_approval: bool = False

    @property
    def ok(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)

    @property
    def dralgo_blocking_issues(self) -> tuple[ValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "error" and _issue_blocks_dralgo_run(issue))

    @property
    def compile_blocking_issues(self) -> tuple[ValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "error")

    @property
    def ready_for_run(self) -> bool:
        return not self.dralgo_blocking_issues

    @property
    def ready_for_dralgo_run(self) -> bool:
        return self.ready_for_run

    @property
    def ready_for_compile(self) -> bool:
        if self.compile_blocking_issues:
            return False
        phasetracer = self.contract.get("phasetracer", {})
        approval = _as_bool(phasetracer.get("approve_compile"))
        return bool(approval and not _missing(phasetracer.get("approval_record")))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": CONTRACT_SCHEMA,
            "ok": self.ok,
            "ready_for_run": self.ready_for_run,
            "ready_for_dralgo_run": self.ready_for_dralgo_run,
            "ready_for_compile": self.ready_for_compile,
            "require_compile_approval": self.require_compile_approval,
            "dralgo_blocking_issue_count": len(self.dralgo_blocking_issues),
            "compile_blocking_issue_count": len(self.compile_blocking_issues),
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass(frozen=True)
class QuestionPacket:
    key: str
    issues: tuple[ValidationIssue, ...]

    @property
    def representative(self) -> ValidationIssue:
        return self.issues[0]

    @property
    def field_keys(self) -> tuple[str, ...]:
        return tuple(issue.field_key for issue in self.issues)


def build_contract_template(
    *,
    source_path: str = "",
    source_sha256: str = "",
    model_name: str = "three_deft_model",
) -> str:
    """Build a standalone 3DEFT contract template.

    This intentionally does not reuse the standard finite-temperature contract
    builder. The template is conservative: every physics-sensitive cell starts
    as ASK_USER unless it is a fixed pipeline policy.
    """

    source_path = source_path or "ASK_USER"
    source_sha256 = source_sha256 or "ASK_USER"
    default_dralgo_program_path = source_path if source_path != "ASK_USER" else "ASK_USER"
    return "\n".join(
        [
            CONTRACT_MARKER,
            f"schema: {CONTRACT_SCHEMA}",
            f"model_name: {model_name}",
            "",
            "# 3DEFT Contract Template",
            "",
            "This contract is for the independent PTagent 3DEFT pipeline only.",
            "It evaluates the reviewed total 3D EFT potential through a PhaseTracer-compatible C++ model.",
            "",
            "## Model And Policy",
            "",
            "| key | value | notes |",
            "| --- | --- | --- |",
            f"| schema | {CONTRACT_SCHEMA} | Required schema marker. |",
            f"| source_path | {source_path} | Reviewed DRalgo Mathematica source file. |",
            f"| source_sha256 | {source_sha256} | Hash of the source artifact if available. |",
            "| potential_mode | external_total_3deft | The generated model consumes only the reviewed total 3D EFT potential. |",
            "| source_model_audit_status | ASK_USER | Must be source_reviewed/user_confirmed/human_reviewed after the agent presents or cites the source-level RepScalar, RepFermion, scalar-potential, Yukawa, AllocateTensors, and ImportModelDRalgo audit. Ask the user only when source evidence is missing or ambiguous. This is not a DRalgo-run approval gate. |",
            "",
            "## Parent Theory",
            "",
            "### Gauge Groups",
            "",
            "| name | group | coupling | representation_notes | evidence |",
            "| --- | --- | --- | --- | --- |",
            "| ASK_USER | ASK_USER | ASK_USER | ASK_USER | ASK_USER |",
            "",
            "### Scalar Multiplets",
            "",
            "| name | representation | components | vev_policy | dralgo_symbol | evidence |",
            "| --- | --- | --- | --- | --- | --- |",
            "| ASK_USER | ASK_USER | ASK_USER | ASK_USER | ASK_USER | ASK_USER |",
            "",
            "### Fermion Multiplets",
            "",
            "| name | representation | retained | yukawa_symbols | evidence |",
            "| --- | --- | --- | --- | --- |",
            "| ASK_USER | ASK_USER | ASK_USER | ASK_USER | ASK_USER |",
            "",
            "### Yukawa Sector",
            "",
            "| policy | retained_terms | ignored_terms | evidence |",
            "| --- | --- | --- | --- |",
            "| ASK_USER | ASK_USER | ASK_USER | ASK_USER |",
            "",
            "### Yukawa Construction Map",
            "",
            "| coupling | invariant_label | operator_structure | invariant_expression | source | status | notes |",
            "| --- | --- | --- | --- | --- | --- | --- |",
            "| ASK_USER | ASK_USER | ASK_USER | ASK_USER | ASK_USER | ASK_USER | Review the source-derived coupling -> invariant_label -> operator_structure map; do not use xSM defaults unless the reviewed source actually contains them. |",
            "",
            "### Scalar Invariant Basis",
            "",
            "| name | expression | coefficient | evidence |",
            "| --- | --- | --- | --- |",
            "| ASK_USER | ASK_USER | ASK_USER | ASK_USER |",
            "",
            "### Scalar Potential Construction Map",
            "",
            "| coupling | invariant_label | operator_structure | potential_term | tensor_derivative | source | status | notes |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
            "| ASK_USER | ASK_USER | ASK_USER | ASK_USER | ASK_USER | ASK_USER | ASK_USER | Tree-level scalar potential terms must be reviewed before generating GradMass/GradQuartic/GradCubic/Tadpole code. |",
            "",
            "### DRalgo Construction Fields",
            "",
            "| dralgo_name | parent_field | group_representation | real_components | evidence |",
            "| --- | --- | --- | --- | --- |",
            "| ASK_USER | ASK_USER | ASK_USER | ASK_USER | ASK_USER |",
            "",
            "## Input Parameters",
            "",
            "| name | latex | review_status | test_value | input_scale_GeV | source | notes |",
            "| --- | --- | --- | --- | --- | --- | --- |",
            "| ASK_USER | ASK_USER | ASK_USER | ASK_USER | ASK_USER | ASK_USER | ASK_USER |",
            "",
            "## RG And Matching",
            "",
            "| key | value | notes |",
            "| --- | --- | --- |",
            "| four_d_to_three_d_matching_scale | ASK_USER | Required 4D RG target and 4D->3D matching scale. Recommended prompt default: 4*pi*exp(-EulerGamma)*xi4*T with xi4=1. |",
            "| four_d_to_three_d_matching_scale_variation | xi4=1 | xi4 is a user-reviewed hard-scale control parameter; it is not a running coupling and must not receive a beta function. |",
            "| scale_policy_review_status | ASK_USER | Must be source_reviewed/user_confirmed/human_reviewed after the agent derives or cites the combined RG/matching, mu_3, and mu_3_us scale policy. Ask the user only when source evidence is missing or ambiguous. |",
            "| hard_matching_order | not_applicable | Filled only when the reviewed source explicitly calls an ordered hard-layer print such as PrintScalarMass[\"...\"]; no agent default. |",
            "| hard_matching_source | not_applicable | Set to dralgo_generated only when an explicit source output order is found. |",
            "| soft_matching_order | not_applicable | Filled only when the reviewed source explicitly calls an ordered soft-layer print such as PrintScalarMassUS[\"...\"]; no agent default. |",
            "| soft_matching_source | not_applicable | Set to dralgo_generated only when an explicit source output order is found. |",
            "| effective_potential_order | not_applicable | Filled from the reviewed source PrintEffectivePotential[\"LO\"/\"NLO\"/\"NNLO\"] call; no recommendation. |",
            "| effective_potential_source | not_applicable | Set to dralgo_generated only when the reviewed source explicitly prints the effective potential. |",
            "| pressure_order | not_applicable | Filled from the reviewed source PrintPressure/PrintPressureUS order when present; no recommendation. |",
            "| pressure_source | not_applicable | Set to dralgo_generated only when the reviewed source explicitly prints pressure. |",
            "",
            "### RG Beta Functions",
            "",
            "| parameter | beta_expression | source | status | notes |",
            "| --- | --- | --- | --- | --- |",
            "| ASK_USER | ASK_USER | BetaFunctions4D[] captured from DRalgo; required before compile when input parameters are running. | ASK_USER | ASK_USER |",
            "",
            "## EFT Stages And Scalar Indices",
            "",
            "### EFT Stages",
            "",
            "| stage | command | order | integrated_scalar_indices | generated_by | evidence |",
            "| --- | --- | --- | --- | --- | --- |",
            "| hard | PerformDRhard[] | ASK_USER | none | ASK_USER | ASK_USER |",
            "| soft | PerformDRsoft[ASK_USER] | ASK_USER | ASK_USER | ASK_USER | ASK_USER |",
            "| ultrasoft | ASK_USER | ASK_USER | ASK_USER | ASK_USER | ASK_USER |",
            "",
            "### Scalar Index Map",
            "",
            "| scalar_index | field_component | parent_multiplet | integrated_out | print_scalar_rep_positions_evidence |",
            "| --- | --- | --- | --- | --- |",
            "| ASK_USER | ASK_USER | ASK_USER | ASK_USER | PrintScalarRepPositions[] required. |",
            "",
            "## 3D Potential For PhaseTracer",
            "",
            "### 3D Fields",
            "",
            "| name | dralgo_background_symbol | source_scalar_indices | normalization | phase_tracer_slot | evidence |",
            "| --- | --- | --- | --- | --- | --- |",
            "| ASK_USER | ASK_USER | ASK_USER | ASK_USER | ASK_USER | ASK_USER |",
            "",
            "### Mathematica Symbol Audit",
            "",
            "| raw_symbol | suggested_symbol | compiler_symbol | physics_role | cform_temp_symbol | source | status | notes |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
            "| ASK_USER | ASK_USER | ASK_USER | ASK_USER | ASK_USER | ASK_USER | ASK_USER | ASK_USER |",
            "",
            "### 3D Coefficients",
            "",
            "| name | expression_or_interpolator | depends_on | source | evidence |",
            "| --- | --- | --- | --- | --- |",
            "| ASK_USER | ASK_USER | ASK_USER | ASK_USER | ASK_USER |",
            "",
            "### 3D Coefficient Order Sums",
            "",
            "| base_parameter | components | expression | source | status | notes |",
            "| --- | --- | --- | --- | --- | --- |",
            "| not_applicable | none | none | not_applicable | reviewed | Filled when DRalgo emits ordered pieces such as muHsq_3dUS_LO and muHsq_3dUS_NLO. |",
            "",
            "### 3D Parameter Evaluator",
            "",
            "| key | value | status | notes |",
            "| --- | --- | --- | --- |",
            "| function_name | get_3d_parameters | reviewed | Generated helper returning reviewed 3D coefficients at T. |",
            "| coefficient_order | ASK_USER | ASK_USER | Comma-separated order of names returned by get_3d_parameters(T). |",
            "| evaluator_mode | ASK_USER | ASK_USER | analytic, table_interpolator, or dralgo_runtime. |",
            "| running_owner | ASK_USER | ASK_USER | Must be dralgo_generated. |",
            "| mu_3_scale | ASK_USER | ASK_USER | Mandatory reviewed soft scale for evaluating DRalgo 3D parameters. Recommended central value: T. |",
            "| mu_3_us_scale | ASK_USER | ASK_USER | Mandatory reviewed ultrasoft target scale for BetaFunctions3DUS[] parameter running. Recommended value: factor*T, with factor chosen from the reviewed SU(2) coupling convention, e.g. g2*T when g2 is an input parameter. |",
            "",
            "### 3D Beta Functions",
            "",
            "| parameter | beta_expression | source | status | notes |",
            "| --- | --- | --- | --- | --- |",
            "| ASK_USER | ASK_USER | Optional BetaFunctions3DUS[] row from DRalgo output. When the parameter has a matched 3D initial value, PhaseTracer uses it to run from mu_3_scale to mu_3_us_scale. | ASK_USER | ASK_USER |",
            "",
            "### 3D Soft Beta Functions",
            "",
            "| parameter | beta_expression | source | status | notes |",
            "| --- | --- | --- | --- | --- |",
            "| not_applicable | not_applicable | BetaFunctions3DS[] is a soft-stage audit output; v1 PhaseTracer generation does not consume it directly. | not_applicable | Captured DRalgo output remains in proof_materials/dralgo_3deft_output.json when present. |",
            "",
            "### 3D Consumption",
            "",
            "| key | value | notes |",
            "| --- | --- | --- |",
            "| field_normalization | canonical_3d | Default 3DEFT convention: evaluate V3D in canonical 3D fields obtained from PhaseTracer coordinates by field_i_3d=phi[i]/sqrt(T). |",
            "| potential_prefactor | T | Default 3DEFT-to-PhaseTracer convention: V(phi,T)=T*V3D(fields_3d,T). |",
            "| v3d_expression | ASK_USER | Total 3D potential before prefactor. Python-like scalar expression. |",
            "| v_phase_tracer_expression | ASK_USER | Final PhaseTracer V(phi,T). Python-like scalar expression. |",
            "| coefficient_eval_mode | ASK_USER | analytic, table_interpolator, or dralgo_runtime. |",
            "| temperature_dependence | ASK_USER | Which coefficients depend on T and how. |",
            "",
            "### PhaseTracer Potential Interface",
            "",
            "| key | value | status | notes |",
            "| --- | --- | --- | --- |",
            "| phase_tracer_base_class | EffectivePotential::Potential | reviewed | Official PhaseTracer effective-potential interface. |",
            "| potential_method | V(Eigen::VectorXd phi, double T) | reviewed | Total reviewed 3DEFT potential after field map and prefactor. |",
            "| field_count_method | get_n_scalars() | reviewed | Must equal the reviewed 3D field count. |",
            "| field_vector_order | phase_tracer_slot order from 3D Fields | reviewed | Same order as 3D Fields.phase_tracer_slot. |",
            "| field_scale | none | reviewed | Use PhaseTracer base default field scale. |",
            "| temperature_scale | none | reviewed | Use PhaseTracer base default temperature scale. |",
            "| field_3d_to_phasetracer_map | field_i_3d=phi[i]/sqrt(T) | reviewed | Default 3DEFT map used by the generated renderer. |",
            "| potential_prefactor_policy | V(phi,T)=T*V3D(fields_3d,T) | reviewed | Default conversion from 3D EFT potential to PhaseTracer energy density. |",
            "| symmetry_hook | none | ASK_USER | Mandatory PhaseTracer apply_symmetry() policy. Agent recommendation: none unless the reviewed source/V3D has an explicitly intended field-reflection equivalence. Supported: none, z2_reflection. |",
            "| symmetry_rules | none | ASK_USER | Mandatory confirmation with symmetry_hook. Required when symmetry_hook=z2_reflection; use semicolon-separated field groups from 3D Fields, e.g. `s` or `phi,s`. |",
            "| low_t_phase_guesses | none | ASK_USER | Mandatory confirmation for get_low_t_phases(). Agent recommendation: none unless numeric low-T phase hints are intentionally supplied. |",
            "",
            "## DRalgo Runner",
            "",
            "| key | value | notes |",
            "| --- | --- | --- |",
            "| dralgo_construction_source | reviewed_source_file | Must be reviewed_source_file. 3DEFT accepts only a user-supplied DRalgo source program. |",
            f"| dralgo_program_path | {default_dralgo_program_path} | Reviewed user-supplied Mathematica .m input for this contract. |",
            "| wolframscript_path | wolframscript | Executable used for the real DRalgo run. |",
            "| output_capture_mode | json_or_text | Runner normalizes captured output to dralgo_3deft_output.json. |",
            "| capture_print_scalar_rep_positions | true | Required for soft matching audit. |",
            "| capture_print_couplings | true | Required for 3D coefficient map. |",
            "| capture_print_effective_potential | true | Required for PhaseTracer V. |",
            "| capture_pressure | true | Capture pressure output when the reviewed DRalgo file reaches the corresponding EFT stage. |",
            "| capture_beta_functions | true | Capture DRalgo beta-function output when available; do not ask the user to hand-copy it before running. |",
            "",
            "## PhaseTracer",
            "",
            "| key | value | notes |",
            "| --- | --- | --- |",
            "| backend | phasetracer | The only backend supported by 3DEFT v1. |",
            "| approve_compile | false | Must become true only after explicit user approval. |",
            "| approval_record | ASK_USER | Written by the approve command after the user says they reviewed the merged contract and approve PhaseTracer generation. |",
            f"| output_dir | {model_name}_phasetracer | Independent 3DEFT output folder. |",
            "| reference_fields | ASK_USER | Comma-separated field values in 3D field order. |",
            "| reference_temperature | ASK_USER | Test temperature for generated V(phi,T). |",
            "| reference_expected_v | not_applicable | Optional direct V value for a fixed-3D-parameter comparison point; not used for full RG/matching validation. |",
            "",
        ]
    )


def parse_contract(markdown: str) -> dict[str, Any]:
    """Parse a 3DEFT Markdown contract into a standalone dictionary."""

    model_policy = _key_value_table(markdown, "Model And Policy")
    parent = {
        "gauge_groups": _table(markdown, "Gauge Groups"),
        "scalar_multiplets": _table(markdown, "Scalar Multiplets"),
        "fermion_multiplets": _table(markdown, "Fermion Multiplets"),
        "yukawa_sector": _table(markdown, "Yukawa Sector"),
        "yukawa_construction_map": _table(markdown, "Yukawa Construction Map"),
        "scalar_invariant_basis": _table(markdown, "Scalar Invariant Basis"),
        "scalar_potential_construction_map": _table(markdown, "Scalar Potential Construction Map"),
        "dralgo_construction_fields": _table(markdown, "DRalgo Construction Fields"),
    }
    contract = {
        "schema": _schema_from_text(markdown, model_policy),
        "marker_present": CONTRACT_MARKER in markdown,
        "source_hash": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
        "model_name": _first_match(r"^model_name:\s*(.*?)\s*$", markdown) or "three_deft_model",
        "model_policy": model_policy,
        "parent_theory_4d": parent,
        "input_parameters": _input_parameter_table(markdown),
        "rg_matching": _key_value_table(markdown, "RG And Matching"),
        "rg_beta_functions": _table(markdown, "RG Beta Functions"),
        "eft_stages": _table(markdown, "EFT Stages"),
        "scalar_index_map": _table(markdown, "Scalar Index Map"),
        "three_d_fields": _table(markdown, "3D Fields"),
        "symbol_audit": _table(markdown, "Mathematica Symbol Audit"),
        "three_d_coefficients": _table(markdown, "3D Coefficients"),
        "three_d_coefficient_order_sums": _table(markdown, "3D Coefficient Order Sums"),
        "three_d_parameter_evaluator": _table(markdown, "3D Parameter Evaluator"),
        "three_d_beta_functions": _table(markdown, "3D Beta Functions"),
        "three_d_soft_beta_functions": _table(markdown, "3D Soft Beta Functions"),
        "three_d_consumption": _key_value_table(markdown, "3D Consumption"),
        "phasetracer_interface": _table(markdown, "PhaseTracer Potential Interface"),
        "runner": _key_value_table(markdown, "DRalgo Runner"),
        "phasetracer": _key_value_table(markdown, "PhaseTracer"),
    }
    return contract


def validate_contract_template(markdown: str, *, require_compile_approval: bool = False) -> ValidationReport:
    contract = parse_contract(markdown)
    issues: list[ValidationIssue] = []

    if not contract["marker_present"]:
        issues.append(_issue("schema.marker", "missing_marker", f"Missing required marker {CONTRACT_MARKER}."))
    if contract.get("schema") != CONTRACT_SCHEMA:
        issues.append(_issue("schema", "wrong_schema", f"Expected schema {CONTRACT_SCHEMA}."))

    policy = contract["model_policy"]
    _require_value(issues, policy, "potential_mode", "model_policy.potential_mode")
    if _norm(policy.get("potential_mode")) != "external_total_3deft":
        issues.append(
            _issue(
                "model_policy.potential_mode",
                "unsupported_potential_mode",
                "3DEFT v1 requires potential_mode=external_total_3deft.",
            )
        )
    if not _evidence_reviewed_status(policy.get("source_model_audit_status")):
        issues.append(
            _issue(
                "model_policy.source_model_audit_status",
                "source_model_audit_not_confirmed",
                "The source model audit must be source_reviewed/user_confirmed/human_reviewed after reviewing RepScalar, RepFermion, scalar-potential, Yukawa, AllocateTensors, and ImportModelDRalgo evidence; ask the user only when source evidence is missing or ambiguous.",
            )
        )
    _validate_parent_theory(contract, issues)
    _validate_input_parameters(contract, issues)
    _validate_rg_matching(contract, issues)
    _validate_eft_stages(contract, issues)
    _validate_3d_consumption(contract, issues)
    _validate_parameter_evaluator(contract, issues)
    _validate_three_d_beta_functions(contract, issues)
    _validate_symbol_audit(contract, issues)
    _validate_phasetracer_interface(contract, issues)
    _validate_dralgo_runner(contract, issues)
    _validate_phasetracer_policy(contract, issues, require_compile_approval=require_compile_approval)

    return ValidationReport(contract=contract, issues=tuple(issues), require_compile_approval=require_compile_approval)


def write_resolved_artifacts(
    template_path: str | Path,
    *,
    require_compile_approval: bool = False,
    gate: str = "dralgo_run",
) -> dict[str, Path]:
    template_path = Path(template_path)
    markdown = template_path.read_text(encoding="utf-8")
    validation = validate_contract_template(markdown, require_compile_approval=require_compile_approval)
    base = proof_dir_for_template(template_path)
    base.mkdir(parents=True, exist_ok=True)
    resolved_path = base / RESOLVED_CONTRACT_NAME
    validation_path = base / VALIDATION_NAME
    questions_path = base / USER_QUESTIONS_NAME
    review_gate_path = base / REVIEW_GATE_NAME

    _write_json(resolved_path, validation.contract)
    _write_json(validation_path, validation.to_dict())
    questions_path.write_text(build_user_questions(validation), encoding="utf-8")
    effective_gate = gate
    if effective_gate == "dralgo_run" and template_path.name == MERGED_CONTRACT_NAME:
        effective_gate = "phasetracer_compile"
    review_gate_path.write_text(build_review_gate_report(validation, template_path=template_path.name, gate=effective_gate), encoding="utf-8")
    return {
        "resolved": resolved_path,
        "validation": validation_path,
        "questions": questions_path,
        "review_gate": review_gate_path,
    }


def build_user_questions(report: ValidationReport) -> str:
    lines = [
        "# 3DEFT User Questions",
        "",
        "This file is generated by `python -m ptagent --ptagent-engine 3deft resolve`.",
        "Question mode asks one unresolved item at a time. The agent should fill every mechanical item it can infer from the reviewed DRalgo source or from `dralgo_3deft_output.json` before asking the user.",
        "DRalgo-derived rows such as beta functions, scalar index maps, 3D coefficients, and effective potentials are deferred to the agent/DRalgo backfill queue whenever possible.",
        "The only mandatory whole-template approval gate is PhaseTracer compile approval on the merged contract.",
        "",
        "## Agent-Assisted Review Policy",
        "",
        "The user supplies the DRalgo `.m`/`.wl` file. The agent only wraps it with capture markers, runs Mathematica, normalizes the output, and merges auditable rows back into Markdown.",
        "Do not ask the user to hand-copy DRalgo-derived outputs before the marked wrapper has run:",
        "- scalar index maps come from `PrintScalarRepPositions[]`;",
        "- 3D coefficients come from `PrintCouplings[]` / `PrintCouplingsUS[]`;",
        "- effective potentials come from `PrintEffectivePotential[...]`;",
        "- beta functions come from `BetaFunctions4D[]`, `BetaFunctions3DS[]`, and `BetaFunctions3DUS[]` when available.",
        "",
    ]
    if not report.issues:
        lines.extend(
            [
                "No mandatory blockers remain.",
                "",
                "Next agent action: run `env-check`, generate/run the marked DRalgo wrapper, merge the output, then ask for whole-template PhaseTracer compile approval.",
            ]
        )
        return "\n".join(lines) + "\n"

    unique_issues = _unique_issues(report.issues)
    user_questions = [issue for issue in unique_issues if _issue_needs_user_question(issue)]
    agent_backfill = [issue for issue in unique_issues if _issue_should_be_backfilled_by_agent(issue)]
    packets = _build_question_packets(user_questions)

    lines.extend(
        [
            "## Question Progress",
            "",
            f"- Underlying blocking fields remaining: {len(user_questions)}.",
            f"- Current question: 1 of {len(packets)}.",
            f"- Remaining after this answer: {max(len(packets) - 1, 0)}.",
            "- Related blockers are fused into one question packet, and packets are asked one at a time in dependency order.",
            "",
            "## Current Question",
            "",
        ]
    )
    current_packet: QuestionPacket | None = None
    if packets:
        current_packet = packets[0]
        lines.extend(_render_question_packet(current_packet, number=1, total=len(packets)))
        lines.append("")
        lines.append("After the user answers this packet, the agent should patch the Markdown contract, rerun `resolve`, and ask the next remaining packet.")
    else:
        lines.append("No direct user question is needed right now. Remaining blockers are expected to be filled by the agent from the DRalgo source/output or by the final PhaseTracer approval gate.")

    if agent_backfill:
        lines.extend(["", "## Agent Backfill Queue", ""])
        lines.append("These are not user questions yet. Codex/PTagent should first try to fill them from the reviewed DRalgo source, marked DRalgo output, or generated contract context.")
        lines.append("")
        for issue in agent_backfill:
            lines.append(f"- `{issue.field_key}`: {issue.message}")
            hint = _question_hint(issue)
            if hint:
                lines.append(f"  Agent action: {hint}")

    remaining_gate = [issue for issue in unique_issues if _issue_stage(issue) == "gate"]
    if remaining_gate:
        lines.extend(["", "## Approval Gate", ""])
        for issue in remaining_gate:
            lines.append(f"- `{issue.field_key}`: {issue.message}")

    lines.extend(["", "## All Remaining Validation Issues", ""])
    current_fields = set(current_packet.field_keys if current_packet else ())
    listed_issues = [issue for issue in unique_issues if issue.field_key not in current_fields]
    groups = (
        ("Pre-DRalgo source/preflight", [issue for issue in listed_issues if _issue_stage(issue) == "pre_dralgo"]),
        ("Agent/DRalgo backfill before compile", [issue for issue in listed_issues if _issue_stage(issue) == "post_dralgo"]),
        ("User/compile policy before PhaseTracer generation", [issue for issue in listed_issues if _issue_stage(issue) == "compile"]),
        ("Approval", [issue for issue in remaining_gate if issue.field_key not in current_fields]),
    )
    for heading, issues in groups:
        if not issues:
            continue
        lines.append(f"### {heading}")
        lines.append("")
        for issue in issues:
            lines.append(f"- [ ] `{issue.field_key}`: {issue.message}")
            hint = _question_hint(issue)
            if hint:
                lines.append(f"  Hint: {hint}")
        lines.append("")
    lines.append("")
    return "\n".join(lines)


def build_review_gate_report(report: ValidationReport, *, template_path: str, gate: str = "dralgo_run") -> str:
    gate = gate.casefold()
    if gate not in {"dralgo_run", "phasetracer_compile"}:
        gate = "dralgo_run"
    if gate == "phasetracer_compile":
        title = "PhaseTracer Compile Approval"
        approval_cell = "`PhaseTracer.approve_compile=true` and `PhaseTracer.approval_record=<exact user sentence>`"
        next_action = (
            "`python -m ptagent --ptagent-engine 3deft approve --template <merged-contract> "
            "--gate phasetracer_compile --user-approval \"<exact user sentence>\"`, "
            "then `python -m ptagent --ptagent-engine 3deft compile --template <merged-contract>`"
        )
        review_target = template_path
        focus_sections = (
            "3D Fields",
            "Mathematica Symbol Audit",
            "3D Coefficients",
            "3D Coefficient Order Sums",
            "3D Consumption",
            "3D Parameter Evaluator",
            "3D Beta Functions",
            "PhaseTracer Potential Interface",
            "PhaseTracer",
        )
        approval_phrase = "I reviewed the merged 3DEFT contract and approve PhaseTracer compile."
        gate_open = report.ready_for_compile
    else:
        title = "DRalgo Extraction Status"
        approval_cell = "no DRalgo approval cell is required"
        next_action = "`python -m ptagent --ptagent-engine 3deft env-check`, then `generate-dralgo`/`run`"
        review_target = template_path
        focus_sections = (
            "DRalgo Runner",
            "RG And Matching",
            "EFT Stages And Scalar Indices",
        )
        approval_phrase = "No DRalgo run approval is required; source preflight controls wrapper/run."
        gate_open = report.ready_for_dralgo_run

    blocking = list(report.compile_blocking_issues if gate == "phasetracer_compile" else report.dralgo_blocking_issues)
    lines = [
        f"# {title}",
        "",
        "This file is an agent pre-review checklist. The human-readable Markdown contract remains the source of truth.",
        "",
        "## Agent Pre-Review",
        "",
        f"- Contract file: `{review_target}`",
        f"- Blocking issue count for this gate: {len(blocking)}",
        f"- Pre-DRalgo resolved: {str(report.ready_for_run).lower()}",
        f"- Compile blockers remaining: {len(report.compile_blocking_issues)}",
        f"- Current gate open: {str(gate_open).lower()}",
        "",
    ]
    if blocking:
        lines.extend(
            [
                "## Blocked",
                "",
                "Do not ask for user approval yet. Fix `three_deft_user_questions.md` and rerun `resolve`.",
                "",
            ]
        )
        seen: set[str] = set()
        for issue in blocking:
            if issue.field_key in seen:
                continue
            seen.add(issue.field_key)
            lines.append(f"- `{issue.field_key}`: {issue.message}")
        lines.append("")
        return "\n".join(lines)

    lines.extend(
        [
            "## User Review",
            "",
            f"Ask the user to open `{review_target}` and check these sections:",
            "",
        ]
    )
    for section in focus_sections:
        lines.append(f"- {section}")
    lines.extend(
        [
            "",
            "The user does not need to approve every table row separately. A whole-template approval is sufficient after they inspect the Markdown.",
            "",
            "## Approval",
            "",
            f"Approval requirement: {approval_cell}.",
            f"Suggested user phrase: \"{approval_phrase}\"",
            f"Next action after approval: {next_action}",
            "",
        ]
    )
    if gate_open:
        lines.extend(
            [
                "## Status",
                "",
                "This gate is already open.",
                "",
            ]
        )
    return "\n".join(lines)


def ensure_ready(report: ValidationReport, *, for_compile: bool = False, for_dralgo_run: bool = False) -> None:
    if for_dralgo_run:
        if report.ready_for_dralgo_run:
            return
        preview = "; ".join(f"{issue.field_key}: {issue.message}" for issue in report.dralgo_blocking_issues[:5])
        raise ThreeDeftBlocked(preview or "3DEFT contract is not ready for DRalgo generation/run.")
    if for_compile:
        if report.ready_for_compile:
            return
        if report.compile_blocking_issues:
            preview = "; ".join(f"{issue.field_key}: {issue.message}" for issue in report.compile_blocking_issues[:5])
            raise ThreeDeftBlocked(preview or "3DEFT contract is not ready for PhaseTracer compile.")
    elif report.ready_for_run:
        return
    preview = "; ".join(f"{issue.field_key}: {issue.message}" for issue in report.issues[:5])
    if for_compile and report.ready_for_run and not report.ready_for_compile:
        preview = "PhaseTracer.approve_compile must be true before compile."
    raise ThreeDeftBlocked(preview or "3DEFT contract is not ready.")


def _validate_parent_theory(contract: dict[str, Any], issues: list[ValidationIssue]) -> None:
    parent = contract["parent_theory_4d"]
    for table_name in ("gauge_groups", "scalar_multiplets", "scalar_invariant_basis", "scalar_potential_construction_map", "dralgo_construction_fields"):
        rows = parent.get(table_name, [])
        if not rows or _rows_have_placeholders(rows):
            issues.append(_issue(f"parent_theory_4d.{table_name}", "required_parent_theory", f"{table_name} must be fully reviewed."))
    _validate_lagrangian_reserved_scale_symbols(parent, issues)
    _validate_scalar_potential_construction_map(parent, issues)

    fermions = parent.get("fermion_multiplets", [])
    if not fermions or _rows_have_placeholders(fermions):
        issues.append(
            _issue(
                "parent_theory_4d.fermion_multiplets",
                "required_fermion_multiplets",
                "Fermion multiplets must be supplied, or explicitly recorded as none with retained=false.",
            )
        )
    else:
        for index, row in enumerate(fermions):
            retained = _norm(row.get("retained"))
            if retained not in BOOL_TRUE | BOOL_FALSE:
                issues.append(
                    _issue(
                        f"parent_theory_4d.fermion_multiplets[{index}].retained",
                        "fermion_retained_policy_missing",
                        "Each fermion multiplet needs retained=true/false.",
                    )
                )

    yukawa = parent.get("yukawa_sector", [])
    if not yukawa or _rows_have_placeholders(yukawa):
        issues.append(
            _issue(
                "parent_theory_4d.yukawa_sector",
                "required_yukawa_policy",
                "Yukawa retained/ignored policy must be fully reviewed.",
            )
        )
    elif _yukawa_sector_requires_map(yukawa):
        rows = parent.get("yukawa_construction_map", [])
        if not rows or _rows_have_placeholders(rows):
            issues.append(
                _issue(
                    "parent_theory_4d.yukawa_construction_map",
                    "required_yukawa_construction_map",
                    "Reviewed Yukawa terms require a coupling -> invariant label -> operator/invariant-expression map.",
                )
            )
        else:
            for index, row in enumerate(rows):
                prefix = f"parent_theory_4d.yukawa_construction_map[{index}]"
                for key in ("coupling", "invariant_label", "operator_structure", "invariant_expression"):
                    if _missing(row.get(key)):
                        issues.append(_issue(f"{prefix}.{key}", "missing_yukawa_map_value", f"Yukawa construction map row needs {key}."))
                status = _norm(row.get("status"))
                if status not in REVIEW_STATUS_VALUES:
                    issues.append(_issue(f"{prefix}.status", "invalid_review_status", "Yukawa construction map status must be reviewed."))


def _validate_scalar_potential_construction_map(parent: dict[str, Any], issues: list[ValidationIssue]) -> None:
    rows = parent.get("scalar_potential_construction_map", [])
    if not rows or _rows_have_placeholders(rows):
        return
    for index, row in enumerate(rows):
        prefix = f"parent_theory_4d.scalar_potential_construction_map[{index}]"
        for key in ("coupling", "invariant_label", "operator_structure", "potential_term", "tensor_derivative"):
            if _missing(row.get(key)):
                issues.append(_issue(f"{prefix}.{key}", "missing_scalar_potential_map_value", f"Scalar potential construction map row needs {key}."))
        status = _norm(row.get("status"))
        if status not in REVIEW_STATUS_VALUES:
            issues.append(_issue(f"{prefix}.status", "invalid_review_status", "Scalar potential construction map status must be reviewed."))


def _validate_lagrangian_reserved_scale_symbols(parent: dict[str, Any], issues: list[ValidationIssue]) -> None:
    tables = {
        "scalar_invariant_basis": ("invariant_label", "operator_structure", "expression", "source"),
        "scalar_potential_construction_map": ("coupling", "invariant_label", "operator_structure", "potential_term"),
        "yukawa_construction_map": ("coupling", "invariant_label", "operator_structure", "invariant_expression"),
    }
    for table_name, columns in tables.items():
        for index, row in enumerate(parent.get(table_name, [])):
            if not row:
                continue
            for column in columns:
                if column not in row:
                    continue
                if _missing(row.get(column)):
                    continue
                symbols = _identifier_tokens(row.get(column, ""))
                reserved = sorted(symbols & LAGRANGIAN_RESERVED_SCALE_SYMBOLS)
                if not reserved:
                    continue
                issues.append(
                    _issue(
                        f"parent_theory_4d.{table_name}[{index}].{column}",
                        "lagrangian_scale_symbol_collision",
                        "The reviewed 4D Lagrangian uses reserved renormalization-scale symbol(s) "
                        f"{', '.join(reserved)}. These names are reserved for scale choices; rename the model variable before DRalgo run/compile.",
                    )
                )


def _yukawa_sector_requires_map(rows: list[dict[str, Any]]) -> bool:
    for row in rows:
        retained = _norm(row.get("retained_terms"))
        policy = _norm(row.get("policy"))
        if retained and retained not in {"none", "no", "false", "ignored", "not_applicable", "notapplicable", "na", "n/a"}:
            return True
        if policy and policy not in {"none", "no_yukawa", "ignored", "not_applicable", "notapplicable", "na", "n/a"}:
            return True
    return False


def _validate_input_parameters(contract: dict[str, Any], issues: list[ValidationIssue]) -> None:
    rows = _contract_input_parameters(contract)
    if not rows or _rows_have_placeholders(rows):
        issues.append(_issue("input_parameters", "required_input_parameters", "At least one fully reviewed input parameter row is required."))
        return
    for index, row in enumerate(rows):
        prefix = f"input_parameters[{index}]"
        name = row.get("name", "")
        if _missing(name):
            issues.append(_issue(f"{prefix}.name", "missing_input_parameter_name", "Input parameter name is required."))
        elif not _is_identifier(name):
            issues.append(_issue(f"{prefix}.name", "invalid_identifier", "Input parameter names must be C++/Python identifiers."))
        elif _is_reserved_identifier(name):
            issues.append(_issue(f"{prefix}.name", "reserved_identifier", _reserved_message("Input parameter name", name)))
        elif str(name).strip() in LAGRANGIAN_RESERVED_SCALE_SYMBOLS:
            issues.append(
                _issue(
                    f"{prefix}.name",
                    "lagrangian_scale_symbol_collision",
                    f"Input parameter name {name!r} is reserved for renormalization-scale choices; rename the Lagrangian parameter before DRalgo run/compile.",
                )
            )
        if not _evidence_reviewed_status(row.get("review_status")):
            issues.append(
                _issue(
                    f"{prefix}.review_status",
                    "input_parameter_not_user_confirmed",
                    "Input parameter review_status must be source_reviewed/dralgo_generated/user_confirmed/human_reviewed; agent_reviewed is not enough without source evidence.",
                )
            )
        required_keys = ("test_value",) if _is_scale_control_input_row(row) else ("test_value", "input_scale")
        for key in required_keys:
            if _missing(row.get(key)):
                issues.append(_issue(f"{prefix}.{key}", "required_input_parameter_metadata", f"Input parameter {name or index} needs {key}."))
        if not _is_number(row.get("test_value")):
            issues.append(_issue(f"{prefix}.test_value", "invalid_test_value", "test_value must be numeric for reference evaluation."))


def _validate_rg_matching(contract: dict[str, Any], issues: list[ValidationIssue]) -> None:
    rg = contract.get("rg_matching", {})
    for key in ("four_d_to_three_d_matching_scale", "four_d_to_three_d_matching_scale_variation"):
        _require_value(issues, rg, key, f"rg_matching.{key}")
    _validate_scale_expression_symbols(
        contract,
        "rg_matching.four_d_to_three_d_matching_scale",
        rg.get("four_d_to_three_d_matching_scale", ""),
        issues,
    )
    if not _evidence_reviewed_status(rg.get("scale_policy_review_status")):
        issues.append(
            _issue(
                "rg_matching.scale_policy_review_status",
                "scale_policy_not_user_confirmed",
                "RG/matching scale policy must be source_reviewed/dralgo_generated/user_confirmed/human_reviewed; agent_reviewed is not enough for four_d_to_three_d_matching_scale, mu_3_scale, and mu_3_us_scale without source evidence.",
            )
        )
    beta_variable = _norm(rg.get("beta_function_variable", "log_mu"))
    if beta_variable not in BETA_VARIABLE_VALUES_4D:
        issues.append(
            _issue(
                "rg_matching.beta_function_variable",
                "unsupported_beta_variable",
                "Internal 4D RG beta_function_variable expects log_mu or log_mu_squared when explicitly overridden.",
            )
        )
    beta_dependent_policy = _norm(rg.get("four_d_beta_dependent_variable_policy", "dralgo_native"))
    if beta_dependent_policy not in FOUR_D_BETA_DEPENDENT_VARIABLE_POLICIES:
        issues.append(
            _issue(
                "rg_matching.four_d_beta_dependent_variable_policy",
                "required_beta_dependent_variable_policy",
                "Internal 4D RG beta policy must be dralgo_native or converted_to_contract_variables when explicitly overridden.",
            )
        )
    if _norm(rg.get("ode_solver", "rk4")) != "rk4":
        issues.append(_issue("rg_matching.ode_solver", "unsupported_ode_solver", "Internal 4D RG currently supports the RK4 solver."))
    if not _is_positive_int(str(rg.get("ode_steps", "64")).strip()):
        issues.append(_issue("rg_matching.ode_steps", "invalid_ode_steps", "Internal 4D RG needs ode_steps as a positive integer when explicitly overridden."))
    _validate_rg_beta_functions(contract, issues)
    for key in ("hard_matching_order", "soft_matching_order", "effective_potential_order", "pressure_order"):
        value = _norm(rg.get(key))
        if value not in ORDER_VALUES:
            issues.append(_issue(f"rg_matching.{key}", "required_order", f"{key} must be LO, NLO, NNLO, or not_applicable according to explicit source output calls."))
    for key in ("hard_matching_source", "soft_matching_source", "effective_potential_source", "pressure_source"):
        value = _norm(rg.get(key))
        if value not in ORDER_SOURCE_VALUES:
            issues.append(
                _issue(
                    f"rg_matching.{key}",
                    "required_order_source",
                    f"{key} must be dralgo_generated or not_applicable.",
                )
            )


def _validate_rg_beta_functions(contract: dict[str, Any], issues: list[ValidationIssue]) -> None:
    inputs = [row for row in _contract_input_parameters(contract) if not _is_scale_control_input_row(row)]
    input_names = {
        str(row.get("name", "")).strip()
        for row in inputs
        if _is_identifier(row.get("name", "")) and not _is_reserved_identifier(row.get("name", ""))
    }
    if not input_names:
        issues.append(_issue("rg_beta_functions", "required_beta_inputs", "4D running needs reviewed input parameters before beta functions can be audited."))
        return

    input_scales = {str(row.get("input_scale", "")).strip() for row in inputs if not _missing(row.get("input_scale"))}
    if len(input_scales) > 1:
        issues.append(
            _issue(
                "input_parameters.input_scale",
                "runtime_rg_requires_common_scale",
                "4D running of coupled beta functions currently requires all input parameters to share one numeric input_scale.",
            )
        )
    for index, row in enumerate(inputs):
        if not _is_number(row.get("input_scale")):
            issues.append(_issue(f"input_parameters[{index}].input_scale", "invalid_runtime_rg_input_scale", "4D running needs numeric input_scale values."))

    rows = contract.get("rg_beta_functions", [])
    if not rows or _rows_have_placeholders(rows):
        issues.append(
            _issue(
                "rg_beta_functions",
                "required_rg_beta_functions",
                "4D running requires one reviewed BetaFunctions4D[] row for every input parameter.",
            )
        )
        return

    seen: set[str] = set()
    beta_dependent_policy = _norm(contract.get("rg_matching", {}).get("four_d_beta_dependent_variable_policy", "dralgo_native"))
    for index, row in enumerate(rows):
        prefix = f"rg_beta_functions[{index}]"
        parameter = str(row.get("parameter", "")).strip()
        covered_parameter = parameter
        if parameter not in input_names:
            squared_base = _squared_beta_base(parameter, input_names)
            if beta_dependent_policy == "dralgo_native" and squared_base:
                covered_parameter = squared_base
            else:
                issues.append(
                    _issue(
                        f"{prefix}.parameter",
                        "unknown_beta_parameter",
                    "Each beta parameter must match a reviewed input parameter name, or a DRalgo-native squared gauge parameter such as g1_sq.",
                    )
                )
                continue
        seen.add(covered_parameter)
        if _missing(row.get("beta_expression")):
            issues.append(_issue(f"{prefix}.beta_expression", "missing_beta_expression", "beta_expression is required; use 0 for exactly non-running inputs."))
        if _missing(row.get("source")):
            issues.append(_issue(f"{prefix}.source", "missing_beta_source", "Beta source must identify BetaFunctions4D[] from the reviewed DRalgo run."))
        if _norm(row.get("status")) not in REVIEW_STATUS_VALUES:
            issues.append(_issue(f"{prefix}.status", "invalid_review_status", "Beta function status must be reviewed."))
    missing = input_names - seen
    if missing:
        issues.append(
            _issue(
                "rg_beta_functions",
                "missing_beta_parameters",
                f"Missing beta functions for input parameters: {', '.join(sorted(missing))}.",
            )
        )


def _squared_beta_base(parameter: str, input_names: set[str]) -> str:
    for suffix in ("_sq", "sq"):
        if parameter.endswith(suffix):
            base = parameter[: -len(suffix)]
            if base in input_names:
                return base
    return ""


def _validate_eft_stages(contract: dict[str, Any], issues: list[ValidationIssue]) -> None:
    stages = contract.get("eft_stages", [])
    if not stages or _rows_have_placeholders(stages):
        issues.append(_issue("eft_stages", "required_eft_stages", "hard, soft, and ultrasoft EFT stages must be auditable."))
    soft_rows = [row for row in stages if _norm(row.get("stage")) == "soft"]
    scalar_map = contract.get("scalar_index_map", [])
    if not scalar_map or _rows_have_placeholders(scalar_map):
        issues.append(
            _issue(
                "scalar_index_map",
                "required_scalar_index_map",
                "PrintScalarRepPositions[] output is required before PerformDRsoft[...] can be audited.",
            )
        )
        return
    mapped_indices = {_norm(row.get("scalar_index")) for row in scalar_map if not _missing(row.get("scalar_index"))}
    for index, row in enumerate(scalar_map):
        prefix = f"scalar_index_map[{index}]"
        if _missing(row.get("field_component")) or _missing(row.get("parent_multiplet")):
            issues.append(_issue(prefix, "incomplete_scalar_index_map", "Each scalar index needs field component and parent multiplet."))
        if _norm(row.get("integrated_out")) not in BOOL_TRUE | BOOL_FALSE:
            issues.append(_issue(f"{prefix}.integrated_out", "integrated_out_missing", "integrated_out must be true or false."))
        if _missing(row.get("print_scalar_rep_positions_evidence")):
            issues.append(_issue(f"{prefix}.evidence", "missing_scalar_position_evidence", "PrintScalarRepPositions[] evidence is required."))
    for soft_index, row in enumerate(soft_rows):
        raw = str(row.get("integrated_scalar_indices", ""))
        if _missing(raw):
            issues.append(_issue(f"eft_stages.soft[{soft_index}].integrated_scalar_indices", "missing_soft_indices", "Soft matching must state integrated scalar indices."))
            continue
        for scalar_index in _extract_indices(raw):
            if _norm(scalar_index) not in mapped_indices:
                issues.append(
                    _issue(
                        f"eft_stages.soft[{soft_index}].integrated_scalar_indices",
                        "unmapped_soft_index",
                        f"PerformDRsoft index {scalar_index} is not present in Scalar Index Map.",
                    )
                )

def _validate_3d_consumption(contract: dict[str, Any], issues: list[ValidationIssue]) -> None:
    fields = contract.get("three_d_fields", [])
    if not fields:
        issues.append(_issue("three_d_fields", "required_3d_fields", "3D fields and PhaseTracer slot order must be reviewed."))
    else:
        if _rows_have_placeholders(fields):
            issues.append(_issue("three_d_fields", "required_3d_fields", "3D fields and PhaseTracer slot order must be reviewed."))
        slots: set[str] = set()
        for index, row in enumerate(fields):
            name = row.get("name", "")
            if _missing(name) or not _is_identifier(name):
                issues.append(_issue(f"three_d_fields[{index}].name", "invalid_3d_field", "3D field names must be valid identifiers."))
            elif _is_reserved_identifier(name):
                issues.append(_issue(f"three_d_fields[{index}].name", "reserved_identifier", _reserved_message("3D field name", name)))
            normalization = row.get("normalization", "")
            if _missing(normalization):
                issues.append(_issue(f"three_d_fields[{index}].normalization", "missing_field_normalization", "Each 3D field must use an explicit normalization enum such as canonical_3d or identity."))
            elif _field_normalization_mode(normalization) == "unsupported":
                issues.append(
                    _issue(
                        f"three_d_fields[{index}].normalization",
                        "unsupported_field_normalization",
                        "Field normalization must be a reviewed enum such as canonical_3d, identity, or phase_tracer_times_sqrt_t; do not store raw formulas like phi[i]/sqrt(T).",
                    )
                )
            if _missing(row.get("dralgo_background_symbol")):
                issues.append(
                    _issue(
                        f"three_d_fields[{index}].dralgo_background_symbol",
                        "missing_background_symbol",
                        "Each PhaseTracer field needs the corresponding DRalgo/Mathematica background symbol.",
                    )
                )
            slot = _norm(row.get("phase_tracer_slot"))
            if _missing(slot):
                issues.append(_issue(f"three_d_fields[{index}].phase_tracer_slot", "missing_field_slot", "PhaseTracer slot order is required."))
            elif slot in slots:
                issues.append(_issue(f"three_d_fields[{index}].phase_tracer_slot", "duplicate_field_slot", "PhaseTracer slots must be unique."))
            slots.add(slot)
    coeffs = contract.get("three_d_coefficients", [])
    if not coeffs or _rows_have_placeholders(coeffs):
        issues.append(_issue("three_d_coefficients", "required_3d_coefficients", "3D coefficient map must be reviewed."))
    else:
        for index, row in enumerate(coeffs):
            name = row.get("name", "")
            if _missing(name) or not _is_identifier(name):
                issues.append(_issue(f"three_d_coefficients[{index}].name", "invalid_3d_coefficient", "3D coefficient names must be valid identifiers."))
            elif _is_reserved_identifier(name):
                issues.append(_issue(f"three_d_coefficients[{index}].name", "reserved_identifier", _reserved_message("3D coefficient name", name)))
    _validate_3d_coefficient_order_sums(contract, issues)
    consumption = contract.get("three_d_consumption", {})
    for key in ("field_normalization", "potential_prefactor", "v3d_expression", "v_phase_tracer_expression", "coefficient_eval_mode", "temperature_dependence"):
        _require_value(issues, consumption, key, f"three_d_consumption.{key}")
    if not _missing(consumption.get("field_normalization")) and _field_normalization_mode(consumption.get("field_normalization")) == "unsupported":
        issues.append(
            _issue(
                "three_d_consumption.field_normalization",
                "unsupported_field_normalization",
                "field_normalization must be a reviewed enum such as canonical_3d or identity; the detailed per-field map belongs in 3D Fields.",
            )
        )
    _validate_3d_consumption_symbols(contract, issues)


def _validate_3d_coefficient_order_sums(contract: dict[str, Any], issues: list[ValidationIssue]) -> None:
    rows = contract.get("three_d_coefficient_order_sums", [])
    if not rows or _rows_have_placeholders(rows):
        return
    coefficient_expressions = {
        str(row.get("name", "")).strip(): str(row.get("expression_or_interpolator", "")).strip()
        for row in contract.get("three_d_coefficients", [])
        if _is_identifier(row.get("name", ""))
    }
    for index, row in enumerate(rows):
        base = str(row.get("base_parameter", "")).strip()
        if _norm(base) in {"not_applicable", "not applicable", "none", ""} or _missing(base):
            continue
        prefix = f"three_d_coefficient_order_sums[{index}]"
        if base not in coefficient_expressions:
            issues.append(_issue(f"{prefix}.base_parameter", "unknown_order_sum_base", "Order-sum base_parameter must be present in 3D Coefficients."))
            continue
        components = _csv(row.get("components", ""))
        if not components:
            issues.append(_issue(f"{prefix}.components", "missing_order_sum_components", "Order-sum rows must list LO/NLO/NNLO component coefficients."))
            continue
        unknown = [component for component in components if component not in coefficient_expressions]
        if unknown:
            issues.append(
                _issue(
                    f"{prefix}.components",
                    "unknown_order_sum_components",
                    f"Order-sum components must be present in 3D Coefficients: {', '.join(unknown)}.",
                )
            )
        expression = str(row.get("expression", "")).strip()
        expected_expression = " + ".join(components)
        if expression and expression != expected_expression:
            issues.append(
                _issue(
                    f"{prefix}.expression",
                    "inconsistent_order_sum_expression",
                    f"Order-sum expression should be `{expected_expression}` to match components.",
                )
            )
        coefficient_expression = coefficient_expressions.get(base, "")
        if coefficient_expression and coefficient_expression != expected_expression:
            issues.append(
                _issue(
                    f"three_d_coefficients.{base}",
                    "inconsistent_order_sum_coefficient",
                    f"3D Coefficients row for {base} should use `{expected_expression}` to match the reviewed order-sum table.",
                )
            )


def _validate_3d_consumption_symbols(contract: dict[str, Any], issues: list[ValidationIssue]) -> None:
    fields = {
        str(row.get("name", "")).strip()
        for row in contract.get("three_d_fields", [])
        if _is_identifier(row.get("name", ""))
    }
    field_symbol_map = {
        str(row.get("dralgo_background_symbol", "")).strip(): str(row.get("name", "")).strip()
        for row in contract.get("three_d_fields", [])
        if _is_identifier(row.get("name", "")) and not _missing(row.get("dralgo_background_symbol"))
    }
    coefficients = {
        str(row.get("name", "")).strip()
        for row in contract.get("three_d_coefficients", [])
        if _is_identifier(row.get("name", ""))
    }
    input_parameters = {
        str(row.get("name", "")).strip()
        for row in _contract_input_parameters(contract)
        if _is_identifier(row.get("name", ""))
    }
    if not fields or not coefficients:
        return

    allowed = fields | coefficients | input_parameters | set(THREE_D_SCALE_SYMBOLS) | {"T", "pi"}
    consumption = contract.get("three_d_consumption", {})
    for key in ("v3d_expression", "v_phase_tracer_expression"):
        expression = str(consumption.get(key, "")).strip()
        if _missing(expression):
            continue
        expression = _normalize_contract_expression(expression, field_symbol_map=field_symbol_map)
        try:
            used = _expression_identifiers(expression)
        except SyntaxError as exc:
            issues.append(
                _issue(
                    f"three_d_consumption.{key}",
                    "invalid_expression_syntax",
                    f"{key} must be a Python-like scalar expression before PhaseTracer generation: {exc.msg}.",
                )
            )
            continue
        unmapped = sorted(used - allowed)
        if unmapped:
            order_sum_hints = _missing_order_sum_hints(unmapped, coefficients)
            extra = ""
            if order_sum_hints:
                extra = " " + " ".join(order_sum_hints)
            issues.append(
                _issue(
                    f"three_d_consumption.{key}",
                    "unmapped_3d_potential_symbols",
                    "Every non-field/non-scale symbol in V3D must be declared as a reviewed input parameter "
                    "or in the 3D Coefficients table. "
                    f"Missing reviewed input/3D coefficient rows or symbol-audit repairs for: {', '.join(unmapped)}."
                    f"{extra}",
                )
            )


def _missing_order_sum_hints(unmapped: list[str], coefficients: set[str]) -> list[str]:
    hints: list[str] = []
    for name in unmapped:
        components = [
            f"{name}_{order}"
            for order in ("LO", "NLO", "NNLO")
            if f"{name}_{order}" in coefficients
        ]
        components.extend(
            f"{name}{order}"
            for order in ("LO", "NLO", "NNLO")
            if f"{name}{order}" in coefficients
        )
        if components:
            hints.append(
                f"For `{name}`, ordered pieces exist ({', '.join(components)}); add a reviewed total row "
                f"`{name} = {' + '.join(components)}` and record it in `3D Coefficient Order Sums`."
            )
    return hints


def _validate_parameter_evaluator(contract: dict[str, Any], issues: list[ValidationIssue]) -> None:
    rows = contract.get("three_d_parameter_evaluator", [])
    if not rows:
        issues.append(
            _issue(
                "three_d_parameter_evaluator",
                "required_parameter_evaluator",
                "3D Parameter Evaluator must define get_3d_parameters(T), coefficient order, evaluator mode, and running owner.",
            )
        )
        return
    if _rows_have_placeholders(rows):
        issues.append(
            _issue(
                "three_d_parameter_evaluator",
                "required_parameter_evaluator",
                "3D Parameter Evaluator must define get_3d_parameters(T), coefficient order, evaluator mode, running owner, mu_3_scale, and mu_3_us_scale.",
            )
        )

    by_key = {str(row.get("key", "")).strip(): row for row in rows if not _missing(row.get("key"))}
    for key in PARAMETER_EVALUATOR_KEYS:
        row = by_key.get(key)
        if row is None:
            issues.append(_issue(f"three_d_parameter_evaluator.{key}", "missing_parameter_evaluator_key", f"Missing 3D parameter evaluator key {key}."))
            continue
        if _missing(row.get("value")):
            issues.append(_issue(f"three_d_parameter_evaluator.{key}", "missing_parameter_evaluator_value", f"{key} must be user-confirmed."))
        status = _norm(row.get("status"))
        if status not in REVIEW_STATUS_VALUES:
            issues.append(_issue(f"three_d_parameter_evaluator.{key}.status", "invalid_review_status", f"{key} status must be reviewed."))

    function_name = str(by_key.get("function_name", {}).get("value", "")).strip()
    if function_name and function_name != "get_3d_parameters":
        issues.append(
            _issue(
                "three_d_parameter_evaluator.function_name",
                "unexpected_parameter_function",
                "The independent PhaseTracer renderer expects function_name=get_3d_parameters.",
            )
        )

    mode = _norm(by_key.get("evaluator_mode", {}).get("value"))
    if mode and mode not in PARAMETER_EVALUATOR_MODES:
        issues.append(
            _issue(
                "three_d_parameter_evaluator.evaluator_mode",
                "unsupported_parameter_evaluator_mode",
                f"evaluator_mode must be one of {sorted(PARAMETER_EVALUATOR_MODES)}.",
            )
        )
    owner = _norm(by_key.get("running_owner", {}).get("value"))
    if owner and owner not in PARAMETER_RUNNING_OWNERS:
        issues.append(
            _issue(
                "three_d_parameter_evaluator.running_owner",
                "unsupported_parameter_running_owner",
                f"running_owner must be one of {sorted(PARAMETER_RUNNING_OWNERS)}.",
            )
        )

    _validate_three_d_scale_rows(contract, by_key, issues)

    coefficient_order = _csv(by_key.get("coefficient_order", {}).get("value", ""))
    coefficient_names = {
        str(row.get("name", "")).strip()
        for row in contract.get("three_d_coefficients", [])
        if _is_identifier(row.get("name", ""))
    }
    if not coefficient_order:
        issues.append(
            _issue(
                "three_d_parameter_evaluator.coefficient_order",
                "missing_coefficient_order",
                "coefficient_order must list the 3D coefficient names returned by get_3d_parameters(T).",
            )
        )
        return
    unknown = set(coefficient_order) - coefficient_names
    missing = coefficient_names - set(coefficient_order)
    if unknown:
        issues.append(
            _issue(
                "three_d_parameter_evaluator.coefficient_order",
                "unknown_evaluator_coefficients",
                f"coefficient_order contains names not present in 3D Coefficients: {', '.join(sorted(unknown))}.",
            )
        )
    if missing:
        issues.append(
            _issue(
                "three_d_parameter_evaluator.coefficient_order",
                "missing_evaluator_coefficients",
                f"coefficient_order is missing 3D Coefficients entries: {', '.join(sorted(missing))}.",
            )
        )


def _validate_three_d_scale_rows(contract: dict[str, Any], by_key: dict[str, dict[str, str]], issues: list[ValidationIssue]) -> None:
    for key, row in by_key.items():
        if key not in LEGACY_THREE_D_RG_KEYS:
            continue
        value = row.get("value")
        normalized = _norm(value)
        if normalized in {"", "not_applicable", "not applicable", "none", "false", "no", "0"} or _missing(value):
            continue
        issues.append(
            _issue(
                f"three_d_parameter_evaluator.{key}",
                "legacy_3d_runtime_rg_removed",
                "This legacy 3DUS policy row is no longer accepted. "
                "Use the mandatory mu_3_scale / mu_3_us_scale rows and DRalgo BetaFunctions3DUS[] output instead.",
            )
        )

    for key in THREE_D_SCALE_KEYS:
        row = by_key.get(key)
        if row is None:
            issues.append(
                _issue(
                    f"three_d_parameter_evaluator.{key}",
                    "missing_3d_scale_substitution",
                    f"{key} is mandatory. Add a reviewed scale expression.",
                )
            )
            continue
        value = row.get("value")
        if _missing(value) or _norm(value) in {"not_applicable", "not applicable", "none", "false", "no", "0"}:
            issues.append(
                _issue(
                    f"three_d_parameter_evaluator.{key}",
                    "missing_3d_scale_substitution",
                    f"{key} must be user-confirmed for the 3D soft/ultrasoft parameter evaluation.",
                )
            )
        else:
            _validate_scale_expression_symbols(contract, f"three_d_parameter_evaluator.{key}", value, issues)
        if not _evidence_reviewed_status(row.get("status")):
            issues.append(
                _issue(
                    f"three_d_parameter_evaluator.{key}.status",
                    "scale_not_user_confirmed",
                    f"{key} status must be source_reviewed/dralgo_generated/user_confirmed/human_reviewed; agent_reviewed is not enough without source evidence.",
                )
            )


def _validate_three_d_beta_functions(contract: dict[str, Any], issues: list[ValidationIssue]) -> None:
    rows = contract.get("three_d_beta_functions", [])
    if not rows or _rows_have_placeholders(rows):
        return
    coefficient_names = {
        str(row.get("name", "")).strip()
        for row in contract.get("three_d_coefficients", [])
        if _is_identifier(row.get("name", "")) and not _is_reserved_identifier(row.get("name", ""))
    }
    if not coefficient_names:
        issues.append(_issue("three_d_beta_functions", "required_3d_beta_coefficients", "3D beta-function audit rows need reviewed 3D coefficient names."))
        return

    seen: set[str] = set()
    for index, row in enumerate(rows):
        prefix = f"three_d_beta_functions[{index}]"
        parameter = str(row.get("parameter", "")).strip()
        if parameter not in coefficient_names:
            issues.append(_issue(f"{prefix}.parameter", "unknown_3d_beta_parameter", "Each 3D beta parameter must match a reviewed 3D coefficient name."))
            continue
        seen.add(parameter)
        if _missing(row.get("beta_expression")):
            issues.append(_issue(f"{prefix}.beta_expression", "missing_3d_beta_expression", "3D beta_expression is required; use 0 for exactly non-running coefficients."))
        if _missing(row.get("source")):
            issues.append(_issue(f"{prefix}.source", "missing_3d_beta_source", "3D beta source must identify BetaFunctions3DUS[] from the reviewed DRalgo run."))
        if _norm(row.get("status")) not in REVIEW_STATUS_VALUES:
            issues.append(_issue(f"{prefix}.status", "invalid_review_status", "3D beta function status must be reviewed."))
    missing = coefficient_names - seen
    if missing:
        return


def _validate_symbol_audit(contract: dict[str, Any], issues: list[ValidationIssue]) -> None:
    rows = contract.get("symbol_audit", [])
    if not rows or _rows_have_placeholders(rows):
        issues.append(
            _issue(
                "symbol_audit",
                "required_symbol_audit",
                "Mathematica Symbol Audit must map raw DRalgo symbols to suggested/compiler symbols, physics roles, and CForm-safe symbols.",
            )
        )
        return

    fields = contract.get("three_d_fields", [])
    field_names = {str(row.get("name", "")).strip() for row in fields if not _missing(row.get("name"))}
    field_raw_symbols = {
        str(row.get("dralgo_background_symbol", "")).strip()
        for row in fields
        if not _missing(row.get("dralgo_background_symbol"))
    }
    audited_field_names: set[str] = set()
    audited_raw_symbols: set[str] = set()

    for index, row in enumerate(rows):
        prefix = f"symbol_audit[{index}]"
        raw_symbol = str(row.get("raw_symbol", "")).strip()
        suggested_symbol = str(row.get("suggested_symbol", "")).strip()
        compiler_symbol = str(row.get("compiler_symbol", "")).strip()
        cform_temp_symbol = str(row.get("cform_temp_symbol", "")).strip()
        physics_role = _norm(row.get("physics_role", row.get("role")))
        status = _norm(row.get("status"))
        if _missing(raw_symbol):
            issues.append(_issue(f"{prefix}.raw_symbol", "missing_raw_symbol", "Raw Mathematica/DRalgo symbol is required."))
        if not _is_identifier(suggested_symbol):
            issues.append(_issue(f"{prefix}.suggested_symbol", "invalid_suggested_symbol", "suggested_symbol must be a C++/Python identifier."))
        elif _is_reserved_identifier(suggested_symbol):
            issues.append(_issue(f"{prefix}.suggested_symbol", "reserved_identifier", _reserved_message("suggested_symbol", suggested_symbol)))
        if physics_role not in SYMBOL_PHYSICS_ROLES:
            issues.append(_issue(f"{prefix}.physics_role", "invalid_symbol_physics_role", f"physics_role must be one of {sorted(SYMBOL_PHYSICS_ROLES)}."))
        if not _is_identifier(compiler_symbol):
            issues.append(_issue(f"{prefix}.compiler_symbol", "invalid_compiler_symbol", "compiler_symbol must be a C++/Python identifier."))
        elif _is_reserved_identifier(compiler_symbol):
            issues.append(_issue(f"{prefix}.compiler_symbol", "reserved_identifier", _reserved_message("compiler_symbol", compiler_symbol)))
        if not _is_cform_temp_symbol(cform_temp_symbol):
            issues.append(
                _issue(
                    f"{prefix}.cform_temp_symbol",
                    "invalid_cform_temp_symbol",
                    "cform_temp_symbol must contain only ASCII letters/digits, start with a letter, and contain no underscore.",
                )
            )
        if _missing(row.get("source")):
            issues.append(_issue(f"{prefix}.source", "missing_symbol_source", "Each symbol audit row needs a source."))
        if status not in REVIEW_STATUS_VALUES:
            issues.append(_issue(f"{prefix}.status", "invalid_review_status", f"status must be one of {sorted(REVIEW_STATUS_VALUES)}."))
        if physics_role == "field":
            audited_field_names.add(compiler_symbol)
            audited_raw_symbols.add(raw_symbol)
            if compiler_symbol not in field_names:
                issues.append(
                    _issue(
                        f"{prefix}.compiler_symbol",
                        "field_symbol_not_in_3d_fields",
                        "Field compiler_symbol must match a reviewed 3D Fields.name entry.",
                    )
                )
            if raw_symbol not in field_raw_symbols:
                issues.append(
                    _issue(
                        f"{prefix}.raw_symbol",
                        "field_raw_symbol_not_in_3d_fields",
                        "Field raw_symbol must match a reviewed 3D Fields.dralgo_background_symbol entry.",
                    )
                )
    missing_field_names = field_names - audited_field_names
    missing_raw_symbols = field_raw_symbols - audited_raw_symbols
    if missing_field_names:
        issues.append(
            _issue(
                "symbol_audit.fields",
                "missing_field_symbol_audit",
                f"Every 3D field needs a symbol audit row; missing compiler symbols: {', '.join(sorted(missing_field_names))}.",
            )
        )
    if missing_raw_symbols:
        issues.append(
            _issue(
                "symbol_audit.raw_fields",
                "missing_field_raw_symbol_audit",
                f"Every 3D field raw symbol needs audit; missing raw symbols: {', '.join(sorted(missing_raw_symbols))}.",
            )
        )


def _validate_phasetracer_interface(contract: dict[str, Any], issues: list[ValidationIssue]) -> None:
    rows = contract.get("phasetracer_interface", [])
    if not rows:
        issues.append(
            _issue(
                "phasetracer_interface",
                "required_phasetracer_interface",
                "PhaseTracer Potential Interface table must be reviewed before generating V(phi,T).",
            )
        )
        return

    by_key = {str(row.get("key", "")).strip(): row for row in rows if not _missing(row.get("key"))}
    for key in PHASETRACER_INTERFACE_KEYS:
        row = by_key.get(key)
        if row is None:
            issues.append(_issue(f"phasetracer_interface.{key}", "missing_interface_key", f"Missing PhaseTracer interface key {key}."))
            continue
        if _missing(row.get("value")):
            issues.append(_issue(f"phasetracer_interface.{key}", "missing_interface_value", f"{key} must be user-confirmed."))
        status = _norm(row.get("status"))
        if key in PHASETRACER_USER_CONFIRMED_HOOK_KEYS and not _user_confirmed_status(row.get("status")):
            issues.append(
                _issue(
                    f"phasetracer_interface.{key}.status",
                    "phasetracer_hook_not_user_confirmed",
                    f"{key} controls PhaseTracer apply_symmetry()/phase hooks and must be user_confirmed/human_reviewed after the agent presents a source-backed recommendation and reason and ask the user to confirm the hook policy.",
                )
            )
        elif status not in REVIEW_STATUS_VALUES:
            issues.append(_issue(f"phasetracer_interface.{key}.status", "invalid_review_status", f"{key} status must be reviewed."))

    base = str(by_key.get("phase_tracer_base_class", {}).get("value", ""))
    if base and "EffectivePotential::Potential" not in base:
        issues.append(
            _issue(
                "phasetracer_interface.phase_tracer_base_class",
                "unexpected_phasetracer_base",
                "3DEFT v1 expects the PhaseTracer EffectivePotential::Potential interface.",
            )
        )
    potential_method = str(by_key.get("potential_method", {}).get("value", ""))
    if potential_method and ("V(" not in potential_method or "T" not in potential_method):
        issues.append(
            _issue(
                "phasetracer_interface.potential_method",
                "unexpected_potential_method",
                "PhaseTracer potential_method should identify V(Eigen::VectorXd phi, double T).",
            )
        )
    field_count = str(by_key.get("field_count_method", {}).get("value", ""))
    if field_count and "get_n_scalars" not in field_count:
        issues.append(
            _issue(
                "phasetracer_interface.field_count_method",
                "unexpected_field_count_method",
                "PhaseTracer field_count_method should identify get_n_scalars().",
            )
        )


def _validate_dralgo_runner(contract: dict[str, Any], issues: list[ValidationIssue]) -> None:
    runner = contract.get("runner", {})
    construction_source = _norm(runner.get("dralgo_construction_source"))
    program_path = runner.get("dralgo_program_path")

    if construction_source not in DRALGO_CONSTRUCTION_SOURCE_VALUES:
        issues.append(
            _issue(
                "runner.dralgo_construction_source",
                "required_dralgo_construction_source",
                "DRalgo construction source must be reviewed_source_file.",
            )
        )
        return

    if _missing(program_path):
        issues.append(
            _issue(
                "runner.dralgo_program_path",
                "required_dralgo_program_path",
                "reviewed_source_file requires a reviewed dralgo_program_path pointing to the Mathematica .m/.wl construction.",
            )
        )
def _validate_phasetracer_policy(contract: dict[str, Any], issues: list[ValidationIssue], *, require_compile_approval: bool) -> None:
    pt = contract.get("phasetracer", {})
    backend = _norm(pt.get("backend"))
    if backend != "phasetracer":
        issues.append(_issue("phasetracer.backend", "unsupported_backend", "3DEFT v1 supports only backend=phasetracer."))
    for key in ("reference_fields", "reference_temperature"):
        _require_value(issues, pt, key, f"phasetracer.{key}")
    if not _is_number(pt.get("reference_temperature")):
        issues.append(_issue("phasetracer.reference_temperature", "invalid_reference_temperature", "reference_temperature must be numeric."))
    reference_fields = _csv(pt.get("reference_fields", ""))
    if not reference_fields or not all(_is_number(item) for item in reference_fields):
        issues.append(_issue("phasetracer.reference_fields", "invalid_reference_fields", "reference_fields must be comma-separated numbers."))
    if require_compile_approval and not _as_bool(pt.get("approve_compile")):
        issues.append(_issue("phasetracer.approve_compile", "compile_approval_required", "Explicit PhaseTracer compile approval is required."))
    if _as_bool(pt.get("approve_compile")):
        approval_record = pt.get("approval_record")
        if _missing(approval_record):
            issues.append(
                _issue(
                    "phasetracer.approval_record",
                    "missing_approval_record",
                    "PhaseTracer approval must be recorded by the approve command after explicit user review.",
                )
            )
        elif not looks_like_compile_approval_text(approval_record):
            issues.append(
                _issue(
                    "phasetracer.approval_record",
                    "invalid_approval_record",
                    "PhaseTracer approval_record must contain the user's explicit review and approval sentence.",
                )
            )


def _issue(field_key: str, code: str, message: str) -> ValidationIssue:
    return ValidationIssue(field_key=field_key, code=code, message=message)


def _issue_blocks_dralgo_run(issue: ValidationIssue) -> bool:
    return _issue_stage(issue) == "pre_dralgo"


def _issue_stage(issue: ValidationIssue) -> str:
    key = issue.field_key
    code = issue.code
    if key == "model_policy.source_model_audit_status":
        return "pre_dralgo"
    if key.startswith(("schema", "marker", "model_policy")):
        return "pre_dralgo"
    if key.startswith("parent_theory_4d"):
        return "post_dralgo"
    if key.startswith("runner."):
        return "pre_dralgo"

    if key.startswith("input_parameters"):
        return "pre_dralgo"
    if key == "rg_matching.scale_policy_review_status":
        return "pre_dralgo"
    if key.startswith("three_d_parameter_evaluator.") and (
        "mu_3_scale" in key or "mu_3_us_scale" in key
    ):
        return "pre_dralgo"
    if key.startswith("rg_matching") or key.startswith("rg_beta_functions"):
        return "post_dralgo"
    if key.startswith("eft_stages") or key.startswith("scalar_index_map"):
        return "post_dralgo"
    if key.startswith("three_d_") or key.startswith("symbol_audit"):
        return "post_dralgo"
    if key.startswith("phasetracer_interface"):
        return "compile"
    if key.startswith("phasetracer."):
        return "compile"
    if code in {"compile_approval_required"}:
        return "gate"
    return "pre_dralgo"


def _unique_issues(issues: tuple[ValidationIssue, ...] | list[ValidationIssue]) -> list[ValidationIssue]:
    seen: set[str] = set()
    result: list[ValidationIssue] = []
    for issue in issues:
        if issue.field_key in seen:
            continue
        seen.add(issue.field_key)
        result.append(issue)
    return result


def _build_question_packets(issues: list[ValidationIssue]) -> list[QuestionPacket]:
    groups: dict[str, list[ValidationIssue]] = {}
    order: list[str] = []
    for issue in sorted(issues, key=_question_priority):
        key = _question_packet_key(issue)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(issue)
    return [QuestionPacket(key=key, issues=tuple(groups[key])) for key in order]


def _question_packet_key(issue: ValidationIssue) -> str:
    key = issue.field_key
    if key == "model_policy.source_model_audit_status":
        return "model.source_audit"
    if key.startswith("runner."):
        return "dralgo.source_file"
    if key.startswith("parent_theory_4d."):
        return "model.source_audit"
    if key.startswith("input_parameters"):
        return "input_parameters.review"
    if key.startswith("rg_matching."):
        tail = key.split(".", 1)[1]
        if "order" in tail or tail.endswith("_source"):
            return "rg_matching.orders"
        return "scale.rg_matching_policy"
    if key.startswith("three_d_parameter_evaluator."):
        tail = key.split(".", 1)[1]
        if tail in {"coefficient_order", "function_name", "evaluator_mode", "running_owner"}:
            return "three_d.parameter_evaluator"
        return "scale.rg_matching_policy"
    if key.startswith("three_d_consumption") or key.startswith("three_d_fields"):
        return "phasetracer.v3d_mapping"
    if key.startswith("phasetracer_interface."):
        tail = key.split(".", 1)[1].split(".", 1)[0]
        if tail in PHASETRACER_USER_CONFIRMED_HOOK_KEYS:
            return "phasetracer.hooks"
        return "phasetracer.v3d_mapping"
    if key.startswith("phasetracer."):
        if key in {"phasetracer.reference_fields", "phasetracer.reference_temperature", "phasetracer.reference_expected_v"}:
            return "phasetracer.reference_point"
        return "phasetracer.compile_policy"
    return key


def _render_question_packet(packet: QuestionPacket, *, number: int, total: int) -> list[str]:
    representative = packet.representative
    title = _packet_title(packet)
    agent_action = _packet_agent_action(packet)
    lines = [
        f"### Q{number}. {title}",
        "",
        f"- Question {number} of {total}.",
        f"- Problem packet: `{len(packet.issues)}` related blockers were grouped because they belong to the same decision.",
        f"- In plain language: {_packet_plain_language(packet)}",
        "- Materials checked: reviewed DRalgo source, current contract, generated proof files, explicit user inputs, and normalized DRalgo output when present.",
        "- Missing or conflicting information: the grouped blocker fields below still cannot be resolved from checked evidence.",
        "- Why this blocks: resolving this packet differently could change physics intent, field content, matching policy, PhaseTracer implementation, or numerical interpretation.",
        f"- Edit targets: {_packet_edit_targets(packet)}",
        f"- Accepted format: {_packet_format(packet)}",
        f"- Ask the user: {_packet_prompt(packet)}",
        f"- My current thought (reviewable): {_packet_recommendation(packet)}",
        f"- Codex/PTagent should handle first: {agent_action}",
        f"- If you accept my recommendation: Codex/PTagent will {agent_action} Then it will patch the Markdown, rerun `resolve`, and ask the next packet only if blockers remain.",
    ]
    notes = _packet_notes(packet)
    if notes:
        lines.append(f"- Notes: {notes}")
    lines.extend(["", "Underlying blockers:"])
    for issue in packet.issues[:8]:
        lines.append(f"- `{issue.field_key}`: `{issue.code}` - {issue.message}")
    if len(packet.issues) > 8:
        lines.append(f"- plus {len(packet.issues) - 8} more related blockers in this packet.")
    lines.extend(["", "Answer slot:", "", "```text", f"Q{number}: ", "```"])
    return lines


def _packet_title(packet: QuestionPacket) -> str:
    titles = {
        "dralgo.source_file": "Reviewed DRalgo source file",
        "model.source_audit": "DRalgo source model audit",
        "input_parameters.review": "Input parameter basis and reference values",
        "scale.rg_matching_policy": "RG/matching and 3D scale policy",
        "rg_matching.orders": "DRalgo output orders",
        "three_d.parameter_evaluator": "3D parameter evaluator",
        "phasetracer.v3d_mapping": "3D potential to PhaseTracer mapping",
        "phasetracer.hooks": "PhaseTracer apply_symmetry policy",
        "phasetracer.reference_point": "PhaseTracer reference point",
        "phasetracer.compile_policy": "PhaseTracer compile policy",
    }
    return titles.get(packet.key, packet.representative.field_key)


def _packet_plain_language(packet: QuestionPacket) -> str:
    if packet.key == "input_parameters.review":
        return "The generated constructor and RG initial conditions need a reviewed input-parameter basis, with one test value and input scale for each parameter."
    if packet.key == "model.source_audit":
        return "The supplied DRalgo source defines the model in representation/tensor language. The agent should report raw representation entries and source evidence, but should not assign particle identities unless the source names or comments make them explicit."
    if packet.key == "scale.rg_matching_policy":
        return "The 4D input running target, 4D->3D matching scale, soft 3D scale mu_3, and ultrasoft target scale mu_3_us are one connected scale convention."
    if packet.key == "rg_matching.orders":
        return "DRalgo output orders are not selected by recommendation; PTagent consumes only the explicit ordered print calls present in the reviewed source, otherwise records not_applicable."
    if packet.key == "phasetracer.v3d_mapping":
        return "PhaseTracer needs a reviewed map from its field vector and temperature to the DRalgo 3D fields and total V3D prefactor."
    if packet.key == "phasetracer.hooks":
        return "PhaseTracer apply_symmetry() and low-temperature phase hooks change phase counting and symmetry-equivalent point handling. PTagent must state the source-backed recommendation and ask the user to confirm the hook policy even when the source suggests a candidate symmetry."
    if packet.key == "phasetracer.reference_point":
        return "The generated C++ project needs one concrete field/temperature point for comparing the rendered potential against metadata."
    if packet.key == "dralgo.source_file":
        return "3DEFT mode executes only a reviewed Mathematica DRalgo source file; the agent should not synthesize the model file from contract rows."
    return packet.representative.message


def _packet_edit_targets(packet: QuestionPacket) -> str:
    section_targets = {
        "dralgo.source_file": "`DRalgo Runner` table",
        "model.source_audit": "`Model And Policy` source audit status plus `Parent Theory` source-audit tables",
        "input_parameters.review": "`Input Parameters` table",
        "scale.rg_matching_policy": "`RG And Matching` and `3D Parameter Evaluator` scale rows",
        "rg_matching.orders": "`RG And Matching` order rows",
        "three_d.parameter_evaluator": "`3D Parameter Evaluator` table",
        "phasetracer.v3d_mapping": "`3D Fields`, `3D Consumption`, and `PhaseTracer Potential Interface` tables",
        "phasetracer.hooks": "`PhaseTracer Potential Interface` hook rows",
        "phasetracer.reference_point": "`PhaseTracer` reference rows",
        "phasetracer.compile_policy": "`PhaseTracer` table",
    }
    if packet.key in section_targets:
        return section_targets[packet.key]
    targets: list[str] = []
    for issue in packet.issues:
        target = f"`{issue.field_key}`"
        if target not in targets:
            targets.append(target)
    if len(targets) <= 3:
        return "; ".join(targets)
    return "; ".join(targets[:3]) + f"; plus {len(targets) - 3} related cells listed below"


def _packet_format(packet: QuestionPacket) -> str:
    if packet.key == "input_parameters.review":
        return "One compact list: `name -> test_value=<number>, input_scale_GeV=<number>, review_status=source_reviewed|user_confirmed`; mark non-inputs as constant/derived/remove."
    if packet.key == "model.source_audit":
        return "`RepScalar entries=...; RepFermion/RepFermion1Gen entries=...; physical labels if known=...; Yukawa policy=...; scalar potential terms=...; ImportModelDRalgo mode=...`; approve or correct the source-audit summary."
    if packet.key == "scale.rg_matching_policy":
        return "`matching_scale=4*pi*exp(-EulerGamma)*xi4*T with xi4=1 by default; mu_3_scale=T; mu_3_us_scale=factor*T`, where `factor` is the reviewed SU(2)-coupling scale factor, for example `g2` if `g2` is an input parameter."
    if packet.key == "rg_matching.orders":
        return "`hard=<source-explicit order or not_applicable>; soft=<source-explicit order or not_applicable>; effective_potential=<source-explicit order or not_applicable>; pressure=<source-explicit order or not_applicable>`."
    if packet.key == "phasetracer.v3d_mapping":
        return "`field_order=...; V3D=<DRalgo output expression>`; default map is `field_i_3d=phi[i]/sqrt(T)` and default prefactor is `V=T*V3D`."
    if packet.key == "phasetracer.hooks":
        return "`symmetry_hook=none|z2_reflection; symmetry_rules=none|<field groups>; low_t_phase_guesses=none|0,0;246,0; status=user_confirmed|human_reviewed`."
    if packet.key == "phasetracer.reference_point":
        return "`reference_fields=100,100; reference_temperature=100; reference_expected_v=not_applicable` unless a fixed-3D-parameter direct V comparison value is supplied."
    return "Patch the listed Markdown cells with reviewed values."


def _packet_prompt(packet: QuestionPacket) -> str:
    if packet.key == "input_parameters.review":
        return "Please confirm the final input-parameter list and provide one test value and input scale for each."
    if packet.key == "model.source_audit":
        return "Please review the model encoded in the DRalgo source: raw RepScalar and RepFermion/RepFermion1Gen entries, any physical labels you want attached, Yukawa retained/ignored policy, scalar potential terms, and ImportModelDRalgo mode."
    if packet.key == "scale.rg_matching_policy":
        return "Please confirm the combined RG/matching policy and the two mandatory DRalgo 3D scales: mu_3 for the soft initial point and mu_3_us for the ultrasoft target point."
    if packet.key == "rg_matching.orders":
        return "Please only correct the DRalgo output order rows if the agent missed an explicit ordered print call in the source."
    if packet.key == "phasetracer.v3d_mapping":
        return "Please review the PhaseTracer field order and DRalgo-derived V3D expression; the 3D field map and T*V3D prefactor use the pipeline default."
    if packet.key == "phasetracer.hooks":
        return "Please confirm the PhaseTracer apply_symmetry() policy, the symmetry_rules used for any reflected fields, and low_t_phase_guesses; use explicit `none` when no hook is intended."
    if packet.key == "phasetracer.reference_point":
        return "Please give one reference field vector and temperature; expected V stays not_applicable unless it comes from a direct fixed-3D-parameter V comparison."
    return packet.representative.message


def _packet_recommendation(packet: QuestionPacket) -> str:
    if packet.key == "input_parameters.review":
        return "use explicit numeric assignments already present in the source as candidates, then ask the user to confirm test values and input scales."
    if packet.key == "model.source_audit":
        return "treat the source program as authoritative for representations and tensors; present physical particle labels as unknown unless the source evidence makes them clear."
    if packet.key == "scale.rg_matching_policy":
        return "production default: `matching_scale=4*pi*exp(-EulerGamma)*xi4*T` with `xi4=1`, `mu_3_scale=T`, and `mu_3_us_scale=g2*T` when `g2` is the reviewed SU(2) coupling input; otherwise use a reviewed `us_scale_factor*T`."
    if packet.key == "rg_matching.orders":
        return "do not recommend LO/NLO/NNLO; copy only explicit ordered print calls from the reviewed source and set missing order rows to not_applicable."
    if packet.key == "phasetracer.v3d_mapping":
        return "use the pipeline default map field_i_3d=phi[i]/sqrt(T) and V(phi,T)=T*V3D unless the contract is deliberately edited later."
    if packet.key == "phasetracer.hooks":
        return "recommend `symmetry_hook=none` unless the reviewed V3D/source shows an intended field-reflection equivalence; if all terms are even in a field, report it only as a candidate and ask the user before using `z2_reflection`. Keep low-T guesses `none` unless numeric phase hints are intentionally supplied."
    if packet.key == "phasetracer.reference_point":
        return "use fields around 100 and T around 100 for a stable reference point when the model scale allows it; otherwise ask the user for a source-program test point."
    recommendations = [_question_recommendation(issue) for issue in packet.issues]
    recommendations = [item for item in recommendations if item]
    return "; ".join(recommendations[:2]) or _question_hint(packet.representative) or "patch source-evident values first, then ask only the uncertain part."


def _packet_agent_action(packet: QuestionPacket) -> str:
    if packet.key == "input_parameters.review":
        return "merge explicit numeric assignments from the source program before asking, then ask only for missing or unconfirmed input metadata."
    if packet.key == "model.source_audit":
        return "parse and summarize raw RepScalar and RepFermion/RepFermion1Gen entries, source variable names, Yukawa invariants, scalar potential terms, AllocateTensors, and ImportModelDRalgo before asking."
    if packet.key == "scale.rg_matching_policy":
        return "do not ask for beta-function rows or RG solver internals before DRalgo runs; merge BetaFunctions4D[] and BetaFunctions3DUS[] output when available, then ask only for the physical scale choices."
    if packet.key == "phasetracer.v3d_mapping":
        return "fill field order, coefficient order, V3D, and symbol maps from DRalgo output; do not ask about the default field normalization or V3D prefactor."
    if packet.key == "phasetracer.hooks":
        return "inspect the reviewed V3D/source for even-field reflection candidates and present that reasoning, but do not silently accept an empty or non-empty apply_symmetry() policy."
    if packet.key == "phasetracer.reference_point":
        return "choose a stable reference point if obvious; ask only when no physically sensible point is available."
    return "patch any field directly supported by DRalgo source/output, then keep only the remaining physics choice in this packet."


def _packet_notes(packet: QuestionPacket) -> str:
    if packet.key == "input_parameters.review":
        return "This defines both the generated constructor signature and RG initial conditions."
    if packet.key == "model.source_audit":
        return "This is a source-program model review. RepScalar/RepFermion rows are representation evidence; particle names are user-reviewed labels unless explicit in the source."
    if packet.key == "scale.rg_matching_policy":
        return "Beta function expressions and solver details are DRalgo/backend-derived rows; do not ask the user to transcribe them. mu_3_scale fixes the soft initial point, and mu_3_us_scale fixes the ultrasoft target point used by BetaFunctions3DUS[] parameter running."
    if packet.key == "phasetracer.v3d_mapping":
        return "The normalization/prefactor are fixed by the 3DEFT default; only the actual DRalgo-derived V3D and field order need review."
    if packet.key == "phasetracer.hooks":
        return "This controls generated apply_symmetry(), get_symmetry_axes(), and get_low_t_phases(); even the default empty implementation must be user-confirmed."
    return ""


def _issue_should_be_backfilled_by_agent(issue: ValidationIssue) -> bool:
    key = issue.field_key
    if _issue_stage(issue) == "gate":
        return False
    if key.startswith("rg_beta_functions") or key.startswith("three_d_beta_functions") or key.startswith("three_d_soft_beta_functions"):
        return True
    if key.startswith("scalar_index_map"):
        return True
    if key.startswith("three_d_coefficients"):
        return True
    if key == "three_d_consumption.v3d_expression":
        return True
    if key.startswith("rg_matching.") and ("order" in key or key.endswith("_source")):
        return True
    if key.startswith("parent_theory_4d"):
        return False
    if key.startswith("eft_stages"):
        return True
    if key.startswith("symbol_audit"):
        return True
    return False


def _issue_needs_user_question(issue: ValidationIssue) -> bool:
    if issue.severity != "error":
        return False
    if _issue_stage(issue) == "gate":
        return False
    return not _issue_should_be_backfilled_by_agent(issue)


def _question_priority(issue: ValidationIssue) -> tuple[int, str]:
    key = issue.field_key
    if key.startswith("runner."):
        return (0, key)
    if key == "model_policy.source_model_audit_status":
        return (1, key)
    if key.startswith("parent_theory_4d"):
        return (1, key)
    if key.startswith("input_parameters"):
        return (2, key)
    if key.startswith("rg_matching"):
        return (3, key)
    if key.startswith("three_d_parameter_evaluator"):
        return (3, key)
    if key.startswith("three_d_consumption"):
        return (4, key)
    if key.startswith("phasetracer_interface"):
        return (5, key)
    if key.startswith("phasetracer."):
        return (6, key)
    return (9, key)


def _question_recommendation(issue: ValidationIssue) -> str:
    key = issue.field_key
    if key == "rg_matching.four_d_to_three_d_matching_scale":
        return "Use a hard 4D->3D matching scale near the thermal scale, for example `4*pi*exp(-EulerGamma)*xi4*T`, and ask/record the default hard-scale factor `xi4=1`."
    if key == "rg_matching.four_d_to_three_d_matching_scale_variation":
        return "Record the reviewed hard-scale factor, default `xi4=1`. xi4 controls mu4 and Lb/Lf but is not a running coupling."
    if key == "rg_matching.four_d_beta_dependent_variable_policy":
        return "Use `dralgo_native` when rows are copied from DRalgo; switch to `converted_to_contract_variables` only if the agent explicitly converts gauge-coupling-squared beta functions to the contract variables."
    if key == "rg_matching.ode_steps":
        return "Use the internal default `64`; this is backend metadata, not a user-facing scale question."
    if key == "three_d_parameter_evaluator.mu_3_scale":
        return "Use central `T` for mu_3. This is mandatory even if the merged expressions do not visibly contain a mu_3 symbol."
    if key == "three_d_parameter_evaluator.mu_3_us_scale":
        return "Use `factor*T` for mu_3_us, with factor reviewed from the SU(2) coupling convention; in many DRalgo models this can be `g2*T` when `g2` is an input parameter."
    if key.startswith("input_parameters"):
        return "Use explicit numeric assignments if present; otherwise ask the user for test_value and input_scale_GeV."
    if key.startswith("phasetracer."):
        return "Use a reference point around fields `(100, ...)` and `T ~ 100` when it is physically sensible for the model, then compare the generated C++ value against the Python metadata."
    return ""


def _question_hint(issue: ValidationIssue) -> str:
    key = issue.field_key
    if key.startswith("input_parameters"):
        return "For each input parameter give test_value, input_scale_GeV, and review_status=source_reviewed/dralgo_generated/user_confirmed/human_reviewed. Scan/fixed metadata is not part of the 3DEFT PhaseTracer contract."
    if key.startswith("runner."):
        return "Provide a reviewed user-supplied DRalgo .m/.wl path. PTagent 3DEFT accepts only DRalgo source programs and will not create a model file from the contract."
    if "scalar_multiplets" in key or "dralgo_construction_fields" in key:
        return "Confirm the RepScalar content from the reviewed DRalgo source: raw representation, real/complex component count, DRalgo symbol, VEV/background policy, and optional physical label if the source does not state it."
    if issue.code == "lagrangian_scale_symbol_collision":
        return "Rename the physical Lagrangian variable. The names mu, mu3, mu_4, and mu_3_us are reserved for renormalization-scale choices in the 3DEFT pipeline."
    if "scalar_potential_construction_map" in key:
        return "Review the tree-level scalar potential terms: coupling, invariant label, operator structure, potential term, and which DRalgo derivative helper should be generated."
    if "yukawa_construction_map" in key:
        return "Review the mapping coupling -> invariant_label -> operator_structure. If the reviewed source contains an xSM-like top Yukawa, yt is the numeric coupling and YukawaTop is only the invariant label; Ysff/YsffC are generated containers."
    if "four_d_beta_dependent_variable_policy" in key:
        return "Use dralgo_native if BetaFunctions4D[] rows follow DRalgo output directly: gauge betas are for g^2, while masses, scalar couplings, and Yukawas are linear. Use converted_to_contract_variables only after explicitly converting RHSs."
    if key.startswith("rg_matching.four_d_to_three_d_matching_scale_variation"):
        return "Record the reviewed central hard-scale factor, default xi4=1. Do not add a beta function for xi4."
    if key.startswith("rg_matching.four_d_to_three_d_matching_scale"):
        return "Confirm the 4D RG target and 4D->3D matching scale as an explicit function of T, for example 4*pi*exp(-EulerGamma)*xi4*T with xi4=1 by default."
    if "matching_order" in key or "potential_order" in key or "pressure_order" in key:
        return "Do not ask the user to choose an order. Inspect the reviewed DRalgo source for explicit ordered print calls such as PrintEffectivePotential[\"NNLO\"]; if absent, record not_applicable."
    if "mu_3_scale" in key:
        return "Confirm the mandatory soft-scale substitution. Recommended central value is `T`."
    if "mu_3_us_scale" in key:
        return "Confirm the mandatory ultrasoft target scale. Recommended form is `factor*T`, where `factor` is a reviewed SU(2)-coupling scale factor such as `g2` or an input parameter like `us_scale_factor`."
    if "three_d_scale_dependence_mode" in key or "three_d_matching_scale" in key or "three_d_phase_tracer_target_scale" in key:
        return "This is a legacy 3DUS policy field. Remove it and use the mandatory reviewed mu_3_scale / mu_3_us_scale rows."
    if "three_d_beta_functions" in key or "three_d_to_three_d_us_rg" in key or "three_d_us_ode" in key or "three_d_us_beta" in key:
        return "BetaFunctions3DUS[] is captured from DRalgo output. The renderer uses it to evolve matched 3D parameters from mu_3_scale to mu_3_us_scale when matching initial values are present."
    if "rg_beta_functions" in key or "ode_steps" in key or "beta_function_variable" in key:
        return "Do not ask the user to hand-copy beta functions or RG solver details before DRalgo runs. Set capture_beta_functions=true for the marked DRalgo script, then merge BetaFunctions4D[] output."
    if "fermion" in key or "yukawa" in key:
        return "Confirm the fermion/Yukawa sector encoded in the reviewed DRalgo source. Report RepFermion/RepFermion1Gen entries as raw representations first; attach particle labels only when the source or user confirms them."
    if "scalar_index" in key:
        return "Use the agent's representation-order guess as guidance, but treat PrintScalarRepPositions[] as authoritative. For xSM with Higgs doublet first and singlet second, the likely singlet index is 5; confirm from DRalgo before trusting PerformDRsoft[{5}]."
    if key == "three_d_consumption.v3d_expression":
        return "This is the concrete total 3D potential, not the field-normalization policy. Prefer merging a successful DRalgo PrintEffectivePotential[...] capture; otherwise record a reviewed V3D expression from the DRalgo source/output before PhaseTracer compile approval."
    if "three_d_consumption" in key:
        return "Use default field_i_3d=phi[i]/sqrt(T) and V(phi,T)=T*V3D. Only V3D itself should be merged from DRalgo output."
    if "three_d_parameter_evaluator" in key:
        return "Confirm get_3d_parameters(T), coefficient order, evaluator mode, and that running/matching comes from the reviewed DRalgo source/output."
    if "symbol_audit" in key:
        return "Map every raw Mathematica/DRalgo symbol to suggested and final compiler identifiers, a physics role, and a CForm-safe temporary name with no underscores."
    if key.startswith("phasetracer_interface."):
        tail = key.split(".", 1)[1].split(".", 1)[0]
        if tail in PHASETRACER_USER_CONFIRMED_HOOK_KEYS:
            return "Confirm the generated PhaseTracer apply_symmetry(), get_symmetry_axes(), and get_low_t_phases() policy. The agent must state the source-backed recommendation, any even-field reflection candidate, and why that recommendation is safe."
    if "phasetracer_interface" in key:
        return "Use the default field map and prefactor policy. Confirm only model-specific field order or optional hooks if the reviewed source requires them."
    if "approve_compile" in key:
        return "Ask for explicit approval to generate the PhaseTracer project after blockers are resolved."
    return ""


def _schema_from_text(markdown: str, policy: dict[str, str]) -> str:
    direct = _first_match(r"^schema:\s*(.*?)\s*$", markdown)
    return direct or policy.get("schema", "")


def _first_match(pattern: str, text: str) -> str:
    match = re.search(pattern, text, flags=re.MULTILINE)
    return match.group(1).strip() if match else ""


def _key_value_table(markdown: str, heading: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in _table(markdown, heading):
        key = row.get("key", "").strip()
        if key:
            result[key] = row.get("value", "").strip()
    return result


def _table(markdown: str, heading: str) -> list[dict[str, str]]:
    body = _heading_body(markdown, heading)
    lines = body.splitlines()
    for start, line in enumerate(lines):
        if not line.lstrip().startswith("|"):
            continue
        block: list[str] = []
        for current in lines[start:]:
            if current.lstrip().startswith("|"):
                block.append(current.strip())
            elif block:
                break
        parsed = _parse_markdown_table(block)
        if parsed:
            return parsed
    return []


def _input_parameter_table(markdown: str) -> list[dict[str, str]]:
    rows = _table(markdown, "Input Parameters")
    return [_normalize_input_parameter_row(row) for row in rows]


def _normalize_input_parameter_row(row: dict[str, str]) -> dict[str, str]:
    normalized = dict(row)
    if "input_scale" not in normalized:
        for alias in ("input_scale_gev", "input_scale_ge_v", "input_scale_in_gev"):
            if alias in normalized:
                normalized["input_scale"] = normalized[alias]
                break
    return normalized


def _parse_markdown_table(lines: list[str]) -> list[dict[str, str]]:
    if len(lines) < 2:
        return []
    headers = [_key(cell) for cell in _split_row(lines[0])]
    separator = _split_row(lines[1])
    if not headers or not all(re.fullmatch(r":?-{3,}:?", cell.strip()) for cell in separator):
        return []
    rows: list[dict[str, str]] = []
    for line in lines[2:]:
        cells = _split_row(line)
        if not cells:
            continue
        row: dict[str, str] = {}
        for index, header in enumerate(headers):
            row[header] = cells[index].strip() if index < len(cells) else ""
        rows.append(row)
    return rows


def _split_row(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _heading_body(markdown: str, wanted: str) -> str:
    headings = list(re.finditer(r"^(#{1,6})\s+(.*?)\s*$", markdown, flags=re.MULTILINE))
    wanted_key = _heading_key(wanted)
    for index, match in enumerate(headings):
        level = len(match.group(1))
        title = match.group(2).strip()
        if _heading_key(title) != wanted_key:
            continue
        start = match.end()
        end = len(markdown)
        for following in headings[index + 1 :]:
            if len(following.group(1)) <= level:
                end = following.start()
                break
        return markdown[start:end]
    return ""


def _heading_key(title: str) -> str:
    title = re.sub(r"^\d+[\).\s-]*", "", title.strip())
    return re.sub(r"\s+", " ", title).casefold()


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().casefold()).strip("_")


def _rows_have_placeholders(rows: list[dict[str, str]]) -> bool:
    for row in rows:
        for value in row.values():
            if _missing(value):
                return True
    return False


def _require_value(issues: list[ValidationIssue], table: dict[str, str], key: str, field_key: str) -> None:
    if _missing(table.get(key)):
        issues.append(_issue(field_key, "required_value", _required_value_message(field_key)))


def _expression_identifiers(expression: str) -> set[str]:
    tree = ast.parse(expression, mode="eval")
    return {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id not in EXPRESSION_HELPER_NAMES
    }


def _identifier_tokens(value: Any) -> set[str]:
    raw = str(value or "")
    tokens = set(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", raw))
    if not tokens or not looks_like_mathematica_inputform(raw):
        return tokens
    try:
        converted = convert_mathematica_inputform_expression(raw).expression
    except ValueError:
        return tokens
    tokens.update(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", converted))
    return tokens


def _normalize_contract_expression(expression: str, *, field_symbol_map: dict[str, str]) -> str:
    expr = expression.strip().strip("`")
    if looks_like_mathematica_inputform(expr):
        expr = convert_mathematica_inputform_expression(expr, field_map=field_symbol_map).expression
    if "^" in expr and "**" not in expr:
        expr = expr.replace("^", "**")
    return expr


def _validate_scale_expression_symbols(
    contract: dict[str, Any],
    field_key: str,
    expression: Any,
    issues: list[ValidationIssue],
) -> None:
    if _missing(expression):
        return
    text = _scale_expression_rhs(field_key, str(expression).strip(), issues)
    if not text:
        return
    allowed = {
        str(row.get("name", "")).strip()
        for row in _contract_input_parameters(contract)
        if _is_identifier(row.get("name", ""))
    }
    allowed |= set(THREE_D_SCALE_SYMBOLS) | {"T", "pi"}
    try:
        normalized = _normalize_contract_expression(text, field_symbol_map={})
        used = _expression_identifiers(normalized)
    except SyntaxError as exc:
        issues.append(_issue(field_key, "invalid_scale_expression", f"Scale expression must be Python-like after Mathematica conversion: {exc.msg}."))
        return
    except ValueError as exc:
        issues.append(_issue(field_key, "invalid_scale_expression", f"Scale expression could not be converted from Mathematica syntax: {exc}."))
        return
    unmapped = sorted(used - allowed)
    if unmapped:
        issues.append(
            _issue(
                field_key,
                "unmapped_scale_symbols",
                "Scale expressions may use T, xi4/xi_4, reviewed input parameters, and reserved scale aliases only. "
                f"Unmapped symbol(s): {', '.join(unmapped)}.",
            )
        )


def _scale_expression_rhs(field_key: str, text: str, issues: list[ValidationIssue]) -> str:
    text = text.strip().strip("`")
    if "=" not in text:
        return text
    lhs, rhs = text.split("=", 1)
    lhs = lhs.strip()
    rhs = rhs.strip()
    if not lhs or not rhs or "=" in rhs:
        issues.append(_issue(field_key, "invalid_scale_expression", "Scale expression assignment must have the form scale_alias=<expression>."))
        return ""
    allowed_lhs = _scale_assignment_lhs(field_key)
    if lhs not in allowed_lhs:
        issues.append(
            _issue(
                field_key,
                "invalid_scale_assignment_lhs",
                f"Scale assignment left-hand side {lhs!r} is not valid for {field_key}; allowed: {', '.join(sorted(allowed_lhs))}.",
            )
        )
        return ""
    return rhs


def _scale_assignment_lhs(field_key: str) -> set[str]:
    if field_key == "rg_matching.four_d_to_three_d_matching_scale":
        return {"four_d_to_three_d_matching_scale", "matching_scale", "mu", "mu4", "mu_4"}
    if field_key == "rg_matching.four_d_to_three_d_matching_scale_variation":
        return {"four_d_to_three_d_matching_scale_variation", "xi4", "xi_4"}
    if field_key.endswith("mu_3_scale"):
        return {"mu_3_scale", "mu3", "mu_3"}
    if field_key.endswith("mu_3_us_scale"):
        return {"mu_3_us_scale", "mu3US", "mu3us", "mu_3us", "mu_3_us"}
    return set(THREE_D_SCALE_SYMBOLS) | {"matching_scale"}


def _field_normalization_mode(value: Any) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().casefold()).strip("_")
    return FIELD_NORMALIZATION_VALUES.get(normalized, "unsupported")


def _required_value_message(field_key: str) -> str:
    if field_key == "three_d_consumption.v3d_expression":
        return (
            "three_d_consumption.v3d_expression requires a reviewed total V3D expression, "
            "normally merged from DRalgo PrintEffectivePotential[...] output."
        )
    return f"{field_key} must be user-confirmed."


def _missing(value: Any) -> bool:
    if value is None:
        return True
    normalized = _norm(value)
    if normalized in MISSING_VALUES:
        return True
    return normalized.startswith("ask_user")


def _norm(value: Any) -> str:
    return str(value).strip().strip("`").casefold()


def _user_confirmed_status(value: Any) -> bool:
    return _norm(value) in USER_CONFIRM_STATUS_VALUES


def _evidence_reviewed_status(value: Any) -> bool:
    return _norm(value) in EVIDENCE_REVIEW_STATUS_VALUES


def _contract_input_parameters(contract: dict[str, Any]) -> list[dict[str, Any]]:
    return contract.get("input_parameters", [])


def _is_scale_control_input_row(row: dict[str, Any]) -> bool:
    name = str(row.get("name", "")).strip().casefold()
    if name in {"xi4", "xi_4"}:
        return True
    evidence = " ".join(str(row.get(key, "")) for key in ("source", "notes", "latex")).casefold()
    return "scale_policy" in evidence or "scale-control" in evidence or "matching scale" in evidence


def _as_bool(value: Any) -> bool | None:
    normalized = _norm(value)
    if normalized in BOOL_TRUE:
        return True
    if normalized in BOOL_FALSE:
        return False
    return None


def looks_like_compile_approval_text(text: Any) -> bool:
    normalized = _norm(text)
    return any(word in normalized for word in APPROVAL_WORDS) and any(word in normalized for word in REVIEW_WORDS)


def _is_number(value: Any) -> bool:
    if _missing(value):
        return False
    try:
        float(str(value).strip())
    except (TypeError, ValueError):
        return False
    return True


def _is_positive_int(value: Any) -> bool:
    if _missing(value):
        return False
    try:
        return int(str(value).strip()) > 0
    except (TypeError, ValueError):
        return False


def _is_identifier(value: Any) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(value).strip()))


def _is_reserved_identifier(value: Any) -> bool:
    return is_reserved_identifier(str(value).strip())


def _reserved_message(label: str, value: Any) -> str:
    raw = str(value).strip()
    repaired = repair_reserved_identifier(raw)
    if repaired != raw:
        return f"{label} {raw!r} is reserved; use {repaired!r} or another reviewed model-specific name."
    return f"{label} {raw!r} must not be a C++/Python reserved word; choose a reviewed model-specific name."


def _is_cform_temp_symbol(value: Any) -> bool:
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", str(value).strip()))


def _csv(value: Any) -> list[str]:
    if _missing(value):
        return []
    return [item.strip() for item in str(value).split(",") if item.strip()]


def _extract_indices(raw: str) -> list[str]:
    if _norm(raw) in {"none", "[]", "{}"}:
        return []
    return re.findall(r"\d+", raw)


def _write_json(path: Path, data: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return path


