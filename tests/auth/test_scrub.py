"""T-Hd4wQ2: ``auth/scrub.py`` (HLD 11.13): ``redact``, the filter and the record factory."""

from __future__ import annotations

import io
import logging
from collections.abc import Iterator

import pytest

from agent_orchestrator.auth import scrub
from agent_orchestrator.auth.scrub import (
    REDACTED,
    UNFORMATTABLE_MESSAGE,
    SecretRedactingFilter,
    auth_logger,
    install_log_redaction,
    redact,
)

COOKIE_VALUE = "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0UvW"  # 43 chars
assert len(COOKIE_VALUE) == 43
BASE32_SECRET = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"
assert len(BASE32_SECRET) == 32
CODE_16 = "ABCD-EFGH-JKMN-PQRS"
HASH = "$scrypt$ln=15,r=8,p=1$c2FsdHNhbHQ$aGFzaGhhc2hoYXNo"


class TestRedact:
    @pytest.mark.parametrize(
        ("text", "kept"),
        [
            (f"ao_sid_8765={COOKIE_VALUE}", "ao_sid_8765="),
            (f"Cookie: __Host-ao_sid_8765={COOKIE_VALUE}; other=1", "__Host-ao_sid_8765="),
            (f"X-AO-Session-Proof: {COOKIE_VALUE}", "X-AO-Session-Proof: "),
            (f"x-ao-session-proof={COOKIE_VALUE}", "x-ao-session-proof="),
            ('"password": "hunter2 with space"', '"password": '),
            ("password=hunter2", "password="),
            ("current_password=hunter2", "current_password="),
            ("new_password=hunter2", "new_password="),
            ("code=123456", "code="),
            ('"session_proof": "abc"', '"session_proof": '),
            ('"enrollment_token": "abc"', '"enrollment_token": '),
            ("recovery_code=whatever", "recovery_code="),
            ("PASSWORD: hunter2", "PASSWORD: "),
        ],
    )
    def test_keeps_the_prefix_and_hides_the_value(self, text: str, kept: str) -> None:
        out = redact(text)
        assert REDACTED in out
        assert out.startswith(kept) or kept in out
        for secret in (COOKIE_VALUE, "hunter2", "123456", '"abc"', "whatever"):
            assert secret not in out

    @pytest.mark.parametrize(
        "secret",
        [
            "otpauth://totp/ao:alice?secret=JBSWY3DPEHPK3PXP&issuer=ao",
            HASH,
            BASE32_SECRET,
            CODE_16,
        ],
    )
    def test_whole_match_patterns(self, secret: str) -> None:
        out = redact(f"before {secret} after")
        assert out == f"before {REDACTED} after" or out.startswith("before [REDACTED]")
        assert secret not in out

    def test_hash_after_a_key_is_still_redacted_once(self) -> None:
        out = redact(f"password={HASH}")
        assert out == f"password={REDACTED}"

    @pytest.mark.parametrize(
        "text",
        [
            "run-20261004-abc started",
            "GET /api/files?path=src/main.py 200",
            "workspace /home/user/project/.ao/config.yaml loaded",
            "task build finished in 1.2s",
            "",
        ],
    )
    def test_ordinary_text_is_byte_identical(self, text: str) -> None:
        assert redact(text) == text

    def test_is_idempotent(self) -> None:
        once = redact(f"password=x {CODE_16} {BASE32_SECRET}")
        assert redact(once) == once

    def test_short_cookie_like_value_is_left_alone(self) -> None:
        assert redact("ao_sid_8765=short") == "ao_sid_8765=short"


@pytest.fixture()
def restore_factory() -> Iterator[None]:
    original = logging.getLogRecordFactory()
    yield
    logging.setLogRecordFactory(original)


class TestFilter:
    def test_redacts_a_record_logged_with_arguments(self) -> None:
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger = auth_logger("ao.test.scrub.filter")
        logger.propagate = False
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        try:
            logger.info("login password=%s from %s", "hunter2", "10.0.0.1")
        finally:
            logger.removeHandler(handler)
        out = stream.getvalue()
        assert "hunter2" not in out
        assert f"password={REDACTED}" in out
        assert "10.0.0.1" in out

    def test_auth_logger_attaches_exactly_one_filter(self) -> None:
        name = "ao.test.scrub.idempotent"
        first = auth_logger(name)
        second = auth_logger(name)
        assert first is second
        assert [f for f in first.filters if isinstance(f, SecretRedactingFilter)] != []
        assert len(first.filters) == 1

    def test_filter_never_drops_a_record(self) -> None:
        record = logging.LogRecord("n", logging.INFO, __file__, 1, "plain", None, None)
        assert SecretRedactingFilter().filter(record) is True
        assert record.getMessage() == "plain"


