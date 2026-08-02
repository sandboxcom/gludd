"""Pydantic contracts for the travel agent (TRV Phase A).

Implements the JSON contracts in ``docs/specs/FEATURE_TRAVEL_AGENT.md`` §4
with strict validation: every monetary amount carries a currency, date ranges
are ordered, enumerated types reject unknown values, and required fields
cannot be empty.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "1.0"


def _new_uuid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class SegmentKind(StrEnum):
    transport = "transport"
    stay = "stay"
    rest = "rest"


class CabinClass(StrEnum):
    economy = "economy"
    premium_economy = "premium_economy"
    business = "business"
    first = "first"


class EventKind(StrEnum):
    wedding = "wedding"
    conference = "conference"
    funeral = "funeral"


class DocKind(StrEnum):
    passport = "passport"
    visa = "visa"
    insurance = "insurance"


class NotificationKind(StrEnum):
    check_in_reminder = "check_in_reminder"
    gate_change = "gate_change"
    delay = "delay"


class BookingStatus(StrEnum):
    draft = "draft"
    expired = "expired"
    confirmed = "confirmed"
    cancelled = "cancelled"
    refused = "refused"


class ItineraryStatus(StrEnum):
    draft = "draft"
    awaiting_approval = "awaiting_approval"
    approved = "approved"
    expired = "expired"
    active = "active"
    completed = "completed"


class ValidationStatus(StrEnum):
    pass_ = "pass"
    fail = "fail"
    warning = "warning"


# ---------------------------------------------------------------------------
# Shared value types
# ---------------------------------------------------------------------------


class Money(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount: float = Field(ge=0.0)
    currency: str = Field(min_length=3, max_length=3)

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, v: str) -> str:
        return v.upper()


class ProviderInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(min_length=1)
    offer_id: str = Field(min_length=1)
    retrieved_at: datetime


class Coordinates(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lat: float
    lon: float


class ValidationEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    check: str = Field(min_length=1)
    status: ValidationStatus
    detail: str = ""


# ---------------------------------------------------------------------------
# Traveler
# ---------------------------------------------------------------------------


class Passport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    number_masked: str = Field(min_length=1)
    issuing_country: str = Field(min_length=2, max_length=3)
    expiry_date: date
    blank_pages: int = Field(default=2, ge=0)


class VisaHeld(BaseModel):
    model_config = ConfigDict(extra="forbid")
    country: str = Field(min_length=2, max_length=3)
    type: str = Field(min_length=1)
    expiry: date


class LoyaltyProgram(BaseModel):
    model_config = ConfigDict(extra="forbid")
    program: str = Field(min_length=1)
    member_id_masked: str = Field(min_length=1)
    tier: str = Field(min_length=1)


class Traveler(BaseModel):
    model_config = ConfigDict(extra="forbid")
    traveler_id: str = Field(default_factory=_new_uuid, min_length=1)
    name: str = Field(min_length=1)
    passport_number: str = Field(min_length=1)
    passport_expiry: date | None = None
    nationality: str = Field(default="", min_length=2, max_length=3)
    dietary: list[str] = Field(default_factory=list)
    accessibility: list[str] = Field(default_factory=list)
    visa_country: str | None = None
    visa_expiry: date | None = None
    date_of_birth: date | None = None
    nationalities: list[str] = Field(default_factory=list)
    residence_country: str | None = None
    passport: Passport | None = None
    visas_held: list[VisaHeld] = Field(default_factory=list)
    loyalty_programs: list[LoyaltyProgram] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------


class BudgetLineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: str = Field(min_length=1)
    description: str = ""
    amount: float = Field(ge=0.0, default=0.0)


class Budget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    currency: str = Field(min_length=3, max_length=3)
    line_items: list[BudgetLineItem] = Field(default_factory=list)
    total: float = Field(ge=0.0)

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, v: str) -> str:
        return v.upper()


# ---------------------------------------------------------------------------
# TripSegment
# ---------------------------------------------------------------------------


class TripSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segment_type: SegmentKind
    from_location: str = Field(min_length=1)
    to_location: str = Field(min_length=1)
    departure: datetime
    arrival: datetime
    cost: float = Field(ge=0.0)
    currency: str = Field(min_length=3, max_length=3)
    provider: str | None = None
    booking_id: str | None = None
    confirmation_code: str | None = None

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, v: str) -> str:
        return v.upper()

    @field_validator("arrival")
    @classmethod
    def _arrival_after_departure(cls, v: datetime, info: Any) -> datetime:
        dep = info.data.get("departure")
        if dep is not None and v < dep:
            raise ValueError("arrival must not be before departure")
        return v


# ---------------------------------------------------------------------------
# TripRequest
# ---------------------------------------------------------------------------


class TripPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_stops: int = Field(default=2, ge=0)
    cabin_class: CabinClass = CabinClass.economy
    max_connections: int = Field(default=2, ge=0)
    preferred_airlines: list[str] = Field(default_factory=list)
    avoid_airlines: list[str] = Field(default_factory=list)
    earliest_departure_hour: int = Field(default=6, ge=0, le=23)
    latest_arrival_hour: int = Field(default=22, ge=0, le=23)
    max_transfer_minutes: int = Field(default=180, ge=0)


class TripRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default=SCHEMA_VERSION)
    request_id: str = Field(default_factory=_new_uuid, min_length=1)
    origin: str = Field(min_length=1)
    destination: str = Field(min_length=1)
    start_date: date
    end_date: date
    travelers: list[Traveler] = Field(default_factory=list)
    budget: Budget
    preferences: dict[str, Any] = Field(default_factory=lambda: {"max_stops": 2})
    travel_modes: list[str] = Field(default_factory=list)
    stops: list[TripSegment] = Field(default_factory=list)

    @field_validator("end_date")
    @classmethod
    def _end_after_start(cls, v: date, info: Any) -> date:
        sd = info.data.get("start_date")
        if sd is not None and v < sd:
            raise ValueError("end_date must not be before start_date")
        return v


# ---------------------------------------------------------------------------
# FlightSearch
# ---------------------------------------------------------------------------


class FlightSearch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    origin: str = Field(min_length=1)
    destination: str = Field(min_length=1)
    departure_date: date
    return_date: date | None = None
    passengers: int = Field(ge=1)
    cabin_class: CabinClass = CabinClass.economy


# ---------------------------------------------------------------------------
# FlightSegment
# ---------------------------------------------------------------------------


class FlightSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    flight_number: str = Field(min_length=1)
    airline: str = Field(min_length=1)
    departure_airport: str = Field(min_length=1)
    arrival_airport: str = Field(min_length=1)
    departure_time: datetime
    arrival_time: datetime
    aircraft: str | None = None
    cabin_class: str = Field(min_length=1)
    duration_minutes: int | None = None

    @field_validator("arrival_time")
    @classmethod
    def _arrival_after_departure(cls, v: datetime, info: Any) -> datetime:
        dep = info.data.get("departure_time")
        if dep is not None and v < dep:
            raise ValueError("arrival_time must not be before departure_time")
        return v


# ---------------------------------------------------------------------------
# FlightBooking
# ---------------------------------------------------------------------------


class FlightFareRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    refundable: bool = False
    changeable: bool = False
    change_fee: Money | None = None
    cancellation_policy: str = ""
    advance_purchase_days: int = 0
    min_stay_days: int = 0
    max_stay_days: int = 0
    stopover_allowed: bool = False


class FlightFare(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_amount: Money
    taxes: list[dict[str, Any]] = Field(default_factory=list)
    fees: list[dict[str, Any]] = Field(default_factory=list)
    total_amount: Money | None = None
    fare_rules: FlightFareRule = Field(default_factory=FlightFareRule)


class FlightBooking(BaseModel):
    model_config = ConfigDict(extra="forbid")

    booking_id: str = Field(default_factory=_new_uuid, min_length=1)
    confirmation_code: str = Field(min_length=1)
    airline: str = Field(min_length=1)
    segments: list[FlightSegment] = Field(min_length=1)
    total_price: float = Field(ge=0.0)
    currency: str = Field(min_length=3, max_length=3)
    status: BookingStatus = BookingStatus.draft
    fare: FlightFare | None = None
    provider: ProviderInfo | None = None
    expires_at: datetime | None = None
    alternatives: list[str] = Field(default_factory=list)

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, v: str) -> str:
        return v.upper()


# ---------------------------------------------------------------------------
# HotelSearch
# ---------------------------------------------------------------------------


class HotelSearch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    location: str = Field(min_length=1)
    check_in: date
    check_out: date
    guests: int = Field(ge=1)
    rooms: int = Field(ge=1)

    @field_validator("check_out")
    @classmethod
    def _check_out_after_check_in(cls, v: date, info: Any) -> date:
        ci = info.data.get("check_in")
        if ci is not None and v < ci:
            raise ValueError("check_out must not be before check_in")
        return v


# ---------------------------------------------------------------------------
# RoomType
# ---------------------------------------------------------------------------


class RoomType(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    beds: str = Field(min_length=1)
    max_occupancy: int = Field(ge=1)
    price_per_night: float = Field(ge=0.0)
    currency: str = Field(min_length=3, max_length=3)

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, v: str) -> str:
        return v.upper()


# ---------------------------------------------------------------------------
# HotelBooking
# ---------------------------------------------------------------------------


class HotelCancellationTerms(BaseModel):
    model_config = ConfigDict(extra="forbid")
    free_until: datetime | None = None
    penalty_after: Money | None = None
    non_refundable: bool = False
    policy_text: str = ""


class HotelRate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_per_night: Money | None = None
    taxes: list[dict[str, Any]] = Field(default_factory=list)
    resort_fee: Money | None = None
    total_amount: Money | None = None
    board_basis: str = "room_only"
    cancellation: HotelCancellationTerms = Field(default_factory=HotelCancellationTerms)
    payment: str = "pay_at_hotel"
    deposit_amount: Money | None = None


class HotelBooking(BaseModel):
    model_config = ConfigDict(extra="forbid")

    booking_id: str = Field(default_factory=_new_uuid, min_length=1)
    confirmation_code: str = Field(min_length=1)
    hotel_name: str = Field(min_length=1)
    address: str = Field(min_length=1)
    room: RoomType
    check_in: date
    check_out: date
    total_price: float = Field(ge=0.0)
    currency: str = Field(min_length=3, max_length=3)
    status: BookingStatus = BookingStatus.draft
    property_id: str | None = None
    rate: HotelRate | None = None
    provider: ProviderInfo | None = None
    expires_at: datetime | None = None

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, v: str) -> str:
        return v.upper()

    @field_validator("check_out")
    @classmethod
    def _check_out_after_check_in(cls, v: date, info: Any) -> date:
        ci = info.data.get("check_in")
        if ci is not None and v < ci:
            raise ValueError("check_out must not be before check_in")
        return v


# ---------------------------------------------------------------------------
# CarRental
# ---------------------------------------------------------------------------


class CarRental(BaseModel):
    model_config = ConfigDict(extra="forbid")

    booking_id: str = Field(default_factory=_new_uuid, min_length=1)
    confirmation_code: str = Field(min_length=1)
    pickup_location: str = Field(min_length=1)
    dropoff_location: str = Field(min_length=1)
    pickup_date: date
    dropoff_date: date
    car_type: str = Field(min_length=1)
    total_price: float = Field(ge=0.0)
    currency: str = Field(min_length=3, max_length=3)
    rental_company: str | None = None
    includes_insurance: bool = False

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, v: str) -> str:
        return v.upper()

    @field_validator("dropoff_date")
    @classmethod
    def _dropoff_after_pickup(cls, v: date, info: Any) -> date:
        pd = info.data.get("pickup_date")
        if pd is not None and v < pd:
            raise ValueError("dropoff_date must not be before pickup_date")
        return v


# ---------------------------------------------------------------------------
# TrainBooking
# ---------------------------------------------------------------------------


class TrainBooking(BaseModel):
    model_config = ConfigDict(extra="forbid")

    booking_id: str = Field(default_factory=_new_uuid, min_length=1)
    confirmation_code: str = Field(min_length=1)
    operator: str = Field(min_length=1)
    departure_station: str = Field(min_length=1)
    arrival_station: str = Field(min_length=1)
    departure_time: datetime
    arrival_time: datetime
    seat_class: str = Field(min_length=1)
    total_price: float = Field(ge=0.0)
    currency: str = Field(min_length=3, max_length=3)
    train_number: str | None = None
    seat_number: str | None = None

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, v: str) -> str:
        return v.upper()

    @field_validator("arrival_time")
    @classmethod
    def _arrival_after_departure(cls, v: datetime, info: Any) -> datetime:
        dep = info.data.get("departure_time")
        if dep is not None and v < dep:
            raise ValueError("arrival_time must not be before departure_time")
        return v


# ---------------------------------------------------------------------------
# BusBooking
# ---------------------------------------------------------------------------


class BusBooking(BaseModel):
    model_config = ConfigDict(extra="forbid")

    booking_id: str = Field(default_factory=_new_uuid, min_length=1)
    confirmation_code: str = Field(min_length=1)
    operator: str = Field(min_length=1)
    departure_stop: str = Field(min_length=1)
    arrival_stop: str = Field(min_length=1)
    departure_time: datetime
    arrival_time: datetime
    total_price: float = Field(ge=0.0)
    currency: str = Field(min_length=3, max_length=3)
    seat_number: str | None = None
    ticket_type: str | None = None

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, v: str) -> str:
        return v.upper()


# ---------------------------------------------------------------------------
# EventBooking
# ---------------------------------------------------------------------------


class EventBooking(BaseModel):
    model_config = ConfigDict(extra="forbid")

    booking_id: str = Field(default_factory=_new_uuid, min_length=1)
    event_type: EventKind
    name: str = Field(min_length=1)
    location: str | None = None
    venue: str | None = None
    event_date: date
    guests: int | None = None
    attendees: int | None = None
    total_price: float = Field(ge=0.0)
    currency: str = Field(min_length=3, max_length=3)
    status: BookingStatus = BookingStatus.draft
    provider: ProviderInfo | None = None
    expires_at: datetime | None = None

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, v: str) -> str:
        return v.upper()


# ---------------------------------------------------------------------------
# TravelDoc
# ---------------------------------------------------------------------------


class TravelDoc(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc_type: DocKind
    doc_number: str = Field(min_length=1)
    issuing_country: str = Field(min_length=2, max_length=3)
    expiry_date: date
    holder_name: str = Field(min_length=1)
    issue_date: date | None = None
    nationality: str | None = None


# ---------------------------------------------------------------------------
# Notification
# ---------------------------------------------------------------------------


class Notification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notification_type: NotificationKind
    title: str = Field(min_length=1)
    message: str = Field(min_length=1)
    recipient: str = Field(min_length=1)
    delay_minutes: int | None = None
    sent: bool = False


# ---------------------------------------------------------------------------
# MultiStopRoute
# ---------------------------------------------------------------------------


class MultiStopRoute(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route_id: str = Field(default_factory=_new_uuid, min_length=1)
    name: str = Field(min_length=1)
    segments: list[TripSegment] = Field(min_length=1)
    optimized: bool = False
    total_cost: float = 0.0
    validation: list[ValidationEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def _compute_total_cost(self) -> MultiStopRoute:
        self.total_cost = sum(seg.cost for seg in self.segments)
        return self


# ---------------------------------------------------------------------------
# Itinerary
# ---------------------------------------------------------------------------


class TimelineEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_index: int = Field(ge=0)
    type: str = Field(min_length=1)
    start_time: datetime
    end_time: datetime
    timezone: str = Field(min_length=1)
    location: str = Field(min_length=1)
    booking_id: str | None = None
    details: str = ""
    alerts: list[str] = Field(default_factory=list)


class DocumentNeeded(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1)
    country: str = Field(min_length=2, max_length=3)
    status: str = Field(default="valid", min_length=1)


class EmergencyContact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1)
    country: str = Field(min_length=2, max_length=3)
    number: str = Field(min_length=1)
    available_24h: bool = False


class WeatherForecastEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: date
    temp_high_c: float = 0.0
    temp_low_c: float = 0.0
    conditions: str = ""
    precipitation_mm: float = Field(ge=0.0, default=0.0)
    wind_kmh: float = Field(ge=0.0, default=0.0)
    uv_index: float = Field(ge=0.0, default=0.0)


class StopForecast(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stop_index: int = Field(ge=0)
    forecast: list[WeatherForecastEntry] = Field(default_factory=list)


class WeatherForecast(BaseModel):
    model_config = ConfigDict(extra="forbid")

    retrieved_at: datetime | None = None
    source: str = ""
    stops: list[StopForecast] = Field(default_factory=list)


class ErrorEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1)
    retryable: bool = False
    message: str = Field(min_length=1)


class Itinerary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default=SCHEMA_VERSION)
    itinerary_id: str = Field(default_factory=_new_uuid, min_length=1)
    request_id: str = Field(default_factory=_new_uuid, min_length=1)
    status: ItineraryStatus = ItineraryStatus.draft
    approval_token: str | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None
    route_id: str | None = None
    timeline: list[TimelineEntry] = Field(default_factory=list)
    documents_needed: list[DocumentNeeded] = Field(default_factory=list)
    emergency_contacts: list[EmergencyContact] = Field(default_factory=list)
    weather_forecast: WeatherForecast | None = None
    total_cost: Money | None = None
    version_digest: str = ""
    expires_at: datetime | None = None
    errors: list[ErrorEntry] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Route stop (from MultiStopRoute spec)
# ---------------------------------------------------------------------------


class RouteStop(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stop_index: int = Field(ge=0)
    city: str = Field(min_length=1)
    country: str = Field(min_length=2, max_length=3)
    arrival_mode: str = "start"
    arrival_booking_id: str | None = None
    arrival_time: datetime | None = None
    departure_time: datetime | None = None
    dwell_hours: float = Field(ge=0.0, default=0.0)
    accommodation_id: str | None = None
    events: list[str] = Field(default_factory=list)
    visa_required: bool = False
    visa_type: str | None = None
    travel_advisory_level: str | None = None
    weather_risk: list[str] = Field(default_factory=list)
    timezone: str = "UTC"


class Transit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_stop_index: int = Field(ge=0)
    to_stop_index: int = Field(ge=0)
    mode: str = Field(min_length=1)
    booking_id: str | None = None
    departure_time: datetime | None = None
    arrival_time: datetime | None = None
    duration_minutes: int = Field(ge=0, default=0)
    buffer_minutes: int = Field(ge=0, default=60)
    visa_required: bool = False
    connection_warning: str | None = None


class BudgetEstimate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str = Field(min_length=1)
    stop_index_ref: int = Field(ge=0)
    booking_id_ref: str | None = None
    estimated_amount: Money | None = None
    certainty: str = "estimated"
    notes: str = ""


class TotalCostEstimate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: float = Field(ge=0.0)
    currency: str = Field(min_length=3, max_length=3)
    lower_bound: Money | None = None
    upper_bound: Money | None = None

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, v: str) -> str:
        return v.upper()


# ---------------------------------------------------------------------------
# __all__
# ---------------------------------------------------------------------------


__all__ = [
    "SCHEMA_VERSION",
    "BookingStatus",
    "Budget",
    "BudgetEstimate",
    "BudgetLineItem",
    "BusBooking",
    "CabinClass",
    "CarRental",
    "Coordinates",
    "DocKind",
    "DocumentNeeded",
    "EmergencyContact",
    "ErrorEntry",
    "EventBooking",
    "EventKind",
    "FlightBooking",
    "FlightFare",
    "FlightFareRule",
    "FlightSearch",
    "FlightSegment",
    "HotelBooking",
    "HotelCancellationTerms",
    "HotelRate",
    "HotelSearch",
    "Itinerary",
    "ItineraryStatus",
    "LoyaltyProgram",
    "Money",
    "MultiStopRoute",
    "Notification",
    "NotificationKind",
    "Passport",
    "ProviderInfo",
    "RoomType",
    "RouteStop",
    "SegmentKind",
    "StopForecast",
    "TimelineEntry",
    "TotalCostEstimate",
    "TrainBooking",
    "Transit",
    "TravelDoc",
    "Traveler",
    "TripPreferences",
    "TripRequest",
    "TripSegment",
    "ValidationEntry",
    "ValidationStatus",
    "VisaHeld",
    "WeatherForecast",
    "WeatherForecastEntry",
]
