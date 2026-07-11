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

    def test_concurrent_first_access_loads_once(self):
        # Threads racing the *first* get() of one name must not both run the
        # loader or get different instances — the lazy-load slow path is
        # serialised. Deterministic given a correct lock: the loader runs
        # exactly once no matter how the threads interleave.
        import threading

        calls: list[int] = []

        def loader():
            calls.append(1)
            return GoodGreeter()

        reg: Registry[Greeter] = Registry("greeters")
        reg.register("good", loader)

        results: list[object] = []
        barrier = threading.Barrier(8)

        def worker():
            barrier.wait()  # release all threads into get() together
            results.append(reg.get("good"))

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(calls) == 1  # loader ran exactly once
        assert len({id(r) for r in results}) == 1  # all got the same instance

    def test_unknown_name_raises_keyerror(self):
        from brailix.core.errors import BrailixError, UnknownAdapterError

        reg: Registry[Greeter] = Registry("greeters")
        reg.register("a", GoodGreeter)
        with pytest.raises(KeyError) as ei:
            reg.get("nope")
        assert "available" in str(ei.value)
        assert "'a'" in str(ei.value)
        # Typed UnknownAdapterError: a KeyError (back-compat for the many
        # catchers / tests) AND a BrailixError (so a top-level BrailixError
        # handler surfaces it without swallowing unrelated internal KeyErrors).
        assert isinstance(ei.value, UnknownAdapterError)
        assert isinstance(ei.value, BrailixError)

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

    def test_overriding_registers_then_restores(self):
        reg: Registry[Greeter] = Registry("greeters")
        with reg.overriding("x", GoodGreeter):
            assert reg.has("x")
        assert not reg.has("x")

    def test_overriding_restores_on_exception(self):
        reg: Registry[Greeter] = Registry("greeters")

        def boom():
            with reg.overriding("x", GoodGreeter):
                assert reg.has("x")
                raise RuntimeError

        with pytest.raises(RuntimeError):
            boom()
        assert not reg.has("x")

    def test_overriding_scope_rolls_back_every_registration(self):
        reg: Registry[Greeter] = Registry("greeters")
        with reg.overriding():
            reg.register("x", GoodGreeter)
            reg.register("y", GoodGreeter)
            assert reg.has("x") and reg.has("y")
        assert not reg.has("x")
        assert not reg.has("y")

    def test_overriding_restores_the_prior_loader(self):
        reg: Registry[Greeter] = Registry("greeters")
        reg.register("x", GoodGreeter)
        original = reg.get("x")
        with reg.overriding("x", GoodGreeter):
            # A freshly-registered loader shadows the original in the block.
            assert reg.get("x") is not original
        # ...and the original (same cached instance) is back afterwards.
        assert reg.get("x") is original

    def test_overriding_requires_loader_with_name(self):
        reg: Registry[Greeter] = Registry("greeters")

        def use():
            with reg.overriding("x"):
                pass

        with pytest.raises(ValueError, match="requires a loader"):
            use()

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
        # A genuinely-absent optional dependency raises ModuleNotFoundError
        # (with ``name`` set) from the loader's ``import``.
        def loader():
            raise ModuleNotFoundError("No module named 'hanlp'", name="hanlp")

        reg = Registry("zh_analyzer")
        reg.register("hanlp", loader, extra="hanlp")
        with pytest.raises(MissingExtraError) as ei:
            reg.get("hanlp")
        assert ei.value.adapter == "hanlp"
        assert ei.value.extra == "hanlp"
        assert ei.value.missing_module == "hanlp"
        assert "pip install brailix[hanlp]" in str(ei.value)
        # The concrete failed import is surfaced for diagnosis.
        assert "hanlp" in str(ei.value)

    def test_missing_extra_records_transitive_dependency(self):
        # The extra IS the adapter's package, but a *transitive* dependency it
        # imports is absent (e.g. g2pM importing numpy). The extra hint still
        # helps, but recording the real missing module removes the guesswork.
        def loader():
            raise ModuleNotFoundError("No module named 'numpy'", name="numpy")

        reg = Registry("pinyin")
        reg.register("g2pm", loader, extra="g2pm")
        with pytest.raises(MissingExtraError) as ei:
            reg.get("g2pm")
        assert ei.value.missing_module == "numpy"
        assert "numpy" in str(ei.value)

    def test_internal_module_not_found_propagates(self):
        # The extra is installed, but the adapter's loader imports a renamed /
        # mistyped INTERNAL module. That's a code bug — surfacing "install the
        # extra" would misdirect the user, so the original error propagates.
        def loader():
            raise ModuleNotFoundError(
                "No module named 'brailix.frontend.zh.gone'",
                name="brailix.frontend.zh.gone",
            )

        reg = Registry("zh_analyzer")
        reg.register("hanlp", loader, extra="hanlp")
        with pytest.raises(ModuleNotFoundError) as ei:
            reg.get("hanlp")
        assert not isinstance(ei.value, MissingExtraError)
        assert ei.value.name == "brailix.frontend.zh.gone"

    def test_internal_circular_import_propagates(self):
        # A circular import inside the adapter surfaces as an ImportError whose
        # ``name`` is the partially-initialised brailix module — also a code
        # bug, not a missing extra.
        def loader():
            raise ImportError(
                "cannot import name 'X' from partially initialized module "
                "'brailix.backend.zh'",
                name="brailix.backend.zh",
            )

        reg = Registry("zh_analyzer")
        reg.register("hanlp", loader, extra="hanlp")
        with pytest.raises(ImportError) as ei:
            reg.get("hanlp")
        assert not isinstance(ei.value, MissingExtraError)

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


