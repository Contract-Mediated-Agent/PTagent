from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


EVIDENCE_LEVELS = {
    "source_exact",
    "source_inferred",
    "standard_convention",
    "machine_inferred",
    "user_supplied",
    "user_approved_machine_inferred",
    "user_approved_mapping",
}

APPROVED_EVIDENCE_LEVELS = {
    "source_exact",
    "source_inferred",
    "standard_convention",
    "user_supplied",
    "user_approved_machine_inferred",
    "user_approved_mapping",
}

CORE_MODEL_FIELDS = {
    "background_fields",
    "tree_potential",
    "parameter_closure",
    "mass_spectrum",
    "daisy_scheme",
    "effective_potential",
}

IMPLEMENTATION_MODES = {
    "full_one_loop",
    "simplified_effective_potential",
    "tree_plus_thermal_masses",
    "unknown",
}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


@dataclass
class SourceSpan:
    source_id: str
    page_number: int = 0
    heading: str = ""
    text: str = ""
    score: float = 0.0
    parser: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SourceSpan":
        return cls(
            source_id=str(data.get("source_id", "")),
            page_number=int(data.get("page_number", 0) or 0),
            heading=str(data.get("heading", "")),
            text=str(data.get("text", "")),
            score=float(data.get("score", 0.0) or 0.0),
            parser=str(data.get("parser", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EvidenceItem:
    field_key: str
    level: str
    summary: str
    source_ids: list[str] = field(default_factory=list)
    requires_user_approval: bool = False
    approved: bool = False
    notes: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EvidenceItem":
        return cls(
            field_key=str(data.get("field_key", "")),
            level=str(data.get("level", "")),
            summary=str(data.get("summary", "")),
            source_ids=[str(item) for item in _as_list(data.get("source_ids"))],
            requires_user_approval=bool(data.get("requires_user_approval", False)),
            approved=bool(data.get("approved", False)),
            notes=str(data.get("notes", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def is_satisfied(self) -> bool:
        if self.level not in APPROVED_EVIDENCE_LEVELS:
            return self.approved
        if self.requires_user_approval:
            return self.approved
        return True


@dataclass
class FormulaRecord:
    formula_id: str
    latex: str
    source_id: str = ""
    label: str = ""
    kind: str = "unknown"
    evidence_level: str = "source_exact"
    confidence: float = 0.0
    raw_text: str = ""
    equation_number: str = ""
    context: str = ""
    parser_sources: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FormulaRecord":
        return cls(
            formula_id=str(data.get("formula_id", "")),
            latex=str(data.get("latex", "")),
            source_id=str(data.get("source_id", "")),
            label=str(data.get("label", "")),
            kind=str(data.get("kind", "unknown")),
            evidence_level=str(data.get("evidence_level", "source_exact")),
            confidence=float(data.get("confidence", 0.0) or 0.0),
            raw_text=str(data.get("raw_text", "")),
            equation_number=str(data.get("equation_number", "")),
            context=str(data.get("context", "")),
            parser_sources=[str(item) for item in _as_list(data.get("parser_sources"))],
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SymbolRecord:
    symbol: str
    role: str = "unknown"
    source_ids: list[str] = field(default_factory=list)
    definitions: list[str] = field(default_factory=list)
    evidence_level: str = "source_inferred"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SymbolRecord":
        return cls(
            symbol=str(data.get("symbol", "")),
            role=str(data.get("role", "unknown")),
            source_ids=[str(item) for item in _as_list(data.get("source_ids"))],
            definitions=[str(item) for item in _as_list(data.get("definitions"))],
            evidence_level=str(data.get("evidence_level", "source_inferred")),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RelationEdge:
    source: str
    target: str
    relation: str
    evidence_id: str = ""
    notes: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RelationEdge":
        return cls(
            source=str(data.get("source", "")),
            target=str(data.get("target", "")),
            relation=str(data.get("relation", "")),
            evidence_id=str(data.get("evidence_id", "")),
            notes=str(data.get("notes", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PaperMemory:
    paper_id: str
    source_path: str = ""
    source_type: str = ""
    paper_markdown: str = ""
    source_spans: list[SourceSpan] = field(default_factory=list)
    formula_registry: list[FormulaRecord] = field(default_factory=list)
    symbol_registry: list[SymbolRecord] = field(default_factory=list)
    relation_graph: list[RelationEdge] = field(default_factory=list)
    field_evidence: dict[str, EvidenceItem] = field(default_factory=dict)
    retrieval_index: dict[str, list[str]] = field(default_factory=dict)
    notes: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PaperMemory":
        return cls(
            paper_id=str(data.get("paper_id", "")),
            source_path=str(data.get("source_path", "")),
            source_type=str(data.get("source_type", "")),
            paper_markdown=str(data.get("paper_markdown", "")),
            source_spans=[SourceSpan.from_dict(item) for item in _as_list(data.get("source_spans"))],
            formula_registry=[FormulaRecord.from_dict(item) for item in _as_list(data.get("formula_registry"))],
            symbol_registry=[SymbolRecord.from_dict(item) for item in _as_list(data.get("symbol_registry"))],
            relation_graph=[RelationEdge.from_dict(item) for item in _as_list(data.get("relation_graph"))],
            field_evidence={
                str(key): EvidenceItem.from_dict(value)
                for key, value in _as_dict(data.get("field_evidence")).items()
            },
            retrieval_index={
                str(key): [str(item) for item in _as_list(value)]
                for key, value in _as_dict(data.get("retrieval_index")).items()
            },
            notes=str(data.get("notes", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["source_spans"] = [item.to_dict() for item in self.source_spans]
        data["formula_registry"] = [item.to_dict() for item in self.formula_registry]
        data["symbol_registry"] = [item.to_dict() for item in self.symbol_registry]
        data["relation_graph"] = [item.to_dict() for item in self.relation_graph]
        data["field_evidence"] = {key: item.to_dict() for key, item in self.field_evidence.items()}
        return data


@dataclass
class ConventionMapping:
    paper_symbol: str
    canonical_symbol: str
    code_symbol: str
    mapping_equation: str
    evidence: EvidenceItem
    approval_status: str = "pending"
    notes: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConventionMapping":
        return cls(
            paper_symbol=str(data.get("paper_symbol", "")),
            canonical_symbol=str(data.get("canonical_symbol", "")),
            code_symbol=str(data.get("code_symbol", "")),
            mapping_equation=str(data.get("mapping_equation", "")),
            evidence=EvidenceItem.from_dict(_as_dict(data.get("evidence"))),
            approval_status=str(data.get("approval_status", "pending")),
            notes=str(data.get("notes", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["evidence"] = self.evidence.to_dict()
        return data

    def is_approved(self) -> bool:
        return self.approval_status == "user_approved_mapping" or self.evidence.is_satisfied()


@dataclass
class FieldDefinition:
    latex_symbol: str
    program_symbol: str
    role: str = ""
    evidence: EvidenceItem | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FieldDefinition":
        evidence = data.get("evidence")
        return cls(
            latex_symbol=str(data.get("latex_symbol", "")),
            program_symbol=str(data.get("program_symbol", "")),
            role=str(data.get("role", "")),
            evidence=EvidenceItem.from_dict(evidence) if isinstance(evidence, dict) else None,
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["evidence"] = self.evidence.to_dict() if self.evidence else None
        return data


@dataclass
class ParameterDefinition:
    latex_symbol: str
    program_symbol: str
    value: str = ""
    role: str = ""
    evidence: EvidenceItem | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ParameterDefinition":
        evidence = data.get("evidence")
        return cls(
            latex_symbol=str(data.get("latex_symbol", "")),
            program_symbol=str(data.get("program_symbol", "")),
            value=str(data.get("value", "")),
            role=str(data.get("role", "")),
            evidence=EvidenceItem.from_dict(evidence) if isinstance(evidence, dict) else None,
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["evidence"] = self.evidence.to_dict() if self.evidence else None
        return data


@dataclass
class PotentialPieces:
    V0: str = ""
    V_CW: str = ""
    V_CT: str = ""
    V_CT_conditions: str = ""
    V_CT_linear_basis: list[dict[str, str]] = field(default_factory=list)
    V_CT_linear_conditions: list[dict[str, str]] = field(default_factory=list)
    V1: str = ""
    V_T: str = ""
    V_daisy: str = ""
    V_eff: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PotentialPieces":
        return cls(
            V0=str(data.get("V0", "")),
            V_CW=str(data.get("V_CW", "")),
            V_CT=str(data.get("V_CT", "")),
            V_CT_conditions=str(data.get("V_CT_conditions", "")),
            V_CT_linear_basis=[dict(item) for item in _as_list(data.get("V_CT_linear_basis"))],
            V_CT_linear_conditions=[dict(item) for item in _as_list(data.get("V_CT_linear_conditions"))],
            V1=str(data.get("V1", "")),
            V_T=str(data.get("V_T", "")),
            V_daisy=str(data.get("V_daisy", "")),
            V_eff=str(data.get("V_eff", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DaisyInfo:
    scheme: str = "None"
    parwani_replacement_scope: str = "None"
    thermal_coefficients: list[dict[str, str]] = field(default_factory=list)
    custom_expression: str = ""
    evidence: EvidenceItem | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DaisyInfo":
        evidence = data.get("evidence")
        return cls(
            scheme=str(data.get("scheme", "None")),
            parwani_replacement_scope=str(data.get("parwani_replacement_scope", "None")),
            thermal_coefficients=[dict(item) for item in _as_list(data.get("thermal_coefficients"))],
            custom_expression=str(data.get("custom_expression", "")),
            evidence=EvidenceItem.from_dict(evidence) if isinstance(evidence, dict) else None,
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["evidence"] = self.evidence.to_dict() if self.evidence else None
        return data


@dataclass
class ZeroTempLoopInfo:
    representation: str = "unknown"
    counterterm_status: str = "unclear"
    goldstone_prescription: str = "unclear"
    goldstone_applies_to: str = "unclear"
    goldstone_notes: str = ""
    renormalization_scheme: str = ""
    renormalization_scale: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ZeroTempLoopInfo":
        return cls(
            representation=str(data.get("representation", "unknown")),
            counterterm_status=str(data.get("counterterm_status", "unclear")),
            goldstone_prescription=str(data.get("goldstone_prescription", "unclear")),
            goldstone_applies_to=str(data.get("goldstone_applies_to", "unclear")),
            goldstone_notes=str(data.get("goldstone_notes", "")),
            renormalization_scheme=str(data.get("renormalization_scheme", "")),
            renormalization_scale=str(data.get("renormalization_scale", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ClosureRow:
    symbol: str
    appears_in: str
    symbol_type: str
    determined_by: str
    status: str
    notes: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ClosureRow":
        return cls(
            symbol=str(data.get("symbol", "")),
            appears_in=str(data.get("appears_in", "")),
            symbol_type=str(data.get("symbol_type", "")),
            determined_by=str(data.get("determined_by", "")),
            status=str(data.get("status", "")),
            notes=str(data.get("notes", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ModelIR:
    model_name: str
    paper_id: str = ""
    source_path: str = ""
    review_status: str = "needs_review"
    implementation_mode: str = "unknown"
    background_fields: list[FieldDefinition] = field(default_factory=list)
    physical_fields: list[FieldDefinition] = field(default_factory=list)
    parameters: list[ParameterDefinition] = field(default_factory=list)
    input_parameters: list[ParameterDefinition] = field(default_factory=list)
    fixed_parameters: list[ParameterDefinition] = field(default_factory=list)
    branch_constraints: list[dict[str, str]] = field(default_factory=list)
    calibration_conditions: list[dict[str, str]] = field(default_factory=list)
    derived_parameters: list[dict[str, str]] = field(default_factory=list)
    parameter_relations: list[dict[str, str]] = field(default_factory=list)
    masses: dict[str, str] = field(default_factory=dict)
    potentials: PotentialPieces = field(default_factory=PotentialPieces)
    zero_temp_loop: ZeroTempLoopInfo = field(default_factory=ZeroTempLoopInfo)
    daisy: DaisyInfo = field(default_factory=DaisyInfo)
    closure_table: list[ClosureRow] = field(default_factory=list)
    convention_mappings: list[ConventionMapping] = field(default_factory=list)
    field_evidence: dict[str, EvidenceItem] = field(default_factory=dict)
    notes: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModelIR":
        return cls(
            model_name=str(data.get("model_name", "")),
            paper_id=str(data.get("paper_id", "")),
            source_path=str(data.get("source_path", "")),
            review_status=str(data.get("review_status", "needs_review")),
            implementation_mode=str(data.get("implementation_mode", "unknown")),
            background_fields=[FieldDefinition.from_dict(item) for item in _as_list(data.get("background_fields"))],
            physical_fields=[FieldDefinition.from_dict(item) for item in _as_list(data.get("physical_fields"))],
            parameters=[ParameterDefinition.from_dict(item) for item in _as_list(data.get("parameters"))],
            input_parameters=[ParameterDefinition.from_dict(item) for item in _as_list(data.get("input_parameters"))],
            fixed_parameters=[ParameterDefinition.from_dict(item) for item in _as_list(data.get("fixed_parameters"))],
            branch_constraints=[dict(item) for item in _as_list(data.get("branch_constraints"))],
            calibration_conditions=[dict(item) for item in _as_list(data.get("calibration_conditions"))],
            derived_parameters=[dict(item) for item in _as_list(data.get("derived_parameters"))],
            parameter_relations=[dict(item) for item in _as_list(data.get("parameter_relations"))],
            masses={str(key): str(value) for key, value in _as_dict(data.get("masses")).items()},
            potentials=PotentialPieces.from_dict(_as_dict(data.get("potentials"))),
            zero_temp_loop=ZeroTempLoopInfo.from_dict(_as_dict(data.get("zero_temp_loop"))),
            daisy=DaisyInfo.from_dict(_as_dict(data.get("daisy"))),
            closure_table=[ClosureRow.from_dict(item) for item in _as_list(data.get("closure_table"))],
            convention_mappings=[
                ConventionMapping.from_dict(item) for item in _as_list(data.get("convention_mappings"))
            ],
            field_evidence={
                str(key): EvidenceItem.from_dict(value)
                for key, value in _as_dict(data.get("field_evidence")).items()
            },
            notes=str(data.get("notes", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["background_fields"] = [item.to_dict() for item in self.background_fields]
        data["physical_fields"] = [item.to_dict() for item in self.physical_fields]
        data["parameters"] = [item.to_dict() for item in self.parameters]
        data["input_parameters"] = [item.to_dict() for item in self.input_parameters]
        data["fixed_parameters"] = [item.to_dict() for item in self.fixed_parameters]
        data["potentials"] = self.potentials.to_dict()
        data["zero_temp_loop"] = self.zero_temp_loop.to_dict()
        data["daisy"] = self.daisy.to_dict()
        data["closure_table"] = [item.to_dict() for item in self.closure_table]
        data["convention_mappings"] = [item.to_dict() for item in self.convention_mappings]
        data["field_evidence"] = {key: item.to_dict() for key, item in self.field_evidence.items()}
        return data


@dataclass
class ValidationIssue:
    severity: str
    code: str
    message: str
    field_key: str = ""
    suggested_action: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ValidationIssue":
        return cls(
            severity=str(data.get("severity", "")),
            code=str(data.get("code", "")),
            message=str(data.get("message", "")),
            field_key=str(data.get("field_key", "")),
            suggested_action=str(data.get("suggested_action", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ValidationReport:
    ready_for_compile: bool
    ready_for_runtime: bool
    issues: list[ValidationIssue] = field(default_factory=list)
    missing_core_fields: list[str] = field(default_factory=list)
    conflict_fields: list[str] = field(default_factory=list)
    closure_table: list[ClosureRow] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ValidationReport":
        return cls(
            ready_for_compile=bool(data.get("ready_for_compile", False)),
            ready_for_runtime=bool(data.get("ready_for_runtime", False)),
            issues=[ValidationIssue.from_dict(item) for item in _as_list(data.get("issues"))],
            missing_core_fields=[str(item) for item in _as_list(data.get("missing_core_fields"))],
            conflict_fields=[str(item) for item in _as_list(data.get("conflict_fields"))],
            closure_table=[ClosureRow.from_dict(item) for item in _as_list(data.get("closure_table"))],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready_for_compile": self.ready_for_compile,
            "ready_for_runtime": self.ready_for_runtime,
            "issues": [item.to_dict() for item in self.issues],
            "missing_core_fields": self.missing_core_fields,
            "conflict_fields": self.conflict_fields,
            "closure_table": [item.to_dict() for item in self.closure_table],
        }


@dataclass
class PhaseHistoryResult:
    status: str
    model_path: str = ""
    inputs: dict[str, Any] = field(default_factory=dict)
    transitions: list[dict[str, Any]] = field(default_factory=list)
    Tc_list: list[float | None] = field(default_factory=list)
    Tn_list: list[float | None] = field(default_factory=list)
    action_over_T: list[float | None] = field(default_factory=list)
    path_labels: list[str] = field(default_factory=list)
    summary: str = ""
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PhaseHistoryResult":
        return cls(
            status=str(data.get("status", "")),
            model_path=str(data.get("model_path", "")),
            inputs=dict(data.get("inputs", {})),
            transitions=[dict(item) for item in _as_list(data.get("transitions"))],
            Tc_list=list(data.get("Tc_list", [])),
            Tn_list=list(data.get("Tn_list", [])),
            action_over_T=list(data.get("action_over_T", [])),
            path_labels=[str(item) for item in _as_list(data.get("path_labels"))],
            summary=str(data.get("summary", "")),
            warnings=[str(item) for item in _as_list(data.get("warnings"))],
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AgentRun:
    run_id: str
    source_path: str
    memory_path: str = ""
    material_path: str = ""
    template_path: str = ""
    contract_resolved_path: str = ""
    ir_path: str = ""
    validation_path: str = ""
    generated_model_path: str = ""
    phase_history_path: str = ""
    report_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentRun":
        return cls(
            run_id=str(data.get("run_id", "")),
            source_path=str(data.get("source_path", "")),
            memory_path=str(data.get("memory_path", "")),
            material_path=str(data.get("material_path", "")),
            template_path=str(data.get("template_path", "")),
            contract_resolved_path=str(data.get("contract_resolved_path", "")),
            ir_path=str(data.get("ir_path", "")),
            validation_path=str(data.get("validation_path", "")),
            generated_model_path=str(data.get("generated_model_path", "")),
            phase_history_path=str(data.get("phase_history_path", "")),
            report_path=str(data.get("report_path", "")),
        )
