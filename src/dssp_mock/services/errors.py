from __future__ import annotations

from typing import Any


class ProblemError(Exception):
    def __init__(
        self,
        status: int,
        title: str,
        detail: str,
        *,
        problem_type: str = "about:blank",
        extensions: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(detail)
        self.status = status
        self.title = title
        self.detail = detail
        self.problem_type = problem_type
        self.extensions = extensions or {}

    def as_dict(self, instance: str | None = None) -> dict[str, Any]:
        result: dict[str, Any] = {
            "type": self.problem_type,
            "title": self.title,
            "status": self.status,
            "detail": self.detail,
        }
        if instance is not None:
            result["instance"] = instance
        result.update(self.extensions)
        return result


def not_found(kind: str, identifier: str) -> ProblemError:
    return ProblemError(
        404,
        f"{kind} not found",
        f"No {kind.lower()} with id {identifier!r} exists in this mock instance.",
        problem_type="https://dssp-mock.local/problems/not-found",
    )


def invalid_request(detail: str, *, errors: list[dict[str, Any]] | None = None) -> ProblemError:
    extensions = {"errors": errors} if errors else None
    return ProblemError(
        422,
        "Invalid request",
        detail,
        problem_type="https://dssp-mock.local/problems/invalid-request",
        extensions=extensions,
    )


def unsupported(detail: str) -> ProblemError:
    return ProblemError(
        501,
        "Feature disabled by architecture",
        detail,
        problem_type="https://dssp-mock.local/problems/feature-disabled",
    )
