import ast
import math
import operator
from collections.abc import Callable

from pydantic import BaseModel, Field

from app.tools.registry import ToolContext, ToolResult

Number = int | float
BinaryOperator = Callable[[Number, Number], Number]
UnaryOperator = Callable[[Number], Number]
MAX_ABSOLUTE_RESULT = 1_000_000_000_000
MAX_POWER_EXPONENT = 100

BINARY_OPERATORS: dict[type[ast.operator], BinaryOperator] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
UNARY_OPERATORS: dict[type[ast.unaryop], UnaryOperator] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


class CalculatorInput(BaseModel):
    expression: str = Field(min_length=1)


class CalculatorTool:
    name = "calculator"
    description = "Evaluate a basic arithmetic expression."
    input_model = CalculatorInput

    async def run(
        self,
        input_data: BaseModel,
        context: ToolContext,
    ) -> ToolResult:
        del context
        calculator_input = CalculatorInput.model_validate(input_data)
        try:
            parsed = ast.parse(calculator_input.expression, mode="eval")
            result = self._evaluate(parsed.body)
        except (SyntaxError, ValueError, ZeroDivisionError, OverflowError) as exc:
            return ToolResult(
                ok=False,
                content=f"Calculation failed: {exc}",
                error="calculation_failed",
            )

        return ToolResult(
            ok=True,
            content=str(result),
            data={"result": result},
        )

    def _evaluate(self, node: ast.AST) -> Number:
        if isinstance(node, ast.Constant):
            return self._evaluate_constant(node)

        if isinstance(node, ast.BinOp):
            return self._evaluate_binary_operation(node)

        if isinstance(node, ast.UnaryOp):
            return self._evaluate_unary_operation(node)

        raise ValueError(f"unsupported expression: {node.__class__.__name__}")

    def _evaluate_constant(self, node: ast.Constant) -> Number:
        value = node.value
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError("only numeric constants are supported")
        return self._validate_number(value)

    def _evaluate_binary_operation(self, node: ast.BinOp) -> Number:
        operator_fn = BINARY_OPERATORS.get(type(node.op))
        if operator_fn is None:
            raise ValueError(f"unsupported operator: {node.op.__class__.__name__}")
        left = self._evaluate(node.left)
        right = self._evaluate(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > MAX_POWER_EXPONENT:
            raise ValueError("power exponent is too large")
        return self._validate_number(operator_fn(left, right))

    def _evaluate_unary_operation(self, node: ast.UnaryOp) -> Number:
        operator_fn = UNARY_OPERATORS.get(type(node.op))
        if operator_fn is None:
            raise ValueError(f"unsupported operator: {node.op.__class__.__name__}")
        return self._validate_number(operator_fn(self._evaluate(node.operand)))

    def _validate_number(self, value: Number) -> Number:
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("result must be finite")
        if abs(value) > MAX_ABSOLUTE_RESULT:
            raise ValueError("result is too large")
        return value
