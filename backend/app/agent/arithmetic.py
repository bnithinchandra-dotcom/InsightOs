"""
Safe deterministic arithmetic evaluator and intent classifier for InsightOS.

Evaluates standalone mathematical expressions using Python's AST module
with an explicit allowlist of safe nodes and operations.
No eval(), exec(), or arbitrary dynamic execution is used.
"""

from __future__ import annotations

import ast
import logging
import math
import re
from typing import Any

from app.agent.contracts import AgentOutcome, UsageMetadata

logger = logging.getLogger(__name__)

# Numerical and resource limits
MAX_EXPRESSION_LENGTH = 160
MAX_NODE_COUNT = 40
MAX_EVAL_DEPTH = 12
MAX_RESULT_MAGNITUDE = 10**30

# Dataset-related keywords that indicate a data-task, not standalone arithmetic
_DATA_EXCLUSION_TERMS = re.compile(
    r"\b(data|dataset|file|table|column|row|schema|profile|quality|missing|"
    r"duplicate|metric|measure|report|join|relationship|correlation|"
    r"forecast|prediction|predict|anomaly|preprocess|transformation|"
    r"statistic|average|mean|median|revenue|sales|profit|margin|cost|"
    r"expenses|churn|order|orders|customer|customers)\b",
    re.IGNORECASE,
)

# Words indicating conversational or explanatory requests, not calculation
_EXPLANATION_TERMS = re.compile(
    r"\b(explain|why|how|what does|teach|define|describe|history of)\b",
    re.IGNORECASE,
)

# Natural language prefixes wrapping a calculation request
_MATH_PREFIX_PATTERN = re.compile(
    r"^(?:what\s+is|calculate|compute|evaluate|solve)\s+",
    re.IGNORECASE,
)

# Characters allowed in candidate pure expressions
_ALLOWED_CHARS_PATTERN = re.compile(r"^[0-9\s\+\-\*\/\%\(\)\.]+=?$")


class ArithmeticSafetyError(ValueError):
    """Raised when an expression exceeds safety limits or contains forbidden AST nodes."""


def _normalize_leading_zeros(expr: str) -> str:
    """
    Normalize leading zeros in decimal integer literals without affecting decimals.

    In expressions like '301+90+07=', Python's ast.parse rejects '07' as an invalid
    octal literal. This transforms '07' to '7', '00' to '0', while preserving '0.5'.
    """
    return re.sub(r"(?<![\d.])0+([1-9]\d*|0)(?![\d.])", r"\1", expr)


def _safe_format_number(value: int | float) -> str:
    """Format a numeric result cleanly without unnecessary trailing decimals."""
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise ArithmeticSafetyError("Result is non-finite.")
        if value.is_integer() and abs(value) < MAX_RESULT_MAGNITUDE:
            return str(int(value))
        # Format floating-point numbers with reasonable precision
        return f"{value:.6g}"
    return str(value)


def _safe_format_expression(expr: str) -> str:
    """Format an arithmetic expression cleanly for user-facing display."""
    # Normalize spaces around operators
    formatted = expr.strip().rstrip("=").strip()
    formatted = _normalize_leading_zeros(formatted)
    # Insert clean spacing around binary operators while preserving readability
    formatted = re.sub(r"\s*([\+\-\*\/])\s*", r" \1 ", formatted)
    # Fix unary minus/plus at start or after open parenthesis
    formatted = re.sub(r"\( \+ ", "(+", formatted)
    formatted = re.sub(r"\( \- ", "(-", formatted)
    formatted = re.sub(r"^ \+ ", "+", formatted)
    formatted = re.sub(r"^ \- ", "-", formatted)
    # Collapse multiple spaces
    formatted = re.sub(r"\s+", " ", formatted).strip()
    return formatted


