"""HTML 页面路由：SPA 入口与登录页。"""

import os

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from auth import get_current_user_optional

router = APIRouter()

_APP_DIR = os.path.dirname(os.path.dirname(__file__))
_STATIC_DIR = os.path.join(_APP_DIR, "static")
_TEMPLATES_DIR = os.path.join(_APP_DIR, "templates")
templates = Jinja2Templates(directory=_TEMPLATES_DIR)


@router.get("/favicon.ico", include_in_schema=False)
async def favicon_ico():
    path = os.path.join(_STATIC_DIR, "favicon.svg")
    if os.path.isfile(path):
        return FileResponse(path, media_type="image/svg+xml")
    return HTMLResponse(status_code=204)


@router.get("/.well-known/appspecific/com.chrome.devtools.json", include_in_schema=False)
async def chrome_devtools_wellknown():
    return JSONResponse({})


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, user=Depends(get_current_user_optional)):
    if user:
        return RedirectResponse(url="/", status_code=302)
    login_path = os.path.join(_TEMPLATES_DIR, "login_page.html")
    if os.path.exists(login_path):
        return templates.TemplateResponse(request=request, name="login_page.html", context={"request": request})
    return HTMLResponse("<h1>Clash Hub</h1><p>模板文件缺失</p>")


@router.get("/", response_class=HTMLResponse)
@router.get("/overview", response_class=HTMLResponse)
@router.get("/subs", response_class=HTMLResponse)
@router.get("/imports", response_class=HTMLResponse)
@router.get("/profiles", response_class=HTMLResponse)
@router.get("/templates", response_class=HTMLResponse)
@router.get("/settings", response_class=HTMLResponse)
@router.get("/config", response_class=HTMLResponse)
@router.get("/logs", response_class=HTMLResponse)
async def app_root(request: Request, user=Depends(get_current_user_optional)):
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    app_path = os.path.join(_TEMPLATES_DIR, "app_page.html")
    if os.path.exists(app_path):
        return templates.TemplateResponse(request=request, name="app_page.html", context={"request": request})
    return HTMLResponse("<h1>Clash Hub</h1><p>模板文件缺失</p>")
