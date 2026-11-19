from django.urls import path

from . import views

app_name = "orders"

urlpatterns = [
    path("", views.cart, name="cart"),
    path("add/<int:menu_id>/<int:slot>/", views.add_to_cart, name="add"),
    path("add-month/<int:menu_id>/", views.add_month, name="add_month"),
    path("remove/<int:item_id>/", views.remove_item, name="remove"),
    path("pay/", views.checkout, name="checkout"),
    path("callback/", views.payment_callback, name="callback"),
    path("history/", views.history, name="history"),
]
