"""Travel globe knowledge registry — airports, cities, IATA codes.

Lookup dicts for 60+ major airports:
  - city_to_iata     city/name -> IATA code
  - iata_to_name     IATA -> descriptive name
  - iata_to_city     IATA -> city name
  - iata_to_country  IATA -> ISO-3166 alpha-3 country code
  - country_to_cities ISO-3166 alpha-3 -> list of city names
  - country_to_airports ISO-3166 alpha-3 -> list of IATA codes
  - country_name     ISO-3166 alpha-3 -> full country name
  - city_country     city -> ISO-3166 alpha-3

Also provides helper functions:
  - resolve_iata     resolve a string (city or IATA) to an IATA code
  - nearest_airport  find the nearest airport from the route graph
"""

from __future__ import annotations

_AIRPORT_RECORDS: list[tuple[str, str, str, str]] = [
    ("JFK", "New York", "USA", "John F Kennedy International"),
    ("LGA", "New York", "USA", "LaGuardia"),
    ("EWR", "Newark", "USA", "Newark Liberty International"),
    ("LAX", "Los Angeles", "USA", "Los Angeles International"),
    ("SFO", "San Francisco", "USA", "San Francisco International"),
    ("ORD", "Chicago", "USA", "O'Hare International"),
    ("MDW", "Chicago", "USA", "Chicago Midway"),
    ("ATL", "Atlanta", "USA", "Hartsfield-Jackson Atlanta"),
    ("DFW", "Dallas", "USA", "Dallas/Fort Worth International"),
    ("IAH", "Houston", "USA", "George Bush Intercontinental"),
    ("MIA", "Miami", "USA", "Miami International"),
    ("BOS", "Boston", "USA", "Logan International"),
    ("SEA", "Seattle", "USA", "Seattle-Tacoma International"),
    ("DEN", "Denver", "USA", "Denver International"),
    ("PHX", "Phoenix", "USA", "Phoenix Sky Harbor"),
    ("LAS", "Las Vegas", "USA", "Harry Reid International"),
    ("MCO", "Orlando", "USA", "Orlando International"),
    ("IAD", "Washington", "USA", "Washington Dulles International"),
    ("DCA", "Washington", "USA", "Ronald Reagan Washington National"),
    ("HNL", "Honolulu", "USA", "Daniel K Inouye International"),
    ("LHR", "London", "GBR", "London Heathrow"),
    ("LGW", "London", "GBR", "London Gatwick"),
    ("CDG", "Paris", "FRA", "Paris Charles de Gaulle"),
    ("ORY", "Paris", "FRA", "Paris Orly"),
    ("FRA", "Frankfurt", "DEU", "Frankfurt Airport"),
    ("MUC", "Munich", "DEU", "Munich Airport"),
    ("AMS", "Amsterdam", "NLD", "Amsterdam Schiphol"),
    ("MAD", "Madrid", "ESP", "Adolfo Suárez Madrid-Barajas"),
    ("BCN", "Barcelona", "ESP", "Barcelona-El Prat"),
    ("FCO", "Rome", "ITA", "Rome Fiumicino"),
    ("MXP", "Milan", "ITA", "Milan Malpensa"),
    ("ZRH", "Zurich", "CHE", "Zurich Airport"),
    ("VIE", "Vienna", "AUT", "Vienna International"),
    ("IST", "Istanbul", "TUR", "Istanbul Airport"),
    ("DXB", "Dubai", "ARE", "Dubai International"),
    ("AUH", "Abu Dhabi", "ARE", "Abu Dhabi International"),
    ("DOH", "Doha", "QAT", "Hamad International"),
    ("SIN", "Singapore", "SGP", "Singapore Changi"),
    ("NRT", "Tokyo", "JPN", "Tokyo Narita"),
    ("HND", "Tokyo", "JPN", "Tokyo Haneda"),
    ("KIX", "Osaka", "JPN", "Kansai International"),
    ("PVG", "Shanghai", "CHN", "Shanghai Pudong"),
    ("SHA", "Shanghai", "CHN", "Shanghai Hongqiao"),
    ("PEK", "Beijing", "CHN", "Beijing Capital International"),
    ("PKX", "Beijing", "CHN", "Beijing Daxing"),
    ("HKG", "Hong Kong", "HKG", "Hong Kong International"),
    ("ICN", "Seoul", "KOR", "Seoul Incheon"),
    ("GMP", "Seoul", "KOR", "Seoul Gimpo"),
    ("BKK", "Bangkok", "THA", "Suvarnabhumi Airport"),
    ("DMK", "Bangkok", "THA", "Don Mueang International"),
    ("KUL", "Kuala Lumpur", "MYS", "Kuala Lumpur International"),
    ("CGK", "Jakarta", "IDN", "Soekarno-Hatta International"),
    ("MNL", "Manila", "PHL", "Ninoy Aquino International"),
    ("SYD", "Sydney", "AUS", "Sydney Kingsford Smith"),
    ("MEL", "Melbourne", "AUS", "Melbourne Airport"),
    ("AKL", "Auckland", "NZL", "Auckland Airport"),
    ("YYZ", "Toronto", "CAN", "Toronto Pearson International"),
    ("YVR", "Vancouver", "CAN", "Vancouver International"),
    ("YUL", "Montreal", "CAN", "Montreal-Trudeau International"),
    ("MEX", "Mexico City", "MEX", "Mexico City International"),
    ("GRU", "Sao Paulo", "BRA", "São Paulo-Guarulhos"),
    ("EZE", "Buenos Aires", "ARG", "Ministro Pistarini International"),
    ("SCL", "Santiago", "CHL", "Arturo Merino Benítez"),
    ("CPT", "Cape Town", "ZAF", "Cape Town International"),
    ("JNB", "Johannesburg", "ZAF", "O.R. Tambo International"),
    ("NBO", "Nairobi", "KEN", "Jomo Kenyatta International"),
    ("CAI", "Cairo", "EGY", "Cairo International"),
    ("DEL", "Delhi", "IND", "Indira Gandhi International"),
    ("BOM", "Mumbai", "IND", "Chhatrapati Shivaji Maharaj"),
    ("SVO", "Moscow", "RUS", "Sheremetyevo International"),
]

