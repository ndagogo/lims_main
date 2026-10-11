from rest_framework import serializers
from .models import NotificationOutbox, NotificationTemplate
class NotificationOutboxSerializer(serializers.ModelSerializer):
    class Meta: model=NotificationOutbox; fields='__all__'; read_only_fields=('status','provider_message_id','attempts','last_error','sent_at')
class NotificationTemplateSerializer(serializers.ModelSerializer):
    class Meta: model=NotificationTemplate; fields='__all__'
