"""Event loop CLI entrypoint."""

from __future__ import annotations

import asyncio
import logging

from agentic_harness.event_loop.loop import EventLoop

logging.basicConfig(level=logging.INFO)


def main() -> None:
    loop = EventLoop()
    logging.info("Starting agentic harness event loop")
    asyncio.run(loop.run_forever())


if __name__ == "__main__":
    main()
