from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ptagent.runtime_metadata import collect_software_versions

from .sarah_discovery import SarahDiscoveryError, resolve_sarah_model


class SarahExportError(RuntimeError):
    """Raised when the optional SARAH automation route cannot produce its files."""


def export_sarah_model(
    *,
    sarah_root: str | Path = "",
    model: str,
    run_dir: str | Path,
    model_search_path: str | Path | None = None,
    slha_path: str | Path | None = None,
    wolframscript: str = "wolframscript",
    timeout_seconds: int = 600,
) -> dict[str, Any]:
    try:
        discovery = resolve_sarah_model(model, sarah_root=sarah_root,
                                        model_search_path=model_search_path, wolframscript=wolframscript)
    except SarahDiscoveryError as exc:
        raise SarahExportError(str(exc)) from exc
    root = Path(discovery["root"])
    sarah_main = root / "SARAH.m"
    model = discovery["model"]
    model_search_path = discovery["model_search_path"]
    executable = shutil.which(wolframscript) if not Path(wolframscript).is_absolute() else wolframscript
    if not executable or not Path(executable).exists():
        raise SarahExportError("wolframscript is required only for export-sarah and was not found.")
    output = Path(run_dir).expanduser().resolve()
    if (output / "contract_template.md").exists():
        raise SarahExportError("Export never overwrites an existing contract. Use a fresh task directory.")
    input_dir = output / "input"
    proof_dir = output / "proof_materials"
    input_dir.mkdir(parents=True, exist_ok=True)
    proof_dir.mkdir(parents=True, exist_ok=True)

    companion = Path(__file__).resolve().parents[3] / "references" / "sarah" / "ptagent_sarah_export.wl"
    component_exporter = companion.with_name("ptagent_sarah_components.wl")
    tree_exporter = companion.with_name("ptagent_sarah_tree_relations.wl")
    for required in (companion, component_exporter, tree_exporter):
        if not required.is_file():
            raise SarahExportError(f"Bundled companion exporter is missing: {required}")
    from .spheno_benchmark import read_spheno_benchmark
    source_directory = Path(model_search_path) / model
    declarations = read_spheno_benchmark(source_directory)
    (input_dir / "ptagent_source_declarations.json").write_text(
        json.dumps(declarations, indent=2) + "\n", encoding="utf-8")
    driver = input_dir / "run_sarah_export.wl"
    driver.write_text(
        _render_driver(
            sarah_main=sarah_main,
            model=model,
            model_search_path=Path(model_search_path).expanduser().resolve() if model_search_path else None,
            output_dir=input_dir,
            companion=companion,
        ),
        encoding="utf-8",
    )
    try:
        completed = subprocess.run(
            [str(executable), "-file", str(driver)],
            cwd=str(input_dir),
            capture_output=True,
            text=True,
            timeout=max(1, int(timeout_seconds)),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SarahExportError(
            "wolframscript timed out before completing the SARAH export. On macOS, a restricted "
            "agent sandbox can block the WSTP shared-memory transport even when the licensed "
            "WolframKernel works normally. Verify the kernel directly, then rerun export-sarah "
            "with permission to execute the existing local wolframscript outside the restricted sandbox."
        ) from exc
    (proof_dir / "sarah_export_stdout.txt").write_text(completed.stdout, encoding="utf-8")
    (proof_dir / "sarah_export_stderr.txt").write_text(completed.stderr, encoding="utf-8")
    if completed.returncode != 0:
        raise SarahExportError(
            "SARAH export failed. Review proof_materials/sarah_export_stderr.txt; "
            "the existing SARAH installation was not modified."
        )

    vin_candidates = sorted(input_dir.glob("*.vin"))
    map_path = input_dir / "ScaleAndBlock.xml"
    if len(vin_candidates) != 1 or not map_path.is_file():
        raise SarahExportError(
            "MakeVevacious did not produce exactly one .vin file and ScaleAndBlock.xml in the task input directory."
        )
    thermal_path = input_dir / "ptagent_thermal_metadata.json"
    component_path = input_dir / "ptagent_sarah_components.json"
    tree_path = input_dir / "ptagent_sarah_tree_relations.json"
    model_parent = Path(model_search_path).expanduser().resolve() if model_search_path else root / "Models"
    source_directory = model_parent / model
    if tree_path.is_file():
        tree_payload = json.loads(tree_path.read_text())
        tree_payload["provenance"] = {
            "vin_sha256": _record(vin_candidates[0])["sha256"],
            "parameter_map_sha256": _record(map_path)["sha256"],
            "exporter_sha256": _record(tree_exporter)["sha256"],
            "model_source_sha256": {p.name: _record(p)["sha256"] for p in sorted(source_directory.iterdir())
                                    if p.is_file() and p.suffix in {".m", ".wl"}},
        }
        tree_path.write_text(json.dumps(tree_payload, indent=2) + "\n")
    thermal_metadata_ready = False
    supplied_metadata = json.loads(thermal_path.read_text(encoding="utf-8")) if thermal_path.is_file() else {}
    if not isinstance(supplied_metadata, dict):
        raise SarahExportError("The companion exporter produced non-object thermal metadata.")
    if component_path.is_file() and supplied_metadata.get("complete") is not True:
        from .sarah_components import complete_component_export
        from .sarah_import import import_sarah_model

        source_model = import_sarah_model(vin_path=vin_candidates[0], parameter_map_path=map_path)
        metadata = complete_component_export(component_path, source_model)
        thermal_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    if thermal_path.is_file():
        _complete_thermal_provenance(
            thermal_path,
            vin_path=vin_candidates[0],
            parameter_map_path=map_path,
            exporter_path=companion,
            component_path=component_path if component_path.is_file() else None,
            component_exporter=component_exporter if component_path.is_file() else None,
            model_source_dir=source_directory,
            sarah_main=sarah_main,
        )
        thermal_payload = json.loads(thermal_path.read_text(encoding="utf-8"))
        thermal_metadata_ready = bool(
            isinstance(thermal_payload, dict)
            and thermal_payload.get("complete") is True
            and not thermal_payload.get("blockers")
        )
    copied_slha: Path | None = None
    if slha_path:
        source = Path(slha_path).expanduser().resolve()
        copied_slha = input_dir / source.name
        if source != copied_slha:
            shutil.copy2(source, copied_slha)
    manifest = {
        "schema": "ptagent.sarah_export.v1",
        "model": model,
        "sarah_root": str(root),
        "source_discovery": discovery,
        "vin": _record(vin_candidates[0]),
        "parameter_map": _record(map_path),
        "thermal_metadata": _record(thermal_path) if thermal_path.is_file() else None,
        "thermal_metadata_ready": thermal_metadata_ready,
        "component_data": _record(component_path) if component_path.is_file() else None,
        "tree_relations": _record(tree_path) if tree_path.is_file() else None,
        "slha": _record(copied_slha) if copied_slha and copied_slha.is_file() else None,
        "driver": _record(driver),
        "companion_exporter": _record(companion),
        "software_versions": collect_software_versions(
            sarah_root=root, wolframscript=executable,
            wolfram_version=_export_wolfram_version(completed.stdout),
        ),
    }
    if source_directory.is_dir():
        archived = input_dir / "sarah_model"
        archived.mkdir(exist_ok=True)
        for source in sorted(source_directory.iterdir()):
            if source.is_file() and source.suffix in {".m", ".wl"}:
                shutil.copy2(source, archived / source.name)
        manifest["model_source"] = str(archived)
    manifest_path = proof_dir / "sarah_export.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest["manifest"] = str(manifest_path)
    return manifest


def _export_wolfram_version(stdout: str) -> str | None:
    prefix = "PTAGENT_WOLFRAM_VERSION="
    for line in stdout.splitlines():
        if line.startswith(prefix):
            try:
                value = json.loads(line[len(prefix):])
            except ValueError:
                continue
            if isinstance(value, str):
                return value
    return None


def _render_driver(
    *,
    sarah_main: Path,
    model: str,
    model_search_path: Path | None,
    output_dir: Path,
    companion: Path,
) -> str:
    if not model.strip() or any(char in model for char in "\"\n\r"):
        raise SarahExportError("--model must be a non-empty SARAH model name without quotes or newlines.")
    search_line = ""
    if model_search_path:
        search_line = (
            f'If[!MemberQ[SARAH[InputDirectories], "{_wl_path(model_search_path)}"], '
            f'AppendTo[SARAH[InputDirectories], "{_wl_path(model_search_path)}"]];\n'
        )
    return (
        f'SetDirectory["{_wl_path(output_dir)}"];\n'
        + 'Print["PTAGENT_WOLFRAM_VERSION=" <> ExportString[$Version, "RawJSON"]];\n'
        + f'Get["{_wl_path(sarah_main)}"];\n'
        + f'SARAH[OutputDirectory] = "{_wl_path(output_dir)}";\n'
        + search_line
        + f'Start["{model}"];\n'
        + f'Get["{_wl_path(companion.with_name("ptagent_sarah_components.wl"))}"];\n'
        + 'PTagentCompleteScalarSectors[];\n'
        + f'MakeVevacious[Version -> "++", OutputFile -> "{_safe_filename(model)}.vin"];\n'
        + f'Get["{_wl_path(companion)}"];\n'
        + 'PTagentExportThermalMetadata[FileNameJoin[{Directory[], "ptagent_thermal_metadata.json"}]];\n'
        + 'Export[FileNameJoin[{Directory[], "ptagent_sarah_components.json"}], PTagentComponentData[], "RawJSON"];\n'
        + f'Get["{_wl_path(companion.with_name("ptagent_sarah_tree_relations.wl"))}"];\n'
        + 'PTagentExportTreeRelations[FileNameJoin[{Directory[], "ptagent_sarah_tree_relations.json"}], FileNameJoin[{Directory[], "ptagent_source_declarations.json"}]];\n'
        + 'If[ValueQ[$sarahCurrentVevaciousDir], '
        + 'CopyFile[FileNameJoin[{$sarahCurrentVevaciousDir, "ScaleAndBlock.xml"}], FileNameJoin[{Directory[], "ScaleAndBlock.xml"}], OverwriteTarget -> True];\n'
        + f'CopyFile[FileNameJoin[{{$sarahCurrentVevaciousDir, "{_safe_filename(model)}.vin"}}], FileNameJoin[{{Directory[], "{_safe_filename(model)}.vin"}}], OverwriteTarget -> True]];\n'
    )


def _safe_filename(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"_", "-"} else "_" for char in value) or "model"


