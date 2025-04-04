# coding=utf-8
import os
from utils.shortcuts import get_env

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 환경 변수 설정
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'HOST': get_env("POSTGRES_HOST", "localhost"),
        'PORT': get_env("POSTGRES_PORT", "5432"),
        'NAME': get_env("POSTGRES_DB", "onlinejudge"),
        'USER': get_env("POSTGRES_USER", "myuser"),
        'PASSWORD': get_env("POSTGRES_PASSWORD", "1234"),
    }
}

REDIS_CONF = {
    'host': get_env('REDIS_HOST', '127.0.0.1'),
    'port': get_env('REDIS_PORT', '6379')
}

JUDGE_SERVER_TOKEN = get_env("JUDGE_SERVER_TOKEN", "jdkeoakkxkk34kdak")

DEBUG = True

ALLOWED_HOSTS = ["*"]

DATA_DIR = f"{BASE_DIR}/data"
