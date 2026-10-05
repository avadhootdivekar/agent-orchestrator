"""Auth test helpers, one module per owning task (HLD section 20.2).

Import from the owning module (``from tests.auth.helpers.core import FakeClock``); this package
defines no names and re-exports nothing, so S1 lanes never edit one shared file.

==========================  ==========  ======================================================
Module                      Owner       Contents
==========================  ==========  ======================================================
``core.py``                 T-kzEzwy    ``FakeClock``, ``SeededEntropy``, ``run_async``,
                                        ``make_client``, ``same_origin_headers``
``crypto.py``               T-s6sJmB    ``TEST_PARAMS``, ``FastFakeHasher``
``store.py``                T-8NQP8J    ``make_store``
``sessions.py``             T-kwwJ82    ``make_identity``
``stub_runtime.py``         T-G7qByZ    ``StubRuntime``, ``StubRealm``,
                                        ``install_stub_auth_routes``
``enumeration.py``          T-G7qByZ    the route-enumeration harness (``iter_route_contexts``)
==========================  ==========  ======================================================
"""
