from typing import Annotated

from pydantic import BaseModel, Field
from pydantic import StringConstraints


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ManualRegisterRequest(BaseModel):
    file_path: NonEmptyStr = Field(
        description="PDF 文件的绝对路径或相对项目根目录的路径。"
    )
    device_name: NonEmptyStr = Field(description="设备名称。")
    device_model: str | None = Field(default=None, description="可选的设备型号。")


class ManualRegisterResponse(BaseModel):
    manual_id: str
    document_id: str | None = None
    document_version_id: str | None = None
    job_id: str | None = None
    file_path: str
    page_count: int | None = None
    status: str
    next_step: str
