import logging
import logging.handlers
import os
from datetime import datetime

LOG_DIR = "logs"
os.makedirs(LOG_DIR,exist_ok=True)

def setup_logging():
    log_file = os.path.join(
        LOG_DIR,
        f"{datetime.now().strftime('%y-%m-%d_%H-%M-%S')}.log"
    )

    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)

    if logger.hasHandlers():
        logger.handlers.clear()

    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=10*1024*1024,
        backupCount=5
    )

    file_handler.setLevel(logging.DEBUG)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)

    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s] -%(message)s",
        "%y-%m-%d %H:%M:%S"
    )

    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger

