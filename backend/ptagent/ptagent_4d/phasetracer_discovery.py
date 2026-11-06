"""Bounded local discovery and a build/load probe, without installing dependencies."""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable


MAX_CANDIDATES = 32
MAX_ANCESTORS = 6
_CONTAINERS = ("", "external", "extern", "third_party", "vendor")
_HEADERS = (
    "EffectivePotential/include/effectivepotential/potential.hpp",
    "EffectivePotential/include/effectivepotential/one_loop_potential.hpp",
    "include/phase_finder.hpp",
    "include/action_calculator.hpp",
    "include/transition_finder.hpp",
)


def local_search_directories(project_root: Path, cwd: Path | None = None) -> list[Path]:
    """Bounded ancestor/sibling search; stop before the user's home or root."""
    project = Path(project_root).expanduser().resolve()
    current = Path(cwd or Path.cwd()).resolve()
    directories = []
    for start in (current, project):
        for base in (start, *list(start.parents)[:MAX_ANCESTORS]):
            if base == Path(base.anchor) or base == Path.home():
                break
            directories.extend(base / container for container in _CONTAINERS)
    return list(dict.fromkeys(directories))


def local_phasetracer_candidates(project_root: Path, cwd: Path | None = None) -> list[Path]:
    """Inspect nearby directories, never recursively scan a home or filesystem."""
    candidates: list[Path] = []
    seen: set[Path] = set()
    for directory in local_search_directories(project_root, cwd):
        try:
            children = sorted(directory.iterdir(), key=lambda path: path.name.casefold())
        except OSError:
            continue
        possible = [directory] if (directory / "EffectivePotential").is_dir() else []
        for child in children:
            name = child.name.lower().replace("-", "").replace("_", "")
            if name.startswith("phasetracer") and child.is_dir():
                possible.extend([child, child / "github_pt"])
        for path in possible:
            if not (path / "CMakeLists.txt").is_file():
                continue
            path = path.resolve()
            if path not in seen:
                seen.add(path)
                candidates.append(path)
                if len(candidates) >= MAX_CANDIDATES:
                    return candidates
    return candidates


def discover_phasetracer(
    project_root: Path, *, explicit_root: str = "", explicit_source: str = "configuration",
    cwd: Path | None = None, validator: Callable[..., tuple[bool, str]] | None = None,
    require_runtime: bool = True,
) -> dict[str, object]:
    validate = validator or validate_local_phasetracer
    raw = str(explicit_root or "").strip()
    candidates = [raw] if raw else [str(path) for path in local_phasetracer_candidates(project_root, cwd)]
    attempts: list[dict[str, object]] = []
    selection = explicit_source if raw else "local_discovery"
    selected = None
    for candidate in candidates:
        source_ready, message = validate(candidate, require_runtime=False)
        runtime_ready = False
        runtime_checked = source_ready and require_runtime
        if runtime_checked:
            runtime_ready, message = validate(candidate)
        ok = runtime_ready if require_runtime else source_ready
        row = {"root": candidate, "ok": ok, "message": message,
               "source_ready": source_ready, "runtime_ready": runtime_ready,
               "runtime_checked": runtime_checked,
               "build_required": not runtime_ready if runtime_checked else (None if source_ready else False)}
        attempts.append(row)
        # Prefer a verified runtime when preflighting, but retain eligible sources
        # for the first requested project build. Explicit roots never have alternates.
        if source_ready and selected is None:
            selected = row
        if ok:
            selected = row
            break
    if selected:
        path = Path(str(selected["root"])).expanduser()
        selected_root = str(path.resolve()) if path.is_dir() else str(selected["root"])
        message = str(selected["message"])
        if require_runtime and not selected["runtime_ready"]:
            message = ("PhaseTracer source/API is eligible, but its runtime is not ready or could not be verified. "
                       "A requested/approved project build must compile, link and validate it before use. " + message)
        if raw and not selected["ok"]:
            message += " No alternate checkout was selected; the explicit root remains authoritative."
    else:
        selected_root = raw
        details = "; ".join(f"{row['root']}: {row['message']}" for row in attempts)
        message = (f"Explicit PhaseTracer root is not usable ({selection}): {details}. "
                   "No alternate checkout was selected. Correct this path or explicitly clear it to allow local discovery."
                   if raw else "No compatible PhaseTracer checkout was found in the bounded local search. "
                   "This does not establish that PhaseTracer is not installed elsewhere. " + details)
    message += (" No dependency build, installation or download was attempted. "
                "Source generation needs no built libraries. A requested project compilation may build these sources; "
                "other builds, installations and downloads require explicit approval.")
    return {"available": bool(selected and selected["ok"]), "root": selected_root, "configured": bool(raw),
            "source_ready": bool(selected and selected["source_ready"]),
            "runtime_ready": bool(selected and selected["runtime_ready"]),
            "runtime_checked": bool(selected and selected["runtime_checked"]),
            "build_required": selected["build_required"] if selected else False,
            "selection": selection if raw or selected else "not_found", "message": message,
            "attempts": attempts, "candidates": candidates, "candidate_limit": MAX_CANDIDATES,
            "search_directories": [] if raw else [str(p) for p in local_search_directories(project_root, cwd)],
            "ancestor_limit": MAX_ANCESTORS}