class _Exploding:
    def __str__(self) -> str:
        raise RuntimeError("boom with token=SECRETVALUE")


class TestRecordFactory:
    def test_redacts_a_non_propagating_logger(self, restore_factory: None) -> None:
        install_log_redaction()
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger("uvicorn.error")
        old_propagate, logger.propagate = logger.propagate, False
        logger.addHandler(handler)
        old_level = logger.level
        logger.setLevel(logging.INFO)
        try:
            logger.info("cookie ao_sid_1=%s", COOKIE_VALUE)
        finally:
            logger.removeHandler(handler)
            logger.propagate = old_propagate
            logger.setLevel(old_level)
        assert REDACTED in stream.getvalue()
        assert COOKIE_VALUE not in stream.getvalue()

    def test_install_is_idempotent_and_chains_the_previous_factory(
        self, restore_factory: None
    ) -> None:
        calls: list[int] = []
        base = logging.getLogRecordFactory()

        def spy(*args: object, **kwargs: object) -> logging.LogRecord:
            calls.append(1)
            return base(*args, **kwargs)  # type: ignore[arg-type]

        logging.setLogRecordFactory(spy)
        install_log_redaction()
        installed = logging.getLogRecordFactory()
        install_log_redaction()
        assert logging.getLogRecordFactory() is installed
        assert getattr(installed, scrub._FACTORY_MARKER) is True
        record = logging.getLogger("ao.test.scrub.factory").makeRecord(
            "n", logging.INFO, __file__, 1, "token=%s", ("abc",), None
        )
        assert calls == [1]  # exactly one chained call per created record
        assert record.getMessage() == f"token={REDACTED}"

    def test_stacked_wrapper_does_not_rewrite_an_already_redacted_record(
        self, restore_factory: None
    ) -> None:
        install_log_redaction()
        inner = logging.getLogRecordFactory()

        def outer(*args: object, **kwargs: object) -> logging.LogRecord:
            return inner(*args, **kwargs)  # type: ignore[arg-type]

        logging.setLogRecordFactory(outer)
        install_log_redaction()  # no marker on `outer`, so it wraps again: must stay harmless
        record = logging.getLogger("x").makeRecord(
            "x", logging.INFO, __file__, 1, "token=%s", ("abc",), None
        )
        assert record.getMessage() == f"token={REDACTED}"

    def test_unformattable_message_is_withheld_with_one_error(
        self, restore_factory: None, caplog: pytest.LogCaptureFixture
    ) -> None:
        install_log_redaction()
        logger = logging.getLogger("ao.test.scrub.unformattable")
        with caplog.at_level(logging.ERROR, logger=scrub.__name__):
            record = logger.makeRecord(
                logger.name, logging.INFO, __file__, 1, "value=%s", (_Exploding(),), None
            )
        assert record.getMessage() == UNFORMATTABLE_MESSAGE
        assert "SECRETVALUE" not in record.getMessage()
        errors = [r for r in caplog.records if r.name == scrub.__name__]
        assert len(errors) == 1
        assert errors[0].levelno == logging.ERROR
        assert "SECRETVALUE" not in errors[0].getMessage()

    def test_filter_also_withholds_an_unformattable_message(self) -> None:
        record = logging.LogRecord("n", logging.INFO, __file__, 1, "%s", (_Exploding(),), None)
        SecretRedactingFilter().filter(record)
        assert record.getMessage() == UNFORMATTABLE_MESSAGE


