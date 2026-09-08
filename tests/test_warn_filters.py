import sys
import warnings

from apatch.warn_filters import configure_apatch_warnings


def test_configure_idempotent():
    configure_apatch_warnings()
    configure_apatch_warnings()


def test_suppresses_langchain_pydantic_v1_warning_on_314(monkeypatch):
    if sys.version_info < (3, 14):
        return
    # catch_warnings restores filter state on exit, so we can safely reset
    # within the block and re-run configure() to assert the ignore filter lands.
    with warnings.catch_warnings():
        warnings.resetwarnings()
        import apatch.warn_filters as wf

        monkeypatch.setattr(wf, "_CONFIGURED", False)
        wf.configure_apatch_warnings()
        message = "Core Pydantic V1 functionality isn't compatible with Python 3.14 or greater."
        matched = [
            f
            for f in warnings.filters
            if f[0] == "ignore"
            and f[2] is UserWarning
            and f[1] is not None
            and f[1].search(message)
        ]
        assert matched, "configure_apatch_warnings did not install the suppress filter"
