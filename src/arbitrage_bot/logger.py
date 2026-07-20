import logging
import sys
from rich.logging import RichHandler

def setup_logger() -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True, markup=True)],
    )
    logger = logging.getLogger("arb_bot")
    logger.setLevel(logging.INFO)
    return logger

logger = setup_logger()