class TestUvicornAccessRecords:
    """T-2wE08U H2: the process-wide factory must not break uvicorn's positional access args."""

    ACCESS_MSG = '%s - "%s %s HTTP/%s" %d'

    def _record(self, path: str) -> logging.LogRecord:
        logger = logging.getLogger("uvicorn.access")
        return logger.makeRecord(
            logger.name,
            logging.INFO,
            __file__,
            1,
            self.ACCESS_MSG,
            ("127.0.0.1:5000", "GET", path, "1.1", 200),
            None,
        )

    def _format(self, record: logging.LogRecord) -> str:
        uvicorn_logging = pytest.importorskip("uvicorn.logging")
        return str(uvicorn_logging.AccessFormatter(use_colors=False).format(record))

    def test_a_clean_access_record_is_left_untouched(self, restore_factory: None) -> None:
        install_log_redaction()
        record = self._record("/api/health")
        assert record.msg == self.ACCESS_MSG
        assert record.args == ("127.0.0.1:5000", "GET", "/api/health", "1.1", 200)
        assert '"GET /api/health HTTP/1.1" 200' in self._format(record)

    def test_a_secret_in_the_path_is_redacted_and_the_args_keep_their_shape(
        self, restore_factory: None
    ) -> None:
        install_log_redaction()
        record = self._record(f"/login?password=hunter2&x=1 {BASE32_SECRET}")
        assert isinstance(record.args, tuple) and len(record.args) == 5
        formatted = self._format(record)  # must not raise: the formatter unpacks the 5-tuple
        assert "hunter2" not in formatted and BASE32_SECRET not in formatted
        assert REDACTED in formatted

    @pytest.mark.parametrize(
        "path", ["/x?code=", "/x?password=", "/x?token=&x=1", "/x?secret=&code=&password="]
    )
    def test_n1_an_empty_value_key_keeps_the_access_args_shape(
        self, path: str, restore_factory: None
    ) -> None:
        # The formatted line's `\s*\S+` runs past the empty value into " HTTP/1.1", so the
        # per-arg and formatted-line results disagree; the fallback must keep the 5-tuple.
        install_log_redaction()
        record = self._record(path)
        assert isinstance(record.args, tuple) and len(record.args) == 5
        assert record.args[4] == 200  # the status stays an int for AccessFormatter
        formatted = self._format(record)  # must not raise
        assert "Logging error" not in formatted
        assert "200" in formatted

    def test_n1_a_secret_spread_over_args_never_leaks_in_the_fallback(
        self, restore_factory: None
    ) -> None:
        install_log_redaction()
        record = self._record(f"/x?code= {BASE32_SECRET}")
        assert BASE32_SECRET not in self._format(record)

    def test_n1_the_fallback_keeps_a_tuple_args_length_and_non_string_slots(self) -> None:
        record = logging.LogRecord(
            "n", logging.INFO, __file__, 1, "%s %s %d", ("a", "code=", 7), None
        )
        SecretRedactingFilter().filter(record)
        assert isinstance(record.args, tuple) and len(record.args) == 3
        assert record.args[2] == 7
        assert record.getMessage()  # formats without raising

    def test_n1_the_fallback_does_not_keep_an_object_that_hides_a_secret(self) -> None:
        class Holder:
            def __str__(self) -> str:
                return "password=hunter2"

        record = logging.LogRecord("n", logging.INFO, __file__, 1, "%s %s", ("a", Holder()), None)
        SecretRedactingFilter().filter(record)
        assert "hunter2" not in record.getMessage()
        assert isinstance(record.args, tuple) and len(record.args) == 2

    def test_n1_a_percent_in_the_message_survives_the_fallback(self) -> None:
        record = logging.LogRecord(
            "n", logging.INFO, __file__, 1, "100%% %s then", ("password=",), None
        )
        SecretRedactingFilter().filter(record)
        assert "100%" in record.getMessage()

    def test_the_filter_keeps_a_dict_args_shape(self) -> None:
        record = logging.LogRecord(
            "n",
            logging.INFO,
            __file__,
            1,
            "%(who)s token=%(tok)s",
            ({"who": "a", "tok": "zz"},),
            None,
        )
        SecretRedactingFilter().filter(record)
        assert isinstance(record.args, dict)
        assert record.getMessage() == f"a token={REDACTED}"

    def test_a_secret_hidden_in_a_non_string_arg_falls_back_to_a_flat_message(self) -> None:
        class Holder:
            def __str__(self) -> str:
                return "password=hunter2"

        record = logging.LogRecord("n", logging.INFO, __file__, 1, "got %s", (Holder(),), None)
        SecretRedactingFilter().filter(record)
        assert record.getMessage() == f"got password={REDACTED}"
