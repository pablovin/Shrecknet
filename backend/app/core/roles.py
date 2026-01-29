"""Role hierarchy utilities for authorization."""

from app.models.user import UserRole


# Define role hierarchy: lower index = lower privilege
ROLE_HIERARCHY = [
    UserRole.PLAYER,
    UserRole.WRITER,
    UserRole.WORLD_BUILDER,
    UserRole.ADMIN,
]


def has_role(user_role: UserRole, required_role: UserRole) -> bool:
    """
    Check if user_role has at least the privilege level of required_role.

    Uses hierarchical comparison: PLAYER < WRITER < WORLD_BUILDER < ADMIN

    Args:
        user_role: The user's actual role
        required_role: The minimum required role

    Returns:
        True if user_role has at least the privilege of required_role

    Examples:
        >>> has_role(UserRole.ADMIN, UserRole.PLAYER)
        True
        >>> has_role(UserRole.PLAYER, UserRole.ADMIN)
        False
        >>> has_role(UserRole.WRITER, UserRole.WRITER)
        True
    """
    try:
        user_level = ROLE_HIERARCHY.index(user_role)
        required_level = ROLE_HIERARCHY.index(required_role)
        return user_level >= required_level
    except ValueError:
        # If role not in hierarchy, deny access
        return False


def get_minimum_role(*roles: UserRole) -> UserRole | None:
    """
    Get the minimum (lowest privilege) role from a list of roles.

    Args:
        *roles: Variable number of UserRole values

    Returns:
        The role with the lowest privilege level, or None if no roles provided

    Examples:
        >>> get_minimum_role(UserRole.ADMIN, UserRole.PLAYER)
        UserRole.PLAYER
        >>> get_minimum_role(UserRole.WORLD_BUILDER, UserRole.WRITER)
        UserRole.WRITER
    """
    if not roles:
        return None

    valid_roles = [r for r in roles if r in ROLE_HIERARCHY]
    if not valid_roles:
        return None

    return min(valid_roles, key=lambda r: ROLE_HIERARCHY.index(r))
