import ipaddress

from account.decorators import check_contest_permission
from contest.models import ContestStatus, ContestRuleType
from judge.tasks import judge_task
from options.options import SysOptions
from problem.models import Problem, ProblemRuleType
from utils.api import APIView, validate_serializer
from utils.cache import cache
from utils.captcha import Captcha
from utils.throttling import TokenBucket
from ..models import Submission
from ..serializers import (CreateSubmissionSerializer, SubmissionModelSerializer,
                          SubmissionSafeModelSerializer, SubmissionListSerializer)


class SpaceSubmissionAPI(APIView):
    """
    API for handling submissions through a space (with API Gateway authentication)
    Uses X-User-ID and X-Username headers instead of session authentication
    URL pattern: /api/v1/coding-test/{space_id}/submission/
    """
    
    def throttling(self, request):
        # Get user ID from header instead of request.user
        user_id = request.headers.get("X-User-ID")
        if not user_id:
            return "User ID not found in headers"
            
        user_bucket = TokenBucket(key=str(user_id),
                                redis_conn=cache, **SysOptions.throttling["user"])
        can_consume, wait = user_bucket.consume()
        if not can_consume:
            return "Please wait %d seconds" % (int(wait))

    @check_contest_permission(check_type="problems")
    def check_contest_permission(self, request):
        contest = self.contest
        if contest.status == ContestStatus.CONTEST_ENDED:
            return self.error("The contest has ended")
            
        # Get user information from headers for space submissions
        user_id = request.headers.get("X-User-ID")
        if not user_id:
            return self.error("User ID not found in headers")

    @validate_serializer(CreateSubmissionSerializer)
    def post(self, request, space_id):
        print(f"[DEBUG] POST request to /api/v1/coding-test/{space_id}/submission")
        print(f"[DEBUG] Headers: {dict(request.headers)}")
        print(f"[DEBUG] Data: {request.data}")
        data = request.data
        hide_id = False
        
        # Extract user info from headers
        user_id = request.headers.get("X-User-ID")
        username = request.headers.get("X-Username")
        
        if not user_id or not username:
            return self.error("User identification not found in request headers")
            
        if data.get("contest_id"):
            error = self.check_contest_permission(request)
            if error:
                return error
            contest = self.contest
            # TODO: Implement proper permission check based on headers

        if data.get("captcha"):
            if not Captcha(request).check(data["captcha"]):
                return self.error("Invalid captcha")
                
        error = self.throttling(request)
        if error:
            return self.error(error)

        try:
            # 문제 ID를 찾을 때 space_id도 함께 확인
            problem = Problem.objects.get(
                id=data["problem_id"], 
                space_id=space_id,
                contest_id=data.get("contest_id"), 
                visible=True
            )
            print(f"[DEBUG] Problem found: {problem.id} - {problem.title}")
            print(f"[DEBUG] Problem languages: {problem.languages}")
        except Problem.DoesNotExist:
            return self.error("Problem does not exist")
            
        if data["language"] not in problem.languages:
            return self.error(f"{data['language']} is not allowed in the problem")
            
        # Get client IP (either from X-Forwarded-For or fallback)
        client_ip = request.headers.get("X-Forwarded-For", "0.0.0.0").split(",")[0].strip()
            
        # Create submission with user info from headers
        submission = Submission.objects.create(
            user_id=int(user_id),
            username=username,
            language=data["language"],
            code=data["code"],
            problem_id=problem.id,
            ip=client_ip,
            contest_id=data.get("contest_id")
        )
        print("Submission created:", submission.id, "by user:", username)
        print("submission code:", data["code"])
        print("submission language:", data["language"])
        print("submission problem_id:", problem.id)
        print("submission problem title:", problem.title)
        print("submission problem space_id:", problem.space_id)
        print("submission problem languages:", problem.languages)
            
        # Send to judge task
        judge_task.send(submission.id, problem.id)
            
        if hide_id:
            return self.success()
        else:
            return self.success({"submission_id": submission.id})

    def get(self, request, space_id, submission_id=None):
        """Get submission details by ID or list submissions"""
        # 특정 제출 상세 조회
        if submission_id:
            try:
                submission = Submission.objects.select_related("problem").get(id=submission_id)
                
                # space_id 체크 (문제의 space_id가 URL의 space_id와 일치하는지)
                problem = Problem.objects.get(id=submission.problem_id)
                if str(problem.space_id) != str(space_id):
                    return self.error("Submission not found in this space")
                
                # Extract user info from headers
                user_id = request.headers.get("X-User-ID")
                if not user_id:
                    return self.error("User identification not found in request headers")
                
                # Check if the user has permission to view this submission
                if not self._check_submission_permission(submission, int(user_id)):
                    return self.error("No permission for this submission")

                # TODO: Implement proper admin role check based on headers
                is_admin = False  # This should be replaced with actual check
                
                if submission.problem.rule_type == ProblemRuleType.OI or is_admin:
                    submission_data = SubmissionModelSerializer(submission).data
                else:
                    submission_data = SubmissionSafeModelSerializer(submission).data
                
                # Check if user can unshare the submission
                submission_data["can_unshare"] = self._check_submission_permission(
                    submission, int(user_id), check_share=False
                )
                return self.success(submission_data)
            except Submission.DoesNotExist:
                return self.error("Submission doesn't exist")
            except Problem.DoesNotExist:
                return self.error("Problem associated with submission does not exist")
        
        # 제출 목록 조회
        # Extract user info from headers
        user_id = request.headers.get("X-User-ID")
        if not user_id:
            return self.error("User identification not found in request headers")
        
        if not request.GET.get("limit"):
            return self.error("Limit is needed")
        
        # 해당 space의 문제 ID 목록 가져오기
        try:
            problem_ids = Problem.objects.filter(space_id=space_id).values_list('id', flat=True)
        except Exception as e:
            return self.error(f"Error fetching problems: {str(e)}")
        
        # 해당 space의 문제에 대한 제출만 필터링
        submissions = Submission.objects.filter(problem_id__in=problem_ids).select_related("problem__created_by")
        
        problem_id = request.GET.get("problem_id")
        myself = request.GET.get("myself")
        result = request.GET.get("result")
        username = request.GET.get("username")
        
        if problem_id:
            try:
                problem = Problem.objects.get(id=problem_id, space_id=space_id, visible=True)
                submissions = submissions.filter(problem=problem)
            except Problem.DoesNotExist:
                return self.error("Problem doesn't exist")
        
        if (myself and myself == "1") or not SysOptions.submission_list_show_all:
            submissions = submissions.filter(user_id=user_id)
        elif username:
            submissions = submissions.filter(username__icontains=username)
        
        if result:
            submissions = submissions.filter(result=result)
        
        data = self.paginate_data(request, submissions)
        
        # Create a mock User object with just id for the serializer
        class MockUser:
            def __init__(self, id):
                self.id = id
        
        mock_user = MockUser(int(user_id))
        data["results"] = SubmissionListSerializer(data["results"], many=True, user=mock_user).data
        return self.success(data)
            
    def _check_submission_permission(self, submission, user_id, check_share=True):
        """
        Custom method to check if user has permission for the submission
        Replicates the logic from the Submission model but uses user_id from headers
        """
        # The submission owner has all permissions
        if submission.user_id == user_id:
            return True
            
        # TODO: Implement proper admin role check
        # is_admin = user_is_admin(user_id)
        # if is_admin:
        #    return True
            
        # If checking share permissions specifically
        if check_share:
            # Shared submissions can be viewed by others
            if submission.shared:
                return True
                
            # Contest submissions might have special rules
            if submission.contest:
                # TODO: Implement proper contest status and permission check
                # if user_has_permission_for_contest(user_id, submission.contest):
                #    return True
                pass
                
        return False