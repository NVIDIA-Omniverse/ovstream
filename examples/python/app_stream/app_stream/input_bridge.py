# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.

"""Thread-safe hand-off of events and commands from ovstream's worker thread to the main loop."""

from __future__ import annotations

import queue
from typing import Any, Callable, List


class InputBridge:
    """Queues input events and commands; the main loop drains once per tick."""

    def __init__(self):
        self._events: "queue.Queue[Any]" = queue.Queue()
        self._commands: "queue.Queue[Callable[[], None]]" = queue.Queue()
        self.resize_handler: Callable[[int, int], None] = lambda w, h: None
        self.set_handler: Callable[[Any], None] = lambda payload: None

    # Called from the streaming worker thread.
    def on_input(self, event) -> None:
        self._events.put(event)

    def on_unicode(self, text: str) -> None:
        """Composed text from the client; queued as a str among the input events."""
        self._events.put(text)

    def post(self, command: Callable[[], None]) -> None:
        self._commands.put(command)

    def on_resize_request(self, width: int, height: int) -> None:
        self._commands.put(lambda: self.resize_handler(width, height))

    def on_set_command(self, payload: Any) -> None:
        """`app_stream:set` from a browser client; applied on the main thread."""
        self._commands.put(lambda: self.set_handler(payload))

    # Called on the main thread.
    def drain_events(self) -> List[Any]:
        out = []
        while True:
            try:
                out.append(self._events.get_nowait())
            except queue.Empty:
                return out

    def run_commands(self) -> int:
        n = 0
        while True:
            try:
                self._commands.get_nowait()()
                n += 1
            except queue.Empty:
                return n
