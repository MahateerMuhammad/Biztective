"""Calendar arithmetic: ISO weeks, complete-week windows and equal-period comparisons."""

from dataclasses import dataclass
from datetime import date, timedelta

WEEK = timedelta(days=7)


def week_start(day: date) -> date:
    """Monday of the ISO week containing `day`."""
    return day - timedelta(days=day.weekday())


def last_complete_week(as_of: date) -> date:
    """Monday of the last ISO week that ends on or before `as_of`."""
    start = week_start(as_of)
    return start if as_of.weekday() == 6 else start - WEEK


def first_complete_week(first_day: date) -> date:
    """Monday of the first ISO week that starts on or after `first_day`."""
    start = week_start(first_day)
    return start if start == first_day else start + WEEK


@dataclass(frozen=True)
class Windows:
    """Detection windows (ARCHITECTURE §5.1): baseline B then current W, oldest first."""

    baseline: tuple[date, ...]
    current: tuple[date, ...]

    @property
    def start(self) -> date:
        return (self.baseline or self.current)[0]

    @property
    def end(self) -> date:
        return self.current[-1] + timedelta(days=6)

    @property
    def baseline_end(self) -> date:
        return self.baseline[-1] + timedelta(days=6)


def detection_windows(
    first_day: date, as_of: date, current_weeks: int, baseline_weeks: int
) -> Windows | None:
    """Complete weeks only, at both ends. None when not even the current window fits."""
    last = last_complete_week(as_of)
    first = first_complete_week(first_day)
    available = (last - first).days // 7 + 1 if last >= first else 0
    if available < current_weeks:
        return None
    current = tuple(last - WEEK * i for i in range(current_weeks - 1, -1, -1))
    baseline_count = min(baseline_weeks, available - current_weeks)
    baseline = tuple(current[0] - WEEK * i for i in range(baseline_count, 0, -1))
    return Windows(baseline=baseline, current=current)


@dataclass(frozen=True)
class Period:
    start: date
    end: date  # inclusive

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def previous(self) -> Period:
        """The immediately preceding period of equal length (AMD-10)."""
        return Period(self.start - timedelta(days=self.days), self.start - timedelta(days=1))
