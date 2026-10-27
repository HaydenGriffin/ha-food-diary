"""How full a person was a couple of hours after a meal: hungry again, satisfied or still full.

One answer per day and meal (breakfast, lunch or dinner; not snacks), kept in the person's diary (`fullness`: {date:
{meal: answer}}) for KEEP_DAYS. An answer can be given for today and the BACK_DAYS days before, never for a day still to
come; giving none (None) takes it back. Reading them back joins each answer to what that meal had in the diary as it is
now, so an answer stays right when the meal's food changes after it.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from homeassistant.exceptions import ServiceValidationError

from .diary import PLANNED, Diary, num, today

ANSWERS = ("hungry", "satisfied", "full")
ASKED = PLANNED  # the meals it is asked about
KEEP_DAYS = 400
BACK_DAYS = 7
NOT_YET = "That day hasn't come yet."
TOO_LATE = f"Fullness can only be given for today and the {BACK_DAYS} days before."


def check_day(d: str, now: str) -> None:
    """An answer's day must be today or up to BACK_DAYS before it."""
    if d > now:
        raise ServiceValidationError(NOT_YET)
    if d < (date.fromisoformat(now) - timedelta(days=BACK_DAYS)).isoformat():
        raise ServiceValidationError(TOO_LATE)


def kept(answers: dict[str, dict[str, str]], now: str) -> dict[str, dict[str, str]]:
    """The answers from within KEEP_DAYS of `now`, without days left with none."""
    cut = (date.fromisoformat(now) - timedelta(days=KEEP_DAYS)).isoformat()
    return {d: dict(meals) for d, meals in answers.items() if d >= cut and meals}


def foods(entries: list[dict[str, Any]], meal: str) -> list[dict[str, Any]]:
    """A meal's foods as {name, kcal}, the most calories first."""
    out = [{"name": e.get("name", ""), "kcal": round(num(e.get("kcal")), 1)} for e in entries if e.get("meal") == meal]
    return sorted(out, key=lambda f: -f["kcal"])


class Fullness:
    """One person's answers, kept in their diary's store."""

    def __init__(self, diary: Diary) -> None:
        self.diary = diary

    @property
    def all(self) -> dict[str, dict[str, str]]:
        return self.diary.data.setdefault("fullness", {})

    def prune(self) -> None:
        """Forget the answers older than KEEP_DAYS (when the diary loads and on every answer)."""
        if (left := kept(self.all, today())) != self.all:
            self.diary.data["fullness"] = left
            self.diary.async_changed()

    def get(self, d: str, meal: str) -> str | None:
        return self.all.get(d, {}).get(meal)

    def set(self, d: str, meal: str, answer: str | None) -> str | None:
        """The answer for that day's meal (None takes it back); the same answer again changes nothing. Returns it."""
        check_day(d, today())
        if self.get(d, meal) != answer:
            if answer is None:
                self.all.get(d, {}).pop(meal, None)
            else:
                self.all.setdefault(d, {})[meal] = answer
            self.diary.async_changed()
        self.prune()
        return answer

    def answers(self, days: int) -> list[dict[str, Any]]:
        """The answers from today and the days-1 days before, newest first (a day's dinner before its lunch), each with
        the calories and foods its meal has in the diary now."""
        start = (date.fromisoformat(today()) - timedelta(days=days - 1)).isoformat()
        out = []
        for d in sorted((d for d in self.all if start <= d <= today()), reverse=True):
            for meal in reversed(ASKED):
                if answer := self.all[d].get(meal):
                    eaten = foods(self.diary.entries(d), meal)
                    total = round(sum(f["kcal"] for f in eaten), 1)
                    out.append({"date": d, "meal": meal, "fullness": answer, "kcal": total, "foods": eaten})
        return out
