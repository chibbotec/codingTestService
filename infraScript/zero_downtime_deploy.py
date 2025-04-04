#!/usr/bin/env python3

import os
import subprocess
import sys
import time

# 기본 경로 설정
BASE_DIR = "/dockerProjects/chibbotec/coding_test_service"

def run_command(command):
    """명령어를 실행하고 결과를 출력합니다."""
    print(f"실행: {command}")
    process = subprocess.run(command, shell=True, text=True)
    return process.returncode == 0

def setup_directories():
    """필요한 디렉토리 구조를 생성합니다."""
    print("필요한 디렉토리 생성 중...")

    # OnlineJudge 관련 디렉토리
    os.makedirs(f"{BASE_DIR}/OnlineJudge/data", exist_ok=True)
    
    # 로그 및 실행 디렉토리
    os.makedirs(f"{BASE_DIR}/JudgeServer/log", exist_ok=True)
    os.makedirs(f"{BASE_DIR}/JudgeServer/run", exist_ok=True)
    
    print("디렉토리 생성 완료")

def pull_images():
    """GitHub Container Registry에서 최신 이미지를 가져옵니다."""
    print("OnlineJudge 이미지 가져오는 중...")
    if not run_command("docker pull ghcr.io/chibbotec/onlinejudge:latest"):
        print("OnlineJudge 이미지 가져오기 실패")
        return False
    
    print("JudgeServer 이미지 가져오는 중...")
    if not run_command("docker pull ghcr.io/chibbotec/judgeserver:latest"):
        print("JudgeServer 이미지 가져오기 실패")
        return False
    
    print("이미지 가져오기 완료")
    return True

def deploy_containers():
    """컨테이너를 배포합니다."""
    # 작업 디렉토리로 이동
    os.chdir(BASE_DIR)
    
    print("컨테이너 배포 중...")
    if not run_command("docker-compose up -d"):
        print("컨테이너 배포 실패")
        return False
    
    print("배포 상태 확인 중...")
    time.sleep(5)  # 컨테이너가 시작될 시간을 줍니다
    # run_command("docker compose ps")
    
    print("컨테이너 배포 완료")
    return True

def main():
    """배포 과정의 메인 함수입니다."""
    print("===== OnlineJudge 시스템 배포 시작 =====")
    
    # 1. 필요한 디렉토리 생성
    setup_directories()
    
    # 2. 최신 이미지 가져오기
    if not pull_images():
        print("이미지 가져오기에 실패했습니다. 배포를 중단합니다.")
        return False
    
    # 3. 컨테이너 배포
    if not deploy_containers():
        print("컨테이너 배포에 실패했습니다.")
        return False
    
    print("===== 배포 완료 =====")
    print("OnlineJudge 웹 인터페이스: http://localhost:9050")
    print("Judge 서버: http://localhost:12358")
    return True

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)