class _SafeEvaluator(ast.NodeVisitor):
    """AST visitor that evaluates only allowlisted arithmetic operations."""

    def __init__(self) -> None:
        self.node_count = 0
        self.depth = 0

    def visit(self, node: ast.AST) -> Any:
        self.node_count += 1
        if self.node_count > MAX_NODE_COUNT:
            raise ArithmeticSafetyError(f"Expression exceeds node limit of {MAX_NODE_COUNT}.")
        self.depth += 1
        if self.depth > MAX_EVAL_DEPTH:
            raise ArithmeticSafetyError(f"Expression exceeds depth limit of {MAX_EVAL_DEPTH}.")
        try:
            return super().visit(node)
        finally:
            self.depth -= 1

    def generic_visit(self, node: ast.AST) -> Any:
        raise ArithmeticSafetyError(f"Forbidden AST node: {type(node).__name__}")

    def visit_Expression(self, node: ast.Expression) -> Any:
        return self.visit(node.body)

    def visit_Constant(self, node: ast.Constant) -> int | float:
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ArithmeticSafetyError(f"Forbidden constant type: {type(node.value).__name__}")
        if abs(node.value) > MAX_RESULT_MAGNITUDE:
            raise ArithmeticSafetyError("Literal value exceeds magnitude limit.")
        return node.value

    def visit_UnaryOp(self, node: ast.UnaryOp) -> int | float:
        operand = self.visit(node.operand)
        if isinstance(node.op, ast.UAdd):
            return +operand
        if isinstance(node.op, ast.USub):
            return -operand
        raise ArithmeticSafetyError(f"Forbidden unary operator: {type(node.op).__name__}")

    def visit_BinOp(self, node: ast.BinOp) -> int | float:
        left = self.visit(node.left)
        right = self.visit(node.right)

        if isinstance(node.op, ast.Add):
            result = left + right
        elif isinstance(node.op, ast.Sub):
            result = left - right
        elif isinstance(node.op, ast.Mult):
            result = left * right
        elif isinstance(node.op, ast.Div):
            if right == 0:
                raise ArithmeticSafetyError("Division by zero.")
            result = left / right
        elif isinstance(node.op, ast.FloorDiv):
            if right == 0:
                raise ArithmeticSafetyError("Division by zero.")
            result = left // right
        elif isinstance(node.op, ast.Mod):
            if right == 0:
                raise ArithmeticSafetyError("Modulo by zero.")
            result = left % right
        else:
            raise ArithmeticSafetyError(f"Forbidden binary operator: {type(node.op).__name__}")

        if isinstance(result, float) and (math.isnan(result) or math.isinf(result)):
            raise ArithmeticSafetyError("Non-finite numerical result.")
        if abs(result) > MAX_RESULT_MAGNITUDE:
            raise ArithmeticSafetyError("Result exceeds maximum magnitude limit.")

        return result


def evaluate_expression(expr: str) -> int | float:
    """
    Safely evaluate a mathematical expression string using an AST allowlist.

    Raises ArithmeticSafetyError or ValueError on invalid/forbidden syntax.
    """
    if len(expr) > MAX_EXPRESSION_LENGTH:
        raise ArithmeticSafetyError(f"Expression length {len(expr)} exceeds limit {MAX_EXPRESSION_LENGTH}.")

    normalized = _normalize_leading_zeros(expr)
    try:
        parsed = ast.parse(normalized, mode="eval")
    except SyntaxError as error:
        raise ArithmeticSafetyError(f"Syntax error in expression: {error}") from error

    evaluator = _SafeEvaluator()
    return evaluator.visit(parsed)


def extract_arithmetic_expression(message: str) -> str | None:
    """
    Extract a candidate arithmetic expression from user message if it represents
    a standalone calculation request. Returns None if message is not an arithmetic request.
    """
    stripped = message.strip()
    if not stripped or len(stripped) > MAX_EXPRESSION_LENGTH:
        return None

    # Exclude dataset-specific tasks and explanatory conversational questions
    if _DATA_EXCLUSION_TERMS.search(stripped) or _EXPLANATION_TERMS.search(stripped):
        return None

    # Strip natural-language prefix if present (e.g. "What is 301 + 90 + 7?")
    prefix_match = _MATH_PREFIX_PATTERN.match(stripped)
    candidate = stripped[prefix_match.end():] if prefix_match else stripped

    # Strip trailing punctuation common in questions / formulas
    candidate = candidate.rstrip("=?. \t\r\n")

    # If empty after stripping, not a calculation
    if not candidate:
        return None

    # Must contain allowed arithmetic characters only
    if not _ALLOWED_CHARS_PATTERN.match(candidate):
        return None

    # Must contain at least one arithmetic operator (+, -, *, /, %)
    if not re.search(r"[\+\-\*\/\%]", candidate):
        return None

    # Must contain at least one digit
    if not re.search(r"\d", candidate):
        return None

    return candidate


def execute_deterministic_arithmetic(
    *,
    user_message: str,
    context: dict[str, Any] | None = None,
) -> AgentOutcome | None:
    """
    Detect and execute standalone arithmetic deterministically.

    Returns completed AgentOutcome if user_message is a standalone arithmetic
    calculation; returns None if it should fall through to normal agent execution.
    """
    # If pinned to a dataset, do not intercept — dataset calculations belong to tools
    if isinstance(context, dict) and context.get("dataset_id") is not None:
        return None

    candidate = extract_arithmetic_expression(user_message)
    if candidate is None:
        return None

    try:
        result = evaluate_expression(candidate)
        formatted_expr = _safe_format_expression(candidate)
        formatted_result = _safe_format_number(result)
        answer = f"{formatted_expr} = {formatted_result}"

        logger.info(
            "Deterministic arithmetic evaluated successfully: %r -> %r",
            user_message,
            answer,
        )
        return AgentOutcome(
            status="completed",
            assistant_text=answer,
            activities=(),
            structured_results=(),
            warnings=(),
            usage=UsageMetadata(),
            error_code=None,
        )
    except (ArithmeticSafetyError, ValueError) as error:
        logger.debug(
            "Expression %r was not evaluated deterministically (%s); falling through.",
            user_message,
            error,
        )
        return None

