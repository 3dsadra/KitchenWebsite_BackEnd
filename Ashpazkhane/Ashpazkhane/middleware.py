"""مدیران فقط به پنل مدیریت دسترسی دارند، نه به صفحه‌های کاربر عادی."""

from django.shortcuts import redirect

ALLOWED_PREFIXES = ("/panel/", "/admin/", "/static/", "/media/")
ALLOWED_PATHS = {
    "/panel",
    "/login/",
    "/logout/",
    "/favicon.ico",
    "/manifest.webmanifest",
    "/sw.js",
}


class StaffPanelOnlyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)

        # جنگو برای کاربر غیرفعال کاربر ناشناس برمی‌گرداند؛ بنابراین اگر نشست
        # هنوز شناسهٔ کاربر داشته باشد ولی احراز هویت معتبر نباشد (مثلاً حساب
        # پس از ورود غیرفعال یا حذف شده باشد)، نشست همان‌جا بسته شود.
        if request.session.get("_auth_user_id") and (
            user is None or not user.is_authenticated
        ):
            request.session.flush()
            user = request.user

        if user is not None and user.is_authenticated and user.is_staff:
            path = request.path

            # مدیریت پیشرفتهٔ جنگو فقط برای مدیر ارشد.
            if path.startswith("/admin/") and not user.is_superuser:
                return redirect("/panel/")

            allowed = path in ALLOWED_PATHS or path.startswith(ALLOWED_PREFIXES)
            if not allowed:
                return redirect("/panel/")

        return self.get_response(request)
