import io
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from backend.database import get_session
from backend.models import ApplicantProfile, User
from backend.auth.dependencies import get_current_user

router = APIRouter(prefix="/users", tags=["cv"])

_ALLOWED = {".pdf", ".docx", ".txt"}


def _extract_text(filename: str, content: bytes) -> str:
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext == ".pdf":
        import pdfplumber
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    if ext == ".docx":
        import docx
        doc = docx.Document(io.BytesIO(content))
        return "\n".join(p.text for p in doc.paragraphs)
    return content.decode("utf-8")


class CVResponse(BaseModel):
    cv_text: str | None
    has_cv: bool


class CVUploadResponse(BaseModel):
    message: str
    char_count: int


async def _get_or_create_profile(session: AsyncSession, user_id) -> ApplicantProfile:
    result = await session.execute(
        select(ApplicantProfile).where(ApplicantProfile.user_id == user_id)
    )
    profile = result.scalar_one_or_none()
    if profile is None:
        profile = ApplicantProfile(user_id=user_id)
        session.add(profile)
    return profile


@router.post("/cv", response_model=CVUploadResponse)
async def upload_cv(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    filename = file.filename or ""
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in _ALLOWED:
        raise HTTPException(status_code=400, detail="Unsupported file type. Upload PDF, DOCX, or TXT.")
    content = await file.read()
    text = _extract_text(filename, content)
    if not text.strip():
        raise HTTPException(status_code=400, detail="Could not extract text from file.")
    profile = await _get_or_create_profile(session, current_user.id)
    profile.cv_text = text
    session.add(profile)
    await session.commit()
    return CVUploadResponse(message="CV uploaded successfully", char_count=len(text))


@router.get("/cv", response_model=CVResponse)
async def get_cv(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    result = await session.execute(
        select(ApplicantProfile).where(ApplicantProfile.user_id == current_user.id)
    )
    profile = result.scalar_one_or_none()
    cv_text = profile.cv_text if profile else None
    return CVResponse(cv_text=cv_text, has_cv=cv_text is not None)
