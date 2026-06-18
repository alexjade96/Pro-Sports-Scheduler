"""
Core data models shared by all three solver options.
"""
from dataclasses import dataclass, field
from datetime import date, time
from typing import Optional


@dataclass
class Team:
    id: str
    name: str
    city: str
    ground: str
    european: bool
    rivalries: list[str]


@dataclass
class Slot:
    """A specific date + kickoff time combination."""
    date: date
    kickoff: str       # e.g. "15:00"
    day_of_week: str   # e.g. "Saturday"

    @property
    def slot_id(self) -> str:
        return f"{self.date}_{self.kickoff.replace(':', '')}"


@dataclass
class Fixture:
    """An unscheduled home/away pairing."""
    fixture_id: str
    home_team_id: str
    away_team_id: str


@dataclass
class ScheduledFixture:
    """A fixture assigned to a specific slot."""
    fixture: Fixture
    slot: Slot

    @property
    def home_team_id(self) -> str:
        return self.fixture.home_team_id

    @property
    def away_team_id(self) -> str:
        return self.fixture.away_team_id


@dataclass
class Schedule:
    """A complete season schedule — list of assigned fixtures."""
    season: str
    fixtures: list[ScheduledFixture] = field(default_factory=list)

    def fixtures_for_team(self, team_id: str) -> list[ScheduledFixture]:
        return [
            sf for sf in self.fixtures
            if sf.home_team_id == team_id or sf.away_team_id == team_id
        ]

    def home_fixtures_for_team(self, team_id: str) -> list[ScheduledFixture]:
        return [sf for sf in self.fixtures if sf.home_team_id == team_id]

    def away_fixtures_for_team(self, team_id: str) -> list[ScheduledFixture]:
        return [sf for sf in self.fixtures if sf.away_team_id == team_id]
