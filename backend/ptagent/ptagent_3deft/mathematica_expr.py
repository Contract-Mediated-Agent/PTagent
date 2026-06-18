from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class ConvertedMathematicaExpression:
    expression: str
    symbol_map: dict[str, str]
    identifiers: tuple[str, ...]
    source_format: str = "python_like"
    cform_symbol_map: dict[str, str] = field(default_factory=dict)
    cform_post_map: dict[str, str] = field(default_factory=dict)
    wolfram_replacement_rules: str = "{}"


_FUNCTIONS = {
    "Abs": "abs",
    "Cos": "cos",
    "Exp": "exp",
    "Log": "log",
    "Max": "max",
    "Min": "min",
    "Power": "pow",
    "Sin": "sin",
    "Sqrt": "sqrt",
    "Tan": "tan",
}

_CONSTANTS = {
    "Pi": "pi",
    "E": "2.7182818284590452353602874713526625",
    "True": "True",
    "False": "False",
}

_ESCAPE_NAMES = {
    "Alpha": "alpha",
    "Beta": "beta",
    "Gamma": "gamma",
    "Delta": "delta",
    "Epsilon": "epsilon",
    "CurlyEpsilon": "curlyEpsilon",
    "Zeta": "zeta",
    "Eta": "eta",
    "Theta": "theta",
    "CurlyTheta": "curlyTheta",
    "Iota": "iota",
    "Kappa": "kappa",
    "CurlyKappa": "curlyKappa",
    "Lambda": "lam",
    "Mu": "mu",
    "Nu": "nu",
    "Xi": "xi",
    "Omicron": "omicron",
    "Rho": "rho",
    "CurlyRho": "curlyRho",
    "Sigma": "sigma",
    "FinalSigma": "finalSigma",
    "Tau": "tau",
    "Upsilon": "upsilon",
    "Phi": "phi",
    "CurlyPhi": "curlyPhi",
    "Chi": "chi",
    "Psi": "psi",
    "Omega": "omega",
    "CurlyPi": "curlyPi",
    "CapitalAlpha": "capitalAlpha",
    "CapitalBeta": "capitalBeta",
    "CapitalGamma": "capitalGamma",
    "CapitalDelta": "capitalDelta",
    "CapitalEpsilon": "capitalEpsilon",
    "CapitalZeta": "capitalZeta",
    "CapitalEta": "capitalEta",
    "CapitalTheta": "capitalTheta",
    "CapitalIota": "capitalIota",
    "CapitalKappa": "capitalKappa",
    "CapitalLambda": "capitalLam",
    "CapitalMu": "capitalMu",
    "CapitalNu": "capitalNu",
    "CapitalXi": "capitalXi",
    "CapitalOmicron": "capitalOmicron",
    "CapitalPi": "capitalPi",
    "CapitalRho": "capitalRho",
    "CapitalSigma": "capitalSigma",
    "CapitalTau": "capitalTau",
    "CapitalUpsilon": "capitalUpsilon",
    "CapitalPhi": "capitalPhi",
    "CapitalChi": "capitalChi",
    "CapitalPsi": "capitalPsi",
    "CapitalOmega": "capitalOmega",
}

