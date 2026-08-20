"""Routes de lecture : la liste et la sante du pipeline. Rien d'autre en V0."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from veille.config import settings
from veille.web.deps import get_session
from veille.web.filters import FILTERS
from veille.web.queries import feed_health, list_articles

BASE_DIR = Path(__file__).parent

app = FastAPI(title="veille", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.filters.update(FILTERS)

SessionDep = Annotated[Session, Depends(get_session)]
LangParam = Annotated[Literal["fr", "en"] | None, Query()]
TopicParam = Annotated[Literal["ai", "sec", "both"] | None, Query()]


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    session: SessionDep,
    lang: LangParam = None,
    topic: TopicParam = None,
    page: Annotated[int, Query(ge=1)] = 1,
) -> HTMLResponse:
    result = list_articles(session, lang=lang, topic=topic, page=page, page_size=settings.page_size)
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "page": result,
            "lang": lang,
            "topic": topic,
            "now": datetime.now(tz=UTC),
        },
    )


@app.get("/feeds", response_class=HTMLResponse)
def feeds(request: Request, session: SessionDep) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="feeds.html",
        context={"health": feed_health(session), "now": datetime.now(tz=UTC)},
    )
