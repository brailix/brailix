from typing import Any, Protocol, runtime_checkable

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


class TestANoneAdapterIsRefused:
    """"An adapter is never ``None``" is what the lock-free fast path is
    built on — ``_cache.get(name)`` reads ``None`` as *not cached* — so it
    has to be a check, not a comment.

    A protocol-configured registry already refused one (nothing conforms to a
    Protocol with methods); a registry declaring no protocol cached it, and
    then every later ``get`` walked the locked slow path to be handed the same
    nothing. The plugin that returned it was long gone by the time the
    ``AttributeError`` surfaced, several layers into a translation run.
    """

    def test_a_loader_returning_none_raises_naming_the_adapter(self):
        reg: Registry[Greeter] = Registry("greeters")
        reg.register("empty", lambda: None)
        with pytest.raises(TypeError) as ei:
            reg.get("empty")
        assert "empty" in str(ei.value)
        assert "greeters" in str(ei.value)

    def test_the_refusal_repeats_rather_than_caching_none(self):
        # Nothing was cached, so the next call re-runs the loader and fails
        # the same way — no half-registered state to explain.
        calls: list[int] = []

        def loader():
            calls.append(1)
            return None

        reg: Registry[Greeter] = Registry("greeters")
        reg.register("empty", loader)
        for _ in range(2):
            with pytest.raises(TypeError):
                reg.get("empty")
        assert len(calls) == 2

    def test_the_none_refusal_wins_over_the_protocol_message(self):
        # A protocol-configured registry rejected None already, but as "does
        # not conform to protocol Greeter" — true, and the least useful way to
        # say it: the loader returned nothing at all, which is a different
        # repair from a missing method. The specific message is the one to
        # keep, so the None check runs first.
        reg: Registry[Greeter] = Registry("greeters", protocol=Greeter)
        reg.register("empty", lambda: None)
        with pytest.raises(TypeError) as ei:
            reg.get("empty")
        assert "None" in str(ei.value)


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

    def test_a_missing_symbol_from_an_installed_package_propagates(self):
        # The dependency IS installed; a version of it simply no longer has
        # the symbol (``from transformers import BertTokenizer`` after an
        # upstream removal). CPython raises a plain ImportError — not a
        # ModuleNotFoundError — and still sets ``name`` to the *package*, so
        # wrapping every ImportError told the user to install something they
        # already had, and buried the real error behind advice that could not
        # work. Only "the module is not there at all" is a missing extra.
        import sys
        import types

        installed = types.ModuleType("installed_but_changed")
        sys.modules["installed_but_changed"] = installed

        def loader():
            from installed_but_changed import removed_symbol  # noqa: F401

            return GoodGreeter()

        reg: Registry[Greeter] = Registry("zh_analyzer")
        reg.register("adapter", loader, extra="someextra")
        try:
            with pytest.raises(ImportError) as ei:
                reg.get("adapter")
        finally:
            del sys.modules["installed_but_changed"]
        assert not isinstance(ei.value, MissingExtraError)
        assert not isinstance(ei.value, ModuleNotFoundError)
        # The original message survives, so the real cause is readable.
        assert "removed_symbol" in str(ei.value)

    def test_a_missing_submodule_is_still_a_missing_extra(self):
        # The other half of the same rule: ``import pkg.sub`` where nothing is
        # installed raises ModuleNotFoundError, which is exactly the case the
        # extras hint answers. Narrowing the wrap must not cost this.
        def loader():
            import not_installed_anywhere_at_all.sub  # noqa: F401

            return GoodGreeter()

        reg: Registry[Greeter] = Registry("zh_analyzer")
        reg.register("adapter", loader, extra="someextra")
        with pytest.raises(MissingExtraError) as ei:
            reg.get("adapter")
        assert ei.value.missing_module == "not_installed_anywhere_at_all"

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


