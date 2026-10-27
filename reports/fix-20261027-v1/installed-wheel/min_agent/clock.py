"""可信日历时钟与有限日期解析，不负责提醒调度或自然语言意图识别。"""

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo


class Clock:
    def __init__(
        self, *, time_zone: str = "Asia/Shanghai", now: Callable[[], datetime] | None = None,
    ) -> None:
        self._zone = ZoneInfo(time_zone)
        self._now = now if now is not None else lambda: datetime.now(UTC)

    def today(self) -> date:
        current = self._now()
        if not isinstance(current, datetime):
            raise TypeError("时钟必须返回datetime")
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("时钟必须返回带时区的datetime")
        return current.astimezone(self._zone).date()

    def resolve_date(self, value: date | str | None) -> date | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            raise TypeError("截止日期不能包含时刻")
        if isinstance(value, date):
            return value
        if not isinstance(value, str):
            raise TypeError("截止日期必须是date、日期字符串或None")
        relative_days = {"今天": 0, "明天": 1, "后天": 2}
        if value in relative_days:
            return self.today() + timedelta(days=relative_days[value])
        try:
            absolute = date.fromisoformat(value)
        except ValueError:
            raise ValueError("截止日期只支持YYYY-MM-DD、今天、明天或后天") from None
        if absolute.isoformat() != value:
            raise ValueError("绝对日期必须采用YYYY-MM-DD")
        return absolute
