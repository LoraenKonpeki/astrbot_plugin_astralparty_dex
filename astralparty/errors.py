class UserError(Exception):
    """A deliberately credential-free message that can be shown in chat."""


class SessionConflict(UserError):
    pass


class AuthExpired(UserError):
    pass
