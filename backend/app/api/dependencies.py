from fastapi import HTTPException, Request


async def get_current_user_id(request: Request) -> int:
    """
    Resolve the authenticated user for a request.

    ASTA does not have an authentication implementation yet. This
    dependency fails closed: access is only granted when trusted
    authentication middleware (or a future auth dependency) sets
    ``request.state.user_id``. It never accepts a user id from the
    request body, query string, or headers.

    Tests override this dependency with a fixed user id.
    """

    user_id = getattr(request.state, "user_id", None)

    if not isinstance(user_id, int):
        raise HTTPException(
            status_code=401,
            detail="Authentication is not configured."
        )

    return user_id


async def get_optional_user_id(request: Request) -> int | None:
    """
    Resolve the authenticated user when trusted auth is available.

    Used by endpoints that already support unauthenticated access but can
    add personalized context when a user is authenticated. It never accepts
    a user id from the request body, query string, or headers.
    """

    user_id = getattr(request.state, "user_id", None)

    if isinstance(user_id, int):
        return user_id

    return None
