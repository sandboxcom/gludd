"""Dogfood module — self-testing release loop components."""

from general_ludd.dogfood.runner import DogfoodConfig, DogfoodProfile, DogfoodRunner, SmokeTaskResult
from general_ludd.dogfood.sprint_parser import SprintItem, parse_sprint_markdown
from general_ludd.dogfood.validator import BypassFinding, DogfoodValidationResult, DogfoodValidator

__all__ = [
    "BypassFinding",
    "DogfoodConfig",
    "DogfoodProfile",
    "DogfoodRunner",
    "DogfoodValidationResult",
    "DogfoodValidator",
    "SmokeTaskResult",
    "SprintItem",
    "parse_sprint_markdown",
]
