from dataclasses import dataclass

from app.parser import Line, TrackDelay, TrackError


@dataclass(frozen=True)
class Verdict:
    kind: str
    reason: str


class Guard:
    def __init__(self, threshold: int):
        self.threshold = threshold
        self.consecutive = 0
        self.track_errors = 0

    def reset(self) -> None:
        self.consecutive = 0
        self.track_errors = 0

    def on_event(self, ev):
        if isinstance(ev, TrackDelay):
            self.consecutive = 0
            self.track_errors = 0
            return None
        if isinstance(ev, TrackError):  # text-independent: gamdl may not print any 429/403 text
            self.track_errors += 1
            if self.track_errors >= self.threshold:
                return Verdict("rate_limited",
                               f"{self.track_errors} consecutive track errors: possible rate limit or block")
            return None
        if isinstance(ev, Line) and ev.level in ("WARNING", "ERROR", "CRITICAL"):
            if ev.cls == "auth":
                return Verdict("cookies", "authentication failed: re-export cookies.txt")
            if ev.cls == "rate_limit":
                self.consecutive += 1
                if self.consecutive >= self.threshold:
                    return Verdict("rate_limited", f"{self.consecutive} consecutive 429/403 errors")
        return None
