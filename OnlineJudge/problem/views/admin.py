import hashlib
import json
import os
import sys
# import shutil
import tempfile
import zipfile
from wsgiref.util import FileWrapper

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.http import StreamingHttpResponse, FileResponse

from account.decorators import problem_permission_required, ensure_created_by
from contest.models import Contest, ContestStatus
from fps.parser import FPSHelper, FPSParser
from judge.dispatcher import SPJCompiler
from options.options import SysOptions
from submission.models import Submission, JudgeStatus
from utils.api import APIView, CSRFExemptAPIView, validate_serializer, APIError
from utils.constants import Difficulty
from utils.shortcuts import rand_str, natural_sort_key
from utils.tasks import delete_files
from ..models import Problem, ProblemRuleType, ProblemTag
from ..serializers import (CreateContestProblemSerializer, CompileSPJSerializer,
                           CreateProblemSerializer, EditProblemSerializer, EditContestProblemSerializer,
                           ProblemAdminSerializer, TestCaseUploadForm, ContestProblemMakePublicSerializer,
                           AddContestProblemSerializer, ExportProblemSerializer,
                           ExportProblemRequestSerialzier, UploadProblemForm, ImportProblemSerializer,
                           FPSProblemSerializer)
from ..utils import TEMPLATE_BASE, build_problem_template
from utils.spaceMemberClient import SpaceMemberClient
from account.models import User
import json
import logging

class TestCaseZipProcessor(object):
    def process_zip(self, uploaded_zip_file, spj, dir=""):
        try:
            zip_file = zipfile.ZipFile(uploaded_zip_file, "r")
        except zipfile.BadZipFile:
            raise APIError("Bad zip file")
        name_list = zip_file.namelist()
        test_case_list = self.filter_name_list(name_list, spj=spj, dir=dir)
        if not test_case_list:
            raise APIError("Empty file")

        test_case_id = rand_str()
        test_case_dir = os.path.join(settings.TEST_CASE_DIR, test_case_id)
        os.mkdir(test_case_dir)
        os.chmod(test_case_dir, 0o710)

        size_cache = {}
        md5_cache = {}

        for item in test_case_list:
            with open(os.path.join(test_case_dir, item), "wb") as f:
                content = zip_file.read(f"{dir}{item}").replace(b"\r\n", b"\n")
                size_cache[item] = len(content)
                if item.endswith(".out"):
                    md5_cache[item] = hashlib.md5(content.rstrip()).hexdigest()
                f.write(content)
        test_case_info = {"spj": spj, "test_cases": {}}

        info = []

        if spj:
            for index, item in enumerate(test_case_list):
                data = {"input_name": item, "input_size": size_cache[item]}
                info.append(data)
                test_case_info["test_cases"][str(index + 1)] = data
        else:
            # ["1.in", "1.out", "2.in", "2.out"] => [("1.in", "1.out"), ("2.in", "2.out")]
            test_case_list = zip(*[test_case_list[i::2] for i in range(2)])
            for index, item in enumerate(test_case_list):
                data = {"stripped_output_md5": md5_cache[item[1]],
                        "input_size": size_cache[item[0]],
                        "output_size": size_cache[item[1]],
                        "input_name": item[0],
                        "output_name": item[1]}
                info.append(data)
                test_case_info["test_cases"][str(index + 1)] = data

        with open(os.path.join(test_case_dir, "info"), "w", encoding="utf-8") as f:
            f.write(json.dumps(test_case_info, indent=4))

        for item in os.listdir(test_case_dir):
            os.chmod(os.path.join(test_case_dir, item), 0o640)

        return info, test_case_id

    def filter_name_list(self, name_list, spj, dir=""):
        ret = []
        prefix = 1
        if spj:
            while True:
                in_name = f"{prefix}.in"
                if f"{dir}{in_name}" in name_list:
                    ret.append(in_name)
                    prefix += 1
                    continue
                else:
                    return sorted(ret, key=natural_sort_key)
        else:
            while True:
                in_name = f"{prefix}.in"
                out_name = f"{prefix}.out"
                if f"{dir}{in_name}" in name_list and f"{dir}{out_name}" in name_list:
                    ret.append(in_name)
                    ret.append(out_name)
                    prefix += 1
                    continue
                else:
                    return sorted(ret, key=natural_sort_key)


