# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.


"""ovstream (NVST) key codes to ImGui keys, for feeding keyboard events into ovui."""

from __future__ import annotations

# ImGuiKey values (Dear ImGui 1.87+ named keys).
_TAB, _LEFT, _RIGHT, _UP, _DOWN, _PAGE_UP, _PAGE_DOWN, _HOME, _END, _INSERT, _DELETE, _BACKSPACE, \
    _SPACE, _ENTER, _ESCAPE, _LCTRL, _LSHIFT, _LALT, _LSUPER, _RCTRL, _RSHIFT, _RALT, _RSUPER = range(512, 535)
_KEY_0, _KEY_A, _F1 = 536, 546, 572
_APOSTROPHE, _COMMA, _MINUS, _PERIOD, _SLASH, _SEMICOLON, _EQUAL, _LBRACKET, _BACKSLASH, _RBRACKET, \
    _GRAVE, _CAPS_LOCK, _SCROLL_LOCK, _NUM_LOCK, _PRINT_SCREEN, _PAUSE, _KEYPAD_0 = range(596, 613)
_KP_DECIMAL, _KP_DIVIDE, _KP_MULTIPLY, _KP_SUBTRACT, _KP_ADD, _KP_ENTER = range(622, 628)

_TABLE = {
    0x0020: _SPACE, 0x0027: _APOSTROPHE, 0x002C: _COMMA, 0x002D: _MINUS, 0x002E: _PERIOD, 0x002F: _SLASH,
    0x003B: _SEMICOLON, 0x003D: _EQUAL, 0x005B: _LBRACKET, 0x005C: _BACKSLASH, 0x005D: _RBRACKET,
    0x00C0: _GRAVE, 0x00D7: _KP_MULTIPLY, 0x00F7: _KP_DIVIDE,
    0x0100: _ESCAPE, 0x0101: _TAB, 0x0102: _TAB, 0x0103: _BACKSPACE, 0x0104: _ENTER, 0x0105: _ENTER,
    0x0106: _INSERT, 0x0107: _DELETE, 0x0108: _PAUSE, 0x0109: _PRINT_SCREEN,
    0x0200: _HOME, 0x0201: _END, 0x0202: _LEFT, 0x0203: _UP, 0x0204: _RIGHT, 0x0205: _DOWN,
    0x0206: _PAGE_UP, 0x0207: _PAGE_DOWN,
    0x0301: _LSHIFT, 0x0302: _LSHIFT, 0x0303: _RSHIFT, 0x0304: _LCTRL, 0x0305: _LCTRL, 0x0306: _RCTRL,
    0x0307: _LALT, 0x0308: _LALT, 0x0309: _RALT, 0x0310: _LSUPER, 0x0311: _LSUPER, 0x0312: _RSUPER,
    0x0501: _CAPS_LOCK, 0x0502: _NUM_LOCK, 0x0503: _SCROLL_LOCK,
    0x060A: _KP_ADD, 0x060B: _KP_SUBTRACT, 0x060C: _KP_DECIMAL,
    0x060D: _INSERT, 0x060E: _END, 0x060F: _DOWN, 0x0610: _PAGE_DOWN, 0x0611: _LEFT, 0x0613: _RIGHT,
    0x0614: _HOME, 0x0615: _UP, 0x0616: _PAGE_UP, 0x0617: _DELETE,
}
_TABLE.update({0x0030 + i: _KEY_0 + i for i in range(10)})       # digits
_TABLE.update({0x0041 + i: _KEY_A + i for i in range(26)})       # letters
_TABLE.update({0x0400 + i: _F1 + i for i in range(24)})          # F1-F24
_TABLE.update({0x0600 + i: _KEYPAD_0 + i for i in range(10)})    # keypad digits

MOD_SHIFT, MOD_CONTROL, MOD_ALT, MOD_CAPS_LOCK = 0x0001, 0x0002, 0x0004, 0x0010


def to_imgui_key(key_code: int) -> int:
    """ImGuiKey for an NVST key code, or 0 when there is no equivalent."""
    return _TABLE.get(int(key_code), 0)


def printable(key_code: int, modifiers: int) -> str:
    """The ASCII character a key press types, or '' (Ctrl/Alt combinations type nothing).

    Used only when the client does not deliver composed text itself."""
    if modifiers & (MOD_CONTROL | MOD_ALT):
        return ""
    if key_code == 0x0020:
        return " "
    if 0x0030 <= key_code <= 0x0039:
        return chr(key_code)
    if 0x0041 <= key_code <= 0x005A:
        upper = bool(modifiers & MOD_SHIFT) ^ bool(modifiers & MOD_CAPS_LOCK)
        return chr(key_code) if upper else chr(key_code + 32)
    return ""
