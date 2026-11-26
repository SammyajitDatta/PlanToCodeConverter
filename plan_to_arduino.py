"""Translate a JSON or indented text plan using a trusted device catalog (Python 3.10+)."""
import argparse
import ast
import math
import json
import re
try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib
from pathlib import Path


# Both front ends feed the same typed expression compiler.
CMP = {ast.Gt: ">", ast.GtE: ">=", ast.Lt: "<", ast.LtE: "<=",
       ast.Eq: "==", ast.NotEq: "!="}
MATH = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/"}
CPP = {"int32": "int32_t", "float": "float", "bool": "bool",
       "timestamp": "uint32_t", "duration": "uint32_t"}
NUMBERS = {"int32", "float"}


def expression_from_text(source, line):
    keywords = {"AND": "and", "OR": "or", "NOT": "not",
                "true": "True", "false": "False"}
    source = re.sub(r"\b(?:AND|OR|NOT|true|false)\b",
                    lambda match: keywords[match[0]], source.strip())

    def convert(node):
        if isinstance(node, ast.Constant) and type(node.value) in (bool, int, float):
            return node.value
        if isinstance(node, ast.Name):
            return {"var": node.id}
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
            return {"function": node.func.id, "args": [convert(arg) for arg in node.args]}
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) in CMP:
            return {"op": CMP[type(node.ops[0])], "args": [convert(node.left), convert(node.comparators[0])]}
        if isinstance(node, ast.BoolOp):
            return {"op": "and" if isinstance(node.op, ast.And) else "or",
                    "args": [convert(value) for value in node.values]}
        if isinstance(node, ast.BinOp) and type(node.op) in MATH:
            return {"op": MATH[type(node.op)], "args": [convert(node.left), convert(node.right)]}
        if isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.Not):
                return {"op": "not", "args": [convert(node.operand)]}
            if isinstance(node.op, ast.USub):
                val = convert(node.operand)
                if type(val) in (int, float):
                    return -val
                return {"op": "-", "args": [0, val]}
        raise ValueError("unsupported expression")

    try:
        return convert(ast.parse(source, mode="eval").body)
    except (SyntaxError, ValueError) as error:
        raise ValueError(f"line {line}: invalid expression {source!r}: {error}") from error


def parse_text(source):
    rows = []
    for number, raw in enumerate(source.splitlines(), 1):
        if raw.strip():
            expanded = raw.expandtabs(4).rstrip()
            rows.append((len(expanded) - len(expanded.lstrip()), expanded.strip(), number))
    pos = 0
    state = []
    while pos < len(rows) and rows[pos][0] == 0 and rows[pos][1].startswith("STATE "):
        _, statement, number = rows[pos]
        match = re.fullmatch(r"STATE ([A-Za-z][A-Za-z0-9_]*):\s*(\w+)\s*=\s*(.+)", statement)
        if not match:
            raise ValueError(f"line {number}: expected STATE name: type = literal")
        state.append({"name": match[1], "type": match[2],
                      "value": expression_from_text(match[3], number)})
        pos += 1
    if pos == len(rows) or rows[pos][:2] != (0, "LOOP:"):
        raise ValueError("expected LOOP: after optional STATE declarations")
    pos += 1

    def block(parent_indent):
        nonlocal pos
        if pos == len(rows) or rows[pos][0] <= parent_indent:
            raise ValueError(f"expected indented body after line {rows[pos-1][2]}")
        indent = rows[pos][0]
        nodes = []
        while pos < len(rows) and rows[pos][0] >= indent:
            level, statement, number = rows[pos]
            if level != indent:
                raise ValueError(f"line {number}: inconsistent indentation")
            pos += 1
            if statement.startswith("IF ") and statement.endswith(":"):
                condition = expression_from_text(statement[3:-1].strip(), number)
                node = {"type": "if", "condition": condition, "then": block(indent)}
                if pos < len(rows) and rows[pos][:2] == (indent, "ELSE:"):
                    pos += 1
                    node["else"] = block(indent)
            elif statement.startswith("CALL "):
                result = expression_from_text(statement[5:], number)
                if not isinstance(result, dict) or "function" not in result:
                    raise ValueError(f"line {number}: CALL needs a device function")
                node = {"type": "call", **result}
            elif match := re.fullmatch(r"(LET|SET) ([A-Za-z][A-Za-z0-9_]*)\s*=\s*(.+)", statement):
                node = {"type": match[1].lower(), "name": match[2],
                        "value": expression_from_text(match[3], number)}
            elif match := re.fullmatch(r"WAIT\s+(\d+)\s*ms", statement):
                node = {"type": "wait", "duration_ms": int(match[1])}
            else:
                raise ValueError(f"line {number}: unknown statement {statement!r}")
            nodes.append(node)
        return nodes

    body = block(0)
    if pos != len(rows):
        raise ValueError(f"line {rows[pos][2]}: unexpected dedent")
    plan = {"type": "loop", "body": body}
    if state:
        plan["state"] = state
    return plan


