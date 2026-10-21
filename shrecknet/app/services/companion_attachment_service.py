"""Private, bounded companion image storage outside public media mounts."""
import asyncio
import hashlib
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from uuid import uuid4
from PIL import Image
from app.models.character_companion import CompanionAttachment, utcnow
from app.repositories.character_companion_repository import CompanionRepository, fail, record
from app.services.media_service import MediaService, ImageValidationError


def private_root():
    # data directory is a sibling of public media, never a static mount.
    from app.core.config_store import _default_data_dir
    path = _default_data_dir() / "companion_attachments"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def normalize(data):
    try:
        with Image.open(BytesIO(data)) as image:
            if image.format not in {"PNG", "JPEG", "WEBP"} or getattr(image, "n_frames", 1) != 1:
                fail(415, "unsupported_attachment_type")
            if image.width * image.height > 20_000_000:
                fail(413, "attachment_pixel_limit")
        image = MediaService()._prepare_image(data, None)
        output = BytesIO()
        image.save(output, format="PNG")
        return output.getvalue(), image.size
    except (ImageValidationError, OSError, Image.DecompressionBombError):
        fail(415, "unsupported_attachment_type")


def attachment_evidence(attachment, chat_id):
    """One private-image reference for admitted history and synthesis context."""
    from app.integrations.companion.retrieval import evidence
    return evidence("user_attachment", attachment.id,
        "User-provided image. Cite only details visibly supported by the attached image; unreadable details remain uncertain.",
        title="User-provided image", reference={"attachment_id":attachment.id,"sha256":attachment.sha256},
        scope={"user_id":attachment.user_id,"chat_id":chat_id},
        structured={"width":attachment.width,"height":attachment.height})


class CompanionAttachmentService:
    def __init__(self, session):
        self.session, self.repo = session, CompanionRepository(session)

    async def upload(self, actor_id, upload):
        data = await upload.read(10 * 1024 * 1024 + 1)
        if len(data) > 10 * 1024 * 1024:
            fail(413, "attachment_too_large")
        normalized, (width, height) = await asyncio.to_thread(normalize, data)
        key = str(uuid4())
        path = private_root() / key
        await asyncio.to_thread(path.write_bytes, normalized)
        path.chmod(0o600)
        row = CompanionAttachment(user_id=actor_id, storage_key=key, size=len(data), width=width, height=height, sha256=hashlib.sha256(normalized).hexdigest())
        try:
            self.session.add(row)
            await self.session.commit()
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return self.metadata(row)

    @staticmethod
    def metadata(row):
        return {**record(row, "storage_key"), "mime_type": "image/png", "content_url": f"/users/me/character-companion/attachments/{row.id}/content"}

    async def get(self, actor_id, identifier):
        row = await self.repo.owned(CompanionAttachment, identifier, actor_id)
        if row.deleted:
            fail(404, "attachment_not_found")
        if not row.turn_id and row.created_at.replace(tzinfo=utcnow().tzinfo) < utcnow() - timedelta(hours=24):
            fail(410, "attachment_expired")
        return row

    async def delete(self, actor_id, identifier):
        row = await self.repo.owned(CompanionAttachment, identifier, actor_id)
        if row.deleted:
            return {"attachment_id": identifier, "status": "deleting"}
        row.deleted = True
        # Block further publication before physical deletion; worker checks this flag.
        await self.session.commit()
        (private_root() / row.storage_key).unlink(missing_ok=True)
        return {"attachment_id": identifier, "status": "deleting"}
