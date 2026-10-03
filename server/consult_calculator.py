"""M03 §5.3: bounded AST arithmetic, without executing Python code."""
from __future__ import annotations

import ast
import math
import operator
from datetime import datetime


def parse_date(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("日期必须为文字")
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            result = datetime.strptime(value, fmt)
            if result.strftime(fmt) == value:
                return result
        except ValueError:
            pass
    raise ValueError("日期格式应为 YYYY-MM-DD 或带小时、分钟、秒的日期")


def evaluate_expression(expression: str) -> float:
    if not isinstance(expression, str) or len(expression) > 2000:
        raise ValueError("算式为空或过长")
    try:
        tree = ast.parse(expression, mode="eval")
    except (SyntaxError, RecursionError) as exc:
        raise ValueError("无法解析算式") from exc
    if sum(1 for _ in ast.walk(tree)) > 200:
        raise ValueError("算式过于复杂")
    operators = {ast.Add: operator.add, ast.Sub: operator.sub,
                 ast.Mult: operator.mul, ast.Div: operator.truediv}
    durations = {"minutes_between": 60, "hours_between": 3600, "days_between": 86400}

    def visit(node):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            value = float(node.value)
        elif isinstance(node, ast.UnaryOp) and type(node.op) in (ast.UAdd, ast.USub):
            value = visit(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1)
        elif isinstance(node, ast.BinOp) and type(node.op) in operators:
            value = operators[type(node.op)](visit(node.left), visit(node.right))
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
              and node.func.id in durations and len(node.args) == 2 and not node.keywords):
            if not all(isinstance(arg, ast.Constant) and isinstance(arg.value, str) for arg in node.args):
                raise ValueError("日期函数只接受两个日期文字")
            value = (parse_date(node.args[1].value) - parse_date(node.args[0].value)).total_seconds() / durations[node.func.id]
        else:
            raise ValueError("算式含未允许内容")
        if not math.isfinite(value) or abs(value) > 1e100:
            raise ValueError("计算结果超出允许范围")
        return value

    try:
        return visit(tree.body)
    except (ArithmeticError, OverflowError, RecursionError) as exc:
        raise ValueError("算式无法计算") from exc


def recompute_calculations(calculations: list, outputs: dict) -> tuple[list, dict]:
    """Return audit rows and corrected outputs. Arithmetic does not validate business scope."""
    corrected = dict(outputs)
    rows = []
    for calculation in calculations:
        row = dict(calculation)
        row.update(program_result=None, consistent=None, status="未经复算", note="复算只核对算术，不验证代入数值的口径")
        try:
            result = evaluate_expression(row.get("expression"))
            row["program_result"] = result
            original = row.get("model_result")
            consistent = (type(original) in (int, float) and math.isfinite(original)
                          and abs(result - original) <= max(0.01, abs(result) * 0.005))
            row["consistent"] = consistent
            row["status"] = "一致" if consistent else "已按程序复算更正"
            # Only declared, already valid outputs may be overwritten.
            key = row.get("output_key")
            if key in corrected and not consistent:
                corrected[key] = result
        except (ValueError, TypeError) as exc:
            row["error"] = str(exc)
        rows.append(row)
    return rows, corrected