_UNICODE_GREEK_NAMES = {
    "\u03b1": "alpha",
    "\u03b2": "beta",
    "\u03b3": "gamma",
    "\u03b4": "delta",
    "\u03b5": "epsilon",
    "\u03f5": "curlyEpsilon",
    "\u03b6": "zeta",
    "\u03b7": "eta",
    "\u03b8": "theta",
    "\u03d1": "curlyTheta",
    "\u03b9": "iota",
    "\u03ba": "kappa",
    "\u03f0": "curlyKappa",
    "\u03bb": "lam",
    "\u03bc": "mu",
    "\u03bd": "nu",
    "\u03be": "xi",
    "\u03bf": "omicron",
    "\u03c1": "rho",
    "\u03f1": "curlyRho",
    "\u03c3": "sigma",
    "\u03c2": "finalSigma",
    "\u03c4": "tau",
    "\u03c5": "upsilon",
    "\u03d5": "phi",
    "\u03c6": "curlyPhi",
    "\u03c7": "chi",
    "\u03c8": "psi",
    "\u03c9": "omega",
    "\u03d6": "curlyPi",
    "\u0391": "capitalAlpha",
    "\u0392": "capitalBeta",
    "\u0393": "capitalGamma",
    "\u0394": "capitalDelta",
    "\u0395": "capitalEpsilon",
    "\u0396": "capitalZeta",
    "\u0397": "capitalEta",
    "\u0398": "capitalTheta",
    "\u0399": "capitalIota",
    "\u039a": "capitalKappa",
    "\u039b": "capitalLam",
    "\u039c": "capitalMu",
    "\u039d": "capitalNu",
    "\u039e": "capitalXi",
    "\u039f": "capitalOmicron",
    "\u03a0": "capitalPi",
    "\u03a1": "capitalRho",
    "\u03a3": "capitalSigma",
    "\u03a4": "capitalTau",
    "\u03a5": "capitalUpsilon",
    "\u03a6": "capitalPhi",
    "\u03a7": "capitalChi",
    "\u03a8": "capitalPsi",
    "\u03a9": "capitalOmega",
}

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PYTHON_IDENTIFIER_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")
_EXCLUDED_IDENTIFIERS = frozenset(_FUNCTIONS.values()) | {"pi", "True", "False"}
_PYTHON_RESERVED_IDENTIFIERS = {
    "False",
    "None",
    "True",
    "and",
    "as",
    "assert",
    "async",
    "await",
    "break",
    "class",
    "continue",
    "def",
    "del",
    "elif",
    "else",
    "except",
    "finally",
    "for",
    "from",
    "global",
    "if",
    "import",
    "in",
    "is",
    "lambda",
    "nonlocal",
    "not",
    "or",
    "pass",
    "raise",
    "return",
    "try",
    "while",
    "with",
    "yield",
}
_CPP_RESERVED_IDENTIFIERS = {
    "alignas",
    "alignof",
    "and",
    "and_eq",
    "asm",
    "atomic_cancel",
    "atomic_commit",
    "atomic_noexcept",
    "auto",
    "bitand",
    "bitor",
    "bool",
    "break",
    "case",
    "catch",
    "char",
    "char8_t",
    "char16_t",
    "char32_t",
    "class",
    "compl",
    "concept",
    "const",
    "consteval",
    "constexpr",
    "constinit",
    "const_cast",
    "continue",
    "co_await",
    "co_return",
    "co_yield",
    "decltype",
    "default",
    "delete",
    "do",
    "double",
    "dynamic_cast",
    "else",
    "enum",
    "explicit",
    "export",
    "extern",
    "false",
    "float",
    "for",
    "friend",
    "goto",
    "if",
    "inline",
    "int",
    "long",
    "mutable",
    "namespace",
    "new",
    "noexcept",
    "not",
    "not_eq",
    "nullptr",
    "operator",
    "or",
    "or_eq",
    "private",
    "protected",
    "public",
    "reflexpr",
    "register",
    "reinterpret_cast",
    "requires",
    "return",
    "short",
    "signed",
    "sizeof",
    "static",
    "static_assert",
    "static_cast",
    "struct",
    "switch",
    "synchronized",
    "template",
    "this",
    "thread_local",
    "throw",
    "true",
    "try",
    "typedef",
    "typeid",
    "typename",
    "union",
    "unsigned",
    "using",
    "virtual",
    "void",
    "volatile",
    "wchar_t",
    "while",
    "xor",
    "xor_eq",
}
RESERVED_COMPILER_IDENTIFIERS = frozenset(_PYTHON_RESERVED_IDENTIFIERS | _CPP_RESERVED_IDENTIFIERS)
_RESERVED_IDENTIFIER_REPAIRS = {identifier: f"{identifier}_" for identifier in RESERVED_COMPILER_IDENTIFIERS}
_RESERVED_IDENTIFIER_REPAIRS["lambda"] = "lam"


