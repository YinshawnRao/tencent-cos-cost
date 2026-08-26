"""FastAPI：账号全局 + 桶页。密钥只留在服务端。"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from cos_cost.formatters import money_text
from cos_cost.monthutil import previous_month_utc8
from cos_cost.secrets import classify_collect_error, sanitize_error_text
from cos_cost.web.service import DashboardService

WEB_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"


def create_app(service: DashboardService) -> FastAPI:
    app = FastAPI(title="COS 机会大师", docs_url=None, redoc_url=None)
    app.state.service = service
    templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
    templates.env.filters["money"] = money_text
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    def account_page(
        request: Request,
        month: str | None = None,
        region: str | None = None,
        q: str | None = None,
    ) -> HTMLResponse:
        payload = service.account(month, region=region or None, q=q or None)
        return templates.TemplateResponse(
            request,
            "account.html",
            {
                "payload": payload,
                "bootstrap": json.dumps(payload, ensure_ascii=False),
            },
        )

    @app.get("/b/{bucket}", response_class=HTMLResponse)
    def bucket_page(
        request: Request,
        bucket: str,
        month: str | None = None,
    ) -> HTMLResponse:
        payload = service.bucket(bucket, month)
        if payload.get("error") == "bucket_not_found":
            raise HTTPException(status_code=404, detail="未找到该存储桶")
        return templates.TemplateResponse(
            request,
            "bucket.html",
            {
                "payload": payload,
                "bootstrap": json.dumps(payload, ensure_ascii=False),
            },
        )

    @app.get("/api/account")
    def api_account(
        month: str | None = Query(default=None),
        region: str | None = Query(default=None),
        q: str | None = Query(default=None),
    ) -> dict:
        return service.account(month, region=region or None, q=q or None)

    @app.get("/api/buckets/{bucket}")
    def api_bucket(bucket: str, month: str | None = Query(default=None)) -> dict:
        payload = service.bucket(bucket, month)
        if payload.get("error") == "bucket_not_found":
            raise HTTPException(status_code=404, detail="未找到该存储桶")
        return payload

    @app.get("/api/health")
    def api_health() -> dict:
        status = service.settings_status()
        return {
            "ok": True,
            "mock": service.mock,
            "mode": status["mode"],
            "default_month": previous_month_utc8(),
        }

    @app.get("/export/pdf")
    def export_pdf(month: str | None = Query(default=None)) -> Response:
        from cos_cost.ext.export import render_pdf

        payload = service.report_payload(month)
        data = render_pdf(payload)
        stamp = payload.get("month") or "report"
        return Response(
            content=data,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="cos-cost-{stamp}.pdf"'},
        )

    @app.get("/export/xlsx")
    def export_xlsx(month: str | None = Query(default=None)) -> Response:
        from cos_cost.ext.export import render_xlsx

        payload = service.report_payload(month)
        data = render_xlsx(payload)
        stamp = payload.get("month") or "report"
        return Response(
            content=data,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="cos-cost-{stamp}.xlsx"'},
        )

    @app.post("/api/ask")
    async def api_ask(request: Request) -> dict:
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="需要 JSON {q, month}")
        question = str(body.get("q") or body.get("question") or "").strip()
        if not question:
            raise HTTPException(status_code=400, detail="缺少 q")
        month = body.get("month")
        return service.ask(question, month if isinstance(month, str) else None)

    @app.get("/api/settings/status")
    def api_settings_status() -> dict:
        return service.settings_status()

    @app.post("/api/settings/credentials")
    async def api_settings_credentials(request: Request) -> dict:
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="需要 JSON {secret_id, secret_key}")
        secret_id = str(body.get("secret_id") or "")
        secret_key = str(body.get("secret_key") or "")
        try:
            return service.save_credentials(
                secret_id=secret_id,
                secret_key=secret_key,
                token=str(body.get("token") or "") or None,
                month=str(body.get("month") or "") or None,
                model_api_key=str(body.get("model_api_key") or "") or None,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=sanitize_error_text(str(exc), secret_key=secret_key, secret_id=secret_id),
            ) from exc
        except Exception as exc:  # pragma: no cover — unexpected collect crash
            raise HTTPException(
                status_code=400,
                detail=classify_collect_error(
                    sanitize_error_text(str(exc), secret_key=secret_key, secret_id=secret_id)
                ),
            ) from exc

    @app.get("/api/settings/job")
    def api_settings_job() -> dict:
        return service.job_status()

    @app.post("/api/settings/job/cancel")
    def api_settings_job_cancel() -> dict:
        return service.cancel_collect()

    @app.post("/api/settings/mock")
    def api_settings_mock() -> dict:
        return service.use_mock()

    return app
