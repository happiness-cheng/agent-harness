"""有明确语法、十进制精度和资源边界的纯数值计算工具。"""

import ast
import re
from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DecimalException,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    Subnormal,
    Underflow,
)

from pydantic import BaseModel, ConfigDict, Field

from min_agent.tools.registry import ToolDefinition

MAX_EXPRESSION_LENGTH = 512
MAX_AST_NODES = 128

_BINARY_OPERATIONS = {
    ast.Add: Context.add,
    ast.Sub: Context.subtract,
    ast.Mult: Context.multiply,
    ast.Div: Context.divide,
}
_UNARY_OPERATIONS = {ast.UAdd: Context.plus, ast.USub: Context.minus}


class CalculatorArguments(BaseModel):
    """公开参数约束，也用于生成模型可见的 JSON Schema。"""

    model_config = ConfigDict(strict=True, extra="forbid")

    expression: str = Field(
        min_length=1,
        max_length=MAX_EXPRESSION_LENGTH,
        pattern=r"\S",
        description="仅包含普通十进制数字、四则运算、括号及一元正负号的表达式。",
    )


def _evaluate(node: ast.AST, source: str, context: Context) -> Decimal:
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        literal = ast.get_source_segment(source, node)
        if literal is None:
            raise ValueError("无法读取数字字面量")
        return context.create_decimal(literal)

    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATIONS:
        left = _evaluate(node.left, source, context)
        right = _evaluate(node.right, source, context)
        if isinstance(node.op, ast.Div) and right.is_zero():
            raise ValueError("除数不能为零")
        return _BINARY_OPERATIONS[type(node.op)](context, left, right)

    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPERATIONS:
        value = _evaluate(node.operand, source, context)
        return _UNARY_OPERATIONS[type(node.op)](context, value)

    raise ValueError("仅支持四则运算、括号和一元正负号")


def calculate(expression: str) -> str:
    """按每步 28 位有效数字计算，返回规范化的普通十进制文本。"""

    source = CalculatorArguments(expression=expression).expression.strip()
    if re.fullmatch(r"[0-9.()+*/\s-]+", source) is None:
        raise ValueError("表达式包含不支持的字符或数字格式")
    try:
        tree = ast.parse(source, mode="eval")
    except (SyntaxError, RecursionError):
        raise ValueError("表达式语法无效或嵌套过深") from None
    if sum(1 for _ in ast.walk(tree)) > MAX_AST_NODES:
        raise ValueError(f"表达式最多允许 {MAX_AST_NODES} 个语法树节点")

    context = Context(
        prec=28,
        rounding=ROUND_HALF_EVEN,
        Emin=-100,
        Emax=100,
        traps=[DivisionByZero, InvalidOperation, Overflow, Underflow, Subnormal],
    )
    try:
        result = _evaluate(tree.body, source, context)
    except DecimalException:
        raise ValueError("数值超出允许范围或计算无效") from None

    if result.is_zero():
        return "0"
    text = format(result, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


CALCULATOR_TOOL = ToolDefinition(
    name="calculator",
    description=(
        "需要确定性数值计算时使用。支持普通十进制数字、+ - * /、括号和一元正负号；"
        "不支持幂、函数、变量、科学计数法或代码执行。"
        "输入最多512字符、128个语法树节点；非零数值数量级指数为-100到100。"
        "数字读取和每步运算按28位有效数字、恰好一半取偶数舍入，返回十进制字符串；"
        "除零、语法或资源越界报错。示例：(1+2)*3 返回9，0.1+0.2 返回0.3。"
    ),
    parameters=CalculatorArguments,
    handler=calculate,
)
