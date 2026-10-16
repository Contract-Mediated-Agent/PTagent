"""Opt-in, source-checked compatibility for old DRalgo/GroupMath loaders.

No model expressions are evaluated here. The generated prelude is process-local;
installed files and System definitions are never patched.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

from .contract import ThreeDeftBlocked
from .extraction import _comment_free_source, _source_statements


COMPAT_ENV = "PTAGENT_DRALGO_COMPAT"
SOURCE_ENV = "PTAGENT_DRALGO_COMPAT_SOURCE"
COMPAT_MODE = "legacy-groupmath"


def source_load_policy(text: str) -> tuple[bool, str]:
    """Accept only an explicit, unconditional loading prefix, never dynamic code."""
    statements = _source_statements(text)
    policy = re.fullmatch(r"(?:Global`)?\$LoadGroupMath\s*=\s*(True|False)", statements[0]) if statements else None
    needs = re.fullmatch(r'(?:System`)?Needs\s*\[\s*"(DRalgo`(?:DRalgo`)?)"\s*\]', statements[1]) if len(statements) > 1 else None
    if not policy or not needs:
        raise ThreeDeftBlocked(
            "Legacy DRalgo compatibility requires a literal $LoadGroupMath=True/False; "
            'Needs["DRalgo`" or "DRalgo`DRalgo`"]; prefix. Dynamic or earlier loading abstains.'
        )
    suffix = "\n".join(statements[2:])
    if re.search(r"\$LoadGroupMath|\$Context|\$Path|<<|\b(?:Needs|Get|ToExpression|Symbol|Begin|BeginPackage|EndPackage|Clear|ClearAll|Remove|SetDirectory)\b", suffix):
        raise ThreeDeftBlocked("Legacy DRalgo compatibility abstains on additional/dynamic loading or context changes.")
    return policy[1] == "True", needs[1]


def _discover_package_files(executable: str, load_groupmath: bool) -> dict:
    group = 'FindFile["GroupMath`GroupMath`"]' if load_groupmath else '""'
    group_loader = 'FindFile["GroupMath`"]' if load_groupmath else '""'
    code = '''Print["PTAGENT_LOADER_PATHS=" <> ExportString[<|
      "dralgo" -> Quiet[FindFile["DRalgo`DRalgo`"]],
      "groupmath_loader" -> @LOADER@,
      "groupmath" -> @GROUP@,
      "system_matrix_protected" -> (NameQ["System`BlockDiagonalMatrix"] &&
        MemberQ[Attributes[System`BlockDiagonalMatrix], Protected])|>, "RawJSON"]];'''.replace("@GROUP@", group).replace("@LOADER@", group_loader)
    try:
        result = subprocess.run([executable, "-code", code], capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ThreeDeftBlocked(f"Legacy DRalgo package-path probe failed: {exc}") from exc
    prefix = "PTAGENT_LOADER_PATHS="
    payload = result.stdout.partition(prefix)[2]
    try:
        paths, _ = json.JSONDecoder().raw_decode(payload.strip())
    except (ValueError, TypeError) as exc:
        details = (result.stdout + "\n" + result.stderr).strip()[-600:]
        raise ThreeDeftBlocked("Legacy DRalgo package-path probe did not return usable paths; check the local Wolfram runtime.\n" + details) from exc
    if result.returncode or not isinstance(paths, dict):
        raise ThreeDeftBlocked("Legacy DRalgo package-path probe failed; no compatibility changes were made.")
    return paths


def _read_package(path: object) -> tuple[Path, str]:
    if not isinstance(path, str) or not Path(path).is_file():
        raise ThreeDeftBlocked(f"Legacy DRalgo compatibility requires an existing, readable package file: {path!r}.")
    file = Path(path).resolve()
    return file, _comment_free_source(file.read_text(encoding="utf-8"))


def verify_legacy_packages(paths: dict, load_groupmath: bool) -> list[Path]:
    """Recognize only the diagnosed old context and unqualified matrix definition."""
    dralgo, code = _read_package(paths.get("dralgo"))
    if not re.match(r'\s*BeginPackage\s*\[\s*"DRalgo`"\s*\]', code):
        raise ThreeDeftBlocked("Legacy compatibility abstains: installed DRalgo does not declare the known DRalgo` context.")
    if not re.search(r'If\s*\[\s*Global`\$LoadGroupMath\s*,\s*Get\s*\[\s*"GroupMath`"\s*\]', code):
        raise ThreeDeftBlocked("Legacy compatibility abstains: installed DRalgo's GroupMath loading policy is unrecognized.")
    files = [dralgo]
    if load_groupmath:
        groupmath, group_code = _read_package(paths.get("groupmath"))
        entry, entry_code = _read_package(paths.get("groupmath_loader", str(groupmath)))
        if entry != groupmath:
            statements = _source_statements(entry_code)
            if not statements or not re.fullmatch(r'Get\s*\[\s*"GroupMath`GroupMath`"\s*\]', statements[-1]):
                raise ThreeDeftBlocked("Legacy compatibility abstains: GroupMath initializer has no supported literal forwarding call.")
            files.append(entry)
        generic, generic_code = _read_package(str(groupmath.parent / "GenericTools.m"))
        if (not re.match(r'\s*BeginPackage\s*\[\s*"GroupMath`"\s*\]', group_code)
                or not re.search(r'Get\s*\[\s*FileNameJoin\s*\[\s*\{\s*\$GroupMathDirectory\s*,\s*"GenericTools.m"\s*\}', group_code)
                or not re.search(r'(?m)^\s*BlockDiagonalMatrix\s*\[\s*[A-Za-z][A-Za-z0-9]*_\s*\]\s*:=', generic_code)
                or paths.get("system_matrix_protected") is not True):
            raise ThreeDeftBlocked("Legacy compatibility abstains: the known GroupMath/System matrix collision was not verified.")
        files += [groupmath, generic]
    return files


def compatibility_prelude(source: Path | None, executable: str) -> str:
    mode = os.environ.get(COMPAT_ENV, "").strip()
    if not mode:
        return ""
    if mode != COMPAT_MODE:
        raise ThreeDeftBlocked(f"Unsupported {COMPAT_ENV}={mode!r}; supported opt-in is {COMPAT_MODE}.")
    configured_source = os.environ.get(SOURCE_ENV, "").strip()
    if source is None:
        if not configured_source:
            raise ThreeDeftBlocked(f"Opt-in compatibility preflight requires {SOURCE_ENV} pointing to the reviewed source, to preserve its GroupMath policy.")
        source = Path(configured_source).expanduser()
    source = source.resolve()
    if configured_source and Path(configured_source).expanduser().resolve() != source:
        raise ThreeDeftBlocked(f"{SOURCE_ENV} differs from the contract source; compatibility abstains.")
    if not source.is_file():
        raise ThreeDeftBlocked(f"Compatibility source does not exist: {source}")
    policy, requested_context = source_load_policy(source.read_text(encoding="utf-8"))
    paths = _discover_package_files(executable, policy)
    files = verify_legacy_packages(paths, policy)
    return render_compatibility_prelude(source, files, policy, requested_context)


def render_compatibility_prelude(source: Path, files: list[Path], policy: bool, requested_context: str) -> str:
    # Bind the static decision to the same bytes at execution time.
    hashes = "{" + ",".join(
        "{" + json.dumps(str(file), ensure_ascii=False) + "," +
        json.dumps(hashlib.sha256(file.read_bytes()).hexdigest()) + "}"
        for file in [source, *files]
    ) + "}"
    text = r'''(* Explicit opt-in legacy package compatibility; no model or installed-file edits. *)
PTAgentLoader`fail[reason_] := (Print["PTAGENT_COMPAT_FAILED: " <> reason]; Exit[82]);
If[MemberQ[$Packages, "DRalgo`"] || MemberQ[$Packages, "GroupMath`"],
  PTAgentLoader`fail["requires a fresh kernel"]];
If[!TrueQ[And @@ ((FileExistsQ[#[[1]]] &&
    IntegerString[FileHash[#[[1]], "SHA256"], 16, 64] === #[[2]]) & /@ @HASHES@)],
  PTAgentLoader`fail["source or verified package bytes changed"]];
@MATRIX_BEFORE@
Global`$LoadGroupMath = @POLICY@;
Block[{$MessageList = {}},
  Needs["DRalgo`", @DRALGO_FILE@];
  PTAgentLoader`messages = $MessageList;
];
If[PTAgentLoader`messages =!= {}, PTAgentLoader`fail["package initialization emitted messages"]];
If[!MemberQ[$Packages, "DRalgo`"] || !TrueQ[And @@ (# > 0 & /@ {
  Length[DownValues[DRalgo`AllocateTensors]], Length[DownValues[DRalgo`ImportModelDRalgo]],
  Length[DownValues[DRalgo`PerformDRhard]], Length[DownValues[DRalgo`PerformDRsoft]]})],
  PTAgentLoader`fail["public definitions or declared package context missing"]];
@MATRIX_AFTER@
@ALIAS@
Print["PTAGENT_COMPAT_OK: legacy-groupmath; LoadGroupMath=@POLICY@"];
'''
    matrix_before = r'''
If[!MemberQ[Attributes[System`BlockDiagonalMatrix], Protected],
  PTAgentLoader`fail["protected System matrix capability changed"]];
PTAgentLoader`sample = Normal[System`BlockDiagonalMatrix[{{{2}}, {{3,4},{5,6}}}]];
PTAgentLoader`snapshot[] := {Attributes[System`BlockDiagonalMatrix], OwnValues[System`BlockDiagonalMatrix],
  DownValues[System`BlockDiagonalMatrix], UpValues[System`BlockDiagonalMatrix],
  SubValues[System`BlockDiagonalMatrix], Options[System`BlockDiagonalMatrix]};
PTAgentLoader`before = PTAgentLoader`snapshot[];
Symbol["GroupMath`BlockDiagonalMatrix"];
'''
    matrix_after = r'''
If[PTAgentLoader`before =!= PTAgentLoader`snapshot[] ||
   PTAgentLoader`sample =!= Normal[System`BlockDiagonalMatrix[{{{2}}, {{3,4},{5,6}}}]] ||
   GroupMath`BlockDiagonalMatrix[{{{2}}, {{3,4},{5,6}}}] =!= {{2,0,0},{0,3,4},{0,5,6}},
   PTAgentLoader`fail["matrix compatibility safety check failed"]];
Print["PTAGENT_COMPAT_SYSTEM_MATRIX_UNCHANGED=True"];
'''
    alias = r'''BeginPackage["DRalgo`DRalgo`", {"DRalgo`"}]; EndPackage[];''' if requested_context == "DRalgo`DRalgo`" else ""
    for key, value in {
        "HASHES": hashes, "POLICY": "True" if policy else "False",
        "DRALGO_FILE": json.dumps(str(files[0]), ensure_ascii=False),
        "MATRIX_BEFORE": matrix_before if policy else "",
        "MATRIX_AFTER": matrix_after if policy else "",
        "ALIAS": alias,
    }.items():
        text = text.replace("@" + key + "@", value)
    return text