def convert_mathematica_inputform_expression(
    expression: str,
    *,
    field_map: Mapping[str, str] | None = None,
    symbol_overrides: Mapping[str, str] | None = None,
    treat_pi_as_variable: bool = False,
    repair_reserved_identifiers: bool = True,
) -> ConvertedMathematicaExpression:
    """Convert Mathematica InputForm text into the Python-like expression dialect.

    The conversion is syntax-level and model-independent. Physics-sensitive
    symbol choices, especially which Mathematica background field becomes which
    PhaseTracer coordinate, are supplied by ``field_map`` or
    ``symbol_overrides``.
    """

    overrides = dict(field_map or {})
    overrides.update(symbol_overrides or {})
    for raw, replacement in overrides.items():
        if not _IDENTIFIER_RE.fullmatch(replacement):
            raise ValueError(f"override for {raw!r} is not a valid identifier: {replacement!r}")

    source_format = classify_mathematica_expression(expression)
    converted_parts: list[str] = []
    symbol_map: dict[str, str] = {}
    index = 0
    while index < len(expression):
        char = expression[index]
        if char == '"':
            literal, index = _consume_string(expression, index)
            converted_parts.append(literal)
            continue
        if _starts_symbol(expression, index):
            raw, index = _consume_symbol(expression, index)
            replacement = _convert_symbol(
                raw,
                overrides,
                symbol_map,
                treat_pi_as_variable=treat_pi_as_variable,
                repair_reserved_identifiers=repair_reserved_identifiers,
            )
            converted_parts.append(replacement)
            continue
        converted_parts.append(char)
        index += 1

    converted = "".join(converted_parts)
    converted = _convert_mathematica_numbers(converted)
    converted = converted.replace("^", "**")
    converted = converted.replace("[", "(").replace("]", ")")
    converted = _flatten_simple_indexed_symbols(converted)

    identifiers = tuple(
        sorted({match.group(0) for match in _PYTHON_IDENTIFIER_RE.finditer(converted) if match.group(0) not in _EXCLUDED_IDENTIFIERS})
    )
    cform_symbol_map = _build_cform_symbol_map(symbol_map)
    cform_post_map = {temporary: compiler for raw, temporary in cform_symbol_map.items() for compiler in (symbol_map[raw],)}
    return ConvertedMathematicaExpression(
        expression=converted,
        symbol_map=symbol_map,
        identifiers=identifiers,
        source_format=source_format,
        cform_symbol_map=cform_symbol_map,
        cform_post_map=cform_post_map,
        wolfram_replacement_rules=_wolfram_replacement_rules(cform_symbol_map),
    )


def classify_mathematica_expression(expression: str) -> str:
    if (
        "\\[" in expression
        or any(char in _UNICODE_GREEK_NAMES or char == "\u03c0" for char in expression)
        or re.search(r"\b(?:Abs|Cos|Exp|Log|Max|Min|Power|Sin|Sqrt|Tan)\s*\[", expression)
    ):
        return "mathematica_inputform"
    if "*^" in expression or re.search(r"\b(?:Abs|Cos|Exp|Log|Max|Min|Power|Sin|Sqrt|Tan)\s*\(", expression):
        return "mathematica_cform"
    return "python_like"


def postprocess_cform_expression(
    expression: str,
    *,
    cform_post_map: Mapping[str, str],
) -> ConvertedMathematicaExpression:
    """Convert CForm text emitted after temporary-symbol replacement.

    Mathematica CForm should run on symbols that contain only letters and
    digits. This function first normalizes CForm syntax such as ``Power(x,2)``
    and then replaces the temporary Mathematica-safe symbols by compiler
    identifiers such as ``lam1H``.
    """

    converted = convert_mathematica_inputform_expression(expression)
    compiler_expression = converted.expression
    for temporary, compiler in sorted(cform_post_map.items(), key=lambda item: len(item[0]), reverse=True):
        if not _IDENTIFIER_RE.fullmatch(temporary):
            raise ValueError(f"CForm temporary symbol is not a plain Mathematica identifier: {temporary!r}")
        if not _IDENTIFIER_RE.fullmatch(compiler):
            raise ValueError(f"compiler symbol is not a valid identifier: {compiler!r}")
        compiler = repair_reserved_identifier(compiler)
        compiler_expression = re.sub(rf"\b{re.escape(temporary)}\b", compiler, compiler_expression)
    identifiers = tuple(
        sorted({match.group(0) for match in _PYTHON_IDENTIFIER_RE.finditer(compiler_expression) if match.group(0) not in _EXCLUDED_IDENTIFIERS})
    )
    return ConvertedMathematicaExpression(
        expression=compiler_expression,
        symbol_map={temporary: repair_reserved_identifier(compiler) for temporary, compiler in cform_post_map.items()},
        identifiers=identifiers,
        source_format="mathematica_cform",
    )