def validate_local_phasetracer(root: str | Path, *, require_runtime: bool = True) -> tuple[bool, str]:
    """Source eligibility checks declarations; runtime readiness proves link/load.

    Neither mode builds the dependency. The normal requested project build owns
    that step, including the first build of a source-only checkout.
    """
    root = Path(root).expanduser().resolve()
    if not root.is_dir() or not (root / "CMakeLists.txt").is_file():
        return False, f"PhaseTracer source root needs a directory containing CMakeLists.txt: {root}"
    missing = [name for name in _HEADERS if not (root / name).is_file()]
    if missing:
        return False, f"PhaseTracer headers are incomplete at {root}: {', '.join(missing)}"
    try:
        potential = (root / _HEADERS[0]).read_text(encoding="utf-8")
        transitions = (root / _HEADERS[-1]).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return False, f"Cannot read PhaseTracer API at {root}: {exc}"
    potential = re.sub(r"/\*.*?\*/|//[^\n]*", "", potential, flags=re.S)
    transitions = re.sub(r"/\*.*?\*/|//[^\n]*", "", transitions, flags=re.S)
    missing_api = []
    if not re.search(r"\bvirtual\s+double\s+get_raddof\s*\(\s*\)\s*const\b", potential):
        missing_api.append("virtual EffectivePotential::Potential::get_raddof() const")
    if not re.search(r"\bvoid\s+set_fit_action_curve\s*\(\s*(?:const\s+)?bool\b|"
                     r"\bPROPERTY\s*\(\s*bool\s*,\s*fit_action_curve\s*,", transitions):
        missing_api.append("PhaseTracer::TransitionFinder::set_fit_action_curve(bool)")
    if missing_api:
        return False, f"PhaseTracer required API missing at {root}: {', '.join(missing_api)}"
    if not require_runtime:
        return True, "Source layout and required API declarations are eligible; built-library compatibility is not checked."
    cmake = shutil.which("cmake")
    if not cmake:
        return False, "CMake is unavailable; PhaseTracer API/library compatibility could not be verified."
    # Compile only a consumer. Never run the checkout's build/install targets:
    # upstream builds may write libraries and CMake registrations outside the build directory.
    try:
        with tempfile.TemporaryDirectory(prefix="ptagent-phase-probe-") as temporary:
            work = Path(temporary)
            (work / "CMakeLists.txt").write_text(_PROBE_CMAKE, encoding="utf-8")
            (work / "probe.cpp").write_text(_PROBE_CPP, encoding="utf-8")
            build = work / "build"
            commands = (
                ("configure", [cmake, "-S", str(work), "-B", str(build),
                               f"-DPHASETRACER_ROOT={root}", "-DCMAKE_BUILD_TYPE=Release"], 30),
                ("compile/link", [cmake, "--build", str(build), "--config", "Release", "--parallel", "2"], 60),
            )
            for stage, command, timeout in commands:
                ok, message = _run_probe(stage, command, timeout)
                if not ok:
                    return False, f"PhaseTracer {root}: {message}"
            binary = build / ("ptagent_probe.exe" if os.name == "nt" else "ptagent_probe")
            if not binary.exists() and os.name == "nt":
                binary = build / "Release" / "ptagent_probe.exe"
            ok, message = _run_probe("load/run", [str(binary)], 10)
            if not ok:
                return False, f"PhaseTracer {root}: {message}"
            if "PTAGENT_PHASE_API_OK" not in message:
                return False, f"PhaseTracer probe did not confirm capabilities at {root}: {message}"
            libraries = (build / "probe-libraries.txt").read_text(encoding="utf-8").strip()
            return True, "ok (required API compiled; libraries linked and loaded). " + libraries
    except (OSError, UnicodeError) as exc:
        return False, f"PhaseTracer compatibility probe could not complete at {root}: {type(exc).__name__}: {exc}"


