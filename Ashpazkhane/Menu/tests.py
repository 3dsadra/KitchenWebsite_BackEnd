from io import BytesIO
from datetime import datetime, timedelta
from unittest.mock import patch

from django.contrib.auth import authenticate, get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from Ashpazkhane.jalali import is_off_day, jalali_weekday, weekday_index

from .models import MenuEntry, MonthlyMenu, Order, OrderItem, PaymentAttempt
from .services import is_order_closed, order_deadline


class PurchaseFlowTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="0012345678", password="secret123", first_name="سارا", last_name="احمدی"
        )
        self.menu = MonthlyMenu.objects.create(year=1410, month=1)
        for slot, name, cost in [
            (2, "Adas Polo", 80000),
            (3, "Ghorme Sabzi", 90000),
            (9, "Zereshk Polo", 95000),
            (10, "Khoresh Bademjan", 85000),
        ]:
            MenuEntry.objects.create(menu=self.menu, slot=slot, name=name, cost=cost)

    def test_guest_cannot_add_day(self):
        response = self.client.post(
            reverse("orders:add", args=[self.menu.pk, 2])
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])
        self.assertFalse(OrderItem.objects.exists())

    def test_guest_home_has_no_buy_form(self):
        response = self.client.get("/", {"m": "1410-01"})
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, reverse("orders:add", args=[self.menu.pk, 2]))
        self.assertContains(response, "ورود برای خرید")

    def test_add_single_day(self):
        self.client.login(username="0012345678", password="secret123")
        response = self.client.post(reverse("orders:add", args=[self.menu.pk, 2]))
        self.assertRedirects(response, reverse("orders:cart"))
        self.assertEqual(OrderItem.objects.count(), 1)

        cart = self.client.get(reverse("orders:cart"))
        self.assertContains(cart, "Adas Polo")

    def test_add_whole_month(self):
        self.client.login(username="0012345678", password="secret123")
        self.client.post(reverse("orders:add_month", args=[self.menu.pk]))
        order = Order.objects.get(user=self.user, status=Order.Status.PENDING)
        self.assertEqual(order.items.count(), 4)

    def test_paid_days_are_not_added_again(self):
        paid = Order.objects.create(user=self.user, status=Order.Status.PAID)
        OrderItem.objects.create(
            order=paid,
            menu=self.menu,
            menu_year=1410,
            menu_month=1,
            slot=2,
            name="Adas Polo",
            cost=80000,
        )

        self.client.login(username="0012345678", password="secret123")
        self.client.post(reverse("orders:add_month", args=[self.menu.pk]))
        pending = Order.objects.get(user=self.user, status=Order.Status.PENDING)
        self.assertEqual(pending.items.count(), 3)
        self.assertFalse(pending.items.filter(slot=2).exists())

    @patch("Menu.views.zarinpal.request_payment")
    def test_checkout_redirects_to_gateway(self, request_payment):
        request_payment.return_value = {"data": {"code": 100, "authority": "A0001"}}

        self.client.login(username="0012345678", password="secret123")
        self.client.post(reverse("orders:add", args=[self.menu.pk, 2]))
        response = self.client.post(reverse("orders:checkout"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("A0001", response["Location"])
        order = Order.objects.get(user=self.user, status=Order.Status.PENDING)
        self.assertEqual(order.authority, "A0001")
        self.assertEqual(order.amount, 80000)
        request_payment.assert_called_once()
        self.assertEqual(request_payment.call_args.args[0], 800000)

    @patch("Menu.views.zarinpal.verify_payment")
    def test_successful_callback_marks_order_paid(self, verify_payment):
        verify_payment.return_value = {"data": {"code": 100, "ref_id": "R123456"}}

        self.client.login(username="0012345678", password="secret123")
        self.client.post(reverse("orders:add", args=[self.menu.pk, 2]))
        order = Order.objects.get(user=self.user, status=Order.Status.PENDING)
        order.authority = "A0001"
        order.amount = 80000
        order.save()

        response = self.client.get(
            reverse("orders:callback"), {"Authority": "A0001", "Status": "OK"}
        )
        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PAID)
        self.assertEqual(order.ref_id, "R123456")

        history = self.client.get(reverse("orders:history"))
        self.assertContains(history, "Adas Polo")


class OffDayTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="0012345678", password="secret123"
        )
        self.menu = MonthlyMenu.objects.create(year=1410, month=1)
        # 1410-01-01 is a Friday.
        self.off_slot = 1
        self.assertEqual(weekday_index(jalali_weekday(1410, 1, 1)), 6)
        MenuEntry.objects.create(
            menu=self.menu, slot=self.off_slot, name="Off day meal", cost=50000
        )

    def test_off_day_cannot_be_added(self):
        self.client.login(username="0012345678", password="secret123")
        self.client.post(reverse("orders:add", args=[self.menu.pk, self.off_slot]))
        self.assertFalse(OrderItem.objects.exists())

    def test_off_day_is_not_added_with_whole_month(self):
        self.client.login(username="0012345678", password="secret123")
        self.client.post(reverse("orders:add_month", args=[self.menu.pk]))
        self.assertFalse(OrderItem.objects.exists())

    def test_home_shows_closed_day(self):
        response = self.client.get("/", {"m": "1410-01"})
        self.assertContains(response, "تعطیل")
        self.assertNotContains(
            response, reverse("orders:add", args=[self.menu.pk, self.off_slot])
        )


class AdminPanelTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.staff = User.objects.create_user(
            username="0000000000",
            password="admin12345",
            first_name="مدیر",
            last_name="آشپزخانه",
            is_staff=True,
        )
        self.student = User.objects.create_user(
            username="1112223334",
            password="secret123",
            first_name="علی",
            last_name="رضایی",
        )
        self.other = User.objects.create_user(
            username="5556667778",
            password="secret123",
            first_name="مریم",
            last_name="کریمی",
        )
        self.menu = MonthlyMenu.objects.create(year=1410, month=2)
        MenuEntry.objects.create(menu=self.menu, slot=2, name="قورمه‌سبزی", cost=90000)
        MenuEntry.objects.create(menu=self.menu, slot=3, name="قیمه", cost=85000)

        self._make_paid(self.student, 2, "قورمه‌سبزی", 90000, "R1")
        self._make_paid(self.other, 3, "قیمه", 85000, "R2")

    def _make_paid(self, user, slot, name, cost, ref):
        order = Order.objects.create(
            user=user, status=Order.Status.PAID, amount=cost, ref_id=ref
        )
        OrderItem.objects.create(
            order=order,
            menu=self.menu,
            menu_year=self.menu.year,
            menu_month=self.menu.month,
            slot=slot,
            name=name,
            cost=cost,
        )
        return order

    def test_panel_requires_staff(self):
        response = self.client.get(reverse("panel"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

        self.client.login(username="1112223334", password="secret123")
        response = self.client.get(reverse("panel"))
        self.assertRedirects(response, reverse("home"))

    def test_dashboard_renders(self):
        self.client.login(username="0000000000", password="admin12345")
        response = self.client.get(reverse("panel"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "علی رضایی")

    def test_orders_filter_and_search(self):
        self.client.login(username="0000000000", password="admin12345")

        response = self.client.get(
            reverse("panel_orders"), {"month": "1410-02"}
        )
        self.assertContains(response, "علی رضایی")
        self.assertContains(response, "مریم کریمی")

        response = self.client.get(
            reverse("panel_orders"), {"month": "1410-02", "q": "1112223334"}
        )
        self.assertContains(response, "علی رضایی")
        self.assertNotContains(response, "مریم کریمی")

        response = self.client.get(
            reverse("panel_orders"), {"month": "1410-02", "day": "3"}
        )
        self.assertContains(response, "مریم کریمی")
        self.assertNotContains(response, "علی رضایی")

    def test_excel_export(self):
        from openpyxl import load_workbook

        self.client.login(username="0000000000", password="admin12345")
        response = self.client.get(
            reverse("panel_orders"), {"month": "1410-02", "export": "xlsx"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("spreadsheetml", response["Content-Type"])
        self.assertIn("attachment", response["Content-Disposition"])

        workbook = load_workbook(BytesIO(response.content))
        self.assertIn("جزئیات سفارش‌ها", workbook.sheetnames)
        detail = workbook["جزئیات سفارش‌ها"]
        values = [cell.value for row in detail.iter_rows() for cell in row]
        self.assertIn("علی رضایی", values)
        self.assertIn("مریم کریمی", values)

    def test_create_user_from_panel(self):
        self.client.login(username="0000000000", password="admin12345")
        response = self.client.post(
            reverse("panel_users"),
            {
                "first_name": "زهرا",
                "last_name": "محمدی",
                "national_code": "2223334445",
                "password": "secret123",
            },
        )
        self.assertRedirects(response, reverse("panel_users"))
        user = get_user_model().objects.get(username="2223334445")
        self.assertEqual(user.first_name, "زهرا")
        self.assertFalse(user.is_staff)
        self.assertIsNotNone(
            authenticate(username="2223334445", password="secret123")
        )

    def test_menu_save_ignores_off_days(self):
        self.client.login(username="0000000000", password="admin12345")
        off_day = next(
            day for day in range(1, 8) if is_off_day(1410, 2, day)
        )
        response = self.client.post(
            reverse("panel_menu_save"),
            {
                "menu_id": self.menu.pk,
                f"name_{off_day}": "غذای تعطیل",
                f"cost_{off_day}": "50000",
                "name_6": "لوبیاپلو",
                "cost_6": "75000",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            MenuEntry.objects.filter(menu=self.menu, slot=off_day).exists()
        )
        self.assertTrue(
            MenuEntry.objects.filter(menu=self.menu, slot=6, name="لوبیاپلو").exists()
        )

    def test_admin_can_login_with_code(self):
        user = authenticate(username="0000000000", password="admin12345")
        self.assertIsNotNone(user)
        self.assertTrue(user.is_staff)

    def test_staff_is_redirected_from_site_pages(self):
        self.client.login(username="0000000000", password="admin12345")

        for path in ("/", "/cart/", "/cart/history/", "/login/"):
            response = self.client.get(path)
            self.assertRedirects(response, reverse("panel"), fetch_redirect_response=False)

    def test_staff_login_goes_to_panel(self):
        response = self.client.post(
            reverse("login"),
            {"username": "0000000000", "password": "admin12345"},
        )
        self.assertRedirects(response, reverse("panel"), fetch_redirect_response=False)

    def test_staff_logout_goes_to_login(self):
        self.client.login(username="0000000000", password="admin12345")
        response = self.client.get(reverse("logout"))
        self.assertRedirects(
            response, reverse("login"), fetch_redirect_response=False
        )

    def test_delivery_excel_for_a_day(self):
        from openpyxl import load_workbook

        self.client.login(username="0000000000", password="admin12345")
        response = self.client.get(
            reverse("panel_orders"),
            {
                "month": "1410-02",
                "day": "2",
                "delivery": "xlsx",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("spreadsheetml", response["Content-Type"])

        workbook = load_workbook(BytesIO(response.content))
        sheet = workbook["لیست تحویل"]
        values = [cell.value for row in sheet.iter_rows() for cell in row]
        self.assertIn("علی رضایی", values)
        self.assertNotIn("مریم کریمی", values)

    def test_finance_page(self):
        self.client.login(username="0000000000", password="admin12345")
        response = self.client.get(reverse("panel_finance"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "گزارش مالی")
        self.assertContains(response, "اردیبهشت ۱۴۱۰")

    def test_payments_page_lists_attempts(self):
        order = Order.objects.create(
            user=self.student,
            status=Order.Status.PENDING,
            amount=90000,
            authority="A-TEST",
        )
        PaymentAttempt.objects.create(
            order=order, amount=90000, authority="A-TEST", status="requested"
        )
        self.client.login(username="0000000000", password="admin12345")
        response = self.client.get(reverse("panel_payments"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "تاریخچهٔ تلاش‌های پرداخت")

    @patch("Ashpazkhane.panel_views.zarinpal.verify_payment")
    def test_manual_payment_check_marks_paid(self, verify_payment):
        verify_payment.return_value = {"data": {"code": 100, "ref_id": "RX"}}
        order = Order.objects.create(
            user=self.student,
            status=Order.Status.PENDING,
            amount=90000,
            authority="A-TEST",
        )
        self.client.login(username="0000000000", password="admin12345")
        response = self.client.post(
            reverse("panel_payment_check", args=[order.pk])
        )
        self.assertRedirects(response, reverse("panel_payments"))
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PAID)
        self.assertEqual(order.ref_id, "RX")
        self.assertTrue(
            PaymentAttempt.objects.filter(
                order=order, status=PaymentAttempt.Status.VERIFIED
            ).exists()
        )

    def test_password_reset_and_deactivate_user(self):
        self.client.login(username="0000000000", password="admin12345")

        response = self.client.post(
            reverse("panel_user_detail", args=[self.student.pk]),
            {"action": "password", "password": "newsecret1"},
        )
        self.assertRedirects(
            response, reverse("panel_user_detail", args=[self.student.pk])
        )
        self.assertIsNotNone(
            authenticate(username=self.student.username, password="newsecret1")
        )

        self.client.post(
            reverse("panel_user_detail", args=[self.student.pk]),
            {"action": "toggle_active"},
        )
        self.student.refresh_from_db()
        self.assertFalse(self.student.is_active)
        self.assertIsNone(
            authenticate(username=self.student.username, password="newsecret1")
        )

    def test_admin_cannot_deactivate_self(self):
        self.client.login(username="0000000000", password="admin12345")
        self.client.post(
            reverse("panel_user_detail", args=[self.staff.pk]),
            {"action": "toggle_active"},
        )
        self.staff.refresh_from_db()
        self.assertTrue(self.staff.is_active)


class OrderWindowTests(TestCase):
    """مهلت سفارش: تا ساعت ۹ صبح همان روز."""

    def test_deadline_is_nine_in_the_morning(self):
        deadline = order_deadline(1410, 1, 2)
        local = timezone.localtime(deadline)
        self.assertEqual((local.hour, local.minute), (9, 0))

    def test_friday_can_still_order_saturday_before_nine(self):
        friday = jalali_weekday(1410, 1, 1)
        friday_noon = timezone.make_aware(
            datetime(friday.year, friday.month, friday.day, 12, 0)
        )
        self.assertFalse(
            is_order_closed(1410, 1, 2, now=friday_noon)
        )

    def test_saturday_is_closed_after_nine(self):
        deadline = order_deadline(1410, 1, 2)
        self.assertTrue(
            is_order_closed(1410, 1, 2, now=deadline + timedelta(minutes=1))
        )
        self.assertFalse(
            is_order_closed(1410, 1, 2, now=deadline - timedelta(minutes=1))
        )


class CapacityTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="0012345678", password="secret123", first_name="سارا"
        )
        self.other = get_user_model().objects.create_user(
            username="1112223334", password="secret123", first_name="علی"
        )
        self.menu = MonthlyMenu.objects.create(year=1410, month=1)
        # 1410-01-02 is a Saturday in the future, so the order window is open.
        self.entry = MenuEntry.objects.create(
            menu=self.menu, slot=2, name="Adas Polo", cost=80000, capacity=1
        )

    def _pay(self, user):
        order = Order.objects.create(user=user, status=Order.Status.PAID)
        OrderItem.objects.create(
            order=order,
            menu=self.menu,
            menu_year=1410,
            menu_month=1,
            slot=2,
            name="Adas Polo",
            cost=80000,
        )

    def test_full_day_cannot_be_added(self):
        self._pay(self.other)
        self.client.login(username="0012345678", password="secret123")
        self.client.post(reverse("orders:add", args=[self.menu.pk, 2]))
        self.assertFalse(OrderItem.objects.filter(order__user=self.user).exists())

    def test_day_with_free_capacity_can_be_added(self):
        self.client.login(username="0012345678", password="secret123")
        response = self.client.post(reverse("orders:add", args=[self.menu.pk, 2]))
        self.assertRedirects(response, reverse("orders:cart"))
        self.assertTrue(OrderItem.objects.filter(order__user=self.user).exists())


class SecurityTests(TestCase):

    def setUp(self):
        cache.clear()
        User = get_user_model()
        self.superuser = User.objects.create_superuser(
            username="0000000000",
            password="admin12345",
            first_name="مدیر",
            last_name="ارشد",
        )
        self.staff = User.objects.create_user(
            username="2222222222",
            password="secret123",
            first_name="مدیر",
            last_name="ساده",
            is_staff=True,
        )
        self.other_staff = User.objects.create_user(
            username="3333333333",
            password="secret123",
            first_name="مدیر",
            last_name="دیگر",
            is_staff=True,
        )
        self.student = User.objects.create_user(
            username="0012345678",
            password="secret123",
            first_name="سارا",
            last_name="احمدی",
        )
        self.menu = MonthlyMenu.objects.create(year=1410, month=4)
        self.entry = MenuEntry.objects.create(
            menu=self.menu, slot=2, name="قورمه‌سبزی", cost=90000, capacity=1
        )

    def _paid_order(self, user, slot=2, name="قورمه‌سبزی", cost=90000):
        order = Order.objects.create(
            user=user, status=Order.Status.PAID, amount=cost, ref_id="R-1"
        )
        OrderItem.objects.create(
            order=order,
            menu=self.menu,
            menu_year=self.menu.year,
            menu_month=self.menu.month,
            slot=slot,
            name=name,
            cost=cost,
        )
        return order

    def test_guest_cannot_open_panel_pages(self):
        for name in (
            "panel",
            "panel_orders",
            "panel_finance",
            "panel_payments",
            "panel_users",
            "panel_menu",
        ):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 302, name)
            self.assertIn("/login/", response["Location"], name)

    def test_non_superuser_staff_cannot_open_django_admin(self):
        self.client.login(username="2222222222", password="secret123")
        response = self.client.get("/admin/")
        self.assertRedirects(response, "/panel/")

    def test_non_superuser_cannot_create_staff(self):
        self.client.login(username="2222222222", password="secret123")
        response = self.client.post(
            reverse("panel_users"),
            {
                "first_name": "نفوذی",
                "last_name": "تست",
                "national_code": "4444444444",
                "password": "secret123",
                "is_staff": "on",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            get_user_model().objects.filter(username="4444444444").exists()
        )

    def test_non_superuser_cannot_reset_other_staff_password(self):
        self.client.login(username="2222222222", password="secret123")
        self.client.post(
            reverse("panel_user_detail", args=[self.other_staff.pk]),
            {"action": "password", "password": "changedpass1"},
        )
        self.assertIsNone(
            authenticate(username="3333333333", password="changedpass1")
        )
        self.assertIsNotNone(
            authenticate(username="3333333333", password="secret123")
        )

    def test_open_redirect_target_is_rejected(self):
        for target in ("https://evil.example/", "//evil.example/"):
            response = self.client.post(
                reverse("login"),
                {
                    "username": "0012345678",
                    "password": "secret123",
                    "next": target,
                },
            )
            self.assertRedirects(response, "/")
            self.client.logout()

    def test_excel_formula_injection_is_escaped(self):
        from openpyxl import load_workbook

        attacker = get_user_model().objects.create_user(
            username="5555555555",
            password="secret123",
            first_name="=HYPERLINK(\"http://evil\")",
            last_name="تست",
        )
        self._paid_order(attacker)
        self.client.login(username="0000000000", password="admin12345")
        response = self.client.get(
            reverse("panel_orders"), {"month": "1410-04", "export": "xlsx"}
        )
        workbook = load_workbook(BytesIO(response.content))
        values = [
            cell.value
            for row in workbook["جزئیات سفارش‌ها"].iter_rows()
            for cell in row
        ]
        self.assertTrue(
            any(
                isinstance(value, str) and value.startswith("'=HYPERLINK")
                for value in values
            )
        )

    @patch("Menu.views.zarinpal.request_payment")
    def test_checkout_drops_item_that_lost_capacity(self, request_payment):
        self._paid_order(self.student)
        other = get_user_model().objects.create_user(
            username="6666666666", password="secret123"
        )
        pending = Order.objects.create(user=other, status=Order.Status.PENDING)
        OrderItem.objects.create(
            order=pending,
            menu=self.menu,
            menu_year=self.menu.year,
            menu_month=self.menu.month,
            slot=2,
            name="قورمه‌سبزی",
            cost=90000,
        )
        self.client.login(username="6666666666", password="secret123")
        response = self.client.post(reverse("orders:checkout"))
        self.assertRedirects(response, reverse("orders:cart"))
        self.assertFalse(OrderItem.objects.filter(order=pending).exists())
        request_payment.assert_not_called()

    @patch("Menu.views.zarinpal.verify_payment")
    def test_invalid_callback_authority_is_rejected(self, verify_payment):
        self.client.login(username="0012345678", password="secret123")
        response = self.client.get(
            reverse("orders:callback"),
            {"Authority": "@@@bad@@@", "Status": "OK"},
        )
        self.assertRedirects(response, reverse("orders:cart"))
        verify_payment.assert_not_called()

    @patch("Menu.views.zarinpal.verify_payment")
    def test_callback_accepts_authority_from_earlier_attempt(self, verify_payment):
        verify_payment.return_value = {"data": {"code": 100, "ref_id": "RX"}}
        order = Order.objects.create(
            user=self.student,
            status=Order.Status.PENDING,
            amount=90000,
            authority="ANEW1",
        )
        PaymentAttempt.objects.create(
            order=order,
            amount=90000,
            authority="AOLD1",
            status=PaymentAttempt.Status.REQUESTED,
        )
        self.client.login(username="0012345678", password="secret123")
        response = self.client.get(
            reverse("orders:callback"),
            {"Authority": "AOLD1", "Status": "OK"},
        )
        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PAID)
        self.assertEqual(order.ref_id, "RX")

    def test_deactivated_user_session_is_closed(self):
        self.client.login(username="0012345678", password="secret123")
        self.student.is_active = False
        self.student.save(update_fields=["is_active"])
        self.client.get("/")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_login_is_locked_after_repeated_failures(self):
        from django.conf import settings

        for _ in range(settings.LOGIN_MAX_ATTEMPTS):
            self.client.post(
                reverse("login"),
                {"username": "0012345678", "password": "wrongpass1"},
            )
        response = self.client.post(
            reverse("login"),
            {"username": "0012345678", "password": "secret123"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

