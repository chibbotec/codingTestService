import hashlib
import json
import logging
from urllib.parse import urljoin

import requests
from django.db import transaction, IntegrityError
from django.db.models import F

from account.models import User
from conf.models import JudgeServer
from contest.models import ContestRuleType, ACMContestRank, OIContestRank, ContestStatus
from options.options import SysOptions
from problem.models import Problem, ProblemRuleType
from problem.utils import parse_problem_template
from submission.models import JudgeStatus, Submission
from utils.cache import cache
from utils.constants import CacheKey

logger = logging.getLogger(__name__)

# 继续处理在队列中的问题
def process_pending_task():
    if cache.llen(CacheKey.waiting_queue):
        # 防止循环引入
        from judge.tasks import judge_task
        tmp_data = cache.rpop(CacheKey.waiting_queue)
        if tmp_data:
            data = json.loads(tmp_data.decode("utf-8"))
            judge_task.send(**data)


# class ChooseJudgeServer:
#     def __init__(self):
#         self.server = None

#     def __enter__(self) -> [JudgeServer, None]:
#         with transaction.atomic():
#             servers = JudgeServer.objects.select_for_update().filter(is_disabled=False).order_by("task_number")
#             servers = [s for s in servers if s.status == "normal"]
#             for server in servers:
#                 if server.task_number <= server.cpu_core * 2:
#                     server.task_number = F("task_number") + 1
#                     server.save(update_fields=["task_number"])
#                     self.server = server
#                     return server
#         return None

#     def __exit__(self, exc_type, exc_val, exc_tb):
#         if self.server:
#             JudgeServer.objects.filter(id=self.server.id).update(task_number=F("task_number") - 1)
class ChooseJudgeServer:
    def __init__(self):
        self.server = None

    def __enter__(self) -> [JudgeServer, None]:
        print("Finding available judge servers...")
        with transaction.atomic():
            servers = JudgeServer.objects.select_for_update().filter(is_disabled=False).order_by("task_number")
            print(f"Total servers found: {len(servers)}")
            if len(servers) == 0:
                print("No judge servers registered in database")
                return None
                
            for s in servers:
                print(f"Server: {s.hostname}, URL: {s.service_url}, Status: {s.status}, Tasks: {s.task_number}, Disabled: {s.is_disabled}")
                
            servers = [s for s in servers if s.status == "normal"]
            print(f"Normal status servers: {len(servers)}")
            
            for server in servers:
                if server.task_number <= server.cpu_core * 2:
                    server.task_number = F("task_number") + 1
                    server.save(update_fields=["task_number"])
                    self.server = server
                    return server
        return None

    def __exit__(self, exc_type, exc_val, exc_tb):
        print(f"Exiting ChooseJudgeServer context, server: {self.server.hostname if self.server else 'None'}")
        if self.server:
            JudgeServer.objects.filter(id=self.server.id).update(task_number=F("task_number") - 1)

class DispatcherBase(object):
    def __init__(self):
        self.token = hashlib.sha256(SysOptions.judge_server_token.encode("utf-8")).hexdigest()

    def _request(self, url, data=None):
        kwargs = {"headers": {"X-Judge-Server-Token": self.token}}
        if data:
            kwargs["json"] = data
        try:
            return requests.post(url, **kwargs).json()
        except Exception as e:
            logger.exception(e)


class SPJCompiler(DispatcherBase):
    def __init__(self, spj_code, spj_version, spj_language):
        super().__init__()
        spj_compile_config = list(filter(lambda config: spj_language == config["name"], SysOptions.spj_languages))[0]["spj"][
            "compile"]
        self.data = {
            "src": spj_code,
            "spj_version": spj_version,
            "spj_compile_config": spj_compile_config
        }

    def compile_spj(self):
        with ChooseJudgeServer() as server:
            if not server:
                return "No available judge_server"
            result = self._request(urljoin(server.service_url, "compile_spj"), data=self.data)
            if not result:
                return "Failed to call judge server"
            if result["err"]:
                return result["data"]


