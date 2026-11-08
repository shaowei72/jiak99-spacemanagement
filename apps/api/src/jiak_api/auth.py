"""Who is calling, and what they may do (roles Sales and Operations, SPEC §5.1).

For now ("dev" auth mode) the caller says who they are in headers:
    X-User: alice@example.com      (default "dev")
    X-Role: Sales | Operations     (default "Operations")
Real Microsoft Entra ID sign-in replaces this later; routes won't need to change.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Literal

from fastapi import Depends, Header, HTTPException

Role = Literal["Sales", "Operations"]


@dataclass(frozen=True)
class User:
    name: str
    role: Role

    def can(self, role: Role) -> bool:
        """Operations can do everything Sales can."""
        return self.role == "Operations" or self.role == role


def current_user(
    x_user: Annotated[str, Header()] = "dev",
    x_role: Annotated[Role, Header()] = "Operations",
) -> User:
    return User(name=x_user, role=x_role)


def require(role: Role) -> Callable[[User], User]:
    """A dependency that lets the request through only for users with `role`."""

    def check(user: Annotated[User, Depends(current_user)]) -> User:
        if not user.can(role):
            raise HTTPException(status_code=403, detail=f"Needs the {role} role.")
        return user

    return check
