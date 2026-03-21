# sysapi.py
# Global handle used by REPL tools (e.g., syscli.neofetch()) to access live system state.

_REG = None

def set_registry(reg):
    """Called by supervisor/main to expose the live Registry to REPL tools."""
    global _REG
    _REG = reg

def get_registry():
    """Used by REPL tools to read the live Registry (or None if not set)."""
    return _REG