def _run_probe(stage: str, command: list[str], timeout: int) -> tuple[bool, str]:
    try:
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace",
            start_new_session=os.name != "nt",
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"{stage} probe could not complete: {type(exc).__name__}: {exc}"
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        # CMake's compiler/build descendants inherit this group, including when
        # the direct process has already exited while a child holds its pipes.
        try:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               capture_output=True, timeout=5, check=False)
        except (OSError, subprocess.SubprocessError):
            process.kill()
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            stdout, stderr = exc.stdout or b"", exc.stderr or b""
            process.stdout.close()
            process.stderr.close()
            process.kill()
            process.wait(timeout=5)
        output = "\n".join(part.decode(errors="replace") if isinstance(part, bytes) else part
                           for part in (stdout, stderr))
        return False, (f"{stage} probe could not complete: TimeoutExpired after {timeout}s; "
                       "probe process group stopped. This is not evidence of a missing installation.\n" + output[-6000:])
    output = "\n".join(part.strip() for part in (stdout, stderr) if part.strip())
    if process.returncode:
        return False, (f"{stage} probe failed (exit {process.returncode}); check required API, "
                       f"library architecture and link/load dependencies.\n{output[-6000:]}")
    return True, output


_PROBE_CMAKE = r'''cmake_minimum_required(VERSION 3.16)
project(ptagent_phase_probe LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_FIND_USE_PACKAGE_REGISTRY OFF)
set(CMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY OFF)
list(APPEND CMAKE_MODULE_PATH "${PHASETRACER_ROOT}/cmake")
if(POLICY CMP0167)
  cmake_policy(SET CMP0167 OLD)
endif()
find_package(Boost REQUIRED COMPONENTS filesystem log OPTIONAL_COMPONENTS system)
find_package(ALGLIB REQUIRED)
find_package(Eigen3 REQUIRED)
find_package(NLopt REQUIRED)
find_package(Threads REQUIRED)
find_library(PT_LIBRARY NAMES phasetracer PATHS
  "${PHASETRACER_ROOT}/lib" "${PHASETRACER_ROOT}/build/lib"
  "${PHASETRACER_ROOT}/build/src" NO_DEFAULT_PATH)
find_library(EP_LIBRARY NAMES effectivepotential PATHS
  "${PHASETRACER_ROOT}/EffectivePotential/lib" "${PHASETRACER_ROOT}/lib"
  "${PHASETRACER_ROOT}/build/EffectivePotential/src" NO_DEFAULT_PATH)
if(NOT PT_LIBRARY OR NOT EP_LIBRARY)
  message(FATAL_ERROR "Built PhaseTracer/EffectivePotential libraries are missing from the selected checkout. Build existing sources only after user approval.")
endif()
add_executable(ptagent_probe probe.cpp)
target_include_directories(ptagent_probe BEFORE PRIVATE
  ${EIGEN3_INCLUDE_DIR} ${EIGEN3_INCLUDE_DIRS}
  "${PHASETRACER_ROOT}/include"
  "${PHASETRACER_ROOT}/EffectivePotential/include"
  "${PHASETRACER_ROOT}/EffectivePotential/include/effectivepotential"
  ${Boost_INCLUDE_DIRS} ${ALGLIB_INCLUDES} ${NLopt_INCLUDE_DIRS})
target_link_libraries(ptagent_probe PRIVATE ${PT_LIBRARY} ${EP_LIBRARY}
  ${Boost_LIBRARIES} ${ALGLIB_LIBRARIES} ${NLopt_LIBRARIES} Threads::Threads)
file(WRITE "${CMAKE_BINARY_DIR}/probe-libraries.txt"
  "phasetracer=${PT_LIBRARY}; effectivepotential=${EP_LIBRARY}; compiler=${CMAKE_CXX_COMPILER}")
'''


_PROBE_CPP = r'''#include <cmath>
#include <iostream>
#include "one_loop_potential.hpp"
#include "phase_finder.hpp"
#include "action_calculator.hpp"
#include "transition_finder.hpp"

class Probe : public EffectivePotential::OneLoopPotential {
public:
  size_t get_n_scalars() const override { return 1; }
  double V0(Eigen::VectorXd x) const override { return x.squaredNorm(); }
  double get_raddof() const override { return 0.; }
};

int main() {
  Probe model;
  PhaseTracer::PhaseFinder phases(model);
  PhaseTracer::ActionCalculator actions(model);
  PhaseTracer::TransitionFinder transitions(phases, actions);
  transitions.set_fit_action_curve(false);
  const auto x = Eigen::VectorXd::Zero(1).eval();
  if (!std::isfinite(model.V(x, 0.))) return 1;
  std::cout << "PTAGENT_PHASE_API_OK\n";
}
'''


if __name__ == "__main__":
    ok, message = validate_local_phasetracer(Path(sys.argv[1]), require_runtime="--source-only" not in sys.argv[2:])
    print(json.dumps({"ok": ok, "message": message}))
    raise SystemExit(0 if ok else 1)
