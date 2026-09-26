from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import xml.etree.ElementTree as XmlElementTree
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from defusedxml import ElementTree as SafeElementTree
from defusedxml.common import DefusedXmlException

from .safe_expression import SafeExpressionError, evaluate_safe_expression, parse_safe_expression
from .slha import SlhaDocument, parse_slha_file


MAX_XML_BYTES = 16 * 1024 * 1024
MAX_JSON_BYTES = 16 * 1024 * 1024
SARAH_IMPORT_SCHEMA = "ptagent.sarah_model.v1"
THERMAL_METADATA_SCHEMA = "ptagent.sarah_thermal.v1"

_BLOCK_INDEX = r"-?\d+(?:\.0*)?"
_BLOCK_REFERENCE = re.compile(
    rf"\b([A-Za-z_][A-Za-z0-9_]*)\[\s*({_BLOCK_INDEX}(?:\s*,\s*{_BLOCK_INDEX})*)?\s*\]"
)
_ASSIGNMENT = re.compile(r"^\s*([A-Za-z_]\w*)\s*=\s*(.+?)\s*$")


class SarahImportError(ValueError):
    """Raised when SARAH/Vevacious++ input is unsafe, unsupported, or inconsistent."""


@dataclass(frozen=True)
class SarahMassSector:
    name: str
    kind: str
    spin_type: str
    particle_names: tuple[str, ...]
    multiplicity: float
    basis: tuple[str, ...]
    matrix: tuple[tuple[str, ...], ...]
    source_location: str
    dof_per_eigenvalue: float
    cw_constant: float
    transverse_dof_per_eigenvalue: float = 0.0
    longitudinal_dof_per_eigenvalue: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "spin_type": self.spin_type,
            "particle_names": list(self.particle_names),
            "multiplicity": self.multiplicity,
            "basis": list(self.basis),
            "matrix": [list(row) for row in self.matrix],
            "source_location": self.source_location,
            "dof_per_eigenvalue": self.dof_per_eigenvalue,
            "cw_constant": self.cw_constant,
            "transverse_dof_per_eigenvalue": self.transverse_dof_per_eigenvalue,
            "longitudinal_dof_per_eigenvalue": self.longitudinal_dof_per_eigenvalue,
        }


@dataclass
class SarahImportedModel:
    model_name: str
    version: str
    renormalization_scheme: str
    gauge_fixing: str
    fields: list[str]
    dsb_minimum: dict[str, str]
    tree_potential: str
    extra_polynomial: str
    mass_sectors: list[SarahMassSector]
    block_references: dict[str, dict[str, Any]]
    derived_parameters: dict[str, str]
    parameter_values: dict[str, float]
    unresolved_parameters: list[str]
    renormalization_scale_sq: float | None
    valid_blocks: list[str]
    scale_policy: dict[str, str]
    provenance: dict[str, Any]
    thermal_metadata: dict[str, Any] | None = None
    thermal_result: dict[str, Any] | None = None
    issues: list[dict[str, str]] = field(default_factory=list)

    @property
    def thermal_ready(self) -> bool:
        return bool(self.thermal_metadata and self.thermal_result and not self.thermal_result.get("blockers"))

    @property
    def numerical_ready(self) -> bool:
        return not self.unresolved_parameters and self.renormalization_scale_sq is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SARAH_IMPORT_SCHEMA,
            "model_name": self.model_name,
            "vevacious_version": self.version,
            "renormalization_scheme": self.renormalization_scheme,
            "gauge_fixing": self.gauge_fixing,
            "fields": list(self.fields),
            "dsb_minimum": dict(self.dsb_minimum),
            "tree_potential": self.tree_potential,
            "extra_polynomial": self.extra_polynomial,
            "mass_sectors": [sector.to_dict() for sector in self.mass_sectors],
            "block_references": dict(self.block_references),
            "derived_parameters": dict(self.derived_parameters),
            "parameter_values": dict(self.parameter_values),
            "unresolved_parameters": list(self.unresolved_parameters),
            "renormalization_scale_sq": self.renormalization_scale_sq,
            "valid_blocks": list(self.valid_blocks),
            "scale_policy": dict(self.scale_policy),
            "thermal_metadata": self.thermal_metadata,
            "thermal_result": self.thermal_result,
            "thermal_ready": self.thermal_ready,
            "numerical_ready": self.numerical_ready,
            "issues": list(self.issues),
            "provenance": dict(self.provenance),
        }