class TestOverridingConcurrency:
    """``overriding()`` takes the lock only to snapshot on entry and to
    restore on exit — the caller's block runs WITHOUT it. These pin that
    contract: a worker thread must be able to use the registry inside the
    block (holding the RLock across the ``yield`` would deadlock any
    thread but the owner), and exit restores the entry snapshot verbatim
    (test-support semantics: even a registration another thread made
    during the block is rolled back)."""

    def test_worker_thread_uses_registry_inside_block(self):
        import threading

        reg: Registry[Greeter] = Registry("greeters")
        reg.register("base", GoodGreeter)

        results: list[object] = []

        def worker() -> None:
            results.append(reg.get("tmp"))
            results.append(reg.has("base"))
            results.append(reg.names())

        with reg.overriding("tmp", GoodGreeter):
            t = threading.Thread(target=worker)
            t.start()
            t.join(timeout=5)
            # A lock held across the yield would leave the worker blocked
            # on its first registry call — caught here, not as a hang.
            assert not t.is_alive(), "registry deadlocked inside overriding()"

        assert isinstance(results[0], GoodGreeter)
        assert results[1] is True
        assert results[2] == ["base", "tmp"]

    def test_exit_restores_entry_snapshot_even_over_concurrent_register(self):
        # Documented rollback semantics, pinned deliberately: overriding()
        # restores the ENTRY snapshot, so a registration made by another
        # thread DURING the block is rolled back too. That is what
        # "temporarily install, restore prior state" means for a
        # test-support API — production code must not register adapters
        # concurrently with an overriding() block and expect them to stick.
        import threading

        reg: Registry[Greeter] = Registry("greeters")
        reg.register("base", GoodGreeter)

        with reg.overriding("tmp", GoodGreeter):
            t = threading.Thread(
                target=lambda: reg.register("late", GoodGreeter)
            )
            t.start()
            t.join(timeout=5)
            assert reg.has("late")  # landed while the block was open

        assert not reg.has("tmp")  # the override is gone...
        assert not reg.has("late")  # ...and so is the concurrent late-comer
        assert reg.has("base")


class TestGeneration:
    """``generation`` is the registration-surface version counter the
    compilation fingerprint folds in: every mutation of what a name can
    resolve to must advance it, and pure reads must not — otherwise a
    runtime re-register would keep serving caches built by the replaced
    implementation (or the steady state would thrash them)."""

    def test_register_bumps(self):
        reg: Registry[Greeter] = Registry("greeters")
        g0 = reg.generation
        reg.register("a", GoodGreeter)
        assert reg.generation == g0 + 1
        # Re-registering the SAME name is exactly the replaced-adapter case.
        reg.register("a", GoodGreeter)
        assert reg.generation == g0 + 2

    def test_unregister_bumps(self):
        reg: Registry[Greeter] = Registry("greeters")
        reg.register("a", GoodGreeter)
        g = reg.generation
        reg.unregister("a")
        assert reg.generation == g + 1

    def test_get_does_not_bump(self):
        reg: Registry[Greeter] = Registry("greeters")
        reg.register("a", GoodGreeter)
        g = reg.generation
        reg.get("a")  # loads
        reg.get("a")  # cache hit
        assert reg.generation == g

    def test_clear_cache_bumps(self):
        """Dropping the cached instances CAN change what a name resolves to.

        An ``auto`` adapter picks its delegate by probing what is currently
        installed / downloaded and memoises the choice on the instance, so a
        fresh instance may pick differently — "same loader, same
        implementation" doesn't hold, and a fingerprint that ignored the
        clear would let a cache serve braille from the previous delegate.
        """
        reg: Registry[Greeter] = Registry("greeters")
        reg.register("a", GoodGreeter)
        reg.get("a")
        g = reg.generation
        reg.clear_cache()
        assert reg.generation == g + 1

    def test_clear_cache_bump_reaches_pipeline_fingerprint(self):
        """The bump has to be visible where it matters: a live Pipeline's
        fingerprint (which folds every compilation-relevant registry's
        generation) must move, so ``source_hash`` and the
        ``frontend_fingerprint`` stamps invalidate."""
        from brailix import Pipeline
        from brailix.frontend.zh.analyzer.registry import analyzer_registry

        pipe = Pipeline(profile="cn_current", analyzer="char", resolver="null")
        before = pipe.fingerprint
        analyzer_registry.clear_cache()
        assert pipe.fingerprint != before

    def test_has_and_names_do_not_bump(self):
        reg: Registry[Greeter] = Registry("greeters")
        reg.register("a", GoodGreeter)
        g = reg.generation
        reg.has("a")
        reg.names()
        assert reg.generation == g

    def test_overriding_exit_bumps(self):
        # Exit restores the entry snapshot — a registration-surface change
        # (what "tmp" resolves to just flipped back), so it must advance
        # the counter even though the content equals the entry state.
        reg: Registry[Greeter] = Registry("greeters")
        reg.register("base", GoodGreeter)
        g = reg.generation
        with reg.overriding("tmp", GoodGreeter):
            assert reg.generation == g + 1  # the block's own register
        assert reg.generation == g + 2  # ...plus the restore


