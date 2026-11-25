"""Read-only HTTP API over ``as_of`` queries, and the time-slider demo page.

``Api`` answers requests, ``make_server`` serves it with the standard library, and
``vintage-lakehouse serve`` starts both on a stored warehouse. See
docs/adr/0012-read-only-api-and-demo.md.
"""

from vintage_lakehouse.api.app import Api, Response, make_server

__all__ = ["Api", "Response", "make_server"]
