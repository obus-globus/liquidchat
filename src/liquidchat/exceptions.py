"""Exception types raised by the liquidchat package."""

from __future__ import annotations


class LiquidChatError(Exception):
    """Base class for all liquidchat errors."""


class MissingTokenError(LiquidChatError):
    """Raised when a JWT token is required but has not been configured."""


class LoginFailedError(LiquidChatError):
    """Raised when the server rejects a login attempt."""


class ProtocolError(LiquidChatError):
    """Raised when the server sends a message we cannot parse."""
