"""
IRIS CLI entry point.
Run an agent task from the command line:

    python main.py "open notepad and type hello"

With no argument it starts an interactive loop. Task text is redacted in
logs; credentials belong in the credential store (see tools/credentials.py),
never in task strings.
"""

import sys

from agent.agent import run_agent
from logging_config import setup_logging
import logging

setup_logging()
logger = logging.getLogger(__name__)

logger.info("=" * 60)
logger.info("IRIS System Starting...")
logger.info("=" * 60)


def main() -> None:
    if len(sys.argv) > 1:
        task = " ".join(sys.argv[1:])
        observations, _steps = run_agent(task)
        for o in observations:
            print(o)
        return

    print("IRIS interactive mode — type 'exit' to quit, 'tools' to list tools.")
    while True:
        try:
            task = input("\niris> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not task:
            continue
        if task.lower() in ("exit", "quit"):
            break
        if task.lower() == "tools":
            from tools import TOOLS
            print("\n".join(sorted(TOOLS)))
            continue
        observations, _steps = run_agent(task)
        for o in observations:
            print(o)


if __name__ == "__main__":
    main()
