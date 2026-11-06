import io
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from backend.database import get_session
from backend.models import ApplicantProfile, User
from backend.auth.dependencies import get_current_user
from backend.cv.redact import redact

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


class ExtractResponse(BaseModel):
    text: str


class RedactRequest(BaseModel):
    text: str


class RedactResponse(BaseModel):
    text: str
    removed: dict[str, int]


class SaveRequest(BaseModel):
    text: str
    confirm: bool = False


async def _get_or_create_profile(session: AsyncSession, user_id) -> ApplicantProfile:
    result = await session.execute(
        select(ApplicantProfile).where(ApplicantProfile.user_id == user_id)
    )
    profile = result.scalar_one_or_none()
    if profile is None:
        profile = ApplicantProfile(user_id=user_id)
        session.add(profile)
    return profile


@router.post("/cv/extract", response_model=ExtractResponse)
async def extract_cv(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    filename = file.filename or ""
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in _ALLOWED:
        raise HTTPException(status_code=400, detail="Unsupported file type. Upload PDF, DOCX, or TXT.")
    text = _extract_text(filename, await file.read())
    if not text.strip():
        raise HTTPException(status_code=400, detail="Could not extract text from file.")
    return ExtractResponse(text=text)


@router.post("/cv/redact", response_model=RedactResponse)
async def redact_cv(
    body: RedactRequest,
    current_user: User = Depends(get_current_user),
):
    text, removed = redact(body.text)
    return RedactResponse(text=text, removed=removed)


@router.put("/cv", response_model=CVResponse)
async def save_cv(
    body: SaveRequest,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    if not body.text.strip():
        # An empty editor saves as no CV, so advanced search asks for one again.
        profile = await _get_or_create_profile(session, current_user.id)
        profile.cv_text = None
        await session.commit()
        return CVResponse(cv_text=None, has_cv=False)
    if not body.confirm:
        _, findings = redact(body.text)
        if findings:
            raise HTTPException(status_code=409, detail={"findings": findings})
    profile = await _get_or_create_profile(session, current_user.id)
    profile.cv_text = body.text
    session.add(profile)
    await session.commit()
    return CVResponse(cv_text=body.text, has_cv=True)


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
