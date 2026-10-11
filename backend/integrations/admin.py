from django.contrib import admin
from .models import Analyzer, AnalyzerTestMapping, AnalyzerMessage
@admin.register(Analyzer)
class AnalyzerAdmin(admin.ModelAdmin): list_display=('name','centre','manufacturer','model','protocol','is_active','last_seen_at'); list_filter=('centre','protocol','is_active'); search_fields=('name','instrument_identifier','host')
@admin.register(AnalyzerTestMapping)
class MappingAdmin(admin.ModelAdmin): list_display=('analyzer','instrument_test_code','test','parameter','enabled'); list_filter=('analyzer','enabled'); search_fields=('instrument_test_code','test__name')
@admin.register(AnalyzerMessage)
class MessageAdmin(admin.ModelAdmin): list_display=('received_at','analyzer','sample','status'); list_filter=('status','analyzer'); search_fields=('external_message_id','sample__sample_id'); readonly_fields=('raw_payload','parsed_payload','received_at')
