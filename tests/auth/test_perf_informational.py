"""NFR-5 informational measurements (T-U2ERMo item 7; HLD 3.5, 20.1; design-review minor 4).

NFR-5 is a *target*, not an acceptance criterion: this module measures and PRINTS p95 numbers
(they are recorded in the ticket STATUS) and **never asserts a threshold**. It is opt-in
(``pytest -m slow tests/auth/test_perf_informational.py -s``): it skips unless the ``slow`` marker
is selected, because it runs real scrypt and 1000 requests, and CI timing noise makes it unfit as
a gate. The only assertions are sanity checks that the measurement itself ran.

Measured:
* per-request overhead on the dashboard app: p95 of ``classify`` + ``sessions.lookup`` +
  proof match + ``provider.revalidate`` over ``REQUEST_SAMPLES`` requests (microseconds), and the
  p95 wall time of a whole authenticated TestClient round trip for context;
* login: p95 of a real ``POST /api/auth/login`` with the production ``BoundedScryptHasher`` at
  ``CURRENT_PARAMS`` over ``LOGIN_SAMPLES`` logins (milliseconds).
"""

from __future__ import annotations

import json
import math
import statistics
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from agent_orchestrator.auth.http.middleware import classify
from agent_orchestrator.auth.passwords import CURRENT_PARAMS, BoundedScryptHasher
from agent_orchestrator.auth.policy import (
    DASHBOARD_COOKIE_ONLY_NAVIGATION,
    DASHBOARD_ROUTE_POLICIES,
)
from agent_orchestrator.auth.provider import Revalidation
from agent_orchestrator.auth.store import UserStoreFile, add_user
from agent_orchestrator.ui.service import DashboardService
from tests.auth.helpers import real_routes
from tests.auth.helpers.core import FakeClock, SeededEntropy, run_async
from tests.auth.helpers.real_routes import Dash, build_dash

dash_factory = real_routes.dash_factory  # keeps the module importable like its siblings

REQUEST_SAMPLES = 1000
LOGIN_SAMPLES = 20
PERCENTILE = 95
PASSWORD = "correct horse battery staple"
PROTECTED_PATH = "/api/workspace"
SEED = 29

pytestmark = pytest.mark.slow


@pytest.fixture(autouse=True)
def _opt_in(request: pytest.FixtureRequest) -> None:
    if "slow" not in str(request.config.getoption("-m", default="")):
        pytest.skip("informational NFR-5 measurement: run with -m slow (never a gate)")


def p95(samples: list[float]) -> float:
    """Nearest-rank 95th percentile."""
    ordered = sorted(samples)
    return ordered[max(math.ceil(PERCENTILE / 100 * len(ordered)) - 1, 0)]


def summarize(name: str, samples: list[float], unit: str) -> dict[str, float | str | int]:
    row: dict[str, float | str | int] = {
        "metric": name,
        "unit": unit,
        "samples": len(samples),
        "p50": round(statistics.median(samples), 3),
        "p95": round(p95(samples), 3),
        "max": round(max(samples), 3),
    }
    print(f"NFR-5 (informational) {json.dumps(row)}")
    return row


def timed(fn: Callable[[], object], repeat: int, scale: float) -> list[float]:
    samples: list[float] = []
    for _ in range(repeat):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * scale)
    return samples


def real_hash_dash(tmp_path: Path, service: DashboardService, hasher: BoundedScryptHasher) -> Dash:
    """A dashboard over a real runtime whose only user has a real ``CURRENT_PARAMS`` hash."""
    dash = build_dash(
        tmp_path,
        service,
        clock=FakeClock(),
        seed=SEED,
        users=(),
        hasher=hasher,
        address_threshold=10_000,
        lockout_threshold=10_000,
    )
    entropy = SeededEntropy(SEED)
    encoded = run_async(hasher.hash(PASSWORD))

    def seed(f: UserStoreFile) -> None:
        add_user(f, "alice", encoded, now=dash.clock.now_utc(), entropy=entropy)

    dash.runtime.store.mutate(seed, create=True)
    return dash


def test_nfr5_request_overhead_and_login_p95(
    tmp_path: Path,
    dashboard_service: DashboardService,
    record_property: Callable[[str, object], None],
) -> None:
    hasher = BoundedScryptHasher()
    try:
        dash = real_hash_dash(tmp_path, dashboard_service, hasher)

        # --- login p95 with the real hasher at CURRENT_PARAMS ------------------------------
        login_ms: list[float] = []
        for _ in range(LOGIN_SAMPLES):
            start = time.perf_counter()
            response = dash.login("alice", PASSWORD)
            login_ms.append((time.perf_counter() - start) * 1000)
            assert response.status_code == 200
        # the session of the last login drives the per-request measurements
        token, proof = dash.cookie_value, dash.proof
        assert token is not None and proof is not None

        # --- per-request overhead: classify + lookup + proof + revalidate -----------------
        scope = {
            "type": "http",
            "method": "GET",
            "path": PROTECTED_PATH,
            "root_path": "",
            "app": dash.app,
        }
        sessions, provider = dash.runtime.sessions, dash.runtime.provider

        def one_request() -> None:
            classify(scope, DASHBOARD_ROUTE_POLICIES, DASHBOARD_COOKIE_ONLY_NAVIGATION)
            record = sessions.lookup(token)
            assert record is not None and sessions.proof_matches(record, proof)
            assert provider.revalidate(record.user_id, record.credential_epoch) is (
                Revalidation.VALID
            )

        for _ in range(50):  # warm up caches and the interpreter
            one_request()
        overhead_us = timed(one_request, REQUEST_SAMPLES, 1_000_000)
        round_trip_ms = timed(lambda: dash.protected(), REQUEST_SAMPLES // 5, 1000)
    finally:
        hasher.close()

    rows = [
        summarize("per-request overhead: classify+lookup+proof+revalidate", overhead_us, "us"),
        summarize("authenticated TestClient round trip (context)", round_trip_ms, "ms"),
        summarize(f"login with real scrypt at ln={CURRENT_PARAMS.log2_n}", login_ms, "ms"),
    ]
    for row in rows:
        record_property(f"nfr5_{row['unit']}_p95_{str(row['metric'])[:24]}", row["p95"])
    # Sanity only: the measurements ran. NFR-5 is a target; no threshold is asserted here.
    assert [row["samples"] for row in rows] == [
        REQUEST_SAMPLES,
        REQUEST_SAMPLES // 5,
        LOGIN_SAMPLES,
    ]
