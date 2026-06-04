from typing import Protocol, runtime_checkable

import pytest

from brailix.core.errors import MissingExtraError
from brailix.core.registry import Registry


@runtime_checkable
class Greeter(Protocol):
    def greet(self, who: str) -> str: ...


class GoodGreeter:
    def greet(self, who: str) -> str:
        return f"hello {who}"


class BadGreeter:
    pass  # missing .greet


class TestBasicRegistration:
    def test_register_and_get(self):
        reg: Registry[Greeter] = Registry("greeters")
        reg.register("good", GoodGreeter)
        inst = reg.get("good")
        assert inst.greet("you") == "hello you"

    def test_get_caches_instance(self):
        calls: list[int] = []

        def loader():
            calls.append(1)
            return GoodGreeter()

        reg: Registry[Greeter] = Registry("greeters")
        reg.register("good", loader)
        a = reg.get("good")
        b = reg.get("good")
        assert a is b
        assert len(calls) == 1

    def test_unknown_name_raises_keyerror(self):
        reg: Registry[Greeter] = Registry("greeters")
        reg.register("a", GoodGreeter)
        with pytest.raises(KeyError) as ei:
            reg.get("nope")
        assert "available" in str(ei.value)
        assert "'a'" in str(ei.value)

    def test_has_and_names(self):
        reg: Registry[Greeter] = Registry("greeters")
        assert not reg.has("x")
        reg.register("x", GoodGreeter)
        reg.register("y", GoodGreeter)
        assert reg.has("x")
        assert reg.names() == ["x", "y"]

    def test_unregister(self):
        reg: Registry[Greeter] = Registry("greeters")
        reg.register("x", GoodGreeter)
        reg.get("x")
        reg.unregister("x")
        assert not reg.has("x")

    def test_clear_cache(self):
        calls: list[int] = []

        def loader():
            calls.append(1)
            return GoodGreeter()

        reg: Registry[Greeter] = Registry("greeters")
        reg.register("x", loader)
        reg.get("x")
        reg.clear_cache()
        reg.get("x")
        assert len(calls) == 2

    def test_reregister_invalidates_cached_instance(self):
        first = GoodGreeter()
        second = GoodGreeter()

        reg: Registry[Greeter] = Registry("greeters")
        reg.register("x", lambda: first)
        assert reg.get("x") is first

        reg.register("x", lambda: second)
        assert reg.get("x") is second

    def test_reregister_clears_stale_extra(self):
        def loader():
            raise ImportError("missing thing")

        reg: Registry[Greeter] = Registry("greeters")
        reg.register("x", GoodGreeter, extra="old-extra")
        reg.get("x")

        reg.register("x", loader)
        with pytest.raises(ImportError):
            reg.get("x")


class TestProtocolValidation:
    def test_passing_protocol_accepts_good(self):
        reg: Registry[Greeter] = Registry("greeters", protocol=Greeter)
        reg.register("good", GoodGreeter)
        assert reg.get("good").greet("x") == "hello x"

    def test_failing_protocol_rejects_bad(self):
        reg: Registry[Greeter] = Registry("greeters", protocol=Greeter)
        reg.register("bad", BadGreeter)
        with pytest.raises(TypeError) as ei:
            reg.get("bad")
        assert "Greeter" in str(ei.value)

    def test_no_protocol_skips_check(self):
        reg: Registry[Greeter] = Registry("greeters")
        reg.register("bad", BadGreeter)
        # No protocol → no validation; returns the broken instance.
        reg.get("bad")


class TestLazyImportFailure:
    def test_import_error_with_extra_becomes_missing_extra(self):
        def loader():
            raise ImportError("No module named 'hanlp'")

        reg = Registry("zh_analyzer")
        reg.register("hanlp", loader, extra="hanlp")
        with pytest.raises(MissingExtraError) as ei:
            reg.get("hanlp")
        assert ei.value.adapter == "hanlp"
        assert ei.value.extra == "hanlp"
        assert "pip install brailix[hanlp]" in str(ei.value)

    def test_import_error_without_extra_propagates(self):
        def loader():
            raise ImportError("missing thing")

        reg = Registry("x")
        reg.register("x", loader)  # no extra declared
        with pytest.raises(ImportError):
            reg.get("x")

    def test_non_import_error_propagates(self):
        def loader():
            raise RuntimeError("boom")

        reg = Registry("x")
        reg.register("x", loader, extra="x")
        with pytest.raises(RuntimeError):
            reg.get("x")


class TestLazyLoading:
    def test_register_does_not_import(self):
        """Critical: registering an adapter must not call the loader."""
        called: list[int] = []

        def loader():
            called.append(1)
            return GoodGreeter()

        reg: Registry[Greeter] = Registry("greeters")
        reg.register("x", loader)
        assert called == []
        reg.get("x")
        assert called == [1]
