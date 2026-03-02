from app.db.repositories.user_repository import UserRepository


class UserSessionService:
    def __init__(self, user_repository: UserRepository):
        self._user_repository = user_repository

    async def add_user_if_not_exists(self, tg_id: int) -> None:
        await self._user_repository.add_user_if_not_exists(tg_id)

    async def set_user_session(self, tg_id: int, session_str: str) -> None:
        await self._user_repository.set_user_session(tg_id, session_str)

    async def get_user_session(self, tg_id: int) -> str | None:
        return await self._user_repository.get_user_session(tg_id)

    async def get_all_sessions(self) -> list[tuple[int, str]]:
        return await self._user_repository.get_all_sessions()

    async def clear_user_session(self, tg_id: int) -> None:
        await self._user_repository.clear_user_session(tg_id)

    async def set_user_feed(self, tg_id: int, feed_chat_id: int, feed_chat_title: str) -> None:
        await self._user_repository.set_user_feed(tg_id, feed_chat_id, feed_chat_title)

    async def get_user_feed(self, tg_id: int) -> tuple[int, str] | None:
        return await self._user_repository.get_user_feed(tg_id)

    async def clear_user_feed(self, tg_id: int) -> None:
        await self._user_repository.clear_user_feed(tg_id)


_user_session_service = UserSessionService(UserRepository())


def get_user_session_service() -> UserSessionService:
    return _user_session_service