class TestCaseAPI(CSRFExemptAPIView, TestCaseZipProcessor):
    request_parsers = ()

    def get(self, request):
        problem_id = request.GET.get("problem_id")
        if not problem_id:
            return self.error("Parameter error, problem_id is required")
        try:
            problem = Problem.objects.get(id=problem_id)
        except Problem.DoesNotExist:
            return self.error("Problem does not exists")

        if problem.contest:
            ensure_created_by(problem.contest, request.user)
        else:
            ensure_created_by(problem, request.user)

        test_case_dir = os.path.join(settings.TEST_CASE_DIR, problem.test_case_id)
        if not os.path.isdir(test_case_dir):
            return self.error("Test case does not exists")
        name_list = self.filter_name_list(os.listdir(test_case_dir), problem.spj)
        name_list.append("info")
        file_name = os.path.join(test_case_dir, problem.test_case_id + ".zip")
        with zipfile.ZipFile(file_name, "w") as file:
            for test_case in name_list:
                file.write(f"{test_case_dir}/{test_case}", test_case)
        response = StreamingHttpResponse(FileWrapper(open(file_name, "rb")),
                                         content_type="application/octet-stream")

        response["Content-Disposition"] = f"attachment; filename=problem_{problem.id}_test_cases.zip"
        response["Content-Length"] = os.path.getsize(file_name)
        return response

    def post(self, request, space_id=None):
        print(f"TestCaseAPI.post 요청 받음: space_id={space_id}, user={request.user}")
        logger = logging.getLogger(__name__)
        logger.info(f"TestCaseAPI.post 요청 받음: space_id={space_id}, user={request.user}")
        form = TestCaseUploadForm(request.POST, request.FILES)
        if form.is_valid():
            spj = form.cleaned_data["spj"] == "true"
            file = form.cleaned_data["file"]
        else:
            return self.error("Upload failed")
        zip_file = f"/tmp/{rand_str()}.zip"
        with open(zip_file, "wb") as f:
            for chunk in file:
                f.write(chunk)
        info, test_case_id = self.process_zip(zip_file, spj=spj)
        os.remove(zip_file)
        return self.success({"id": test_case_id, "info": info, "spj": spj})


class CompileSPJAPI(APIView):
    @validate_serializer(CompileSPJSerializer)
    def post(self, request):
        data = request.data
        spj_version = rand_str(8)
        error = SPJCompiler(data["spj_code"], spj_version, data["spj_language"]).compile_spj()
        if error:
            return self.error(error)
        else:
            return self.success()


class ProblemBase(APIView):
    def common_checks(self, request):
        data = request.data
        if data["spj"]:
            if not data["spj_language"] or not data["spj_code"]:
                return "Invalid spj"
            if not data["spj_compile_ok"]:
                return "SPJ code must be compiled successfully"
            data["spj_version"] = hashlib.md5(
                (data["spj_language"] + ":" + data["spj_code"]).encode("utf-8")).hexdigest()
        else:
            data["spj_language"] = None
            data["spj_code"] = None
        if data["rule_type"] == ProblemRuleType.OI:
            total_score = 0
            for item in data["test_case_score"]:
                if item["score"] <= 0:
                    return "Invalid score"
                else:
                    total_score += item["score"]
            data["total_score"] = total_score
        data["languages"] = list(data["languages"])

# class ProblemAPI(ProblemBase):
#     # @problem_permission_required
#     @validate_serializer(CreateProblemSerializer)
#     def post(self, request):
#         data = request.data
#         _id = data["_id"]
#         if not _id:
#             return self.error("Display ID is required")
#         if Problem.objects.filter(_id=_id, contest_id__isnull=True).exists():
#             return self.error("Display ID already exists")

#         error_info = self.common_checks(request)
#         if error_info:
#             return self.error(error_info)

#         # todo check filename and score info
#         tags = data.pop("tags")
#         data["created_by"] = request.user
#         problem = Problem.objects.create(**data)

#         for item in tags:
#             try:
#                 tag = ProblemTag.objects.get(name=item)
#             except ProblemTag.DoesNotExist:
#                 tag = ProblemTag.objects.create(name=item)
#             problem.tags.add(tag)
#         return self.success(ProblemAdminSerializer(problem).data)

