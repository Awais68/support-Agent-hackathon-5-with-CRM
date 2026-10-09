"""X3: the worker shuts down cleanly on SIGTERM.

As PID 1 in its container the worker had no SIGTERM handler, so the signal
was ignored: `docker stop` and pod termination waited out the grace period
and SIGKILLed it (measured: 10.3s, exit 137). The consumers never left their
groups and the producer and pool were never closed.
"""

import asyncio
import os
import signal

from workers import message_processor as mp


async def test_sigterm_cancels_tasks_and_returns():
    run = mp.run_until_signalled
    cleaned_up = asyncio.Event()

    async def forever():
        try:
            await asyncio.Event().wait()
        finally:
            cleaned_up.set()

    asyncio.get_running_loop().call_later(0.1, os.kill, os.getpid(), signal.SIGTERM)
    await asyncio.wait_for(run([forever()]), timeout=5)
    assert cleaned_up.is_set()


async def test_handlers_are_removed_afterwards():
    async def done():
        return None

    await mp.run_until_signalled([done()])
    assert signal.getsignal(signal.SIGTERM) in (signal.SIG_DFL, None)
