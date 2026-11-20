# This file is part of the greennode namespace.
# It is intentionally minimal to support pkgutil-style namespace packages,
# so multiple GreenNode MCP products can share the `greennode` namespace.
__path__ = __import__("pkgutil").extend_path(__path__, __name__)
