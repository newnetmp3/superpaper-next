"""Custom-painted, keyboard-accessible Wallpaper Studio controls.

Kept independent of profile persistence, rendering and the settings panel.
"""

import wx  # pyright: ignore[reportMissingImports]  # ty:ignore[unresolved-import]


class StudioNavigationButton(wx.Control):
    """Theme-independent sidebar entry, drawn consistently under GTK and KDE."""

    def __init__(self, parent, label, icon):
        super().__init__(parent, style=wx.BORDER_NONE)
        self.label = label
        self.icon = icon
        self.selected = False
        self.hovered = False
        self.SetMinSize(wx.Size(148, 39))
        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)
        self.SetCursor(wx.Cursor(wx.CURSOR_HAND))
        self.Bind(wx.EVT_PAINT, self._paint)
        self.Bind(wx.EVT_ENTER_WINDOW, self._enter)
        self.Bind(wx.EVT_LEAVE_WINDOW, self._leave)
        self.Bind(wx.EVT_LEFT_UP, self._activate)
        self.Bind(wx.EVT_KEY_DOWN, self._key_down)

    def SetSelected(self, selected):
        self.selected = selected
        self.Refresh()

    def _enter(self, event):
        self.hovered = True
        self.Refresh()

    def _leave(self, event):
        self.hovered = False
        self.Refresh()

    def _activate(self, event):
        clicked = wx.CommandEvent(wx.wxEVT_BUTTON, self.GetId())
        clicked.SetEventObject(self)
        wx.PostEvent(self, clicked)

    def _key_down(self, event):
        if event.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_SPACE):
            self._activate(event)
        else:
            event.Skip()

    def _paint(self, event):
        dc = wx.AutoBufferedPaintDC(self)
        w, h = self.GetClientSize()
        dc.SetBackground(wx.Brush(wx.Colour(25, 34, 48)))
        dc.Clear()
        background = (
            wx.Colour(35, 110, 207)
            if self.selected
            else wx.Colour(41, 58, 79)
            if self.hovered
            else wx.Colour(25, 34, 48)
        )
        dc.SetBrush(wx.Brush(background))
        dc.SetPen(wx.Pen(wx.Colour(61, 136, 234) if self.selected else background))
        dc.DrawRoundedRectangle(3, 2, max(1, w - 6), max(1, h - 4), 7)
        dc.SetTextForeground(wx.Colour(247, 250, 255) if self.selected else wx.Colour(206, 219, 235))
        font = self.GetFont()
        font.SetWeight(wx.FONTWEIGHT_BOLD if self.selected else wx.FONTWEIGHT_NORMAL)
        dc.SetFont(font)
        icon_w, icon_h = dc.GetTextExtent(self.icon)
        dc.DrawText(self.icon, 15, max(0, (h - icon_h) // 2))
        _, text_h = dc.GetTextExtent(self.label)
        dc.DrawText(self.label, max(39, icon_w + 21), max(0, (h - text_h) // 2))


class StudioActionButton(StudioNavigationButton):
    """A consistent, accessible painted action for the primary Studio commands."""

    def __init__(self, parent, label, primary=False):
        super().__init__(parent, label, "")
        self.primary = primary
        self.SetMinSize(wx.Size(max(96, len(label) * 8 + 26), 37))

    def SetLabel(self, label):
        self.label = label
        self.Refresh()

    def _paint(self, event):
        dc = wx.AutoBufferedPaintDC(self)
        w, h = self.GetClientSize()
        dc.SetBackground(wx.Brush(wx.Colour(17, 27, 41)))
        dc.Clear()
        if self.primary:
            background = wx.Colour(48, 132, 241) if self.hovered else wx.Colour(34, 107, 215)
        else:
            background = wx.Colour(57, 75, 98) if self.hovered else wx.Colour(43, 57, 75)
        dc.SetPen(wx.Pen(wx.Colour(72, 99, 131) if not self.primary else wx.Colour(67, 148, 246)))
        dc.SetBrush(wx.Brush(background))
        dc.DrawRoundedRectangle(2, 2, max(1, w - 4), max(1, h - 4), 6)
        font = self.GetFont()
        font.SetWeight(wx.FONTWEIGHT_BOLD if self.primary else wx.FONTWEIGHT_NORMAL)
        dc.SetFont(font)
        dc.SetTextForeground(wx.Colour(247, 251, 255))
        text_w, text_h = dc.GetTextExtent(self.label)
        dc.DrawText(self.label, max(0, (w - text_w) // 2), max(0, (h - text_h) // 2))


class StudioSegmentButton(StudioActionButton):
    """Compact painted tabs that retain keyboard and mouse button semantics."""

    def __init__(self, parent, label):
        super().__init__(parent, label)
        # Two columns fit inside the 294px Processing inspector on GTK.
        self.SetMinSize(wx.Size(112, 35))

    def SetSelected(self, selected):
        self.primary = bool(selected)
        super().SetSelected(selected)
