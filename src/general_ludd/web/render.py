"""Offline-render compatibility exports for the current toolkit."""

from general_ludd.web.toolkit import OfflineRenderer
from general_ludd.web.tools import render_js

render_page = render_js

__all__ = ["OfflineRenderer", "render_js", "render_page"]