_COUNTRY_NAMES: dict[str, str] = {
    "ARE": "United Arab Emirates",
    "ARG": "Argentina",
    "AUS": "Australia",
    "AUT": "Austria",
    "BRA": "Brazil",
    "CAN": "Canada",
    "CHE": "Switzerland",
    "CHL": "Chile",
    "CHN": "China",
    "DEU": "Germany",
    "EGY": "Egypt",
    "ESP": "Spain",
    "FRA": "France",
    "GBR": "United Kingdom",
    "HKG": "Hong Kong",
    "IDN": "Indonesia",
    "IND": "India",
    "ITA": "Italy",
    "JPN": "Japan",
    "KEN": "Kenya",
    "KOR": "South Korea",
    "MEX": "Mexico",
    "MYS": "Malaysia",
    "NLD": "Netherlands",
    "NZL": "New Zealand",
    "PHL": "Philippines",
    "QAT": "Qatar",
    "RUS": "Russia",
    "SGP": "Singapore",
    "THA": "Thailand",
    "TUR": "Turkey",
    "USA": "United States",
    "ZAF": "South Africa",
}

city_country: dict[str, str] = {}
city_to_iata: dict[str, str] = {}
iata_to_name: dict[str, str] = {}
iata_to_city: dict[str, str] = {}
iata_to_country: dict[str, str] = {}
country_to_airports: dict[str, list[str]] = {}
country_to_cities: dict[str, list[str]] = {}
country_name: dict[str, str] = dict(_COUNTRY_NAMES)

for _iata, _city, _cc, _name in _AIRPORT_RECORDS:
    _city_key = _city.upper()
    city_country.setdefault(_city_key, _cc)
    city_to_iata.setdefault(_city_key, _iata)
    iata_to_name[_iata] = _name
    iata_to_city[_iata] = _city
    iata_to_country[_iata] = _cc
    country_to_airports.setdefault(_cc, []).append(_iata)
    country_to_cities.setdefault(_cc, [])
    if _city.upper() not in (c.upper() for c in country_to_cities[_cc]):
        country_to_cities[_cc].append(_city)


def resolve_iata(query: str) -> str | None:
    q = query.strip().upper()
    if len(q) == 3 and q in iata_to_name:
        return q
    return city_to_iata.get(q)


def iata_is_valid(code: str) -> bool:
    return code.strip().upper() in iata_to_name


def nearest_airport(city_input: str, supported: frozenset[str] | None = None) -> str | None:
    resolved = resolve_iata(city_input)
    if resolved is None:
        return None
    if supported is None:
        return resolved
    if resolved in supported:
        return resolved
    cc = iata_to_country.get(resolved)
    if cc:
        for apt in country_to_airports.get(cc, []):
            if apt in supported:
                return apt
    return resolved


__all__ = [
    "city_country",
    "city_to_iata",
    "country_name",
    "country_to_airports",
    "country_to_cities",
    "iata_is_valid",
    "iata_to_city",
    "iata_to_country",
    "iata_to_name",
    "nearest_airport",
    "resolve_iata",
]
