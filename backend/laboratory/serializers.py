from rest_framework import serializers
from .models import (
    ServiceGroup, Test, Tariff, TestPrice, LabOrder, Sample, SampleActivity, SampleBatch,
    BatchSample, SampleProcessing, TestParameter, ResultTemplate, LabResult, ResultValue,
    ResultReview, QcMaterial, QcRun, TatEscalation, TatPauseReason, TatRule,
)

class ServiceGroupSerializer(serializers.ModelSerializer):
    class Meta: model=ServiceGroup; fields='__all__'
class TestSerializer(serializers.ModelSerializer):
    service_group_name=serializers.CharField(source='service_group.name',read_only=True,default='')
    class Meta: model=Test; fields='__all__'
    def validate_service_group(self, value):
        if value is None:
            raise serializers.ValidationError('Each test must be mapped to a service group.')
        return value
    def validate(self, attrs):
        group = attrs.get('service_group', getattr(self.instance, 'service_group', None))
        if group is None:
            raise serializers.ValidationError({'service_group': 'Each test must be mapped to a service group.'})
        return attrs
class TariffSerializer(serializers.ModelSerializer):
    class Meta: model=Tariff; fields='__all__'
class TestPriceSerializer(serializers.ModelSerializer):
    test_name=serializers.CharField(source='test.name',read_only=True); tariff_name=serializers.CharField(source='tariff.name',read_only=True)
    class Meta: model=TestPrice; fields=['id','tariff','tariff_name','test','test_name','price','outsource_price','created_at','updated_at']
class LabOrderSerializer(serializers.ModelSerializer):
    class Meta: model=LabOrder; fields='__all__'; read_only_fields=['id','status','created_at','updated_at']
    def validate(self,attrs):
        test=attrs.get('test',getattr(self.instance,'test',None)); tariff=attrs.get('tariff',getattr(self.instance,'tariff',None))
        if test and not tariff: raise serializers.ValidationError({'tariff':'A tariff is required.'})
        if tariff and test:
            price=TestPrice.objects.filter(test=test,tariff=tariff).first()
            if not price: raise serializers.ValidationError({'tariff':'No price is configured for this test and tariff.'})
            attrs['price']=price.price
        return attrs
class SampleSerializer(serializers.ModelSerializer):
    patient_name=serializers.SerializerMethodField(); visit_id=serializers.CharField(source='order.visit.visit_id',read_only=True); test_name=serializers.CharField(source='order.test.name',read_only=True)
    class Meta:
        model=Sample
        fields='__all__'
        read_only_fields=[
            'id','sample_id','status','collected_by','collected_at','collector_ip','collector_browser',
            'specimen_type','disposition','rejection_reason','corrective_action','rejected_by','rejected_at',
            'recollection_of','created_at','updated_at',
        ]
    def get_patient_name(self,obj): return f'{obj.order.visit.patient.first_name} {obj.order.visit.patient.last_name}'
class SampleActivitySerializer(serializers.ModelSerializer):
    class Meta: model=SampleActivity; fields='__all__'; read_only_fields=['id','performed_by','ip_address','browser','created_at','updated_at']
class SampleBatchSerializer(serializers.ModelSerializer):
    sample_ids=serializers.ListField(child=serializers.CharField(max_length=8),write_only=True,required=False)
    samples=serializers.SerializerMethodField()
    class Meta: model=SampleBatch; fields=['id','batch_code','centre','department','status','created_by','dispatched_at','received_at','received_by','sample_ids','samples','created_at','updated_at']; read_only_fields=['id','batch_code','status','created_by','dispatched_at','received_at','received_by']
    def get_samples(self,obj): return list(obj.items.select_related('sample').values_list('sample__sample_id',flat=True))
class SampleProcessingSerializer(serializers.ModelSerializer):
    class Meta: model=SampleProcessing; fields='__all__'; read_only_fields=['id','performed_by','started_at','completed_at','created_at','updated_at']
class TestParameterSerializer(serializers.ModelSerializer):
    class Meta: model=TestParameter; fields='__all__'
class ResultTemplateSerializer(serializers.ModelSerializer):
    class Meta: model=ResultTemplate; fields='__all__'
class ResultValueSerializer(serializers.ModelSerializer):
    parameter_name=serializers.CharField(source='parameter.name',read_only=True)
    class Meta: model=ResultValue; fields=['id','parameter','parameter_name','value','flag']
class LabResultSerializer(serializers.ModelSerializer):
    values=ResultValueSerializer(many=True,read_only=True); sample_id=serializers.CharField(source='sample.sample_id',read_only=True)
    class Meta: model=LabResult; fields='__all__'; read_only_fields=['id','status','entered_by','reviewed_by','authenticated_by','released_by','entered_at','reviewed_at','authenticated_at','released_at','version','created_at','updated_at']
class ResultReviewSerializer(serializers.ModelSerializer):
    class Meta: model=ResultReview; fields='__all__'; read_only_fields=['id','reviewer','created_at','updated_at']


class QcMaterialSerializer(serializers.ModelSerializer):
    class Meta:
        model = QcMaterial
        fields = '__all__'


class QcRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = QcRun
        fields = '__all__'
        read_only_fields = ['id', 'entered_by', 'created_at', 'updated_at']


class TatRuleSerializer(serializers.ModelSerializer):
    test_code = serializers.CharField(source='test.code', read_only=True, default='')
    service_group_name = serializers.CharField(source='service_group.name', read_only=True, default='')

    class Meta:
        model = TatRule
        fields = '__all__'

    def validate(self, attrs):
        test = attrs.get('test', getattr(self.instance, 'test', None))
        group = attrs.get('service_group', getattr(self.instance, 'service_group', None))
        if not test and not group:
            raise serializers.ValidationError('Bind the rule to a test or a service group.')
        expected = attrs.get('expected_minutes', getattr(self.instance, 'expected_minutes', None))
        if expected is not None and expected < 1:
            raise serializers.ValidationError({'expected_minutes': 'Expected TAT must be at least one minute.'})
        return attrs


class TatPauseReasonSerializer(serializers.ModelSerializer):
    class Meta:
        model = TatPauseReason
        fields = '__all__'


class TatEscalationSerializer(serializers.ModelSerializer):
    sample_id = serializers.CharField(source='sample.sample_id', read_only=True)
    centre = serializers.CharField(source='sample.centre.code', read_only=True)
    test_name = serializers.CharField(source='sample.order.test.name', read_only=True)

    class Meta:
        model = TatEscalation
        fields = '__all__'
        read_only_fields = [
            'id', 'fired_at', 'elapsed_minutes', 'expected_minutes',
            'acknowledged_at', 'acknowledged_by', 'created_at', 'updated_at',
        ]