#     @problem_permission_required
#     def get(self, request):
#         problem_id = request.GET.get("id")
#         rule_type = request.GET.get("rule_type")
#         user = request.user
#         if problem_id:
#             try:
#                 problem = Problem.objects.get(id=problem_id)
#                 ensure_created_by(problem, request.user)
#                 return self.success(ProblemAdminSerializer(problem).data)
#             except Problem.DoesNotExist:
#                 return self.error("Problem does not exist")

#         problems = Problem.objects.filter(contest_id__isnull=True).order_by("-create_time")
#         if rule_type:
#             if rule_type not in ProblemRuleType.choices():
#                 return self.error("Invalid rule_type")
#             else:
#                 problems = problems.filter(rule_type=rule_type)

#         keyword = request.GET.get("keyword", "").strip()
#         if keyword:
#             problems = problems.filter(Q(title__icontains=keyword) | Q(_id__icontains=keyword))
#         if not user.can_mgmt_all_problem():
#             problems = problems.filter(created_by=user)
#         return self.success(self.paginate_data(request, problems, ProblemAdminSerializer))

#     @problem_permission_required
#     @validate_serializer(EditProblemSerializer)
#     def put(self, request):
#         data = request.data
#         problem_id = data.pop("id")

#         try:
#             problem = Problem.objects.get(id=problem_id)
#             ensure_created_by(problem, request.user)
#         except Problem.DoesNotExist:
#             return self.error("Problem does not exist")

#         _id = data["_id"]
#         if not _id:
#             return self.error("Display ID is required")
#         if Problem.objects.exclude(id=problem_id).filter(_id=_id, contest_id__isnull=True).exists():
#             return self.error("Display ID already exists")

#         error_info = self.common_checks(request)
#         if error_info:
#             return self.error(error_info)
#         # todo check filename and score info
#         tags = data.pop("tags")
#         data["languages"] = list(data["languages"])

#         for k, v in data.items():
#             setattr(problem, k, v)
#         problem.save()

#         problem.tags.remove(*problem.tags.all())
#         for tag in tags:
#             try:
#                 tag = ProblemTag.objects.get(name=tag)
#             except ProblemTag.DoesNotExist:
#                 tag = ProblemTag.objects.create(name=tag)
#             problem.tags.add(tag)

#         return self.success()

#     @problem_permission_required
#     def delete(self, request):
#         id = request.GET.get("id")
#         if not id:
#             return self.error("Invalid parameter, id is required")
#         try:
#             problem = Problem.objects.get(id=id, contest_id__isnull=True)
#         except Problem.DoesNotExist:
#             return self.error("Problem does not exists")
#         ensure_created_by(problem, request.user)
#         # d = os.path.join(settings.TEST_CASE_DIR, problem.test_case_id)
#         # if os.path.isdir(d):
#         #     shutil.rmtree(d, ignore_errors=True)
#         problem.delete()
#         return self.success()

