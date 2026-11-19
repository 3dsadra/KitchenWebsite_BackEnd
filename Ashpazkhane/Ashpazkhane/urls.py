from django.contrib import admin
from django.urls import include, path

from . import panel_views, pwa, views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", views.homepage, name="home"),
    path("login/", views.login_v, name="login"),
    path("logout/", views.logout_v, name="logout"),
    path("manifest.webmanifest", pwa.manifest, name="manifest"),
    path("sw.js", pwa.service_worker, name="service_worker"),
    path("cart/", include("Menu.urls")),
    path("panel/", panel_views.dashboard, name="panel"),
    path("panel/orders/", panel_views.orders, name="panel_orders"),
    path("panel/finance/", panel_views.finance, name="panel_finance"),
    path("panel/payments/", panel_views.payments, name="panel_payments"),
    path(
        "panel/payments/<int:order_id>/check/",
        panel_views.payment_check,
        name="panel_payment_check",
    ),
    path("panel/users/", panel_views.users, name="panel_users"),
    path(
        "panel/users/<int:user_id>/",
        panel_views.user_detail,
        name="panel_user_detail",
    ),
    path("panel/menu/", panel_views.menu, name="panel_menu"),
    path("panel/menu/save/", panel_views.menu_save, name="panel_menu_save"),
    path("panel/menu/create/", panel_views.menu_create, name="panel_menu_create"),
]
