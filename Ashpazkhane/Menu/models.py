from django.conf import settings
from django.db import models


class MonthlyMenu(models.Model):
    year = models.PositiveIntegerField(verbose_name="سال (شمسی)", help_text="مثلاً ۱۴۰۵")
    month = models.PositiveSmallIntegerField(
        verbose_name="ماه (شمسی)", help_text="۱ = فروردین تا ۱۲ = اسفند"
    )

    class Meta:
        verbose_name = "منوی ماهانه"
        verbose_name_plural = "منوهای ماهانه"
        unique_together = ("year", "month")
        ordering = ["-year", "-month"]

    def __str__(self):
        from Ashpazkhane.jalali import JALALI_MONTHS

        if 1 <= self.month <= 12:
            return f"{JALALI_MONTHS[self.month - 1]} {self.year}"
        return f"{self.year}-{self.month:02d}"

    @property
    def key(self):
        return f"{self.year}-{self.month:02d}"

    @property
    def label_fa(self):
        from Ashpazkhane.jalali import month_label

        return month_label(self)

    @classmethod
    def for_date(cls, d):
        from Ashpazkhane.jalali import to_jalali

        jy, jm, _ = to_jalali(d)
        return cls.objects.filter(year=jy, month=jm).first()


class MenuEntry(models.Model):
    menu = models.ForeignKey(
        MonthlyMenu, on_delete=models.CASCADE, related_name="slots", verbose_name="منو"
    )
    slot = models.SmallIntegerField(verbose_name="روز ماه", help_text="روز شمسی، از ۱ تا ۳۱")
    name = models.CharField(max_length=100, verbose_name="نام غذا")
    cost = models.PositiveBigIntegerField(verbose_name="قیمت (تومان)")
    capacity = models.PositiveIntegerField(
        default=0,
        verbose_name="ظرفیت روز",
        help_text="حداکثر تعداد غذا برای این روز؛ صفر یعنی بدون محدودیت.",
    )

    class Meta:
        verbose_name = "وعدهٔ منو"
        verbose_name_plural = "وعده‌های منو"
        unique_together = ("menu", "slot")
        ordering = ["slot"]

    def __str__(self):
        return f"Day {self.slot}: {self.name} ({self.cost})"


class Order(models.Model):
    """سبد خرید و سفارش کاربر؛ هر کاربر یک سفارش «در انتظار پرداخت» دارد."""

    class Status(models.TextChoices):
        PENDING = "pending", "در انتظار پرداخت"
        PAID = "paid", "پرداخت‌شده"
        CANCELED = "canceled", "لغوشده"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="orders"
    )
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING
    )
    amount = models.PositiveBigIntegerField(default=0, verbose_name="مبلغ (تومان)")
    authority = models.CharField(max_length=64, blank=True, default="")
    ref_id = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "سفارش"
        verbose_name_plural = "سفارش‌ها"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["authority"]),
        ]

    def __str__(self):
        return f"{self.user} — {self.get_status_display()} ({self.amount})"

    @property
    def total(self):
        return sum(item.cost for item in self.items.all())

    @property
    def amount_fa(self):
        from Ashpazkhane.jalali import toman

        return toman(self.amount)

    @property
    def ref_id_fa(self):
        from Ashpazkhane.jalali import fa_digits

        return fa_digits(self.ref_id) if self.ref_id else ""


class OrderItem(models.Model):
    """یک وعدهٔ خریداری‌شده؛ اطلاعات غذا به‌صورت عکس‌لحظه‌ای ذخیره می‌شود."""

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    menu = models.ForeignKey(
        MonthlyMenu,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_items",
        verbose_name="منو",
    )
    menu_year = models.PositiveIntegerField()
    menu_month = models.PositiveSmallIntegerField()
    slot = models.SmallIntegerField(verbose_name="روز ماه")
    name = models.CharField(max_length=100, verbose_name="نام غذا")
    cost = models.PositiveBigIntegerField(verbose_name="قیمت (تومان)")

    class Meta:
        verbose_name = "وعدهٔ سفارش"
        verbose_name_plural = "وعده‌های سفارش"
        constraints = [
            models.UniqueConstraint(
                fields=["order", "menu", "slot"], name="unique_order_menu_slot"
            )
        ]

    def __str__(self):
        return f"{self.name} — {self.menu_year}/{self.menu_month}/{self.slot}"

    @property
    def date_label(self):
        from Ashpazkhane.jalali import format_jalali_parts

        return format_jalali_parts(
            self.menu_year, self.menu_month, self.slot, with_weekday=True
        )

    @property
    def cost_fa(self):
        from Ashpazkhane.jalali import toman

        return toman(self.cost)


class PaymentAttempt(models.Model):
    """هر تلاش پرداخت، موفق یا ناموفق، برای پیگیری مالی ثبت می‌شود."""

    class Status(models.TextChoices):
        REQUESTED = "requested", "درخواست پرداخت"
        VERIFIED = "verified", "تأییدشده"
        FAILED = "failed", "ناموفق"
        CANCELED = "canceled", "لغوشده"

    order = models.ForeignKey(
        Order, on_delete=models.CASCADE, related_name="attempts"
    )
    amount = models.PositiveBigIntegerField(default=0, verbose_name="مبلغ (تومان)")
    authority = models.CharField(max_length=64, blank=True, default="")
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.REQUESTED
    )
    gateway_code = models.CharField(max_length=16, blank=True, default="")
    ref_id = models.CharField(max_length=64, blank=True, default="")
    message = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "تلاش پرداخت"
        verbose_name_plural = "تلاش‌های پرداخت"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["authority"])]

    def __str__(self):
        return f"{self.order_id} — {self.get_status_display()}"

    @property
    def created_fa(self):
        from django.utils import timezone

        from Ashpazkhane.jalali import fa_digits, format_jalali

        local = timezone.localtime(self.created_at)
        return (
            f"{format_jalali(local.date())} ساعت "
            f"{fa_digits(local.strftime('%H:%M'))}"
        )

    @property
    def amount_fa(self):
        from Ashpazkhane.jalali import toman

        return toman(self.amount)