class ProblemAPI(CSRFExemptAPIView, ProblemBase):
    @validate_serializer(CreateProblemSerializer)
    def post(self, request, space_id=None):
        logger = logging.getLogger(__name__)
        print("======================요청들어옴")

        data = request.data
        _id = data["_id"]
        if not _id:
            return self.error("Display ID is required")
            
        # space_id가 있으면 해당 space 내에서만 고유한 ID인지 확인
        if space_id:
            if Problem.objects.filter(_id=_id, space_id=space_id).exists():
                return self.error("Display ID already exists in this space")
        else:
            # 기존 로직 - 공간이 없는 일반 문제
            if Problem.objects.filter(_id=_id, contest_id__isnull=True, space_id__isnull=True).exists():
                return self.error("Display ID already exists")

        error_info = self.common_checks(request)
        if error_info:
            return self.error(error_info)
        
        # API Gateway로부터 온 사용자 정보 확인
        user_id = request.headers.get("X-User-ID")
        print(f"Request contains user info - ID: {user_id}")
        username = request.headers.get("X-Username")

        # todo check filename and score info
        tags = data.pop("tags")
        
         # 사용자 처리 로직
        if user_id and username:
            logger.info(f"Request contains user info - ID: {user_id}, Username: {username}")
            
            # 이미 등록된 사용자인지 확인
            user = User.objects.filter(id=user_id).first()
            
            if not user:
                # 사용자가 등록되어 있지 않고 space_id가 있는 경우 스페이스 멤버 정보 조회
                if space_id:
                    logger.info(f"User not found in database, fetching from space service")
                    space_member_client = SpaceMemberClient()
                    member_info = space_member_client.get_space_member(space_id, user_id)
                    print(f"Space member info: {member_info}")
                    
                    if member_info:
                        # 사용자 생성 (간단한 구현, 실제로는 더 많은 필드 필요할 수 있음)
                        user = User.objects.create(
                            id=member_info.get("id"),
                            nickname=member_info.get("nickname"),
                            role=member_info.get("role"),
                            space_id=space_id
                        )

                    else:
                        # 스페이스 멤버 정보도 없는 경우 현재 요청 사용자 사용
                        logger.warning(f"Space member info not found, using request user")
                        user = request.user
                else:
                    # space_id가 없는 경우 현재 요청 사용자 사용
                    user = request.user
            
            data["created_by"] = user
        else:
            # API Gateway를 통해 오지 않은 요청인 경우 기본 로직 사용
            data["created_by"] = request.user
        
        # space_id가 있으면 문제에 추가
        if space_id:
            data["space_id"] = space_id
            
        # 문제 생성
        problem = Problem.objects.create(**data)

        # 태그 처리
        for item in tags:
            try:
                tag = ProblemTag.objects.get(name=item)
            except ProblemTag.DoesNotExist:
                tag = ProblemTag.objects.create(name=item)
            problem.tags.add(tag)
            
        return self.success(ProblemAdminSerializer(problem).data)

    # @problem_permission_required - 데코레이터 제거
    def get(self, request, space_id=None, problem_id=None):
        # 특정 공간의 특정 문제를 조회하는 경우
        print("======================get요청들어옴")
        if space_id and problem_id:
            try:
                problem = Problem.objects.get(id=problem_id, space_id=space_id)
                return self.success(ProblemAdminSerializer(problem).data)
            except Problem.DoesNotExist:
                return self.error("Problem does not exist")
        
        # 특정 공간의 모든 문제를 조회하는 경우
        if space_id:
            problems = Problem.objects.filter(space_id=space_id).order_by("-create_time")
            keyword = request.GET.get("keyword", "").strip()
            if keyword:
                problems = problems.filter(Q(title__icontains=keyword) | Q(_id__icontains=keyword))
            return self.success(self.paginate_data(request, problems, ProblemAdminSerializer))
            
        # 기존 로직 - 일반 문제 조회
        problem_id = request.GET.get("id")
        rule_type = request.GET.get("rule_type")
        user = request.user
        
        if problem_id:
            try:
                problem = Problem.objects.get(id=problem_id)
                # API 게이트웨이에서 처리하므로 제거
                # ensure_created_by(problem, request.user)
                return self.success(ProblemAdminSerializer(problem).data)
            except Problem.DoesNotExist:
                return self.error("Problem does not exist")

        problems = Problem.objects.filter(contest_id__isnull=True, space_id__isnull=True).order_by("-create_time")
        if rule_type:
            if rule_type not in ProblemRuleType.choices():
                return self.error("Invalid rule_type")
            else:
                problems = problems.filter(rule_type=rule_type)

        keyword = request.GET.get("keyword", "").strip()
        if keyword:
            problems = problems.filter(Q(title__icontains=keyword) | Q(_id__icontains=keyword))
        if not user.can_mgmt_all_problem():
            problems = problems.filter(created_by=user)
        return self.success(self.paginate_data(request, problems, ProblemAdminSerializer))

    # @problem_permission_required - 데코레이터 제거
    @validate_serializer(EditProblemSerializer)
    def put(self, request, space_id=None, problem_id=None):
        data = request.data
        
        # 경로에서 problem_id가 제공된 경우
        if problem_id:
            problem_id_to_update = problem_id
        else:
            # 기존 방식으로 요청 바디에서 id 가져오기 
            problem_id_to_update = data.get("id")
            
        if not problem_id_to_update:
            return self.error("Problem ID is required")

        try:
            # space_id가 있으면 해당 공간의 문제인지 확인
            if space_id:
                problem = Problem.objects.get(id=problem_id_to_update, space_id=space_id)
            else:
                problem = Problem.objects.get(id=problem_id_to_update)
                # API 게이트웨이에서 처리하므로 제거
                # ensure_created_by(problem, request.user)
        except Problem.DoesNotExist:
            return self.error("Problem does not exist")

        _id = data.get("_id")
        if not _id:
            return self.error("Display ID is required")
            
        # ID 중복 검사
        if space_id:
            # 공간 내에서 ID 중복 검사
            if Problem.objects.exclude(id=problem_id_to_update).filter(_id=_id, space_id=space_id).exists():
                return self.error("Display ID already exists in this space")
        else:
            # 기존 로직
            if Problem.objects.exclude(id=problem_id_to_update).filter(_id=_id, contest_id__isnull=True, space_id__isnull=True).exists():
                return self.error("Display ID already exists")

        error_info = self.common_checks(request)
        if error_info:
            return self.error(error_info)
            
        # todo check filename and score info
        tags = data.pop("tags", [])
        
        # data의 각 필드를 문제 객체에 설정
        for k, v in data.items():
            if k != "id":  # id는 변경하지 않음
                setattr(problem, k, v)
        problem.save()

        # 태그 업데이트
        problem.tags.clear()
        for tag_name in tags:
            try:
                tag = ProblemTag.objects.get(name=tag_name)
            except ProblemTag.DoesNotExist:
                tag = ProblemTag.objects.create(name=tag_name)
            problem.tags.add(tag)

        return self.success()

    # @problem_permission_required - 데코레이터 제거
    def delete(self, request, space_id=None, problem_id=None):
        # 경로에서 problem_id가 제공된 경우
        if problem_id:
            id_to_delete = problem_id
        else:
            # 기존 방식으로 쿼리 파라미터에서 id 가져오기
            id_to_delete = request.GET.get("id")
            
        if not id_to_delete:
            return self.error("Invalid parameter, id is required")
            
        try:
            # space_id가 있으면 해당 공간의 문제인지 확인
            if space_id:
                problem = Problem.objects.get(id=id_to_delete, space_id=space_id)
            else:
                problem = Problem.objects.get(id=id_to_delete, contest_id__isnull=True, space_id__isnull=True)
                # API 게이트웨이에서 처리하므로 제거
                # ensure_created_by(problem, request.user)
        except Problem.DoesNotExist:
            return self.error("Problem does not exist")
            
        # 문제 삭제
        problem.delete()
        return self.success()

