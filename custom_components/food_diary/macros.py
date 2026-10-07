"""Partial numbers made whole: a recipe that prints its calories and protein but not its carbs and fat.

A dish's numbers are partial when it has calories, at least one of protein, carbs or fat is missing (0), and what the known
ones add up to (4 kcal a gram of protein or carbs, 9 of fat) leaves a real part of the calories unexplained: under 70% of
them, or more than 60 kcal. Completing keeps the calories and the known macros, and fills the missing ones from an estimate
of the same dish, scaled so 4·protein + 4·carbs + 9·fat comes to the calories.
"""

from __future__ import annotations

from typing import Any

from .diary import num, nums

KCAL_PER_G = {"protein_g": 4, "carbs_g": 4, "fat_g": 9}
COVERED = 0.7  # known macros explaining less than this share of the calories: partial
UNEXPLAINED = 60  # or leaving more than this many kcal unexplained


def macro_kcal(v: dict[str, Any]) -> float:
    return sum(f * num(v.get(k)) for k, f in KCAL_PER_G.items())


def missing(v: dict[str, Any]) -> list[str]:
    return [k for k in KCAL_PER_G if num(v.get(k)) <= 0]


def partial(v: dict[str, Any] | None) -> bool:
    """Calories with macros missing that would explain a real part of them. Numbers already completed (or tried) are not."""
    v = v or {}
    kcal = num(v.get("kcal"))
    if kcal <= 0 or v.get("completed") or not missing(v):
        return False
    known = macro_kcal(v)
    return known < COVERED * kcal or kcal - known > UNEXPLAINED


def complete(v: dict[str, Any], est: dict[str, Any]) -> dict[str, float] | None:
    """v's numbers with its missing macros taken from `est` and scaled to fill the calories the known ones leave (fibre from
    `est` for the same calories when v has none). None when there's nothing to fill or `est` has none of what's missing."""
    kcal, gaps = num(v.get("kcal")), missing(v)
    left = kcal - macro_kcal(v)
    share = sum(KCAL_PER_G[k] * num(est.get(k)) for k in gaps)
    if left <= 0 or share <= 0:
        return None
    out = nums(v)
    for k in gaps:
        out[k] = round(num(est.get(k)) * left / share, 1)
    if out["fibre_g"] <= 0 and num(est.get("fibre_g")) and num(est.get("kcal")):
        out["fibre_g"] = round(num(est["fibre_g"]) * kcal / num(est["kcal"]), 1)
    return out
