"""General finite-temperature phase-transition agent."""

from __future__ import annotations

from .ptagent_4d.config import Settings, get_settings
from .ptagent_4d.extraction_material import PotentialExtractionMaterial, build_potential_material
from .ptagent_4d.model_preflight import ModelSelectionRequired
from .ptagent_4d.schemas import (
    AgentRun,
    ConventionMapping,
    EvidenceItem,
    FormulaRecord,
    ModelIR,
    PaperMemory,
    PhaseHistoryResult,
    SourceSpan,
    ValidationReport,
)
from .ptagent_4d.workflow import PhaseTransitionAgent

__all__ = [
    "AgentRun",
    "ConventionMapping",
    "EvidenceItem",
    "FormulaRecord",
    "ModelIR",
    "ModelSelectionRequired",
    "PaperMemory",
    "PhaseHistoryResult",
    "PhaseTransitionAgent",
    "PotentialExtractionMaterial",
    "Settings",
    "SourceSpan",
    "ValidationReport",
    "build_potential_material",
    "get_settings",
]