class TestAvailabilityProbe:
    """``available`` answers "is this adapter's dependency installed?" without
    running the loader.

    It exists because the only previous way to find out was to *load* the
    adapter, and loading is not a neutral question to ask: a segmentation
    engine's loader reads a hundred-megabyte model, so a front-end populating
    an engine picker would have loaded every engine to decide which ones to
    offer. Not offering an engine that cannot work is what stops a stored
    setting from naming one, which is how a document ends up compiling to
    nothing with only a wall of per-block errors to explain it.
    """

    def test_a_declared_probe_that_is_missing_reads_unavailable(self) -> None:
        reg: Registry[object] = Registry("probe")
        reg.register(
            "ghost",
            lambda: object(),
            extra="ghost",
            probe="a_module_that_is_not_installed_anywhere",
        )
        assert reg.has("ghost")
        assert reg.available("ghost") is False
        assert reg.available_names() == []

    def test_a_declared_probe_that_resolves_reads_available(self) -> None:
        reg: Registry[object] = Registry("probe")
        reg.register("real", lambda: object(), extra="json", probe="json")
        assert reg.available("real") is True
        assert reg.available_names() == ["real"]

    def test_an_undeclared_probe_reads_available(self) -> None:
        # "Cannot tell" is not "missing": a built-in with no third-party
        # dependency, or a plugin that declared nothing, must stay offered.
        reg: Registry[object] = Registry("probe")
        reg.register("builtin", lambda: object())
        assert reg.available("builtin") is True

    def test_an_unregistered_name_reads_unavailable(self) -> None:
        reg: Registry[object] = Registry("probe")
        assert reg.available("nobody") is False

    def test_every_module_of_a_multi_probe_must_resolve(self) -> None:
        reg: Registry[object] = Registry("probe")
        reg.register(
            "pair", lambda: object(), probe=("json", "not_installed_at_all")
        )
        assert reg.available("pair") is False

    def test_the_probe_never_runs_the_loader(self) -> None:
        # The whole point: asking must not pay the import it is asking about.
        calls: list[int] = []

        def loader() -> object:
            calls.append(1)
            return object()

        reg: Registry[object] = Registry("probe")
        reg.register("heavy", loader, probe="json")
        assert reg.available("heavy") is True
        assert reg.available_names() == ["heavy"]
        assert calls == []

    def test_unregister_forgets_the_probe(self) -> None:
        reg: Registry[object] = Registry("probe")
        reg.register("x", lambda: object(), probe="nope_not_here")
        reg.unregister("x")
        reg.register("x", lambda: object())
        assert reg.available("x") is True

    def test_re_registering_without_a_probe_clears_the_old_one(self) -> None:
        # register() replaces a registration whole; a stale probe would keep
        # reporting the new adapter missing.
        reg: Registry[object] = Registry("probe")
        reg.register("x", lambda: object(), probe="nope_not_here")
        assert reg.available("x") is False
        reg.register("x", lambda: object())
        assert reg.available("x") is True