class ContestProblemAPI(ProblemBase):
    @validate_serializer(CreateContestProblemSerializer)
    def post(self, request):
        data = request.data
        try:
            contest = Contest.objects.get(id=data.pop("contest_id"))
            ensure_created_by(contest, request.user)
        except Contest.DoesNotExist:
            return self.error("Contest does not exist")

        if data["rule_type"] != contest.rule_type:
            return self.error("Invalid rule type")

        _id = data["_id"]
        if not _id:
            return self.error("Display ID is required")

        if Problem.objects.filter(_id=_id, contest=contest).exists():
            return self.error("Duplicate Display id")

        error_info = self.common_checks(request)
        if error_info:
            return self.error(error_info)

        # todo check filename and score info
        data["contest"] = contest
        tags = data.pop("tags")
        data["created_by"] = request.user
        problem = Problem.objects.create(**data)

        for item in tags:
            try:
                tag = ProblemTag.objects.get(name=item)
            except ProblemTag.DoesNotExist:
                tag = ProblemTag.objects.create(name=item)
            problem.tags.add(tag)
        return self.success(ProblemAdminSerializer(problem).data)

    def get(self, request):
        problem_id = request.GET.get("id")
        contest_id = request.GET.get("contest_id")
        user = request.user
        if problem_id:
            try:
                problem = Problem.objects.get(id=problem_id)
                ensure_created_by(problem.contest, user)
            except Problem.DoesNotExist:
                return self.error("Problem does not exist")
            return self.success(ProblemAdminSerializer(problem).data)

        if not contest_id:
            return self.error("Contest id is required")
        try:
            contest = Contest.objects.get(id=contest_id)
            ensure_created_by(contest, user)
        except Contest.DoesNotExist:
            return self.error("Contest does not exist")
        problems = Problem.objects.filter(contest=contest).order_by("-create_time")
        if user.is_admin():
            problems = problems.filter(contest__created_by=user)
        keyword = request.GET.get("keyword")
        if keyword:
            problems = problems.filter(title__contains=keyword)
        return self.success(self.paginate_data(request, problems, ProblemAdminSerializer))

    @validate_serializer(EditContestProblemSerializer)
    def put(self, request):
        data = request.data
        user = request.user

        try:
            contest = Contest.objects.get(id=data.pop("contest_id"))
            ensure_created_by(contest, user)
        except Contest.DoesNotExist:
            return self.error("Contest does not exist")

        if data["rule_type"] != contest.rule_type:
            return self.error("Invalid rule type")

        problem_id = data.pop("id")

        try:
            problem = Problem.objects.get(id=problem_id, contest=contest)
        except Problem.DoesNotExist:
            return self.error("Problem does not exist")

        _id = data["_id"]
        if not _id:
            return self.error("Display ID is required")
        if Problem.objects.exclude(id=problem_id).filter(_id=_id, contest=contest).exists():
            return self.error("Display ID already exists")

        error_info = self.common_checks(request)
        if error_info:
            return self.error(error_info)
        # todo check filename and score info
        tags = data.pop("tags")
        data["languages"] = list(data["languages"])

        for k, v in data.items():
            setattr(problem, k, v)
        problem.save()

        problem.tags.remove(*problem.tags.all())
        for tag in tags:
            try:
                tag = ProblemTag.objects.get(name=tag)
            except ProblemTag.DoesNotExist:
                tag = ProblemTag.objects.create(name=tag)
            problem.tags.add(tag)
        return self.success()

    def delete(self, request):
        id = request.GET.get("id")
        if not id:
            return self.error("Invalid parameter, id is required")
        try:
            problem = Problem.objects.get(id=id, contest_id__isnull=False)
        except Problem.DoesNotExist:
            return self.error("Problem does not exists")
        ensure_created_by(problem.contest, request.user)
        if Submission.objects.filter(problem=problem).exists():
            return self.error("Can't delete the problem as it has submissions")
        # d = os.path.join(settings.TEST_CASE_DIR, problem.test_case_id)
        # if os.path.isdir(d):
        #    shutil.rmtree(d, ignore_errors=True)
        problem.delete()
        return self.success()