class JudgeDispatcher(DispatcherBase):
    def __init__(self, submission_id, problem_id):
        super().__init__()
        self.submission = Submission.objects.get(id=submission_id)
        self.contest_id = self.submission.contest_id
        self.last_result = self.submission.result if self.submission.info else None

        if self.contest_id:
            self.problem = Problem.objects.select_related("contest").get(id=problem_id, contest_id=self.contest_id)
            self.contest = self.problem.contest
        else:
            self.problem = Problem.objects.get(id=problem_id)

    def _compute_statistic_info(self, resp_data):
        # 用时和内存占用保存为多个测试点中最长的那个
        self.submission.statistic_info["time_cost"] = max([x["cpu_time"] for x in resp_data])
        self.submission.statistic_info["memory_cost"] = max([x["memory"] for x in resp_data])

        # sum up the score in OI mode
        if self.problem.rule_type == ProblemRuleType.OI:
            score = 0
            try:
                for i in range(len(resp_data)):
                    if resp_data[i]["result"] == JudgeStatus.ACCEPTED:
                        resp_data[i]["score"] = self.problem.test_case_score[i]["score"]
                        score += resp_data[i]["score"]
                    else:
                        resp_data[i]["score"] = 0
            except IndexError:
                logger.error(f"Index Error raised when summing up the score in problem {self.problem.id}")
                self.submission.statistic_info["score"] = 0
                return
            self.submission.statistic_info["score"] = score

    def judge(self):
        try:
            print("===== Judge started =====")

             # 시스템 옵션 출력
            print("Judge server configuration:")
            print(f"Judge server token (first 8 chars): {SysOptions.judge_server_token[:8] if SysOptions.judge_server_token else 'None'}")
            print(f"Available languages: {[lang['name'] for lang in SysOptions.languages]}")
            print(f"Available SPJ languages: {[lang['name'] for lang in SysOptions.spj_languages]}")
            language = self.submission.language
            print(f"Judge language: {language}")
            
            print("Finding language configuration...")
            sub_config = list(filter(lambda item: language == item["name"], SysOptions.languages))[0]
            
            spj_config = {}
            if self.problem.spj_code:
                print("Special judge code found, looking for SPJ language...")
                for lang in SysOptions.spj_languages:
                    if lang["name"] == self.problem.spj_language:
                        spj_config = lang["spj"]
                        print(f"SPJ language found: {self.problem.spj_language}")
                        break

            print("Checking for problem template...")
            if language in self.problem.template:
                print("Template for this language exists, applying template...")
                template = parse_problem_template(self.problem.template[language])
                # code = f"{template['prepend']}\n{self.submission.code}\n{template['append']}"
                code = self.submission.code
            else:
                print("No template for this language")
                code = self.submission.code
            print(code)
            print("Preparing judging data...")
            data = {
                "language_config": sub_config["config"],
                "src": code,
                "max_cpu_time": self.problem.time_limit,
                "max_memory": 1024 * 1024 * self.problem.memory_limit,
                "test_case_id": self.problem.test_case_id,
                "output": False,
                "spj_version": self.problem.spj_version,
                "spj_config": spj_config.get("config"),
                "spj_compile_config": spj_config.get("compile"),
                "spj_src": self.problem.spj_code,
                "io_mode": self.problem.io_mode
            }

            print("Choosing judge server...")
            with ChooseJudgeServer() as server:
                if not server:
                    print("No judge server available, pushing to waiting queue")
                    data = {"submission_id": self.submission.id, "problem_id": self.problem.id}
                    cache.lpush(CacheKey.waiting_queue, json.dumps(data))
                    return
                print(f"Judge server selected: {server.service_url}")
                Submission.objects.filter(id=self.submission.id).update(result=JudgeStatus.JUDGING)
                print("Submitting to judge server...")
                resp = self._request(urljoin(server.service_url, "/judge"), data=data)
                print(f"Judge server response received: {bool(resp)}")

            if not resp:
                print("Empty response from judge server, marking as SYSTEM_ERROR")
                Submission.objects.filter(id=self.submission.id).update(result=JudgeStatus.SYSTEM_ERROR)
                return

            if resp["err"]:
                print(f"Compilation error occurred: {resp['err']}")
                self.submission.result = JudgeStatus.COMPILE_ERROR
                self.submission.statistic_info["err_info"] = resp["data"]
                self.submission.statistic_info["score"] = 0
            else:
                print("Processing test case results...")
                resp["data"].sort(key=lambda x: int(x["test_case"]))
                self.submission.info = resp
                print("Computing statistics...")
                self._compute_statistic_info(resp["data"])
                
                error_test_case = list(filter(lambda case: case["result"] != 0, resp["data"]))
                print(f"Error test cases: {len(error_test_case)} out of {len(resp['data'])}")
                
                # ACM모드에서는 모든 테스트 케이스가 정확해야 AC, 그렇지 않으면 첫 번째 오류 테스트 케이스의 상태를 가져옴
                # OI모드에서는 모든 테스트 케이스가 정확하면 AC, 모두 오류면 첫 번째 오류 테스트 케이스의 상태, 그렇지 않으면 부분 정답
                if not error_test_case:
                    print("No error test cases, marking as ACCEPTED")
                    self.submission.result = JudgeStatus.ACCEPTED
                elif self.problem.rule_type == ProblemRuleType.ACM or len(error_test_case) == len(resp["data"]):
                    print(f"Setting result to: {error_test_case[0]['result']}")
                    self.submission.result = error_test_case[0]["result"]
                else:
                    print("Some test cases passed, marking as PARTIALLY_ACCEPTED")
                    self.submission.result = JudgeStatus.PARTIALLY_ACCEPTED
                    
            print("Saving submission...")
            self.submission.save()

            if self.contest_id:
                print(f"Contest submission (contest_id: {self.contest_id})")
                if self.contest.status != ContestStatus.CONTEST_UNDERWAY or \
                        User.objects.get(id=self.submission.user_id).is_contest_admin(self.contest):
                    logger.info(
                        "Contest debug mode, id: " + str(self.contest_id) + ", submission id: " + self.submission.id)
                    return
                print("Updating contest status...")
                with transaction.atomic():
                    print("Updating contest problem status...")
                    self.update_contest_problem_status()
                    print("Updating contest rank...")
                    self.update_contest_rank()
            # else:
            #     print("Regular submission (no contest)")
            #     if self.last_result:
            #         print("Updating problem status (rejudge)...")
            #         self.update_problem_status_rejudge()
            #     else:
            #         print("Updating problem status (first judge)...")
            #         self.update_problem_status()

            print("Judging complete, processing pending tasks...")
            process_pending_task()
            print("===== Judge finished =====")
        except Exception as e:
            print(f"ERROR in judge: {str(e)}")
            import traceback
            print(traceback.format_exc())
            # 예외가 발생해도 DB에 에러 상태 저장 시도
            try:
                Submission.objects.filter(id=self.submission.id).update(result=JudgeStatus.SYSTEM_ERROR)
            except:
                print("Failed to update submission status after error")
    

    
    def update_problem_status_rejudge(self):
        result = str(self.submission.result)
        problem_id = str(self.problem.id)
        with transaction.atomic():
            # update problem status
            problem = Problem.objects.select_for_update().get(contest_id=self.contest_id, id=self.problem.id)
            if self.last_result != JudgeStatus.ACCEPTED and self.submission.result == JudgeStatus.ACCEPTED:
                problem.accepted_number += 1
            problem_info = problem.statistic_info
            problem_info[self.last_result] = problem_info.get(self.last_result, 1) - 1
            problem_info[result] = problem_info.get(result, 0) + 1
            problem.save(update_fields=["accepted_number", "statistic_info"])

            profile = User.objects.select_for_update().get(id=self.submission.user_id).userprofile
            if problem.rule_type == ProblemRuleType.ACM:
                acm_problems_status = profile.acm_problems_status.get("problems", {})
                if acm_problems_status[problem_id]["status"] != JudgeStatus.ACCEPTED:
                    acm_problems_status[problem_id]["status"] = self.submission.result
                    if self.submission.result == JudgeStatus.ACCEPTED:
                        profile.accepted_number += 1
                profile.acm_problems_status["problems"] = acm_problems_status
                profile.save(update_fields=["accepted_number", "acm_problems_status"])

            else:
                oi_problems_status = profile.oi_problems_status.get("problems", {})
                score = self.submission.statistic_info["score"]
                if oi_problems_status[problem_id]["status"] != JudgeStatus.ACCEPTED:
                    # minus last time score, add this tim score
                    profile.add_score(this_time_score=score,
                                      last_time_score=oi_problems_status[problem_id]["score"])
                    oi_problems_status[problem_id]["score"] = score
                    oi_problems_status[problem_id]["status"] = self.submission.result
                    if self.submission.result == JudgeStatus.ACCEPTED:
                        profile.accepted_number += 1
                profile.oi_problems_status["problems"] = oi_problems_status
                profile.save(update_fields=["accepted_number", "oi_problems_status"])

    def update_problem_status(self):
        result = str(self.submission.result)
        problem_id = str(self.problem.id)
        with transaction.atomic():
            # update problem status
            problem = Problem.objects.select_for_update().get(contest_id=self.contest_id, id=self.problem.id)
            problem.submission_number += 1
            if self.submission.result == JudgeStatus.ACCEPTED:
                problem.accepted_number += 1
            problem_info = problem.statistic_info
            problem_info[result] = problem_info.get(result, 0) + 1
            problem.save(update_fields=["accepted_number", "submission_number", "statistic_info"])

            # update_userprofile
            user = User.objects.select_for_update().get(id=self.submission.user_id)
            user_profile = user.userprofile
            user_profile.submission_number += 1
            if problem.rule_type == ProblemRuleType.ACM:
                acm_problems_status = user_profile.acm_problems_status.get("problems", {})
                if problem_id not in acm_problems_status:
                    acm_problems_status[problem_id] = {"status": self.submission.result, "_id": self.problem._id}
                    if self.submission.result == JudgeStatus.ACCEPTED:
                        user_profile.accepted_number += 1
                elif acm_problems_status[problem_id]["status"] != JudgeStatus.ACCEPTED:
                    acm_problems_status[problem_id]["status"] = self.submission.result
                    if self.submission.result == JudgeStatus.ACCEPTED:
                        user_profile.accepted_number += 1
                user_profile.acm_problems_status["problems"] = acm_problems_status
                user_profile.save(update_fields=["submission_number", "accepted_number", "acm_problems_status"])

            else:
                oi_problems_status = user_profile.oi_problems_status.get("problems", {})
                score = self.submission.statistic_info["score"]
                if problem_id not in oi_problems_status:
                    user_profile.add_score(score)
                    oi_problems_status[problem_id] = {"status": self.submission.result,
                                                      "_id": self.problem._id,
                                                      "score": score}
                    if self.submission.result == JudgeStatus.ACCEPTED:
                        user_profile.accepted_number += 1
                elif oi_problems_status[problem_id]["status"] != JudgeStatus.ACCEPTED:
                    # minus last time score, add this time score
                    user_profile.add_score(this_time_score=score,
                                           last_time_score=oi_problems_status[problem_id]["score"])
                    oi_problems_status[problem_id]["score"] = score
                    oi_problems_status[problem_id]["status"] = self.submission.result
                    if self.submission.result == JudgeStatus.ACCEPTED:
                        user_profile.accepted_number += 1
                user_profile.oi_problems_status["problems"] = oi_problems_status
                user_profile.save(update_fields=["submission_number", "accepted_number", "oi_problems_status"])

    def update_contest_problem_status(self):
        with transaction.atomic():
            user = User.objects.select_for_update().get(id=self.submission.user_id)
            user_profile = user.userprofile
            problem_id = str(self.problem.id)
            if self.contest.rule_type == ContestRuleType.ACM:
                contest_problems_status = user_profile.acm_problems_status.get("contest_problems", {})
                if problem_id not in contest_problems_status:
                    contest_problems_status[problem_id] = {"status": self.submission.result, "_id": self.problem._id}
                elif contest_problems_status[problem_id]["status"] != JudgeStatus.ACCEPTED:
                    contest_problems_status[problem_id]["status"] = self.submission.result
                else:
                    # 如果已AC， 直接跳过 不计入任何计数器
                    return
                user_profile.acm_problems_status["contest_problems"] = contest_problems_status
                user_profile.save(update_fields=["acm_problems_status"])

            elif self.contest.rule_type == ContestRuleType.OI:
                contest_problems_status = user_profile.oi_problems_status.get("contest_problems", {})
                score = self.submission.statistic_info["score"]
                if problem_id not in contest_problems_status:
                    contest_problems_status[problem_id] = {"status": self.submission.result,
                                                           "_id": self.problem._id,
                                                           "score": score}
                else:
                    contest_problems_status[problem_id]["score"] = score
                    contest_problems_status[problem_id]["status"] = self.submission.result
                user_profile.oi_problems_status["contest_problems"] = contest_problems_status
                user_profile.save(update_fields=["oi_problems_status"])

            problem = Problem.objects.select_for_update().get(contest_id=self.contest_id, id=self.problem.id)
            result = str(self.submission.result)
            problem_info = problem.statistic_info
            problem_info[result] = problem_info.get(result, 0) + 1
            problem.submission_number += 1
            if self.submission.result == JudgeStatus.ACCEPTED:
                problem.accepted_number += 1
            problem.save(update_fields=["submission_number", "accepted_number", "statistic_info"])

    def update_contest_rank(self):
        if self.contest.rule_type == ContestRuleType.OI or self.contest.real_time_rank:
            cache.delete(f"{CacheKey.contest_rank_cache}:{self.contest.id}")

        def get_rank(model):
            return model.objects.select_for_update().get(user_id=self.submission.user_id, contest=self.contest)

        if self.contest.rule_type == ContestRuleType.ACM:
            model = ACMContestRank
            func = self._update_acm_contest_rank
        else:
            model = OIContestRank
            func = self._update_oi_contest_rank

        try:
            rank = get_rank(model)
        except model.DoesNotExist:
            try:
                model.objects.create(user_id=self.submission.user_id, contest=self.contest)
                rank = get_rank(model)
            except IntegrityError:
                rank = get_rank(model)
        func(rank)

    def _update_acm_contest_rank(self, rank):
        info = rank.submission_info.get(str(self.submission.problem_id))
        # 因前面更改过，这里需要重新获取
        problem = Problem.objects.select_for_update().get(contest_id=self.contest_id, id=self.problem.id)
        # 此题提交过
        if info:
            if info["is_ac"]:
                return

            rank.submission_number += 1
            if self.submission.result == JudgeStatus.ACCEPTED:
                rank.accepted_number += 1
                info["is_ac"] = True
                info["ac_time"] = (self.submission.create_time - self.contest.start_time).total_seconds()
                rank.total_time += info["ac_time"] + info["error_number"] * 20 * 60

                if problem.accepted_number == 1:
                    info["is_first_ac"] = True
            elif self.submission.result != JudgeStatus.COMPILE_ERROR:
                info["error_number"] += 1

        # 第一次提交
        else:
            rank.submission_number += 1
            info = {"is_ac": False, "ac_time": 0, "error_number": 0, "is_first_ac": False}
            if self.submission.result == JudgeStatus.ACCEPTED:
                rank.accepted_number += 1
                info["is_ac"] = True
                info["ac_time"] = (self.submission.create_time - self.contest.start_time).total_seconds()
                rank.total_time += info["ac_time"]

                if problem.accepted_number == 1:
                    info["is_first_ac"] = True

            elif self.submission.result != JudgeStatus.COMPILE_ERROR:
                info["error_number"] = 1
        rank.submission_info[str(self.submission.problem_id)] = info
        rank.save()

    def _update_oi_contest_rank(self, rank):
        problem_id = str(self.submission.problem_id)
        current_score = self.submission.statistic_info["score"]
        last_score = rank.submission_info.get(problem_id)
        if last_score:
            rank.total_score = rank.total_score - last_score + current_score
        else:
            rank.total_score = rank.total_score + current_score
        rank.submission_info[problem_id] = current_score
        rank.save()
