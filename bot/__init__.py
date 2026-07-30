"""Chess bot package with lazy engine loading for lightweight tooling/tests."""

__all__ = ["ChessBotEngine"]


def __getattr__(name):
    if name == "ChessBotEngine":
        from .engine import ChessBotEngine
        return ChessBotEngine
    raise AttributeError(name)
