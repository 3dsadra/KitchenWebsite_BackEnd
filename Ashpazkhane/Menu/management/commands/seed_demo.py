from django.core.management.base import BaseCommand

from Ashpazkhane import clock
from Ashpazkhane.jalali import (
    is_off_day,
    jalali_month_length,
    to_jalali,
)

from Menu.models import MenuEntry, MonthlyMenu


DISHES = [
    "قورمه‌سبزی",
    "قیمه بادمجان",
    "چلوکباب کوبیده",
    "زرشک‌پلو با مرغ",
    "باقالی‌پلو با ماهیچه",
    "عدس‌پلو",
    "سبزی‌پلو با ماهی",
    "کشک بادمجان",
    "آبگوشت",
    "ماکارونی",
    "لوبیاپلو",
    "مرصع‌پلو",
    "چلو خورش کرفس",
    "خورش فسنجان",
    "خورش قیمه",
    "کباب تابه‌ای",
    "دمی باقالی",
    "شامی کباب",
    "سوپ جو و نان",
    "پلو مخلوط",
    "خورش بادمجان",
    "لازانیا",
    "تاس کباب",
    "آش رشته",
    "چلو ماهیچه",
    "کباب مرغ",
    "خورش آلو اسفناج",
    "هویج‌پلو",
    "شیرین‌پلو",
    "خوراک لوبیا و عدس",
]

COSTS = [78000, 85000, 92000, 105000, 115000, 68000, 98000]


def shift_jalali_month(jy: int, jm: int, delta: int) -> tuple[int, int]:
    index = jy * 12 + (jm - 1) + delta
    year, month = divmod(index, 12)
    return year, month + 1


class Command(BaseCommand):
    help = "منوی نمایشی برای ماه شمسی قبل، ماه جاری و ماه بعد می‌سازد."

    def add_arguments(self, parser):
        parser.add_argument(
            "--demo-user",
            action="store_true",
            help="یک کاربر نمونه با کد ملی 0012345678 و رمز ashpazkhane بسازد.",
        )

    def handle(self, *args, **options):
        today = clock.today()
        jy, jm, _ = to_jalali(today)
        written = 0

        for delta in (-1, 0, 1):
            year, month = shift_jalali_month(jy, jm, delta)
            menu, _ = MonthlyMenu.objects.get_or_create(year=year, month=month)
            days_in_month = jalali_month_length(year, month)

            off_days = []
            for day in range(1, days_in_month + 1):
                if is_off_day(year, month, day):  # پنجشنبه و جمعه تعطیل
                    off_days.append(day)
                    continue

                dish = DISHES[(day + month) % len(DISHES)]
                cost = COSTS[(day * 3 + month) % len(COSTS)]

                MenuEntry.objects.update_or_create(
                    menu=menu,
                    slot=day,
                    defaults={"name": dish, "cost": cost},
                )
                written += 1

            # منوهای قدیمی ممکن است برای روزهای تعطیل غذا داشته باشند.
            MenuEntry.objects.filter(menu=menu, slot__in=off_days).delete()

            self.stdout.write(
                self.style.SUCCESS(
                    f"Menu {year}-{month:02d} ready ({days_in_month} days)."
                )
            )

        if options["demo_user"]:
            from django.contrib.auth.models import User

            user, created = User.objects.get_or_create(
                username="0012345678",
                defaults={"first_name": "کاربر", "last_name": "نمونه"},
            )
            if created:
                user.set_password("ashpazkhane")
                user.save()
                self.stderr.write(
                    self.style.WARNING(
                        "کاربر نمایشی ساخته شد (کد 0012345678 / رمز ashpazkhane). "
                        "این کاربر و رمز آن را در محیط واقعی حذف کنید."
                    )
                )

        self.stdout.write(self.style.SUCCESS(f"{written} menu entries written."))