def looks_like_mathematica_inputform(expression: str) -> bool:
    return classify_mathematica_expression(expression) != "python_like"


def _convert_symbol(
    raw: str,
    overrides: Mapping[str, str],
    symbol_map: dict[str, str],
    *,
    treat_pi_as_variable: bool,
    repair_reserved_identifiers: bool,
) -> str:
    if raw in overrides:
        replacement = overrides[raw]
        symbol_map[raw] = replacement
        return replacement
    if raw in _FUNCTIONS:
        return _FUNCTIONS[raw]
    if raw in {"Pi", r"\[Pi]", "\u03c0"} and not treat_pi_as_variable:
        return "pi"
    if raw in {r"\[Pi]", "\u03c0"} and treat_pi_as_variable:
        symbol_map[raw] = "pi"
        return "pi"
    if raw in _CONSTANTS:
        return _CONSTANTS[raw]
    replacement = _sanitize_symbol(raw)
    if repair_reserved_identifiers:
        replacement = repair_reserved_identifier(replacement)
    symbol_map[raw] = replacement
    return replacement


def _flatten_simple_indexed_symbols(expression: str) -> str:
    """Render simple Mathematica indexed symbols as scalar identifiers.

    DRalgo frequently prints tensors such as ``lambdaVL[1]``. After the syntax
    pass brackets become ``lambdaVL(1)``, which would otherwise be parsed as a
    function call by the C++ renderer. For numeric tensor slots, use ordinary
    scalar identifiers like ``lambdaVL_1``.
    """

    math_functions = set(_FUNCTIONS.values()) | {"abs", "cos", "exp", "log", "max", "min", "pow", "sin", "sqrt", "tan"}
    pattern = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\((\d+)\)")
    previous = None
    result = expression
    while previous != result:
        previous = result

        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            if name in math_functions:
                return match.group(0)
            return f"{name}_{match.group(2)}"

        result = pattern.sub(replace, result)
    return result


def repair_reserved_identifier(identifier: str) -> str:
    """Return a compiler-safe spelling for reserved generated identifiers."""

    if identifier == "lambda":
        return "lam"
    if identifier.startswith("lambda") and len(identifier) > len("lambda"):
        suffix = identifier[len("lambda") :]
        if suffix[:1].isdigit() or suffix[:1].isupper() or suffix.startswith("_"):
            return "lam" + suffix
    return _RESERVED_IDENTIFIER_REPAIRS.get(identifier, identifier)


def is_reserved_identifier(identifier: str) -> bool:
    """Return true when an identifier collides with Python or C++ syntax."""

    return identifier in RESERVED_COMPILER_IDENTIFIERS


def suggest_compiler_symbol(
    raw_symbol: str,
    *,
    field_map: Mapping[str, str] | None = None,
    symbol_overrides: Mapping[str, str] | None = None,
    treat_pi_as_variable: bool = False,
) -> str:
    """Suggest a compiler identifier for one raw Mathematica/DRalgo symbol."""

    converted = convert_mathematica_inputform_expression(
        raw_symbol,
        field_map=field_map,
        symbol_overrides=symbol_overrides,
        treat_pi_as_variable=treat_pi_as_variable,
    )
    return converted.expression