class MakeContestProblemPublicAPIView(APIView):
    @validate_serializer(ContestProblemMakePublicSerializer)
    @problem_permission_required
    def post(self, request):
        data = request.data
        display_id = data.get("display_id")
        if Problem.objects.filter(_id=display_id, contest_id__isnull=True).exists():
            return self.error("Duplicate display ID")

        try:
            problem = Problem.objects.get(id=data["id"])
        except Problem.DoesNotExist:
            return self.error("Problem does not exist")

        if not problem.contest or problem.is_public:
            return self.error("Already be a public problem")
        problem.is_public = True
        problem.save()
        # https://docs.djangoproject.com/en/1.11/topics/db/queries/#copying-model-instances
        tags = problem.tags.all()
        problem.pk = None
        problem.contest = None
        problem._id = display_id
        problem.visible = False
        problem.submission_number = problem.accepted_number = 0
        problem.statistic_info = {}
        problem.save()
        problem.tags.set(tags)
        return self.success()

class AddContestProblemAPI(APIView):
    @validate_serializer(AddContestProblemSerializer)
    def post(self, request):
        data = request.data
        try:
            contest = Contest.objects.get(id=data["contest_id"])
            problem = Problem.objects.get(id=data["problem_id"])
        except (Contest.DoesNotExist, Problem.DoesNotExist):
            return self.error("Contest or Problem does not exist")

        if contest.status == ContestStatus.CONTEST_ENDED:
            return self.error("Contest has ended")
        if Problem.objects.filter(contest=contest, _id=data["display_id"]).exists():
            return self.error("Duplicate display id in this contest")

        tags = problem.tags.all()
        problem.pk = None
        problem.contest = contest
        problem.is_public = True
        problem.visible = True
        problem._id = request.data["display_id"]
        problem.submission_number = problem.accepted_number = 0
        problem.statistic_info = {}
        problem.save()
        problem.tags.set(tags)
        return self.success()

