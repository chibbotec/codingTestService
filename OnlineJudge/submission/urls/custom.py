from django.conf.urls import url
from django.views.decorators.csrf import csrf_exempt

from ..views.custom import SpaceSubmissionAPI

urlpatterns = [
    url(r"^v1/coding-test/(?P<space_id>\w+)/submission/?$", 
        csrf_exempt(SpaceSubmissionAPI.as_view()), 
        name="space_problem_submit_api"),

    url(r"^v1/coding-test/(?P<space_id>\w+)/submission/(?P<submission_id>\w+)/?$", 
    csrf_exempt(SpaceSubmissionAPI.as_view()), 
    name="space_problem_submit_api"),
]