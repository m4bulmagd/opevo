import logging

from app.core.redaction import SafeExtraFilter


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    for handler in logging.getLogger().handlers:
        if not any(isinstance(item, SafeExtraFilter) for item in handler.filters):
            handler.addFilter(SafeExtraFilter())