class TestConcurrentMutation:
    """Runtime registration must not race the compile threads calling
    ``get``. Every mutation and the ``get`` fast path are serialised by the
    one lock; these stress a fixed number of iterations (not a wall-clock
    window, so the test is deterministic) with many threads.
    """

    def test_register_blocks_while_get_holds_lock(self):
        # Deterministic proof that a mutation is serialised against an
        # in-progress ``get``: while a slow loader runs under the lock, a
        # concurrent ``register`` (which also takes the lock) must block until
        # the loader finishes, not interleave with it. Before P2.1 ``register``
        # was lock-free and would mutate the dicts mid-load.
        import threading

        in_loader = threading.Event()
        release = threading.Event()
        order: list[str] = []

        def slow_loader() -> Greeter:
            in_loader.set()
            assert release.wait(timeout=5)
            order.append("loader_done")
            return GoodGreeter()

        reg: Registry[Greeter] = Registry("greeters")
        reg.register("x", slow_loader)

        getter = threading.Thread(target=lambda: reg.get("x"))
        getter.start()
        assert in_loader.wait(timeout=5)  # get is inside the loader, holds lock

        def registrar() -> None:
            reg.register("y", GoodGreeter)  # contends for the same lock
            order.append("register_done")

        reg_thread = threading.Thread(target=registrar)
        reg_thread.start()
        # The registrar cannot make progress while the loader holds the lock.
        reg_thread.join(timeout=0.2)
        assert reg_thread.is_alive()
        assert "register_done" not in order

        release.set()  # loader completes and releases the lock
        reg_thread.join(timeout=5)
        getter.join(timeout=5)
        assert not reg_thread.is_alive()
        # Strict ordering: the registration lands only after the load finished.
        assert order == ["loader_done", "register_done"]

    def test_register_churn_never_crashes_get_under_load(self):
        # Concurrency smoke test: many getters hammering the fast path while
        # registrars evict the cache under them must never crash. The fast
        # path is a single atomic ``dict.get`` — not ``name in _cache`` then
        # ``_cache[name]``, whose gap a concurrent cache-evicting ``register``
        # could turn into a KeyError (a real hazard once the GIL no longer
        # makes the two reads effectively atomic, e.g. free-threaded builds).
        import threading

        reg: Registry[Greeter] = Registry("greeters")
        reg.register("x", GoodGreeter)
        reg.get("x")  # prime the cache so the fast path is exercised

        errors: list[BaseException] = []
        iterations = 4000
        barrier = threading.Barrier(8)

        def getter() -> None:
            barrier.wait()
            for _ in range(iterations):
                try:
                    reg.get("x")
                except KeyError as e:  # UnknownAdapterError is a KeyError too
                    errors.append(e)

        def churner() -> None:
            barrier.wait()
            for _ in range(iterations):
                reg.register("x", GoodGreeter)  # evicts the cache each time

        threads = [threading.Thread(target=getter) for _ in range(6)]
        threads += [threading.Thread(target=churner) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors  # x stays registered → no KeyError of any kind

    def test_mixed_mutation_and_get_stays_consistent(self):
        # register / unregister / clear_cache / get all hammering the same
        # name: the only legal failure a getter may see is
        # UnknownAdapterError (a concurrent unregister removed the loader);
        # a bare KeyError would mean a torn read of the dicts.
        import threading

        from brailix.core.errors import UnknownAdapterError

        reg: Registry[Greeter] = Registry("greeters")
        reg.register("x", GoodGreeter)

        errors: list[BaseException] = []
        instances: list[object] = []
        iterations = 3000
        barrier = threading.Barrier(9)

        def getter() -> None:
            barrier.wait()
            for _ in range(iterations):
                try:
                    instances.append(reg.get("x"))
                except UnknownAdapterError:
                    pass  # legal: unregistered right now
                except KeyError as e:
                    errors.append(e)  # a torn read — must never happen

        def registrar() -> None:
            barrier.wait()
            for _ in range(iterations):
                reg.register("x", GoodGreeter)

        def remover() -> None:
            barrier.wait()
            for _ in range(iterations):
                reg.unregister("x")

        def cache_clearer() -> None:
            barrier.wait()
            for _ in range(iterations):
                reg.clear_cache()

        threads = [threading.Thread(target=getter) for _ in range(6)]
        threads += [
            threading.Thread(target=registrar),
            threading.Thread(target=remover),
            threading.Thread(target=cache_clearer),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        # Every successful get returned a real adapter, never a torn None.
        assert all(isinstance(i, GoodGreeter) for i in instances)
