"""موتور KPI: شاخص‌ها در جدول تعریف می‌شوند و فرمول با ارزیاب امن (AST) محاسبه می‌شود،
نه eval آزاد؛ بنابراین افزودن KPI جدید نیاز به تغییر کد ندارد."""
from __future__ import annotations

import ast
import operator as op

_OPS = {ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul, ast.Div: op.truediv,
        ast.Pow: op.pow, ast.USub: op.neg, ast.UAdd: op.pos}
_FUNCS = {"max": max, "min": min, "abs": abs}


class FormulaError(ValueError):
    pass


def safe_eval(expr: str, variables: dict[str, float]) -> float:
    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.Name):
            if n.id not in variables:
                raise FormulaError(f"متغیر «{n.id}» تعریف نشده است")
            return variables[n.id]
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.operand))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _FUNCS:
            return _FUNCS[n.func.id](*[ev(a) for a in n.args])
        raise FormulaError("عبارت غیرمجاز در فرمول")
    try:
        tree = ast.parse(expr, mode="eval")
        return float(ev(tree))
    except ZeroDivisionError:
        raise FormulaError("تقسیم بر صفر")
    except SyntaxError:
        raise FormulaError("خطای نحوی در فرمول")


DEFAULT_KPIS = [
    # code, name, formula, unit, group, warn, critical (حد بالا؛ None = بدون حد)
    ("SWC", "مصرف ویژه آب (مصرف/تولید)", "consumed / production", "m3/t", "consumption", None, None),
    ("FWI", "شدت برداشت آب تازه", "freshwater / production", "m3/t", "consumption", None, None),
    ("WWI", "شدت تولید پساب", "wastewater / production", "m3/t", "consumption", None, None),
    ("REUSE", "نرخ بازچرخانی", "reused / total_use * 100", "%", "efficiency", None, None),
    ("EFF", "راندمان مصرف آب", "beneficial / input * 100", "%", "efficiency", None, None),
    ("IMBAL", "درصد عدم تطابق موازنه", "abs(input - accounted) / input * 100", "%", "loss", 10, 20),
    ("LEAK", "نرخ نشتی", "leakage / input * 100", "%", "loss", 5, 10),
    ("EVAP", "نرخ تبخیر", "evaporation / input * 100", "%", "loss", None, None),
    ("COST_M3", "هزینه واقعی هر m3 آب", "supply + pumping + treatment + distribution + disposal", "واحد پول/m³", "economic", None, None),
    ("COST_T", "هزینه آب به ازای هر تن", "(consumed * (supply + pumping + treatment + distribution + disposal)) / production", "واحد پول/t", "economic", None, None),
]


def compute_kpi(formula: str, variables: dict[str, float]) -> float:
    return safe_eval(formula, variables)


def evaluate_level(value: float | None, warn: float | None, critical: float | None) -> str:
    if value is None:
        return "نامشخص"
    if critical is not None and value >= critical:
        return "بحرانی"
    if warn is not None and value >= warn:
        return "هشدار"
    return "عادی"