class ExportProblemAPI(APIView):
    def choose_answers(self, user, problem):
        ret = []
        for item in problem.languages:
            submission = Submission.objects.filter(problem=problem,
                                                   user_id=user.id,
                                                   language=item,
                                                   result=JudgeStatus.ACCEPTED).order_by("-create_time").first()
            if submission:
                ret.append({"language": submission.language, "code": submission.code})
        return ret

    def process_one_problem(self, zip_file, user, problem, index):
        info = ExportProblemSerializer(problem).data
        info["answers"] = self.choose_answers(user, problem=problem)
        compression = zipfile.ZIP_DEFLATED
        zip_file.writestr(zinfo_or_arcname=f"{index}/problem.json",
                          data=json.dumps(info, indent=4),
                          compress_type=compression)
        problem_test_case_dir = os.path.join(settings.TEST_CASE_DIR, problem.test_case_id)
        with open(os.path.join(problem_test_case_dir, "info")) as f:
            info = json.load(f)
        for k, v in info["test_cases"].items():
            zip_file.write(filename=os.path.join(problem_test_case_dir, v["input_name"]),
                           arcname=f"{index}/testcase/{v['input_name']}",
                           compress_type=compression)
            if not info["spj"]:
                zip_file.write(filename=os.path.join(problem_test_case_dir, v["output_name"]),
                               arcname=f"{index}/testcase/{v['output_name']}",
                               compress_type=compression)

    @validate_serializer(ExportProblemRequestSerialzier)
    def get(self, request):
        problems = Problem.objects.filter(id__in=request.data["problem_id"])
        for problem in problems:
            if problem.contest:
                ensure_created_by(problem.contest, request.user)
            else:
                ensure_created_by(problem, request.user)
        path = f"/tmp/{rand_str()}.zip"
        with zipfile.ZipFile(path, "w") as zip_file:
            for index, problem in enumerate(problems):
                self.process_one_problem(zip_file=zip_file, user=request.user, problem=problem, index=index + 1)
        delete_files.send_with_options(args=(path,), delay=300_000)
        resp = FileResponse(open(path, "rb"))
        resp["Content-Type"] = "application/zip"
        resp["Content-Disposition"] = "attachment;filename=problem-export.zip"
        return resp

class ImportProblemAPI(CSRFExemptAPIView, TestCaseZipProcessor):
    request_parsers = ()

    def post(self, request):
        form = UploadProblemForm(request.POST, request.FILES)
        if form.is_valid():
            file = form.cleaned_data["file"]
            tmp_file = f"/tmp/{rand_str()}.zip"
            with open(tmp_file, "wb") as f:
                for chunk in file:
                    f.write(chunk)
        else:
            return self.error("Upload failed")

        count = 0
        with zipfile.ZipFile(tmp_file, "r") as zip_file:
            name_list = zip_file.namelist()
            for item in name_list:
                if "/problem.json" in item:
                    count += 1
            with transaction.atomic():
                for i in range(1, count + 1):
                    with zip_file.open(f"{i}/problem.json") as f:
                        problem_info = json.load(f)
                        serializer = ImportProblemSerializer(data=problem_info)
                        if not serializer.is_valid():
                            return self.error(f"Invalid problem format, error is {serializer.errors}")
                        else:
                            problem_info = serializer.data
                            for item in problem_info["template"].keys():
                                if item not in SysOptions.language_names:
                                    return self.error(f"Unsupported language {item}")

                        problem_info["display_id"] = problem_info["display_id"][:24]
                        for k, v in problem_info["template"].items():
                            problem_info["template"][k] = build_problem_template(v["prepend"], v["template"],
                                                                                 v["append"])

                        spj = problem_info["spj"] is not None
                        rule_type = problem_info["rule_type"]
                        test_case_score = problem_info["test_case_score"]

                        # process test case
                        _, test_case_id = self.process_zip(tmp_file, spj=spj, dir=f"{i}/testcase/")

                        problem_obj = Problem.objects.create(_id=problem_info["display_id"],
                                                             title=problem_info["title"],
                                                             description=problem_info["description"]["value"],
                                                             input_description=problem_info["input_description"][
                                                                 "value"],
                                                             output_description=problem_info["output_description"][
                                                                 "value"],
                                                             hint=problem_info["hint"]["value"],
                                                             test_case_score=test_case_score if test_case_score else [],
                                                             time_limit=problem_info["time_limit"],
                                                             memory_limit=problem_info["memory_limit"],
                                                             samples=problem_info["samples"],
                                                             template=problem_info["template"],
                                                             rule_type=problem_info["rule_type"],
                                                             source=problem_info["source"],
                                                             spj=spj,
                                                             spj_code=problem_info["spj"]["code"] if spj else None,
                                                             spj_language=problem_info["spj"][
                                                                 "language"] if spj else None,
                                                             spj_version=rand_str(8) if spj else "",
                                                             languages=SysOptions.language_names,
                                                             created_by=request.user,
                                                             visible=False,
                                                             difficulty=Difficulty.MID,
                                                             total_score=sum(item["score"] for item in test_case_score)
                                                             if rule_type == ProblemRuleType.OI else 0,
                                                             test_case_id=test_case_id
                                                             )
                        for tag_name in problem_info["tags"]:
                            tag_obj, _ = ProblemTag.objects.get_or_create(name=tag_name)
                            problem_obj.tags.add(tag_obj)
        return self.success({"import_count": count})

