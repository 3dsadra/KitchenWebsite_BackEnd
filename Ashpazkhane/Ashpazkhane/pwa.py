"""فایل‌های PWA: مانیفست و سرویس‌ورکر در ریشهٔ سایت."""

from django.conf import settings
from django.http import HttpResponse
from django.template.loader import render_to_string


def manifest(request):
    content = render_to_string("pwa/manifest.webmanifest")
    response = HttpResponse(content, content_type="application/manifest+json")
    response["Cache-Control"] = "no-cache"
    return response


def service_worker(request):
    path = settings.BASE_DIR / "static" / "js" / "sw.js"
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return HttpResponse("", content_type="application/javascript", status=404)

    response = HttpResponse(content, content_type="application/javascript")
    response["Service-Worker-Allowed"] = "/"
    response["Cache-Control"] = "no-cache"
    return response
