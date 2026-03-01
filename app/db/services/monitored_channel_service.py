from datetime import datetime

from app.db.repositories import MonitoredChannelRepository


class MonitoredChannelService:
    def __init__(self, monitored_channel_repository: MonitoredChannelRepository):
        self._monitored_channel_repository = monitored_channel_repository

    async def add_channels(self, tg_user_id: int, channels: list[dict[str, object]]) -> None:
        await self._monitored_channel_repository.add_channels(tg_user_id, channels)

    async def get_user_channels(self, tg_user_id: int) -> list[str]:
        return await self._monitored_channel_repository.get_user_channels(tg_user_id)

    async def get_user_monitors(self, tg_user_id: int) -> list[tuple[int, str, int, str, datetime]]:
        return await self._monitored_channel_repository.get_user_monitors(tg_user_id)

    async def get_user_monitor_by_id(
        self,
        tg_user_id: int,
        monitor_id: int,
    ) -> tuple[int, str, int, int, str, datetime] | None:
        return await self._monitored_channel_repository.get_user_monitor_by_id(tg_user_id, monitor_id)

    async def delete_user_monitors(self, tg_user_id: int, monitor_ids: list[int]) -> int:
        return await self._monitored_channel_repository.delete_user_monitors(tg_user_id, monitor_ids)


_monitored_channel_service = MonitoredChannelService(MonitoredChannelRepository())


def get_monitored_channel_service() -> MonitoredChannelService:
    return _monitored_channel_service
