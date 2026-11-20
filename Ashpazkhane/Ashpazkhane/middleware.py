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

        if request.session.get("_auth_user_id") and (
            user is None or not user.is_authenticated
        ):
            request.session.flush()
            user = request.user

        if user is not None and user.is_authenticated and user.is_staff:
            path = request.path

            if path.startswith("/admin/") and not user.is_superuser:
                return redirect("/panel/")

            allowed = path in ALLOWED_PATHS or path.startswith(ALLOWED_PREFIXES)
            if not allowed:
                return redirect("/panel/")

        return self.get_response(request)
