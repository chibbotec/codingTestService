from django.conf.urls import url

from ..views.oj import ProblemTagAPI, ContestProblemAPI, PickOneAPI, SpaceProblemAPI
from ..views.admin import TestCaseAPI

urlpatterns = [
    url(r"^problem/tags/?$", ProblemTagAPI.as_view(), name="problem_tag_list_api"),
    # url(r"^problem/?$", ProblemAPI.as_view(), name="problem_api"),
    url(r"^pickone/?$", PickOneAPI.as_view(), name="pick_one_api"),
    url(r"^contest/problem/?$", ContestProblemAPI.as_view(), name="contest_problem_api"),
    
    # 새로운 URL 패턴: space_id를 경로 파라미터로 포함
    url(r"^v1/coding-test/(?P<space_id>\w+)/problems/?$", SpaceProblemAPI.as_view(), name="space_problem_api"),
    url(r"^v1/coding-test/(?P<space_id>\w+)/problems/(?P<problem_id>\w+)/?$", SpaceProblemAPI.as_view(), name="space_problem_detail_api"),
    url(r"^v1/coding-test/problem-tags/?$", ProblemTagAPI.as_view(), name="coding_test_problem_tags_api"),
]