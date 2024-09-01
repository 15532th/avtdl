import logging

from avtdl.core.info import generate_plugins_description
from avtdl.core.loggers import silence_library_loggers


def test_config_loading(caplog):
    """
    Smoke test for plugins

    Load all plugins by generating docs,
    check it produces no warnings in log
    """
    silence_library_loggers()
    caplog.set_level(logging.WARNING)

    _ = generate_plugins_description(as_html=False)

    assert not caplog.records
