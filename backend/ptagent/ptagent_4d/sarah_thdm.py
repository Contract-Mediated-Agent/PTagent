"""Source-checked adapter for the real SARAH THDM (type I), not a model guesser."""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

import numpy as np
import sympy as sp

from .safe_expression import evaluate_safe_expression, parse_safe_expression
from .sarah_import import SarahImportError, SarahImportedModel

PROFILE_PATH = Path(__file__).resolve().parents[3] / "references/sarah/thdm-type-i-v1.json"
SCALAR_SECTORS = {"scalar_1_hh": [0, 1], "scalar_2_Ah": [2, 3], "scalar_3_Hm": [4, 5, 6, 7]}


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def expression(text: str) -> sp.Expr:
    parsed = parse_safe_expression(text)
    symbols = {name: sp.Symbol(name) for name in parsed.names}
    return sp.sympify(parsed.python, locals=symbols, rational=True)


def matrix_json(matrix: sp.Matrix) -> list[list[str]]:
    return [[str(sp.factor(value)) for value in row] for row in matrix.tolist()]


def audit_source(directory: Path) -> tuple[dict[str, Any], dict[str, float]]:
    profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    compact_files = {}
    for name, expected in profile["source_signatures"].items():
        path = directory / name
        if not path.is_file() or path.stat().st_size > 1024 * 1024:
            raise SarahImportError(f"THDM adapter needs a bounded source file: {path}")
        text = re.sub(r"\(\*.*?\*\)", "", path.read_text(encoding="utf-8"), flags=re.S)
        compact = re.sub(r"\s+", "", text).replace("\\", "")
        compact_files[name] = compact
        signature = compact
        if name == "SPheno.m":
            signature = re.sub(r"DefaultInputValues=\{[^}]*\};", "DefaultInputValues={};", signature)
        if hashlib.sha256(signature.encode()).hexdigest() != expected:
            raise SarahImportError(
                f"Unsupported source convention in {name}. The {profile['id']} adapter cannot "
                "be applied to a modified model; use symbolic import and a reviewed adapter."
            )
    # Only this numeric rule list is parsed. No Wolfram code is evaluated here.
    matches = re.findall(r"DefaultInputValues=\{([^}]*)\};", compact_files["SPheno.m"])
    if len(matches) != 1:
        raise SarahImportError("Expected one literal DefaultInputValues list in SPheno.m.")
    aliases = {**{f"Lambda{i}Input": f"Lambda{i}" for i in range(1, 6)}, "M12input": "M12", "TanBeta": "tanBeta"}
    values = {}
    for rule in matches[0].split(","):
        parts = rule.split("->")
        if len(parts) != 2 or parts[0] not in aliases or aliases[parts[0]] in values:
            raise SarahImportError("Unsupported or duplicate THDM default input rule.")
        value = evaluate_safe_expression(parts[1], {})
        if not math.isfinite(value):
            raise SarahImportError("THDM default inputs must be finite real numbers.")
        values[aliases[parts[0]]] = value
    if set(values) != set(aliases.values()):
        raise SarahImportError("SPheno.m is missing scalar example inputs.")
    return profile, values


