"""Trustbook command line client (``trustbook``): the public HTTP API for scripts and agents."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("trustbook-cli")
except PackageNotFoundError:  # run from a source tree without installing
    __version__ = "0.0.0"
