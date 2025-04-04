import requests
import logging
from django.conf import settings

logger = logging.getLogger(__name__)

class SpaceMemberClient:
    def __init__(self):
        self.base_url = settings.SPACE_SERVICE_URL if hasattr(settings, 'SPACE_SERVICE_URL') else "http://localhost:9030"

    def get_space_member(self, space_id, user_id):
        """
        스페이스 서비스에서 특정 사용자의 스페이스 멤버 정보를 가져옵니다.
        
        Args:
            space_id (int): 스페이스 ID
            user_id (int): 사용자 ID
            
        Returns:
            dict: 스페이스 멤버 정보 (성공 시)
            None: 요청 실패 또는 멤버 정보가 없을 때
        """
        try:
            url = f"{self.base_url}/api/v1/space/{space_id}/members/{user_id}"
            logger.info(f"Requesting space member from: {url}")
            
            response = requests.get(url, timeout=5)
            
            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"Failed to get space member: {response.status_code} - {response.text}")
                return None
        except Exception as e:
            logger.error(f"Error while fetching space member: {str(e)}")
            return None