@dataclass(frozen=True)
class SarahImportRun:
    run_dir: Path
    contract_template_path: Path
    import_json_path: Path
    import_markdown_path: Path
    thermal_json_path: Path
    thermal_markdown_path: Path
    consistency_path: Path
    resolved_contract_path: Path
    model_ir_path: Path
    validation_path: Path
    model: SarahImportedModel

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_dir": str(self.run_dir),
            "contract_template": str(self.contract_template_path),
            "sarah_import": str(self.import_json_path),
            "sarah_import_markdown": str(self.import_markdown_path),
            "thermal_derivation": str(self.thermal_json_path),
            "thermal_derivation_markdown": str(self.thermal_markdown_path),
            "model_consistency": str(self.consistency_path),
            "contract_resolved": str(self.resolved_contract_path),
            "model_ir": str(self.model_ir_path),
            "validation": str(self.validation_path),
            "thermal_ready": self.model.thermal_ready,
            "numerical_ready": self.model.numerical_ready,
            "unresolved_parameters": list(self.model.unresolved_parameters),
        }


def import_sarah_model(
    *,
    vin_path: str | Path,
    parameter_map_path: str | Path,
    thermal_metadata_path: str | Path | None = None,
    slha_path: str | Path | None = None,
    parameters: dict[str, Any] | None = None,
) -> SarahImportedModel:
    vin = Path(vin_path).expanduser().resolve()
    parameter_map = Path(parameter_map_path).expanduser().resolve()
    vin_root = _safe_xml_root(vin, expected_root="VevaciousModelFile")
    map_root = _safe_xml_root(parameter_map, expected_root="LhaBlockParameterManagerInitializationFile")

    details = _required_descendant(vin_root, "ModelFileDetails", vin)
    major = str(details.attrib.get("VevaciousMajorVersion", "")).strip()
    minor = str(details.attrib.get("VevaciousMinorVersion", "0")).strip()
    if major != "2":
        raise SarahImportError(
            f"Unsupported Vevacious model version {major or '<missing>'}.{minor}; "
            "PTagent accepts SARAH Vevacious++ v2 output only."
        )

    manager_details = _required_descendant(map_root, "ParameterManagerDetails", parameter_map)
    manager_major = str(manager_details.attrib.get("VevaciousMajorVersion", "")).strip()
    if manager_major != "2":
        raise SarahImportError("ScaleAndBlock.xml is not a Vevacious++ v2 parameter map.")

    fields = _text_lines(_required_descendant(vin_root, "FieldVariables", vin))
    if not fields:
        raise SarahImportError("Vevacious++ model has no FieldVariables.")
    _require_unique_identifiers(fields, "background field")

    block_references: dict[str, dict[str, Any]] = {}
    derived_raw = _assignment_map(_optional_descendant(map_root, "DerivedParameters"))
    normalized_derived = {
        name: _normalize_expression(expression, block_references)
        for name, expression in derived_raw.items()
    }

    tree = _normalize_expression(_required_text(vin_root, "TreeLevelPotential", vin), block_references)
    loop = _required_descendant(vin_root, "LoopCorrections", vin)
    scheme = str(loop.attrib.get("RenormalizationScheme", "")).strip().upper()
    if scheme not in {"MSBAR", "DRBAR"}:
        raise SarahImportError(f"Unsupported renormalization scheme {scheme or '<missing>'!r}.")
    gauge_fixing = str(loop.attrib.get("GaugeFixing", "")).strip().upper()
    extra = _normalize_expression(_text_of(_optional_descendant(loop, "ExtraPolynomialPart")) or "0.0", block_references)
    dsb = _parse_dsb_minimum(_optional_descendant(vin_root, "DsbMinimum"), block_references)

    sectors: list[SarahMassSector] = []
    sector_index = 0
    for element in loop.iter():
        tag = _local_name(element.tag)
        if tag not in {"RealBosonMassSquaredMatrix", "ComplexWeylFermionMassSquaredMatrix"}:
            continue
        sector_index += 1
        sectors.append(
            _parse_mass_sector(
                element,
                index=sector_index,
                scheme=scheme,
                block_references=block_references,
            )
        )
    if not sectors:
        raise SarahImportError("Vevacious++ model contains no one-loop mass matrices.")

    valid_blocks = _text_lines(_optional_descendant(map_root, "ValidBlocks"))
    scale_policy = _parse_scale_policy(map_root)
    slha = parse_slha_file(slha_path) if slha_path else None
    overrides = _normalize_parameter_overrides(parameters or {})
    parameter_values = _resolve_parameter_values(
        block_references=block_references,
        derived_parameters=normalized_derived,
        slha=slha,
        overrides=overrides,
    )
    _require_real_symmetric_sectors(sectors)
    used_names = _expression_names([tree, extra, *dsb.values(), *(entry for sector in sectors for row in sector.matrix for entry in row)])
    unresolved = sorted(name for name in used_names if name not in fields and name not in parameter_values and name != "pi")
    renormalization_scale_sq = _resolve_renormalization_scale_sq(scale_policy, slha, overrides)

    thermal_metadata = None
    thermal_result = None
    issues: list[dict[str, str]] = []
    if thermal_metadata_path:
        thermal_metadata = _load_thermal_metadata(thermal_metadata_path)
        _validate_thermal_source_hashes(thermal_metadata, vin=vin, parameter_map=parameter_map)
        from .sarah_thermal import derive_thermal_matrices

        thermal_result = derive_thermal_matrices(
            thermal_metadata,
            fields=fields,
            parameter_names=set(parameter_values) | set(unresolved),
        )
        _validate_thermal_sector_mappings(thermal_result, sectors)
        _attach_high_temperature_supertrace_check(thermal_result, fields=fields, sectors=sectors)
    else:
        issues.append(
            {
                "severity": "warning",
                "code": "thermal_metadata_missing",
                "message": "Tree-level and zero-temperature data were imported, but resummed thermal compilation is blocked until ptagent_thermal_metadata.json is supplied.",
            }
        )
    if unresolved:
        issues.append(
            {
                "severity": "error",
                "code": "unresolved_parameters",
                "message": "Numerical execution is blocked by unresolved parameters: " + ", ".join(unresolved),
            }
        )
    if renormalization_scale_sq is None:
        issues.append(
            {
                "severity": "error",
                "code": "renormalization_scale_missing",
                "message": "renormScaleSq could not be resolved from ScaleAndBlock.xml, the SLHA block scale, or explicit parameters.",
            }
        )

    provenance = {
        "vin": _source_record(vin),
        "parameter_map": _source_record(parameter_map),
        "slha": _source_record(Path(slha_path).expanduser().resolve()) if slha_path else None,
        "thermal_metadata": _source_record(Path(thermal_metadata_path).expanduser().resolve()) if thermal_metadata_path else None,
        "parameter_overrides_sha256": _json_sha256(parameters or {}),
        "sarah_version": _extract_sarah_version(vin),
        "vevacious_version": f"{major}.{minor}",
        "thermal_exporter_version": str(
            ((thermal_metadata or {}).get("provenance") or {}).get("exporter_version", "not_supplied")
        ),
        "importer_schema": SARAH_IMPORT_SCHEMA,
    }
    return SarahImportedModel(
        model_name=str(details.attrib.get("ModelName", "SARAHModel")).strip() or "SARAHModel",
        version=f"{major}.{minor}",
        renormalization_scheme=scheme,
        gauge_fixing=gauge_fixing,
        fields=fields,
        dsb_minimum=dsb,
        tree_potential=tree,
        extra_polynomial=extra,
        mass_sectors=sectors,
        block_references=block_references,
        derived_parameters=normalized_derived,
        parameter_values=parameter_values,
        unresolved_parameters=unresolved,
        renormalization_scale_sq=renormalization_scale_sq,
        valid_blocks=valid_blocks,
        scale_policy=scale_policy,
        provenance=provenance,
        thermal_metadata=thermal_metadata,
        thermal_result=thermal_result,
        issues=issues,
    )


