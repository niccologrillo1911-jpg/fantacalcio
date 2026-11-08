"""Parser for the 'Calendario' Excel exported by Fantacalcio Leghe (.xlsx or .xls)."""

import io
import re
from typing import Any

HEADER_RE = re.compile(r"^\s*(\d+)\s*[ªa°º^]?\s*giornata\b(?!\s*(di\s+)?serie)", re.IGNORECASE)
SERIEA_RE = re.compile(r"^\s*(\d+)\s*[ªa°º^]?\s*giornata\s+(di\s+)?serie\s*a", re.IGNORECASE)
SCORE_RE = re.compile(r"^\s*(\d+)\s*-\s*(\d+)\s*$")


class ExcelFormatError(ValueError):
    pass


def _read_rows(content: bytes) -> list[list[Any]]:
    try:
        import openpyxl

        wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True, read_only=False)  # read_only trusts the file's (often wrong) dimension tag
        rows: list[list[Any]] = []
        for ws in wb.worksheets:
            rows.extend([list(r) for r in ws.iter_rows(values_only=True)])
            rows.append([])
        return rows
    except Exception:
        pass
    try:
        import xlrd

        book = xlrd.open_workbook(file_contents=content)
        rows = []
        for sheet in book.sheets():
            rows.extend([sheet.row_values(i) for i in range(sheet.nrows)])
            rows.append([])
        return rows
    except Exception as exc:
        raise ExcelFormatError("Il file non è un Excel valido.") from exc


def _cell(row: list[Any], col: int) -> Any:
    return row[col] if col < len(row) else None


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", "."))
    except ValueError:
        return None


def parse_calendar(content: bytes) -> dict:
    """Return {"teams": [...], "rounds": [{number, seriea_round, played, matches:[...]}]}."""
    rows = _read_rows(content)
    rounds: dict[int, dict] = {}
    teams: list[str] = []

    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            if not isinstance(value, str):
                continue
            m = HEADER_RE.match(value)
            if not m:
                continue
            number = int(m.group(1))
            sa_cell = _cell(row, c + 2)
            sa = SERIEA_RE.match(sa_cell) if isinstance(sa_cell, str) else None
            matches = []
            rr = r + 1
            while rr < len(rows):
                line = rows[rr]
                home = _cell(line, c)
                away = _cell(line, c + 3)
                if not isinstance(home, str) or not home.strip() or HEADER_RE.match(home):
                    break
                if not isinstance(away, str) or not away.strip():
                    break
                score = _cell(line, c + 4)
                sm = SCORE_RE.match(str(score)) if score is not None else None
                hf, af = _num(_cell(line, c + 1)), _num(_cell(line, c + 2))
                played = hf is not None and af is not None and (sm is not None or hf > 0 or af > 0)
                home_name, away_name = home.strip(), away.strip()
                for t in (home_name, away_name):
                    if t not in teams:
                        teams.append(t)
                matches.append(
                    {
                        "home": home_name,
                        "away": away_name,
                        "home_fp": hf if played else None,
                        "away_fp": af if played else None,
                        "played": played,
                    }
                )
                rr += 1
            if matches:
                rounds[number] = {
                    "number": number,
                    "seriea_round": int(sa.group(1)) if sa else None,
                    "played": any(x["played"] for x in matches),
                    "matches": matches,
                }

    if not rounds:
        raise ExcelFormatError(
            "Non trovo le giornate nel file. Usa il file 'Calendario' scaricato da Fantacalcio Leghe."
        )
    return {"teams": teams, "rounds": [rounds[k] for k in sorted(rounds)]}


FIRST_GOAL = 66.0
GOAL_STEP = 6.0
CLOSE_GAP = 2.5  # same goals but more than this apart in fantapoints -> higher score wins


def goals_for(fp: float) -> int:
    """66 -> 1 goal, then one more every 6 points (72, 78, 84...). Below 66 no goals."""
    if fp < FIRST_GOAL:
        return 0
    return int((fp - FIRST_GOAL) // GOAL_STEP) + 1


def score_match(home_fp: float, away_fp: float) -> tuple[int, int, int, int]:
    """Return (home_goals, away_goals, home_points, away_points).

    Points = 2 for a win / 1 draw / 0 loss, plus the goals scored.
    """
    hg, ag = goals_for(home_fp), goals_for(away_fp)
    if hg == ag == 0:
        # both under 66: no goals and always a draw, whatever the gap
        return 0, 0, 1, 1
    if hg == ag:
        diff = home_fp - away_fp
        if diff > CLOSE_GAP:
            return hg, ag, 2 + hg, ag
        if -diff > CLOSE_GAP:
            return hg, ag, hg, 2 + ag
        return hg, ag, 1 + hg, 1 + ag
    if hg > ag:
        return hg, ag, 2 + hg, ag
    return hg, ag, hg, 2 + ag


def apply_scoring(rounds: list[dict]) -> list[dict]:
    """Copy of rounds with goals and final points computed from the fantapoints."""
    out = []
    for rnd in rounds:
        matches = []
        for m in rnd.get("matches", []):
            m = {**m, "home_goals": None, "away_goals": None, "home_points": None, "away_points": None}
            if m.get("played") and m.get("home_fp") is not None and m.get("away_fp") is not None:
                m["home_goals"], m["away_goals"], m["home_points"], m["away_points"] = score_match(
                    m["home_fp"], m["away_fp"]
                )
            else:
                m["played"] = False
            matches.append(m)
        out.append({**rnd, "matches": matches, "played": any(x["played"] for x in matches)})
    return out


def compute_standings(teams: list[str], rounds: list[dict]) -> list[dict]:
    """rounds must already be scored with apply_scoring."""
    table = {
        t: {"team": t, "points": 0, "played": 0, "won": 0, "drawn": 0, "lost": 0,
            "goals_for": 0, "goals_against": 0, "fantapoints": 0.0}
        for t in teams
    }
    for rnd in rounds:
        for m in rnd["matches"]:
            if not m["played"]:
                continue
            h, a = table[m["home"]], table[m["away"]]
            for side, gf, ga, pts, opp_pts, fp in (
                (h, m["home_goals"], m["away_goals"], m["home_points"], m["away_points"], m["home_fp"]),
                (a, m["away_goals"], m["home_goals"], m["away_points"], m["home_points"], m["away_fp"]),
            ):
                side["played"] += 1
                side["goals_for"] += gf
                side["goals_against"] += ga
                side["fantapoints"] += fp or 0
                side["points"] += pts
                result = (pts - gf) - (opp_pts - ga)  # compare the 2/1/0 part only
                if result > 0:
                    side["won"] += 1
                elif result == 0:
                    side["drawn"] += 1
                else:
                    side["lost"] += 1
    rows = list(table.values())
    for row in rows:
        row["goal_diff"] = row["goals_for"] - row["goals_against"]
        row["fantapoints"] = round(row["fantapoints"], 2)
    rows.sort(key=lambda x: (-x["points"], -x["fantapoints"], -x["goal_diff"], x["team"].lower()))
    for i, row in enumerate(rows, start=1):
        row["position"] = i
    return rows
