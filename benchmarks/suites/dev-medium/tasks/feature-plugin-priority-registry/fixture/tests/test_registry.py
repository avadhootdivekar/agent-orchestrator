import pytest
from registry.core import Registry
from registry.dispatch import NoHandlerResolvedError, dispatch
from registry.handlers import make_greeter


def test_register_and_is_registered() -> None:
    reg = Registry()
    h = make_greeter("en")
    reg.register("greet", h, priority=0)
    assert reg.is_registered("greet", h)


def test_unregister_removes_handler() -> None:
    reg = Registry()
    h = make_greeter("en")
    reg.register("greet", h)
    reg.unregister("greet", h)
    assert not reg.is_registered("greet", h)


def test_dispatch_single_handler_resolves() -> None:
    reg = Registry()
    reg.register("greet", make_greeter("en"))
    assert dispatch(reg, "greet", {"language": "en"}) == "Hello"


def test_dispatch_raises_when_nothing_resolves() -> None:
    reg = Registry()
    reg.register("greet", make_greeter("fr"))
    with pytest.raises(NoHandlerResolvedError):
        dispatch(reg, "greet", {"language": "en"})


def test_handlers_for_orders_by_priority_descending() -> None:
    reg = Registry()
    low = make_greeter("es")
    high = make_greeter("en")
    reg.register("greet", low, priority=0)
    reg.register("greet", high, priority=10)
    assert reg.handlers_for("greet") == [high, low]
