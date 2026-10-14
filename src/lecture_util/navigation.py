"""Contextual Vim navigation shared by Textual apps and screens."""
from __future__ import annotations

from textual.app import App
from textual.binding import Binding
from textual.widgets import DataTable, Input, OptionList, TextArea

VIM_BINDINGS = [
    Binding("j", "vim_move('next')", show=False, priority=True),
    Binding("k", "vim_move('previous')", show=False, priority=True),
    Binding("ctrl+j", "vim_focus('next')", show=False, priority=True),
    Binding("ctrl+k", "vim_focus('previous')", show=False, priority=True),
]


class VimNavigation:
    """Keep printable navigation keys available for normal text editing."""

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action in {"vim_move", "vim_focus"}:
            if isinstance(self, App):
                if self.screen is not self.screen_stack[0]:
                    return False
            elif self is not self.app.screen:
                return False
            return action == "vim_focus" or not isinstance(
                self.focused, (Input, TextArea)
            )
        return super().check_action(action, parameters)

    def action_vim_move(self, direction: str) -> None:
        focused = self.focused
        if isinstance(focused, (OptionList, DataTable)):
            if direction == "previous":
                focused.action_cursor_up()
            else:
                focused.action_cursor_down()
        else:
            self.action_vim_focus(direction)

    def action_vim_focus(self, direction: str) -> None:
        screen = self.screen if isinstance(self, App) else self
        if direction == "previous":
            screen.focus_previous()
        else:
            screen.focus_next()
