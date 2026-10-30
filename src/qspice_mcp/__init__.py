"""QSPICE MCP server package."""

__version__ = "0.2.0"


def main() -> None:
    from .server import main as _main
    _main()
