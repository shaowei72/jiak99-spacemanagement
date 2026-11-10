"""Outlook calendar access (SPEC §5.4).

- `model`: what a calendar event looks like to the app (dates as 'YYYY-MM-DD', times as minutes).
- `gateway`: the `GraphGateway` protocol every calendar implementation follows.
- `fake`: an in-memory calendar for development and tests (used unless GRAPH_MODE=real).
- `format`: the subject and body the app writes for a booking.
- `template`: reads the short subject sales type by hand, e.g. "[PHE] 11:00 · 10A 2C · Tan family".
"""
