"""Authentication-specific exceptions."""


class AuthError(Exception):
    """Base class for authentication and session errors."""


class OwnerAlreadyExistsError(AuthError):
    """Raised when owner setup is attempted after an owner exists."""


class OwnerSetupDisabledError(AuthError):
    """Raised when owner setup is disabled or the setup token is invalid."""


class InvalidCredentialsError(AuthError):
    """Raised when a login attempt fails."""


class InvalidOwnerUsernameError(AuthError):
    """Raised when the requested owner username is not acceptable."""


class InvalidOwnerPasswordError(AuthError):
    """Raised when the requested owner password does not meet policy."""