def _build_cform_symbol_map(symbol_map: Mapping[str, str]) -> dict[str, str]:
    used: set[str] = set()
    result: dict[str, str] = {}
    for raw, compiler in symbol_map.items():
        temporary = _mathematica_safe_temporary(compiler)
        base = temporary
        suffix = 2
        while temporary in used:
            temporary = f"{base}{suffix}"
            suffix += 1
        used.add(temporary)
        result[raw] = temporary
    return result


def _mathematica_safe_temporary(compiler_symbol: str) -> str:
    compact = re.sub(r"[^A-Za-z0-9]", "", compiler_symbol)
    if not compact:
        compact = "Symbol"
    return "pt" + compact[0].upper() + compact[1:]


def _wolfram_replacement_rules(cform_symbol_map: Mapping[str, str]) -> str:
    if not cform_symbol_map:
        return "{}"
    rules = [f"HoldPattern[{raw}] :> {temporary}" for raw, temporary in cform_symbol_map.items()]
    return "{" + ", ".join(rules) + "}"


def _starts_symbol(text: str, index: int) -> bool:
    char = text[index]
    return char.isalpha() or char == "$" or text.startswith("\\[", index)


def _consume_symbol(text: str, index: int) -> tuple[str, int]:
    start = index
    while index < len(text):
        if text.startswith("\\[", index):
            end = text.find("]", index + 2)
            if end == -1:
                raise ValueError(f"unterminated Mathematica escaped symbol at offset {index}")
            index = end + 1
            continue
        char = text[index]
        if char.isalnum() or char == "$":
            index += 1
            continue
        break
    return text[start:index], index


def _consume_string(text: str, index: int) -> tuple[str, int]:
    start = index
    index += 1
    escaped = False
    while index < len(text):
        char = text[index]
        index += 1
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            break
    return text[start:index], index


def _sanitize_symbol(raw: str) -> str:
    chunks = _symbol_chunks(raw)
    if not chunks:
        return "_"
    name = chunks[0][1]
    for kind, part in chunks[1:]:
        name += _join_symbol_part(kind, part)
    name = re.sub(r"[^A-Za-z0-9_]", "_", name)
    name = re.sub(r"_+", "_", name).strip("_")
    if not name:
        name = "_"
    if name[0].isdigit():
        name = "_" + name
    return name


def _symbol_chunks(raw: str) -> list[tuple[str, str]]:
    chunks: list[tuple[str, str]] = []
    index = 0
    while index < len(raw):
        if raw.startswith("\\[", index):
            end = raw.find("]", index + 2)
            if end == -1:
                raise ValueError(f"unterminated Mathematica escaped symbol in {raw!r}")
            name = raw[index + 2 : end]
            chunks.append(("escape", _escape_to_identifier(name)))
            index = end + 1
            continue
        if raw[index] in _UNICODE_GREEK_NAMES:
            chunks.append(("escape", _UNICODE_GREEK_NAMES[raw[index]]))
            index += 1
            continue
        start = index
        while index < len(raw) and not raw.startswith("\\[", index) and raw[index] not in _UNICODE_GREEK_NAMES:
            index += 1
        ascii_part = raw[start:index].replace("$", "_")
        if ascii_part:
            chunks.append(("ascii", ascii_part))
    return chunks


def _escape_to_identifier(name: str) -> str:
    if name in _ESCAPE_NAMES:
        return _ESCAPE_NAMES[name]
    if name.startswith("Capital") and len(name) > len("Capital"):
        tail = name[len("Capital") :]
        return "capital" + tail[:1].upper() + tail[1:]
    if name:
        return name[0].lower() + name[1:]
    return "_"


def _join_symbol_part(kind: str, part: str) -> str:
    if kind != "escape" or not part:
        return part
    return part[:1].upper() + part[1:]


def _convert_mathematica_numbers(expression: str) -> str:
    return re.sub(
        r"(?P<mantissa>(?:\d+(?:\.\d*)?|\.\d+)(?:`[0-9.]+)?)\*\^(?P<exponent>[+-]?\d+)",
        lambda match: match.group("mantissa").split("`", 1)[0] + "e" + match.group("exponent"),
        expression,
    )
