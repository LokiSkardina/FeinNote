# Responsible for: selecting notes eligible for folder retention policies.

"""Pure retention planning; deletion remains under controller coordination."""


class CleanupService:
    """Calculate count and age retention with conservative invalid-date handling."""

    @staticmethod
    def candidates(notes, rule, now=None):
        """Return eligible paths from one folder; invalid timestamps are preserved."""
        import math
        import time
        mode = rule.get("mode", "disabled")
        if mode == "disabled":
            return []
        value = rule.get("value")
        if mode not in ("count", "days") or type(value) is not int or value < 1:
            raise ValueError("Cleanup requires a positive integer.")
        dated = []
        for note, path in notes:
            try:
                timestamp = float(note.get("created_at", note.get("timestamp", 0)))
            except (TypeError, ValueError):
                continue
            if math.isfinite(timestamp) and timestamp > 0:
                dated.append((timestamp, path))
        dated.sort(reverse=True)
        if mode == "count":
            return [path for _, path in dated[value:]]
        cutoff = (time.time() if now is None else now) - value * 86400
        return [path for timestamp, path in dated if timestamp < cutoff]
