"""The wall of a container: each command runs inside one, as a user that is not root."""

from lamplighter.adapters.container.executor import AsRoot, ContainerExecutor

__all__ = ["AsRoot", "ContainerExecutor"]
