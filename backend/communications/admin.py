from django.contrib import admin
from .models import NotificationOutbox, NotificationTemplate
@admin.register(NotificationOutbox)
class OutboxAdmin(admin.ModelAdmin): list_display=('created_at','centre','channel','recipient','status','attempts','sent_at'); list_filter=('centre','channel','status'); search_fields=('recipient','provider_message_id'); readonly_fields=('created_at','updated_at','sent_at','provider_message_id','attempts','last_error')
@admin.register(NotificationTemplate)
class TemplateAdmin(admin.ModelAdmin): list_display=('key','name','channel','is_active'); list_filter=('channel','is_active'); search_fields=('key','name')
