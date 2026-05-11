from pathlib import Path
from uuid import uuid4

from app.schemas.manual import ManualRegisterRequest, ManualRegisterResponse


class ManualIndexer:
    async def register_manual(
        self, payload: ManualRegisterRequest
    ) -> ManualRegisterResponse:
        # MVP keeps registration deterministic. Next step: parse PDF pages,
        # chunk text, and build a persisted LlamaIndex index under data/indexes.
        file_path = Path(payload.file_path)
        if not file_path.is_absolute():
            file_path = Path.cwd() / file_path

        if not file_path.exists():
            raise FileNotFoundError(f"未找到维修手册文件：{file_path}")

        return ManualRegisterResponse(
            manual_id=str(uuid4()),
            file_path=str(file_path.resolve()),
            page_count=None,
            status="已注册，等待索引构建",
            next_step="下一步接入 PDF 解析、文本分块和 LlamaIndex 索引构建流程。",
        )
