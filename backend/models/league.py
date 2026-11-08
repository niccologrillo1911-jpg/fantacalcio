from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class Match(BaseModel):
    home: str
    away: str
    home_fp: float | None = None
    away_fp: float | None = None
    home_goals: int | None = None
    away_goals: int | None = None
    home_points: int | None = None  # final points: 2 win / 1 draw / 0 loss + goals scored
    away_points: int | None = None
    played: bool = False


class Round(BaseModel):
    number: int
    seriea_round: int | None = None
    played: bool = False
    matches: list[Match] = []


class StandingRow(BaseModel):
    position: int
    team: str
    points: int
    played: int
    won: int
    drawn: int
    lost: int
    goals_for: int
    goals_against: int
    goal_diff: int
    fantapoints: float


class SyncStatus(BaseModel):
    state: Literal["idle", "running", "ok", "error"] = "idle"
    message: str | None = None
    at: datetime | None = None


class AutomationInfo(BaseModel):
    name: str
    alias: str | None = None


class LeagueSummary(BaseModel):
    id: str
    name: str
    teams: list[str] = []
    source: Literal["none", "file", "auto"] = "none"
    updated_at: datetime | None = None


class LeagueDetail(LeagueSummary):
    rounds: list[Round] = []
    standings: list[StandingRow] = []
    last_played: int | None = None
    automation: AutomationInfo | None = None
    sync: SyncStatus = SyncStatus()


class LeagueCreate(BaseModel):
    name: str = Field(min_length=3, max_length=20)
    access_password: str = Field(min_length=3, max_length=20)
    settings_password: str = Field(min_length=3, max_length=20)


class LeagueAccess(BaseModel):
    password: str = Field(min_length=1, max_length=200)


class SettingsAuth(BaseModel):
    settings_password: str = Field(min_length=1, max_length=200)


class RenameRequest(SettingsAuth):
    name: str = Field(min_length=3, max_length=20)


class AccessPasswordRequest(SettingsAuth):
    new_password: str = Field(min_length=3, max_length=20)


class AutomationRequest(SettingsAuth):
    league_name: str = Field(min_length=1, max_length=120)
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=200)


class OkResponse(BaseModel):
    ok: bool = True
