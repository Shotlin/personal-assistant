"""Fail-closed errors shared by the local recipe runtime."""


class RecipeFailure(RuntimeError):
    """An unsupported or failed recipe step; do not guess another action."""
