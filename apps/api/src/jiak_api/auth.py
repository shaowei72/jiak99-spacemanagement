"""Who is calling, and what they may do (roles Sales and Operations, SPEC §5.1).

For now ("dev" auth mode) the caller says who they are in headers:
    X-User: alice@example.com      (default: JIAK_USER, else "dev")
    X-Role: Sales | Operations     (default "Operations")
On the owner's laptop (SPEC §5.5) nobody sends headers, so every change is recorded under the name
in JIAK_USER. Real Microsoft Entra ID sign-in replaces this at the cloud move; routes won't change.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Literal

from fastapi import Depends, Header, HTTPException, Request

Role = Literal["Sales", "Operations"]


@dataclass(frozen=True)
class User:
    name: str
    role: Role

    def can(self, role: Role) -> bool:
        """Operations can do everything Sales can."""
        return self.role == "Operations" or self.role == role


def current_user(
    request: Request,
    x_user: Annotated[str | None, Header()] = None,
    x_role: Annotated[Role, Header()] = "Operations",
) -> User:
    """Who is calling: the X-User header, else the app's default user (JIAK_USER)."""
    name = x_user or request.app.state.default_user
    return User(name=name, role=x_role)


def require(role: Role) -> Callable[[User], User]:
    """A dependency that lets the request through only for users with `role`."""

    def check(user: Annotated[User, Depends(current_user)]) -> User:
        if not user.can(role):
            raise HTTPException(status_code=403, detail=f"Needs the {role} role.")
        return user

    return check