def derive_model(model: SarahImportedModel, source_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Reconstruct the audited full multiplets and check all imported sectors."""
    audit_source(source_dir)
    if model.fields != ["phi1", "phi2"] or model.renormalization_scheme != "MSBAR" or model.gauge_fixing != "LANDAU":
        raise SarahImportError("The THDM adapter requires the audited neutral MSBAR/Landau source.")
    sectors = {s.name: s for s in model.mass_sectors}
    expected_derived = {}
    for name, index in (("M112", 20), ("M222", 21)):
        expected_derived.update({name: f"SLHA_HMIX_{index}", name+"SarahTree": f"SLHA_TREEHMIX_{index}",
                                 name+"SarahLoop": f"SLHA_LOOPHMIX_{index}",
                                 name+"Tree": f"ifnonzero({name}SarahTree, {name})", name+"Loop": f"ifnonzero({name}SarahLoop, {name})"})
    if model.derived_parameters != expected_derived:
        raise SarahImportError("THDM ScaleAndBlock.xml parameter mapping differs from the audited source.")
    expected_names = set(SCALAR_SECTORS) | {"fermion_4_Fd", "fermion_5_Fu", "fermion_6_Fe", "gauge_7_VP_VZ", "gauge_8_VWm"}
    if set(sectors) != expected_names:
        raise SarahImportError("THDM sector list differs from the audited source.")
    q = sp.symbols("h1 h2 a1 a2 c1r c2r c1i c2i", real=True)
    h1 = sp.Matrix([q[4] + sp.I*q[6], q[0] + sp.I*q[2]]) / sp.sqrt(2)
    h2 = sp.Matrix([q[5] + sp.I*q[7], q[1] + sp.I*q[3]]) / sp.sqrt(2)
    n1, n2, z = [(a.conjugate().T*b)[0] for a, b in ((h1, h1), (h2, h2), (h1, h2))]
    l1, l2, l3, l4, l5 = sp.symbols("SLHA_HMIX_31 SLHA_HMIX_32 SLHA_HMIX_33 SLHA_HMIX_34 SLHA_HMIX_35")
    m1, m2, m12 = sp.symbols("M112Tree M222Tree SLHA_HMIX_22")
    phi1, phi2, g1, g2 = sp.symbols("phi1 phi2 SLHA_GAUGE_1 SLHA_GAUGE_2")
    full = sp.expand(m1*n1+m2*n2+m12*(z+sp.conjugate(z))+l1*n1**2+l2*n2**2+l3*n1*n2+l4*z*sp.conjugate(z)+l5*(z**2+sp.conjugate(z)**2)/2)
    background = {**{x: 0 for x in q[2:]}, q[0]: phi1, q[1]: phi2}
    tree = expression(model.tree_potential)
    if sp.simplify(full.subs(background)-tree) != 0:
        raise SarahImportError("Imported THDM tree potential differs from the source adapter.")
    hessian = sp.hessian(full, q)
    matrices = {name: sp.Matrix([[expression(e) for e in row] for row in sec.matrix]) for name, sec in sectors.items()}
    expected = {name: hessian.extract(indices, indices).subs(background) for name, indices in SCALAR_SECTORS.items()}
    for name, group in (("fermion_4_Fd", "YD"), ("fermion_5_Fu", "YU"), ("fermion_6_Fe", "YE")):
        yuk = sp.Matrix(3, 3, lambda i, j: sp.Symbol(f"SLHA_{group}_{i+1}_{j+1}"))
        expected[name] = phi2**2*yuk.T*yuk/2
    norm = (phi1**2+phi2**2)/4
    expected["gauge_7_VP_VZ"] = norm*sp.Matrix([[g1**2, -g1*g2], [-g1*g2, g2**2]])
    expected["gauge_8_VWm"] = norm*g2**2*sp.eye(2)
    for name, reference in expected.items():
        dof = 1 if name in SCALAR_SECTORS else (4 if name == "fermion_6_Fe" else 12 if name.startswith("fermion") else 3)
        c = sp.Rational(5, 6) if name.startswith("gauge") else sp.Rational(3, 2)
        sec = sectors[name]
        expected_kind = "scalar" if name in SCALAR_SECTORS else "fermion" if name.startswith("fermion") else "gauge"
        if (sec.kind != expected_kind or matrices[name].shape != reference.shape or any(sp.simplify(x) != 0 for x in matrices[name]-reference)
                or sec.dof_per_eigenvalue != dof or abs(sec.cw_constant-float(c)) > 1e-12):
            raise SarahImportError(f"THDM source matrix, multiplicity or CW constant mismatch: {name}")
    extra = (sp.Symbol("M112Loop")-m1)*phi1**2/2+(sp.Symbol("M222Loop")-m2)*phi2**2/2
    if sp.simplify(expression(model.extra_polynomial)-extra) != 0:
        raise SarahImportError("Unexpected ExtraPolynomialPart in THDM input.")

    quartic = sp.hessian(sp.trace(hessian), q)
    scalar_gauge = sp.eye(8)*(g1**2/4+3*g2**2/4)
    trace_f = sum(sp.Rational(str(sectors[name].dof_per_eigenvalue))*sp.trace(mat) for name, mat in matrices.items() if name.startswith("fermion"))
    yuk_neutral = sp.simplify(sp.hessian(trace_f, [phi1, phi2])/4)
    if any(yuk_neutral[i, j] != 0 for i, j in ((0, 0), (0, 1), (1, 0))):
        raise SarahImportError("Unexpected Yukawa structure; this adapter is type I only.")
    scalar_yukawa = sp.diag(*[yuk_neutral[i % 2, i % 2] for i in range(8)])
    # Representations are fixed by the source signature, not inferred from the
    # neutral mass spectrum (which does not include massless neutrinos).
    scalar_reps = [(1, sp.Rational(1, 2), 2, 1)]*2
    fermion_reps = [(3, sp.Rational(1, 6), 2, 3), (3, -sp.Rational(1, 2), 2, 1),
                    (3, sp.Rational(1, 3), 1, 3), (3, -sp.Rational(2, 3), 1, 3), (3, 1, 1, 1)]
    def trace(reps, group):
        return sum(n*w*c*y**2 if group == "U1" else n*c*(sp.Rational(1, 2) if w == 2 else 0) for n, y, w, c in reps)
    adjoint = sp.diag(0, 2*g2**2, 2*g2**2, 2*g2**2)
    traces = [sp.diag(trace(reps, "U1")*g1**2, *([trace(reps, "SU2")*g2**2]*3)) for reps in (scalar_reps, fermion_reps)]
    scalar_pi = sp.simplify(quartic/24+scalar_gauge/4+scalar_yukawa/12)
    gauge_pi = sp.simplify(adjoint/3+traces[0]/3+traces[1]/6)
    high_t = sum(sp.Rational(str(sectors[name].dof_per_eigenvalue))*sp.trace(mat)/(48 if name.startswith("fermion") else 24) for name, mat in matrices.items())
    if any(sp.simplify(x) != 0 for x in sp.hessian(high_t, [phi1, phi2])-scalar_pi[:2, :2]):
        raise SarahImportError("THDM scalar thermal supertrace check failed.")
    solutions = sp.solve([sp.diff(tree, p) for p in (phi1, phi2)], [m1, m2], dict=True)
    if len(solutions) != 1:
        raise SarahImportError("Tree tadpoles do not have a unique quadratic-parameter solution.")
    flip = {phi1: -phi1, phi2: -phi2}
    if any(sp.simplify(x.subs(flip, simultaneous=True)-x) != 0 for mat in matrices.values() for x in mat):
        raise SarahImportError("Source matrices do not preserve simultaneous sign reflection.")
    proof = {"schema": "ptagent.sarah_preparation_checks.v1", "source_matrices_match": sorted(matrices),
             "tree_potential_matches": True, "scalar_supertrace_matches": True,
             "tadpole_solutions": {str(k): str(v) for k, v in solutions[0].items()},
             "simultaneous_symmetry": True, "scope": "Local tree checks and leading high-T contractions; no global vacuum proof."}
    metadata = {
        "schema": "ptagent.sarah_thermal.v1", "complete": True, "blockers": [],
        "background_fields": model.fields, "scalar_basis": [str(x) for x in q], "gauge_basis": ["B", "W3", "W1", "W2"],
        "gauge_groups": ["U1", "SU2", "SU3"], "scalar_components": [{"name": str(x)} for x in q],
        "fermion_components": [{"name": name} for name in ("q", "l", "d", "u", "e")],
        "representation_data": {"scalar_reps": [[str(x) for x in row] for row in scalar_reps], "weyl_reps": [[str(x) for x in row] for row in fermion_reps]},
        "coupling_tensors": {"full_scalar_potential": str(full), "quartic_abcc": matrix_json(quartic)},
        "basis_mappings": {"background_scalar_indices": [0, 1], "definition": "H_i=((c_ir+i*c_ii),(h_i+i*a_i))/sqrt(2)"},
        "conventions": {"normalization": "source Lambda1,2 without 1/2; +M12 with dimension mass squared", "approximation": "leading high-T, all source species relativistic"},
        "thermal_invariants": dict(zip(("scalar_quartic", "scalar_gauge", "scalar_yukawa", "gauge_adjoint", "gauge_scalar_trace", "gauge_fermion_trace"), map(matrix_json, (quartic, scalar_gauge, scalar_yukawa, adjoint, *traces)))),
        "sector_mappings": {"scalar": [{"sector": name, "indices": indices} for name, indices in SCALAR_SECTORS.items()], "gauge": [{"sector": "gauge_7_VP_VZ", "indices": [0, 1]}, {"sector": "gauge_8_VWm", "indices": [2, 3]}]},
        "consistency": {"scalar_pi_over_t2": matrix_json(scalar_pi), "background_scalar_indices": [0, 1]},
        "provenance": {"exporter_version": "ptagent-thdm-adapter-1", "source_hashes": {"vin": model.provenance["vin"]["sha256"], "parameter_map": model.provenance["parameter_map"]["sha256"], **{name: file_hash(source_dir/name) for name in ("THDM.m", "parameters.m", "SPheno.m")}}},
    }
    return metadata, proof


def bind_parameters(model: SarahImportedModel, profile: dict, defaults: dict, overrides: dict, proof: dict) -> tuple[dict, dict]:
    values = {**profile["parameters"], **defaults}
    unknown = set(overrides)-set(values)-set(model.unresolved_parameters)-{"renormScaleSq"}
    if unknown:
        raise SarahImportError("Unsupported inputs (no automatic physical-spectrum fit): " + ", ".join(sorted(unknown)))
    for key, value in overrides.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise SarahImportError(f"Parameter {key} must be a finite real number.")
    values.update({k: v for k, v in overrides.items() if k in values})
    if values["vSM"] <= 0 or values["tanBeta"] <= 0 or values["Q"] <= 0:
        raise SarahImportError("vSM, tanBeta and Q must be positive.")
    v1 = values["vSM"]/math.sqrt(1+values["tanBeta"]**2)
    v2 = values["tanBeta"]*v1
    bound = {"SLHA_HMIX_102": v1, "SLHA_HMIX_103": v2, "SLHA_HMIX_22": values["M12"],
             "SLHA_GAUGE_1": values["g1"], "SLHA_GAUGE_2": values["g2"], "Q": values["Q"]}
    bound.update({f"SLHA_HMIX_{30+i}": values[f"Lambda{i}"] for i in range(1, 6)})
    # SLHA and explicit native inputs take priority over the preset aliases.
    bound.update({k: v for k, v in overrides.items() if k in model.unresolved_parameters or k == "renormScaleSq"})
    if min(bound["SLHA_HMIX_102"], bound["SLHA_HMIX_103"], bound.get("renormScaleSq", values["Q"]**2)) <= 0:
        raise SarahImportError("The reference VEVs and renormalization scale squared must be positive.")
    for group, names in (("YU", ("u", "c", "t")), ("YD", ("d", "s", "b")), ("YE", ("e", "mu", "tau"))):
        for i in range(1, 4):
            for j in range(1, 4):
                bound.setdefault(f"SLHA_{group}_{i}_{j}", math.sqrt(2)*values[f"m_{names[i-1]}"]/bound["SLHA_HMIX_103"] if i == j else 0.0)
    at_vacuum = {**bound, "phi1": bound["SLHA_HMIX_102"], "phi2": bound["SLHA_HMIX_103"]}
    for name, formula in proof["tadpole_solutions"].items():
        bound.setdefault(name, evaluate_safe_expression(formula, at_vacuum))
    bound.setdefault("M112Loop", bound["M112Tree"])
    bound.setdefault("M222Loop", bound["M222Tree"])
    return bound, values


def check_point(model: SarahImportedModel, bound: dict, proof: dict) -> dict:
    phi = sp.symbols("phi1 phi2")
    vacuum = [bound["SLHA_HMIX_102"], bound["SLHA_HMIX_103"]]
    if not all(math.isfinite(x) for x in vacuum) or min(vacuum) <= 0:
        raise SarahImportError("The THDM preset requires positive nonzero neutral reference VEVs.")
    substitutions = {sp.Symbol(k): v for k, v in bound.items()}
    substitutions.update(dict(zip(phi, vacuum)))
    tree = expression(model.tree_potential)
    gradient = np.array([float(sp.diff(tree, p).subs(substitutions)) for p in phi])
    scale = max(1.0, np.linalg.norm(vacuum)**3)
    if not np.isfinite(gradient).all() or np.max(np.abs(gradient))/scale > 1e-9:
        raise SarahImportError("Reference point fails the tree tadpole residual check; no scan or refit was performed.")
    spectra = {}
    for sector in model.mass_sectors:
        matrix = sp.Matrix([[expression(x) for x in row] for row in sector.matrix])
        array = np.array(matrix.subs(substitutions), dtype=float)
        if not np.isfinite(array).all():
            raise SarahImportError(f"Nonfinite matrix at the reference point: {sector.name}")
        eig = np.linalg.eigvalsh(array)
        spectra[sector.name] = eig.tolist()
        if sector.name in SCALAR_SECTORS:
            tolerance = max(1.0, np.max(np.abs(eig)))*1e-8
            expected_zero = {"scalar_1_hh": 0, "scalar_2_Ah": 1, "scalar_3_Hm": 2}[sector.name]
            if min(eig) < -tolerance or np.count_nonzero(np.abs(eig) <= tolerance) != expected_zero:
                raise SarahImportError(f"Local scalar/Goldstone check failed: {sector.name}. This point is not accepted as the preset vacuum.")
    # Boundedness conditions use the audited full-doublet convention, not only
    # the two neutral background directions. This is not a global vacuum test.
    l1, l2, l3, l4, l5 = [bound[f"SLHA_HMIX_{30+i}"] for i in range(1, 6)]
    if l1 <= 0 or l2 <= 0 or min(l3, l3+l4-abs(l5)) <= -2*math.sqrt(l1*l2):
        raise SarahImportError("The point fails the strict tree quartic boundedness check for this adapter.")
    return {**proof, "numerical_checks": {"tadpole_residual": gradient.tolist(), "relative_tadpole_residual": float(np.max(np.abs(gradient))/scale), "vacuum_GeV": vacuum, "mass_squared_GeV2": spectra, "tree_quartic_bounded": True, "physical_scalar_curvatures_positive": True, "goldstone_count": 3}, "passed": True}