def fields(node, required, optional=()):
    if not isinstance(node, dict) or not set(required) <= node.keys() <= set(required) | set(optional):
        raise ValueError(f"expected fields {required}, optional {optional}, received {node!r}")


def compile_plan(plan, device):
    """Return sketch text. Only the trusted catalog supplies C++ definitions."""
    functions = device["functions"]
    used = {}
    state_types = {}
    needs_int_bounds = False

    def validate_name(name, scope):
        if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name)
                or name in scope or name in {"AND", "OR", "NOT", "and", "or", "not", "True", "False", "true", "false"}):
            raise ValueError(f"invalid or duplicate variable name {name!r}")

    def require(actual, expected):
        if actual != expected and (actual, expected) != ("int32", "float"):
            raise ValueError(f"expected {expected}, received {actual}")

    def expr(node, scope, integer_update=False):
        nonlocal needs_int_bounds
        if type(node) is bool:
            return ("true" if node else "false"), "bool"
        if type(node) is int and -(2**31) <= node < 2**31:
            return f"int32_t({node})", "int32"
        if type(node) is float and math.isfinite(node) and abs(node) <= 3.4028234663852886e38:
            return f"{node!r}f", "float"
        if not isinstance(node, dict):
            raise ValueError(f"invalid expression literal {node!r}")
        if "var" in node:
            fields(node, ("var",))
            name = node["var"]
            if not isinstance(name, str) or name not in scope:
                raise ValueError(f"undefined variable {name!r}")
            return f"plan_{name}", scope[name]
        if "function" in node:
            fields(node, ("function", "args"))
            name, args = node["function"], node["args"]
            if name == "NOW_MS":
                if args != []:
                    raise ValueError("NOW_MS takes no arguments")
                return "uint32_t(millis())", "timestamp"
            if name == "ELAPSED_MS":
                if not isinstance(args, list) or len(args) != 1:
                    raise ValueError("ELAPSED_MS expects one timestamp")
                start, actual = expr(args[0], scope)
                require(actual, "timestamp")
                return f"uint32_t(uint32_t(millis()) - {start})", "duration"
            if not isinstance(name, str) or name not in functions:
                raise ValueError(f"unknown device function {name!r}")
            spec = functions[name]
            types = spec.get("args", [])
            if not isinstance(args, list) or len(args) != len(types):
                raise ValueError(f"{name} expects {len(types)} arguments")
            values = []
            for arg, expected in zip(args, types):
                value, actual = expr(arg, scope)
                require(actual, expected)
                values.append(value)
            used[name] = spec
            return f"{name}({', '.join(values)})", spec["returns"]

        fields(node, ("op", "args"))
        op, args = node["op"], node["args"]
        if not isinstance(args, list):
            raise ValueError("operator args must be a list")
        operands = [expr(arg, scope, integer_update) for arg in args]
        if op in ("and", "or", "not"):
            if len(args) < (1 if op == "not" else 2) or (op == "not" and len(args) != 1):
                raise ValueError(f"invalid operand count for {op}")
            for _, kind in operands:
                require(kind, "bool")
            if op == "not":
                return f"(!{operands[0][0]})", "bool"
            joiner = " && " if op == "and" else " || "
            return "(" + joiner.join(value for value, _ in operands) + ")", "bool"
        if len(operands) != 2:
            raise ValueError(f"{op} expects two operands")
        (left, a), (right, b) = operands
        if op in ("==", "!=") and a == b == "bool":
            return f"({left} {op} {right})", "bool"
        if op in CMP.values() and "duration" in (a, b) and {a, b} <= {"duration", "int32"}:
            return f"(int64_t({left}) {op} int64_t({right}))", "bool"
        if a not in NUMBERS or b not in NUMBERS:
            raise ValueError(f"{op} expects numeric operands")
        if op in CMP.values():
            return f"({left} {op} {right})", "bool"
        if integer_update and a == b == "int32" and op in ("+", "-"):
            # Use a wide intermediate and saturate at the int32 bounds.
            needs_int_bounds = True
            return f"clampPlanCounter(int64_t({left}) {op} int64_t({right}))", "int32"
        if op in MATH.values():
            return f"(float({left}) {op} float({right}))", "float"
        raise ValueError(f"unknown operator {op!r}")

    def emit(nodes, level, outer_scope):
        scope = outer_scope.copy()
        if not isinstance(nodes, list):
            raise ValueError("a block must be an array of statements")
        pad = "  " * level
        lines = []
        for node in nodes:
            kind = node.get("type") if isinstance(node, dict) else None
            if kind == "call":
                fields(node, ("type", "function", "args"))
                value, _ = expr({"function": node["function"], "args": node["args"]}, scope)
                lines.append(pad + value + ";")
            elif kind == "let":
                fields(node, ("type", "name", "value"))
                name = node["name"]
                validate_name(name, scope)
                value, actual = expr(node["value"], scope)
                if actual not in CPP:
                    raise ValueError("LET requires a returned value")
                scope[name] = actual
                lines.append(f"{pad}const {CPP[actual]} plan_{name} = {value};")
            elif kind == "set":
                fields(node, ("type", "name", "value"))
                name = node["name"]
                if not isinstance(name, str) or name not in state_types:
                    raise ValueError(f"SET requires a declared STATE variable: {name!r}")
                value, actual = expr(node["value"], scope, integer_update=state_types[name] == "int32")
                require(actual, state_types[name])
                lines.append(f"{pad}plan_{name} = {value};")
            elif kind == "wait":
                fields(node, ("type", "duration_ms"))
                ms = node["duration_ms"]
                if type(ms) is not int or not 0 <= ms < 2**32:
                    raise ValueError("duration_ms must be an unsigned 32-bit integer")
                lines.append(f"{pad}delay({ms}UL);")
            elif kind == "if":
                fields(node, ("type", "condition", "then"), ("else",))
                condition, actual = expr(node["condition"], scope)
                require(actual, "bool")
                lines.append(pad + "if (" + condition + ") {")
                lines.extend(emit(node["then"], level + 1, scope))
                if "else" in node:
                    lines.append(pad + "} else {")
                    lines.extend(emit(node["else"], level + 1, scope))
                lines.append(pad + "}")
            else:
                raise ValueError(f"unsupported statement: {node!r}")
        return lines

    fields(plan, ("type", "body"), ("state",))
    if plan["type"] != "loop":
        raise ValueError("plan must have a loop root")
    state_declarations = []
    state = plan.get("state", [])
    if not isinstance(state, list):
        raise ValueError("state must be a list of declarations")
    for entry in state:
        fields(entry, ("name", "type", "value"))
        name, kind, initial = entry["name"], entry["type"], entry["value"]
        validate_name(name, state_types)
        if kind not in ("bool", "int32", "float", "timestamp", "duration"):
            raise ValueError(f"unsupported STATE type {kind!r}")
        if type(initial) not in (bool, float, int):
            raise ValueError("STATE requires a literal initializer")
        if kind in ("timestamp", "duration"):
            if type(initial) is not int or not 0 <= initial < 2**32:
                raise ValueError(f"{kind} initializer must be an unsigned 32-bit integer")
            literal = f"{initial}UL"
        else:
            literal, actual = expr(initial, {})
            require(actual, kind)
        state_types[name] = kind
        state_declarations.append(f"{CPP[kind]} plan_{name} = {literal};")
    loop = emit(plan["body"], 1, state_types)
    pins = dict.fromkeys(pin for spec in used.values() for pin in spec.get("pins", []))
    declarations, setup = [], []
    for name in pins:
        pin = device["pins"][name]
        declarations.append(f"constexpr int {name} = {pin['gpio']};")
        setup.append(f"  pinMode({name}, {pin['mode']});")
        if "initial" in pin:
            setup.append(f"  digitalWrite({name}, {pin['initial']});")
    modules = [device["modules"][key] for key in dict.fromkeys(
        key for spec in used.values() for key in spec.get("modules", []))]
    headers = dict.fromkeys(header for mod in modules for header in mod.get("includes", []))
    sections = ["\n".join(["#include <Arduino.h>", *[f"#include <{h}>" for h in headers]]),
                "\n".join(declarations), "\n".join(state_declarations)]
    for mod in modules:
        sections.append(mod.get("declarations", ""))
        setup.extend("  " + line for line in mod.get("setup", []))
    if needs_int_bounds:
        sections.append("static int32_t clampPlanCounter(int64_t value) {\n"
                        "  if (value > INT32_MAX) return INT32_MAX;\n"
                        "  if (value < INT32_MIN) return INT32_MIN;\n"
                        "  return int32_t(value);\n}")
    sections.extend(spec["definition"].strip() for spec in used.values())
    sections.append("\n".join(["void setup() {", *setup, "}"]))
    sections.append("\n".join(["void loop() {", *loop, "}"]))
    return "\n\n".join(filter(None, sections)) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parent
    output = root / "executablescript" / "executablescript.ino"
    parser.add_argument("plan", type=Path)
    parser.add_argument("device", type=Path, nargs="?", default=root / "device.toml")
    args = parser.parse_args()
    try:
        source = args.plan.read_text(encoding="utf-8-sig").strip("\r\n")
        plan = json.loads(source) if source.lstrip().startswith("{") else parse_text(source)
        device = tomllib.loads(args.device.read_text(encoding="utf-8"))
        sketch = compile_plan(plan, device)
        if output.resolve() in (args.plan.resolve(), args.device.resolve()):
            raise ValueError("output must differ from both inputs")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(sketch, encoding="utf-8")
        print(f"Generated {output}")
    except (OSError, ValueError, KeyError, TypeError, RecursionError) as error:
        parser.exit(1, f"Conversion failed: {error}\n")


if __name__ == "__main__":
    main()
