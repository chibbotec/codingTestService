from django.contrib.auth.models import AbstractBaseUser
from django.contrib.auth.models import AbstractUser
from django.conf import settings
from django.db import models
from utils.models import JSONField


class AdminType(object):
    REGULAR_USER = "Member"
    ADMIN = "Admin"
    SUPER_ADMIN = "OWNER"


class ProblemPermission(object):
    NONE = "None"
    OWN = "Own"
    ALL = "All"


# class UserManager(models.Manager):
#     use_in_migrations = True

#     def get_by_natural_key(self, username):
#         return self.get(**{f"{self.model.USERNAME_FIELD}__iexact": username})


# class User(AbstractBaseUser):
#     username = models.TextField(unique=True)
#     email = models.TextField(null=True)
#     create_time = models.DateTimeField(auto_now_add=True, null=True)
#     # One of UserType
#     admin_type = models.TextField(default=AdminType.REGULAR_USER)
#     problem_permission = models.TextField(default=ProblemPermission.NONE)
#     reset_password_token = models.TextField(null=True)
#     reset_password_token_expire_time = models.DateTimeField(null=True)
#     # SSO auth token
#     auth_token = models.TextField(null=True)
#     two_factor_auth = models.BooleanField(default=False)
#     tfa_token = models.TextField(null=True)
#     session_keys = JSONField(default=list)
#     # open api key
#     open_api = models.BooleanField(default=False)
#     open_api_appkey = models.TextField(null=True)
#     is_disabled = models.BooleanField(default=False)

#     USERNAME_FIELD = "username"
#     REQUIRED_FIELDS = []

#     objects = UserManager()

#     def is_admin(self):
#         return self.admin_type == AdminType.ADMIN

#     def is_super_admin(self):
#         return self.admin_type == AdminType.SUPER_ADMIN

#     def is_admin_role(self):
#         return self.admin_type in [AdminType.ADMIN, AdminType.SUPER_ADMIN]

#     def can_mgmt_all_problem(self):
#         return self.problem_permission == ProblemPermission.ALL

#     def is_contest_admin(self, contest):
#         return self.is_authenticated and (contest.created_by == self or self.admin_type == AdminType.SUPER_ADMIN)

#     class Meta:
#         db_table = "user"

# class UserManager(models.Manager):
#     use_in_migrations = True

#     def get_by_natural_key(self, username):
#         return self.get(**{f"{self.model.USERNAME_FIELD}__iexact": username})
    
#     def create_user(self, nickname, password=None, **extra_fields):
#         user = self.model(nickname=nickname, **extra_fields)
#         if password:
#             user.set_password(password)
#         user.save(using=self._db)
#         return user
    
#     def create_superuser(self, nickname, password=None, **extra_fields):
#         extra_fields.setdefault('role', 'ADMIN')
#         return self.create_user(nickname, password, **extra_fields)

class UserManager(models.Manager):
    use_in_migrations = True

    def get_queryset(self):
        return super().get_queryset()

    def get_by_natural_key(self, username):
        return self.get(**{f"{self.model.USERNAME_FIELD}__iexact": username})
    
    def create_user(self, nickname, password=None, **extra_fields):
        user = self.model(nickname=nickname, **extra_fields)
        if password:
            user.set_password(password)
        user.save(using=self._db)
        return user
    
    def create_superuser(self, nickname, password=None, **extra_fields):
        extra_fields.setdefault('role', 'ADMIN')
        return self.create_user(nickname, password, **extra_fields)


class User(models.Model):
    id = models.AutoField(primary_key=True)
    nickname = models.CharField(max_length=100, unique=True)
    role = models.CharField(max_length=20, default="MEMBER")
    space_id = models.IntegerField(null=True, blank=True)
    
    # Django 인증이 필요로 하는 속성들
    USERNAME_FIELD = 'nickname'
    REQUIRED_FIELDS = []
    
    # 메서드 대신 속성으로 정의
    is_active = True
    is_authenticated = True  # 항상 인증된 것으로 간주
    is_anonymous = False
    
    objects = UserManager()
    
    # 기존 코드에서 사용하는 메서드들
    def is_admin(self):
        return self.role == "ADMIN"
        
    def is_super_admin(self):
        return self.role == "OWNER"
        
    def is_admin_role(self):
        return self.role in ["ADMIN", "OWNER"]
        
    def can_mgmt_all_problem(self):
        return self.role == "ADMIN"
        
    def is_contest_admin(self, contest):
        return True and (contest.created_by == self or self.is_super_admin())

    class Meta:
        db_table = "user"

class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    # acm_problems_status examples:
    # {
    #     "problems": {
    #         "1": {
    #             "status": JudgeStatus.ACCEPTED,
    #             "_id": "1000"
    #         }
    #     },
    #     "contest_problems": {
    #         "1": {
    #             "status": JudgeStatus.ACCEPTED,
    #             "_id": "1000"
    #         }
    #     }
    # }
    acm_problems_status = JSONField(default=dict)
    # like acm_problems_status, merely add "score" field
    oi_problems_status = JSONField(default=dict)

    real_name = models.TextField(null=True)
    avatar = models.TextField(default=f"{settings.AVATAR_URI_PREFIX}/default.png")
    blog = models.URLField(null=True)
    mood = models.TextField(null=True)
    github = models.TextField(null=True)
    school = models.TextField(null=True)
    major = models.TextField(null=True)
    language = models.TextField(null=True)
    # for ACM
    accepted_number = models.IntegerField(default=0)
    # for OI
    total_score = models.BigIntegerField(default=0)
    submission_number = models.IntegerField(default=0)

    def add_accepted_problem_number(self):
        self.accepted_number = models.F("accepted_number") + 1
        self.save()

    def add_submission_number(self):
        self.submission_number = models.F("submission_number") + 1
        self.save()

    # 计算总分时， 应先减掉上次该题所得分数， 然后再加上本次所得分数
    def add_score(self, this_time_score, last_time_score=None):
        last_time_score = last_time_score or 0
        self.total_score = models.F("total_score") - last_time_score + this_time_score
        self.save()

    class Meta:
        db_table = "user_profile"
