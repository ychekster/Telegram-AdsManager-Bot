from app.db.repositories.muted_post_text_repository import MutedPostTextRepository


class MutedPostTextService:
    def __init__(self, muted_post_text_repository: MutedPostTextRepository):
        self._muted_post_text_repository = muted_post_text_repository

    async def should_mute(self, tg_user_id: int, text_hash: str, muted_text: str, ttl_days: int = 7) -> bool:
        return await self._muted_post_text_repository.should_mute(
            tg_user_id,
            text_hash,
            muted_text,
            ttl_days=ttl_days,
        )

    async def get_user_muted_texts(self, tg_user_id: int) -> list[str]:
        return await self._muted_post_text_repository.get_user_muted_texts(tg_user_id)


_muted_post_text_service = MutedPostTextService(MutedPostTextRepository())


def get_muted_post_text_service() -> MutedPostTextService:
    return _muted_post_text_service
