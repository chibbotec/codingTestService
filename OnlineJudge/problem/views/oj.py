import random
from django.db.models import Q, Count
from utils.api import APIView
from account.decorators import check_contest_permission
from ..models import ProblemTag, Problem, ProblemRuleType
from ..serializers import ProblemSerializer, TagSerializer, ProblemSafeSerializer
from contest.models import ContestRuleType


class ProblemTagAPI(APIView):
    def get(self, request):
        qs = ProblemTag.objects
        keyword = request.GET.get("keyword")
        if keyword:
            qs = ProblemTag.objects.filter(name__icontains=keyword)
        tags = qs.annotate(problem_count=Count("problem")).filter(problem_count__gt=0)
        return self.success(TagSerializer(tags, many=True).data)


class PickOneAPI(APIView):
    def get(self, request):
        problems = Problem.objects.filter(contest_id__isnull=True, visible=True)
        count = problems.count()
        if count == 0:
            return self.error("No problem to pick")
        return self.success(problems[random.randint(0, count - 1)]._id)


# class ProblemAPI(APIView):
    @staticmethod
    def _add_problem_status(request, queryset_values):
        if request.user.is_authenticated:
            profile = request.user.userprofile
            acm_problems_status = profile.acm_problems_status.get("problems", {})
            oi_problems_status = profile.oi_problems_status.get("problems", {})
            # paginate data
            results = queryset_values.get("results")
            if results is not None:
                problems = results
            else:
                problems = [queryset_values, ]
            for problem in problems:
                if problem["rule_type"] == ProblemRuleType.ACM:
                    problem["my_status"] = acm_problems_status.get(str(problem["id"]), {}).get("status")
                else:
                    problem["my_status"] = oi_problems_status.get(str(problem["id"]), {}).get("status")

    def get(self, request):
        # Space ID를 쿼리 파라미터에서 가져오기
        space_id = request.GET.get("space_id")
        
        # 문제 상세 정보 요청인 경우
        problem_id = request.GET.get("problem_id")
        if problem_id:
            try:
                # 기본 쿼리
                query = {"_id": problem_id, "contest_id__isnull": True, "visible": True}
                
                # space_id가 제공된 경우 필터에 추가
                if space_id:
                    query["space_id"] = space_id
                
                problem = Problem.objects.select_related("created_by").get(**query)
                problem_data = ProblemSerializer(problem).data
                self._add_problem_status(request, problem_data)
                return self.success(problem_data)
            except Problem.DoesNotExist:
                return self.error("Problem does not exist")

        # 문제 목록 요청인 경우
        limit = request.GET.get("limit")
        if not limit:
            return self.error("Limit is needed")

        # 기본 쿼리: contest_id가 없고 visible인 문제들
        problems = Problem.objects.select_related("created_by").filter(contest_id__isnull=True, visible=True)
        
        # space_id 필터 적용
        if space_id:
            problems = problems.filter(space_id=space_id)
        
        # 기존 필터 적용 (태그, 키워드, 난이도 등)
        tag_text = request.GET.get("tag")
        if tag_text:
            problems = problems.filter(tags__name=tag_text)

        keyword = request.GET.get("keyword", "").strip()
        if keyword:
            problems = problems.filter(Q(title__icontains=keyword) | Q(_id__icontains=keyword))

        difficulty = request.GET.get("difficulty")
        if difficulty:
            problems = problems.filter(difficulty=difficulty)
            
        data = self.paginate_data(request, problems, ProblemSerializer)
        self._add_problem_status(request, data)
        return self.success(data)

class ContestProblemAPI(APIView):
    def _add_problem_status(self, request, queryset_values):
        if request.user.is_authenticated:
            profile = request.user.userprofile
            if self.contest.rule_type == ContestRuleType.ACM:
                problems_status = profile.acm_problems_status.get("contest_problems", {})
            else:
                problems_status = profile.oi_problems_status.get("contest_problems", {})
            for problem in queryset_values:
                problem["my_status"] = problems_status.get(str(problem["id"]), {}).get("status")

    @check_contest_permission(check_type="problems")
    def get(self, request):
        problem_id = request.GET.get("problem_id")
        if problem_id:
            try:
                problem = Problem.objects.select_related("created_by").get(_id=problem_id,
                                                                           contest=self.contest,
                                                                           visible=True)
            except Problem.DoesNotExist:
                return self.error("Problem does not exist.")
            if self.contest.problem_details_permission(request.user):
                problem_data = ProblemSerializer(problem).data
                self._add_problem_status(request, [problem_data, ])
            else:
                problem_data = ProblemSafeSerializer(problem).data
            return self.success(problem_data)

        contest_problems = Problem.objects.select_related("created_by").filter(contest=self.contest, visible=True)
        if self.contest.problem_details_permission(request.user):
            data = ProblemSerializer(contest_problems, many=True).data
            self._add_problem_status(request, data)
        else:
            data = ProblemSafeSerializer(contest_problems, many=True).data
        return self.success(data)

class SpaceProblemAPI(APIView):
    def get(self, request, space_id, problem_id=None):
        print(f"[DEBUG] SpaceProblemAPI: space_id={space_id}, problem_id={problem_id}")
        # 문제 상세 정보 요청인 경우
        if problem_id:
            try:
                problem = Problem.objects.select_related("created_by") \
                    .get(id=problem_id, space_id=space_id, visible=True)
                problem_data = ProblemSerializer(problem).data
                print(problem_data)
                return self.success(problem_data)
            except Problem.DoesNotExist:
                return self.error("Problem does not exist")
        
        # 문제 목록 요청인 경우
        problems = Problem.objects.select_related("created_by").filter(space_id=space_id, visible=True)
        
        # 필터 적용
        tag_text = request.GET.get("tag")
        if tag_text:
            problems = problems.filter(tags__name=tag_text)

        keyword = request.GET.get("keyword", "").strip()
        if keyword:
            problems = problems.filter(Q(title__icontains=keyword) | Q(_id__icontains=keyword))

        difficulty = request.GET.get("difficulty")
        if difficulty:
            problems = problems.filter(difficulty=difficulty)
        
        # 페이지네이션 옵션으로 처리
        offset = int(request.GET.get("offset", 0))
        limit = int(request.GET.get("limit", 10))
        results = problems[offset:offset + limit]
        
        data = ProblemSerializer(results, many=True).data
        return self.success(data)
    
    def post(self, request, space_id):
        # 새 문제 생성
        data = request.data
        data["space_id"] = space_id
        
        # 필요한 필드 검증
        if not data.get("title") or not data.get("description"):
            return self.error("Title and description are required")
        
        # 기본값 설정
        data.setdefault("difficulty", 2)  # Medium
        data.setdefault("time_limit", 1000)  # 1000ms
        data.setdefault("memory_limit", 256)  # 256MB
        data.setdefault("visible", True)
        
        # 문제 생성
        serializer = ProblemSerializer(data=data)
        if serializer.is_valid():
            problem = serializer.save()
            return self.success(ProblemSerializer(problem).data)
        else:
            return self.error(serializer.errors)
    
    def delete(self, request, space_id, problem_id):
        try:
            problem = Problem.objects.get(_id=problem_id, space_id=space_id)
            problem.delete()
            return self.success("Problem deleted successfully")
        except Problem.DoesNotExist:
            return self.error("Problem does not exist")