def run_sarah_import(
    *,
    vin_path: str | Path,
    parameter_map_path: str | Path,
    run_dir: str | Path,
    thermal_metadata_path: str | Path | None = None,
    slha_path: str | Path | None = None,
    parameters: dict[str, Any] | None = None,
) -> SarahImportRun:
    model = import_sarah_model(
        vin_path=vin_path,
        parameter_map_path=parameter_map_path,
        thermal_metadata_path=thermal_metadata_path,
        slha_path=slha_path,
        parameters=parameters,
    )
    root = Path(run_dir).expanduser().resolve()
    input_dir = root / "input"
    proof_dir = root / "proof_materials"
    input_dir.mkdir(parents=True, exist_ok=True)
    proof_dir.mkdir(parents=True, exist_ok=True)
    sources = (
        ("vin", vin_path),
        ("parameter_map", parameter_map_path),
        ("thermal_metadata", thermal_metadata_path),
        ("slha", slha_path),
    )
    for provenance_key, source in sources:
        if not source:
            continue
        path = Path(source).expanduser().resolve()
        target = input_dir / path.name
        if path != target:
            shutil.copy2(path, target)
        model.provenance[provenance_key] = _source_record(target)
    if parameters:
        parameter_path = input_dir / "parameters.json"
        parameter_path.write_text(
            json.dumps(parameters, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        model.provenance["parameters"] = _source_record(parameter_path)

    import_json = proof_dir / "sarah_import.json"
    import_json.write_text(json.dumps(model.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    import_markdown = proof_dir / "sarah_import.md"
    import_markdown.write_text(_render_import_markdown(model), encoding="utf-8")
    thermal_json = proof_dir / "thermal_derivation.json"
    thermal_payload = model.thermal_result or {
        "schema": "ptagent.thermal_derivation.v1",
        "ready": False,
        "blockers": ["thermal_metadata_missing"],
    }
    thermal_json.write_text(json.dumps(thermal_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    thermal_markdown = proof_dir / "thermal_derivation.md"
    thermal_markdown.write_text(_render_thermal_markdown(model), encoding="utf-8")
    consistency = proof_dir / "model_consistency.json"
    consistency.write_text(json.dumps(_consistency_report(model), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    from .sarah_contract import build_sarah_contract

    contract = root / "contract_template.md"
    contract.write_text(build_sarah_contract(model), encoding="utf-8")

    from .template_contract import contract_to_model_ir, validate_contract_template, write_resolved_contract_data

    validation = validate_contract_template(contract.read_text(encoding="utf-8"), review_required=True)
    resolved_contract = proof_dir / "contract_resolved.json"
    if validation.contract:
        write_resolved_contract_data(validation.contract, resolved_contract)
    else:
        resolved_contract.write_text("{}\n", encoding="utf-8")
    model_ir = proof_dir / "model_ir.json"
    model_ir.write_text(
        json.dumps(contract_to_model_ir(validation.contract, source_path=str(Path(vin_path).resolve())).to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    validation_path = proof_dir / "validation.json"
    validation_path.write_text(json.dumps(validation.to_report().to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return SarahImportRun(
        run_dir=root,
        contract_template_path=contract,
        import_json_path=import_json,
        import_markdown_path=import_markdown,
        thermal_json_path=thermal_json,
        thermal_markdown_path=thermal_markdown,
        consistency_path=consistency,
        resolved_contract_path=resolved_contract,
        model_ir_path=model_ir,
        validation_path=validation_path,
        model=model,
    )


def is_vevacious_v2_file(path: str | Path) -> bool:
    try:
        root = _safe_xml_root(Path(path), expected_root="VevaciousModelFile")
        details = _required_descendant(root, "ModelFileDetails", Path(path))
        return str(details.attrib.get("VevaciousMajorVersion", "")).strip() == "2"
    except (OSError, SarahImportError):
        return False


def _safe_xml_root(path: Path, *, expected_root: str):
    path = path.expanduser().resolve()
    size = path.stat().st_size
    if size > MAX_XML_BYTES:
        raise SarahImportError(f"XML input exceeds the {MAX_XML_BYTES} byte safety limit: {path}")
    raw = path.read_bytes()
    upper = raw.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise SarahImportError(f"DTD and ENTITY declarations are forbidden in PTagent XML input: {path}")
    try:
        root = SafeElementTree.fromstring(raw)
    except (DefusedXmlException, XmlElementTree.ParseError) as exc:
        raise SarahImportError(f"Unsafe or malformed XML input {path}: {exc}") from exc
    actual = _local_name(root.tag)
    if actual != expected_root:
        raise SarahImportError(f"Expected XML root {expected_root!r}, found {actual!r} in {path}.")
    return root


def _parse_mass_sector(
    element,
    *,
    index: int,
    scheme: str,
    block_references: dict[str, dict[str, Any]],
) -> SarahMassSector:
    spin = str(element.attrib.get("SpinType", "")).strip()
    particle_text = str(element.attrib.get("ParticleName", "")).strip()
    particles = tuple(item.strip() for item in particle_text.split(",") if item.strip()) or (f"sector_{index}",)
    try:
        multiplicity = float(str(element.attrib.get("MultiplicityFactor", "1")).strip())
    except ValueError as exc:
        raise SarahImportError(f"Mass sector {particle_text!r} has a non-numeric MultiplicityFactor.") from exc
    if multiplicity <= 0:
        raise SarahImportError(f"Mass sector {particle_text!r} has a non-positive MultiplicityFactor.")
    if spin == "ScalarBoson":
        kind = "scalar"
        spin_dof = 1.0
        cw_constant = 1.5
    elif spin in {"GaugeBoson", "VectorBoson"}:
        kind = "gauge"
        spin_dof = 3.0
        cw_constant = 5.0 / 6.0 if scheme == "MSBAR" else 1.5
    elif spin == "WeylFermion" or _local_name(element.tag) == "ComplexWeylFermionMassSquaredMatrix":
        kind = "fermion"
        spin_dof = 2.0
        cw_constant = 1.5
    else:
        raise SarahImportError(f"Unsupported Vevacious++ SpinType {spin!r} for {particle_text!r}.")
    entries = _matrix_entries(element)
    size = math.isqrt(len(entries))
    if not entries or size * size != len(entries):
        raise SarahImportError(
            f"Mass sector {particle_text!r} contains {len(entries)} entries; a square matrix was expected."
        )
    normalized = [_normalize_expression(entry, block_references) for entry in entries]
    matrix = tuple(tuple(normalized[row * size + column] for column in range(size)) for row in range(size))
    base_name = _program_symbol("_".join(particles) or f"sector_{index}")
    basis = tuple(f"{base_name}_{position + 1}" for position in range(size))
    return SarahMassSector(
        name=f"{kind}_{index}_{base_name}",
        kind=kind,
        spin_type=spin,
        particle_names=particles,
        multiplicity=multiplicity,
        basis=basis,
        matrix=matrix,
        source_location=f"/VevaciousModelFile/LoopCorrections/{_local_name(element.tag)}[{index}]",
        dof_per_eigenvalue=multiplicity * spin_dof,
        cw_constant=cw_constant,
        transverse_dof_per_eigenvalue=multiplicity * 2.0 if kind == "gauge" else 0.0,
        longitudinal_dof_per_eigenvalue=multiplicity if kind == "gauge" else 0.0,
    )


def _matrix_entries(element) -> list[str]:
    children = list(element)
    if children:
        rows = [child for child in children if _local_name(child.tag).lower() in {"row", "matrixrow"}]
        if rows:
            result: list[str] = []
            for row in rows:
                cells = list(row)
                if cells:
                    result.extend(_text_of(cell) for cell in cells)
                else:
                    result.extend(_split_row(_text_of(row)))
            return [item for item in result if item]
        elements = [child for child in children if _local_name(child.tag).lower() in {"element", "entry", "matrixelement"}]
        if elements:
            positioned: list[tuple[int, int, str]] = []
            for child in elements:
                row = int(child.attrib.get("Row", child.attrib.get("row", "1")))
                column = int(child.attrib.get("Column", child.attrib.get("column", "1")))
                positioned.append((row, column, _text_of(child)))
            return [value for _row, _column, value in sorted(positioned)]
    return [line.strip().rstrip(",") for line in _text_of(element).splitlines() if line.strip().rstrip(",")]


def _split_row(text: str) -> list[str]:
    return [item.strip() for item in text.strip().strip("{}").split(",") if item.strip()]


def _normalize_expression(expression: str, block_references: dict[str, dict[str, Any]]) -> str:
    def replace(match: re.Match[str]) -> str:
        block = match.group(1).upper()
        indices = tuple(
            _integral_block_index(value)
            for value in (match.group(2) or "").split(",")
            if value.strip()
        )
        symbol = _block_symbol(block, indices)
        block_references.setdefault(symbol, {"block": block, "indices": list(indices)})
        return symbol

    replaced = _BLOCK_REFERENCE.sub(replace, str(expression).strip())
    try:
        return parse_safe_expression(replaced).python
    except SafeExpressionError as exc:
        raise SarahImportError(f"Unsupported SARAH expression {expression!r}: {exc}") from exc


def _integral_block_index(value: str) -> int:
    integer, separator, fraction = value.strip().partition(".")
    if separator and any(character != "0" for character in fraction):
        raise SarahImportError(f"SLHA block index must be integral, found {value!r}.")
    return int(integer)


def _parse_dsb_minimum(element, block_references: dict[str, dict[str, Any]]) -> dict[str, str]:
    result: dict[str, str] = {}
    if element is None:
        return result
    for line in _text_lines(element):
        match = _ASSIGNMENT.match(line)
        if not match:
            raise SarahImportError(f"Malformed DsbMinimum assignment: {line!r}")
        result[match.group(1)] = _normalize_expression(match.group(2), block_references)
    return result


def _parse_scale_policy(root) -> dict[str, str]:
    section = _optional_descendant(root, "RenormalizationScaleChoices")
    fixed = _optional_descendant(section, "FixedScaleChoice") if section is not None else None
    return {
        "evaluation_type": _text_of(_optional_descendant(fixed, "EvaluationType")) if fixed is not None else "",
        "evaluation_argument": _text_of(_optional_descendant(fixed, "EvaluationArgument")) if fixed is not None else "",
    }


def _resolve_parameter_values(
    *,
    block_references: dict[str, dict[str, Any]],
    derived_parameters: dict[str, str],
    slha: SlhaDocument | None,
    overrides: dict[str, float],
) -> dict[str, float]:
    values = dict(overrides)
    if slha:
        for symbol, reference in block_references.items():
            value = slha.value(reference["block"], reference["indices"])
            if value is not None:
                values.setdefault(symbol, value)
    pending = dict(derived_parameters)
    while pending:
        progressed = False
        for name, expression in list(pending.items()):
            try:
                values[name] = evaluate_safe_expression(expression, values)
            except (SafeExpressionError, KeyError):
                continue
            del pending[name]
            progressed = True
        if not progressed:
            break
    return values


def _resolve_renormalization_scale_sq(
    policy: dict[str, str],
    slha: SlhaDocument | None,
    overrides: dict[str, float],
) -> float | None:
    if "renormScaleSq" in overrides:
        return float(overrides["renormScaleSq"])
    if "Q" in overrides:
        return float(overrides["Q"]) ** 2
    evaluation_type = policy.get("evaluation_type", "").strip().lower()
    argument = policy.get("evaluation_argument", "").strip()
    if evaluation_type == "fixednumber":
        try:
            return float(argument) ** 2
        except ValueError:
            return None
    if evaluation_type in {"blocklowestscale", "blockscale"} and slha:
        block = slha.blocks.get(argument.upper())
        if block and block.scale is not None:
            return block.scale**2
    return None


def _load_thermal_metadata(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    if source.stat().st_size > MAX_JSON_BYTES:
        raise SarahImportError(f"Thermal metadata exceeds the {MAX_JSON_BYTES} byte safety limit: {source}")
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SarahImportError(f"Malformed thermal metadata JSON {source}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != THERMAL_METADATA_SCHEMA:
        raise SarahImportError(
            f"Thermal metadata must use schema {THERMAL_METADATA_SCHEMA!r}: {source}"
        )
    return payload


def _validate_thermal_source_hashes(
    metadata: dict[str, Any],
    *,
    vin: Path,
    parameter_map: Path,
) -> None:
    provenance = metadata.get("provenance")
    hashes = provenance.get("source_hashes") if isinstance(provenance, dict) else None
    if not isinstance(hashes, dict):
        raise SarahImportError("Thermal metadata is missing provenance.source_hashes.")
    expected = {
        "vin": hashlib.sha256(vin.read_bytes()).hexdigest(),
        "parameter_map": hashlib.sha256(parameter_map.read_bytes()).hexdigest(),
    }
    for key, value in expected.items():
        recorded = str(hashes.get(key, "")).strip().lower()
        if recorded != value:
            raise SarahImportError(
                f"Thermal metadata {key} SHA256 does not match the supplied source file. "
                "Regenerate the companion metadata from the same SARAH export."
            )


def _normalize_parameter_overrides(parameters: dict[str, Any]) -> dict[str, float]:
    result: dict[str, float] = {}
    for raw_name, raw_value in parameters.items():
        name = str(raw_name).strip()
        block_match = _BLOCK_REFERENCE.fullmatch(name)
        if block_match:
            indices = tuple(
                _integral_block_index(value)
                for value in (block_match.group(2) or "").split(",")
                if value.strip()
            )
            name = _block_symbol(block_match.group(1).upper(), indices)
        if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
            raise SarahImportError(f"Parameter override {raw_name!r} must be a real number.")
        result[name] = float(raw_value)
    return result


def _assignment_map(element) -> dict[str, str]:
    result: dict[str, str] = {}
    if element is None:
        return result
    for line in _text_lines(element):
        match = _ASSIGNMENT.match(line)
        if not match:
            raise SarahImportError(f"Malformed derived-parameter assignment: {line!r}")
        result[match.group(1)] = match.group(2)
    return result


def _expression_names(expressions: Iterable[str]) -> set[str]:
    names: set[str] = set()
    for expression in expressions:
        names.update(parse_safe_expression(expression).names)
    return names


def _consistency_report(model: SarahImportedModel) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    for sector in model.mass_sectors:
        size = len(sector.matrix)
        symmetric = all(
            _expressions_equivalent(sector.matrix[row][column], sector.matrix[column][row])
            for row in range(size)
            for column in range(size)
        )
        checks.append(
            {
                "sector": sector.name,
                "kind": sector.kind,
                "size": size,
                "square": all(len(row) == size for row in sector.matrix),
                "symbolically_symmetric": symmetric,
                "dof_per_eigenvalue": sector.dof_per_eigenvalue,
                "status": "pass" if symmetric else "requires_numeric_symmetry_check",
            }
        )
    return {
        "schema": "ptagent.sarah_consistency.v1",
        "model": model.model_name,
        "checks": checks,
        "parameter_coverage": {
            "resolved": sorted(model.parameter_values),
            "unresolved": list(model.unresolved_parameters),
            "status": "pass" if not model.unresolved_parameters else "blocked",
        },
        "renormalization": {
            "scheme": model.renormalization_scheme,
            "scale_sq": model.renormalization_scale_sq,
            "status": "pass" if model.renormalization_scale_sq is not None else "blocked",
        },
        "thermal_alignment": {
            "status": "pass" if model.thermal_ready else "partial_or_blocked",
            "blockers": list((model.thermal_result or {}).get("blockers", ["thermal_metadata_missing"])),
        },
        "source_hashes": {
            key: value.get("sha256")
            for key, value in model.provenance.items()
            if isinstance(value, dict) and value.get("sha256")
        },
        "unresolved_parameters": list(model.unresolved_parameters),
        "thermal_ready": model.thermal_ready,
        "numerical_ready": model.numerical_ready,
    }


def _require_real_symmetric_sectors(sectors: list[SarahMassSector]) -> None:
    for sector in sectors:
        for row in range(len(sector.matrix)):
            for column in range(row + 1, len(sector.matrix)):
                left = sector.matrix[row][column]
                right = sector.matrix[column][row]
                if not _expressions_equivalent(left, right):
                    raise SarahImportError(
                        f"Mass-squared matrix {sector.name!r} is not real symmetric at entries "
                        f"[{row},{column}] and [{column},{row}]."
                    )


def _expressions_equivalent(left: str, right: str) -> bool:
    if left == right:
        return True
    try:
        import sympy as sp

        return bool(sp.simplify(sp.sympify(left) - sp.sympify(right)) == 0)
    except Exception:
        return False


def _validate_thermal_sector_mappings(
    thermal_result: dict[str, Any],
    sectors: list[SarahMassSector],
) -> None:
    mappings = thermal_result.get("sector_mappings")
    if not isinstance(mappings, dict):
        return
    blockers = thermal_result.setdefault("blockers", [])
    for kind in ("scalar", "gauge"):
        rows = mappings.get(kind)
        if not isinstance(rows, list):
            blocker = f"{kind}_sector_mappings_missing"
            if blocker not in blockers:
                blockers.append(blocker)
            continue
        by_sector = {
            str(row.get("sector", "")): row
            for row in rows
            if isinstance(row, dict) and str(row.get("sector", ""))
        }
        for sector in (item for item in sectors if item.kind == kind):
            mapping = by_sector.get(sector.name)
            if not mapping:
                blockers.append(f"thermal_mapping_missing:{sector.name}")
                continue
            indices = mapping.get("indices")
            if not isinstance(indices, list) or len(indices) != len(sector.matrix) or any(
                not isinstance(index, int) or index < 0 for index in indices
            ):
                blockers.append(f"thermal_mapping_invalid:{sector.name}")
                continue
            basis_size = len(thermal_result.get("scalar_basis" if kind == "scalar" else "gauge_basis", []))
            if len(set(indices)) != len(indices) or any(index >= basis_size for index in indices):
                blockers.append(f"thermal_mapping_out_of_range:{sector.name}")
    thermal_result["ready"] = not blockers


def _attach_high_temperature_supertrace_check(
    thermal_result: dict[str, Any],
    *,
    fields: list[str],
    sectors: list[SarahMassSector],
) -> None:
    if thermal_result.get("blockers") and not thermal_result.get("scalar_pi_over_t2"):
        return
    try:
        import sympy as sp
    except ImportError:
        blockers = thermal_result.setdefault("blockers", [])
        blockers.append("scalar_supertrace_check_requires_sympy")
        thermal_result["ready"] = False
        return
    try:
        high_temperature_term = sp.Integer(0)
        for sector in sectors:
            trace = sum(sp.sympify(sector.matrix[index][index]) for index in range(len(sector.matrix)))
            denominator = 48 if sector.kind == "fermion" else 24
            high_temperature_term += sp.Rational(str(sector.dof_per_eigenvalue)) * trace / denominator
        symbols = [sp.Symbol(name, real=True) for name in fields]
        imported_symbols = {str(symbol): symbol for symbol in high_temperature_term.free_symbols}
        derivative_symbols = [imported_symbols.get(name, symbol) for name, symbol in zip(fields, symbols)]
        hessian = [
            [sp.simplify(sp.diff(high_temperature_term, left, right)) for right in derivative_symbols]
            for left in derivative_symbols
        ]
        scalar_coefficients = thermal_result.get("scalar_pi_over_t2")
        scalar_basis = thermal_result.get("scalar_basis")
        consistency = thermal_result.get("consistency")
        raw_indices = consistency.get("background_scalar_indices") if isinstance(consistency, dict) else None
        if raw_indices is None and scalar_basis == fields:
            raw_indices = list(range(len(fields)))
        if not isinstance(raw_indices, list) or len(raw_indices) != len(fields) or any(
            not isinstance(index, int) or index < 0 for index in raw_indices
        ):
            result = {
                "status": "blocked",
                "equivalent": False,
                "reason": "background_scalar_indices_missing",
                "derived_pi_over_t2": [[str(value) for value in row] for row in hessian],
            }
        elif not isinstance(scalar_coefficients, list) or any(index >= len(scalar_coefficients) for index in raw_indices):
            result = {
                "status": "blocked",
                "equivalent": False,
                "reason": "background_scalar_indices_out_of_range",
                "derived_pi_over_t2": [[str(value) for value in row] for row in hessian],
            }
        else:
            selected = [
                [sp.sympify(scalar_coefficients[row][column]) for column in raw_indices]
                for row in raw_indices
            ]
            equivalent = all(
                sp.simplify(hessian[row][column] - selected[row][column]) == 0
                for row in range(len(fields))
                for column in range(len(fields))
            )
            result = {
                "status": "pass" if equivalent else "failed",
                "equivalent": bool(equivalent),
                "background_scalar_indices": raw_indices,
                "derived_pi_over_t2": [[str(value) for value in row] for row in hessian],
                "metadata_pi_over_t2": [[str(value) for value in row] for row in selected],
            }
    except Exception as exc:
        result = {
            "status": "blocked",
            "equivalent": False,
            "reason": f"{type(exc).__name__}: {exc}",
        }
    thermal_result["high_temperature_supertrace_check"] = result
    if result.get("status") != "pass":
        blockers = thermal_result.setdefault("blockers", [])
        if "scalar_supertrace_consistency_failed" not in blockers:
            blockers.append("scalar_supertrace_consistency_failed")
    thermal_result["ready"] = not thermal_result.get("blockers")


def _render_import_markdown(model: SarahImportedModel) -> str:
    sector_lines = [
        f"| `{sector.name}` | {sector.kind} | {len(sector.matrix)} | {sector.dof_per_eigenvalue:g} | `{sector.source_location}` |"
        for sector in model.mass_sectors
    ]
    issues = "\n".join(f"- **{item['severity']} `{item['code']}`:** {item['message']}" for item in model.issues) or "- None."
    return (
        f"# SARAH/Vevacious++ Import: {model.model_name}\n\n"
        f"- Vevacious++ version: `{model.version}`\n"
        f"- Scheme: `{model.renormalization_scheme}`\n"
        f"- Gauge fixing: `{model.gauge_fixing}`\n"
        f"- Background fields: `{', '.join(model.fields)}`\n"
        f"- Thermal ready: `{str(model.thermal_ready).lower()}`\n"
        f"- Numerical ready: `{str(model.numerical_ready).lower()}`\n\n"
        "| sector | kind | matrix size | dof/eigenvalue | source |\n"
        "|---|---|---:|---:|---|\n"
        + "\n".join(sector_lines)
        + "\n\n## Issues\n\n"
        + issues
        + "\n"
    )


def _render_thermal_markdown(model: SarahImportedModel) -> str:
    if not model.thermal_result:
        return (
            "# Thermal Derivation\n\n"
            "Thermal metadata was not supplied. Tree-level and zero-temperature import succeeded, "
            "but Parwani and Arnold-Espinosa compilation remain blocked.\n"
        )
    return (
        "# Thermal Derivation\n\n"
        f"- Ready: `{str(model.thermal_ready).lower()}`\n"
        f"- Scalar basis: `{', '.join(model.thermal_result.get('scalar_basis', []))}`\n"
        f"- Gauge basis: `{', '.join(model.thermal_result.get('gauge_basis', []))}`\n"
        "- Formula: `Pi_scalar/T^2 = lambda_abcc/24 + gauge_ab/4 + yukawa_ab/12`\n"
        "- Formula: `Pi_L/T^2 = C_A/3 + T_scalar/3 + T_Weyl/6`\n"
    )


def _required_descendant(root, name: str, path: Path):
    result = _optional_descendant(root, name)
    if result is None:
        raise SarahImportError(f"Required XML element {name!r} is missing from {path}.")
    return result


def _optional_descendant(root, name: str):
    if root is None:
        return None
    for element in root.iter():
        if _local_name(element.tag) == name:
            return element
    return None


def _required_text(root, name: str, path: Path) -> str:
    element = _required_descendant(root, name, path)
    text = _text_of(element)
    if not text:
        raise SarahImportError(f"Required XML element {name!r} is empty in {path}.")
    return text


def _text_of(element) -> str:
    if element is None:
        return ""
    return "".join(element.itertext()).strip()


def _text_lines(element) -> list[str]:
    return [line.strip() for line in _text_of(element).splitlines() if line.strip()]


def _local_name(tag: str) -> str:
    return str(tag).rsplit("}", 1)[-1]


def _block_symbol(block: str, indices: tuple[int, ...]) -> str:
    suffix = "_".join(str(index).replace("-", "m") for index in indices)
    return f"SLHA_{_program_symbol(block)}" + (f"_{suffix}" if suffix else "")


def _program_symbol(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", str(value)).strip("_") or "sector"
    if cleaned[0].isdigit():
        cleaned = "v_" + cleaned
    return cleaned


def _require_unique_identifiers(values: list[str], subject: str) -> None:
    if len(set(values)) != len(values):
        raise SarahImportError(f"Duplicate {subject} names are not supported: {values}")
    for value in values:
        if not re.fullmatch(r"[A-Za-z_]\w*", value):
            raise SarahImportError(f"Invalid {subject} name {value!r}.")


def _source_record(path: Path) -> dict[str, Any]:
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}


def _json_sha256(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _extract_sarah_version(path: Path) -> str:
    head = path.read_text(encoding="utf-8", errors="ignore")[:4096]
    match = re.search(r"SARAH\s+version\s*([^\s<]+)", head, re.IGNORECASE)
    return match.group(1) if match else "unknown"
