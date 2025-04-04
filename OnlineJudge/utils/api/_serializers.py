from rest_framework import serializers


class UsernameSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    nickname = serializers.CharField()  # nickname 필드를 username으로 매핑
    role = serializers.CharField(read_only=True)  # role 필드 추가
    space_id = serializers.IntegerField(read_only=True, required=False)  # space_id 필드 추가

    def __init__(self, *args, **kwargs):
        self.need_real_name = kwargs.pop("need_real_name", False)
        super().__init__(*args, **kwargs)

    def get_real_name(self, obj):
        return obj.userprofile.real_name if self.need_real_name else None
