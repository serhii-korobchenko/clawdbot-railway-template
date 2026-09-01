from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from ..auth import is_authenticated, sign_in, sign_out, verify_password


router = APIRouter()


@router.get("/login")
async def login_page(request: Request):
    if is_authenticated(request):
        return RedirectResponse("/", status_code=303)

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"error": None},
    )


@router.post("/login")
async def login(request: Request, password: str = Form(...)):
    settings = request.app.state.settings

    if not verify_password(password, settings.password):
        templates = request.app.state.templates
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"error": "Невірний пароль."},
            status_code=401,
        )

    sign_in(request)
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
async def logout(request: Request):
    sign_out(request)
    return RedirectResponse("/login", status_code=303)
