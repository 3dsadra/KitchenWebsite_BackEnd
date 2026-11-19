from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Create or update the admin account used to access the staff panel."

    def add_arguments(self, parser):
        parser.add_argument("--code", default="0000000000", help="10-digit username")
        parser.add_argument("--password", default="admin12345")
        parser.add_argument("--first-name", default="مدیر")
        parser.add_argument("--last-name", default="آشپزخانه")
        parser.add_argument(
            "--reset-password",
            action="store_true",
            help="Reset the password even if the account already exists.",
        )

    def handle(self, *args, **options):
        code = str(options["code"]).strip()
        password = options["password"]

        if not code.isdigit() or len(code) != 10:
            raise CommandError("Code must be a 10-digit number.")

        if len(password) < 8:
            raise CommandError("Password must be at least 8 characters.")

        if password == "admin12345":
            self.stderr.write(
                self.style.WARNING(
                    "هشدار: رمز پیش‌فرض است؛ برای محیط واقعی رمز قوی‌تری بگذارید."
                )
            )

        User = get_user_model()
        user, created = User.objects.get_or_create(
            username=code,
            defaults={
                "first_name": options["first_name"],
                "last_name": options["last_name"],
            },
        )

        if created or options["reset_password"]:
            user.set_password(password)

        user.is_staff = True
        user.is_superuser = True
        user.is_active = True
        user.first_name = options["first_name"]
        user.last_name = options["last_name"]
        user.save()

        action = "created" if created else "updated"
        self.stdout.write(
            self.style.SUCCESS(f"Admin {action}: code={code}")
        )