class FPSProblemImport(CSRFExemptAPIView):
    request_parsers = ()

    def _create_problem(self, problem_data, creator):
        if problem_data["time_limit"]["unit"] == "ms":
            time_limit = problem_data["time_limit"]["value"]
        else:
            time_limit = problem_data["time_limit"]["value"] * 1000
        template = {}
        prepend = {}
        append = {}
        for t in problem_data["prepend"]:
            prepend[t["language"]] = t["code"]
        for t in problem_data["append"]:
            append[t["language"]] = t["code"]
        for t in problem_data["template"]:
            our_lang = lang = t["language"]
            if lang == "Python":
                our_lang = "Python3"
            template[our_lang] = TEMPLATE_BASE.format(prepend.get(lang, ""), t["code"], append.get(lang, ""))
        spj = problem_data["spj"] is not None
        Problem.objects.create(_id=f"fps-{rand_str(4)}",
                               title=problem_data["title"],
                               description=problem_data["description"],
                               input_description=problem_data["input"],
                               output_description=problem_data["output"],
                               hint=problem_data["hint"],
                               test_case_score=problem_data["test_case_score"],
                               time_limit=time_limit,
                               memory_limit=problem_data["memory_limit"]["value"],
                               samples=problem_data["samples"],
                               template=template,
                               rule_type=ProblemRuleType.ACM,
                               source=problem_data.get("source", ""),
                               spj=spj,
                               spj_code=problem_data["spj"]["code"] if spj else None,
                               spj_language=problem_data["spj"]["language"] if spj else None,
                               spj_version=rand_str(8) if spj else "",
                               visible=False,
                               languages=SysOptions.language_names,
                               created_by=creator,
                               difficulty=Difficulty.MID,
                               test_case_id=problem_data["test_case_id"])

    def post(self, request):
        form = UploadProblemForm(request.POST, request.FILES)
        if form.is_valid():
            file = form.cleaned_data["file"]
            with tempfile.NamedTemporaryFile("wb") as tf:
                for chunk in file.chunks(4096):
                    tf.file.write(chunk)

                tf.file.flush()
                os.fsync(tf.file)

                problems = FPSParser(tf.name).parse()
        else:
            return self.error("Parse upload file error")

        helper = FPSHelper()
        with transaction.atomic():
            for _problem in problems:
                test_case_id = rand_str()
                test_case_dir = os.path.join(settings.TEST_CASE_DIR, test_case_id)
                os.mkdir(test_case_dir)
                score = []
                for item in helper.save_test_case(_problem, test_case_dir)["test_cases"].values():
                    score.append({"score": 0, "input_name": item["input_name"],
                                  "output_name": item.get("output_name")})
                problem_data = helper.save_image(_problem, settings.UPLOAD_DIR, settings.UPLOAD_PREFIX)
                s = FPSProblemSerializer(data=problem_data)
                if not s.is_valid():
                    return self.error(f"Parse FPS file error: {s.errors}")
                problem_data = s.data
                problem_data["test_case_id"] = test_case_id
                problem_data["test_case_score"] = score
                self._create_problem(problem_data, request.user)
        return self.success({"import_count": len(problems)})