class TestRegistrationMetadataIsChecked:
    """``register`` is a third party's entry point, so its arguments are
    checked where they are passed.

    The registration outlives the call: whatever it stores is read much later,
    by code that never saw the plugin. A ``probe=(123,)`` sat there until a
    front-end asked what was installed, and then broke *discovery itself* —
    :meth:`available_names` walks every registration, so ``find_spec(123)``
    raising ``AttributeError`` meant no engine list could be built at all. One
    plugin's typo, every engine gone, and the traceback points at
    ``importlib``.
    """

    def test_a_bad_probe_cannot_be_registered_at_all(self) -> None:
        reg: Registry[object] = Registry("probe")
        with pytest.raises(TypeError) as excinfo:
            reg.register("bad", lambda: object(), probe=(123,))
        assert "bad" in str(excinfo.value)

    def test_a_non_iterable_probe_names_the_adapter(self) -> None:
        reg: Registry[object] = Registry("probe")
        with pytest.raises(TypeError) as excinfo:
            reg.register("bad", lambda: object(), probe=123)  # type: ignore[arg-type]
        assert "bad" in str(excinfo.value)

    @pytest.mark.parametrize("probe", ["", ("json", "")])
    def test_an_empty_module_name_is_refused(self, probe) -> None:
        reg: Registry[object] = Registry("probe")
        with pytest.raises(ValueError):
            reg.register("bad", lambda: object(), probe=probe)

    def test_one_bad_plugin_no_longer_breaks_the_whole_engine_list(
        self,
    ) -> None:
        """The failure that mattered: discovery is a list comprehension over
        every registration, so a value only *one* adapter got wrong took the
        others down with it."""
        reg: Registry[object] = Registry("probe")
        reg.register("good", lambda: object(), probe="json")
        with pytest.raises(TypeError):
            reg.register("plugin", lambda: object(), probe=(123,))
        assert reg.names() == ["good"]
        assert reg.available_names() == ["good"]

    def test_a_refused_registration_changes_nothing(self) -> None:
        """Validation runs before the lock, so there is no half-applied
        registration to roll back — the previous one is still whole."""
        reg: Registry[object] = Registry("probe")
        reg.register("x", GoodGreeter, probe="json")
        generation = reg.generation
        with pytest.raises(TypeError):
            reg.register("x", GoodGreeter, probe=(None,))
        assert reg.generation == generation
        assert reg.available("x") is True
        assert isinstance(reg.get("x"), GoodGreeter)

    def test_an_empty_probe_tuple_means_the_same_as_omitting_it(self) -> None:
        # A plugin computing ``probe=tuple(deps)`` from an empty ``deps`` is
        # saying "no third-party dependency", not "nothing is available".
        reg: Registry[object] = Registry("probe")
        reg.register("pure", lambda: object(), probe=())
        assert reg.available("pure") is True

    def test_any_iterable_of_module_names_is_accepted(self) -> None:
        # No ``type: ignore`` here on purpose: the signature says
        # ``str | Iterable[str] | None``, which is what the docstring and the
        # implementation have always meant. It used to say ``tuple``, so the
        # one shape a plugin most naturally computes — a list — type-checked
        # as an error while working perfectly at runtime.
        reg: Registry[object] = Registry("probe")
        reg.register("listed", lambda: object(), probe=["json", "struct"])
        assert reg.available("listed") is True

    def test_a_generator_of_module_names_is_accepted(self) -> None:
        # "Any iterable" includes a one-shot one: ``_normalize_probe`` reads
        # it exactly once, into a tuple, before anything can consume it twice.
        reg: Registry[object] = Registry("probe")
        reg.register("gen", lambda: object(), probe=(m for m in ("json",)))
        assert reg.available("gen") is True

    def test_a_nameless_adapter_is_refused(self) -> None:
        reg: Registry[object] = Registry("probe")
        with pytest.raises(ValueError):
            reg.register("", lambda: object())
        with pytest.raises(TypeError):
            reg.register(None, lambda: object())  # type: ignore[arg-type]
        assert reg.names() == []

    def test_a_loader_that_is_not_callable_is_refused_at_registration(
        self,
    ) -> None:
        # It used to be accepted and fail at the first ``get`` — in a
        # front-end, that is a translation run rather than a plugin's own
        # startup.
        reg: Registry[object] = Registry("probe")
        with pytest.raises(TypeError):
            reg.register("x", "not a callable")  # type: ignore[arg-type]

    @pytest.mark.parametrize("extra", ["", 123])
    def test_an_extra_that_cannot_be_a_pip_group_is_refused(
        self, extra
    ) -> None:
        # ``extra`` is quoted verbatim into the "pip install brailix[...]"
        # line a MissingExtraError shows the user.
        reg: Registry[object] = Registry("probe")
        with pytest.raises(ValueError):
            reg.register("x", lambda: object(), extra=extra)


def _shipped_registries() -> list[Registry[Any]]:
    """Every registry that ships adapters behind an optional extra."""
    from brailix.frontend.graphics.registry import graphic_source_registry
    from brailix.frontend.ja.analyzer.registry import (
        analyzer_registry as ja_analyzer_registry,
    )
    from brailix.frontend.math.registry import math_source_registry
    from brailix.frontend.music.registry import music_source_registry
    from brailix.frontend.zh.analyzer.registry import analyzer_registry
    from brailix.frontend.zh.pinyin.registry import resolver_registry

    return [
        analyzer_registry,
        resolver_registry,
        ja_analyzer_registry,
        math_source_registry,
        music_source_registry,
        graphic_source_registry,
    ]


def _imported_top_level_modules(module_name: str) -> set[str]:
    """Top-level module names ``module_name``'s source imports, at any depth.

    ``ast.walk``, not the module body: an adapter imports its library *inside*
    the loader — that laziness is the whole point of the registry — so a scan
    of top-level statements would find nothing to compare a probe against.
    Read statically, so an adapter whose extra is not installed is covered
    exactly like one whose extra is.
    """
    import ast
    import importlib.util
    from pathlib import Path

    spec = importlib.util.find_spec(module_name)
    assert spec is not None and spec.origin is not None, module_name
    tree = ast.parse(Path(spec.origin).read_text(encoding="utf-8"))

    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            names.add(node.module.split(".")[0])
    return names


