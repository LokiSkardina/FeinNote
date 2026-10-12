# Responsible for: application logging in the portable data directory.

import logging
import os
from datetime import datetime
from utils.paths import get_data_path

def setup_logger(name='WhisperNote', log_dir='logs'):
    """Configure file and console logging; relative directories are app-local."""
    if not os.path.isabs(log_dir):
        log_dir = get_data_path(log_dir)
    os.makedirs(log_dir, exist_ok=True)
    
    logger = logging.getLogger(name)
    
    if logger.handlers:
        return logger
    
    logger.setLevel(logging.DEBUG)
    log_file = os.path.join(log_dir, f'whisper_note_{datetime.now().strftime("%Y%m%d")}.log')
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    
    formatter = logging.Formatter(
        '[%(asctime)s] [%(levelname)-8s] [%(name)s] [%(funcName)s:%(lineno)d] %(message)s',
        datefmt='%H:%M:%S'
    )
    
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger

def log_separator(logger, title="", char="=", length=80):
    if title:
        padding = (length - len(title) - 2) // 2
        line = char * padding + f" {title} " + char * padding
        if len(line) < length:
            line += char * (length - len(line))
    else:
        line = char * length
    
    logger.info(line)
