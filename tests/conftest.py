import pytest


class _OsNameProxy:
    """The real ``os`` module with only ``name`` overridden."""

    def __init__(self, base, name):
        self._base = base
        self.name = name

    def __getattr__(self, attribute):
        return getattr(self._base, attribute)


@pytest.fixture
def patch_os_name(monkeypatch):
    """Make one module see a different ``os.name`` without patching ``os`` itself.

    Setting ``os.name`` globally switches ``pathlib.Path`` to the other flavour
    for the whole process, so a failing assertion then crashes pytest's own
    reporting with "cannot instantiate 'WindowsPath' on your system".
    """

    def apply(module, name):
        monkeypatch.setattr(module, "os", _OsNameProxy(module.os, name))

    return apply
