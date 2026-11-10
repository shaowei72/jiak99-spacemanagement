"""Outlook calendar access (SPEC §5.4).

- `model`: what a calendar event looks like to the app (dates as 'YYYY-MM-DD', times as minutes).
- `gateway`: the `GraphGateway` protocol every calendar implementation follows.
- `fake`: an in-memory calendar for development and tests (used unless GRAPH_MODE=real).
- `format`: the subject and body the app writes for a booking.
- `template`: reads the short subject sales type by hand, e.g. "[PHE] 11:00 · 10A 2C · Tan family".
- `ics`: reads (and writes) an .ics file saved from Outlook.
- `oldstyle`: the forgiving reader for the free-text bookings typed before the app.
- `takeover`: the review sheet for taking over the calendar once.
- `anonymise`: test data from the real calendar without its personal data.
"""
