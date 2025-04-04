from django.core.management.base import BaseCommand

from account.models import AdminType, ProblemPermission, User, UserProfile
from utils.shortcuts import rand_str  # NOQA


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument("--username", type=str)
        parser.add_argument("--password", type=str)
        parser.add_argument("--action", type=str)

    def handle(self, *args, **options):
        nickname = options["nickname"]
        role = options["role"]

        if not(nickname and role):
            self.stdout.write(self.style.ERROR("Invalid args"))
            exit(1)

        if role == "create_super_admin":
            if User.objects.filter(id=1).exists():
                self.stdout.write(self.style.SUCCESS(f"User {nickname} exists, operation ignored"))
                exit()

            user = User.objects.create(nickname=nickname, role=AdminType.SUPER_ADMIN)
            user.save()
            UserProfile.objects.create(user=user)

            self.stdout.write(self.style.SUCCESS("User created"))
        # elif action == "reset":
        #     try:
        #         user = User.objects.get(username=username)
        #         user.set_password(password)
        #         user.save()
        #         self.stdout.write(self.style.SUCCESS("Password is rested"))
        #     except User.DoesNotExist:
        #         self.stdout.write(self.style.ERROR(f"User {username} doesnot exist, operation ignored"))
        #         exit(1)
        else:
            raise ValueError("Invalid action")