def _wl_path(path: Path) -> str:
    return str(path).replace("\\", "/").replace('"', '\\"')


def _record(path: Path) -> dict[str, str]:
    return {
        "path": str(path.resolve()),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _complete_thermal_provenance(
    thermal_path: Path,
    *,
    vin_path: Path,
    parameter_map_path: Path,
    exporter_path: Path,
    component_path: Path | None = None,
    component_exporter: Path | None = None,
    model_source_dir: Path | None = None,
    sarah_main: Path | None = None,
) -> None:
    payload = json.loads(thermal_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SarahExportError("The companion exporter produced non-object thermal metadata.")
    provenance = payload.setdefault("provenance", {})
    if not isinstance(provenance, dict):
        raise SarahExportError("The companion exporter produced invalid thermal provenance.")
    provenance.setdefault("exporter_version", "ptagent-sarah-exporter-1")
    provenance["source_hashes"] = {
        "vin": _record(vin_path)["sha256"],
        "parameter_map": _record(parameter_map_path)["sha256"],
        "companion_exporter": _record(exporter_path)["sha256"],
    }
    if component_path is not None and component_exporter is not None:
        provenance["source_hashes"].update({
            "component_data": _record(component_path)["sha256"],
            "component_exporter": _record(component_exporter)["sha256"],
            "component_engine": _record(Path(__file__).with_name("sarah_components.py"))["sha256"],
        })
    if model_source_dir is not None:
        provenance["source_hashes"].update({
            f"model/{path.name}": _record(path)["sha256"] for path in sorted(model_source_dir.glob("*.m"))
        })
    if sarah_main is not None:
        provenance["source_hashes"]["sarah_main"] = _record(sarah_main)["sha256"]
    thermal_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