class TestShippedAdaptersDeclareUsableProbes:
    """A probe naming the wrong module is worse than none: it reports a
    working engine missing, and a front-end then hides it (or a stored
    setting is reset off it) for no reason. The ``g2pm`` extra installs the
    ``g2pM`` module, so guessing the module from the extra is exactly the
    mistake available to make here.

    Checked **without loading anything**, which is not a convenience but the
    contract. :meth:`Registry.available` answers "is the module findable",
    explicitly *not* "will the adapter load" — an installed package whose
    model has not been downloaded, or one beside a dependency version the
    adapter refuses, is available and un-loadable at the same time, by design.
    A test that loaded every engine and asserted the two agreed therefore went
    red on a correct probe in exactly the environments the distinction exists
    for, while making the ordinary suite read a hundred-megabyte model per
    engine — to check a fact that is written in the source.

    What is written there is the real relationship: ``probe`` names the
    third-party module the loader imports. Both halves are read off the tree.
    """

    def test_every_probe_names_a_module_its_adapter_imports(self) -> None:
        mismatches: list[str] = []
        for reg in _shipped_registries():
            for name in reg.names():
                # ``_probes`` / ``_loaders``: this is the class's own test, and
                # there is deliberately no public accessor — a probe is an
                # answer ``available`` gives, not a value callers read back.
                probes = reg._probes.get(name)
                if not probes:
                    continue  # declares no third-party dependency
                adapter_module = reg._loaders[name].__module__
                imported = _imported_top_level_modules(adapter_module)
                for probe in probes:
                    if probe.split(".")[0] not in imported:
                        mismatches.append(
                            f"{reg.subsystem}:{name} probes {probe!r} but "
                            f"{adapter_module} imports {sorted(imported)}"
                        )
        assert not mismatches, (
            "declared probes that name a module their adapter never imports "
            "— `available` would report a working engine missing (or an "
            "absent one present):\n  " + "\n  ".join(mismatches)
        )

    def test_the_scan_finds_the_lazy_imports_it_is_reading(self) -> None:
        """The check above passes both on a clean tree and on a scan that
        stopped finding anything, so pin that it really sees a loader-level
        import — the shape every adapter uses."""
        found = _imported_top_level_modules(
            "brailix.frontend.zh.pinyin.adapters.g2pm"
        )
        assert "g2pM" in found

    def test_at_least_one_registry_still_declares_probes(self) -> None:
        """...and that there is something to check at all: if every probe were
        dropped, the loop above would iterate over nothing and pass."""
        probed = [
            name
            for reg in _shipped_registries()
            for name in reg.names()
            if reg._probes.get(name)
        ]
        assert len(probed) >= 10, probed


def test_overriding_restores_the_probe_too() -> None:
    """``overriding`` snapshots every per-name dict, probes included.

    ``register`` replaces a registration whole, so a temporary stub that
    declares no probe clears the real one's. Without the probe in the
    snapshot the clear survived the block, and the next caller was told an
    engine was installed because nothing was left to say otherwise — which is
    the failure this whole mechanism exists to prevent, arriving through the
    test-support helper.
    """
    reg: Registry[object] = Registry("probe")
    reg.register("engine", lambda: object(), probe="not_installed_anywhere")
    assert reg.available("engine") is False

    with reg.overriding("engine", lambda: object()):
        assert reg.available("engine") is True  # the stub has no dependency

    assert reg.available("engine") is False


def test_a_namespace_package_reads_unavailable(tmp_path) -> None:
    """A directory with no code in it imports fine, and is not the package.

    This is what an application bundle leaves behind when it stops shipping
    an engine: the executable is replaced, the engine's *data* directory
    stays, and the application's own directory is the first entry on
    ``sys.path``. ``import <engine>`` then succeeds with no ``__file__``, and
    an adapter that locates its data relative to one fails on ``None`` —
    which is not a "candidate unavailable" error, so an ``auto`` chain
    propagates it instead of skipping, and every block of every document
    fails.

    Answering it here means the picker never offers the engine and a stored
    setting naming it is repaired, before any of that.
    """
    import sys

    leftover = tmp_path / "ghostengine"
    leftover.mkdir()
    (leftover / "models").mkdir()  # data, but no __init__.py
    sys.path.insert(0, str(tmp_path))
    try:
        import importlib.util

        spec = importlib.util.find_spec("ghostengine")
        assert spec is not None, "the bare directory should still import"
        assert spec.origin is None, "…as a namespace package"

        reg: Registry[object] = Registry("probe")
        reg.register("ghost", lambda: object(), probe="ghostengine")
        assert reg.available("ghost") is False
        assert reg.available_names() == []
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop("ghostengine", None)
