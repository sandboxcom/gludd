"""Knowledge module: postal / delivery services by jurisdiction.

Wire format (list of dicts):
    {"country": "US", "service": "USPS", "tracked": true, "notes": "..."}
"""

POSTAL_DELIVERY: list[dict[str, object]] = []
