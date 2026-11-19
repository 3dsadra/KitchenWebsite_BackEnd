from django.contrib import admin

from .models import MenuEntry, MonthlyMenu, Order, OrderItem


class MenuEntryInline(admin.TabularInline):
    model = MenuEntry
    extra = 0
    fields = ("slot", "name", "cost", "capacity")
    ordering = ("slot",)


@admin.register(MonthlyMenu)
class MonthlyMenuAdmin(admin.ModelAdmin):
    list_display = ("__str__", "entries_count", "total_cost")
    list_filter = ("year", "month")
    ordering = ("-year", "-month")
    inlines = [MenuEntryInline]

    @admin.display(description="تعداد وعده")
    def entries_count(self, obj):
        return obj.slots.count()

    @admin.display(description="جمع هزینه (تومان)")
    def total_cost(self, obj):
        total = sum(entry.cost for entry in obj.slots.all())
        return f"{total:,}"


@admin.register(MenuEntry)
class MenuEntryAdmin(admin.ModelAdmin):
    list_display = ("menu", "slot", "name", "cost", "capacity")
    list_filter = ("menu__year", "menu__month")
    search_fields = ("name",)
    ordering = ("menu", "slot")


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ("date_label",)
    fields = ("menu_year", "menu_month", "slot", "name", "cost", "date_label")
    ordering = ("menu_year", "menu_month", "slot")


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "status", "amount", "created_at", "paid_at")
    list_filter = ("status", "created_at")
    search_fields = ("user__username", "user__first_name", "user__last_name", "ref_id")
    readonly_fields = ("authority", "ref_id", "created_at", "paid_at")
    inlines = [OrderItemInline]
    ordering = ("-created_at",)


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ("order", "menu_year", "menu_month", "slot", "name", "cost")
    list_filter = ("menu_year", "menu_month")
    search_fields = ("name", "order__user__username")
    ordering = ("-order__created_at", "slot")
