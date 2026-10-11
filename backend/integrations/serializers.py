from rest_framework import serializers
from .models import Analyzer, AnalyzerTestMapping, AnalyzerMessage

class AnalyzerSerializer(serializers.ModelSerializer):
    class Meta: model = Analyzer; fields = '__all__'; read_only_fields = ('last_seen_at',)
class AnalyzerTestMappingSerializer(serializers.ModelSerializer):
    class Meta: model = AnalyzerTestMapping; fields = '__all__'
class AnalyzerMessageSerializer(serializers.ModelSerializer):
    class Meta: model = AnalyzerMessage; fields = '__all__'; read_only_fields = ('status','parsed_payload','error_message','received_at','reviewed_by','reviewed_at')
