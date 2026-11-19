from django.db.models import Sum

from .models import Order, OrderItem


def cart(request):
    """تعداد و مبلغ وعده‌های داخل سبد خرید را برای همهٔ قالب‌ها فراهم می‌کند."""
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {"cart_count": 0, "cart_total": 0}

    items = OrderItem.objects.filter(
        order__user=user, order__status=Order.Status.PENDING
    )
    return {
        "cart_count": items.count(),
        "cart_total": items.aggregate(total=Sum("cost")).get("total") or 0,
    }
